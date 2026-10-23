"""Activation-state persistence must survive concurrent classifier/verdict work.

Production incident (cascade-k8s, rc11/rc14 engine line, 2026-10-22/23):
agents reached the nano tier and activated, then vanished within minutes.
The bridge log showed a chronic stream of::

    Failed to save state: dictionary changed size during iteration

followed by ``demotion ... reason="snapshot write failed"``.  Two shared
structures are iterated by ``CascadeBridge._save_state`` while background
threads mutate them:

1. ``_verdict_seen_ids`` — mutated by the GCL verdict poller
   (``_poll_gcl_verdicts``).  The deployed rc11/rc14 engine line lost the
   ``_verdict_lock`` that serializes poller vs. snapshot on main, so every
   collision raised ``RuntimeError`` inside ``_save_state``.
2. ``MemoryArchive._memories`` — mutated by classifier workers calling
   ``MemoryArchive.store`` while ``_save_state`` serializes the archive via
   ``MemoryArchive.to_dict``.  This race exists on main today.

On the rc line a failed snapshot withdraws all learned authority
(``_fence_learning``): activations are dropped, qualification evidence is
wiped, and every nano agent is demoted — which is exactly the observed
"activated then empty" fleet behavior.  On main a failed save "only" leaves
a stale snapshot, but the same exception still blocks durable activation
state from ever being checkpointed.

These tests reproduce both interleavings deterministically (no sleep-based
races): a hook fires *mid-iteration* of the save path and releases the real
mutator, exactly as the production threads interleave.  With correct
locking the mutator cannot land inside the iteration — it blocks until the
snapshot completes — so the save succeeds.
"""

import json
import threading
from unittest.mock import patch

import httpx
import pytest

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.memory import Memory

from tests.helpers import make_signal


@pytest.fixture
def bridge(tmp_path):
    b = CascadeBridge(domain="test")
    b.enabled = True
    b._llm_url = ""
    b._state_file = str(tmp_path / "cascade_state.json")
    return b


class TestVerdictPollVsStateSave:
    """The GCL verdict poller must not mutate verdict state mid-snapshot.

    Regression guard for the rc11/rc14 engine line, which dropped the
    ``_verdict_lock`` present on main: an unlocked poller inserting entry
    IDs while ``_save_state`` copies ``_verdict_seen_ids`` raises
    ``dictionary changed size during iteration`` and fails the snapshot.
    """

    def test_save_state_survives_concurrent_verdict_poll(self, bridge):
        parked = threading.Event()
        release = threading.Event()
        inserted = threading.Event()

        class RacingSeenIds(dict):
            """Dict that reproduces the production interleaving.

            ``list()`` calls ``__iter__``; the hook releases the real
            poller thread at that exact point.  If the poller's mutation is
            not excluded by ``_verdict_lock`` it lands between iterator
            creation and consumption and the copy raises.
            """

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.on_iterate = None
                self.on_insert = None

            def __setitem__(self, key, value):
                if self.on_insert is not None:
                    self.on_insert()
                super().__setitem__(key, value)

            def __iter__(self):
                iterator = super().__iter__()
                if self.on_iterate is not None:
                    self.on_iterate()
                return iterator

        seen = RacingSeenIds()
        bridge._verdict_seen_ids = seen
        bridge._ledger_url = "http://ledger.test"
        bridge._ledger_token = ""

        entries = [
            {"entry_id": "verdict-1", "written_ts": 1000, "content": "{}"},
            {"entry_id": "verdict-2", "written_ts": 2000, "content": "{}"},
        ]

        class StubResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"entries": entries}

        class StubClient:
            def __init__(self, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, params=None, headers=None):
                return StubResponse()

        real_loads = json.loads
        gated = {"armed": True}

        def gated_loads(data):
            # Park the poller between entry 1 and entry 2 processing —
            # after the first locked mutation, before the second one.
            if gated["armed"] and isinstance(data, str):
                gated["armed"] = False
                parked.set()
                assert release.wait(timeout=10), "test never released poller"
            return real_loads(data)

        poller = threading.Thread(target=bridge._poll_gcl_verdicts, daemon=True)

        with patch.object(json, "loads", gated_loads), \
                patch.object(httpx, "Client", StubClient):
            poller.start()
            assert parked.wait(timeout=5), "poller never reached content parse"

            def on_iterate():
                # The save now holds _verdict_lock and is inside list().
                # Arm the insert signal, then release the parked poller:
                # its next action is the entry-2 mutation.
                seen.on_insert = inserted.set
                release.set()
                # Locked: the poller blocks on _verdict_lock, this times
                # out, and the snapshot completes consistently.
                # Unlocked: the poller inserts immediately, this returns
                # at once, and the snapshot raises RuntimeError.
                inserted.wait(timeout=0.3)

            seen.on_iterate = on_iterate
            saved = bridge._save_state()

        assert saved is True, (
            "state snapshot failed under a concurrent verdict poll — "
            "the poller mutated _verdict_seen_ids while _save_state "
            "copied it (rc11/rc14 regression: missing _verdict_lock)"
        )
        poller.join(timeout=5)
        assert not poller.is_alive()
        # The blocked mutation lands after the snapshot released the lock.
        assert "verdict-1" in bridge._verdict_seen_ids
        assert "verdict-2" in bridge._verdict_seen_ids

        with open(bridge._state_file) as f:
            state = json.load(f)
        # Consistent serial snapshot: verdict-2 was excluded because it
        # could not mutate while the snapshot held the lock.
        assert state["verdict_seen_ids"] == ["verdict-1"]


class TestMemoryStoreVsStateSave:
    """Classifier workers must not mutate the memory archive mid-snapshot.

    ``_save_state`` serializes ``MemoryArchive.to_dict`` while ``_run_llm``
    workers call ``MemoryArchive.store``.  ``MemoryArchive`` has no internal
    locking, so a store landing mid-iteration raises ``dictionary changed
    size during iteration`` and fails the snapshot — the same production
    failure signature as the verdict race.
    """

    def test_save_state_survives_concurrent_memory_store(self, bridge):
        assert bridge.memory_archive is not None
        archive = bridge.memory_archive
        archive.store(make_signal("event_alpha"), classification="noise")
        archive.store(make_signal("event_beta"), classification="noise")

        iterating = threading.Event()
        stored = threading.Event()

        real_to_dict = Memory.to_dict
        gate = {"armed": True}

        def hooked_to_dict(self):
            if gate["armed"]:
                gate["armed"] = False
                # First memory of the snapshot: signal the worker, then
                # give it the same window the production race provides.
                # Locked: the worker blocks on the archive lock and this
                # times out.  Unlocked: the store lands mid-iteration.
                iterating.set()
                stored.wait(timeout=0.3)
            return real_to_dict(self)

        def worker():
            assert iterating.wait(timeout=5), "test never reached iteration"
            archive.store(make_signal("event_gamma"), classification="noise")
            stored.set()

        thread = threading.Thread(target=worker, daemon=True)
        with patch.object(Memory, "to_dict", hooked_to_dict):
            thread.start()
            saved = bridge._save_state()

        assert saved is True, (
            "state snapshot failed under a concurrent memory store — "
            "MemoryArchive.to_dict iterated _memories while a worker "
            "stored a survivor (dictionary changed size during iteration)"
        )
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert archive.size == 3

        with open(bridge._state_file) as f:
            state = json.load(f)
        # Consistent serial snapshot: the third store was excluded because
        # it could not mutate while the snapshot held the archive lock.
        assert len(state["memory_archive"]["memories"]) == 2
