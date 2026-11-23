"""An instance with no ingest traffic must still checkpoint and drain.

Measured 2026-11-23 on the federation aggregator: it drained 19,713 queued
events at start-up and then refilled to 1,069 with nothing left to move them.
Every ``_flush_memory_events()`` call site sits on an ingest, LLM or promotion
path, and ``_start_memory_ledger_drain()`` re-arms itself only when work is
already pending, so an instance that receives federated imports and never a
signal drains exactly once, at boot.

These tests pin the two replacements: a clock-driven tick, and an explicit
hand-off from the endpoints that mutate the archive out of band.
"""
import json
import time
from unittest.mock import Mock

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.protocol import Signal


def bridge_env(monkeypatch, tmp_path, *, ledger="", interval="0.05"):
    state = tmp_path / "state.json"
    monkeypatch.setenv("CASCADE_STATE_FILE", str(state))
    monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", str(tmp_path / "work.sqlite3"))
    monkeypatch.setenv("CASCADE_STATE_FLUSH_INTERVAL_SECONDS", interval)
    monkeypatch.setenv("CASCADE_LEDGER_URL", ledger)
    return state


def wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def store(bridge, message="fixture"):
    signal = Signal(signal_type="fixture", severity="medium",
                    content={"message": message})
    return bridge.memory_archive.store(signal, classification="needs_attention")


def snapshot(state):
    return json.loads(state.read_text())


def test_idle_instance_drains_without_processing_a_signal(monkeypatch, tmp_path):
    bridge_env(monkeypatch, tmp_path, ledger="https://ledger.example")
    bridge = CascadeBridge()
    sent = []
    monkeypatch.setattr(
        "cascade_compression.integrations.ledger.write_memory_events",
        lambda ledger_url, ledger_token, events, domain: sent.append(list(events)) or True,
    )

    store(bridge)
    assert bridge.stats.signals_processed == 0
    assert bridge._memory_source_pending() == 1

    assert wait(lambda: bridge._ledger_memory_batches_written >= 1)
    assert sent, "the tick never handed the buffer to the ledger"
    assert bridge._memory_source_pending() == 0
    # The whole point: the drain happened with no signal, no LLM work and no
    # promotion cycle to trigger it.
    assert bridge.stats.signals_processed == 0


def test_tick_checkpoints_an_archive_change_that_never_marks_dirty(
    monkeypatch, tmp_path,
):
    state = bridge_env(monkeypatch, tmp_path, interval="0.05")
    bridge = CascadeBridge()
    generation = bridge._state_gen

    memory = store(bridge, "imported-not-processed")
    # The import path mutates the archive without advancing the dirty
    # generation, which is why generation alone cannot gate the checkpoint.
    assert bridge._state_gen == generation

    assert wait(lambda: state.exists()
                and bool(snapshot(state)["memory_archive"]["memories"]))
    assert [m["memory_id"] for m in snapshot(state)["memory_archive"]["memories"]] == [
        str(memory.memory_id)]


def test_flush_memory_events_checkpoints_without_a_ledger_destination(
    monkeypatch, tmp_path,
):
    # No ledger URL: `_stage_memory_events` returns before it touches the
    # state file, so an unattached instance could not save through it at all.
    state = bridge_env(monkeypatch, tmp_path, interval="300")
    bridge = CascadeBridge()

    memory = store(bridge, "unattested")
    assert bridge.flush_memory_events() is True

    persisted = snapshot(state)["memory_archive"]
    assert [m["memory_id"] for m in persisted["memories"]] == [str(memory.memory_id)]
    assert len(persisted["pending_events"]) == 1


def test_close_cancels_the_tick_and_does_not_rearm(monkeypatch, tmp_path):
    bridge_env(monkeypatch, tmp_path, ledger="https://ledger.example")
    bridge = CascadeBridge()
    assert bridge._flush_timer is not None

    bridge.close()
    assert bridge._flush_timer is None
    bridge._start_state_flush_timer()
    assert bridge._flush_timer is None


def test_no_tick_without_a_state_file_or_a_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("CASCADE_STATE_FILE", "")
    monkeypatch.setenv("CASCADE_LEDGER_URL", "")
    monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", "")
    bridge = CascadeBridge()
    bridge._start_state_flush_timer()
    assert bridge._flush_timer is None


def test_import_and_consolidate_endpoints_hand_off(monkeypatch):
    import cascade_compression.service as service

    bridge = Mock()
    archive = Mock()
    archive.import_memories.return_value = 2
    archive.consolidate.return_value = {
        "processed": 1, "evicted": 0, "compression_ratio": 0.0,
    }
    monkeypatch.setattr(service, "_bridge", bridge)
    monkeypatch.setattr(service, "_memory_archive", archive)

    assert service.memory_import({"memories": []}) == {"imported": 2}
    bridge.flush_memory_events.assert_called_once()

    bridge.flush_memory_events.reset_mock()
    service.consolidate()
    bridge.flush_memory_events.assert_called_once()
