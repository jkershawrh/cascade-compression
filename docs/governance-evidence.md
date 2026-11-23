# Governed-authority evidence windows

The repeat/suppression profile makes claims that a test suite cannot settle:
authority is *earned* from confirmed noise, *withdrawn* by three independent
paths, and *re-earned* after a restart. Observing those over time is the only
way to support them, and a live counter is not evidence — it disappears with the
process.

A **window** is a declared interval over a pinned artifact during which the
governance trail is the record. This note defines what makes one citable.

## What a window requires

1. **A pinned image digest** per instance. A tag is not enough; the digest is
   what the claim attaches to.
2. **No restarts.** Authority is re-earned by design, so a restart resets the
   window's meaning. The capture records `pod_started_at` for exactly this
   check.
3. **A fixed configuration.** Record the environment that governs behaviour
   (memory cap, dedup windows, promotion interval, repeat identity policy).
   Any change ends the window.
4. **A declared start**, and an end that is stated when the record is made.

## What to capture

Per instance, per capture:

- image digest, pod start time, and the deployment's reported `started_at`
- `compression_ratio`, `cascade_handled` / `signals_processed`
- `activated` agents and the promotion log tail
- `shadow_checks` / `shadow_demotions`
- verdict catch-up state (`complete`, `error`, `watermark`)
- memory counters (`size`, `capacity`, `formed_total`, `evictions_total`)
- the governance trail: `<state>.governance.jsonl`

The trail is the primary artifact. Everything else is context for reading it.

## What invalidates a window

- any instance restart (recorded by pod start time)
- a configuration change, including a hand-edited state file (for example a
  seeded verdict watermark)
- a new build, even one that only changes documentation
- a partially failed capture: missing trail or missing digest

State an invalidation explicitly rather than silently restarting the window.

## Reading `evictions_total`

`formed_total` and `evictions_total` must be read together. An archive at
capacity evicts as fast as it forms, so a large `formed_total` with almost equal
`evictions_total` means the archive is a conveyor, not an accumulating memory.
Any claim about durable memory has to account for that ratio.

The archive is also the destination of every classification result, so a
conveyor costs more than durable memory. A result is retained, then applied to
the memory it is linked to; if that memory was evicted in between, the result is
resolved as dropped work and the verdict is never applied — no suppression
authority, no label, nothing counted as success. `llm_dropped_target_evicted`
counts exactly those discards and is **per process**, so read it as a live loss
rate next to `llm_classified` (observations) and
`llm_signal_outcomes_since_start.classified` (applied successes).

The audit destination produces the same shape of loss from a different cause. If the ledger
cannot accept the memory event that must accompany a result, the result cannot commit; once it is
older than `CASCADE_COMPLETION_PENDING_MAX_AGE` (default 900 s) it is discarded exactly as above —
never applied, never counted as success — and counted in `llm_dropped_audit_unavailable`. Read
both counters as live per-process loss rates: one for a destination that no longer holds the
memory, one for a destination that cannot be told about it. Neither belongs in `classified`.

A conveyor outrunning the classifier looks like a healthy but idle lane from the
outcome block alone: on 2026-11-20 `cascade-aap` held 9,929 of a 10,000 cap,
evicted 10,334,000 of 10,343,929 formations, discarded 775 results in 40
minutes, and reported `classified` unchanged across a 180 s sample while
`llm_classified` rose 635 → 736. Over 7.5 h the lane applied **one** verdict
against 7,212 observations, so that ratio is deterministic-tier only.

The intuitive remedy for a conveyor is to raise `CASCADE_MEMORY_MAX`, and that
is the wrong first step. The archive is part of the same state snapshot the
classifier checkpoints before it publishes a verdict, so a larger archive means
a slower checkpoint — and the checkpoint, not the model, bounds the
classification rate. On 2026-11-20 `cascade-aap` serialised, gzipped and fsynced
34.6 MB of JSON per checkpoint
(988 ms: 294 serialise, 661 gzip, 32 write), twice per result, capping the lane
at 0.51 results/s against a measured 0.47. Sizing the archive for the ~74,000
entries that would outlive an 890 s classifier raised that to ~4.8 s and lowered
the ceiling to 0.10 results/s, below the admission rate, so the queue would grow
faster than it drains. Make the per-result checkpoint cheap first — journal the
completion, flush the archive on a timer — and bound the queue by age while you
do, because a delay that grows without bound cannot be sized for: on the same
instance the classification delay reached 7.0 h within 7.5 h, which at 107
signals/s would be ~2.7 M entries, and that instance had stopped forming
memories entirely while still discarding every result that arrived. A larger
archive before those changes is worse than no change at all.

`llm_signal_outcomes_since_start` is restored verbatim from the state snapshot
at boot, despite its name and its `started_at`, which is per-process. Its
`enqueued` and `in_flight` therefore span restarts and cannot be compared with
per-process `signals_processed` or `cascade_forwarded`; `reconciled` and
`terminal_complete` are the fields that tell you whether the restored ledger
closes.

## Reading the promotion trail

One climb writes two records, and their counts are not meant to match:

- `promoted` is a **per-agent summary** of a sweep: `from_tier`, the tier
  reached, and `transitions` (how many tiers were crossed in that sweep). Its
  `mirror_of: promotion` says the authoritative per-transition record follows.
- `promotion` (or `demotion`) is the **per-transition evidence chain** that goes
  to the ledger. There is one per tier boundary, so a single sweep that crosses
  two tiers writes one `promoted` summary and two `promotion` records.

`status` on a `promoted` summary is the **nano activation gate** — 200 samples
with zero important. A candidate promoted on 57 samples is correctly `red`
against that gate while still meeting the candidate thresholds, so read
`status_scope` before calling the pair contradictory.

Inside `evidence`, the false-negative fields have different populations:

- `false_negative_count` is cumulative and divides by `samples_tested` to
  reproduce `false_negative_rate` and `1 - accuracy`.
- `false_negative_count_last_batch` is the count that instant demotion acts on.
  It reads 0 whenever the latest batch was clean, even while the lifetime rate
  is non-zero.

Records written before 2026-11-20 are not trustworthy on this point: the bridge
never populated the count, so `false_negative_count: 0` appears next to a
non-zero rate, and the batch counter was not recorded at all. Cite those by
`false_negative_rate` and `samples_tested` only.

## Citing a window

Quote the window identifier, the image digest, the pod start times, and the
trail entries directly. A claim of the form "authority was withdrawn" needs an
entry with a timestamp, an agent and a reason; a claim of the form "compression
held at X%" needs the window boundaries and the artifact digest.

Do not aggregate across windows. Two windows on different digests are two
observations, not one longer one.
