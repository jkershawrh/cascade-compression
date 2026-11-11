"""Durable memory queue: SQLite-backed classification work survives restarts.

The spool (CASCADE_LLM_QUEUE_FILE) is opt-in. When enabled, queued and
in-flight classification work is persisted before admission and acknowledged
only after the memory checkpoint, so a restart replays it at-least-once
instead of declaring it terminal. Per-signal accounting must stay reconciled
across that replay.
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.protocol import Signal
from cascade_compression.classifier import ClassificationResult


def make_signal(**overrides):
    kwargs = dict(
        signal_type="pod_restart",
        severity="medium",
        source="node-1",
        namespace="prod",
        cluster="cluster-a",
        content={"message": "container restarted"},
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


@pytest.fixture
def durable_env(tmp_path, monkeypatch):
    """Shared env for a bridge with both snapshot and spool persistence."""
    monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", str(tmp_path / "queue.sqlite3"))
    monkeypatch.setenv("CASCADE_LEDGER_URL", "")
    return {
        "state": str(tmp_path / "state.json"),
        "queue": str(tmp_path / "queue.sqlite3"),
    }


def synthetic_result(label="routine_noise"):
    return ClassificationResult(
        label=label, backend="synthetic", authoritative_backend="synthetic",
        model_revision="synthetic",
    )


def drain(bridge):
    deadline = time.monotonic() + 5
    while bridge._llm_active_threads and time.monotonic() < deadline:
        time.sleep(0.005)
    assert bridge._llm_active_threads == 0


def spool_rows(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT id, payload FROM work").fetchall()


class TestDurableSpoolUnit:
    def test_pending_work_survives_reopen(self, durable_env):
        bridge = CascadeBridge()
        try:
            bridge._enqueue_llm_signals([make_signal()])
            assert bridge._durable_queue.stats()["pending_entries"] == 1
        finally:
            bridge.close()

        reopened = CascadeBridge()
        try:
            assert len(reopened._llm_buffer) == 1
            assert reopened._durable_queue.stats()["pending_entries"] == 1
        finally:
            reopened.close()

    def test_queue_file_is_opt_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.delenv("CASCADE_LLM_QUEUE_FILE", raising=False)
        bridge = CascadeBridge()
        try:
            bridge._enqueue_llm_signals([make_signal()])
            assert bridge._durable_queue is None
            stats = bridge.get_stats()["llm_durable_queue"]
            assert stats["enabled"] is False
            assert stats["uncommitted_changes"] == 0
            assert stats["memory_checkpoint_healthy"] is True
            assert bridge.get_stats()["classifier_work_recovery"] == (
                "terminal_receipts_on_restart"
            )
        finally:
            bridge.close()

    def test_single_owner_enforced(self, durable_env):
        bridge = CascadeBridge()
        try:
            with pytest.raises(Exception):
                CascadeBridge()
        finally:
            bridge.close()

    def test_content_is_redacted_before_persistence(self, durable_env):
        bridge = CascadeBridge()
        try:
            signal = make_signal(content={
                "message": "login failed",
                "password": "hunter2",
                "authorization": "Bearer abc.def.ghi",
            })
            bridge._enqueue_llm_signals([signal])
            rows = spool_rows(durable_env["queue"])
            payload = json.loads(rows[0][1])
            assert "hunter2" not in rows[0][1]
            assert payload["content"]["password"] == "[REDACTED]"
            assert "abc.def.ghi" not in rows[0][1]
        finally:
            bridge.close()


class TestRestartReplay:
    def test_queued_work_replays_with_reconciled_accounting(self, durable_env):
        bridge = CascadeBridge()
        try:
            signals = [make_signal(content={"message": f"event {i}"})
                       for i in range(3)]
            assert bridge._enqueue_llm_signals(signals)
            outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["enqueued"] == outcomes["queued"] == 3
        finally:
            bridge.close()

        again = CascadeBridge()
        try:
            assert len(again._llm_buffer) == 3
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            # Snapshot accounting carried over; replay must not re-count it.
            assert outcomes["enqueued"] == 3
            assert outcomes["queued"] == 3
            assert outcomes["reconciled"] is True
            assert again._classifier_terminal_receipts == []
        finally:
            again.close()

    def test_completed_work_is_acked_and_not_replayed(self, durable_env, monkeypatch):
        bridge = CascadeBridge(llm_url="http://example.invalid")
        try:
            monkeypatch.setattr(bridge.classifier, "classify",
                                lambda *a: synthetic_result())
            monkeypatch.setattr(bridge, "_classification_dispatch_enabled",
                                lambda: True)
            assert bridge._enqueue_llm_signals([make_signal()])
            bridge._dispatch_llm_batches()
            drain(bridge)
            assert bridge._durable_queue.stats()["pending_entries"] == 0
            assert not bridge._durable_dirty
            outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["classified"] == 1
            assert outcomes["terminal_complete"] is True
        finally:
            bridge.close()

        again = CascadeBridge()
        try:
            assert again._llm_buffer == []
            assert again._durable_queue.stats()["pending_entries"] == 0
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["classified"] == 1
            assert outcomes["queued"] == 0
            assert outcomes["terminal_complete"] is True
        finally:
            again.close()

    def test_inflight_work_replays_with_dispatch_reconciled(
            self, durable_env, monkeypatch):
        """A crash mid-classification replays the work, not a failure receipt."""
        bridge = CascadeBridge(llm_url="http://example.invalid")
        try:
            # A worker that never records an outcome models a process that
            # died between dispatch and acknowledgement.
            monkeypatch.setattr(bridge, "_run_llm", lambda batch: None)
            monkeypatch.setattr(bridge, "_classification_dispatch_enabled",
                                lambda: True)
            assert bridge._enqueue_llm_signals([make_signal()])
            bridge._dispatch_llm_batches()
            # The no-op worker never decrements the thread gauge (it died);
            # the snapshot below was already written synchronously at dispatch.
            snapshot = json.load(open(durable_env["state"]))
            phases = [e["phase"] for e in snapshot["classifier_work"]["outstanding"]]
            assert phases == ["inflight"]
            assert bridge._durable_queue.stats()["pending_entries"] == 1
        finally:
            bridge.close()

        again = CascadeBridge(llm_url="http://example.invalid")
        try:
            # The interrupted entry is back in the queue, not in a receipt.
            assert len(again._llm_buffer) == 1
            assert again._classifier_terminal_receipts == []
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["enqueued"] == 1
            assert outcomes["dispatched"] == 0
            assert outcomes["queued"] == 1
            assert outcomes["in_flight"] == 0
            assert outcomes["reconciled"] is True

            # Replayed work classifies normally and terminates cleanly.
            monkeypatch.setattr(again.classifier, "classify",
                                lambda *a: synthetic_result())
            monkeypatch.setattr(again, "_classification_dispatch_enabled",
                                lambda: True)
            again._dispatch_llm_batches()
            drain(again)
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["classified"] == 1
            assert outcomes["terminal_complete"] is True
            assert again._durable_queue.stats()["pending_entries"] == 0
        finally:
            again.close()

    def test_spool_ahead_of_snapshot_counts_fresh_admissions(
            self, durable_env, monkeypatch):
        """Work admitted to the spool after a failed snapshot still reconciles."""
        bridge = CascadeBridge()
        try:
            assert bridge._enqueue_llm_signals(
                [make_signal(content={"message": "snapshotted"})])
            with patch("cascade_compression.lifecycle.os.replace",
                       side_effect=OSError("synthetic storage failure")):
                # The durable flush succeeded before the snapshot failed, so
                # the spool holds work the snapshot never accounted.
                assert bridge._enqueue_llm_signals(
                    [make_signal(content={"message": "spooled-only"})]) is False
            assert bridge._durable_queue.stats()["pending_entries"] == 2
        finally:
            bridge.close()

        again = CascadeBridge()
        try:
            assert len(again._llm_buffer) == 2
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["enqueued"] == 2
            assert outcomes["queued"] == 2
            assert outcomes["reconciled"] is True
        finally:
            again.close()

    def test_priority_survives_restart(self, durable_env):
        bridge = CascadeBridge()
        try:
            assert bridge._enqueue_llm_signals([
                make_signal(severity="critical", content={"message": "a"}),
                make_signal(severity="medium", content={"message": "b"}),
            ])
            assert len(bridge._llm_priority_buffer) == 1
        finally:
            bridge.close()

        again = CascadeBridge()
        try:
            assert len(again._llm_priority_buffer) == 1
            assert len(again._llm_buffer) == 1
            assert again._llm_priority_buffer[0]["severity"] == "critical"
        finally:
            again.close()

    def test_coalesced_entry_replays_with_all_signal_ids(self, durable_env):
        bridge = CascadeBridge()
        try:
            signals = [make_signal() for _ in range(3)]
            assert bridge._enqueue_llm_signals(signals)
            assert len(bridge._llm_buffer) == 1
        finally:
            bridge.close()

        again = CascadeBridge()
        try:
            assert len(again._llm_buffer) == 1
            entry = again._llm_buffer[0]
            assert len(entry["_signal_ids"]) == 3
            # Replayed work retains its members, but the original admission
            # evidence is unavailable after restoring the JSON projection.
            assert not again._llm_coalesce_index
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["queued"] == 3
            assert outcomes["reconciled"] is True
        finally:
            again.close()


class TestDropAndExpiry:
    def test_backpressure_drop_removes_spool_rows(self, durable_env, monkeypatch):
        bridge = CascadeBridge()
        try:
            bridge._llm_max_buffer = 1
            bridge._llm_max_buffer_bytes = 1_000_000
            assert bridge._enqueue_llm_signals([
                make_signal(content={"message": "first"}),
                make_signal(content={"message": "second"}),
            ])
            assert bridge._llm_dropped == 1
            assert bridge._durable_queue.stats()["pending_entries"] == 1
            outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["dropped"] == 1
            assert outcomes["reconciled"] is True
        finally:
            bridge.close()

        again = CascadeBridge()
        try:
            # Only the survivor replays; dropped work must not resurrect.
            assert len(again._llm_buffer) == 1
            assert again._llm_buffer[0]["content"]["message"] == "second"
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["enqueued"] == 2
            assert outcomes["queued"] == 1
            assert outcomes["dropped"] == 1
            assert outcomes["reconciled"] is True
        finally:
            again.close()

    def test_ttl_expiry_drops_aged_rows_and_marks_memory(self, durable_env):
        bridge = CascadeBridge()
        try:
            memory = bridge.memory_archive.store(
                make_signal(), classification="needs_attention")
            signal = make_signal()
            bridge._classification_dispatch_enabled = lambda: True
            assert bridge._enqueue_llm_signals([signal])
            entry = bridge._llm_buffer[0]
            with bridge._pending_memory_lock:
                bridge._pending_memory_ids[str(signal.signal_id)] = memory.memory_id
            # Mark the row as aged past the TTL directly in the spool.
            with sqlite3.connect(durable_env["queue"]) as db:
                db.execute("UPDATE work SET born = born - 100000")
            with bridge._llm_queue_lock:
                bridge._expire_durable_locked()
            assert bridge._llm_buffer == []
            assert bridge._durable_queue.stats()["pending_entries"] == 0
            outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["dropped"] == 1
            assert bridge.memory_archive.get(memory.memory_id).evidence[
                "classification_status"] == "expired"
            with bridge._pending_memory_lock:
                assert bridge._pending_memory_ids == {}
        finally:
            bridge.close()

    def test_worker_start_failure_is_terminal_and_not_replayed(
            self, durable_env, monkeypatch):
        bridge = CascadeBridge(llm_url="http://example.invalid")
        try:
            class FailedThread:
                def __init__(self, **_kwargs):
                    pass

                def start(self):
                    raise RuntimeError("thread unavailable")

            monkeypatch.setattr("cascade_compression.bridge.threading.Thread",
                                FailedThread)
            assert bridge._enqueue_llm_signals([make_signal()])
            bridge._dispatch_llm_batches()
            outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["failed"] == 1
            assert outcomes["queued"] == outcomes["in_flight"] == 0
            assert outcomes["reconciled"] is True
            # Terminal-failed work is deleted from the spool: cross-restart
            # accounting must not replay an already-counted outcome.
            assert bridge._durable_queue.stats()["pending_entries"] == 0
        finally:
            bridge.close()

        again = CascadeBridge()
        try:
            assert again._llm_buffer == []
            outcomes = again.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["failed"] == 1
            assert outcomes["reconciled"] is True
        finally:
            again.close()


class TestMemoryCheckpoint:
    def test_pending_memory_ids_relink_after_restart(self, durable_env, monkeypatch):
        bridge = CascadeBridge(llm_url="http://example.invalid")
        try:
            monkeypatch.setattr(bridge, "_dispatch_llm_batches", lambda: None)
            monkeypatch.setattr(bridge, "_classification_dispatch_enabled",
                                lambda: True)
            signals = [make_signal(signal_type=f"pod_restart_{i}",
                                   content={"message": f"m{i}"}) for i in range(2)]
            bridge.process(signals)
            assert len(bridge._pending_memory_ids) == 2
            linked = dict(bridge._pending_memory_ids)
        finally:
            bridge.close()

        again = CascadeBridge(llm_url="http://example.invalid")
        try:
            # Only memories that survived the checkpoint relink.
            expected = {
                signal_id: mid for signal_id, mid in linked.items()
                if again.memory_archive.get(mid)
            }
            with again._pending_memory_lock:
                assert again._pending_memory_ids == expected
            assert len(again._pending_memory_ids) == 2
        finally:
            again.close()

    def test_classification_writeback_updates_memory_evidence(
            self, durable_env, monkeypatch):
        bridge = CascadeBridge(llm_url="http://example.invalid")
        try:
            monkeypatch.setattr(bridge, "_dispatch_llm_batches", lambda: None)
            monkeypatch.setattr(bridge, "_classification_dispatch_enabled",
                                lambda: True)
            signal = make_signal()
            bridge.process([signal])
            memory_id = bridge._pending_memory_ids[str(signal.signal_id)]

            monkeypatch.setattr(bridge.classifier, "classify",
                                lambda *a: synthetic_result("real_incident"))
            monkeypatch.setattr(bridge, "_dispatch_llm_batches", lambda: None)
            with bridge._llm_queue_lock:
                entry = bridge._llm_buffer.pop()
                bridge._llm_accounting_dispatched = 1
            bridge._llm_active_threads = 1
            bridge._run_llm([entry])

            memory = bridge.memory_archive.get(memory_id)
            assert memory.classification == "real_incident"
            assert memory.evidence["classification_status"] == "classified"
            assert memory.evidence["classification_backend"] == "synthetic"
            assert memory.evidence["classified_at"]
            with bridge._pending_memory_lock:
                assert bridge._pending_memory_ids == {}
        finally:
            bridge.close()

    def test_failed_classification_marks_memory_status(
            self, durable_env, monkeypatch):
        bridge = CascadeBridge(llm_url="http://example.invalid")
        try:
            monkeypatch.setattr(bridge, "_dispatch_llm_batches", lambda: None)
            monkeypatch.setattr(bridge, "_classification_dispatch_enabled",
                                lambda: True)
            signal = make_signal()
            bridge.process([signal])
            memory_id = bridge._pending_memory_ids[str(signal.signal_id)]

            def failing(*args):
                raise RuntimeError("classifier unavailable")

            monkeypatch.setattr(bridge.classifier, "classify", failing)
            with bridge._llm_queue_lock:
                entry = bridge._llm_buffer.pop()
                bridge._llm_accounting_dispatched = 1
            bridge._llm_active_threads = 1
            bridge._run_llm([entry])

            memory = bridge.memory_archive.get(memory_id)
            assert memory.evidence["classification_status"] == "failed"
            outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
            assert outcomes["failed"] == 1
            # Failure keeps the linkage so durable replay can re-link it.
            assert str(signal.signal_id) in bridge._pending_memory_ids
        finally:
            bridge.close()

    def test_checkpoint_failure_pauses_dispatch_and_fails_closed(
            self, durable_env, monkeypatch):
        bridge = CascadeBridge(llm_url="http://example.invalid")
        try:
            assert bridge._enqueue_llm_signals([make_signal()])
            with patch("cascade_compression.lifecycle.os.replace",
                       side_effect=OSError("synthetic storage failure")):
                healthy = bridge._checkpoint_classification_memories()
            assert healthy is False
            assert bridge._durable_memory_checkpoint_healthy is False
            assert bridge._durable_memory_checkpoint_failures == 1

            monkeypatch.setattr(bridge, "_classification_dispatch_enabled",
                                lambda: True)
            bridge._dispatch_llm_batches()
            # Dispatch is paused while the checkpoint is unhealthy; the work
            # stays queued (and spooled) rather than being lost.
            assert len(bridge._llm_buffer) == 1
            assert bridge._durable_queue.stats()["pending_entries"] == 1
            stats = bridge.get_stats()
            assert stats["llm_durable_queue"]["memory_checkpoint_healthy"] is False
        finally:
            bridge.close()

    def test_checkpoint_is_noop_without_spool(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.delenv("CASCADE_LLM_QUEUE_FILE", raising=False)
        bridge = CascadeBridge()
        try:
            with patch("cascade_compression.lifecycle.os.replace",
                       side_effect=OSError("synthetic storage failure")):
                assert bridge._checkpoint_classification_memories() is True
            assert bridge._durable_memory_checkpoint_healthy is True
        finally:
            bridge.close()


class TestWaitForLLM:
    def test_wait_for_llm_fails_closed_on_durable_residue(self, durable_env):
        bridge = CascadeBridge()
        try:
            bridge._enqueue_llm_signals([make_signal()])
            # Queued work never drains within the timeout.
            assert bridge.wait_for_llm(0.1) is False

            bridge._llm_buffer.clear()
            bridge._llm_buffer_bytes = 0
            bridge._rebuild_llm_coalesce_index_locked()
            # Uncommitted spool changes must fail closed.
            bridge._durable_dirty["stale"] = None
            assert bridge.wait_for_llm(0.2) is False
            bridge._durable_dirty.clear()
            # Surviving spool rows must fail closed.
            assert bridge._durable_queue.stats()["pending_entries"] == 1
            assert bridge.wait_for_llm(0.2) is False
        finally:
            bridge.close()

    def test_wait_for_llm_true_when_spool_drains(self, durable_env, monkeypatch):
        bridge = CascadeBridge()
        try:
            assert bridge._enqueue_llm_signals([make_signal()])
            with bridge._llm_queue_lock:
                entry = bridge._llm_buffer.pop()
                bridge._llm_accounting_dispatched = 1
            monkeypatch.setattr(bridge.classifier, "classify",
                                lambda *a: synthetic_result())
            bridge._llm_active_threads = 1
            monkeypatch.setattr(bridge, "_dispatch_llm_batches", lambda: None)
            bridge._run_llm([entry])
            assert bridge._durable_queue.stats()["pending_entries"] == 0
            assert bridge.wait_for_llm(1.0) is True
        finally:
            bridge.close()


class TestServiceHealth:
    def test_health_reports_spool_and_checkpoint_state(self, durable_env, monkeypatch):
        from fastapi.testclient import TestClient
        import cascade_compression.service as service

        bridge = CascadeBridge()
        try:
            monkeypatch.setattr(service, "_bridge", bridge)
            client = TestClient(service.app)
            body = client.get("/health").json()
            queue = body["classification_queue"]
            assert queue["enabled"] is True
            assert queue["healthy"] is True
            assert queue["memory_checkpoint_healthy"] is True
            assert body["status"] in {"ok", "paused"}

            bridge._durable_memory_checkpoint_healthy = False
            body = client.get("/health").json()
            assert body["status"] == "degraded"
            assert body["classification_queue"]["memory_checkpoint_healthy"] is False
        finally:
            bridge.close()
