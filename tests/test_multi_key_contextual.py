"""Multi-key contextual decomposition: suppress signals by multiple context axes."""

import pytest
from uuid import uuid4

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.dynamic_agents import ContextualNoiseSuppressor
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.protocol import Outcome, Signal


def _signal(signal_type="t", source="src", namespace="ns", severity="medium"):
    return Signal(signal_type=signal_type, severity=severity,
                  source=source, namespace=namespace, cluster="c")


def test_multiple_context_keys_create_multiple_suppressors():
    """Each configured key gets its own suppressor with a unique name."""
    s1 = ContextualNoiseSuppressor(context_key="namespace")
    s2 = ContextualNoiseSuppressor(context_key="source")
    assert s1.name != s2.name
    assert "namespace" in s1.name
    assert "source" in s2.name


def test_source_keyed_suppressor_matches_source_not_namespace():
    """A source-keyed suppressor groups by the signal's source attribute."""
    suppressor = ContextualNoiseSuppressor(context_key="source")
    suppressor.add_noise_context("task_verbose", "DeployCluster")
    # Send each signal twice: first occurrence passes, second is suppressed
    signals = [
        _signal("task_verbose", source="DeployCluster", namespace="aap-general"),
        _signal("task_verbose", source="DeployCluster", namespace="aap-general"),
        _signal("task_verbose", source="OtherPlaybook", namespace="aap-general"),
        _signal("task_verbose", source="OtherPlaybook", namespace="aap-general"),
    ]
    decisions = suppressor.process(signals)
    # Only the DeployCluster repeats are suppressed (source is a registered noise context)
    # OtherPlaybook is not registered, so its repeats pass through
    assert len(decisions) == 1
    assert decisions[0].outcome == Outcome.SUPPRESS
    assert "DeployCluster" in decisions[0].evidence


def test_namespace_keyed_suppressor_matches_namespace():
    """A namespace-keyed suppressor groups by the signal's namespace attribute."""
    suppressor = ContextualNoiseSuppressor(context_key="namespace")
    suppressor.add_noise_context("event_unhealthy", "openshift-cnv")
    signals = [
        _signal("event_unhealthy", namespace="openshift-cnv"),
        _signal("event_unhealthy", namespace="openshift-cnv"),
        _signal("event_unhealthy", namespace="openshift-monitoring"),
        _signal("event_unhealthy", namespace="openshift-monitoring"),
    ]
    decisions = suppressor.process(signals)
    assert len(decisions) == 1
    assert "openshift-cnv" in decisions[0].evidence


def test_multi_key_pipeline_both_suppressors_active():
    """Both namespace and source suppressors can be registered simultaneously."""
    ns_sup = ContextualNoiseSuppressor(context_key="namespace")
    src_sup = ContextualNoiseSuppressor(context_key="source")
    ns_sup.add_noise_context("mixed_type", "safe-namespace")
    src_sup.add_noise_context("mixed_type", "safe-playbook")

    signals = [
        _signal("mixed_type", source="safe-playbook", namespace="prod"),
        _signal("mixed_type", source="safe-playbook", namespace="prod"),  # source match (repeat)
        _signal("mixed_type", source="other", namespace="safe-namespace"),
        _signal("mixed_type", source="other", namespace="safe-namespace"),  # namespace match (repeat)
        _signal("mixed_type", source="other", namespace="prod"),
        _signal("mixed_type", source="other", namespace="prod"),  # no match (repeat)
    ]
    d1 = ns_sup.process(signals)
    d2 = src_sup.process(signals)
    # Namespace suppressor catches the safe-namespace repeat
    assert len(d1) == 1
    assert "safe-namespace" in d1[0].evidence
    # Source suppressor catches the safe-playbook repeat
    assert len(d2) == 1
    assert "safe-playbook" in d2[0].evidence


def test_context_keys_dont_cross_contaminate():
    """A source value doesn't trigger a namespace-keyed suppressor and vice versa."""
    ns_sup = ContextualNoiseSuppressor(context_key="namespace")
    ns_sup.add_noise_context("type_a", "some-value")
    # Signal has source="some-value" but namespace="different"
    sig1 = _signal("type_a", source="some-value", namespace="different")
    sig2 = _signal("type_a", source="some-value", namespace="different")
    decisions = ns_sup.process([sig1, sig2])
    # The namespace suppressor checks namespace="different", not source
    assert len(decisions) == 0
