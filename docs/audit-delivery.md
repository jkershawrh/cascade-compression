# Durable audit delivery

Cascade's external ledger and GCL integration is optional. When enabled, ledger delivery must not
share the signal request path, and the two guarantees it serves — keep classifying, and never
erase the audit trail — must never be traded for one another silently. When those two conflict,
this is the policy:

- a completed classification is retained until it can be applied together with its audit event;
- if the ledger cannot accept events (the memory-event spool is at capacity, or the delivery path
  does not answer), the result cannot commit, and admission is held closed rather than applying a
  verdict with no provenance event — a verdict without provenance would mutate institutional memory
  invisibly, which is worse than refusing the signal;
- that hold is bounded: `CASCADE_COMPLETION_PENDING_MAX_AGE` (default 900 s) discards the unapplied
  work, counts it in `llm_dropped_audit_unavailable`, and never counts it as success. Read that
  counter as the price of the bound, next to `llm_dropped_target_evicted`;
- the bound bounds the hold, not the outage. While the destination is dead an instance admits
  little, and keeps nothing it admits. That is the intended cost of refusing to fabricate a
  verdict, and it is not a recovery;
- admission is observable from outside: `/ready` evaluates the gate and returns 503 when the
  instance is refusing signals, and `/health` reports the same state in its `admission` block
  without failing. Keep readiness on `/ready` and liveness on `/health`; serving both from
  `/health` is what allowed a fully blocked instance to report `ok` and stay Ready;
- the gate has two halves and `/ready` must name both. A pending completion blocks admission, and
  so does a memory-event buffer that cannot queue another event: `process()` answers
  `memory_audit_backpressure` in that case with no completion pending at all. Two readings of that
  buffer are misleading. Its byte budget (`CASCADE_MEMORY_EVENT_MAX_BYTES`, default 16 MiB) engages
  long before its entry count on content-carrying events, so an entry count can show headroom on a
  full buffer: measured 2026-11-23, `cascade-k8s` held 71,594 of an advertised 100,000 entries and
  `cascade-aap` 86,919 of 200,000, both at the byte cap, where the entry caps would have needed
  ~23 MB and ~39 MB. And because the queue rejects the event that would *exceed* the budget, a
  saturated buffer settles just below the cap — measured live at 54 and 149 bytes of headroom on a
  16 MiB budget — so "bytes ≥ bytes_max" never fires either. Read
  `memory_audit_source_headroom_bytes` against `memory_audit_admission_floor_bytes`; admission is
  closed when the headroom cannot fit an event. `memory_audit_binding_limit` names which cap is the
  live one (`count` or `bytes`), so "which setting shuts this instance?" is answered by the payload
  rather than by arithmetic. For any given instance one of the two caps is usually out of reach:
  `cascade-memory` at 19,714 of 20,000 entries held 3.6 MB of its 16 MiB, so its count was binding
  and its byte budget was ~4.6x away. The queue and readiness evaluate one rule —
  `audit_admission_closed()` asks the same decision `_queue_events()` enforces — so they cannot drift
  apart again. The one intentional asymmetry: a single event over 64 KiB is refused regardless of
  capacity, which `/ready` does not try to predict, because it would have to close for an unbounded
  payload shape.

Prior behaviour, 2026-11-20T21:27Z to 2026-11-23: this section's constraint was violated in the
field. With the hosted ledger's read path saturated, both instances held admission closed with no
bound and `/health` answered `ok` throughout; `cascade-k8s` refused 26,708 of 26,708 signals over
11.5 h. The bound, the `admission` block and the `/ready` split exist because of that, so do not
treat a build older than them as satisfying this section.

A second partial violation, 2026-11-23T15:2xZ: both ledger-coupled instances refused 100 % of
signals with `memory_audit_backpressure` while `/ready` answered 200. The `/ready` split had closed
the completion half of the gate only; the archive buffer was never consulted.

A third, 2026-11-23T15:52Z: commit `fd981bb` consulted the archive, but compared `bytes >=
bytes_max`, which cannot fire because the queue rejects the event that would *exceed* the budget.
Both instances sat 54 and 149 bytes under a 16 MiB cap and still reported `blocked_by: []`. Do not
treat `blocked_by: []` beside a nearly full buffer as evidence of readiness: check that the build
closes on headroom against an event-sized floor, and that `memory_audit_source_headroom_bytes` is
reported at all.

Set `CASCADE_STATE_FILE` to enable the normal persisted Cascade state. Cascade then creates
separate SQLite write-ahead spools beside that file: one for decision and promotion receipts, and
one for memory lifecycle events before batching. Explicit
`CASCADE_LEDGER_RECEIPT_SPOOL_FILE` and `CASCADE_LEDGER_MEMORY_SPOOL_FILE` settings override their
locations. The spools:

- acknowledges records only after the ledger accepts the batch;
- survives process and pod restarts;
- retries failures with bounded exponential backoff;
- keeps at most one live retry timer per spool and cancels retry work during graceful shutdown;
- has configurable row and serialized-payload bounds (`CASCADE_LEDGER_RECEIPT_PENDING`,
  `CASCADE_LEDGER_RECEIPT_BYTES`, `CASCADE_LEDGER_MEMORY_PENDING`, and
  `CASCADE_LEDGER_MEMORY_BYTES`);
