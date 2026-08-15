"""Prometheus/Thanos collector — reads alerts and metric thresholds.

READ-ONLY. Query only. Never modify alerting rules or silence alerts.

Polls Thanos Querier for health metric thresholds and Alertmanager for
active alerts. Fills the causal gaps the inverse cascade identified:
node_disk_pressure, node_memory_pressure, node_notready.

PromQL queries adapted from stargate-platform/engine/prometheus_miner.py.
Alert classification adapted from stargate-platform/engine/alertmanager_miner.py.
"""

import json
import logging
import os
import ssl
import time
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


HEALTH_QUERIES = {
    "pod_restarts": {
        "query": "sort_desc(kube_pod_container_status_restarts_total>10)",
        "signal_type": "metric_pod_high_restarts",
        "severity": "medium",
    },
    "oom_kills": {
        "query": "increase(kube_pod_container_status_last_terminated_reason{reason='OOMKilled'}[7d])>0",
        "signal_type": "metric_container_oom",
        "severity": "high",
    },
    "cpu_saturation": {
        "query": "instance:node_cpu_utilisation:rate5m>0.8",
        "signal_type": "metric_node_cpu_saturated",
        "severity": "medium",
    },
    "pvc_filling": {
        "query": "(kubelet_volume_stats_used_bytes/kubelet_volume_stats_capacity_bytes)>0.85",
        "signal_type": "metric_pvc_nearly_full",
        "severity": "medium",
    },
    "node_memory": {
        "query": "(1-node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)>0.9",
        "signal_type": "metric_node_memory_exhausted",
        "severity": "critical",
    },
    "api_latency": {
        "query": "histogram_quantile(0.99,rate(apiserver_request_duration_seconds_bucket{verb!='WATCH'}[5m]))>1",
        "signal_type": "metric_api_server_slow",
        "severity": "medium",
    },
    "etcd_latency": {
        "query": "histogram_quantile(0.99,rate(etcd_disk_backend_commit_duration_seconds_bucket[5m]))>0.1",
        "signal_type": "metric_etcd_slow_commits",
        "severity": "medium",
    },
    "pending_pods": {
        "query": "kube_pod_status_phase{phase='Pending'}>0",
        "signal_type": "metric_pod_stuck_pending",
        "severity": "medium",
    },
    "node_disk_pressure": {
        "query": "kube_node_status_condition{condition='DiskPressure',status='true'}==1",
        "signal_type": "metric_node_disk_pressure",
        "severity": "critical",
    },
    "node_memory_pressure": {
        "query": "kube_node_status_condition{condition='MemoryPressure',status='true'}==1",
        "signal_type": "metric_node_memory_pressure",
        "severity": "high",
    },
    "node_not_ready": {
        "query": "kube_node_status_condition{condition='Ready',status='true'}==0",
        "signal_type": "metric_node_notready",
        "severity": "critical",
    },
}

ALERT_SEVERITY_MAP = {
    "critical": "critical",
    "warning": "medium",
    "info": "low",
    "none": "info",
}


class PrometheusSignal:
    _counter = 0

    def __init__(self, record: dict):
        PrometheusSignal._counter += 1
        self.signal_id = PrometheusSignal._counter
        self.cluster_id = record.get("cluster", "unknown")
        self.namespace = record.get("namespace", "")
        self.resource_kind = record.get("source", "prometheus")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "prometheus", "cluster": self.cluster_id}


