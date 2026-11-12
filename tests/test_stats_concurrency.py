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
