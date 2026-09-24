"""Safety and API behavior for opt-in nano profiles."""

import hashlib

from cascade_compression import service
from cascade_compression.bridge import CascadeBridge, _to_cascade_signal
from cascade_compression.cascade.agents import DeduplicateAgent, default_agents
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.protocol import Signal
from cascade_compression.cascade.triage import ExactRepeatTriage


def pod_event(*, cluster="cluster-a", uid="uid-a", severity="medium"):
    message = "Readiness probe failed: synthetic timeout"
    return Signal(
        signal_type="event_unhealthy", severity=severity,
        source="pod-a", cluster=cluster, namespace="test",
        content={
            "kind": "Pod", "uid": uid, "event_uid": "event-a",
            "reason": "Unhealthy", "count": 1, "message": message,
            "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
        },
        labels={"domain": "kubernetes"},
    )


def api_event():
    signal = pod_event()
    return service.SignalInput(
        signal_type=signal.signal_type, severity=signal.severity,
        source=signal.source, cluster=signal.cluster, namespace=signal.namespace,
        content=signal.content, labels=signal.labels,
    )


def test_dedup_identity_includes_cluster_uid_and_stable_content():
    agent = DeduplicateAgent()
    first = pod_event()
    other_cluster = pod_event(cluster="cluster-b")
    replacement = pod_event(uid="uid-b")
    assert agent.process([first, other_cluster, replacement]) == []
    assert len(agent.process([pod_event()])) == 1


def test_bridge_preserves_full_cluster_identity():
    class Input:
        signal_id = "signal-a"
        signal_type = "event_unhealthy"
        severity = "medium"
        resource_name = "pod-a"
        namespace = "test"
        cluster_id = "cluster-alpha-production"
        evidence = {}
        labels = {"domain": "kubernetes"}

    assert _to_cascade_signal(Input()).cluster == "cluster-alpha-production"


def test_triage_pipeline_and_advisor_never_remove_signals():
    pipeline = CascadePipeline(default_agents(), triage_only=True)
    advisor = ExactRepeatTriage()
    first, second = pod_event(), pod_event()
    assert pipeline.run([first, second]).remaining == [first, second]
    first_tag = advisor.assess([first])[str(first.signal_id)]
    second_tag = advisor.assess([second])[str(second.signal_id)]
    assert first_tag["exact_repeat"] is False
    assert second_tag["exact_repeat"] is True
    assert second_tag["group_id"] == first_tag["group_id"]


def test_incomplete_identity_is_not_triage_eligible():
    advisor = ExactRepeatTriage()
    for field in ("uid", "event_uid", "reason", "count", "message_sha256"):
        signal = pod_event()
        signal.content.pop(field)
        assert advisor.assess([signal]) == {}


def test_verified_repeat_profile_fails_open_without_complete_identity():
    for field in ("uid", "event_uid", "reason", "count", "message_sha256"):
        pipeline = CascadePipeline(default_agents(), verified_repeat_only=True)
        first, second = pod_event(), pod_event()
        first.content.pop(field)
        second.content.pop(field)
        assert pipeline.run([first]).remaining == [first]
        assert pipeline.run([second]).remaining == [second]


def test_verified_repeat_profile_preserves_first_and_removes_exact_repeat():
    pipeline = CascadePipeline(default_agents(), verified_repeat_only=True)
    first, second = pod_event(), pod_event()
    assert pipeline.run([first]).remaining == [first]
    result = pipeline.run([second])
    assert result.remaining == []
    assert result.deduped_count == 1


def test_triage_api_preserves_every_signal_and_returns_advisory_tag(monkeypatch):
    monkeypatch.setenv("CASCADE_NANO_PROFILE", "triage_only")
    bridge = CascadeBridge()
    monkeypatch.setattr(service, "_bridge", bridge)
    first = service.cascade(service.BatchRequest(signals=[api_event()]))
    second = service.cascade(service.BatchRequest(signals=[api_event()]))
    assert first["compressed"] == second["compressed"] == 0
    assert first["survivors"] == second["survivors"] == 1
    assert first["triaged_repeats"] == 0
    assert second["triaged_repeats"] == 1
    assert second["signals_needing_attention"][0]["triage"]["exact_repeat"] is True
    assert second["signals_needing_attention"][0]["cluster"] == "cluster-a"
    assert service.health()["nano_profile"] == "triage_only"


def test_api_uses_request_local_result_not_mutable_last_remaining(monkeypatch):
    monkeypatch.setenv("CASCADE_NANO_PROFILE", "triage_only")
    bridge = CascadeBridge()
    monkeypatch.setattr(service, "_bridge", bridge)
    process = bridge.process

    def overlapping_process(signals):
        result = process(signals)
        bridge._last_remaining = []
        return result

    monkeypatch.setattr(bridge, "process", overlapping_process)
    response = service.cascade(service.BatchRequest(signals=[api_event()]))
    assert response["survivors"] == 1
    assert response["compressed"] == 0
