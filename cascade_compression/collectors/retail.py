"""Retail signal collector — maps POS/inventory events to cascade Signal protocol."""

import logging

from .base import DomainCollector

log = logging.getLogger(__name__)


class RetailSignal:
    def __init__(self, evt: dict):
        self.signal_id = evt.get("id", 0)
        self.cluster_id = "retail"
        self.namespace = evt.get("store_id", "unknown")
        self.resource_kind = "retail_event"
        self.resource_name = evt.get("sku", "") or evt.get("register_id", "")
        self.signal_type = self._map_type(evt)
        self.severity = self._map_severity(evt)
        self.evidence = {
            "message": evt.get("description", ""),
            "event_type": evt.get("event_type", ""),
            "amount": evt.get("amount", 0),
            "quantity": evt.get("quantity", 0),
            "department": evt.get("department", ""),
            "employee_id": evt.get("employee_id", ""),
            "store_id": evt.get("store_id", ""),
        }
        self.labels = {"domain": "retail", "department": evt.get("department", "")}
        self._ground_truth = evt.get("label", "")
        self._ground_truth_detail = evt.get("label_detail", "")

    def _map_type(self, evt):
        etype = evt.get("event_type", "")
        amount = evt.get("amount", 0)
        if etype == "pos_return" and amount > 100:
            return "high_value_return"
        return etype

    def _map_severity(self, evt):
        label = evt.get("label", "")
        etype = evt.get("event_type", "")
        amount = evt.get("amount", 0)
        if label in ("shrinkage", "compliance"):
            return "high"
        if label == "operational":
            return "medium"
        if etype == "pos_return" and amount > 200:
            return "medium"
        return "info"


class RetailCollector(DomainCollector):
    name = "retail"
    _signal_class = RetailSignal
    _synthetic_module = "cascade_compression.benchmarks.synthetic_retail"
