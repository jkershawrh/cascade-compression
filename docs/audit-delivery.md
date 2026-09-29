# Durable audit delivery

Cascade's external ledger and GCL integration is optional. When enabled, ledger delivery must not
share the signal request path: an unavailable ledger cannot stop classification, but it also must
not silently erase the audit trail.

Set `CASCADE_STATE_FILE` to enable the normal persisted Cascade state. Cascade then creates
separate SQLite write-ahead spools beside that file: one for decision and promotion receipts, and
one for memory lifecycle events before batching. Explicit
`CASCADE_LEDGER_RECEIPT_SPOOL_FILE` and `CASCADE_LEDGER_MEMORY_SPOOL_FILE` settings override their
locations. The spools:

- acknowledges records only after the ledger accepts the batch;
- survives process and pod restarts;
- retries failures with bounded exponential backoff;
- has configurable row and serialized-payload bounds (`CASCADE_LEDGER_RECEIPT_PENDING`,
  `CASCADE_LEDGER_RECEIPT_BYTES`, `CASCADE_LEDGER_MEMORY_PENDING`, and
  `CASCADE_LEDGER_MEMORY_BYTES`);
- drops the oldest record only after that explicit bound is exceeded; and
- never includes its payloads in public metrics.

Without either path, Cascade uses a memory-only queue and reports
`ledger_memory_queue_durability=memory_only`. This is suitable for local mechanics tests, not for a
governed staging claim.

The `/stats` response exposes those measurements independently as `ledger_receipt_*` and
`ledger_memory_*`: pending count, row and byte utilization, oldest age, spool bytes, cumulative
failures, consecutive failures, drops, successful writes or batches, and last successful delivery.
Alert at minimum on:

- durability other than `sqlite` in governed staging;
- utilization at or above 0.80;
- a non-zero consecutive failure count;
- oldest age beyond the deployment's recovery objective; or
- any increase in dropped events.

The spool protects Cascade-side delivery only. Ledger database capacity, retention, partitioning,
and any ledger-owned outbox remain responsibilities of the ledger deployment. A full database can
still exhaust a bounded spool, so storage alerts and tested recovery procedures are required for a
complete governance claim.

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
