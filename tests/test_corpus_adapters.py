"""Cross-domain corpus adapters: public datasets → Signal protocol."""

import hashlib
import json
from uuid import uuid4

import pytest

from cascade_compression.harness.corpus_adapters import (
    _envelope, fraud_transaction_to_signal, loghub_entry_to_signal,
    mimic_event_to_signal,
)
from cascade_compression.cascade.repeat_integrity import verified_repeat_update_key
from cascade_compression.cascade.protocol import Signal


def test_mimic_event_admits_to_identity():
    """A MIMIC clinical event must pass identity admission (medium severity,
    complete envelope)."""
    event = {
        "subject_id": 12345, "itemid": 220045, "charttime": "2180-01-01 12:00:00",
        "label": "Heart Rate", "value": "72", "valuenum": 72,
        "category": "vital signs", "valueuom": "bpm",
    }
    sig = mimic_event_to_signal(event)
    assert sig["signal_type"] == "clinical_heart_rate"
    assert sig["severity"] == "medium"  # vital signs → medium
    assert sig["labels"]["domain"] == "healthcare"
    # Identity envelope complete
    content = sig["content"]
    assert content["kind"] == "ClinicalEvent"
    assert content["uid"] == "12345"
    assert content["event_uid"].startswith("mimic-12345-")
    assert content["message_sha256"] == hashlib.sha256(
        content["message"].encode()).hexdigest()


def test_mimic_critical_event_maps_high_severity():
    event = {
        "subject_id": 999, "itemid": 67890, "charttime": "2180-01-01",
        "label": "Critical Lab Alert", "value": "K+ 7.2",
        "category": "alarm", "valueuom": "mEq/L",
    }
    sig = mimic_event_to_signal(event)
    assert sig["severity"] == "critical"


def test_fraud_normal_transaction():
    txn = {"Time": 100000, "Amount": 45.67, "Class": 0, "V1": 1.234}
    sig = fraud_transaction_to_signal(txn, "kaggle_ccf")
    assert sig["signal_type"] == "transaction_normal"
    assert sig["labels"]["domain"] == "finance"
    assert sig["content"]["is_fraud"] == 0
    assert "Transaction" in sig["content"]["message"]


def test_fraud_fraudulent_transaction():
    txn = {"TransactionID": 12345, "TransactionDT": 100000, "TransactionAmt": 2500.00,
           "isFraud": 1, "card1": 12345, "ProductCD": "W"}
    sig = fraud_transaction_to_signal(txn, "ieee-cis")
    assert sig["signal_type"] == "transaction_fraud"
    assert sig["content"]["is_fraud"] == 1
    assert sig["source"] == "12345"  # card1


def test_loghub_entry():
    entry = {
        "timestamp": "2026-01-01 00:00:01", "level": "ERROR",
        "component": "DataNode", "event_id": "E1234",
        "content": "Cannot replicate block blk_12345",
        "label": "anomaly", "dataset": "HDFS",
    }
    sig = loghub_entry_to_signal(entry)
    assert sig["signal_type"] == "log_datanode"
    assert sig["severity"] == "high"  # ERROR → high
    assert sig["content"]["kind"] == "SystemLog"
    assert sig["labels"]["label"] == "anomaly"


def test_loghub_info_is_low_severity():
    entry = {
        "timestamp": "2026-01-01", "level": "INFO",
        "component": "NameNode", "event_id": "E5678",
        "content": "Block report received", "label": "normal",
    }
    sig = loghub_entry_to_signal(entry)
    assert sig["severity"] == "info"


def test_healthcare_domain_pack_has_exemplars():
    from cascade_compression.domain_plugins import load_domain_plugin
    plugin = load_domain_plugin("healthcare")
    assert plugin.few_shot_exemplars is not None
    assert len(plugin.few_shot_exemplars.get("needs_attention", [])) >= 3
    # Exemplars carry clinical vocabulary
    all_text = " ".join(e for exs in plugin.few_shot_exemplars.values() for e in exs)
    assert any(k in all_text for k in ("Potassium", "Heart Rate", "Creatinine"))


def test_finance_domain_pack_has_exemplars():
    from cascade_compression.domain_plugins import load_domain_plugin
    plugin = load_domain_plugin("finance")
    assert plugin.few_shot_exemplars is not None
    assert len(plugin.few_shot_exemplars.get("needs_attention", [])) >= 3
    all_text = " ".join(e for exs in plugin.few_shot_exemplars.values() for e in exs)
    assert any(k in all_text for k in ("amount", "transaction", "merchant"))
