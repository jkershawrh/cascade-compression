import json

import pytest
import os
import sqlite3
import time

from cascade_compression.durable_queue import (
    DURABLE_QUEUE_OVERFLOW_POLICY,
    DurableLedgerQueue,
)


def test_queue_survives_reopen_and_acknowledges_only_selected_rows(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    queue = DurableLedgerQueue(str(path), max_pending=10)
    assert queue.enqueue([{"id": 1}, {"id": 2}]) == 0

    reopened = DurableLedgerQueue(str(path), max_pending=10)
    items = reopened.peek(10)
    assert [item.payload for item in items] == [{"id": 1}, {"id": 2}]
    assert reopened.acknowledge([items[0].sequence]) == 1
    assert [item.payload for item in reopened.peek(10)] == [{"id": 2}]


def test_queue_is_bounded_and_rejects_new_without_deleting_oldest(tmp_path):
    queue = DurableLedgerQueue(str(tmp_path / "ledger.sqlite3"), max_pending=3)
    assert queue.enqueue([{"id": i} for i in range(5)]) == 2
    assert [item.payload["id"] for item in queue.peek(10)] == [0, 1, 2]
    assert queue.overflow_policy == DURABLE_QUEUE_OVERFLOW_POLICY
    assert queue.rejected_count() == 2

    reopened = DurableLedgerQueue(queue.path, max_pending=3)
    assert reopened.rejected_count() == 2


def test_queue_preserves_existing_rows_when_configuration_shrinks(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    original = DurableLedgerQueue(str(path), max_pending=3)
    assert original.enqueue([{"id": 1}, {"id": 2}]) == 0

    smaller = DurableLedgerQueue(str(path), max_pending=1)
    assert smaller.enqueue([{"id": 3}]) == 1
    assert [item.payload["id"] for item in smaller.peek(10)] == [1, 2]
    assert smaller.rejected_count() == 1


def test_queue_reports_age_and_storage_without_exposing_payloads(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    queue = DurableLedgerQueue(str(path), max_pending=3)
    queue.enqueue([{"private": "not-returned-by-stats"}])
    time.sleep(0.01)
    assert queue.oldest_age_seconds() > 0
    assert queue.payload_bytes() > 0
    assert queue.storage_bytes() > 0
    capacity = queue.filesystem_capacity()
    assert capacity["total_bytes"] > 0
    assert capacity["free_bytes"] > 0
    assert 0 <= capacity["used_fraction"] <= 1
    assert capacity["filesystem_id"].startswith("device:")
    assert set(capacity) == {
        "filesystem_id", "total_bytes", "free_bytes", "used_fraction",
    }
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
    assert [item.payload["id"] for item in queue.peek(10)] == [1]
    assert queue.payload_bytes() <= 50
    assert queue.rejected_count() == 2


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
    assert queue.rejected_count() == 0


# ---------------------------------------------------------------------------
# Classification-work spool (DurableQueue)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from cascade_compression.durable_queue import DurableQueue, sanitize_payload


def work_entry(work_id="w1", signal_id="s1", enqueued_at=None):
    return {
        "_work_id": work_id,
        "signal_id": signal_id,
        "signal_type": "pod_restart",
        "severity": "medium",
        "_enqueued_at": enqueued_at or datetime.now(timezone.utc).isoformat(),
    }


class TestClassificationSpool:
    def test_apply_and_restore_round_trip(self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"))
        try:
            entry = work_entry()
            assert queue.apply({"w1": entry})
            assert queue.restore() == [entry]
            assert queue.stats()["pending_entries"] == 1
            assert queue.stats()["healthy"] is True
        finally:
            queue.close()

    def test_delete_removes_persisted_work(self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"))
        try:
            assert queue.apply({"w1": work_entry()})
            assert queue.apply({"w1": None})
            assert queue.restore() == []
            assert queue.stats()["pending_entries"] == 0
        finally:
            queue.close()

    def test_restore_orders_oldest_first(self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"))
        try:
            base = datetime.now(timezone.utc)
            entries = {
                "new": work_entry("new", enqueued_at=base.isoformat()),
                "old": work_entry("old", enqueued_at=(
                    base - timedelta(hours=1)).isoformat()),
            }
            assert queue.apply(entries)
            assert [e["_work_id"] for e in queue.restore()] == ["old", "new"]
        finally:
            queue.close()

    def test_restore_rejects_mismatched_work_id(self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"))
        try:
            assert queue.apply({"w1": work_entry("w1")})
            # Corrupt the row identity directly.
            with sqlite3.connect(str(tmp_path / "work.sqlite3")) as db:
                db.execute("UPDATE work SET id='other'")
            with pytest.raises(ValueError):
                queue.restore()
        finally:
            queue.close()

    def test_apply_rejects_naive_timestamp(self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"))
        try:
            naive = work_entry(enqueued_at="2026-01-01T00:00:00")
            assert queue.apply({"w1": naive}) is False
            assert queue.healthy is False
            assert queue.failures == 1
            assert queue.restore() == []
        finally:
            queue.close()

    def test_apply_rejects_capacity_overflow_without_partial_write(
            self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"), max_entries=2)
        try:
            entries = {f"w{i}": work_entry(f"w{i}", f"s{i}") for i in range(3)}
            assert queue.apply(entries) is False
            assert queue.healthy is False
            # The failing transaction left the spool untouched.
            assert queue.restore() == []
        finally:
            queue.close()

    def test_expire_drops_aged_rows(self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"), ttl_seconds=60)
        try:
            assert queue.apply({"w1": work_entry()})
            with sqlite3.connect(str(tmp_path / "work.sqlite3")) as db:
                db.execute("UPDATE work SET born = born - 3600")
            assert queue.expire() == {"w1"}
            assert queue.restore() == []
            assert queue.stats()["counters"]["expired"] == 1
        finally:
            queue.close()

    def test_single_owner_lock(self, tmp_path):
        path = str(tmp_path / "work.sqlite3")
        first = DurableQueue(path)
        try:
            with pytest.raises(OSError):
                DurableQueue(path)
        finally:
            first.close()
        # The lock is released on close.
        second = DurableQueue(path)
        second.close()

    def test_symlinked_paths_are_rejected(self, tmp_path):
        target = tmp_path / "real.sqlite3"
        target.write_bytes(b"")
        link = tmp_path / "link.sqlite3"
        link.symlink_to(target)
        with pytest.raises(ValueError):
            DurableQueue(str(link))

    def test_invalid_bounds_are_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            DurableQueue(str(tmp_path / "w.sqlite3"), max_entries=0)
        with pytest.raises(ValueError):
            DurableQueue(str(tmp_path / "w.sqlite3"), max_bytes=1)
        with pytest.raises(ValueError):
            DurableQueue(str(tmp_path / "w.sqlite3"), ttl_seconds=0)

    def test_stats_do_not_expose_payloads(self, tmp_path):
        queue = DurableQueue(str(tmp_path / "work.sqlite3"))
        try:
            entry = work_entry()
            entry["content"] = {"secret": "not-returned-by-stats"}
            assert queue.apply({"w1": entry})
            assert "not-returned-by-stats" not in json.dumps(queue.stats())
        finally:
            queue.close()


class TestSanitizePayload:
    def test_redacts_sensitive_keys_and_inline_credentials(self):
        value = {
            "password": "hunter2",
            "nested": {"api_key": "abc", "safe": "keep"},
            "msg": "bearer abc123 and password=hunter2",
        }
        cleaned = sanitize_payload(value)
        assert cleaned["password"] == "[REDACTED]"
        assert cleaned["nested"]["api_key"] == "[REDACTED]"
        assert cleaned["nested"]["safe"] == "keep"
        assert "abc123" not in cleaned["msg"]
        assert "hunter2" not in cleaned["msg"]

    def test_bounds_string_length(self):
        cleaned = sanitize_payload("x" * 100000)
        assert len(cleaned) <= 8192

    def test_non_string_scalars_pass_through(self):
        assert sanitize_payload(5) == 5
        assert sanitize_payload(None) is None
        assert sanitize_payload(True) is True

    def test_control_characters_are_stripped(self):
        cleaned = sanitize_payload("line\x1b[31mred\x1b[0m")
        assert "\x1b" not in cleaned
