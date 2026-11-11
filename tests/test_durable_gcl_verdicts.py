"""Local bridge integration against the immutable ledger's filtered offset protocol."""
import json
import time

import httpx
import pytest

from cascade_compression.bridge import CascadeBridge
from tests.test_positive_lifecycle import activate
from tests.test_positive_lifecycle import bridge as qualification_bridge


def now_ms():
    return int(time.time() * 1000)


def verdict(key, ts, domain="kubernetes", **changes):
    content = dict(domain=domain, subject_type="routine", original_agent="learned",
                   verdict="FAILS", decision_ref="decision-" + key)
    content.update(changes)
    return dict(entry_type="audit.verdict", agent_id="governed-cognitive-loop",
                source_id="gcl-cascade-audit", entry_id=key, written_ts=ts, content=content)


class Ledger:
    def __init__(self):
        self.rows = []
        self.calls = []
        self.change = lambda data: data

    def reply(self, request):
        assert request.method == "GET" and request.url.path == "/api/entries"
        assert request.headers["authorization"] == "Bearer local-test"
        params = dict(request.url.params)
        self.calls.append(params)
        assert params["entry_type"] == "audit.verdict"
        assert params["source_id"] == "gcl-cascade-audit"
        assert params["agent_id"] == "governed-cognitive-loop"
        assert params["include_content_raw"] == "false"
        floor, ceiling = int(params["from_ts"]), int(params["to_ts"])
        rows = [r for r in self.rows if floor <= r["written_ts"] <= ceiling]
        pos = int(params.get("page_token") or 0)
        page = rows[pos:pos + int(params["page_size"])]
        end = pos + len(page)
        data = dict(entries=page, total_count=len(rows),
                    next_page_token=str(end) if end < len(rows) else "")
        result = self.change(data)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)


@pytest.fixture
def ledger(monkeypatch):
    ledger = Ledger()
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(ledger.reply), **kw))
    return ledger


@pytest.fixture
def start(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("CASCADE_VERDICT_MODE", "strict")
    bridges = []

    def make(domain="kubernetes"):
        bridge = CascadeBridge(domain=domain, ledger_token="local-test", ledger_url="https://ledger.test")
        bridges.append(bridge)
        return bridge

    yield make
    for bridge in bridges:
        bridge.close()


@pytest.mark.parametrize("minutes", [5, 25])
def test_saved_watermark_consumes_live_fails_once_after_restart(start, ledger, minutes):
    b = start()
    ts = now_ms() - minutes * 60000
    b._verdict_watermark = ts - 1
    assert b._save_state()
    b.close()
    b = start()
    ledger.rows = [verdict("negative", ts)]
    assert b._poll_gcl_verdicts()
    assert int(ledger.calls[0]["from_ts"]) <= ts - 1
    assert b._fn_count == 1
    b.close()
    b = start()
    assert not b.get_stats()["verdict_catchup_complete"]
    assert b._poll_gcl_verdicts()
    assert b._fn_count == 0


def test_oldest_floor_and_tied_pages_pinned_through_failure_and_restart(start, ledger, monkeypatch):
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "1")
    monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "1")
    ts = now_ms() - 3600000
    ledger.rows = [verdict(k, ts) for k in ("one", "two", "three")]
    b = start()
    b._verdict_watermark = ts - 1
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 1
    first = ledger.calls[0]
    assert b._verdict_watermark == ts - 1
    b.close()
    b = start()
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "20")
    ledger.change = lambda data: httpx.Response(503)
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 0
    ledger.change = lambda data: data
    assert not b._poll_gcl_verdicts()
    assert b._poll_gcl_verdicts()
    assert [c.get("page_token", "") for c in ledger.calls] == ["", "1", "1", "2"]
    for c in ledger.calls[1:]:
        for field in ("from_ts", "to_ts", "page_size"):
            assert c[field] == first[field]
    assert b._fn_count == 2


@pytest.mark.parametrize("change", [
    lambda page: {"entries": page["entries"]},
    lambda page: dict(page, total_count=4),
    lambda page: dict(page, next_page_token="0"),
    lambda page: dict(page, partial=True),
    lambda page: dict(page, stale=True),
    lambda page: dict(page, truncated=True),
    lambda page: httpx.Response(206, json=page),
])
def test_partial_or_stale_page_never_commits_coverage(start, ledger, change):
    b = start()
    ts = now_ms() - 60000
    b._verdict_watermark = ts - 1
    ledger.rows = [verdict("negative", ts)]
    ledger.change = change
    assert not b._poll_gcl_verdicts()
    assert b._verdict_watermark == ts - 1
    assert not b.get_stats()["verdict_catchup_complete"]
    ledger.change = lambda data: data
    assert b._poll_gcl_verdicts()
    assert b._fn_count >= 1


