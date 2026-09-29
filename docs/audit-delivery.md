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
