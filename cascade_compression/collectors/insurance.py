"""Insurance signal collector — maps claims/policy events to cascade Signal protocol."""

import logging

from .base import DomainCollector

log = logging.getLogger(__name__)


class InsuranceSignal:
    def __init__(self, evt: dict):
        self.signal_id = evt.get("id", 0)
        self.cluster_id = "insurance"
        self.namespace = evt.get("line_of_business", "general")
        self.resource_kind = "insurance_event"
        self.resource_name = evt.get("claim_id", "") or evt.get("policy_id", "")
        self.signal_type = self._map_type(evt)
        self.severity = self._map_severity(evt)
        pid = evt.get("policy_id", "") or evt.get("claim_id", "")
        self.evidence = {
            "message": f"{evt.get('description', '')} [{pid} {evt.get('state', '')} {evt.get('line_of_business', '')}]",
            "event_type": evt.get("event_type", ""),
            "amount": evt.get("amount", 0),
            "line_of_business": evt.get("line_of_business", ""),
            "state": evt.get("state", ""),
            "provider": evt.get("provider", ""),
            "adjuster": evt.get("adjuster", ""),
        }
        self.labels = {"domain": "insurance", "lob": evt.get("line_of_business", "")}
        self._ground_truth = evt.get("label", "")
        self._ground_truth_detail = evt.get("label_detail", "")

    def _map_type(self, evt):
        etype = evt.get("event_type", "")
        amount = evt.get("amount", 0)
        if etype == "claim_filed" and amount > 25000:
            return "large_claim"
        return etype

    def _map_severity(self, evt):
        amount = evt.get("amount", 0)
        etype = evt.get("event_type", "")
        desc = evt.get("description", "").lower()
        if etype in ("licensing_gap", "reserve_deficiency", "regulatory_filing",
                      "market_conduct", "solvency_warning"):
            return "high"
        if etype in ("sla_breach", "adjuster_overload", "reinsurance_trigger",
                      "system_outage", "backlog_alert"):
            return "medium"
        if etype == "claim_filed" and amount > 25000:
            return "medium"
        if etype == "claim_filed" and any(w in desc for w in
                ("staged", "inflated", "phantom", "identity", "duplicate", "mismatch",
                 "po box", "npi", "impossible", "pre-existing", "attorney",
                 "aftermarket", "oem", "soft tissue", "upcoded", "bundled",
                 "ssn", "vin", "classified", "manufacturing", "clerical")):
            return "high"
        if etype == "claim_filed" and amount > 10000:
            return "medium"
        return "info"


class InsuranceCollector(DomainCollector):
    name = "insurance"
    _signal_class = InsuranceSignal
    _synthetic_module = "cascade_compression.benchmarks.synthetic_insurance"