def test_failed_effect_does_not_skip_later_fails_or_mark_failed_id(start, ledger, monkeypatch):
    b = start()
    ts = now_ms() - 200
    b._verdict_watermark = ts - 1
    ledger.rows = [verdict(k, ts) for k in ("first", "second")]
    real = b.classifier.record_gcl_verdict
    observed = []
    def flaky(kind, result, ref):
        observed.append(ref)
        if ref == "decision-first":
            raise RuntimeError("temporary failure")
        real(kind, result, ref)
    monkeypatch.setattr(b.classifier, "record_gcl_verdict", flaky)
    assert not b._poll_gcl_verdicts()
    assert observed == ["decision-first", "decision-second"]
    assert b._verdict_watermark == ts - 1
    assert "first" not in b._verdict_seen_ids
    monkeypatch.setattr(b.classifier, "record_gcl_verdict", real)
    assert b._poll_gcl_verdicts()
    assert "first" in b._verdict_seen_ids


def test_checkpoint_failure_replays_negative_before_authority(start, ledger, monkeypatch):
    b = start()
    ts = now_ms() - 100000
    b._verdict_watermark = ts - 1
    assert b._save_state()
    ledger.rows = [verdict("one", ts)]
    b._activated_types.add("routine")
    monkeypatch.setattr(b, "_save_state", lambda: False)
    assert not b._poll_gcl_verdicts()
    assert "routine" not in b._activated_types
    assert not b._verdict_seen_ids and b._verdict_watermark == ts - 1
    b.close()
    b = start()
    assert not b.get_stats()["verdict_catchup_complete"]
    assert b._poll_gcl_verdicts()
    assert b._fn_count == 1


def test_foreign_prefix_backfill_builtin_and_survives_cannot_consume_own_id(start, ledger):
    b = start()
    ts = now_ms() - 60000
    ledger.rows = [verdict("shared", ts, domain="finance"),
                   dict(verdict("shared", ts), entry_type="audit.backfill.verdict"),
                   dict(verdict("shared", ts), entry_type="audit.verdict.backfill"),
                   verdict("builtin", ts, original_agent="deduplicate"),
                   verdict("passing", ts, verdict="SURVIVES"), verdict("shared", ts, domain=" k8s ")]
    assert b._poll_gcl_verdicts()
    assert b._fn_evaluated == b._fn_count == b._gcl_builtin_fails == 1


@pytest.mark.parametrize("missing", ["entry_type", "source_id", "agent_id", "entry_id", "domain", "decision_ref"])
def test_unscoped_or_payload_provided_identity_cannot_demote_or_advance(start, ledger, missing):
    b = start()
    ts = now_ms() - 20000
    row = verdict("one", ts)
    if missing in row:
        row["content"][missing] = row.pop(missing)
    else:
        row[missing] = row["content"].pop(missing)
    ledger.rows = [row]
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 0 and not b._verdict_seen_ids and b._verdict_watermark == 0


def test_anonymous_or_plaintext_strict_reader_fences_without_fetch(start, ledger):
    b = start()
    b._ledger_url = "http://ledger.test"
    assert not b._poll_gcl_verdicts()
    b._ledger_url = "https://ledger.test"
    b._ledger_token = ""
    assert not b._poll_gcl_verdicts()
    assert not ledger.calls


def test_checkpoint_bound_to_consumer_domain(start, ledger):
    b = start()
    ledger.rows = [verdict("finance-only", now_ms() - 10000, domain="finance")]
    assert b._poll_gcl_verdicts()
    b.close()
    other = start("finance")
    assert not other._poll_gcl_verdicts()
    assert len(ledger.calls) == 1
    assert not other.get_stats()["verdict_catchup_complete"]


def test_real_activation_demotion_survives_discovery_and_restart(qualification_bridge, ledger, monkeypatch):
    b = qualification_bridge
    activate(b, monkeypatch)
    b._ledger_url = "https://ledger.test"
    b._ledger_token = "local-test"
    monkeypatch.setattr(b, "_flush_promotion_events", lambda: None)
    ledger.rows = [verdict("negative", now_ms() - 25 * 60000, domain=b.domain)]
    assert b._poll_gcl_verdicts()
    b.close()
    again = CascadeBridge(ledger_url=b._ledger_url, ledger_token=b._ledger_token)
    try:
        again._discover_and_promote()
        assert not again._activated_types and not any(again._activated_contexts.values())
        assert again._agent_metrics["synthetic_rule"].deactivated
        assert again._poll_gcl_verdicts()
        again._discover_and_promote()
        assert not again._activated_types and not any(again._activated_contexts.values())
    finally:
        again.close()


