# Outcome loop — anchor proposal retention (2026-11-17)

Roadmap Track 1.5 asks for one chronic issue, an action taken, and the effect
visible. This records the first one, including the part that did not go the
way the thesis needs.

## The recurring condition

The `anchor-pipeline` CronJob runs every 15 minutes for two instances and
writes a proposal file and a report file per instance per run — about 384
files and 2.6 MiB per day — into `PVC anchor-proposals`, which is provisioned
at 100 MiB. Nothing pruned them.

| Measured 2026-11-17 18:00 | Value |
|---|---|
| Files on the volume | 18,035 |
| Used / free | 63 MiB / 24 MiB (**73%**) |
| Growth | ~384 files, ~2.6 MiB per day |
| Time to exhaustion at that rate | **~9 days** |

At exhaustion the CronJob would fail every 15 minutes: `anchor_pipeline`
cannot write its proposal, so the anchor-bootstrap gate stops producing
candidates and the failure becomes permanent rather than intermittent.

## Action

1. One-time prune of unvalidated proposal and report files older than 14 days:
   12,980 files, 23.0 MiB.
2. Retention enforced in the CronJob itself, so it cannot recur:

   ```sh
   find /proposals -maxdepth 1 -type f -name '*.json' \
     -mtime +${CASCADE_ANCHOR_RETENTION_DAYS:-14} -delete 2>/dev/null || true
   ```

   with `CASCADE_ANCHOR_RETENTION_DAYS=14` set on the CronJob.
3. Verified by running the CronJob on demand (`anchor-pipeline-retention-check`):
   `succeeded=1`, gate OPEN (39.1% margin-gated), two proposals written. The
   prune is idempotent on an already-pruned volume.

## Measured outcome

| | Before | After |
|---|---|---|
| Files | 18,035 | **5,058** |
| Used / free | 63 MiB / 24 MiB (73%) | **34 MiB / 53 MiB (39%)** |
| Projected steady state at 14-day retention | exhaustion in ~9 days | **~34-40 MiB, stable** |
| CronJob result | would fail at exhaustion | **succeeded, gate OPEN** |

## What this loop does *not* show

The detection did not come from Cascade. This condition was found by reviewing
the namespace, not by a memory or signal: the engine holds no capacity rule for
its own generated artifacts, and no digest surfaces "your volume is trending
full". Calling this a Cascade outcome loop would overstate the evidence.

The honest claim is narrower: **a recurring generator was bounded, measured
before and after, and verified by re-running the producer.** The thesis needs
the detection half, which is Track 3.1 (digest) plus a capacity rule that emits
a signal when a watched volume's growth projects exhaustion inside its horizon.
Until that exists, loop records should state plainly where the issue was found.

## Follow-up

- Add a capacity signal (projected exhaustion, not just current percentage) so
  the next instance of this class arrives through the pipeline.
- Include generated-artifact volumes in the digest scope.
- Consider the same retention question for `cascade-*-state` snapshots, which
  now serialize to ~17.5 MiB compressed at 50k memories.

## Detection half (follow-up, same day)

The gap above was closed far enough to test: the Prometheus collector now emits
`metric_pvc_projected_full` on a projected days-to-full instead of only a fill
percentage.

- 24 h trend (`deriv`) with a 1 KiB/s growth floor and a 50 % occupancy gate.
  Both filters were chosen from live data: a 6 h window is all burst on
  rewrite-heavy volumes (every PVC looked days from full), and flat volumes with
  sub-KiB/s noise project thousands of days out.
- Exclusive 7-day (high) and 7-30-day (medium) bands, so one volume yields one
  severity.
- The claim, not the node, is the identity. Kubelet volume series carry
  `persistentvolumeclaim`, `node` and `instance` together, and `node` previously
  won — the first version reported which node was involved rather than which
  volume was filling.
- Validated against infra01's Thanos: 118 volume series visible, four signals
  emitted. `deepfield-postgres-data` at 5.0 days (high),
  `local-registry-clair-postgres-15` at 20.6 days (medium), one ocpv06 guest
  disk at 23.3 days, and `cascade-aap-state` at 0.2 days (high).

Two honest limits:

1. **The projection is a linear 24 h extrapolation, so a one-off write inflates
   it.** `cascade-aap-state` reads 0.2 days largely because that volume absorbed
   today's state migration and a 123 MiB pre-change backup. The signal is a
   tripwire ("something is writing hard"), not a forecast. The next iteration
   should require sustained growth across two consecutive windows.
2. **Deployment is split from the engine.** The collector runs from a separate
   image (`cascade-collector`), built from the private tree; there is no
   BuildConfig for it, so this went out as a one-off binary build
   (`cascade-collector-projection`, digest `sha256:adf5e512…`) pointed at
   `collector-prometheus` only. Folding the collector into the release process is
   a prerequisite for citing these signals as part of a release.
