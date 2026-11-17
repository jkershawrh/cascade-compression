"""Snapshot persistence must stay durable but stop costing a full write per request.

Production incident (cascade-aap, 2026-11-17): with a 50k-memory archive the
state file reached ~123 MB. Every ``/cascade`` request serialized and wrote it
(``_enqueue_llm`` → ``_save_state``), and requests also queued behind each
other on ``_snapshot_lock``. Measured effect: a single-signal POST exceeded
120 s and the collector's 30 s client timeout never succeeded.

Two changes are pinned here:

1. ``CASCADE_STATE_COMPRESSION=gzip`` shrinks the on-disk snapshot without
   changing a single restored value, and legacy JSON snapshots still load.
2. ``_save_state`` settles concurrent callers: a snapshot that completed after
   a caller recorded its change satisfies that caller without another write,
   while a change recorded *during* a write still forces a new snapshot.
"""

import gzip
import json
import threading
import time

import pytest

from cascade_compression import bridge as bridge_module
from cascade_compression.bridge import CascadeBridge
from cascade_compression.lifecycle import atomic_snapshot, decode_snapshot


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_STATE_COMPRESSION", "gzip")
    b = CascadeBridge(domain="test")
    b.enabled = True
    b._llm_url = ""
    b._state_file = str(tmp_path / "cascade_state.json")
    yield b
    b._closed = True


class TestCompressedSnapshots:
    def test_gzip_snapshot_round_trips_and_is_compressed(self, bridge, tmp_path):
        from tests.helpers import make_signal

        bridge.memory_archive.store(make_signal(signal_type="pod_crashloop"))
        bridge.memory_archive.store(make_signal(signal_type="node_not_ready"))
        bridge._mark_state_dirty()
        assert bridge._save_state() is True

        raw = open(bridge._state_file, "rb").read()
        assert raw[:2] == b"\x1f\x8b", "snapshot must be gzip-framed"
        assert json.loads(gzip.decompress(raw))["version"] == 5

        restored = CascadeBridge(domain="test")
        restored.enabled = True
        restored._llm_url = ""
        restored._state_file = bridge._state_file
        restored._restore_state()

        assert restored.stats.signals_processed == bridge.stats.signals_processed
        assert sorted(m.signal.signal_type for m in restored.memory_archive._memories.values()) == [
            "node_not_ready", "pod_crashloop"
        ]
        assert all(
            m.content_hash == original.content_hash
            for m, original in zip(
                sorted(restored.memory_archive._memories.values(),
                       key=lambda m: m.signal.signal_type),
                sorted(bridge.memory_archive._memories.values(),
                       key=lambda m: m.signal.signal_type),
            )
        ), "restored memories must keep content hashes so suppression stays admissible"

    def test_legacy_json_snapshot_still_restores(self, bridge):
        atomic_snapshot(bridge._state_file, {"version": 5, "serializer_revision": ""})
        raw = open(bridge._state_file, "rb").read()
        assert raw[:1] == b"{", "uncompressed snapshot stays plain JSON"
        assert decode_snapshot(raw)["version"] == 5

    def test_decode_snapshot_accepts_both_formats(self):
        payload = {"version": 5, "x": [1, 2, 3]}
        plain = json.dumps(payload).encode()
        framed = gzip.compress(plain, compresslevel=1, mtime=0)
        assert decode_snapshot(plain) == payload
        assert decode_snapshot(framed) == payload

    def test_unknown_compression_mode_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASCADE_STATE_COMPRESSION", "brotli")
        with pytest.raises(ValueError):
            CascadeBridge(domain="test")


