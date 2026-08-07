"""Optional immutable ledger integration.

Writes decision records to any immutable ledger via REST gateway.
Fire-and-forget — failures are logged but never block the caller.
Uses the generic decision-record.json schema — not cascade-specific.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List
from uuid import uuid4

log = logging.getLogger(__name__)


def build_decision_record(
    cascade_result: Any,
    cascade_signals: list,
    system_id: str,
    domain: str,
) -> Dict:
    """Build a generic decision record from cascade results.

    Returns a dict matching contracts/schemas/decision-record.json.
    """
    signal_lookup = {}
    for sig in cascade_signals:
        signal_lookup[sig.signal_id] = sig

    decisions = []
    for decision in getattr(cascade_result, "decisions", []):
        sig = signal_lookup.get(decision.signal_id)
        decisions.append({
            "subject_id": str(decision.signal_id),
            "subject_type": sig.signal_type if sig else "",
            "severity": sig.severity if sig else "",
            "namespace": sig.namespace if sig else "",
            "outcome": decision.outcome.value,
            "agent": decision.agent_name,
            "confidence": decision.confidence,
            "tier": "nano",
            "evidence": decision.evidence[:200] if decision.evidence else "",
        })

    return {
        "system_id": system_id,
        "batch_id": str(uuid4()),
        "domain": domain,
        "decisions": decisions,
    }


def write_decisions(
    ledger_url: str,
    ledger_token: str,
    cascade_result: Any,
    cascade_signals: list,
    domain: str,
) -> None:
    if not ledger_url:
        return

    try:
        import httpx
    except ImportError:
        log.debug("httpx not installed — ledger writes disabled")
        return

    record = build_decision_record(
        cascade_result, cascade_signals,
        system_id=f"cascade-{domain}", domain=domain,
    )

    if not record["decisions"]:
        return

    content = json.dumps(record, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(content.encode()).hexdigest()
    correlation_id = record["batch_id"]

    entry = {
        "entry_type": "decision.record",
        "agent_id": record["system_id"],
        "content": content,
        "content_type": "application/json",
        "source_id": record["system_id"],
        "correlation_id": correlation_id,
        "idempotency_key": hashlib.sha256(
            f"decision.record\0{correlation_id}".encode()
        ).hexdigest(),
        "input_hash": input_hash,
    }

    try:
        headers = {"Content-Type": "application/json"}
        if ledger_token:
            headers["Authorization"] = f"Bearer {ledger_token}"

        client = httpx.Client(timeout=10, verify=False)
        client.post(f"{ledger_url.rstrip('/')}/api/receipts", json=entry, headers=headers)
        client.close()
    except Exception as e:
        log.debug("Ledger write failed: %s", str(e)[:60])
