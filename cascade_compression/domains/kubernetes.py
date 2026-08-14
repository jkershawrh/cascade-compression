"""Kubernetes domain pack."""

from cascade_compression.collectors.kubernetes import KubernetesCollector

DOMAIN = "kubernetes"
COLLECTOR_CLASS = KubernetesCollector

SYSTEM_PROMPT = """You are classifying Kubernetes signals on a platform where student/sandbox namespaces frequently have crashlooping pods, resource issues, and misconfigurations. This is NORMAL for this environment.

Classify as exactly one of:
- routine_noise: expected on this platform, safe to suppress
- known_pattern: seen before, low priority
- needs_attention: unusual for this environment, investigate
- real_incident: critical, affects platform stability

Answer with one word only."""


def _k8s_entity_extractor(sig):
    """Entity key: node:source:cluster or namespace:cluster."""
    if sig.source:
        return f"node:{sig.source}:{sig.cluster}" if sig.cluster else f"node:{sig.source}"
    if sig.namespace and sig.cluster:
        return f"ns:{sig.namespace}:{sig.cluster}"
    return None


MEMORY_CONFIG = {
    "entity_extractor": _k8s_entity_extractor,

    "causal_rules": [
        ("node_disk_pressure", "event_provisioningfailed"),
        ("node_disk_pressure", "event_volumefaileddelete"),
        ("node_memory_pressure", "event_unhealthy"),
        ("node_memory_pressure", "pod_crashloop"),
        ("event_claimmisbound", "event_volumefaileddelete"),
        ("event_claimmisbound", "event_failedattachvolume"),
        ("event_failedscheduling", "pod_pending"),
        ("node_notready", "event_failedscheduling"),
        ("node_notready", "pod_pending"),
        ("event_backofflimitexceeded", "pod_crashloop"),
    ],

    "decay_overrides": {
        "event_deprecatedannotation": 0.05,
        "event_ipaddresswrongreference": 0.03,
        "node_healthy": 0.1,
        "node_notready": 0.001,
        "pod_crashloop": 0.005,
        "event_claimmisbound": 0.005,
    },

    "expected_signals": [],
}
