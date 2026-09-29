import os
import sqlite3
import time

from cascade_compression.durable_queue import DurableLedgerQueue


def test_queue_survives_reopen_and_acknowledges_only_selected_rows(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    queue = DurableLedgerQueue(str(path), max_pending=10)
    assert queue.enqueue([{"id": 1}, {"id": 2}]) == 0

    reopened = DurableLedgerQueue(str(path), max_pending=10)
    items = reopened.peek(10)
    assert [item.payload for item in items] == [{"id": 1}, {"id": 2}]
    assert reopened.acknowledge([items[0].sequence]) == 1
    assert [item.payload for item in reopened.peek(10)] == [{"id": 2}]


def test_queue_is_bounded_and_drops_oldest(tmp_path):
    queue = DurableLedgerQueue(str(tmp_path / "ledger.sqlite3"), max_pending=3)
    assert queue.enqueue([{"id": i} for i in range(5)]) == 2
    assert [item.payload["id"] for item in queue.peek(10)] == [2, 3, 4]


def test_queue_reports_age_and_storage_without_exposing_payloads(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    queue = DurableLedgerQueue(str(path), max_pending=3)
    queue.enqueue([{"private": "not-returned-by-stats"}])
    time.sleep(0.01)
    assert queue.oldest_age_seconds() > 0
    assert queue.payload_bytes() > 0
    assert queue.storage_bytes() > 0
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_queue_is_bounded_by_serialized_payload_bytes(tmp_path):
    queue = DurableLedgerQueue(
        str(tmp_path / "ledger.sqlite3"), max_pending=100, max_bytes=50,
    )
    dropped = queue.enqueue([
        {"id": 1, "value": "a" * 20},
        {"id": 2, "value": "b" * 20},
        {"id": 3, "value": "c" * 20},
    ])
    assert dropped == 2
    assert [item.payload["id"] for item in queue.peek(10)] == [3]
    assert queue.payload_bytes() <= 50


def test_queue_migrates_pre_byte_limit_database(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE ledger_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL,
                enqueued_at REAL NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO ledger_events(payload, enqueued_at) VALUES (?, ?)",
            ('{"id":1}', time.time()),
        )
    queue = DurableLedgerQueue(str(path), max_pending=10, max_bytes=100)
    assert queue.count() == 1
    assert queue.payload_bytes() == len('{"id":1}'.encode("utf-8"))
