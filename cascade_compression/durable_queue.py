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

    def __init__(self, path: str, *, max_pending: int):
        if not path:
            raise ValueError("durable queue path is required")
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.path = str(Path(path).expanduser().resolve())
        self.max_pending = max_pending
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ledger_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    enqueued_at REAL NOT NULL
                )"""
            )
            connection.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10.0)

    def enqueue(self, events: Iterable[dict]) -> int:
        encoded = [
            (json.dumps(event, sort_keys=True, separators=(",", ":")), time.time())
            for event in events
        ]
        if not encoded:
            return 0
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO ledger_events(payload, enqueued_at) VALUES (?, ?)",
                encoded,
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM ledger_events"
            ).fetchone()[0]
            overflow = max(0, count - self.max_pending)
            if overflow:
                connection.execute(
                    """DELETE FROM ledger_events WHERE sequence IN (
                        SELECT sequence FROM ledger_events ORDER BY sequence LIMIT ?
                    )""",
                    (overflow,),
                )
            connection.commit()
        return overflow

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

    def storage_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in (Path(self.path), Path(self.path + "-wal"), Path(self.path + "-shm"))
            if path.exists()
        )
