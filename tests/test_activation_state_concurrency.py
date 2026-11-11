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
        # A poll can fetch during another snapshot, but cannot publish an ID
        # or continuation inside it. The next snapshot includes the whole page.
        from cascade_compression import bridge as bridge_module

        fetched = threading.Event()
        saving = threading.Event()
        real_snapshot = bridge_module.atomic_snapshot
        snapshots = []

        def snapshot(path, state):
            snapshots.append((state["verdict_watermark"], list(state["verdict_seen_ids"])))
            if len(snapshots) == 1:
                saving.set()
                assert fetched.wait(5)
            real_snapshot(path, state)

        def read(request):
            assert saving.wait(5)
            fetched.set()
            return httpx.Response(200, json={
                "entries": [{
                    "entry_id": "verdict-1", "entry_type": "audit.verdict",
                    "source_id": "gcl-cascade-audit", "agent_id": "governed-cognitive-loop",
                    "written_ts": 1000, "content": {"domain": "test", "verdict": "FAILS",
                        "original_agent": "synthetic", "subject_type": "routine",
                        "decision_ref": "decision-1"},
                }], "next_page_token": "", "total_count": 1,
            })

        bridge._ledger_url = "https://ledger.test"
        bridge._ledger_token = "synthetic"
        client = httpx.Client
        transport = httpx.MockTransport(read)
        poller = threading.Thread(target=bridge._poll_gcl_verdicts, daemon=True)
        with patch.object(bridge_module, "atomic_snapshot", snapshot), patch.object(
                httpx, "Client", lambda **kw: client(transport=transport, **kw)):
            poller.start()
            assert bridge._save_state()
            poller.join(5)

        assert not poller.is_alive()
        assert snapshots[0] == (0, [])
        assert snapshots[-1] == (bridge._verdict_watermark, ["verdict-1"])
        assert bridge._fn_count == 1
        assert bridge.get_stats()["verdict_catchup_complete"]


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
