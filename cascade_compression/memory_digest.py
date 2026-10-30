"""Durable, hourly email digest for newly accepted federated memories."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable
from collections import Counter

log = logging.getLogger(__name__)


class FederatedMemoryDigest:
    def __init__(self, *, spool_path: str, recipient: str, smtp_host: str,
                 smtp_port: int = 587, username: str = "", password: str = "",
                 sender: str = "", interval_seconds: int = 3600,
                 send: Callable[[list[dict[str, Any]]], None] | None = None,
                 retention_hours: int | None = None,
                 max_records: int | None = None):
        self.spool_path = Path(spool_path)
        self.recipient = recipient
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.sender = sender or username or "cascade@localhost"
        self.interval_seconds = max(60, interval_seconds)
        self._send_override = send
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.retention_hours = retention_hours if retention_hours is not None else 168  # 7 days
        self.max_records = max_records if max_records is not None else 10000

    @property
    def enabled(self) -> bool:
        return bool(self.recipient and (self.smtp_host or self._send_override))

    def enqueue(self, memory) -> None:
        record = memory.to_dict()
        record["aggregated_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            pending = self._read()
            pending.append(record)
            self._write(pending)

    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="federated-memory-digest")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def flush(self) -> int:
        with self._lock:
            pending = self._read()
        if not pending:
            return 0
        if self._send_override:
            self._send_override(pending)
        else:
            self._send_email(pending)
        with self._lock:
            current = self._read()
            sent_ids = {m.get("memory_id") for m in pending}
            self._write([m for m in current if m.get("memory_id") not in sent_ids])
        return len(pending)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._read())

    def summary(self, hours: float | None = None, *,
                start: datetime | None = None,
                end: datetime | None = None) -> dict[str, Any]:
        """Return a compact, stable summary of the unacknowledged hourly window."""
        with self._lock:
            records = self._read()
        if hours is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0, hours))
            records = [r for r in records if _parse_time(r.get("aggregated_at")) >= cutoff]
        if start is not None:
            records = [r for r in records if _parse_time(r.get("aggregated_at")) >= start]
        if end is not None:
            records = [r for r in records if _parse_time(r.get("aggregated_at")) < end]
        return summarize_records(records)

    def summarize_formed(self, records: list[dict[str, Any]], *,
                         start: datetime, end: datetime) -> dict[str, Any]:
        """Summarize retained memories by their original formation time."""
        selected = [
            record for record in records
            if start <= _parse_time(record.get("formed_at")) < end
        ]
        return summarize_records(selected)

    def acknowledge(self, digest_id: str) -> bool:
        with self._lock:
            records = self._read()
            ids = [str(r.get("memory_id", "")) for r in records]
            current_id = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]
            if digest_id != current_id:
                return False
            self._write([])
            return True

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.flush()
            except Exception as exc:
                log.warning("Federated memory digest delivery failed: %s", exc)

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.spool_path.read_text())
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _prune(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        records = [r for r in records if _parse_time(r.get("aggregated_at")) >= cutoff_time]
        if len(records) > self.max_records:
            records = sorted(records, key=lambda r: r.get("aggregated_at", ""), reverse=True)[:self.max_records]
        return records

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.spool_path.with_suffix(self.spool_path.suffix + ".tmp")
        try:
            pruned = self._prune(records)
            temporary.write_text(json.dumps(pruned, separators=(",", ":")))
            temporary.replace(self.spool_path)
        except Exception:
            try:
                if temporary.exists():
                    temporary.unlink()
            except Exception:
                pass
            raise

    def _send_email(self, records: list[dict[str, Any]]) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"Cascade aggregate: {len(records)} new memories"
        msg["From"] = self.sender
        msg["To"] = self.recipient
        lines = [f"{len(records)} federated memories were aggregated in the last hour.", ""]
        for memory in records:
            lines.extend([
                f"Formed: {memory.get('formed_at', 'unknown')}",
                f"Aggregated: {memory.get('aggregated_at', 'unknown')}",
                f"Source: {memory.get('source_instance', 'unknown')}",
                f"Type: {memory.get('signal_type', 'unknown')}",
                f"Severity: {memory.get('severity', 'unknown')}",
                f"Classification: {memory.get('classification', 'unknown')}",
                f"Strength: {memory.get('strength', 0):.3f}", "",
            ])
        msg.set_content("\n".join(lines))
        context = ssl.create_default_context()
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as client:
            client.starttls(context=context)
            if self.username:
                client.login(self.username, self.password)
            client.send_message(msg)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the common summary shape from an explicit record collection."""
    ids = [str(r.get("memory_id", "")) for r in records]
    digest_id = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]
    formed = sorted(str(r.get("formed_at", "")) for r in records if r.get("formed_at"))

    def counts(field: str) -> dict[str, int]:
        return dict(Counter(
            str(record.get(field) or "unknown") for record in records
        ).most_common())

    types = Counter(str(r.get("signal_type") or "unknown") for r in records)
    notable = sorted(
        records,
        key=lambda r: (
            {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(r.get("severity")), 0),
            float(r.get("strength", 0)),
        ),
        reverse=True,
    )[:5]
    return {
        "digest_id": digest_id,
        "count": len(records),
        "formed_from": formed[0] if formed else None,
        "formed_to": formed[-1] if formed else None,
        "sources": counts("source_instance"),
        "severities": counts("severity"),
        "classifications": counts("classification"),
        "top_patterns": dict(types.most_common(8)),
        "notable": [{
            "memory_id": r.get("memory_id"),
            "formed_at": r.get("formed_at"),
            "aggregated_at": r.get("aggregated_at"),
            "source": r.get("source_instance"),
            "signal_type": r.get("signal_type"),
            "severity": r.get("severity"),
            "classification": r.get("classification") or "unknown",
            "strength": r.get("strength"),
        } for r in notable],
    }


def digest_from_env() -> FederatedMemoryDigest:
    return FederatedMemoryDigest(
        spool_path=os.getenv("CASCADE_MEMORY_DIGEST_SPOOL", "/data/memory-digest.json"),
        recipient=os.getenv("CASCADE_MEMORY_DIGEST_TO", ""),
        smtp_host=os.getenv("CASCADE_SMTP_HOST", ""),
        smtp_port=int(os.getenv("CASCADE_SMTP_PORT", "587")),
        username=os.getenv("CASCADE_SMTP_USERNAME", ""),
        password=os.getenv("CASCADE_SMTP_PASSWORD", ""),
        sender=os.getenv("CASCADE_MEMORY_DIGEST_FROM", ""),
        interval_seconds=int(os.getenv("CASCADE_MEMORY_DIGEST_INTERVAL", "3600")),
        retention_hours=int(os.getenv("CASCADE_MEMORY_DIGEST_RETENTION_HOURS", "168")),
        max_records=int(os.getenv("CASCADE_MEMORY_DIGEST_MAX_RECORDS", "10000")),
    )


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
