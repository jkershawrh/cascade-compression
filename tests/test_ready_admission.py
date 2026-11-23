"""Readiness must describe the ingest gate, and liveness must not.

The deployed commit served both probes from /health, which consults the state
snapshot, the durable queue and the learning fence but never admission. An
instance that refused 100 % of signals for 21 h therefore reported ok and
ready the whole time, so the platform never restarted or de-routed it.

These tests pin the split: /ready asks the gate, /health only reports it.
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from cascade_compression.bridge import CascadeBridge
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
