"""Local synthetic API-to-archive and qualification regression contracts."""
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.agents import DeduplicateAgent, default_agents
from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.promotion import AgentMetrics, RuleAgent
from cascade_compression.cascade.protocol import Signal
import cascade_compression.service as service
from tests.test_positive_lifecycle import classify
from tests.test_repeat_update import signal


def packet(s):
    return dict(signal_type=s.signal_type, severity=s.severity, source=s.source,
                namespace=s.namespace, cluster=s.cluster, content=s.content,
                labels=s.labels)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    def create(profile="verified_repeat_update"):
        monkeypatch.setenv("CASCADE_NANO_PROFILE", profile)
        monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("CASCADE_LEDGER_URL", "")
        b = CascadeBridge(llm_url="")
        b.corpus_analyzer.maybe_analyze = lambda: []
        b._last_promotion_time = time.monotonic()
        monkeypatch.setattr(service, "_bridge", b)
        return b, TestClient(service.app)
    opened = []
    def tracked(profile="verified_repeat_update"):
        pair = create(profile)
        opened.append(pair[0])
        return pair
    yield tracked
    for b in opened:
        b.close()


def test_cross_batch_growth_is_delivered_and_archived(harness):
    b, client = harness()
    first = client.post("/cascade", json={"signals": [packet(signal(5))]})
    second = client.post("/cascade", json={"signals": [packet(signal(12))]})
    assert first.status_code == second.status_code == 200
    assert first.json()["signals_needing_attention"][0]["content"]["count"] == 5
    assert second.json()["signals_needing_attention"][0]["content"]["count"] == 12
    restored = MemoryArchive.from_dict(json.loads(json.dumps(b.memory_archive.to_dict())))
    assert {m.signal.content["count"] for m in restored.query(limit=20)} == {5, 12}
    again = client.post("/cascade", json={"signals": [packet(signal(12))]})
    assert again.json()["survivors"] == 0


def test_same_batch_fold_reaches_http_archive_and_classifier_queue(harness, monkeypatch):
    b, client = harness()
    monkeypatch.setattr(b, "_classification_dispatch_enabled", lambda: True)
    monkeypatch.setattr(b, "_dispatch_llm_batches", lambda: None)
    response = client.post("/cascade", json={"signals": [packet(signal(5)), packet(signal(12))]})
    assert response.status_code == 200
    assert response.json()["survivors"] == 1
    assert response.json()["signals_needing_attention"][0]["content"]["count"] == 12
    assert b.memory_archive.query(limit=20)[0].signal.content["count"] == 12
    assert len(b._llm_buffer) == 1
    assert b._llm_buffer[0]["content"]["count"] == 12


def test_same_uuid_in_later_batch_cannot_hide_growth():
    a = DeduplicateAgent(update_on_growth=True)
    first, later = signal(5), signal(12)
    later.signal_id = first.signal_id
    assert a.process([first]) == []
    assert a.process([later]) == []
    assert first.content["count"] == 5


def test_caller_mutation_cannot_rewrite_delivered_snapshot():
    a = DeduplicateAgent(update_on_growth=True)
    first = signal(5)
    assert a.process([first]) == []
    first.content["count"] = 12
    assert a.process([first]) == []


def test_growth_survives_real_bridge_restart(harness):
    b, client = harness()
    for count in (5, 12):
        assert client.post("/cascade", json={"signals": [packet(signal(count))]}).status_code == 200
    assert b._save_state()
    b.close()
    again, client = harness()
    assert {m.signal.content["count"] for m in again.memory_archive.query(limit=20)} == {5, 12}
    # Dedup state is not persisted; first delivery after restart safely passes.
    assert client.post("/cascade", json={"signals": [packet(signal(12))]}).json()["survivors"] == 1


def test_growth_never_inherits_old_memory_approval(harness):
    b, client = harness()
    client.post("/cascade", json={"signals": [packet(signal(5))]})
    old = b.memory_archive.query(limit=20)[0]
    b.memory_archive.verify_memory(old.memory_id, outcome="benign", source="synthetic-review",
                                  policy_revision="test-policy", evidence_ref="synthetic:case")
    client.post("/cascade", json={"signals": [packet(signal(12))]})
    versions = {m.signal.content["count"]: m for m in b.memory_archive.query(limit=20)}
    assert versions[5].verification.status == "approved"
    assert versions[12].verification.status == "unverified"


def test_same_batch_preserves_latest_observation_time(harness):
    b, client = harness()
    first, later = signal(5), signal(12)
    first.content["observed_at"] = "2026-01-01T00:00:00Z"
    later.content["observed_at"] = "2026-01-01T00:00:01Z"
    response = client.post("/cascade", json={"signals": [packet(first), packet(later)]})
    assert response.json()["signals_needing_attention"][0]["content"]["observed_at"] == later.content["observed_at"]
    assert b.memory_archive.query(limit=20)[0].signal.content["observed_at"] == later.content["observed_at"]


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "bad"])
def test_domain_window_rejects_invalid_configuration(monkeypatch, value):
    monkeypatch.setenv("CASCADE_DEDUP_WINDOW_KUBERNETES", value)
    with pytest.raises(ValueError, match="finite and non-negative"):
        DeduplicateAgent(update_on_growth=True)


def test_protected_profile_retains_unidentified_info_classification():
    p = CascadePipeline(default_agents(), verified_repeat_update=True)
    s = Signal(signal_type="probe", severity="info", content={"message": "connection refused"})
    assert p.run([s]).remaining == [s]