def test_strict_without_state_withdraws_but_cannot_ack(start, ledger):
    b = start()
    b._state_file = ""
    ledger.rows = [verdict("negative", now_ms() - 2000)]
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 1 and b._verdict_watermark == 0 and not b._verdict_seen_ids
    assert b.get_stats()["verdict_catchup_error"] == "state_required"


def test_bounds_and_empty_continuation_fail_closed(start, ledger, monkeypatch):
    b = start()
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "20000")
    monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "20000")
    ledger.change = lambda p: dict(p, next_page_token="1", total_count=1)
    assert not b._poll_gcl_verdicts()
    assert ledger.calls[0]["page_size"] == "500" and not b._verdict_cursor
    ts = now_ms() - 2000
    ledger.rows = [verdict(str(n), ts, verdict="SURVIVES") for n in range(24)]
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "1")
    assert not b._poll_gcl_verdicts()
    assert len(ledger.calls) == 2
    ledger.change = lambda p: p
    assert not b._poll_gcl_verdicts()
    assert len(ledger.calls) == 22
    stats = b.get_stats()
    assert stats["verdict_next_offset"] == 20 and stats["verdict_window_total"] == 24
    assert b._poll_gcl_verdicts()


def test_changed_total_restarts_from_original_floor(start, ledger, monkeypatch):
    b = start()
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "1")
    monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "1")
    ts = now_ms() - 40000
    ledger.rows = [verdict(k, ts) for k in ("a", "b")]
    b._verdict_watermark = ts - 2
    assert not b._poll_gcl_verdicts()
    ledger.rows.insert(0, verdict("late", ts - 1))
    assert not b._poll_gcl_verdicts()
    assert b._verdict_cursor is None and b._verdict_watermark == ts - 2
    monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "3")
    assert b._poll_gcl_verdicts()
    assert not ledger.calls[2].get("page_token")
    assert b._fn_count == 3


def test_restart_with_old_pending_upper_bound_cannot_restore_freshness(start, ledger, monkeypatch):
    b = start()
    monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "1")
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "1")
    ts = now_ms() - 40000
    ledger.rows = [verdict(k, ts) for k in ("a", "b")]
    assert not b._poll_gcl_verdicts()
    b.close()
    wall_time = time.time() + 25 * 60
    monkeypatch.setattr(time, "time", lambda: wall_time)
    b = start()
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 1 and not b.get_stats()["verdict_catchup_complete"]
    assert b.get_stats()["verdict_catchup_error"] == "stale_coverage"
    monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "2")
    assert b._poll_gcl_verdicts()


def test_processing_withholds_legacy_learned_agent_until_fresh_strict_eof(
        qualification_bridge, ledger, monkeypatch):
    from types import SimpleNamespace
    b = qualification_bridge
    activate(b, monkeypatch)
    b._ledger_url, b._ledger_token = "https://ledger.test", "local-test"
    b._last_promotion_time = time.monotonic()
    monkeypatch.setattr(b, "_enqueue_decisions_to_ledger", lambda *args: None)
    monkeypatch.setattr(b, "_classification_dispatch_enabled", lambda: False)

    def run(n):
        event = SimpleNamespace(signal_type="routine", severity="medium", namespace="ns",
                                resource_name="host", evidence={"message": f"distinct {n}"})
        b.process([event])
        return len(b._last_remaining)

    assert run(1) == run(2) == 1
    assert b._poll_gcl_verdicts()
    assert run(3) == 1 and run(4) == 0
    wall_time = time.time() + 25 * 60
    monkeypatch.setattr(time, "time", lambda: wall_time)
    assert run(5) == 1
    assert b._poll_gcl_verdicts()
    assert run(6) == 0
    ledger.change = lambda p: httpx.Response(502)
    assert not b._poll_gcl_verdicts()
    assert run(7) == 1


