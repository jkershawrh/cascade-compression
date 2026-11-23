"""Readiness must describe the ingest gate, and liveness must not.

The deployed commit served both probes from /health, which consults the state
snapshot, the durable queue and the learning fence but never admission. An
instance that refused 100 % of signals for 21 h therefore reported ok and
ready the whole time, so the platform never restarted or de-routed it.

These tests pin the split: /ready asks the gate, /health only reports it.
"""
import threading
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.memory import MemoryArchive, MemoryEvent
import cascade_compression.classification_completion as completion
import cascade_compression.service as service


@pytest.fixture
def harness(monkeypatch, tmp_path):
    monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("CASCADE_LEDGER_URL", "")
    monkeypatch.setenv("CASCADE_NANO_PROFILE", "verified_repeat_update")
    b = CascadeBridge(llm_url="")
    b.corpus_analyzer.maybe_analyze = lambda: []
    b._last_promotion_time = time.monotonic()
    monkeypatch.setattr(service, "_bridge", b)
    try:
        yield b, TestClient(service.app)
    finally:
        b._completion_pending = {}
        b._completion_ack = set()
        b.close()


def test_ready_reports_open_admission(harness):
    _, client = harness
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["admission"]["open"] is True
    assert data["admission"]["blocked_by"] == []


def test_closed_gate_is_not_ready(harness, monkeypatch):
    b, client = harness
    monkeypatch.setattr(completion, "resume", lambda bridge, shed=False: False)
    b._completion_pending["work-1"] = {}
    response = client.get("/ready")
    assert response.status_code == 503
    admission = response.json()["admission"]
    assert admission["open"] is False
    assert admission["blocked_by"] == ["classification_completion_pending"]
    assert admission["classification_completion_pending"] == 1
    assert admission["probed"] is True


def test_health_stays_live_when_the_gate_is_closed(harness, monkeypatch):
    """Liveness must not fail here: restarting restores the same blocked state."""
    b, client = harness
    monkeypatch.setattr(completion, "resume", lambda bridge, shed=False: False)
    b._completion_pending["work-1"] = {}
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["admission"]["open"] is False
    assert body["admission"]["blocked_by"] == ["classification_completion_pending"]


def test_ready_recovers_without_an_ingest_request(harness, monkeypatch):
    """The probe may drain the backlog, so a transient pending set cannot
    strand the instance unready with nothing left to trigger recovery."""
    b, client = harness
    b._completion_pending["work-1"] = {}
    monkeypatch.setattr(completion, "resume", lambda bridge, shed=False: True)
    assert client.get("/ready").status_code == 200


def test_ready_reports_the_durable_spool_depth(harness):
    """A saturated spool must not be reported as an empty one.

    `_ledger_memory_pending` is the RAM-only fallback and is empty whenever a
    spool is configured, so reporting its length showed all 100,000
    undeliverable events on cascade-k8s as `ledger_memory_pending: 0` while
    /stats reported the same spool as 100,000/100,000 full.
    """
    b, client = harness
    assert b._ledger_memory_store is not None
    b._ledger_memory_store.enqueue([
        {"event_type": "formed", "memory_id": f"m-{i}"} for i in range(3)
    ])
    admission = client.get("/ready").json()["admission"]
    assert admission["ledger_memory_pending"] == 3
    assert admission["ledger_memory_pending_max"] == b._ledger_memory_pending_max


def test_spool_read_failure_is_not_reported_as_empty(harness, monkeypatch):
    """Unknown is honest; zero is the bug being fixed."""
    b, client = harness

    def broken():
        raise OSError("spool unreadable")

    monkeypatch.setattr(b._ledger_memory_store, "count", broken)
    assert client.get("/ready").json()["admission"]["ledger_memory_pending"] is None


def test_recovery_error_blocks_readiness(harness):
    b, client = harness
    b._classifier_recovery_error = "audit capacity exceeded"
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["admission"]["blocked_by"] == ["classifier_recovery_error"]


