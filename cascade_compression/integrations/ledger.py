"""Optional immutable ledger integration.

Writes decision records to any immutable ledger via REST gateway.
Fire-and-forget — failures are logged but never block the caller.
Uses the generic decision-record.json schema — not cascade-specific.
"""

import hashlib
import json
import logging
from typing import Any, Dict, Optional
from uuid import uuid4

log = logging.getLogger(__name__)


MAX_ENTRY_BYTES = 1_048_576  # the gateway rejects content above this
BUILD_ENTRY_BYTES = 768 * 1024  # build chunks with headroom below the limit


class LedgerPermanentRejection(RuntimeError):
    """The gateway rejected the entry permanently (e.g. oversized content).

    Retrying can never succeed; the caller must drop the entry with
    accounting rather than requeue it.
    """


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

    for entry in build_decision_entries(cascade_result, cascade_signals, domain):
        _post_to_ledger(ledger_url, ledger_token, entry)


def build_decision_entry(
    cascade_result: Any,
    cascade_signals: list,
    domain: str,
) -> Optional[Dict]:
    """Build the first (or only) chunk of the decision record envelope."""
    entries = build_decision_entries(cascade_result, cascade_signals, domain)
    return entries[0] if entries else None


def build_decision_entries(
    cascade_result: Any,
    cascade_signals: list,
    domain: str,
) -> list:
    """Build byte-bounded decision record envelopes.

    The ledger gateway permanently rejects content above MAX_ENTRY_BYTES,
    so large batches are chunked with headroom. Each chunk carries a
    distinct correlation id and idempotency key.
    """
    record = build_decision_record(
        cascade_result, cascade_signals,
        system_id=f"cascade-{domain}", domain=domain,
    )
    if not record["decisions"]:
        return []

    batch_id = record["batch_id"]
    entries = []
    chunk: list = []

    def emit(chunk_decisions, correlation_id):
        chunk_record = dict(record, decisions=chunk_decisions)
        content = json.dumps(chunk_record, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(content.encode()).hexdigest()
        entries.append({
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
        })

    for decision in record["decisions"]:
        trial = chunk + [decision]
        trial_bytes = len(
            json.dumps(dict(record, decisions=trial),
                       sort_keys=True, separators=(",", ":")).encode())
        if chunk and trial_bytes > BUILD_ENTRY_BYTES:
            emit(chunk, f"{batch_id}:{len(entries)}")
            chunk = [decision]
        else:
            chunk = trial
    if chunk:
        emit(chunk, f"{batch_id}:{len(entries)}" if entries else batch_id)
    return entries


def write_promotion_event(
    ledger_url: str,
    ledger_token: str,
    event_dict: Dict,
    domain: str,
) -> None:
    """Write an agent promotion/demotion event to the immutable ledger."""
    if not ledger_url:
        return

    try:
        import httpx  # noqa: F811
    except ImportError:
        log.debug("httpx not installed — ledger writes disabled")
        return

    _post_to_ledger(
        ledger_url, ledger_token, build_promotion_entry(event_dict, domain),
    )


def build_promotion_entry(event_dict: Dict, domain: str) -> Dict:
    """Build an idempotent promotion envelope without network I/O."""
    content = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(content.encode()).hexdigest()
    agent_name = event_dict.get("agent_name", "unknown")
    event_type = event_dict.get("event_type", "unknown")
    correlation_id = f"{agent_name}-{event_type}-{event_dict.get('timestamp', '')}"

    return {
        "entry_type": "agent.promotion",
        "agent_id": f"cascade-{domain}",
        "content": content,
        "content_type": "application/json",
        "source_id": f"cascade-{domain}",
        "correlation_id": correlation_id,
        "idempotency_key": hashlib.sha256(
            f"agent.promotion\0{correlation_id}".encode()
        ).hexdigest(),
        "input_hash": input_hash,
    }


def write_memory_event(
    ledger_url: str,
    ledger_token: str,
    event_dict: Dict,
    domain: str,
) -> None:
    """Write a memory lifecycle event to the immutable ledger."""
    if not ledger_url:
        return

    try:
        import httpx  # noqa: F811
    except ImportError:
        log.debug("httpx not installed — ledger writes disabled")
        return

    content = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(content.encode()).hexdigest()
    memory_id = event_dict.get("memory_id", "unknown")
    event_type = event_dict.get("event_type", "unknown")
    correlation_id = f"{memory_id}-{event_type}-{event_dict.get('timestamp', '')}"

    entry = {
        "entry_type": f"memory.{event_type}",
        "agent_id": f"cascade-{domain}",
        "content": content,
        "content_type": "application/json",
        "source_id": f"cascade-{domain}",
        "correlation_id": correlation_id,
        "idempotency_key": hashlib.sha256(
            f"memory.{event_type}\0{correlation_id}".encode()
        ).hexdigest(),
        "input_hash": input_hash,
    }

    _post_to_ledger(ledger_url, ledger_token, entry)


def write_memory_events(
    ledger_url: str,
    ledger_token: str,
    events: list,
    domain: str,
) -> bool:
    """Write a batch of memory lifecycle events as byte-bounded receipts."""
    if not ledger_url or not events:
        return True
    ok = True
    for entry in build_memory_events_entries(events, domain):
        ok = _post_to_ledger(ledger_url, ledger_token, entry) and ok
    return ok


def build_memory_events_entries(events: list, domain: str) -> list:
    """Build one or more byte-bounded envelopes for a memory-event batch."""
    if not events:
        return []
    entries = []

    def emit(chunk):
        content = json.dumps(
            {"events": chunk}, sort_keys=True, separators=(",", ":"),
        )
        input_hash = hashlib.sha256(content.encode()).hexdigest()
        entries.append({
            "entry_type": "memory.batch",
            "agent_id": f"cascade-{domain}",
            "content": content,
            "content_type": "application/json",
            "source_id": f"cascade-{domain}",
            "correlation_id": f"memory-batch-{input_hash[:24]}",
            "idempotency_key": hashlib.sha256(
                f"memory.batch\0{input_hash}".encode()
            ).hexdigest(),
            "input_hash": input_hash,
        })

    chunk = []
    chunk_bytes = 2  # {}
    for event in events:
        event_bytes = len(json.dumps(event, sort_keys=True,
                                     separators=(",", ":")).encode()) + 1
        if chunk and chunk_bytes + event_bytes > BUILD_ENTRY_BYTES:
            emit(chunk)
            chunk = []
            chunk_bytes = 2
        chunk.append(event)
        chunk_bytes += event_bytes
    if chunk:
        emit(chunk)
    return entries


def build_memory_events_entry(events: list, domain: str) -> Dict:
    """Build one idempotent envelope for a memory-event batch."""
    if not events:
        raise ValueError("memory event batch cannot be empty")
    return build_memory_events_entries(events, domain)[0]


def post_entry(ledger_url: str, ledger_token: str, entry: Dict) -> bool:
    """Post a pre-built ledger entry, returning explicit delivery success."""
    return _post_to_ledger(ledger_url, ledger_token, entry)


def _post_to_ledger(ledger_url: str, ledger_token: str, entry: Dict) -> bool:
    import httpx

    serialized = json.dumps(entry).encode()
    if len(serialized) > MAX_ENTRY_BYTES:
        # The gateway answers oversized content with a generic HTTP 500
        # (its gRPC INVALID_ARGUMENT is not mapped to a 4xx status), so the
        # only reliable signal is the size itself: the entry can never be
        # delivered.
        raise LedgerPermanentRejection(
            f"ledger entry exceeds the gateway content limit: "
            f"{len(serialized)} > {MAX_ENTRY_BYTES} bytes")

    try:
        headers = {"Content-Type": "application/json"}
        if ledger_token:
            headers["Authorization"] = f"Bearer {ledger_token}"

        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{ledger_url.rstrip('/')}/api/receipts",
                json=entry,
                headers=headers,
            )
            if response.status_code in (400, 413):
                # Permanent rejection: the gateway will never accept this
                # entry (oversized content, malformed envelope). Retrying
                # poisons durable spools — surface it to the caller.
                raise LedgerPermanentRejection(
                    f"ledger rejected entry permanently: HTTP "
                    f"{response.status_code} {response.text[:120]}")
            response.raise_for_status()
        return True
    except LedgerPermanentRejection:
        raise
    except Exception as e:
        log.debug("Ledger write failed: %s", str(e)[:60])
        return False
