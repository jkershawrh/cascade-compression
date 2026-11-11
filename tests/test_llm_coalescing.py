"""LLM request coalescing: identical queued work shares one inference call.

Same-key signals arriving while an entry is still queued fold into that
entry (occurrence accounting plus signal-id aggregation) instead of
enqueuing duplicate classifier work. Priority work is never coalesced.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

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
        labels={"domain": "k8s"},
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


@pytest.fixture
def bridge():
    b = CascadeBridge()
    yield b
    b.close()


@pytest.mark.parametrize("field", ["lastTransitionTime", "resourceVersion", "managedFields",
                                   "_aggregation", "timestamp"])
def test_changed_source_metadata_stays_separate(bridge, field):
    signals = [make_signal(content={"message": "m", "state": {field: value}})
               for value in ("one", "two")]
    bridge._enqueue_llm_signals(signals)
    assert len(bridge._llm_buffer) == 2


def test_nested_json_with_different_key_order_can_fold(bridge):
    first = {"a": [1, 2], "b": {"v": 3}}
    second = {"b": {"v": 3}, "a": [1, 2]}
    bridge._enqueue_llm_signals([
        make_signal(content=content, labels={"scopes": ["one", "two"]})
        for content in (first, second)
    ])
    assert len(bridge._llm_buffer) == 1
    assert bridge._llm_buffer[0]["content"] == first


class TestEnqueueCoalescing:
    def test_duplicate_signals_fold_into_one_entry(self, bridge):
        signals = [make_signal() for _ in range(5)]
        bridge._enqueue_llm_signals(signals)

        assert len(bridge._llm_buffer) == 1
        entry = bridge._llm_buffer[0]
        assert len(entry["_signal_ids"]) == 5
        assert entry["_aggregation"]["occurrence_count"] == 5
        assert entry["_aggregation"]["first_seen"]
        assert entry["_aggregation"]["last_seen"]
        assert bridge._llm_coalesced == 4
        assert bridge._llm_coalesced_by_type["pod_restart"] == 4

    def test_original_signal_accounting_counts_every_occurrence(self, bridge):
        signals = [make_signal() for _ in range(5)]
        bridge._enqueue_llm_signals(signals)
        outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
        assert outcomes["enqueued"] == 5
        assert outcomes["queued"] == 5
        assert outcomes["reconciled"] is True

    def test_distinct_signals_stay_separate(self, bridge):
        signals = [make_signal(content={"message": f"event {i}"}) for i in range(3)]
        bridge._enqueue_llm_signals(signals)
        assert len(bridge._llm_buffer) == 3
        assert bridge._llm_coalesced == 0

    def test_priority_signals_are_never_coalesced(self, bridge):
        signals = [make_signal(severity="critical") for _ in range(3)]
        bridge._enqueue_llm_signals(signals)
        assert len(bridge._llm_priority_buffer) == 3
        assert len(bridge._llm_buffer) == 0
        assert bridge._llm_coalesced == 0

    def test_coalescing_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("CASCADE_LLM_COALESCE", "0")
        bridge = CascadeBridge()
        try:
            signals = [make_signal() for _ in range(3)]
            bridge._enqueue_llm_signals(signals)
            assert len(bridge._llm_buffer) == 3
            assert bridge._llm_coalesced == 0
        finally:
            bridge.close()

    def test_folded_entry_records_every_signal_id(self, bridge):
        signals = [make_signal() for _ in range(3)]
        bridge._enqueue_llm_signals(signals)
        entry = bridge._llm_buffer[0]
        assert entry["_signal_ids"] == [str(s.signal_id) for s in signals]

    def test_entries_carry_enqueue_timestamp(self, bridge):
        before = datetime.now(timezone.utc)
        bridge._enqueue_llm_signals([make_signal()])
        enqueued_at = datetime.fromisoformat(bridge._llm_buffer[0]["_enqueued_at"])
        assert enqueued_at >= before.replace(microsecond=before.microsecond)

    def test_fold_grows_retained_byte_accounting(self, bridge):
        bridge._enqueue_llm_signals([make_signal()])
        entry = bridge._llm_buffer[0]
        entry["_signal_ids"] = [entry["_signal_ids"][0]]  # normalize to one
        first = bridge._llm_buffer_bytes
        bridge._enqueue_llm_signals([make_signal()])
        assert len(bridge._llm_buffer) == 1
        assert bridge._llm_buffer_bytes > first
        assert bridge._llm_buffer_bytes == sum(
            bridge._entry_queue_bytes(e) for e in bridge._llm_buffer
        )


class TestDispatchAndExpiry:
    def _drain_via_dispatch(self, bridge, monkeypatch):
        monkeypatch.setattr(bridge, "_classification_dispatch_enabled", lambda: True)
        monkeypatch.setattr(
            bridge.classifier, "classify",
            lambda *args: ClassificationResult(
                label="routine_noise", backend="synthetic",
                authoritative_backend="synthetic", model_revision="synthetic",
            ),
        )
        bridge._dispatch_llm_batches()
        deadline = time.monotonic() + 5
        while bridge._llm_active_threads and time.monotonic() < deadline:
            time.sleep(0.005)
        assert bridge._llm_active_threads == 0

    def test_dispatch_clears_coalesce_index(self, bridge, monkeypatch):
        bridge._enqueue_llm_signals([make_signal(), make_signal()])
        assert len(bridge._llm_coalesce_index) == 1
        self._drain_via_dispatch(bridge, monkeypatch)
        assert bridge._llm_coalesce_index == {}
        outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
        assert outcomes["classified"] == 2
        assert outcomes["reconciled"] is True

    def test_coalesced_entry_classifies_all_member_signals(self, bridge, monkeypatch):
        signals = [make_signal() for _ in range(4)]
        bridge._enqueue_llm_signals(signals)
        self._drain_via_dispatch(bridge, monkeypatch)
        outcomes = bridge.get_stats()["llm_signal_outcomes_since_start"]
        assert outcomes["enqueued"] == 4
        assert outcomes["classified"] == 4
        assert outcomes["reconciled"] is True
        assert outcomes["terminal_complete"] is True
        # One inference result records the covered occurrence count.
        assert bridge.get_llm_results()[0]["occurrence_count"] == 4

    def test_buffer_overflow_drop_rebuilds_coalesce_index(self, bridge):
        bridge._llm_max_buffer = 1
        bridge._llm_max_buffer_bytes = 1_000_000
        bridge._enqueue_llm_signals([make_signal(content={"message": "a"})])
        bridge._enqueue_llm_signals([make_signal(content={"message": "b"})])
        # The oldest entry was dropped under backpressure; the index must
        # reference only surviving entries.
        assert len(bridge._llm_buffer) == 1
        assert len(bridge._llm_coalesce_index) == 1
        survivor = bridge._llm_buffer[0]
        assert list(bridge._llm_coalesce_index.values()) == [survivor]

    def test_rebuild_index_maps_only_existing_entries(self, bridge):
        bridge._enqueue_llm_signals([make_signal(), make_signal(namespace="other")])
        buffered = bridge._llm_buffer.pop()
        # Old persisted/user-provided keys do not establish admission evidence.
        bridge._llm_buffer = [buffered, {"_coalesce_key": "forged", "signal_type": "t"}]
        bridge._rebuild_llm_coalesce_index_locked()
        assert list(bridge._llm_coalesce_index.values()) == [buffered]

    def test_oldest_queue_age_seconds_tracks_enqueued_at(self, bridge):
        assert bridge._llm_oldest_queue_age_seconds() == 0.0
        bridge._enqueue_llm_signals([make_signal()])
        entry = bridge._llm_buffer[0]
        entry["_enqueued_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat()
        age = bridge._llm_oldest_queue_age_seconds()
        assert 590 <= age <= 700
        stats = bridge.get_stats()
        assert stats["llm_oldest_queue_age_seconds"] >= 590

    def test_oldest_queue_age_ignores_invalid_timestamps(self, bridge):
        bridge._enqueue_llm_signals([make_signal()])
        bridge._llm_buffer[0]["_enqueued_at"] = "not-a-timestamp"
        assert bridge._llm_oldest_queue_age_seconds() == 0.0


class TestInflightBudget:
    def test_classify_one_limited_enforces_admission_budget(self, bridge):
        """Concurrent classifications never exceed the semaphore budget."""
        barrier_in, barrier_out = threading.Event(), threading.Event()
        observed, lock = [], threading.Lock()

        def slow_classify(sig, client):
            with lock:
                observed.append(bridge._llm_inflight)
            barrier_in.set()
            barrier_out.wait(5)
            return ClassificationResult(
                label="routine_noise", backend="synthetic",
                authoritative_backend="synthetic", model_revision="synthetic",
            )

        bridge.classifier.classify = slow_classify
        entries = [{"signal_type": "pod_restart", "severity": "medium",
                    "namespace": "prod", "signal_id": f"sig-{i}",
                    "content": {"message": "container restarted"}}
                   for i in range(4)]
        threads = [
            threading.Thread(
                target=bridge._classify_one_limited, args=(entry, None),
            )
            for entry in entries
        ]
        for thread in threads:
            thread.start()
        try:
            assert barrier_in.wait(5)
            time.sleep(0.1)
            assert bridge._llm_inflight == bridge._llm_max_inflight
            assert max(observed) <= bridge._llm_max_inflight
            assert bridge._llm_peak_inflight == bridge._llm_max_inflight
        finally:
            barrier_out.set()
            for thread in threads:
                thread.join(5)
        assert bridge._llm_inflight == 0
        assert len(observed) == 4

    def test_inflight_gauges_exposed_in_stats(self, bridge):
        stats = bridge.get_stats()
        assert stats["llm_inflight"] == 0
        assert stats["llm_peak_inflight"] == 0
        assert stats["llm_max_inflight"] == bridge._llm_max_inflight
        assert stats["llm_max_inflight"] >= 1

    def test_inflight_budget_configurable(self, monkeypatch):
        monkeypatch.setenv("CASCADE_LLM_MAX_INFLIGHT", "3")
        bridge = CascadeBridge()
        try:
            assert bridge._llm_max_inflight == 3
            assert stats_inflight_max(bridge) == 3
        finally:
            bridge.close()


def stats_inflight_max(bridge):
    return bridge.get_stats()["llm_max_inflight"]