def test_poller_serializes_reads_but_snapshot_can_run_during_transport(start, ledger):
    import threading
    b = start()
    entered = threading.Event()
    release = threading.Event()
    def parked(page):
        entered.set()
        assert release.wait(5)
        return page
    ledger.change = parked
    ledger.rows = [verdict("once", now_ms() - 60000)]
    thread = threading.Thread(target=b._poll_gcl_verdicts)
    thread.start()
    try:
        assert entered.wait(5)
        assert b._save_state() and not b._poll_gcl_verdicts()
        assert not b.get_stats()["verdict_catchup_complete"]
        with open(b._state_file) as stream:
            assert json.load(stream)["verdict_seen_ids"] == []
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(ledger.calls) == b._fn_count == 1
    with open(b._state_file) as stream:
        assert json.load(stream)["verdict_seen_ids"] == ["once"]


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"[]", headers={"content-length": str(2**22)}),
    httpx.Response(200, stream=httpx.ByteStream(b" " * (2**21 + 1))),
    httpx.Response(200, headers={"content-encoding": "br"}, stream=httpx.ByteStream(b"bad")),
])
def test_strict_transport_rejects_oversized_or_encoded_pages(start, ledger, response):
    b = start()
    ledger.change = lambda p: response
    assert not b._poll_gcl_verdicts()
    assert not b._verdict_cursor and b._verdict_watermark == 0


def test_ambiguous_page_applies_valid_negatives_only_once_across_restart(start, ledger, monkeypatch):
    b = start()
    ts = now_ms() - 20000
    b._verdict_watermark = ts - 1
    bad = verdict("bad", ts)
    del bad["content"]["domain"]
    ledger.rows = [bad, verdict("good", ts)]
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 1 and b._verdict_watermark == ts - 1
    b.close()
    b = start()
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 0 and b._verdict_watermark == ts - 1
    ledger.rows[0] = verdict("bad", ts)
    assert b._poll_gcl_verdicts()
    assert b._fn_count == 1


def test_legacy_unscoped_negatives_deduplicate_without_claiming_coverage(start, monkeypatch):
    monkeypatch.setenv("CASCADE_VERDICT_MODE", "legacy")
    b = start()
    ts = now_ms() - 25 * 60000
    b._verdict_watermark = ts - 1
    row = verdict("old-id", ts)
    for name in ("entry_type", "agent_id", "source_id"):
        del row[name]
    del row["content"]["domain"]
    client = httpx.Client
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"entries": [
        dict(row, entry_type="audit.backfill.verdict"), row]}))
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=transport, **kw))
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 1 and b._verdict_watermark == ts - 1
    b.close()
    b = start()
    assert not b._poll_gcl_verdicts()
    assert b._fn_count == 0 and not b.get_stats()["verdict_catchup_complete"]


def test_empty_or_backfill_only_read_cannot_move_live_watermark_past_late_visible_fail(start, ledger):
    b = start()
    ts = now_ms() - 3600000
    b._verdict_watermark = ts - 1
    for rows in ([], [dict(verdict("history", ts + 5000), entry_type="audit.backfill.verdict")]):
        ledger.rows = rows
        b._poll_gcl_verdicts()
        assert b._verdict_watermark == ts - 1 and b._fn_count == 0
    ledger.rows = [verdict("late-visible", ts)]
    assert b._poll_gcl_verdicts()
    assert b._fn_count == 1 and b._verdict_watermark == ts


def test_dense_timestamp_replays_only_new_chain_positions_after_restart(start, ledger, monkeypatch):
    from cascade_compression import verdict_catchup
    monkeypatch.setattr(verdict_catchup, "MAX_SEEN", 2)
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "2")
    b = start()
    ts = now_ms() - 100
    ledger.rows = [dict(verdict(str(i), ts), chain_position=i) for i in range(1, 6)]
    assert b._poll_gcl_verdicts() and b._fn_count == 5
    b.close()
    b = start()
    ledger.rows.append(dict(verdict("next", ts), chain_position=6))
    assert b._poll_gcl_verdicts()
    assert b._fn_count == 1


def test_durable_receiver_receipts_from_real_consumer_survive_restart(start, ledger, monkeypatch):
    from cascade_compression import verdict_catchup
    monkeypatch.setattr(verdict_catchup, "MAX_SEEN", 2)
    b = start()
    ts = now_ms() - 50000
    ledger.rows = [
        dict(verdict("negative", ts), correlation_id="batch-1", entry_hash="abc", chain_position=10),
        verdict("positive", ts, verdict="SURVIVES"),
        verdict("built", ts, original_agent="deduplicate"),
        verdict("foreign", ts, domain="aap"), dict(verdict("history", ts), entry_type="audit.backfill.verdict")]
    assert b.get_verdict_receipt("negative") is None
    assert b._poll_gcl_verdicts()
    b.close()
    b = start()
    r = b.get_verdict_receipt("negative")
    assert r == dict(entry_id="negative", entry_type="audit.verdict", entry_hash="abc", chain_position=10,
                     written_ts=ts, agent_id="governed-cognitive-loop", source_id="gcl-cascade-audit",
                     correlation_id="batch-1", domain="kubernetes", decision_ref="decision-negative",
                     subject_type="routine", original_agent="learned", verdict="FAILS",
                     disposition="applied_fails", consumed_at_ms=r["consumed_at_ms"])
    assert r["consumed_at_ms"] >= ts
    assert b.get_verdict_receipt("positive")["disposition"] == "observed_survives"
    assert b.get_verdict_receipt("built")["disposition"] == "builtin_fails"
    assert b.get_verdict_receipt("foreign") is None and b.get_verdict_receipt("history") is None
    assert b._poll_gcl_verdicts()
    assert b._fn_count == 0 and b.get_verdict_receipt("negative") == r


