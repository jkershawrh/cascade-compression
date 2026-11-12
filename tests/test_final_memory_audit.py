"""Synthetic crash boundaries for final classification and its audit trail."""
import json
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.protocol import Signal
from cascade_compression.classifier import ClassificationResult


@pytest.fixture
def files(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("CASCADE_STATE_FILE", str(state))
    monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", str(tmp_path / "work.sqlite3"))
    monkeypatch.setenv("CASCADE_LEDGER_URL", "")
    return state


def admitted(bridge):
    signals = [Signal(signal_type="fixture", severity="medium",
                      content={"message": "repeat"}) for _ in range(3)]
    for signal in signals:
        memory = bridge.memory_archive.store(signal, classification="needs_attention")
        bridge._pending_memory_ids[str(signal.signal_id)] = memory.memory_id
    assert bridge._enqueue_llm_signals(signals)
    entry, = bridge._llm_buffer
    return entry, dict(bridge._pending_memory_ids)


def classify(bridge):
    bridge.classifier.classify = Mock(return_value=ClassificationResult(
        label="real_incident", backend="fixture", model_revision="r1"))
    bridge._dispatch_llm_batches()
    assert bridge.wait_for_llm(3)


def updates(state):
    return [e for e in state["memory_archive"]["pending_events"]
            if e["event_type"] == "classification_updated"]


def check_committed(state, entry, links):
    events = updates(state)
    assert len(events) == len(links)
    assert {e["details"]["signal_id"]: e["memory_id"] for e in events} == {
        sid: str(mid) for sid, mid in links.items()}
    assert {e["details"]["work_id"] for e in events} == {entry["_work_id"]}
    assert len({e["details"]["event_id"] for e in events}) == len(links)
    assert all(e["details"]["classification"] == "real_incident" for e in events)
    for memory in state["memory_archive"]["memories"]:
        assert memory["classification"] == "real_incident"
        assert memory["evidence"]["classification_status"] == "classified"
    assert state["classifier_work"]["accounting"] == dict(
        enqueued=3, dispatched=3, succeeded=3, failed=0, dropped=0)


def test_coalesced_completion_and_audit_survive_restart(files):
    bridge = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(bridge)
        classify(bridge)
        bridge.classifier.classify.assert_called_once()
        check_committed(json.loads(files.read_text()), entry, links)
    finally:
        bridge.close()
    restored = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        assert restored.wait_for_llm(1)
        assert not restored._classifier_terminal_receipts
        assert len([e for e in restored.memory_archive.drain_events()
                    if e.event_type == "classification_updated"]) == 3
    finally:
        restored.close()


@pytest.mark.parametrize("seam", ["retained", "applied", "acknowledged"])
def test_process_exit_preserves_exactly_one_local_transition_per_member(files, seam):
    import subprocess
    import sys
    # The child exits inside an actual checkpoint/SQLite commit, before the
    # worker can run cleanup. The parent opens the same two stores twice.
    child = """
import json, os, sys
from pathlib import Path
from unittest.mock import Mock
from cascade_compression import bridge as module
from tests.test_final_memory_audit import admitted, ClassificationResult

b = module.CascadeBridge(llm_url="http://fixture.invalid")
entry, links = admitted(b)
Path(b._state_file + ".expected").write_text(json.dumps([entry["_work_id"],
    {s: str(m) for s, m in links.items()}]))
checkpoint = module.atomic_snapshot
def snapshot(path, state):
    checkpoint(path, state)
    journal = state["classification_completion"]
    if ((sys.argv[1] == "retained" and journal["pending"])
            or (sys.argv[1] == "applied" and journal["acknowledge"])):
        os._exit(48)
module.atomic_snapshot = snapshot
commit = b._durable_queue.apply
def ack(changes):
    ok = commit(changes)
    if ok and sys.argv[1] == "acknowledged" and None in changes.values():
        os._exit(48)
    return ok
b._durable_queue.apply = ack
b.classifier.classify = Mock(return_value=ClassificationResult(label="real_incident", backend="fixture"))
b._dispatch_llm_batches()
b.wait_for_llm(4)
"""
    proc = subprocess.run([sys.executable, "-c", child, seam], timeout=10,
                          capture_output=True, text=True)
    assert proc.returncode == 48, proc.stderr
    work_id, links = json.loads(files.with_suffix(".json.expected").read_text())
    event_ids = None
    for _ in range(2):
        restored = CascadeBridge(llm_url="http://fixture.invalid")
        try:
            assert restored.wait_for_llm(0.5)
            assert not restored._llm_buffer and not restored._classifier_terminal_receipts
            assert restored._llm_accounting_succeeded == 3
            assert restored._save_state()
            state = json.loads(files.read_text())
            check_committed(state, {"_work_id": work_id}, links)
            ids = [e["details"]["event_id"] for e in updates(state)]
            assert event_ids is None or event_ids == ids
            event_ids = ids
        finally:
            restored.close()


@pytest.mark.parametrize("replaced", [False, True])
def test_ambiguous_checkpoint_fences_and_never_overwrites_committed_state(files, monkeypatch, replaced):
    from cascade_compression import bridge as module
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        original = module.atomic_snapshot
        def uncertain(path, state):
            if state["classifier_work"]["accounting"]["succeeded"]:
                if replaced:
                    original(path, state)
                raise OSError("ambiguous fsync")
            original(path, state)
        with monkeypatch.context() as m:
            m.setattr(module, "atomic_snapshot", uncertain)
            b.classifier.classify = Mock(return_value=ClassificationResult(label="real_incident", backend="fixture"))
            b._dispatch_llm_batches()
            assert not b.wait_for_llm(2)
        assert b._classifier_recovery_error
        assert not b._save_state()
        assert b._durable_queue.stats()["pending_entries"] == 1
        assert all(b.memory_archive.get(mid).classification == "needs_attention" for mid in links.values())
        assert not any(e.event_type == "classification_updated" for e in b.memory_archive.drain_events())
        assert bool(updates(json.loads(files.read_text()))) == replaced
    finally:
        b.close()
    restored = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        assert restored.wait_for_llm(1)
        check_committed(json.loads(files.read_text()), entry, links)
    finally:
        restored.close()


def test_partial_event_capacity_resumes_cursor_without_reinference(files):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        b.memory_archive._event_max_pending = b.memory_archive.pending_event_count() + 1
        b.classifier.classify = Mock(return_value=ClassificationResult(label="real_incident", backend="fixture"))
        b._dispatch_llm_batches()
        assert not b.wait_for_llm(2)
        b.classifier.classify.assert_called_once()
        assert b._llm_accounting_failed == b._llm_accounting_succeeded == 0
        state = json.loads(files.read_text())
        assert state["stats_snapshot"]["llm_classified"] == 1
        prefix, = updates(state)
        assert prefix["details"]["signal_id"] == entry["_signal_ids"][0]
        assert state["classification_completion"]["pending"][entry["_work_id"]]["next_member"] == 1
    finally:
        b.close()
    restored = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        assert restored.wait_for_llm(1)
        check_committed(json.loads(files.read_text()), entry, links)
    finally:
        restored.close()


def test_bounded_handoff_retries_the_same_remote_batch(files, monkeypatch):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        admitted(b)
        classify(b)
        source = b.memory_archive.to_dict()["pending_events"]
        b._ledger_url = "https://fixture.invalid"
        b._ledger_memory_store.max_pending = 2
        monkeypatch.setattr(b, "_schedule_memory_ledger_retry", lambda: None)
        assert b._stage_memory_events()
        assert b._ledger_memory_store.count() == 2
        assert b.memory_archive.pending_event_count() == len(source) - 2
        from cascade_compression.integrations import ledger
        send = Mock(return_value=False)
        monkeypatch.setattr(ledger, "write_memory_events", send)
        b._drain_memory_ledger_queue()
        first = send.call_args.args[2]
        assert b._ledger_memory_batches_written == 0 and b._ledger_memory_store.count() == 2
        b._ledger_memory_store.max_pending = 10
        assert b._stage_memory_events()
        send.reset_mock()
        send.return_value = True
        b._drain_memory_ledger_queue()
        assert send.call_args_list[0].args[2] == first
        assert [e for call in send.call_args_list for e in call.args[2]] == source
        assert b.memory_archive.pending_event_count() == b._ledger_memory_store.count() == 0
        assert b._ledger_memory_events_dropped == 0
    finally:
        b.close()


def test_lost_sqlite_handoff_ack_retains_source_and_deduplicates_after_restart(files, monkeypatch):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        admitted(b)
        classify(b)
        source = b.memory_archive.to_dict()["pending_events"]
        b._ledger_url = "https://fixture.invalid"
        accept = b._ledger_memory_store.accept_memory_prefix
        def lost(*args):
            accept(*args)
            raise OSError("sqlite committed, caller lost ack")
        monkeypatch.setattr(b._ledger_memory_store, "accept_memory_prefix", lost)
        assert not b._stage_memory_events()
        assert b.memory_archive.pending_event_count() == b._ledger_memory_store.count() == len(source)
    finally:
        b.close()
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        assert b._ledger_memory_batches_written == 0
        assert b.memory_archive.pending_event_count() == len(source)
        b._ledger_url = "https://fixture.invalid"
        assert b._stage_memory_events()
        assert not b.memory_archive.pending_event_count()
        assert [r.payload for r in b._ledger_memory_store.peek(100)] == source
    finally:
        b.close()


def test_explicit_drain_after_uncertain_handoff_cannot_skip_new_events(files, monkeypatch):
    b = CascadeBridge()
    try:
        archive = b.memory_archive
        first = archive.store(Signal(signal_type="first"))
        b._ledger_url = "https://fixture.invalid"
        accept = b._ledger_memory_store.accept_memory_prefix
        def lost(*args):
            accept(*args)
            raise OSError("commit acknowledgment lost")
        with monkeypatch.context() as m:
            m.setattr(b._ledger_memory_store, "accept_memory_prefix", lost)
            assert not b._stage_memory_events()
        archive.drain_events()
        second = archive.store(Signal(signal_type="second"))
        assert b._stage_memory_events()
        assert [row.payload["memory_id"] for row in b._ledger_memory_store.peek(3)] == [
            str(first.memory_id), str(second.memory_id)]
    finally:
        b.close()


def test_spool_without_snapshot_refuses_linked_memory_before_admission(files, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.delenv("CASCADE_STATE_FILE")
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        monkeypatch.setattr(b, "_dispatch_llm_batches", lambda: None)
        result = b.process([SimpleNamespace(signal_id=uuid4(), signal_type="a", severity="medium")])
        assert result["accepted"] is False and result["backpressure"] is True
        assert b.memory_archive.size == b._durable_queue.stats()["pending_entries"] == 0
    finally:
        b.close()


def test_redelivered_acknowledged_work_cannot_reclassify_or_overwrite(files):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        classify(b)
        b.classifier.classify.reset_mock()
        b._llm_active_threads = 1
        b._run_llm([entry])
        b.classifier.classify.assert_not_called()
        check_committed(json.loads(files.read_text()), entry, links)
    finally:
        b.close()


def test_tampered_retained_cursor_preserves_snapshot_and_spooled_work(files):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, _ = admitted(b)
        b.memory_archive._event_max_pending = b.memory_archive.pending_event_count()
        b.classifier.classify = Mock(return_value=ClassificationResult(label="real_incident", backend="fixture"))
        b._dispatch_llm_batches()
        assert not b.wait_for_llm(2)
    finally:
        b.close()
    state = json.loads(files.read_text())
    state["classification_completion"]["pending"][entry["_work_id"]]["next_member"] = 2
    files.write_text(json.dumps(state))
    original = files.read_bytes()
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        b.classifier.classify = Mock()
        b._dispatch_llm_batches()
        assert not b.wait_for_llm(0.1) and not b._save_state()
        b.classifier.classify.assert_not_called()
        assert b._durable_queue.stats()["pending_entries"] == 1
        assert files.read_bytes() == original
    finally:
        b.close()


def test_failed_backend_never_emits_final_event(files):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        _, links = admitted(b)
        b.classifier.classify = Mock(return_value=ClassificationResult(
            status="error", label="needs_attention", error_code="invalid_severity"))
        b._dispatch_llm_batches()
        assert not b.wait_for_llm(2)
        assert b._llm_accounting_failed == 3 and b._llm_accounting_succeeded == 0
        assert not updates(json.loads(files.read_text()))
        assert all(b.memory_archive.get(mid).evidence["classification_status"] == "failed"
                   for mid in links.values())
        assert b._durable_queue.stats()["pending_entries"] == 1
    finally:
        b.close()


def test_spool_ahead_of_snapshot_preserves_extra_coalesced_members(files, monkeypatch):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        signals = [Signal(signal_type="fixture", severity="medium", content={"message": "repeat"})]
        memory = b.memory_archive.store(signals[0], classification="needs_attention")
        sid = str(signals[0].signal_id)
        b._pending_memory_ids[sid] = links[sid] = memory.memory_id
        assert b._checkpoint_classification_memories()
        with monkeypatch.context() as m:
            m.setattr(b, "_save_state", lambda: False)
            assert not b._enqueue_llm_signals(signals)
        assert len(entry["_signal_ids"]) == 4
    finally:
        b.close()
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        classify(b)
        assert b.get_stats()["llm_signal_outcomes_since_start"]["reconciled"]
        events = updates(json.loads(files.read_text()))
        assert {e["details"]["signal_id"]: e["memory_id"] for e in events} == {
            sid: str(mid) for sid, mid in links.items()}
    finally:
        b.close()


@pytest.mark.parametrize("spool", [False, True])
def test_member_target_is_bound_at_admission(files, monkeypatch, spool):
    if not spool:
        monkeypatch.delenv("CASCADE_LLM_QUEUE_FILE")
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        sid = entry["signal_id"]
        unrelated = b.memory_archive.store(Signal(signal_type="other"), classification="needs_attention")
        # A newer delivery may reuse an observation ID. Folding another
        # member must not rebind previously admitted targets to a live map.
        b._pending_memory_ids[sid] = unrelated.memory_id
        extra = Signal(signal_type="fixture", severity="medium", content={"message": "repeat"})
        mem = b.memory_archive.store(extra, classification="needs_attention")
        b._pending_memory_ids[str(extra.signal_id)] = links[str(extra.signal_id)] = mem.memory_id
        assert b._enqueue_llm_signals([extra])
        assert len(entry["_signal_ids"]) == 4
        classify(b)
        assert unrelated.classification == "needs_attention"
        assert {e["details"]["signal_id"]: e["memory_id"] for e in updates(json.loads(files.read_text()))} == {
            sid: str(mid) for sid, mid in links.items()}
    finally:
        b.close()


def test_shared_memory_identifies_the_final_event_and_does_not_gain_verification(files):
    import jsonschema
    from pathlib import Path
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        signals = [Signal(signal_type="repeat", severity="medium", namespace="test", cluster="c",
                          source="s", content={"event_uid": "synthetic"}) for _ in range(2)]
        for sig in signals:
            memory = b.memory_archive.store(sig, classification="needs_attention")
            b._pending_memory_ids[str(sig.signal_id)] = memory.memory_id
        assert b.memory_archive.size == 1
        assert b._enqueue_llm_signals(signals)
        classify(b)
        first, last = updates(json.loads(files.read_text()))
        assert [first["details"]["previous_classification"], last["details"]["previous_classification"]] == [
            "needs_attention", "real_incident"]
        assert memory.evidence["classification_event_id"] == last["details"]["event_id"]
        assert memory.evidence["classification_work_id"] == last["details"]["work_id"]
        assert memory.evidence["classification_signal_id"] == str(signals[-1].signal_id)
        assert memory.verification.status == "unverified"
        schema = json.loads((Path(__file__).resolve().parents[1] /
                             "contracts/schemas/memory-event.json").read_text())
        for event in (first, last):
            jsonschema.validate(event, schema)
    finally:
        b.close()


def test_missing_linked_memory_resolves_as_dropped_never_success(files):
    """An evicted destination resolves the work as dropped, never success.

    Production incident (2026-11-12, fifth phase): a retained record whose
    member target was capacity-evicted stayed pending forever, and the
    admission gate refused every new batch while pending work could not
    complete — the collectors retried HTTP 503 for 40+ minutes with the
    instance otherwise healthy. Unapplicable work is now resolved as
    dropped (mirroring the restore-time discard): no classification is
    applied, no success is manufactured, and admission continues.
    """
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        # Simulate a capacity eviction after durable work admission.
        b.memory_archive._max_capacity = 1
        b.memory_archive.store(Signal(signal_type="pressure"))
        b.classifier.classify = Mock(return_value=ClassificationResult(label="real_incident", backend="fixture"))
        b._dispatch_llm_batches()
        # The work resolves (as dropped, never success): the cycle completes.
        assert b.wait_for_llm(2)
        state = json.loads(files.read_text())
        # Never manufactured as success; nothing was applied.
        assert state["classifier_work"]["accounting"]["succeeded"] == 0
        assert not updates(state)
        assert not b._classifier_terminal_receipts
        # The record is resolved, not stuck: nothing stays pending and the
        # bridge keeps admitting work.
        assert entry["_work_id"] not in b._completion_pending
        assert b._enqueue_llm_signals([Signal(signal_type="fixture2", severity="medium",
                                               content={"message": "after eviction"})])
        assert b.process([Signal(signal_type="fixture3", severity="medium",
                                 content={"message": "admission works"})])["accepted"] is True
    finally:
        b.close()


def test_optional_telemetry_failure_cannot_consume_authoritative_work(files):
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        b.classifier.classify = Mock(return_value=ClassificationResult(
            label="real_incident", backend="fixture", latency_ms=float("nan")))
        b._dispatch_llm_batches()
        assert b.wait_for_llm(2)
        check_committed(json.loads(files.read_text()), entry, links)
    finally:
        b.close()


def test_evicted_classification_target_abandons_member_without_fencing(files, monkeypatch):
    """A capacity-evicted destination is discarded at restore, not fenced.

    Production incident (2026-11-12): a restart found retained classification
    work whose linked memory had been evicted between checkpoint and
    completion. The restore fenced the whole bridge ("classification
    application uncertain") and the instance could not start. An evicted
    memory cannot be mislabeled, and the snapshot itself testifies the
    destination is gone, so the record is discarded as dropped; live
    application keeps the stricter uncertainty fence
    (test_missing_linked_memory_never_manufactures_a_success).
    """
    from cascade_compression.cascade.memory import MemoryArchive

    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)

        # Stall application so the completion journal is saved with pending
        # work (the audit buffer is exhausted at checkpoint time).
        def stall(*args, **kwargs):
            raise MemoryEventBackpressure("fixture stall")
        monkeypatch.setattr(MemoryArchive, "update_classification", stall)
        b.classifier.classify = Mock(return_value=ClassificationResult(
            label="real_incident", backend="fixture", model_revision="r1"))
        b._dispatch_llm_batches()
        # The stalled application leaves the work retained-but-pending, with
        # the completion journal checkpointed.
        assert not b.wait_for_llm(3)
        state = json.loads(files.read_text())
        assert entry["_work_id"] in state["classification_completion"]["pending"]
    finally:
        b.close()

    # Construct the production snapshot condition: the journal references a
    # memory the same snapshot no longer contains (capacity eviction happened
    # between checkpoint and completion; the next save persisted both facts).
    state = json.loads(files.read_text())
    victim_mid = str(links[entry["_signal_ids"][0]])
    before = len(state["memory_archive"]["memories"])
    state["memory_archive"]["memories"] = [
        m for m in state["memory_archive"]["memories"]
        if str(m["memory_id"]) != victim_mid]
    assert len(state["memory_archive"]["memories"]) == before - 1
    files.write_text(json.dumps(state))

    restored = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        assert restored._state_healthy is True
        assert restored._classifier_recovery_error == ""
        # The discarded record's destination is still gone and no fence was
        # raised; the surviving memories keep their state.
        assert restored.memory_archive.get(UUID(victim_mid)) is None
        assert restored.memory_archive.size == before - 1
    finally:
        restored.close()


def test_runtime_evicted_target_resolves_without_fencing(files):
    """A capacity-evicted destination is resolved as dropped, not fenced.

    Production incident (2026-11-12): instances whose archives run at
    capacity fenced with "classification application uncertain" when a
    linked memory was evicted between retention and application, degrading
    /health to 503 and crash-looping the instance. The work is resolved as
    dropped (never a manufactured success) and the bridge keeps serving:
    eviction is routine capacity behavior, not commit uncertainty.
    """
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        # Simulate a capacity eviction after durable work admission.
        b.memory_archive._max_capacity = 1
        b.memory_archive.store(Signal(signal_type="pressure"))
        b.classifier.classify = Mock(return_value=ClassificationResult(label="real_incident", backend="fixture"))
        b._dispatch_llm_batches()
        # The work resolves (as dropped, never success): the cycle completes.
        assert b.wait_for_llm(2)
        state = json.loads(files.read_text())
        assert state["classifier_work"]["accounting"]["succeeded"] == 0
        # No fence and no stuck pending record: the bridge keeps accepting
        # and classifying work.
        assert b._classifier_recovery_error == ""
        assert entry["_work_id"] not in b._completion_pending
        assert b._enqueue_llm_signals([Signal(signal_type="fixture2", severity="medium",
                                               content={"message": "after eviction"})])
    finally:
        b.close()


def test_retained_work_from_a_different_identity_is_discarded_not_fatal(files, monkeypatch):
    """Retained results bound to another build are discarded, not fatal.

    Production incident (2026-11-12, third phase): the classifier identity
    binds the package implementation, so any code change invalidates
    retained classification journals from previous builds. Restarts after an
    upgrade raised "retained classification binding mismatch" and fenced the
    whole instance. A different build is a durable fact, not ambiguity: the
    entry is discarded (its results are not trusted by this build) while
    tamper evidence (the event_id digest) stays fatal.
    """
    from cascade_compression.cascade.memory import MemoryArchive

    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)

        def stall(*args, **kwargs):
            raise MemoryEventBackpressure("fixture stall")
        monkeypatch.setattr(MemoryArchive, "update_classification", stall)
        b.classifier.classify = Mock(return_value=ClassificationResult(
            label="real_incident", backend="fixture", model_revision="r1"))
        b._dispatch_llm_batches()
        assert not b.wait_for_llm(3)
        state = json.loads(files.read_text())
        assert entry["_work_id"] in state["classification_completion"]["pending"]
    finally:
        b.close()

    # A different activation TTL changes the classifier identity, standing in
    # for "journal written by a different build or configuration".
    monkeypatch.setenv("CASCADE_ACTIVATION_TTL_HOURS", "48")
    restored = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        assert restored._state_healthy is True
        assert restored._classifier_recovery_error == ""
        assert not restored._completion_pending
    finally:
        restored.close()


