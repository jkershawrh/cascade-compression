"""Healthcare signal collector — maps clinical events to cascade Signal protocol."""

import logging

from .base import DomainCollector

log = logging.getLogger(__name__)


class HealthcareSignal:
    def __init__(self, evt: dict):
        self.signal_id = evt.get("id", 0)
        self.cluster_id = "healthcare"
        self.namespace = evt.get("department", "general")
        self.resource_kind = "clinical_event"
        self.resource_name = evt.get("patient_id", "")
        self.signal_type = self._map_type(evt)
        self.severity = self._map_severity(evt)
        self.evidence = {
            "message": f"{evt.get('event_type','')} {evt.get('value','')} {evt.get('unit','')} [{evt.get('patient_id','')} {evt.get('department','')}]",
            "event_type": evt.get("event_type", ""),
            "value": evt.get("value", ""),
            "reference_range": evt.get("reference_range", ""),
            "provider": evt.get("provider", ""),
            "department": evt.get("department", ""),
        }
        self.labels = {"domain": "healthcare"}
        self._ground_truth = evt.get("label", "")
        self._ground_truth_detail = evt.get("label_detail", "")

    def _map_type(self, evt):
        etype = evt.get("event_type", "")
        sev = evt.get("severity_flag", "")
        if sev == "critical":
            return f"critical_{etype}"
        if sev == "compliance":
            return f"compliance_{etype}"
        if sev == "operational":
            return f"ops_{etype}"
        return etype

    def _map_severity(self, evt):
        sev = evt.get("severity_flag", "normal")
        if sev == "critical":
            return "critical"
        if sev == "compliance":
            return "high"
        if sev == "operational":
            return "medium"
        return "info"


class HealthcareCollector(DomainCollector):
    name = "healthcare"
    _signal_class = HealthcareSignal
    _synthetic_module = "cascade_compression.benchmarks.synthetic_healthcare"
