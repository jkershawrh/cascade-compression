# Public evidence register

Cascade separates executable behavior, historical context, synthetic fixtures, and release proof.
The presence of a number in the repository does not make it a current performance or production
claim. This register identifies the authoritative evidence for each public claim class.

| Claim class | Public assertion allowed | Authoritative evidence | Current status |
|---|---|---|---|
| Engine behavior and contracts | The documented mechanics and fail-closed invariants exist | Source, schemas, and the tests that exercise the named invariant on the same commit | Verified by CI for the commit; not an effectiveness claim |
| Incubating OSS artifact | The prerelease installs, its tested mechanics execute, and its package/container supply chain is bound to one commit | Green CI/public-safety checks, attested candidate package and image, SBOM/provenance, and `config/release-profile.json` declaring `incubating_oss` | Eligible for an experimental prerelease; not staging or production proof |
| Classification effectiveness | Held-out balanced accuracy, macro F1, per-label metrics, dangerous misses, coverage, and calibration | A `decision_grade` report over a frozen label-coverage challenge corpus, two independent human reviews, resolved disagreements, prequalified authoritative `known_pattern` candidates, and bound generative/semantic/hybrid revisions; prevalence claims require a separate representative corpus | Pending for the next RC; no result is claimed yet |
| Runtime cost | Per-core throughput and tail latency for nano, HTTP, semantic cache-hit/unique-miss, and recall cells | `oss-rc-runtime-v1` output from the exact candidate image in an isolated, declared CPU allocation | Pending for the next RC; policy thresholds are not measured results |
| Governed staging health | Classification, runtime, audit delivery, ledger capacity, clean-clone, package/container, SBOM, provenance, and CI passed together | `cascade.staging-evidence.v1alpha3` output returning `staging_success`, bound to an attested candidate manifest and one declared run window | Pending for the next RC |
| Supply-chain identity | A wheel, source archive, SBOM, and container came from a specific candidate commit | Candidate manifest hashes, immutable image digest, GitHub attestations, and release workflow | Mechanics verified; candidate-specific evidence is created only after the release commit is frozen |
| Historical routing benchmark | The bundled router can consume a sanitized benchmark-shaped corpus | `config/corpora.json`, `data/benchmark_matrix.json`, and the metadata embedded in those files | Context only; raw source runs are not published and the snapshot is not RC evidence |
| TCO scenarios | The calculator evaluates explicit assumptions | `data/hardware_profiles.json`, `data/model_profiles.json`, `data/workload_profiles.json`, and request assumptions | Synthetic/mixed assumptions only; not current pricing, customer economics, or measured savings |
| Synthetic compression fixture | The known mixed fixture routes exactly 80 of 100 signals without inference | Runtime harness route oracle and tests | Mechanics only; 80% is fixture construction, not production prevalence or expected savings |
| Production or customer outcome | None | Would require a separately reviewed, sanitized evidence artifact | No public claim |

## Publication rule

A staging-qualified claim must cite the sanitized staging-evidence artifact and its digest. An
incubating OSS prerelease instead publishes its explicit release profile and must not make
classification-effectiveness, natural compression-rate, staging-readiness, or production claims.
Supporting raw
records, review receipts, disagreement queues, runtime logs, deployment details, credentials, and
environment identifiers remain outside this repository. Historical or synthetic data must retain
its evidence classification and cannot be promoted to release evidence by documentation wording.

The fixed staging-qualification requirements are defined in [staging evidence](staging-evidence.md), the
classification methodology in [classification evaluation](classification-evaluation.md), and the
runtime methodology in [runtime benchmark](runtime-benchmark.md). `RELEASING.md` defines the order
in which candidate and release artifacts are created.
