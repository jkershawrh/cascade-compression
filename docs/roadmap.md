# Cascade OSS roadmap

Reviewed 2026-10-13. The source package is on `0.2.0rc5`; the release profile is `incubating_oss`.
Reusable product mechanics exist. The next phase improves adoption, inspectability, and evidence,
without turning classifier research into a prerequisite for having an OSS product. This is an
ordered roadmap with exit criteria, not a dated commitment or a claim that pending gates passed.

## 1. Make the public artifact the qualification target

Owner role: OSS and proof maintainers. Select a frozen candidate package/image and require all
qualification runners to report its exact identity. Resolve local-engine shadowing outside OSS;
do not transfer a different implementation's results to this candidate.

Exit: the tested package, image, source, contracts, configuration, models, and taxonomy reconcile
to one candidate manifest. Changes after qualification require another run.

Progress on step 1: an opt-in runner in the proof repository now checks exact OSS wheel bytes
and loaded module origins. Static synthetic integration passed; this does not complete the
service/replay migration or the candidate provenance/qualification gate.

## 2. Prove useful decisions and incremental work reduction

Owner role: evaluation maintainers and independent reviewers. Run generative, semantic, and hybrid
classification over the same independently adjudicated holdout. Keep label-coverage challenges
separate from representative-prevalence tests. Compare full-system work against the actual prior
workflow and a simple deterministic baseline in the separate proof environment.

Exit: candidate-bound per-label quality, dangerous misses, coverage, calibration, final outcomes,
fallback, and complete measured work. Register only claims supported by reviewed aggregates.
Follow [classification-evaluation.md](classification-evaluation.md) and
[staging-evidence.md](staging-evidence.md); do not weaken their gates to improve the story.

## 3. Demonstrate durability and reviewable decisions

Owner role: runtime and governance maintainers. Exercise restart, model outage, overload, storage
failure, expiry, demotion, rollback, and governed audit recovery. Distinguish classifier pending
work from audit-delivery spools; neither mechanism implies the other's guarantee. Prioritize a
clear decision/evidence trail and replay-before-activation over unrelated feature expansion.
Evaluate memory usefulness and sourced recall; memory strength is not truth or suppression authority.

Exit: measured and reconciled losses, repeats, failures, recovery, queue delay, audit health,
and independently reviewed memory outcomes on the selected artifact.

## 4. Publish evidence and promote the tested artifacts

Owner role: release maintainer and evidence reviewer. Bring reusable fixes upstream with synthetic
regressions and publish only approved public aggregates. Follow [proof-handoff.md](proof-handoff.md).
Keep incubating publication distinct from optional staging qualification and from customer value.

Exit: public-boundary checks, green candidate CI, package/container checks, SBOM/provenance,
and the required attested evidence handoff for any staging-qualified release. A publication date
alone does not establish effectiveness, production readiness, or savings.

## Optional optimization track

Evaluate custom bounded decision models as interchangeable backends. Runtime labels and rubrics
should remain outside the engine's domain policy; score semantics and revisions must be explicit.
Start in comparison mode, recalibrate for new taxonomies, and require held-out evidence before
granting authority. Add a backend only when lower total cost, including fallback, is demonstrated.
The current public service taxonomy remains the documented four-label contract; arbitrary-label
support in a research prototype does not change that contract automatically.

The roadmap is domain-independent. New collectors, hosting platforms, and model families should
follow a demonstrated need rather than become new completion gates for the existing product.
