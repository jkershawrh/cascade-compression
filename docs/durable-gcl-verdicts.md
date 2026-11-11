# Durable GCL verdict consumption

## Receiver contract

Configure `CASCADE_VERDICT_MODE=strict`, `CASCADE_LEDGER_URL=https://127.0.0.1:28099`, the ledger bearer token, `SSL_CERT_FILE=/ledger-tls/ca.crt`, and a persistent, single-owner `CASCADE_STATE_FILE`. The strict reader verifies the gateway certificate, including its IP subject alternative name, with the configured CA file. It bypasses HTTP proxy environment settings.

Call `bridge._poll_gcl_verdicts()` to run one bounded poll. A literal `True` means this call reached a fresh end of the pinned window and durably checkpointed its work. `False` means the receiver must consult `bridge.get_stats()["verdict_catchup_error"]` and retry or repair the reported condition. A page-limited poll can already have consumed individual entries.

Call `bridge.get_verdict_receipt(entry_id)` to look up the exact ledger-assigned verdict entry ID. It returns `None` until a strict, same-domain receipt exists. A returned dictionary contains these fields:

- `entry_id`, `entry_type` (always `audit.verdict`), `entry_hash`, `chain_position`, `written_ts` (ledger epoch milliseconds).
- `agent_id`, `source_id`, `correlation_id`, `domain` (normalized), `decision_ref`, `subject_type`, `original_agent`, and `verdict` (`SURVIVES` or `FAILS`).
- `disposition` is `observed_survives`, `applied_fails`, or `builtin_fails`. The latter records the existing built-in-agent exclusion from learned false-negative feedback. A passing verdict adds zero qualification credit.
- `consumed_at_ms` is the local time the consumer recorded the effect. Older compatible strict gateways can omit optional ledger metadata. Absent `chain_position`, `entry_hash`, or `correlation_id` is represented by `None`.

Compare the entry ID, decision reference, domain, expected verdict and disposition with the receiver's manifest. A run that also requires a complete drain must obtain both the individual receipts and a fresh successful poll. Treat a storage exception as a read failure.

Receipts live in `<CASCADE_STATE_FILE>.verdict-receipts.sqlite3` and survive the bounded in-memory replay cache. Preserve the database together with the bridge snapshot on the same private durable volume. The reader binds receipts to the configured endpoint, normalized domain and verdict mode. Use a separate state/receipt pair for each independent run or ledger backend, including runs that reuse the same loopback address. Only the bridge that owns the state may poll or write it.

The strict writer first checkpoints page effects in the bridge snapshot, commits the entry receipts in one SQLite transaction, and then checkpoints the continuation or completed watermark. A crash before the receipt commit replays the page. A crash after that commit can leave a readable receipt and an older cursor. Re-reading the same authenticated entry checks its stored verdict fields and resumes without applying feedback again. The snapshot binds the database UUID. A missing or different bound database fences recovery. Snapshot and database backups need a consistent recovery point. Storage exhaustion also fences progress.

For an independent read-only process, open the SQLite URI in `mode=ro`. The `identity` singleton contains `scope` and `store_id`; match these to `verdict_scope` and `verdict_store_id` in the bridge snapshot after the poll checkpoints. Use the bound parameter query `SELECT receipt FROM receipts WHERE entry_id = ?` and decode the JSON column. Receipt storage grows with distinct accepted local verdicts. Only a strict reader processing a ledger entry writes this evidence.

## Polling and progress

The gateway's `GET /api/entries` accepts inclusive epoch-millisecond `from_ts` and `to_ts`, an entry-type **prefix**, exact `source_id` and `agent_id`, and a decimal offset in `page_token`. The body contains `entries`, `total_count` and `next_page_token`. The reader requests parsed content with `include_content_raw=false`. It pins both time bounds and the page size across continuations. A single call allows 1–20 pages of 1–500 entries, configured by `CASCADE_VERDICT_MAX_PAGES` and `CASCADE_VERDICT_PAGE_SIZE`. Strict responses use identity encoding and a 2 MiB byte limit per page.