class TestSnapshotSettling:
    def test_concurrent_callers_share_one_write(self, bridge, monkeypatch):
        writes = []
        started = threading.Event()
        release = threading.Event()
        real = bridge_module.atomic_snapshot

        def slow_snapshot(path, state, compression="none"):
            writes.append(path)
            started.set()
            release.wait(5)
            return real(path, state, compression=compression)

        monkeypatch.setattr(bridge_module, "atomic_snapshot", slow_snapshot)

        bridge._mark_state_dirty()
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(bridge._save_state(settle=True)))
            for _ in range(5)
        ]
        threads[0].start()
        assert started.wait(5), "first writer never started"
        for thread in threads[1:]:
            thread.start()
        release.set()
        for thread in threads:
            thread.join(10)

        assert results == [True] * 5
        assert len(writes) == 1, f"expected one settled write, saw {len(writes)}"

    def test_change_recorded_during_write_forces_another_snapshot(self, bridge, monkeypatch):
        writes = []
        real = bridge_module.atomic_snapshot

        def snapshot_with_concurrent_change(path, state, compression="none"):
            writes.append(path)
            if len(writes) == 1:
                # A request mutates state while the first snapshot is being built.
                bridge._mark_state_dirty()
            return real(path, state, compression=compression)

        monkeypatch.setattr(bridge_module, "atomic_snapshot", snapshot_with_concurrent_change)

        bridge._mark_state_dirty()
        assert bridge._save_state(settle=True) is True
        assert bridge._save_state(settle=True) is True
        assert len(writes) == 2

    def test_unchanged_state_does_not_write_again(self, bridge, monkeypatch):
        writes = []
        real = bridge_module.atomic_snapshot
        monkeypatch.setattr(
            bridge_module, "atomic_snapshot",
            lambda path, state, compression="none": (writes.append(path),
                                                     real(path, state, compression=compression))[1],
        )
        bridge._mark_state_dirty()
        assert bridge._save_state(settle=True) is True
        assert bridge._save_state(settle=True) is True
        assert len(writes) == 1

    def test_save_without_recorded_change_still_writes(self, bridge, monkeypatch):
        writes = []
        real = bridge_module.atomic_snapshot
        monkeypatch.setattr(
            bridge_module, "atomic_snapshot",
            lambda path, state, compression="none": (writes.append(path),
                                                     real(path, state, compression=compression))[1],
        )
        assert bridge._save_state() is True
        assert len(writes) == 1


class TestPromotionOffTheRequestPath:
    """The periodic sweep scans the archive; it must not run inside a request.

    Production incident (cascade-aap, 2026-11-17): the sweep ran on whichever
    request crossed the 300 s boundary. With 49k memories a single-signal POST
    took 18-52 s, so the collector's 30 s client timeout never succeeded even
    though the ledger spool was empty.
    """

    def test_promotion_sweep_does_not_block_process(self, bridge, monkeypatch):
        from tests.helpers import make_signal

        started = threading.Event()
        release = threading.Event()
        observed = {}

        def slow_sweep():
            observed["thread"] = threading.current_thread().name
            started.set()
            release.wait(5)

        bridge._promotion_interval = 0
        bridge._last_promotion_time = 0
        monkeypatch.setattr(bridge, "_discover_and_promote", slow_sweep)

        began = time.monotonic()
        result = bridge.process([make_signal()])
        elapsed = time.monotonic() - began

        assert result.get("accepted") is True
        assert started.wait(5), "promotion never started"
        assert elapsed < 5, f"request waited {elapsed:.1f}s for the sweep"
        assert observed["thread"] != threading.current_thread().name
        release.set()

    def test_one_sweep_runs_at_a_time(self, bridge, monkeypatch):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_sweep():
            calls.append(1)
            started.set()
            release.wait(5)

        bridge._promotion_interval = 0
        bridge._last_promotion_time = 0
        monkeypatch.setattr(bridge, "_discover_and_promote", slow_sweep)

        bridge._start_promotion_cycle()
        assert started.wait(5)
        bridge._start_promotion_cycle()  # must not stack a second sweep
        release.set()
        time.sleep(0.1)
        assert len(calls) == 1


class TestStaleSnapshotTemps:
    """A killed snapshot write leaves its temp file behind.

    ``atomic_snapshot`` unlinks in a ``finally``, which does not run on SIGKILL
    (exit 137 after a slow write). One instance leaked 30 temp snapshots at
    766 MB on a 2 Gi volume, which then slowed every write until requests could
    not finish.
    """

    def test_old_temps_are_swept_and_fresh_ones_kept(self, tmp_path, monkeypatch):
        import os
        import time

        state = tmp_path / "cascade_state.json"
        state.write_bytes(b"{}")
        stale = tmp_path / ".cascade-state-stale"
        fresh = tmp_path / ".cascade-state-live"
        unrelated = tmp_path / "notes.txt"
        for path in (stale, fresh, unrelated):
            path.write_bytes(b"x")
        old = time.time() - 7200
        os.utime(stale, (old, old))
        os.utime(unrelated, (old, old))

        monkeypatch.setenv("CASCADE_STATE_FILE", str(state))
        bridge = CascadeBridge(domain="test")

        assert not stale.exists(), "stale temp snapshot must be removed"
        assert fresh.exists(), "an in-progress snapshot must not be touched"
        assert unrelated.exists(), "only snapshot temps are swept"

    def test_no_state_file_is_a_noop(self, monkeypatch):
        monkeypatch.delenv("CASCADE_STATE_FILE", raising=False)
        bridge = CascadeBridge(domain="test")
        assert bridge._sweep_stale_snapshot_temps() == 0
