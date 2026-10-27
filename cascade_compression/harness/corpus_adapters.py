"""Corpus adapters: public datasets → Cascade Signal protocol.

Maps external datasets (MIMIC-IV clinical events, credit card fraud
transactions, Loghub system logs) to the Signal format with identity
envelopes, so they replay through the fast-forward harness and the
full cascade lifecycle (dedup → classification → promotion →
suppression → withdrawal).

Each adapter is domain-agnostic in mechanism: the identity envelope,
the evidence bar, and the governance are identical across domains.
Only the field mapping differs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List
from uuid import uuid4


def _envelope(kind: str, uid: str, event_uid: str, reason: str,
              count: int, message: str) -> Dict[str, Any]:
    """Build the identity envelope required for verified-repeat suppression."""
    return {
        "kind": kind,
        "uid": uid,
        "event_uid": event_uid,
        "reason": reason,
        "count": count,
        "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
    }


def mimic_event_to_signal(event: Dict[str, Any]) -> Dict[str, Any]:
    """Map a MIMIC-IV clinical event to the Signal protocol.

    MIMIC-IV-Ext-22MCTS format (per PhysioNet documentation):
    { subject_id, hadm_id, itemid, charttime, value, valuenum,
      valueuom, label (event description), category }

    The identity envelope:
    - kind: "ClinicalEvent"
    - uid: subject_id (the patient — stable across their events)
    - event_uid: unique per (patient, itemid, charttime)
    - reason: the event label (e.g., "Heart Rate", "Potassium")
    - count: 1 (each observation is new; repeats of the same
      measurement for the same patient fold by identity)
    """
    subject_id = str(event.get("subject_id", ""))
    itemid = str(event.get("itemid", ""))
    charttime = str(event.get("charttime", ""))
    label = str(event.get("label", "")) or str(event.get("category", "unknown"))
    value = event.get("value", "") or event.get("valuenum", "")

    message = f"{label}: {value}" if value else label
    event_uid = f"mimic-{subject_id}-{itemid}-{charttime}"

    # Severity mapping from clinical context
    category = str(event.get("category", "")).lower()
    if any(k in category for k in ("alarm", "critical", "emergency")):
        severity = "critical"
    elif any(k in category for k in ("warning", "abnormal")):
        severity = "high"
    elif any(k in category for k in ("vital", "monitor")):
        severity = "medium"
    else:
        severity = "info"

    return {
        "signal_type": f"clinical_{label.lower().replace(' ', '_').replace('/', '_')[:40]}",
        "severity": severity,
        "source": label[:100],
        "namespace": f"patient_{subject_id}",
        "cluster": "mimic",
        "labels": {"domain": "healthcare"},
        "content": {
            "message": message,
            **_envelope("ClinicalEvent", subject_id, event_uid,
                        label, 1, message),
            "itemid": itemid,
            "charttime": charttime,
            "value": str(value),
            "category": event.get("category", ""),
        },
    }


def fraud_transaction_to_signal(txn: Dict[str, Any],
                                 dataset: str = "kaggle_ccf") -> Dict[str, Any]:
    """Map a credit card fraud transaction to the Signal protocol.

    Kaggle Credit Card Fraud format:
    { Time, V1-V28 (PCA features), Amount, Class (0=normal, 1=fraud) }

    IEEE-CIS format:
    { TransactionID, TransactionDT, TransactionAmt, ProductCD,
      card1-card6, addr1, addr2, dist1, dist2, ... , isFraud }

    The identity envelope:
    - kind: "Transaction"
    - uid: card identifier (card1 or a composite)
    - event_uid: transaction ID or timestamp
    - reason: transaction type/product code
    - count: 1 (each transaction is unique)
    """
    if dataset == "kaggle_ccf":
        time_val = txn.get("Time", 0)
        amount = txn.get("Amount", 0)
        is_fraud = txn.get("Class", 0)
        # PCA features are anonymized; use the first few as the "card" identity
        card_id = str(txn.get("V1", ""))[:20]
        message = f"Transaction amount={amount} time={time_val}"
        event_uid = f"ccf-{time_val}-{uuid4().hex[:8]}"
        severity = "medium"
    else:  # ieee-cis
        txn_id = txn.get("TransactionID", "")
        time_val = txn.get("TransactionDT", 0)
        amount = txn.get("TransactionAmt", 0)
        is_fraud = txn.get("isFraud", 0)
        card_id = str(txn.get("card1", ""))[:20]
        product = txn.get("ProductCD", "unknown")
        message = f"Transaction {product} amount={amount} card={card_id}"
        event_uid = f"ieeecis-{txn_id}"
        severity = "medium"

    return {
        "signal_type": f"transaction_{('fraud' if is_fraud else 'normal')}",
        "severity": severity,
        "source": card_id,
        "namespace": "card_transactions",
        "cluster": dataset,
        "labels": {"domain": "finance"},
        "content": {
            "message": message,
            **_envelope("Transaction", card_id, event_uid,
                        "transaction", 1, message),
            "amount": amount,
            "time": time_val,
            "is_fraud": is_fraud,
        },
    }


def loghub_entry_to_signal(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Loghub system log entry to the Signal protocol.

    Loghub datasets (BGL, HDFS, Thunderbird, OpenStack) provide parsed
    log entries with:
    { timestamp, level (INFO/WARN/ERROR/FATAL), component, event_id,
      content (the log message), label (normal/anomaly) }

    The identity envelope:
    - kind: "SystemLog"
    - uid: component (the emitting node/service)
    - event_uid: unique per (component, event_template)
    - reason: log level
    - count: 1
    """
    component = str(entry.get("component", "unknown"))
    level = str(entry.get("level", "INFO")).upper()
    content = str(entry.get("content", entry.get("message", "")))
    event_template = str(entry.get("event_id", entry.get("template", "")))
    label = str(entry.get("label", "normal"))

    message = f"[{component}] {level}: {content[:200]}"

    severity_map = {"INFO": "info", "WARN": "medium", "WARNING": "medium",
                    "ERROR": "high", "FATAL": "critical", "CRITICAL": "critical"}

    return {
        "signal_type": f"log_{component.lower().replace(' ', '_')[:30]}",
        "severity": severity_map.get(level, "info"),
        "source": component,
        "namespace": "system_logs",
        "cluster": str(entry.get("dataset", "loghub")),
        "labels": {"domain": "kubernetes", "label": label},
        "content": {
            "message": message,
            **_envelope("SystemLog", component, event_template,
                        level, 1, message),
            "level": level,
            "label": label,
        },
    }
