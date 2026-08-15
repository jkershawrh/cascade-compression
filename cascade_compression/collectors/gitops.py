"""GitOps/ArgoCD collector — Application sync and health state.

READ-ONLY. Get only. Never sync/delete/patch Applications.

Polls K8s API for ArgoCD Application CRDs to track sync drift,
degraded health, and sync failures across the platform.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

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
        self._api_url = ""
        self._token = ""
        self._connected = False

    def connect(self, config: dict) -> bool:
        self._api_url = (
            config.get("api_url", "")
            or os.getenv("GITOPS_API_URL", "")
            or self._detect_in_cluster()
        ).rstrip("/")
        self._token = (
            config.get("token", "")
            or os.getenv("GITOPS_TOKEN", "")
            or self._load_sa_token()
        )
        if not self._api_url:
            return False
        data = self._get("/apis/argoproj.io/v1alpha1/applications?limit=1")
        self._connected = data is not None
        if self._connected:
            log.info("GitOps connected: %s", self._api_url)
        return self._connected

    def collect(self) -> list:
        signals = []
        data = self._get("/apis/argoproj.io/v1alpha1/applications?limit=500")
        if not data:
            return signals

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
                    "name": name, "namespace": ns,
                    "message": f"ArgoCD app {name} degraded: {health_msg[:100]}",
                    "sync_status": sync_status, "health_status": health_status,
                }))
            elif sync_status == "OutOfSync":
                signals.append(GitOpsSignal({
                    "signal_type": "app_outofsync",
                    "severity": "medium",
                    "name": name, "namespace": ns,
                    "message": f"ArgoCD app {name} out of sync",
                    "sync_status": sync_status, "health_status": health_status,
                }))
            elif op_phase == "Failed":
                signals.append(GitOpsSignal({
                    "signal_type": "sync_failed",
                    "severity": "high",
                    "name": name, "namespace": ns,
                    "message": f"ArgoCD app {name} sync failed: {op_msg}",
                    "sync_status": sync_status, "op_phase": op_phase,
                }))
            elif health_status == "Healthy" and sync_status == "Synced":
                signals.append(GitOpsSignal({
                    "signal_type": "app_synced",
                    "severity": "info",
                    "name": name, "namespace": ns,
                    "message": f"ArgoCD app {name} synced and healthy",
                    "sync_status": sync_status, "health_status": health_status,
                }))
            else:
                signals.append(GitOpsSignal({
                    "signal_type": f"app_{health_status.lower()}",
                    "severity": "low",
                    "name": name, "namespace": ns,
                    "message": f"ArgoCD app {name}: sync={sync_status} health={health_status}",
                    "sync_status": sync_status, "health_status": health_status,
                }))

        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "api_url": self._api_url}

    def _get(self, path: str) -> Optional[dict]:
        url = f"{self._api_url}{path}"
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = Request(url, headers=headers)
            ctx = ssl.create_default_context()
            ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
            if os.path.exists(ca_path):
                ctx.load_verify_locations(ca_path)
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("GitOps GET %s: %s", path, str(e)[:100])
            return None

    @staticmethod
    def _load_sa_token() -> str:
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as f:
                return f.read().strip()
        except Exception:
            return ""

    @staticmethod
    def _detect_in_cluster() -> str:
        host = os.getenv("KUBERNETES_SERVICE_HOST", "")
        port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
        if host:
            return f"https://{host}:{port}"
        return ""
