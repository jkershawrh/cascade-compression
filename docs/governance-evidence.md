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

## Citing a window

Quote the window identifier, the image digest, the pod start times, and the
trail entries directly. A claim of the form "authority was withdrawn" needs an
entry with a timestamp, an agent and a reason; a claim of the form "compression
held at X%" needs the window boundaries and the artifact digest.

Do not aggregate across windows. Two windows on different digests are two
observations, not one longer one.
