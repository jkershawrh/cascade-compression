"""Adversarial contract tests for the verified repeat-with-update profile.

CDD: written before implementation. The profile suppresses count-increment
re-deliveries while refreshing the retained observation's state, so the
retained record always reflects the final count (the pre-registered
dangerous-miss property) and every fold-in is auditable.
"""
from uuid import uuid4

import pytest

from cascade_compression.cascade.agents import DeduplicateAgent
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.protocol import Outcome, Signal
from cascade_compression.cascade.repeat_integrity import (
    verified_repeat_key, verified_repeat_update_key,
)


def signal(count, message="probe failed", severity="medium",
           event_uid="10000000-0000-4000-8000-0000000000up",
           namespace="app", source="collector", cluster="prod"):
    import hashlib
    return Signal(
        signal_id=uuid4(), signal_type="event_unhealthy", severity=severity,
        source=source, namespace=namespace, cluster=cluster,
        labels={"domain": "kubernetes"},
        content={"message": message, "kind": "Pod", "name": "worker",
                 "uid": "pod-worker", "event_uid": event_uid,
                 "reason": "Unhealthy", "count": count,
                 "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                 "timestamp": f"2026-10-22T00:00:{count:02d}Z"})


class TestUpdateKey:
    def test_update_key_excludes_count(self):
        a = verified_repeat_update_key(signal(5))
        b = verified_repeat_update_key(signal(12))
        assert a is not None and a == b

    def test_update_key_includes_message_digest(self):
        a = verified_repeat_update_key(signal(5, message="probe failed"))
        b = verified_repeat_update_key(signal(5, message="different failure"))
        assert a != b

    def test_update_key_admission_requires_identity(self):
        assert verified_repeat_update_key(signal(5, event_uid="")) is None
        assert verified_repeat_update_key(signal(5, namespace="")) is None

    def test_update_key_admits_any_kind_with_complete_identity(self):
        # The update profile's scope extends beyond Pod events: any involved
        # object kind with complete identity is admissible, and the kind
        # participates in the key so cross-kind observations never merge.
        s = signal(5)
        s.content["kind"] = "ReplicaSet"
        key_rs = verified_repeat_update_key(s)
        assert key_rs is not None
        s2 = signal(5)
        s2.content["kind"] = "Pod"
        assert verified_repeat_update_key(s2) != key_rs

    def test_update_key_requires_nonempty_kind(self):
        s = signal(5)
        s.content["kind"] = ""
        assert verified_repeat_update_key(s) is None

    def test_update_key_rejects_high_severity_scope(self):
        assert verified_repeat_update_key(signal(5, severity="high")) is None


class TestAgentUpdateMode:
    def test_count_growth_suppresses_and_refreshes_retained(self):
        agent = DeduplicateAgent(update_on_growth=True)
        first, grown = signal(5), signal(12)
        decisions = agent.process([first, grown])
        # The re-delivery is suppressed with an auditable transition.
        assert len(decisions) == 1
        assert decisions[0].outcome == Outcome.DEDUPE
        assert "5" in decisions[0].evidence and "12" in decisions[0].evidence
        # The retained observation now carries the final count.
        assert first.content["count"] == 12

    def test_count_decrease_delivers(self):
        agent = DeduplicateAgent(update_on_growth=True)
        first, lower = signal(12), signal(5)
        decisions = agent.process([first, lower])
        # Non-monotonic counts are suspect: no suppression for the decrease.
        decreaser = [d for d in decisions if d.signal_id == lower.signal_id]
        assert not decreaser
        assert first.content["count"] == 12

    def test_unchanged_repeat_suppresses(self):
        agent = DeduplicateAgent(update_on_growth=True)
        a, b = signal(7), signal(7)
        decisions = agent.process([a, b])
        assert len(decisions) == 1 and decisions[0].outcome == Outcome.DEDUPE

    def test_different_identity_delivers(self):
        agent = DeduplicateAgent(update_on_growth=True)
        a = signal(5, event_uid="10000000-0000-4000-8000-0000000000aa")
        b = signal(9, event_uid="10000000-0000-4000-8000-0000000000bb")
        decisions = agent.process([a, b])
        assert not decisions

    def test_timestamp_refreshes_with_count(self):
        agent = DeduplicateAgent(update_on_growth=True)
        first, grown = signal(5), signal(12)
        agent.process([first, grown])
        assert first.content["timestamp"].endswith("12Z")


class TestPipelineProfile:
    def test_profile_retains_final_count_only(self):
        from cascade_compression.cascade.agents import default_agents
        pipeline = CascadePipeline(agents=default_agents(), verified_repeat_update=True)
        observations = [signal(c) for c in (3, 3, 4, 5, 5)]
        result = pipeline.run(observations)
        # One survivor per identity, carrying the final observed count.
        assert len(result.remaining) == 1
        assert result.remaining[0].content["count"] == 5

    def test_profile_is_mutually_exclusive(self):
        with pytest.raises(ValueError):
            CascadePipeline(verified_repeat_only=True, verified_repeat_update=True)

    def test_profile_fail_open_on_high_severity(self):
        from cascade_compression.cascade.agents import default_agents
        pipeline = CascadePipeline(agents=default_agents(), verified_repeat_update=True)
        criticals = [signal(c, severity="critical") for c in (3, 4, 5)]
        result = pipeline.run(criticals)
        # High/critical signals are never removed by this profile.
        assert len(result.remaining) >= 1
        suppressed = [d for d in result.decisions
                      if d.signal_id in {s.signal_id for s in criticals}]
        assert not suppressed


class TestDomainAgnosticAdmission:
    """The verified repeat profiles must be domain-agnostic: any domain label
    and any signal_type prefix can be admitted as long as identity, digest,
    and count requirements are met. The domain and signal_type participate
    in the key material so cross-domain observations never merge."""

    def test_aap_domain_admitted(self):
        s = signal(5)
        s.labels = {"domain": "aap"}
        assert verified_repeat_update_key(s) is not None

    def test_kubernetes_domain_admitted(self):
        s = signal(5)
        s.labels = {"domain": "kubernetes"}
        assert verified_repeat_update_key(s) is not None

    def test_no_domain_admitted(self):
        s = signal(5)
        s.labels = {}
        assert verified_repeat_update_key(s) is not None

    def test_task_signal_type_admitted(self):
        s = signal(5)
        s.signal_type = "task_runner_on_failed"
        assert verified_repeat_update_key(s) is not None

    def test_cross_domain_keys_differ(self):
        k8s = signal(5)
        k8s.labels = {"domain": "kubernetes"}
        aap = signal(5)
        aap.labels = {"domain": "aap"}
        assert verified_repeat_update_key(k8s) != verified_repeat_update_key(aap)

    def test_cross_type_keys_differ(self):
        event = signal(5)
        event.signal_type = "event_unhealthy"
        task = signal(5)
        task.signal_type = "task_runner_on_failed"
        assert verified_repeat_update_key(event) != verified_repeat_update_key(task)

    def test_compliance_label_still_bypasses(self):
        s = signal(5)
        s.labels = {"domain": "kubernetes", "label": "compliance"}
        assert verified_repeat_update_key(s) is None
