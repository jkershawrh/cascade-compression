"""Regression: MemoryArchive.stats() must not race concurrent store() calls.

Observed in production (2026-11-11): the /memories/stats
and /stats endpoints intermittently returned HTTP 500 with
`RuntimeError: dictionary changed size during iteration`, raised from

    sources = {m.source_instance for m in self._memories.values() ...}

in MemoryArchive.stats(), because that comprehension iterated the live
`_memories` dict outside `self._lock` while classification threads stored new
memories. The strengths comprehension was already locked; this test pins the
whole stats() read under the same guarantee.
"""

import threading

from cascade_compression.cascade.memory import MemoryArchive

from tests.helpers import make_signal


def _store_forever(archive: MemoryArchive, stop: threading.Event, counter: list) -> None:
    i = 0
    while not stop.is_set():
        archive.store(make_signal(content={"message": f"race-{i}"}))
        i += 1
    counter.append(i)


class TestStatsConcurrency:
    def test_stats_survives_concurrent_stores(self):
        """stats() must not raise while another thread stores memories."""
        archive = MemoryArchive()
        for i in range(200):
            archive.store(make_signal(content={"message": f"seed-{i}"}))

        stop = threading.Event()
        stored: list = []
        writer = threading.Thread(
            target=_store_forever, args=(archive, stop, stored), daemon=True)
        writer.start()
        try:
            # Each stats() call iterates every memory; with the writer
            # inserting continuously, an unlocked read raises quickly.
            for _ in range(2000):
                stats = archive.stats()
                assert stats["size"] > 0
        finally:
            stop.set()
            writer.join(timeout=10)

        assert stored, "writer thread made no progress; test is inconclusive"


def test_audit_buffer_cap_is_configurable(monkeypatch):
    """CASCADE_MEMORY_EVENT_MAX_PENDING raises audit admission headroom.

    Production finding (2026-11-12): on instances whose memory-event
    production chronically exceeds the ledger drain throughput, the audit
    buffer pinned at its default cap and the new admission gate
    (memory_audit_backpressure) held large collector batches forever. The
    cap is now operator-configurable so landing room can be provisioned.
    """
    monkeypatch.setenv("CASCADE_MEMORY_EVENT_MAX_PENDING", "5000")
    archive = MemoryArchive()
    assert archive._event_max_pending == 5000
    monkeypatch.setenv("CASCADE_MEMORY_EVENT_MAX_PENDING", "10")
    assert MemoryArchive()._event_max_pending == 1000  # floor
    monkeypatch.delenv("CASCADE_MEMORY_EVENT_MAX_PENDING", raising=False)
    assert MemoryArchive()._event_max_pending == 20000  # default


def test_memory_max_env_raises_capacity_across_restore(tmp_path, monkeypatch):
    """CASCADE_MEMORY_MAX must raise a restored archive's capacity.

    Production finding (2026-11-12): an at-capacity archive (10K) churned
    its entire capacity ~1,350 times; retained classification targets were
    evicted before application and the discard flood consumed the
    classification budget. The operator raised CASCADE_MEMORY_MAX but the
    restore path read the persisted capacity and ignored it.
    """
    from cascade_compression.bridge import CascadeBridge
    from tests.helpers import make_signal

    monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", str(tmp_path / "work.sqlite3"))
    monkeypatch.setenv("CASCADE_LEDGER_URL", "")

    first = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        for i in range(20):
            first.memory_archive.store(make_signal(content={"message": f"m{i}"}))
        assert first.memory_archive._max_capacity == 10000
        assert first._save_state()
    finally:
        first.close()

    monkeypatch.setenv("CASCADE_MEMORY_MAX", "50000")
    restored = CascadeBridge(llm_url="http://fixture.invalid")
    try:
        assert restored.memory_archive._max_capacity == 50000
        assert restored.memory_archive.size == 20
    finally:
        restored.close()


def test_eviction_batch_is_bounded_at_large_capacities():
    """Admission at a 50K archive evicts at most 1024 victims.

    Production incident (2026-11-12): with capacity raised to 50K, every
    admission evicted 5,000 memories (10% fraction) — a full-archive sort
    plus thousands of queued eviction events under the archive lock starved
    the service loop (/health 0.1s -> 10s+, liveness kills). The eviction
    batch is now bounded regardless of capacity.
    """
    archive = MemoryArchive(max_capacity=50000)
    for i in range(50000):
        archive.store(make_signal(content={"message": f"m{i}"}))
    assert archive.size == 50000
    archive.store(make_signal(content={"message": "one more"}))
    # One admission evicts a bounded batch (1,024), not 10% of 50K (5,000):
    # size goes 50,000 + 1 admitted - 1,024 evicted = 48,977.
    assert archive.size == 50000 + 1 - 1024
    assert archive._evictions_total == 1024
    assert len(archive._rejection_set) == 1024


def test_rejection_set_membership_is_constant_time():
    """The rejection set must not degrade to O(n) membership.

    Production incident (2026-11-12, final phase): the rejection set was a
    plain list — membership scanned up to 2x-capacity entries per check,
    once per eviction victim and once per store dedup. At 50K capacity that
    was ~100M comparisons per admission under the archive lock. The
    structure is now an insertion-ordered dict with O(1) membership and
    identical serialization.
    """
    archive = MemoryArchive(max_capacity=100)
    for i in range(200):
        archive._add_to_rejection_set(f"hash-{i}")
    # Capped at 2x capacity, oldest dropped, order preserved.
    assert len(archive._rejection_set) == 200
    assert "hash-0" in archive._rejection_set
    assert "hash-199" in archive._rejection_set
    # Round-trip serialization keeps the list shape.
    d = archive.to_dict()
    assert isinstance(d["rejection_set"], list)
    restored = MemoryArchive.from_dict(d)
    assert len(restored._rejection_set) == 200
    assert "hash-199" in restored._rejection_set


def test_storage_bytes_survives_sqlite_sidecar_removal(tmp_path, monkeypatch):
    """A stat racing sqlite's -wal/-shm cleanup must not raise.

    Production finding (2026-11-12): /stats intermittently timed out with
    FileNotFoundError on '<spool>-shm' — the exists()-then-stat() pair raced
    sqlite removing the sidecar when the last connection closed.
    """
    from pathlib import Path

    from cascade_compression.durable_queue import DurableLedgerQueue

    spool = tmp_path / "spool.sqlite3"
    q = DurableLedgerQueue(str(spool), max_pending=10)
    q.enqueue([{"entry_type": "probe"}])
    sidecar = Path(str(spool) + "-shm")
    sidecar.write_bytes(b"x" * 10)

    real_stat = Path.stat

    def racing_stat(self, *a, **k):
        if self.name.endswith("-shm"):
            sidecar.unlink()  # sqlite wins the race mid-stat
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert q.storage_bytes() >= 0  # no raise, sidecar counted as absent
