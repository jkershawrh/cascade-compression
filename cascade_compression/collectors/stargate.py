"""Stargate collector — scan results, stuck instances, remediation events.

READ-ONLY. API reads only. Never trigger scans or remediations.

Polls the Stargate API for scan results, instance health, and
pool velocity metrics.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class StargateSignal:
    _counter = 0

    def __init__(self, record: dict):
        StargateSignal._counter += 1
        self.signal_id = StargateSignal._counter
        self.cluster_id = record.get("cluster", "stargate")
        self.namespace = record.get("namespace", "stargate")
        self.resource_kind = record.get("kind", "scan")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "stargate"}


class StargateCollector(BaseCollector):
    name = "stargate"

    def __init__(self):
        self._api_url = ""
        self._token = ""
        self._connected = False

    def connect(self, config: dict) -> bool:
        self._api_url = (
            config.get("api_url", "")
            or os.getenv("STARGATE_API_URL", "")
        ).rstrip("/")
        self._token = config.get("token", "") or os.getenv("STARGATE_TOKEN", "")
        if not self._api_url:
            log.warning("No STARGATE_API_URL configured")
            return False
        data = self._get("/api/health")
        self._connected = data is not None
        if self._connected:
            log.info("Stargate connected: %s", self._api_url)
        return self._connected

    def collect(self) -> list:
        signals = []
        scans = self._get("/api/scans?limit=50") or []
        for scan in (scans if isinstance(scans, list) else scans.get("results", [])):
            status = scan.get("status", "")
            cluster = scan.get("cluster", "")
            failures = scan.get("failure_count", 0)
            name = f"scan-{cluster}-{scan.get('id', '')}"

            if status == "failed" or failures > 0:
                signals.append(StargateSignal({
                    "signal_type": "scan_failed",
                    "severity": "high",
                    "kind": "scan", "name": name, "cluster": cluster,
                    "message": f"Scan failed on {cluster}: {failures} failures",
                    "failure_count": failures,
                }))
            elif status == "completed":
                signals.append(StargateSignal({
                    "signal_type": "scan_completed",
                    "severity": "info",
                    "kind": "scan", "name": name, "cluster": cluster,
                    "message": f"Scan completed on {cluster}",
                }))

        instances = self._get("/api/instances?stuck=true&limit=50") or []
        for inst in (instances if isinstance(instances, list) else instances.get("results", [])):
            name = inst.get("name", inst.get("namespace", "unknown"))
            cluster = inst.get("cluster", "")
            signals.append(StargateSignal({
                "signal_type": "instance_stuck",
                "severity": "high",
                "kind": "instance", "name": name, "cluster": cluster,
                "message": f"Stuck instance: {name} on {cluster}",
            }))

        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "api_url": self._api_url}

    def _get(self, path: str) -> Optional:
        url = f"{self._api_url}{path}"
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = Request(url, headers=headers)
            ctx = ssl.create_default_context()
            if os.getenv("STARGATE_SSL_VERIFY", "true").lower() == "false":
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Stargate GET %s: %s", path, str(e)[:100])
            return None
