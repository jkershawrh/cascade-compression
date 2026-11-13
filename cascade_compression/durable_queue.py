"""Small SQLite-backed queue for audit events awaiting ledger delivery.

Also hosts the opt-in single-owner classification-work spool (``DurableQueue``):
payload transactions use synchronous commits, successful work is acknowledged
after processing, so recovery is at-least-once, never exactly-once.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from .classifier import redact


DURABLE_QUEUE_OVERFLOW_POLICY = "preserve_queued_reject_new"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ASSIGNMENT_RE = re.compile(
    r"(?i)((?:password|token|secret|api[_-]?key|authorization)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URI_AUTH_RE = re.compile(r"(://)[^\s/@:]+:[^\s/@:]+@")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def _safe_text(value, limit: int = 512) -> str:
    """Bound and sanitize free text before persistence (no control chars)."""
    if not isinstance(value, (str, int, float)):
        return ""
    text = str(value)[:8192]
    if not text:
        return ""
    if "\x1b" in text:
        text = _ANSI_RE.sub("", text)
    if "://" in text and "@" in text:
        text = _URI_AUTH_RE.sub(r"\1[REDACTED]@", text)
    lower = text.lower()
    if any(marker in lower for marker in
           ("password", "token", "secret", "api_key", "api-key", "apikey",
            "authorization")):
        text = _ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    if "bearer" in lower:
        text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return " ".join(text.split()).encode("utf-8")[:limit].decode(
        "utf-8", errors="ignore",
    )


def sanitize_payload(value):
    """Redact structured credentials and inline credentials before persistence."""
    value = redact(value)
    if isinstance(value, dict):
        return {k: sanitize_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(v) for v in value]
    if isinstance(value, str):
        return _safe_text(value, 8192)
    return value


DURABLE_QUEUE_OVERFLOW_POLICY = "preserve_queued_reject_new"


@dataclass(frozen=True)
class QueueItem:
    sequence: int
    payload: dict
    enqueued_at: float


class DurableLedgerQueue:
    """Bounded FIFO that never deletes queued, undelivered records on overflow."""

    overflow_policy = DURABLE_QUEUE_OVERFLOW_POLICY

    def __init__(self, path: str, *, max_pending: int, max_bytes: int = 0):
        if not path:
            raise ValueError("durable queue path is required")
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        self.path = str(Path(path).expanduser().resolve())
        self.max_pending = max_pending
        self.max_bytes = max_bytes
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ledger_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    payload_bytes INTEGER NOT NULL,
                    enqueued_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ledger_queue_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )"""
            )
            connection.execute(
                """INSERT OR IGNORE INTO ledger_queue_meta(key, value)
                   VALUES ('rejected_events', 0)"""
            )
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(ledger_events)"
                ).fetchall()
            }
            if "payload_bytes" not in columns:
                connection.execute(
                    """ALTER TABLE ledger_events
                       ADD COLUMN payload_bytes INTEGER NOT NULL DEFAULT 0"""
                )
                connection.execute(
                    """UPDATE ledger_events
                       SET payload_bytes = length(CAST(payload AS BLOB))"""
                )
            connection.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def enqueue(self, events: Iterable[dict]) -> int:
        """Persist the ordered prefix that fits and return the rejected count.

        Existing rows are immutable until explicit acknowledgement. Once either bound is reached,
        this method rejects the remaining new events instead of evicting older audit history.
        """
        accepted, total = self._enqueue_prefix(events)
        return total - accepted

    def accept_memory_prefix(self, events, source_id, offset):
        """Commit source progress and FIFO insertion in the same transaction."""
        accepted, _ = self._enqueue_prefix(events, source_id, offset)
        return accepted

    def _enqueue_prefix(self, events, source_id=None, offset=0):
        encoded = []
        for event in events:
            payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
            encoded.append((payload, len(payload.encode("utf-8")), time.time()))
        if not encoded:
            return 0, 0
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            count, total_bytes = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM ledger_events"
            ).fetchone()
            replayed = 0
            if source_id is not None:
                progress_key = f"memory-source:{source_id}"
                row = connection.execute(
                    "SELECT value FROM ledger_queue_meta WHERE key=?", (progress_key,)
                ).fetchone()
                replayed = min(len(encoded), max(0, row[0] - offset)) if row else 0
            accepted = []
            for item in encoded[replayed:]:
                payload_bytes = item[1]
                if count >= self.max_pending or (
                    self.max_bytes and total_bytes + payload_bytes > self.max_bytes
                ):
                    break
                accepted.append(item)
                count += 1
                total_bytes += payload_bytes
            if accepted:
                connection.executemany(
                    """INSERT INTO ledger_events(payload, payload_bytes, enqueued_at)
                       VALUES (?, ?, ?)""",
                    accepted,
                )
            admitted = replayed + len(accepted)
            rejected = len(encoded) - admitted
            if source_id is not None and accepted:
                connection.execute(
                    "INSERT OR REPLACE INTO ledger_queue_meta VALUES (?, ?)",
                    (progress_key, offset + admitted),
                )
            if rejected and source_id is None:
                connection.execute(
                    """UPDATE ledger_queue_meta
                       SET value = value + ? WHERE key = 'rejected_events'""",
                    (rejected,),
                )
            connection.commit()
        return admitted, len(encoded)

    def peek(self, limit: int) -> List[QueueItem]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT sequence, payload, enqueued_at
                   FROM ledger_events ORDER BY sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            QueueItem(sequence=row[0], payload=json.loads(row[1]), enqueued_at=row[2])
            for row in rows
        ]

    def memory_delivery_batch(self, limit: int) -> List[QueueItem]:
        """Pin the batch boundary through ambiguous remote or local acknowledgments."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            end = connection.execute(
                "SELECT value FROM ledger_queue_meta WHERE key='memory-batch-end'"
            ).fetchone()
            rows = []
            if end:
                rows = connection.execute(
                    "SELECT sequence,payload,enqueued_at FROM ledger_events "
                    "WHERE sequence <= ? ORDER BY sequence", end,
                ).fetchall()
            if not rows:
                rows = connection.execute(
                    "SELECT sequence,payload,enqueued_at FROM ledger_events "
                    "ORDER BY sequence LIMIT ?", (limit,),
                ).fetchall()
                if rows:
                    connection.execute(
                        "INSERT OR REPLACE INTO ledger_queue_meta VALUES ('memory-batch-end', ?)",
                        (rows[-1][0],),
                    )
            connection.commit()
        return [QueueItem(seq, json.loads(payload), born) for seq, payload, born in rows]

    def acknowledge(self, sequences: Iterable[int]) -> int:
        values = [int(sequence) for sequence in sequences]
        if not values:
            return 0
        placeholders = ",".join("?" for _ in values)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                f"DELETE FROM ledger_events WHERE sequence IN ({placeholders})",
                values,
            )
            connection.commit()
            return cursor.rowcount

    def count(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM ledger_events"
            ).fetchone()[0])

    def oldest_age_seconds(self) -> float:
        with closing(self._connect()) as connection:
            oldest = connection.execute(
                "SELECT MIN(enqueued_at) FROM ledger_events"
            ).fetchone()[0]
        return max(0.0, time.time() - oldest) if oldest is not None else 0.0

    def payload_bytes(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute(
                "SELECT COALESCE(SUM(payload_bytes), 0) FROM ledger_events"
            ).fetchone()[0])

    def rejected_count(self) -> int:
        """Return the durable cumulative count of capacity-rejected events."""
        with closing(self._connect()) as connection:
            return int(connection.execute(
                "SELECT value FROM ledger_queue_meta WHERE key = 'rejected_events'"
            ).fetchone()[0])

    def storage_bytes(self) -> int:
        total = 0
        for path in (Path(self.path), Path(self.path + "-wal"), Path(self.path + "-shm")):
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                # SQLite removes -wal/-shm sidecars when the last connection
                # closes; a stat racing that removal must not fail /stats
                # (observed as intermittent stats timeouts in production).
                continue
        return total

    def filesystem_capacity(self) -> dict:
        """Return path-free capacity telemetry for the filesystem holding the spool."""
        parent = Path(self.path).parent
        usage = shutil.disk_usage(parent)
        used_fraction = (usage.total - usage.free) / usage.total if usage.total else 1.0
        return {
            "filesystem_id": f"device:{os.stat(parent).st_dev}",
            "total_bytes": int(usage.total),
            "free_bytes": int(usage.free),
            "used_fraction": round(used_fraction, 6),
        }


class DurableQueue:
    """Opt-in single-owner SQLite spool for bounded classification work.

    Keys on the queue entry's ``_work_id``. Successful work is acknowledged
    (deleted) only after its memory checkpoint, so restart recovery replays
    pending and interrupted work at-least-once. Capacity failures reject the
    write and mark the spool unhealthy; they never fall back to RAM-only
    admission.
    """

    def __init__(self, path: str, *, max_entries: int = 15000,
                 max_bytes: int = 64 * 1024 * 1024, ttl_seconds: int = 86400):
        if max_entries < 1 or max_bytes < 65536 or ttl_seconds < 1:
            raise ValueError("invalid durable queue bounds")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._mutex = threading.Lock()
        self.failures = 0
        self.healthy = True
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or Path(path + ".lock").is_symlink():
            raise ValueError("queue paths must not be symlinks")
        if target.exists() and target.stat().st_size > max_bytes:
            raise ValueError("existing spool exceeds configured disk budget")
        self._owner = open(path + ".lock", "a+b")
        os.chmod(path + ".lock", 0o600)
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(path, 0o600)
            self._db = sqlite3.connect(path, check_same_thread=False, timeout=5)
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute(f"PRAGMA max_page_count={max_bytes // 4096}")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS work ("
                "id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "born REAL NOT NULL, size INTEGER NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS counters ("
                "name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS spool_identity (singleton INTEGER PRIMARY KEY "
                "CHECK(singleton=1), identity TEXT NOT NULL)"
            )
            self._db.execute("INSERT OR IGNORE INTO spool_identity VALUES (1, hex(randomblob(16)))")
            self.identity = self._db.execute("SELECT identity FROM spool_identity").fetchone()[0]
            self._db.commit()
        except Exception:
            if hasattr(self, "_db"):
                self._db.close()
            self._owner.close()
            raise

    def _count(self, name, amount=1):
        self._db.execute(
            "INSERT INTO counters VALUES (?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
            (name, amount),
        )

    def expire(self, *, protected=()) -> set:
        """Delete TTL-expired rows and return their identifiers."""
        with self._mutex, self._db:
            cutoff = time.time() - self.ttl_seconds
            expired = {row[0] for row in self._db.execute(
                "SELECT id FROM work WHERE born < ?", (cutoff,))}
            expired.difference_update(protected)
            if expired:
                self._db.executemany("DELETE FROM work WHERE id=?", ((k,) for k in expired))
                self._count("expired", len(expired))
            return expired

    def apply(self, changes: dict) -> bool:
        """One transaction per ingress batch; failure leaves old work intact."""
        try:
            with self._mutex, self._db:
                for identifier, entry in changes.items():
                    if entry is None:
                        self._db.execute(
                            "DELETE FROM work WHERE id=?", (identifier,))
                        continue
                    payload = json.dumps(
                        sanitize_payload(entry),
                        separators=(",", ":"), ensure_ascii=False,
                    )
                    size = len(payload.encode("utf-8"))
                    if size > self.max_bytes:
                        raise ValueError("entry exceeds spool byte budget")
                    parsed = datetime.fromisoformat(entry["_enqueued_at"])
                    if parsed.tzinfo is None:
                        raise ValueError("queue time must include a timezone")
                    self._db.execute(
                        "INSERT INTO work VALUES (?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET "
                        "payload=excluded.payload,size=excluded.size",
                        (identifier, payload, parsed.timestamp(), size),
                    )
                count, size = self._db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(size),0) FROM work"
                ).fetchone()
                if count > self.max_entries or size > self.max_bytes:
                    raise ValueError("spool capacity exceeded")
            self.healthy = True
            return True
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            self.failures += 1
            self.healthy = False
            return False

    def restore(self) -> list:
        """Return pending entries ordered oldest-first, validating each row."""
        self.expire()
        with self._mutex:
            rows = self._db.execute(
                "SELECT id,payload FROM work ORDER BY born,id LIMIT ?",
                (self.max_entries + 1,),
            ).fetchall()
            if len(rows) > self.max_entries:
                raise ValueError("existing spool exceeds configured entry budget")
            total = sum(len(row[1].encode("utf-8")) for row in rows)
            if total > self.max_bytes:
                raise ValueError("existing spool exceeds configured byte budget")
            entries = [json.loads(row[1]) for row in rows]
            for (identifier, _), e in zip(rows, entries):
                if (not isinstance(e, dict)
                        or e.get("_work_id") != identifier
                        or not isinstance(e.get("signal_id"), str)
                        or not isinstance(e.get("_enqueued_at"), str)):
                    raise ValueError("invalid durable queue records")
            return entries

    def stats(self) -> dict:
        with self._mutex:
            count, size = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(size),0) FROM work"
            ).fetchone()
            return {"enabled": True, "healthy": self.healthy,
                    "pending_entries": count, "payload_bytes": size,
                    "write_failures": self.failures,
                    "ttl_seconds": self.ttl_seconds,
                    "max_entries": self.max_entries, "max_bytes": self.max_bytes,
                    "counters": dict(
                        self._db.execute("SELECT name,value FROM counters"))}

    def close(self):
        with self._mutex:
            self._db.close()
            self._owner.close()
