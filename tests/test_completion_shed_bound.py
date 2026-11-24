"""A pending completion may not hold admission closed forever.

apply() defers indefinitely while the audit destination raises
MemoryEventBackpressure, and process() refuses all input while resume()
cannot commit. With the destination hung that is an unbounded ingest outage:
cascade-k8s refused every signal from 2026-11-21T05:02Z onward.

The bound discards the work instead -- counted, never applied, never reported
as success -- which is the same terminal shape apply() already uses when a
linked memory was capacity-evicted. The replay-evidence path must never take
this escape, so it does not pass shed=True.
"""
import time

import pytest

from cascade_compression.bridge import CascadeBridge
import cascade_compression.classification_completion as completion
import cascade_compression.service as service


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("CASCADE_LEDGER_URL", "")
    monkeypatch.setenv("CASCADE_NANO_PROFILE", "verified_repeat_update")
    b = CascadeBridge(llm_url="")
    b.corpus_analyzer.maybe_analyze = lambda: []
    b._last_promotion_time = time.monotonic()
    monkeypatch.setattr(service, "_bridge", b)
    try:
        yield b
    finally:
        b._completion_pending = {}
        b._completion_ack = set()
        b.close()


def staged(bridge, work_id="work-1", age_seconds=0.0, members=1):
    record = {
        "result": {"label": "noise", "cache_hit": False, "provenance": {},
                   "classifier_identity": "x" * 64, "completed_at": "", "event_id": "y" * 64},
        "members": [[f"signal-{i}", None] for i in range(members)],
        "context": {},
        "next_member": 0,
    }
    if age_seconds is not None:
        from datetime import datetime, timedelta, timezone
        record["retained_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    bridge._completion_pending[work_id] = record
    return record


def test_expired_record_is_discarded_and_counted(bridge, monkeypatch):
    monkeypatch.setattr(completion, "apply", lambda b, w: False)
    staged(bridge, age_seconds=bridge._completion_pending_max_age + 60, members=3)
    assert completion.resume(bridge, shed=True) is True
    assert bridge._completion_pending == {}
    assert bridge._llm_dropped_audit_unavailable == 3
    assert bridge._llm_accounting_dropped == 3


def test_young_record_still_waits(bridge, monkeypatch):
    monkeypatch.setattr(completion, "apply", lambda b, w: False)
    staged(bridge, age_seconds=1.0)
    assert completion.resume(bridge, shed=True) is False
    assert list(bridge._completion_pending) == ["work-1"]
    assert bridge._llm_dropped_audit_unavailable == 0


def test_replay_evidence_path_never_discards(bridge, monkeypatch):
    """wait_for_llm must report incomplete evidence, not resolve it away."""
    monkeypatch.setattr(completion, "apply", lambda b, w: False)
    staged(bridge, age_seconds=bridge._completion_pending_max_age + 60)
    assert completion.resume(bridge, shed=False) is False
    assert list(bridge._completion_pending) == ["work-1"]
    assert bridge._llm_dropped_audit_unavailable == 0


def test_healthy_commit_is_never_shed(bridge, monkeypatch):
    """Age alone is not a loss. A record that commits is applied, not dropped."""
    monkeypatch.setattr(completion, "apply", lambda b, w: b._completion_pending.pop(w) is not None)
    staged(bridge, age_seconds=bridge._completion_pending_max_age + 60, members=2)
    assert completion.resume(bridge, shed=True) is True
    assert bridge._completion_pending == {}
    assert bridge._llm_dropped_audit_unavailable == 0
    assert bridge._llm_accounting_dropped == 0


def test_restored_record_is_stamped_not_discarded(bridge, monkeypatch):
    """A record reloaded from a snapshot has no retention time yet."""
    monkeypatch.setattr(completion, "apply", lambda b, w: False)
    staged(bridge, age_seconds=None)
    assert completion.resume(bridge, shed=True) is False
    record = bridge._completion_pending["work-1"]
    assert "retained_at" in record
    assert bridge._llm_dropped_audit_unavailable == 0


def test_uncertain_application_is_fenced_not_shed(bridge, monkeypatch):
    """A fence means the state is unknown. Discarding there would be a lie."""
    def fenced(b, w):
        b._classifier_recovery_error = "classification application uncertain: OSError"
        return False

    monkeypatch.setattr(completion, "apply", fenced)
    staged(bridge, age_seconds=bridge._completion_pending_max_age + 60)
    assert completion.resume(bridge, shed=True) is False
    assert list(bridge._completion_pending) == ["work-1"]
    assert bridge._llm_dropped_audit_unavailable == 0


def test_no_progress_deferral_does_not_checkpoint(bridge, monkeypatch):
    """A refused signal must not pay a full archive snapshot on every retry.

    apply() checkpoints after a deferral so next_member is durable. When the
    deferral advanced nothing and the learning fence already carried the same
    reason, the snapshot would be identical -- and on cascade-k8s it was
    several minutes of work per rejection.
    """
    import threading
    import uuid

    from cascade_compression.cascade.memory import MemoryEventBackpressure

    class Store:
        _lock = threading.RLock()
        _events: list = []
        _event_bytes = 0
        classification = None
        last_modified_at = ""
        evidence: dict = {}

        def get(self, mid):
            return self

        def update_classification(self, *args, **kwargs):
            raise MemoryEventBackpressure("audit buffer at capacity")

    record = staged(bridge, work_id="work-1")
    record["result"] = {"label": "real_incident", "cache_hit": False,
                        "provenance": {}, "classifier_identity": "x" * 64,
                        "completed_at": "", "event_id": "y" * 64}
    record["members"] = [["signal-0", str(uuid.uuid4())]]

    real_archive = bridge.memory_archive
    bridge.memory_archive = Store()
    calls: list = []
    monkeypatch.setattr(bridge, "_save_state", lambda **kwargs: calls.append(1) or True)
    try:
        assert completion.apply(bridge, "work-1") is False
        first_pass = len(calls)
        assert first_pass == 1, "the first deferral changes the fence and must checkpoint"
        assert completion.apply(bridge, "work-1") is False
        assert len(calls) == first_pass, "an identical deferral must not checkpoint"
        assert bridge._learning_fence_reason == (
            "negative classification awaiting audit capacity")
    finally:
        bridge.memory_archive = real_archive


def test_shedding_reopens_admission(bridge, monkeypatch):
    """The bound is what turns an unbounded outage into a bounded one."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(completion, "apply", lambda b, w: False)
    record = staged(bridge, age_seconds=1.0)
    assert bridge.admission_state(probe=True)["open"] is False
    assert bridge._completion_pending, "a young record must still hold the gate"

    record["retained_at"] = (
        datetime.now(timezone.utc)
        - timedelta(seconds=bridge._completion_pending_max_age + 60)).isoformat()
    # Readiness only reads. If the probe discarded past the bound, the kubelet
    # would be resolving evidence away; the drain path owns that.
    assert bridge.admission_state(probe=True)["open"] is False, (
        "a probe must not shed")
    assert record is bridge._completion_pending["work-1"]
    assert completion.resume(bridge, shed=True) is True
    assert bridge.admission_state(probe=True)["open"] is True
    assert bridge._llm_dropped_audit_unavailable == 1
