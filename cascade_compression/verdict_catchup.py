"""Bounded consumption of the immutable ledger's GET /api/entries offset pages."""
from datetime import datetime
import hashlib
import json
import logging
import os
import ssl
import time

import httpx

from . import verdict_receipts

log = logging.getLogger(__name__)
MAX_PAGE_SIZE = 500
MAX_PAGES = 20
MAX_BYTES = 2 * 1024 * 1024
MAX_SEEN = 2000
MAX_AGE_MS = 20 * 60 * 1000
LIVE = "audit.verdict"
AUDITOR = "governed-cognitive-loop"
SOURCE = "gcl-cascade-audit"


class Incomplete(ValueError):
    """A fixed, safe-to-report reason for withholding authority."""


def _limit(name, default):
    try:
        return max(1, min(default, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _ms(value):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and 0 < len(value) <= 64:
        if value.isascii() and value.isdecimal():
            result = int(value)
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError("naive timestamp")
            result = int(dt.timestamp() * 1000)
    else:
        raise ValueError("invalid timestamp")
    if not 0 <= result <= int(time.time() * 1000):
        raise ValueError("timestamp out of range")
    return result


def _domain(name):
    name = name.strip().lower()
    return "kubernetes" if name == "k8s" else name


def _scope(bridge):
    identity = [bridge._ledger_url.rstrip("/"), _domain(bridge.domain), bridge._verdict_mode]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _cursor(bridge):
    floor = bridge._verdict_watermark
    floor = _ms(0 if floor is None or floor == "" else floor)
    saved = bridge._verdict_cursor
    if saved is None:
        return dict(version=1, from_ts=floor, to_ts=int(time.time() * 1000),
                    page_size=_limit("CASCADE_VERDICT_PAGE_SIZE", MAX_PAGE_SIZE),
                    offset=0, total=None, high_ts=floor)
    if not isinstance(saved, dict) or set(saved) != {
        "version", "from_ts", "to_ts", "page_size", "offset", "total", "high_ts"
    }:
        raise ValueError("invalid saved verdict cursor")
    if any(type(saved[k]) is not int for k in saved):
        raise ValueError("invalid saved verdict cursor numbers")
    if (saved["version"] != 1 or saved["from_ts"] != floor
            or not floor <= saved["high_ts"] <= _ms(saved["to_ts"])
            or not 1 <= saved["page_size"] <= MAX_PAGE_SIZE
            or not 0 < saved["offset"] < saved["total"]):
        raise ValueError("invalid saved verdict range or offset")
    return saved


def _check_headers(response, *, require_length=False):
    if response.status_code != 200:
        response.raise_for_status()
        raise ValueError("partial verdict response")
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("unsupported encoding")
    length = response.headers.get("content-length")
    if require_length or length is not None:
        if not length or not length.isascii() or not length.isdecimal() or int(length) > MAX_BYTES:
            raise ValueError("missing or oversized content length")


def _legacy_headers(response):
    # Historical GET adapters buffer JSON. Bound real HTTP reads before httpx
    # buffers them. The strict integration uses streaming even for chunked HTTP.
    _check_headers(response, require_length=True)
    if "transfer-encoding" in response.headers:
        raise ValueError("legacy reader requires a fixed-length response")


def _read(client, bridge, cursor, headers):
    params = dict(entry_type=LIVE, page_size=str(cursor["page_size"]), from_ts=cursor["from_ts"],
                  to_ts=cursor["to_ts"], include_content_raw="false")
    if cursor["offset"]:
        params["page_token"] = str(cursor["offset"])
    url = bridge._ledger_url.rstrip("/") + "/api/entries"
    if bridge._verdict_mode == "legacy":
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            data = {"entries": data}
    else:
        params.update(agent_id=AUDITOR, source_id=SOURCE)
        with client.stream("GET", url, params=params, headers=headers) as response:
            _check_headers(response)
            body = bytearray()
            chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
            for chunk in chunks:
                if len(chunk) > MAX_BYTES - len(body):
                    raise ValueError("oversized verdict page")
                body.extend(chunk)
            data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError("missing verdict page")
    if len(data["entries"]) > cursor["page_size"]:
        raise ValueError("page exceeds requested bound")
    return data


def _string(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 4096:
        raise ValueError("invalid verdict identity")
    return value


def _apply(bridge, row, cursor, seen, db, pending):
    from .bridge import _BUILTIN_AGENT_NAMES

    legacy = bridge._verdict_mode == "legacy"
    if not isinstance(row, dict):
        raise ValueError("malformed verdict envelope")
    if "entry_type" in row:
        if _string(row["entry_type"]) != LIVE:
            return  # The ledger's entry_type predicate is a prefix match.
    elif not legacy:
        raise ValueError("missing entry type")
    for field, expected in (("agent_id", AUDITOR), ("source_id", SOURCE)):
        if not (legacy and field not in row) and row.get(field) != expected:
            raise ValueError("wrong auditor")
    content = row.get("content")
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict):
        raise ValueError("missing content")
    foreign = False
    if not (legacy and "domain" not in content):
        foreign = _domain(_string(content.get("domain"))) != _domain(bridge.domain)
    key = "" if legacy and "entry_id" not in row else _string(row.get("entry_id"))
    timestamp = row.get("written_ts")
    if timestamp is None and legacy:
        timestamp = row.get("timestamp", row.get("created_at"))
    ts = _ms(timestamp)
    if not cursor["from_ts"] <= ts <= cursor["to_ts"]:
        raise ValueError("verdict outside requested window")
    kind = _string(content.get("subject_type"))
    ref = _string(content.get("decision_ref"))
    result = content.get("verdict")
    if result not in ("FAILS", "SURVIVES"):
        raise ValueError("unknown verdict")
    if foreign:
        return ts
    agent = _string(content.get("original_agent")) if not legacy or result == "FAILS" else ""
    builtin = agent in _BUILTIN_AGENT_NAMES
    if not legacy:
        position = row.get("chain_position")
        if position is not None and (type(position) is not int or not 0 < position < 2**63):
            raise ValueError("invalid chain position")
        receipt = dict(entry_id=key, entry_type=LIVE, agent_id=AUDITOR, source_id=SOURCE,
                       domain=_domain(bridge.domain), decision_ref=ref, subject_type=kind,
                       original_agent=agent, written_ts=ts, verdict=result, chain_position=position)
        for name in ("entry_hash", "correlation_id"):
            value = row.get(name)
            receipt[name] = None if value is None or value == "" else _string(value)
        if result == "SURVIVES":
            receipt["disposition"] = "observed_survives"
        else:
            receipt["disposition"] = "builtin_fails" if builtin else "applied_fails"
        previous = pending.get(key) or verdict_receipts.lookup(db, key)
        if previous is not None:
            if any(previous[k] != v for k, v in receipt.items()):
                raise ValueError("conflicting immutable verdict ID")
            return ts
    elif key and key in seen:
        return ts
    if result == "FAILS":
        if builtin:
            bridge._gcl_builtin_fails += 1
        else:
            bridge._apply_gcl_failure(kind, ref)
    if not legacy:
        receipt["consumed_at_ms"] = int(time.time() * 1000)
        pending[key] = receipt
    if key:
        seen[key] = None
        if len(seen) > MAX_SEEN:
            del seen[next(iter(seen))]
    return ts


def _next_offset(page, cursor):
    total = page.get("total_count")
    token = page.get("next_page_token")
    end = cursor["offset"] + len(page["entries"])
    if (type(total) is not int or end > total
            or token != (str(end) if end < total else "")
            or (token and not page["entries"])
            or any(page.get(k, False) is not False for k in ("stale", "partial", "truncated"))
            or ("has_more" in page and page["has_more"] is not bool(token))):
        raise Incomplete("incomplete_page")
    return end if token else None


def _checkpoint(bridge, cursor, watermark, scope, seen):
    # Caller holds lifecycle lock, same ordering as all other snapshot writers.
    # Never roll back demotions; retry a failed page from the last durable cursor.
    with bridge._verdict_lock:
        old = (bridge._verdict_cursor, bridge._verdict_watermark,
               bridge._verdict_scope, bridge._verdict_seen_ids)
        bridge._verdict_cursor, bridge._verdict_watermark = cursor, watermark
        bridge._verdict_scope, bridge._verdict_seen_ids = scope, seen
    try:
        if bridge._save_state() is not True:
            raise Incomplete("checkpoint_failed")
    except BaseException:
        with bridge._verdict_lock:
            (bridge._verdict_cursor, bridge._verdict_watermark,
             bridge._verdict_scope, bridge._verdict_seen_ids) = old
        raise


def _consume_page(bridge, page, cursor, scope, db):
    # Pre-upgrade IDs were stamped before their effects. Only trust IDs saved
    # by this consumer under its own ledger/domain/mode binding.
    seen = dict(bridge._verdict_seen_ids) if bridge._verdict_scope else {}
    high_ts = cursor["high_ts"]
    pending = {}
    failed_ids = set()
    for row in page["entries"]:
        try:
            processed = _apply(bridge, row, cursor, seen, db, pending)
            if processed is not None:
                high_ts = max(high_ts, processed)
        except Exception:
            # Continue with independent negatives. A reused ID on the same
            # malformed page must never mask the failed effect on retry.
            key = row.get("entry_id") if isinstance(row, dict) else None
            failed_ids.add(key if isinstance(key, str) else None)

    if bridge._verdict_mode == "strict" and not bridge._state_file:
        raise Incomplete("state_required")
    for key in failed_ids:
        pending.pop(key, None)
        seen.pop(key, None)
    if pending:
        # Make every withdrawal durable before publishing its externally
        # readable receipt. Until SQLite commits, the cursor stays replayable.
        if bridge._save_state() is not True:
            raise Incomplete("checkpoint_failed")
        verdict_receipts.append(db, pending)
    try:
        if failed_ids:
            raise Incomplete("unprocessed_verdict")
        offset = _next_offset(page, cursor)
    except Incomplete:
        _checkpoint(bridge, bridge._verdict_cursor, bridge._verdict_watermark, scope, seen)
        raise
    if cursor["total"] is not None and page["total_count"] != cursor["total"]:
        # Offset pages have no cross-request snapshot. Changed cardinality
        # invalidates the offset, never the original inclusive floor.
        _checkpoint(bridge, None, cursor["from_ts"], scope, seen)
        raise Incomplete("window_changed")
    if offset is None:
        _checkpoint(bridge, None, high_ts, scope, seen)
        return None
    continuation = dict(cursor, offset=offset, total=page["total_count"],
                        high_ts=high_ts)
    _checkpoint(bridge, continuation, cursor["from_ts"], scope, seen)
    return continuation


def poll(bridge):
    """One bounded poll. Only a fresh, durably checkpointed strict EOF opens authority."""
    bridge._verdict_poll_running = True
    bridge._verdict_catchup_complete = False
    bridge._verdict_catchup_error = ""
    try:
        strict = bridge._verdict_mode == "strict"
        scope = _scope(bridge)
        if (bridge._verdict_scope is not None and bridge._verdict_scope != scope
                or bridge._verdict_cursor is not None and bridge._verdict_scope is None):
            raise Incomplete("scope_mismatch")
        if strict and not bridge._ledger_token:
            raise Incomplete("authentication_required")
        if bridge._ledger_token and not bridge._ledger_url.startswith("https://"):
            raise Incomplete("https_required")
        cursor = _cursor(bridge)
        headers = {"Accept-Encoding": "identity"}
        if bridge._ledger_token:
            headers["Authorization"] = "Bearer " + bridge._ledger_token
        hooks = {} if strict else {"response": [_legacy_headers]}
        tls = ssl.create_default_context(cafile=os.getenv("SSL_CERT_FILE") or None)
        with httpx.Client(timeout=15, event_hooks=hooks, trust_env=False, verify=tls) as client, \
                verdict_receipts.open_store(bridge, scope, write=True) as db:
            for _ in range(_limit("CASCADE_VERDICT_MAX_PAGES", MAX_PAGES)):
                if bridge._closed:
                    raise Incomplete("closed")
                page = _read(client, bridge, cursor, headers)
                with bridge._lifecycle_lock:
                    if bridge._closed:
                        raise Incomplete("closed")
                    checked_through = cursor["to_ts"]
                    cursor = _consume_page(bridge, page, cursor, scope, db)
                    if cursor is None:
                        if not strict:
                            raise Incomplete("legacy_negative_only")
                        if not 0 <= int(time.time() * 1000) - checked_through <= MAX_AGE_MS:
                            raise Incomplete("stale_coverage")
                        bridge._verdict_checked_through = checked_through
                        bridge._verdict_catchup_complete = True
                        return True
        bridge._verdict_catchup_error = "page_limit"
    except Exception as exc:
        # Never export transport exception text (it can contain tokens/URLs).
        bridge._verdict_catchup_error = str(exc) if isinstance(exc, Incomplete) else type(exc).__name__
        log.debug("Incomplete verdict catch-up (%s)", bridge._verdict_catchup_error)
    finally:
        bridge._verdict_poll_running = False
    return False
