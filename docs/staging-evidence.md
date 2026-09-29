# Staging-success evidence

A green process is not, by itself, proof that Cascade is safe or effective. The public
`cascade-stage-evidence` command binds independently generated artifacts to one immutable run and
fails closed unless every required gate passes.

```bash
cascade-stage-evidence \
  --manifest staging-manifest.json \
  --classification classification-report.json \
  --runtime runtime-benchmark.json \
  --stats-before stats-before.json \
  --stats-after stats-after.json \
  --output staging-evidence.json
```

The manifest uses `cascade.staging-manifest.v1alpha1` and declares the exact commit, image digest,
configuration digest, taxonomy revision, frozen model revisions, runtime run ID, an audit snapshot
window that brackets the run, release verification, and ledger health. It must include a healthy,
capacity-monitored ledger outbox with a declared policy;
deleting pending rows is not accepted as a recovery policy.

The command returns zero only for `staging_success`. It requires:

- a decision-grade held-out classification report on the same run;
- the fixed `oss-rc-a-v1` quality profile: at least 200 held-out records, at least 25 examples of
  every label, at least 50 important examples, at least 25 authoritative suppressions, 95%
  coverage, 0.85 balanced accuracy, 0.80 macro F1, 0.98 authoritative-suppression precision, and
  calibration error no greater than 0.10;
- zero authoritative dangerous misses;
- measured calibration;
- a CPU allocation and commit-bound runtime benchmark with no errors;
- durable, drained audit queues with no new drops;
- ledger capacity below its declared alert threshold;
- a healthy, drained ledger-owned outbox with an explicit lifecycle policy;
- clean-clone completion within 15 minutes;
- passing tests and package/container smoke checks; and
- successful CI with SBOM and provenance evidence.

The output contains aggregate metrics and cryptographic artifact digests. It excludes raw records,
signal payloads, credentials, cluster names, routes, and deployment manifests. Inputs containing
private evidence should remain in ignored or external evidence storage; only the sanitized output
is eligible for publication after review.
