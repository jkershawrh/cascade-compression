"""Tests for collectors and domain packs."""

import pytest

from cascade_compression.collectors.base import BaseCollector
from cascade_compression.collectors.kubernetes import KubernetesSignal
from cascade_compression.collectors.aap import AAPSignal


class TestBaseCollector:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            BaseCollector()

    def test_describe_default(self):
        class Dummy(BaseCollector):
            def connect(self, config): return True
            def collect(self): return []
            def collect_all(self): return []
        d = Dummy()
        assert d.describe() == {"name": "base", "connected": False}


class TestKubernetesSignal:
    def test_pod_crashloop(self):
        sig = KubernetesSignal({"type": "pod", "name": "nginx", "phase": "Running",
                                "restarts": 10, "reason": "CrashLoopBackOff", "namespace": "default"})
        assert sig.signal_type == "pod_crashloop"
        assert sig.severity == "medium"
        assert sig.labels["domain"] == "kubernetes"

    def test_pod_pending(self):
        sig = KubernetesSignal({"type": "pod", "name": "app", "phase": "Pending",
                                "restarts": 0, "namespace": "staging"})
        assert sig.signal_type == "pod_pending"
        assert sig.severity == "low"

    def test_event(self):
        sig = KubernetesSignal({"type": "event", "name": "pod-1", "reason": "FailedMount",
                                "message": "Unable to mount volume", "namespace": "prod"})
        assert sig.signal_type == "event_failedmount"
        assert sig.severity == "medium"

    def test_node_healthy(self):
        sig = KubernetesSignal({"type": "node", "name": "worker-01",
                                "ready": "True", "memory_pressure": "False", "disk_pressure": "False"})
        assert sig.signal_type == "node_healthy"
        assert sig.severity == "info"

    def test_node_not_ready(self):
        sig = KubernetesSignal({"type": "node", "name": "worker-02",
                                "ready": "False", "memory_pressure": "False", "disk_pressure": "False"})
        assert sig.signal_type == "node_notready"
        assert sig.severity == "critical"


class TestAAPSignal:
    def test_job_succeeded(self):
        sig = AAPSignal({"id": 1, "name": "health-check", "status": "successful",
                         "failed": False, "elapsed": 10}, source="job")
        assert sig.signal_type == "job_successful"
        assert sig.severity == "info"
        assert sig.labels["domain"] == "aap"

    def test_job_failed(self):
        sig = AAPSignal({"id": 2, "name": "provision-cluster", "status": "failed",
                         "failed": True, "elapsed": 300}, source="job")
        assert sig.signal_type == "job_failed"
        assert sig.severity == "high"

    def test_job_failed_prod(self):
        sig = AAPSignal({"id": 3, "name": "prod-deploy", "status": "failed",
                         "failed": True, "elapsed": 60}, source="job")
        assert sig.severity == "critical"

    def test_task_event(self):
        sig = AAPSignal({"id": 4, "event": "runner_on_failed", "failed": True,
                         "host_name": "worker-01", "task": "Install package",
                         "play": "Setup", "role": "", "playbook": "setup.yml",
                         "job_id": 100, "stdout": "error: not found",
                         "job_name": "deploy", "changed": False}, source="task")
        assert sig.signal_type == "task_runner_on_failed"
        assert sig.severity == "high"
        assert "stdout" in sig.evidence

    def test_task_ok(self):
        sig = AAPSignal({"id": 5, "event": "runner_on_ok", "failed": False,
                         "host_name": "localhost", "task": "Gather Facts",
                         "play": "", "role": "", "playbook": "",
                         "job_id": 100, "stdout": "", "job_name": "",
                         "changed": False}, source="task")
        assert sig.signal_type == "task_runner_on_ok"
        assert sig.severity == "info"

    def test_activity_update(self):
        sig = AAPSignal({"id": 6, "operation": "update", "object1": "credential",
                         "object2": "aws-key", "changes": "rotated"}, source="activity")
        assert sig.signal_type == "config_update_credential"
        assert sig.severity == "medium"

    def test_activity_delete(self):
        sig = AAPSignal({"id": 7, "operation": "delete", "object1": "inventory",
                         "object2": "prod-cluster", "changes": ""}, source="activity")
        assert sig.signal_type == "config_delete_inventory"
        assert sig.severity == "high"


class TestDomainPacks:
    def test_kubernetes_pack(self):
        from cascade_compression.domains import kubernetes
        assert kubernetes.DOMAIN == "kubernetes"
        assert kubernetes.COLLECTOR_CLASS is not None
        assert "routine_noise" in kubernetes.SYSTEM_PROMPT

    def test_aap_pack(self):
        from cascade_compression.domains import aap
        assert aap.DOMAIN == "aap"
        assert aap.COLLECTOR_CLASS is not None
        assert "routine_noise" in aap.SYSTEM_PROMPT

    def test_finance_pack(self):
        from cascade_compression.domains import finance
        assert finance.DOMAIN == "finance"
        assert "routine_noise" in finance.SYSTEM_PROMPT