def test_a_full_audit_byte_budget_is_not_ready(harness):
    """The archive buffer is a gate too, and its byte bound binds first.

    cascade-k8s and cascade-aap refused 100 % of signals with
    `memory_audit_backpressure` on 2026-11-23 while /ready answered 200: the
    archive held 86,919 of an advertised 200,000 entries, so the count looked
    healthy while the 16 MB byte budget was full. Readiness must ask the
    condition that process() actually enforces.

    The geometry here is cascade-k8s measured live: 71,594 events occupying
    all but 149 bytes of the 16 MB budget. Zero headroom is not the shape
    that occurs: `_queue_events()` rejects the event that would *exceed* the
    budget, so the buffer settles just below it and a `bytes >= bytes_max`
    test never fires. That is why the first fix reported two refusing
    instances as ready.
    """
    b, client = harness
    archive = b.memory_archive
    # One object repeated: the count is what the gate reads, and any other
    # path may introspect the event shape.
    archive._events = [MemoryEvent(UUID(int=0), "formed")] * 71594
    archive._event_max_pending = 100000   # cascade-k8s advertises this cap
    archive._event_bytes = archive._event_max_bytes - 149

    admission = client.get("/ready").json()["admission"]
    assert admission["open"] is False
    assert "memory_audit_backpressure" in admission["blocked_by"]
    assert admission["memory_audit_source_headroom_bytes"] == 149
    assert admission["memory_audit_source_headroom_bytes"] < admission["memory_audit_admission_floor_bytes"]
    # The count still looks like headroom: that is the misleading surface.
    assert admission["memory_audit_source_pending"] < admission["memory_audit_source_pending_max"]

    # The gate must actually refuse, or this test pins nothing.
    response = client.post("/cascade", json={"signals": [{
        "signal_type": "pod_failed", "severity": "high",
        "source": "k8s", "namespace": "ns", "content": {},
    }]})
    assert response.status_code == 503
    assert response.json()["status"] == "memory_audit_backpressure"


def test_audit_headroom_that_fits_an_event_is_ready(harness):
    """The floor must not close readiness early: real headroom still admits."""
    b, client = harness
    archive = b.memory_archive
    archive._event_bytes = archive._event_max_bytes - archive._min_event_bytes

    admission = client.get("/ready").json()["admission"]
    assert admission["open"] is True
    assert admission["blocked_by"] == []
    assert admission["memory_audit_source_headroom_bytes"] == archive._min_event_bytes


def test_the_count_alone_would_call_a_full_buffer_healthy(harness):
    """Pin the defect the floor exists for: count says 0 %, buffer is full.

    An entry-count predicate reports headroom here; the byte predicate is the
    one that matches the refusal.
    """
    b, client = harness
    archive = b.memory_archive
    archive._event_bytes = archive._event_max_bytes - 1

    assert archive.pending_event_count() == 0
    assert archive.audit_admission_closed() is True
    assert client.get("/ready").status_code == 503


def test_a_full_audit_byte_budget_keeps_liveness(harness):
    """A saturated buffer de-routes the instance; it must not restart it."""
    b, client = harness
    b.memory_archive._event_bytes = b.memory_archive._event_max_bytes
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["admission"]["open"] is False
    assert "memory_audit_backpressure" in body["admission"]["blocked_by"]


def test_ready_does_not_queue_behind_a_busy_ingest(harness, monkeypatch):
    """A probe must never wait on the lifecycle lock an ingest call holds."""
    b, client = harness
    monkeypatch.setattr(completion, "resume", lambda bridge, shed=False: True)
    b._completion_pending["work-1"] = {}
    entered = threading.Event()
    release = threading.Event()

    def hold_lifecycle_lock():
        with b._lifecycle_lock:
            entered.set()
            release.wait(5.0)

    thread = threading.Thread(target=hold_lifecycle_lock)
    thread.start()
    assert entered.wait(5.0)
    try:
        started = time.monotonic()
        response = client.get("/ready")
        elapsed = time.monotonic() - started
    finally:
        release.set()
        thread.join()
    assert response.status_code == 503
    assert response.json()["admission"]["blocked_by"] == ["lifecycle_busy"]
    assert elapsed < 2.0


