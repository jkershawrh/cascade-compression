"""GitOps/ArgoCD collector — Application sync and health state.

READ-ONLY. Get only. Never sync/delete/patch Applications.

Polls K8s API for ArgoCD Application CRDs to track sync drift,
degraded health, and sync failures across the platform.
"""

import logging
import os
from typing import Iterator, List, Optional

from .base import BaseCollector, detect_in_cluster, k8s_api_get, load_sa_token

log = logging.getLogger(__name__)


class GitOpsSignal:
    _counter = 0

    def __init__(self, record: dict):
        GitOpsSignal._counter += 1
        self.signal_id = GitOpsSignal._counter
        self.cluster_id = record.get("cluster", "gitops")
        self.namespace = record.get("namespace", "openshift-gitops")
        self.resource_kind = "Application"
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "gitops", "app": record.get("name", "")}


class GitOpsCollector(BaseCollector):
    name = "gitops"

    def __init__(self):
        self._clusters: List[dict] = []
        self._connected = False

    def connect(self, config: dict) -> bool:
        api_url = config.get("api_url", "") or os.getenv("GITOPS_API_URL", "")
        token = config.get("token", "") or os.getenv("GITOPS_TOKEN", "")
        if api_url:
            self._clusters = [{"name": "single", "api_url": api_url.rstrip("/"), "token": token}]
        else:
            self._clusters = self._discover_clusters()
        if not self._clusters:
            in_cluster = detect_in_cluster()
            if in_cluster:
                self._clusters = [{"name": "local", "api_url": in_cluster, "token": load_sa_token()}]
        if not self._clusters:
            return False
        for c in self._clusters:
            data = k8s_api_get(c["api_url"], "/apis/argoproj.io/v1alpha1/applications?limit=1", c.get("token", ""), label="GitOps")
            if data is not None:
                self._connected = True
                log.info("GitOps connected: %s (%d clusters)", c["name"], len(self._clusters))
                return True
        return False

    def collect(self) -> list:
        signals = []
        for cluster in self._clusters:
            data = k8s_api_get(cluster["api_url"], "/apis/argoproj.io/v1alpha1/applications?limit=500", cluster.get("token", ""), label="GitOps")
            if not data:
                continue
            for item in data.get("items", []):
                meta = item.get("metadata", {})
                status = item.get("status", {})
                name = meta.get("name", "unknown")
                ns = meta.get("namespace", "openshift-gitops")

                sync_status = status.get("sync", {}).get("status", "Unknown")
                health_status = status.get("health", {}).get("status", "Unknown")
                health_msg = status.get("health", {}).get("message", "")

                op = status.get("operationState", {})
                op_phase = op.get("phase", "")
                op_msg = op.get("message", "")[:200]

                if health_status == "Degraded":
                    signals.append(GitOpsSignal({
                        "signal_type": "app_degraded",
                        "severity": "high",
                        "name": name, "namespace": ns, "cluster": cluster["name"],
                        "message": f"ArgoCD app {name} degraded: {health_msg[:100]}",
                        "sync_status": sync_status, "health_status": health_status,
                    }))
                elif sync_status == "OutOfSync":
                    signals.append(GitOpsSignal({
                        "signal_type": "app_outofsync",
                        "severity": "medium",
                        "name": name, "namespace": ns, "cluster": cluster["name"],
                        "message": f"ArgoCD app {name} out of sync",
                        "sync_status": sync_status, "health_status": health_status,
                    }))
                elif op_phase == "Failed":
                    signals.append(GitOpsSignal({
                        "signal_type": "sync_failed",
                        "severity": "high",
                        "name": name, "namespace": ns, "cluster": cluster["name"],
                        "message": f"ArgoCD app {name} sync failed: {op_msg}",
                        "sync_status": sync_status, "op_phase": op_phase,
                    }))
                elif health_status == "Healthy" and sync_status == "Synced":
                    signals.append(GitOpsSignal({
                        "signal_type": "app_synced",
                        "severity": "info",
                        "name": name, "namespace": ns, "cluster": cluster["name"],
                        "message": f"ArgoCD app {name} synced and healthy",
                        "sync_status": sync_status, "health_status": health_status,
                    }))
                else:
                    signals.append(GitOpsSignal({
                        "signal_type": f"app_{health_status.lower()}",
                        "severity": "low",
                        "name": name, "namespace": ns, "cluster": cluster["name"],
                        "message": f"ArgoCD app {name}: sync={sync_status} health={health_status}",
                        "sync_status": sync_status, "health_status": health_status,
                    }))

        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "clusters": len(self._clusters)}

    @staticmethod
    def _discover_clusters() -> List[dict]:
        clusters = []
        for i in range(1, 20):
            url = os.getenv(f"CLUSTER_{i}_API_URL", "")
            if not url:
                continue
            clusters.append({
                "name": os.getenv(f"CLUSTER_{i}_NAME", f"cluster-{i}"),
                "api_url": url.rstrip("/"),
                "token": os.getenv(f"CLUSTER_{i}_TOKEN", ""),
            })
        return clusters