- preserves every queued undelivered record and rejects only new arrivals after an explicit bound
  is reached (`preserve_queued_reject_new`); and
- never includes its payloads in public metrics.

Without either path, Cascade uses a memory-only queue and reports
`ledger_memory_queue_durability=memory_only`. This is suitable for local mechanics tests, not for a
governed staging claim.

The `/stats` response exposes those measurements independently as `ledger_receipt_*` and
`ledger_memory_*`: pending count, row and byte utilization, oldest age, spool bytes, overflow
policy, cumulative failures, consecutive failures, rejected/dropped events, successful writes or
batches, and last successful delivery. It also reports path-free filesystem total/free bytes and
used fraction for each spool, plus a boolean used to combine budgets when both queues share one
volume; no local path, device identity, or payload is exposed. Of those, `pending`, `oldest_age`,
`spool_bytes`, cumulative `failures`, `events_dropped`/`rejected_total`, `batches_written` and
`last_success` are persisted with the state, so they describe a queue across a restart. The two
`consecutive_failures` counters are **per process**: after a restart the streak starts again while
`last_success` still names the last delivery that ever succeeded. To measure an outage that spans a
restart, read `last_success` with `oldest_age` (the age of the oldest undelivered record), not the
consecutive count.

The LLM counters split differently, and combining them across a restart produces a false ratio.
`llm_dropped` is persisted; `llm_classified`, `llm_noise` and `llm_important` are per process and
restart at zero. `noise_pct` is derived from the first two and is reported as `null`, **not `0.0`**,
when this process has observed nothing: an empty denominator is no measurement, not a perfect score.
`top_noise_types` is restored from the checkpoint and then incremented by this process, so its counts
span both horizons. `top_noise_types_scope` says so
(`restored_checkpoint_plus_process`), and `counters_since` dates the process half; never divide that
list by the per-process `classified` to build a rate.
`llm_priority_dropped` is a subset of `llm_dropped`, not an alternative count. Do not divide a
persisted `llm_dropped` by a per-process `llm_classified`; use the
`llm_signal_outcomes_since_start` block instead, and treat `reconciled: false` in that block as
disqualifying it for any rate or loss claim.

That block is not per process either. Its five counters are restored from the classifier-work
checkpoint, so its `started_at` (per process) and its counters (per instance) have different
horizons. Read `accounting_since` and `accounting_origin_known` for the counter origin, the nested
`process` block for this process's own flow, and `unattributed` for the signed residual between
lifetime outcomes and lifetime admissions. Comparing the restored `enqueued` with per-process
`signals_processed` or `cascade_forwarded` fabricates a burst: on 2026-11-23 `cascade-aap`
reported 10,409,157 admissions against one processed signal, all of it restored, and
`unattributed` 91,108 from the same checkpoint.

Capacity rejections are counted transactionally in each SQLite spool and survive process restarts;
the governed staging gate requires those counters in both audit snapshots with no increase. It
also recomputes row and serialized-payload utilization from their raw counters and fails closed if
either spool or its backing filesystem is at or above 80% in either snapshot, if the filesystem
lacks enough free space for the queue's remaining configured payload budget, if a capacity field
is missing, or if a reported utilization is inconsistent with its counters.
Alert at minimum on:

- durability other than `sqlite` in governed staging;
- utilization at or above 0.80;
- a non-zero consecutive failure count;
- oldest age beyond the deployment's recovery objective; or
- any increase in rejected or dropped events.

The spool protects Cascade-side delivery only. Ledger database capacity, retention, partitioning,
and any ledger-owned outbox remain responsibilities of the ledger deployment. A full database can
still exhaust a bounded spool, so storage alerts and tested recovery procedures are required for a
complete governance claim.

The memory-only development fallback remains bounded by dropping old entries and reports
`drop_oldest_memory_only`; it is intentionally unable to satisfy the governed staging gate.

### Immutable outbox recovery

The staging contract names one portable policy: `immutable-relay-and-archive-v1`. A conforming
ledger recovery procedure must preserve every undelivered row and its original idempotency key:

1. Keep Cascade's durable spools enabled while the ledger or relay is impaired. If capacity is near
   its alert threshold, stop nonessential producers before evidence is lost.
2. Reclaim only expired relay leases. Do not delete or rewrite pending, in-flight, or failed payloads.
3. Relay each row idempotently. Record the destination receipt before marking the outbox row
   delivered, so a crash can safely repeat the operation.
4. Move delivered rows into append-only archive storage with their payload hash, destination
   receipt, delivery timestamp, and original ordering key. Archival is not deletion of undelivered
   work.
5. Verify pending, in-flight, and failed counts are zero; verify the oldest pending age is zero;
   reconcile source and archive counts and hashes; and record that no undelivered row was deleted.
6. Exercise this recovery in a drill at least every 90 days. A release-candidate run must also
   observe a successful relay between its before/after audit snapshots.

Cascade intentionally does not issue destructive SQL against a separately owned ledger. The
staging gate consumes the resulting counts and timestamps and fails closed if the outbox is merely
declared healthy without this evidence.