class PrometheusCollector(BaseCollector):
    name = "prometheus"

    def __init__(self):
        self._thanos_url = ""
        self._alertmanager_url = ""
        self._token = ""
        self._clusters: Dict[str, Dict[str, str]] = {}
        self._connected = False
        self._last_alert_ids: set = set()

    def connect(self, config: dict) -> bool:
        self._thanos_url = (
            config.get("thanos_url", "")
            or os.getenv("PROMETHEUS_URL", "")
        ).rstrip("/")
        self._alertmanager_url = (
            config.get("alertmanager_url", "")
            or os.getenv("ALERTMANAGER_URL", "")
        ).rstrip("/")
        self._token = (
            config.get("token", "")
            or os.getenv("PROMETHEUS_TOKEN", "")
        )

        cluster_urls = os.getenv("PROMETHEUS_CLUSTER_URLS", "")
        if cluster_urls:
            try:
                self._clusters = json.loads(cluster_urls)
            except (json.JSONDecodeError, ValueError):
                log.warning("PROMETHEUS_CLUSTER_URLS is not valid JSON")

        if not self._thanos_url and not self._clusters and not self._alertmanager_url:
            log.warning("No Prometheus/Thanos/Alertmanager URL configured")
            return False

        if self._thanos_url:
            data = self._query(self._thanos_url, "up", self._token)
            self._connected = data is not None
        elif self._alertmanager_url:
            self._connected = self._get_alerts(self._alertmanager_url, self._token) is not None
        else:
            self._connected = True

        if self._connected:
            log.info("Prometheus connected: thanos=%s alertmanager=%s clusters=%d",
                     self._thanos_url or "none", self._alertmanager_url or "none",
                     len(self._clusters))
        return self._connected

    def collect(self) -> list:
        signals = []
        signals.extend(self._collect_metrics())
        signals.extend(self._collect_alerts())
        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "thanos_url": self._thanos_url,
            "alertmanager_url": self._alertmanager_url,
            "clusters": list(self._clusters.keys()),
        }

    def _collect_metrics(self) -> List[PrometheusSignal]:
        signals = []
        targets = []
        if self._thanos_url:
            targets.append(("central", self._thanos_url, self._token))
        for cluster, cfg in self._clusters.items():
            url = cfg if isinstance(cfg, str) else cfg.get("url", "")
            token = cfg.get("token", self._token) if isinstance(cfg, dict) else self._token
            if url:
                targets.append((cluster, url, token))

        for cluster, url, token in targets:
            for qname, qcfg in HEALTH_QUERIES.items():
                results = self._query(url, qcfg["query"], token)
                if results is None:
                    continue
                for r in results:
                    metric = r.get("metric", {})
                    value = float(r.get("value", [0, 0])[1])
                    ns = metric.get("namespace", "")
                    name = (
                        metric.get("pod", "")
                        or metric.get("node", "")
                        or metric.get("instance", "")
                        or metric.get("persistentvolumeclaim", "")
                        or qname
                    )
                    signals.append(PrometheusSignal({
                        "signal_type": qcfg["signal_type"],
                        "severity": qcfg["severity"],
                        "source": "prometheus",
                        "cluster": cluster,
                        "namespace": ns,
                        "name": name,
                        "message": f"{qname}: {name} value={value:.2f} in {ns or cluster}",
                        "query_name": qname,
                        "value": value,
                    }))
        return signals

    def _collect_alerts(self) -> List[PrometheusSignal]:
        signals = []
        if not self._alertmanager_url:
            return signals

        alerts = self._get_alerts(self._alertmanager_url, self._token)
        if alerts is None:
            return signals

        new_ids = set()
        for alert in alerts:
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            alertname = labels.get("alertname", "")
            fingerprint = alert.get("fingerprint", alertname)
            new_ids.add(fingerprint)

            if fingerprint in self._last_alert_ids:
                continue

            severity = ALERT_SEVERITY_MAP.get(labels.get("severity", "info"), "low")
            desc = annotations.get("description", annotations.get("summary", ""))[:200]
            ns = labels.get("namespace", "")
            node = labels.get("node", labels.get("instance", ""))

            signals.append(PrometheusSignal({
                "signal_type": f"alert_{alertname.lower()}",
                "severity": severity,
                "source": "alertmanager",
                "cluster": labels.get("cluster", "unknown"),
                "namespace": ns,
                "name": node or alertname,
                "message": f"Alert {alertname}: {desc}",
                "alertname": alertname,
                "starts_at": alert.get("startsAt", ""),
            }))

        self._last_alert_ids = new_ids
        return signals

    def _query(self, url: str, promql: str, token: str) -> Optional[list]:
        encoded = quote(promql, safe="")
        full_url = f"{url}/api/v1/query?query={encoded}"
        try:
            req = Request(full_url)
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            ctx = ssl.create_default_context()
            ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
            if os.path.exists(ca_path):
                ctx.load_verify_locations(ca_path)
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                data = json.loads(resp.read())
                if data.get("status") == "success":
                    return data.get("data", {}).get("result", [])
        except Exception as e:
            log.debug("Prometheus query failed on %s: %s", url, str(e)[:100])
        return None

    def _get_alerts(self, url: str, token: str) -> Optional[list]:
        try:
            req = Request(f"{url}/api/v2/alerts")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            ctx = ssl.create_default_context()
            ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
            if os.path.exists(ca_path):
                ctx.load_verify_locations(ca_path)
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Alertmanager fetch failed: %s", str(e)[:100])
        return None