def test_receipt_never_precedes_durable_effect_and_survives_cursor_failure(start, ledger, monkeypatch):
    b = start()
    ledger.rows = [verdict("one", now_ms() - 60000)]
    real = b._save_state
    monkeypatch.setattr(b, "_save_state", lambda: False)
    assert not b._poll_gcl_verdicts()
    assert b.get_verdict_receipt("one") is None and b._verdict_watermark == 0
    monkeypatch.setattr(b, "_save_state", lambda: False if b._verdict_watermark else real())
    assert not b._poll_gcl_verdicts()
    assert b._verdict_watermark == 0 and b.get_verdict_receipt("one")["verdict"] == "FAILS"
    b.close()
    b = start()
    assert b._poll_gcl_verdicts()
    assert b._fn_count == 0


def test_strict_client_uses_pinned_ca_and_bypasses_environment_proxy(start, monkeypatch):
    import ssl
    import certifi
    b = start()
    b._ledger_url = "https://127.0.0.1:28099"
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1")
    client = httpx.Client
    settings = []
    def factory(**kw):
        settings.append(kw)
        return client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
            "entries": [], "next_page_token": "", "total_count": 0})), **kw)
    monkeypatch.setattr(httpx, "Client", factory)
    assert b._poll_gcl_verdicts()
    assert settings[0]["trust_env"] is False
    context = settings[0]["verify"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setenv("SSL_CERT_FILE", "/missing-run-ca.crt")
    assert not b._poll_gcl_verdicts()
    assert len(settings) == 1


def test_receipt_store_failure_restarts_before_failed_page(start, ledger, monkeypatch):
    from cascade_compression import verdict_receipts
    b = start()
    ts = now_ms() - 60000
    ledger.rows = [verdict("one", ts)]
    append = verdict_receipts.append
    def disk_full(db, records):
        raise OSError("simulated receipt store full")
    monkeypatch.setattr(verdict_receipts, "append", disk_full)
    assert not b._poll_gcl_verdicts() and b._verdict_watermark == 0
    assert b.get_verdict_receipt("one") is None
    b.close()
    b = start()
    monkeypatch.setattr(verdict_receipts, "append", append)
    assert b._poll_gcl_verdicts() and b._fn_count == 1
    assert b.get_verdict_receipt("one")["verdict"] == "FAILS"


def test_conflicting_immutable_id_fences_without_publishing_false_proof(start, ledger):
    b = start()
    ts = now_ms() - 60000
    ledger.rows = [verdict("same", ts, verdict="SURVIVES"), verdict("same", ts)]
    assert not b._poll_gcl_verdicts()
    assert b._verdict_watermark == 0
    assert b.get_verdict_receipt("same") is None


def test_missing_bound_receipt_store_blocks_readiness_after_restart(start, ledger):
    from pathlib import Path
    b = start()
    ledger.rows = [verdict("one", now_ms() - 60000)]
    assert b._poll_gcl_verdicts()
    b.close()
    path = Path(b._state_file + ".verdict-receipts.sqlite3")
    path.rename(path.with_suffix(".offline"))
    b = start()
    assert not b._poll_gcl_verdicts()
    with pytest.raises(OSError):
        b.get_verdict_receipt("one")
    assert not path.exists()


def test_one_effect_checkpoint_and_one_cursor_checkpoint_per_page(start, ledger, monkeypatch):
    b = start()
    ts = now_ms() - 60000
    ledger.rows = [verdict(str(i), ts) for i in range(5)]
    from unittest.mock import Mock
    saves = Mock(wraps=b._save_state)
    monkeypatch.setattr(b, "_save_state", saves)
    assert b._poll_gcl_verdicts()
    assert saves.call_count == 2
    assert all(b.get_verdict_receipt(str(i))["verdict"] == "FAILS" for i in range(5))
