"""Small SQLite-backed queue for audit events awaiting ledger delivery."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


@dataclass(frozen=True)
class QueueItem:
    sequence: int
    payload: dict
    enqueued_at: float


class DurableLedgerQueue:
    """Bounded FIFO whose acknowledgement happens only after remote success."""

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
        return sqlite3.connect(self.path, timeout=10.0)

    def enqueue(self, events: Iterable[dict]) -> int:
        encoded = []
        for event in events:
            payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
            encoded.append((payload, len(payload.encode("utf-8")), time.time()))
        if not encoded:
            return 0
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                """INSERT INTO ledger_events(payload, payload_bytes, enqueued_at)
                   VALUES (?, ?, ?)""",
                encoded,
            )
            count, total_bytes = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM ledger_events"
            ).fetchone()
            count_overflow = max(0, count - self.max_pending)
            byte_overflow = self.max_bytes and total_bytes > self.max_bytes
            dropped = []
            if count_overflow or byte_overflow:
                rows = connection.execute(
                    "SELECT sequence, payload_bytes FROM ledger_events ORDER BY sequence"
                ).fetchall()
                for sequence, payload_bytes in rows:
                    if len(dropped) >= count_overflow and (
                        not self.max_bytes or total_bytes <= self.max_bytes
                    ):
                        break
                    dropped.append(sequence)
                    total_bytes -= payload_bytes
                placeholders = ",".join("?" for _ in dropped)
                connection.execute(
                    f"DELETE FROM ledger_events WHERE sequence IN ({placeholders})",
                    dropped,
                )
            connection.commit()
        return len(dropped)

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

    def storage_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in (Path(self.path), Path(self.path + "-wal"), Path(self.path + "-shm"))
            if path.exists()
        )
