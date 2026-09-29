import os
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
    assert queue.storage_bytes() > 0
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
