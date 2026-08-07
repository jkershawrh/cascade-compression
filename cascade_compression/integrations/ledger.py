"""Optional immutable ledger integration.

Writes cascade decisions to the ARE Immutable Ledger via its REST gateway.
Fire-and-forget — failures are logged but never block the cascade.
No dependency on fleet-llm-d. Just HTTP POST to /api/receipts.
"""

import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)


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

    correlation_id = str(uuid4())

    signal_lookup = {}
    for sig in cascade_signals:
        signal_lookup[sig.signal_id] = sig

    decisions = []
    for decision in getattr(cascade_result, "decisions", []):
        sig = signal_lookup.get(decision.signal_id)
        decisions.append({
            "signal_id": str(decision.signal_id),
            "signal_type": sig.signal_type if sig else "",
            "severity": sig.severity if sig else "",
            "namespace": sig.namespace if sig else "",
            "outcome": decision.outcome.value,
            "agent_name": decision.agent_name,
            "confidence": decision.confidence,
            "tier": "nano",
            "domain": domain,
        })

    if not decisions:
        return

    content = json.dumps(decisions, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(content.encode()).hexdigest()

    entry = {
        "entry_type": "cascade.decision",
        "agent_id": f"cascade-{domain}",
        "content": content,
        "content_type": "application/json",
        "source_id": "cascade-compression",
        "correlation_id": correlation_id,
        "idempotency_key": hashlib.sha256(
            f"cascade.decision\0{correlation_id}".encode()
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
