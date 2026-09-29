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

The manifest uses `cascade.staging-manifest.v1alpha5` and declares the exact commit, image digest,
configuration digest, taxonomy revision, frozen model revisions, runtime run ID, an audit snapshot
window that brackets the run, the complete candidate manifest downloaded from the manual staging
workflow, release verification, and ledger health. The candidate manifest must identify the same
commit and immutable image digest, predate the test window, and attest both target architectures,
container SBOM/provenance, package SBOM/provenance, candidate-manifest provenance, and exact
wheel/source/SBOM hashes. The staging
manifest must also include a healthy, capacity-monitored ledger outbox using the fixed
`immutable-relay-and-archive-v1` policy. The outbox must have no pending,
in-flight, or failed rows; no undelivered row may have been deleted; archival must be verified; the
relay must succeed inside the measured audit window; and a non-destructive recovery drill must have
completed within the previous 90 days.

The command returns zero only for `staging_success`. It requires:

- a decision-grade held-out classification report on the same run;
- a complete same-corpus generative, semantic, and hybrid comparison, with the hybrid arm selected
  for the release decision, frozen revisions for all three arms, and at least 95% coverage per arm;
- the fixed `oss-rc-a-v1` quality profile: at least 200 held-out records, at least 25 examples of
  every label, at least 50 important examples, at least 25 authoritative suppressions, 95%
  coverage, 0.85 balanced accuracy, 0.80 macro F1, 0.98 authoritative-suppression precision, and
  confidence values for at least 95% of classified records with calibration error no greater than
  0.10;
- zero authoritative dangerous misses;
- measured calibration;
- a CPU allocation and commit-bound runtime benchmark with no errors;
- the fixed `oss-rc-runtime-v1` profile, including nano throughput/tail latency, HTTP request-path
  latency, mixed route oracles, llm-d-sc cache-hit and unique-miss overhead at serial and parallel
  load, and exact-precedent recall at 1K and 10K memories;
- durable, drained audit queues using `preserve_queued_reject_new`, with persistent rejection
  counters present and no new rejections or drops;
- ledger capacity below its declared alert threshold;
- a healthy, drained ledger-owned outbox with an explicit lifecycle policy;
- clean-clone completion within 15 minutes;
- passing tests and package/container smoke checks; and
- successful CI with SBOM and provenance evidence.

The output contains aggregate metrics and cryptographic digests for the staging manifest, candidate
manifest, classification report, runtime report, and both audit snapshots. It excludes raw records,
signal payloads, credentials, cluster names, routes, and deployment manifests. Inputs containing
private evidence should remain in ignored or external evidence storage; only the sanitized output
is eligible for publication after review.

After downloading the candidate manifest from its workflow run, verify its attestation before
embedding it in the staging manifest:

```bash
gh attestation verify staging-candidate-manifest.json \
  --repo jkershawrh/cascade-compression
```