def test_longer_domain_window_applies_to_effective_method(monkeypatch):
    monkeypatch.setenv("CASCADE_DEDUP_WINDOW_KUBERNETES", "3600")
    a = DeduplicateAgent(window_seconds=60, update_on_growth=True)
    with patch("cascade_compression.cascade.agents.time.monotonic", return_value=0):
        assert a.process([signal(5)]) == []
    with patch("cascade_compression.cascade.agents.time.monotonic", return_value=61):
        assert len(a.process([signal(5)])) == 1
    with patch("cascade_compression.cascade.agents.time.monotonic", return_value=3601):
        assert a.process([signal(5)]) == []


@pytest.fixture
def qualified(harness, monkeypatch):
    b, client = harness("qualified_repeat_update")
    metrics = AgentMetrics(name="synthetic_rule", config={
        "signal_type": "routine", "pattern_type": "dominant_type"})
    b._agent_metrics[metrics.name] = metrics
    b._agent_rules[metrics.name] = RuleAgent({"name": metrics.name, "signal_types": ["routine"]})
    classify(b, monkeypatch, 200)
    b._discover_and_promote()
    assert b._activated_types == {"routine"}
    # End-to-end HTTP tests remain local: no providers or training.
    monkeypatch.setattr(b, "_classification_dispatch_enabled", lambda: False)
    return b, client


def routine(**changes):
    data = dict(signal_type="routine", severity="medium", source="new-source",
                namespace="prod", cluster="test", content={"value": 1}, labels={})
    data.update(changes)
    return data


def test_qualified_authority_reaches_actual_http_suppression(qualified):
    b, client = qualified
    first = client.post("/cascade", json={"signals": [routine()]})
    second = client.post("/cascade", json={"signals": [routine()]})
    assert first.status_code == second.status_code == 200
    assert first.json()["survivors"] == 1
    assert second.json()["survivors"] == 0
    assert second.json()["compressed"] == 1


def test_contextual_qualification_reaches_http(harness, monkeypatch):
    b, client = harness("qualified_repeat_update")
    classify(b, monkeypatch, 200, namespace="prod")
    classify(b, monkeypatch, 1, "needs_attention", namespace="other")
    b._discover_and_promote()
    assert b._activated_contexts.get("routine") == {"prod"}
    monkeypatch.setattr(b, "_classification_dispatch_enabled", lambda: False)
    assert client.post("/cascade", json={"signals": [routine()]}).json()["survivors"] == 1
    assert client.post("/cascade", json={"signals": [routine()]}).json()["survivors"] == 0
    for _ in range(2):
        assert client.post("/cascade", json={"signals": [routine(namespace="other")]}).json()["survivors"] == 1


def test_unqualified_payload_flags_do_not_grant_authority(harness):
    b, client = harness("qualified_repeat_update")
    b._noise_suppressor.add_noise_type("routine")
    request = routine(content={"qualified": True, "approved": True, "value": 1})
    for _ in range(3):
        assert client.post("/cascade", json={"signals": [request]}).json()["survivors"] == 1


def test_human_approval_is_required_and_can_legitimately_qualify(harness, monkeypatch):
    b, client = harness("qualified_repeat_update")
    b._human_gate = True
    b.promotion.human_gate_enabled = True
    metrics = AgentMetrics(name="synthetic_rule", config={
        "signal_type": "routine", "pattern_type": "dominant_type"})
    b._agent_metrics[metrics.name] = metrics
    b._agent_rules[metrics.name] = RuleAgent({"name": metrics.name, "signal_types": ["routine"]})
    classify(b, monkeypatch, 200)
    b._discover_and_promote()
    assert metrics.tier == "pending_approval"
    monkeypatch.setattr(b, "_classification_dispatch_enabled", lambda: False)
    for _ in range(2):
        assert client.post("/cascade", json={"signals": [routine()]}).json()["survivors"] == 1
    b.promotion.approve(metrics, approved_by="synthetic-reviewer")
    b._discover_and_promote()
    assert b._activated_types == {"routine"}
    # The newly activated suppressor retains the first source observation.
    assert client.post("/cascade", json={"signals": [routine()]}).json()["survivors"] == 1
    assert client.post("/cascade", json={"signals": [routine()]}).json()["survivors"] == 0


def test_mcp_python_requirement_is_explicit(monkeypatch):
    from cascade_compression.mcp_server import build_server
    monkeypatch.setattr("sys.version_info", (3, 9, 0))
    with pytest.raises(SystemExit, match="optional MCP server requires Python 3.10"):
        build_server(None)


def test_recent_checkpoint_failure_retains_then_recovers(qualified):
    b, client = qualified
    client.post("/cascade", json={"signals": [routine()]})
    b._save_failures = 1
    assert client.post("/cascade", json={"signals": [routine()]}).json()["survivors"] == 1
    assert b._save_failures == 0
    assert client.post("/cascade", json={"signals": [routine()]}).json()["survivors"] == 0


@pytest.mark.parametrize("case", ["unqualified", "expired", "changed_rule", "negative", "critical", "protected", "invalid_raw"])
def test_http_suppression_fails_open_without_current_authority(qualified, monkeypatch, case):
    b, client = qualified
    request = routine()
    if case == "unqualified":
        b._qualification_evidence.clear()
    elif case == "expired":
        b._activation_timestamps["routine"] = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
    elif case == "changed_rule":
        b._agent_rules["synthetic_rule"].condition["field"] = "changed"
    elif case == "negative":
        classify(b, monkeypatch, 1, "needs_attention")
        monkeypatch.setattr(b, "_classification_dispatch_enabled", lambda: False)
    elif case == "critical":
        request["severity"] = "critical"
    elif case == "protected":
        request["labels"] = {"label": "compliance"}
    else:
        request["severity"] = "not-a-severity"
    for _ in range(2):
        response = client.post("/cascade", json={"signals": [request]})
        assert response.status_code == 200
        assert response.json()["survivors"] == 1