A persisted watermark is always the inclusive query floor. A new consumer starts at zero. An existing timestamp-only snapshot must belong to this same ledger and domain; the upgrade rereads available entries and disregards its legacy seen IDs. A page checkpoint retains the floor and saves the offset, original total, and highest processed live timestamp. Verified EOF advances the floor only through a processed live timestamp. Empty and backfill-only reads retain the floor. The freshness check uses the completed query's upper bound. The poller validates count, offset, terminal metadata, HTTP status, and explicit partial/stale/truncated indicators. A changed count invalidates a continuation and causes a rescan from its original floor. An error in one row still allows other valid negatives on the fetched page to withdraw authority.

The in-process scheduler polls on the processing/promotion interval. A receiver draining after the last signal must call the poller itself. Concurrent calls to one bridge cannot start a second reader. Learned suppression requires a completed strict scan with an upper bound within the past 20 minutes. A new poll, restart, stale scan, or read/checkpoint failure withholds learned suppression. The 20-minute age limit applies only to authority. Deterministic pipeline stages retain their configured profile behavior.

Inspect `verdict_mode`, `verdict_catchup_complete`, `verdict_catchup_error`, `verdict_watermark`, `verdict_checked_through`, `verdict_next_offset` and `verdict_window_total` in bridge stats. `page_limit` calls for another bounded poll. `stale_coverage` requires a fresh completed window. `incomplete_page`, `unprocessed_verdict`, storage errors, and transport errors require a successful retry. A concurrent caller may receive `False` while the owning poll is running.

The exact live type is `audit.verdict`, with envelope agent `governed-cognitive-loop` and source `gcl-cascade-audit`. Domain matching normalizes case and treats `k8s` and `kubernetes` as aliases. A valid foreign live record can contribute to shared stream progress. Only same-domain records produce local consumption receipts. Explicit backfill and prefix variants supply neither feedback nor timestamp progress. Strict reads require the scoped envelope, ledger-assigned ID and write timestamp, decision reference, subject type, and agent identity. Payload fields cannot substitute for envelope identity.

TLS authenticates the gateway, and the bearer authenticates the reader. The ledger's writer labels and correlation ID are filter fields. Live use requires a trusted issuer and an isolated or writer-authorized backend for the run. The API supplies offset pagination without a cross-request transactional snapshot. Reconcile the run's expected entry IDs to the durable receipts to detect missing or late commits, including inserts behind an already-completed watermark. The consumer cannot reconstruct records skipped before the input snapshot's floor.

## Legacy fixtures and local acceptance

`CASCADE_VERDICT_MODE=legacy` explicitly enables historical single-domain adapter reads. A missing type, writer, or domain is accepted here for negative feedback. Explicit foreign and historical types remain filtered. The legacy reader can deduplicate historical envelope IDs, and it always withholds governed authority and live entry receipts. A body containing only `entries` has ambiguous coverage, so applying an available negative leaves the floor pinned. Keep this mode separate from live replay.

The external `test_run_gates.py` used for this repair has SHA-256 `1d2e36ed870595cb64ef3cb295274ca884b4125041914d4eb27494c64e169bad`. Before the repair its 5-minute control passed and its 25-minute case failed on the target source. On the frozen repair, the original memory test passes in strict mode. **Both original verdict assertions still fail in default strict mode** at the check that a GET was issued. They configure plain HTTP without a token, mock only `Client.get`, and return unscoped entries without pagination metadata. The strict reader stops at `authentication_required` before transport. Running those exact fixture bytes in explicit legacy mode passes all three tests.

Run the strict replacement from the evidence checkout with the supplied test environment:

```sh
python -m pytest -q tests/test_strict_verdict_acceptance.py
python -m pytest -q tests/test_durable_gcl_verdicts.py
```

Both 5- and 25-minute replacement cases serve the authenticated ledger shape on an ephemeral loopback HTTPS port. Each qualifies a real bridge suppressor using 200 synthetic classifications, saves the old watermark, restores it, rejects an unrelated CA and accepts the pinned run CA with a deliberately unusable proxy. It consumes SURVIVES on page one, restarts, consumes FAILS on page two, and checks actual demotion, the saved watermark and seen ID, and the exact durable decision/correlation receipt. A second restart and poll verify deduplication and continued withdrawal after rediscovery. These are local source acceptance results. The gateway and classifier inputs in these tests are synthetic.