def test_binding_limit_names_the_byte_cap_on_the_measured_k8s_geometry(harness):
    """Two caps guard one buffer; say which one is actually refusing.

    cascade-k8s held 71,594 of an advertised 100,000 entries at 16,777,067 of
    16,777,216 bytes. The entry cap needed ~23.4 MB, so it was unreachable and
    no amount of raising `CASCADE_MEMORY_EVENT_MAX_PENDING` would have moved
    the gate. Reporting the binding cap makes that answerable from the payload.
    """
    b, client = harness
    archive = b.memory_archive
    archive._events = [MemoryEvent(UUID(int=0), "formed")] * 71594
    archive._event_max_pending = 100000
    archive._event_bytes = archive._event_max_bytes - 149

    admission = client.get("/ready").json()["admission"]
    assert admission["memory_audit_binding_limit"] == "bytes"
    assert admission["memory_audit_source_pending"] < admission["memory_audit_source_pending_max"]


def test_binding_limit_names_the_count_cap_when_bytes_are_out_of_reach(harness):
    """The inverse geometry: cascade-memory at 19,714 of 20,000, 3.6 MB of 16 MiB.

    Here the count binds and the byte budget is ~4.6x away, so the same code
    reports the opposite binding cap on a healthy instance.
    """
    b, client = harness
    archive = b.memory_archive
    archive._events = [MemoryEvent(UUID(int=0), "formed")] * 19714
    archive._event_max_pending = 20000
    archive._event_bytes = 3572687

    admission = client.get("/ready").json()["admission"]
    assert admission["memory_audit_binding_limit"] == "count"
    assert admission["open"] is True


def test_the_byte_budget_is_configurable(monkeypatch):
    """The cap that binds on the failing instances used to be a constant."""
    monkeypatch.setenv("CASCADE_MEMORY_EVENT_MAX_BYTES", str(1024 * 1024))
    archive = MemoryArchive(audit_backpressure=True)
    assert archive.audit_capacity()["bytes_max"] == 1024 * 1024


def test_the_byte_budget_defaults_to_16_mib(monkeypatch):
    monkeypatch.delenv("CASCADE_MEMORY_EVENT_MAX_BYTES", raising=False)
    archive = MemoryArchive(audit_backpressure=True)
    assert archive.audit_capacity()["bytes_max"] == 16 * 1024 * 1024


def test_the_queue_and_readiness_share_one_rule_at_the_count_boundary(harness):
    """The gate that refuses and the gate that reports must be one rule.

    Three independent readings of this buffer produced three false readiness
    claims in one week. Pin the boundary from both sides: one entry of room
    admits and reports open; the last entry admits and reports closed.
    """
    b, client = harness
    archive = b.memory_archive
    archive._event_max_pending = 10
    archive._events = [MemoryEvent(UUID(int=0), "formed")] * 9

    assert archive.audit_admission_closed() is False
    archive._queue_events([MemoryEvent(UUID(int=0), "formed")])
    assert archive.pending_event_count() == 10
    assert archive.audit_admission_closed() is True
    with pytest.raises(Exception) as refused:
        archive._queue_events([MemoryEvent(UUID(int=0), "formed")])
    assert "(count)" in str(refused.value)


def test_readiness_closes_exactly_when_the_floor_no_longer_fits(harness):
    """Closed means "one representative event does not fit", and the queue agrees.

    The floor is the mean queued size (the structural minimum when empty), so
    it can be smaller than a real event: readiness may stay open while a
    markedly larger payload is refused. That direction is deliberate — the
    alternative closes readiness one event early on every instance — and the
    queue is exact about the payload it is handed.
    """
    b, client = harness
    archive = b.memory_archive
    archive._events = []
    archive._event_bytes = archive._event_max_bytes - archive._min_event_bytes + 1

    assert archive.audit_admission_closed() is True
    with pytest.raises(Exception) as refused:
        archive._queue_events([MemoryEvent(UUID(int=0), "formed")])
    assert "(bytes)" in str(refused.value)

    archive._events = []
    archive._event_bytes = archive._event_max_bytes - archive._min_event_bytes
    assert archive.audit_admission_closed() is False


def test_an_oversized_event_is_refused_without_closing_the_buffer(harness):
    """The payload rule is exact to the queue: it cannot be predicted from capacity."""
    b, client = harness
    archive = b.memory_archive
    payload = "x" * 70_000
    event = MemoryEvent(UUID(int=0), "formed", details={"payload": payload})

    with pytest.raises(Exception) as refused:
        archive._queue_events([event])
    assert "(oversized)" in str(refused.value)
    # An empty buffer is still reportable as open: readiness describes capacity,
    # not the size of whatever a caller happens to send.
    assert archive.audit_admission_closed() is False
    assert archive.pending_event_count() == 0