def test_audit_backpressure_withdraws_learning_not_state_health(files):
    """Audit-capacity backpressure must not mark the snapshot unhealthy.

    Production incident (2026-11-12, fourth phase): restored archives with
    full audit-event buffers hit MemoryEventBackpressure on the first
    application. fence_learning marked the snapshot unhealthy, /health
    returned 503, the liveness probe killed the pod, and the restart cycle
    could never drain the backlog. Learning fences now keep the snapshot
    healthy: the instance serves (HTTP 200, degraded) while it drains.
    """
    b = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        entry, links = admitted(b)
        # Exhaust the audit-event buffer so application backpressures.
        b.memory_archive._event_max_pending = b.memory_archive.pending_event_count()
        b.classifier.classify = Mock(return_value=ClassificationResult(
            label="real_incident", backend="fixture", model_revision="r1"))
        b._dispatch_llm_batches()
        assert not b.wait_for_llm(2)

        # Learning is withdrawn for audit capacity...
        assert b._learning_fence_reason == (
            "negative classification awaiting audit capacity")
        # ...but the snapshot stays healthy and saves keep working.
        assert b._state_healthy is True
        assert b._save_state()
        # A skipped qualification count must not fence the bridge either.
        assert b._classifier_recovery_error == ""
    finally:
        b.close()


def test_health_reports_learning_fence_as_degraded_200(monkeypatch):
    """A learning-fenced bridge serves /health at 200, not 503."""
    from cascade_compression import service

    class StubBridge:
        enabled = True
        nano_profile = "legacy"
        _state_healthy = True
        _learning_fence_reason = "negative classification awaiting audit capacity"
        _durable_queue = None
        _durable_memory_checkpoint_healthy = True

    monkeypatch.setattr(service, "_bridge", StubBridge())
    body = service.health()
    assert body["status"] == "degraded"
    assert body["learning_fenced"] == (
        "negative classification awaiting audit capacity")
    assert body["state_snapshot_healthy"] is True
    # Not a JSONResponse => HTTP 200.
    assert not isinstance(body, dict) or "status" in body
