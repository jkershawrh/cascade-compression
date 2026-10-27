"""Kubernetes domain pack."""

from cascade_compression.collectors.kubernetes import KubernetesCollector

DOMAIN = "kubernetes"
COLLECTOR_CLASS = KubernetesCollector

SYSTEM_PROMPT = """You are classifying Kubernetes signals on a platform where student/sandbox namespaces frequently have crashlooping pods, resource issues, and misconfigurations. This is NORMAL for this environment.

The noise/pattern line: noise is repetition with no content; pattern is an expected event with content. If deleting all future occurrences would cost nothing, it is noise. If they tell you something, it is a pattern.

Classify as exactly one of:
- routine_noise: content-free repetition, safe to suppress
- known_pattern: expected event that still informs, seen before
- needs_attention: unusual for this environment, investigate soon
- real_incident: critical, affects platform stability, operator should respond

Answer with one word only."""

# Truth-informed few-shot exemplars (from the 2026-10-26 human adjudication).
# Validated: 6x agreement improvement on held-out cases; needs_attention
# recall 0% -> 71%. Update when new adjudicated truth becomes available.
FEW_SHOT_EXEMPLARS = {
    "known_pattern": [
        "A resource pool has reached its configured minimum under normal load",
        "A playbook step was skipped because its condition did not apply to that host",
        "A resource claim is progressing through its expected provisioning lifecycle",
        "A resource claim's binding is stale and will be reconciled on the next cycle",
    ],
    "needs_attention": [
        "A volume is bound to a node that no longer exists, blocking scheduling",
        "Storage provisioning failed with an internal error from the storage system",
        "A resource claim is stuck in a failed provisioning state and needs intervention",
        "A workload failed to start and the error requires investigation",
    ],
    "real_incident": [
        "Most sandboxes on a cluster are failing simultaneously",
        "A user environment request failed terminally and the provisioner has given up",
    ],
    "routine_noise": [
        "Scheduled reconciliation completed successfully",
        "Periodic health check reports healthy",
        "Heartbeat signal confirmed component is responsive",
    ],
}


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
        ("metric_node_disk_pressure", "event_provisioningfailed"),
        ("metric_node_disk_pressure", "event_volumefaileddelete"),
        ("metric_node_memory_pressure", "event_unhealthy"),
        ("metric_node_memory_exhausted", "event_unhealthy"),
        ("metric_node_notready", "event_failedscheduling"),
        ("metric_node_cpu_saturated", "metric_api_server_slow"),
        ("metric_etcd_slow_commits", "metric_api_server_slow"),
        ("pool_exhausted", "anarchy_subject_failed"),
        ("pool_low", "anarchy_subject_failed"),
        ("sync_failed", "app_degraded"),
        ("ceph_health_err", "blockpool_failure"),
        ("ceph_health_err", "cephfs_failure"),
        ("ceph_health_err", "pvc_pending"),
        ("ceph_health_err", "pvc_lost"),
        ("ceph_health_warn", "pvc_pending"),
        ("blockpool_failure", "pvc_pending"),
        ("blockpool_failure", "event_failedattachvolume"),
        ("osd_crashloop", "ceph_health_err"),
        ("osd_not_running", "ceph_health_err"),
        ("metric_osd_down", "ceph_health_err"),
        ("metric_osd_down", "pvc_pending"),
        ("metric_ceph_pool_near_full", "ceph_health_warn"),
        ("metric_osd_near_full", "ceph_health_warn"),
        ("storagecluster_error", "ceph_health_err"),
        ("pvc_lost", "event_volumefaileddelete"),
        ("pv_failed", "event_failedattachvolume"),
    ],

    "decay_overrides": {
        "event_deprecatedannotation": 0.05,
        "event_ipaddresswrongreference": 0.03,
        "node_healthy": 0.1,
        "node_notready": 0.001,
        "pod_crashloop": 0.005,
        "event_claimmisbound": 0.005,
        "ceph_health_ok": 0.1,
        "blockpool_healthy": 0.1,
        "cephfs_healthy": 0.1,
        "ceph_health_err": 0.001,
        "osd_crashloop": 0.001,
        "pvc_lost": 0.002,
    },

    "expected_signals": [],
}
