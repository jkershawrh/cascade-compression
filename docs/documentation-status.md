# Documentation status and authority

Reviewed 2026-10-13. This index covers all tracked Markdown documentation in this checkout,
the current local proof guides where applicable, and the new handoff/roadmap guides. It records
document scope and authority; it is not an assertion that every historical number was remeasured
or that CI, release publication, or production qualification was rerun.

The package source is on the `0.2.0rc5` incubating line. [proof-handoff.md](proof-handoff.md)
defines reviewed transfer from production evidence; [roadmap.md](roadmap.md) defines next steps.
Mechanics, classification effectiveness, staging health, production suitability, and value are
separate claim classes. No new effectiveness result is supplied by this documentation update.

## Reading order

Start with README, the roadmap, the proof handoff, and the evidence register/dossier. Use current
guides for the named checkout's implementation. Historical measurements require their exact
run/revision and limitations. Historical narratives are retained for context; follow the roadmap
instead of their old future-work lists. Unknown source records remain unresolved rather than
being filled with plausible values. The custom CDM prototype remains outside these repositories.

## Inventory

| Document | Authority |
|---|---|
| [CHANGELOG.md](../CHANGELOG.md) | current guide |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | current guide |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | current guide |
| [GOVERNANCE.md](../GOVERNANCE.md) | current guide |
| [README.md](../README.md) | current guide |
| [RELEASING.md](../RELEASING.md) | current guide |
| [SECURITY.md](../SECURITY.md) | current guide |
| [SUPPORT.md](../SUPPORT.md) | current guide |
| [docs/adjudication-rubric.md](adjudication-rubric.md) | current guide |
| [docs/anchor-engineering.md](anchor-engineering.md) | current guide |
| [docs/architecture.md](architecture.md) | current guide |
| [docs/audit-delivery.md](audit-delivery.md) | current guide |
| [docs/cascade-compression-mathematics.md](cascade-compression-mathematics.md) | current guide |
| [docs/classification-evaluation.md](classification-evaluation.md) | current guide |
| [docs/contract-compatibility.md](contract-compatibility.md) | current guide |
| [docs/domain-pack-guide.md](domain-pack-guide.md) | current guide |
| [docs/event-workflow.md](event-workflow.md) | current guide |
| [docs/evidence-register.md](evidence-register.md) | current guide |
| [docs/promotion-guidelines.md](promotion-guidelines.md) | current guide |
| [docs/proof-handoff.md](proof-handoff.md) | current guide |
| [docs/roadmap.md](roadmap.md) | current guide |
| [docs/runtime-benchmark.md](runtime-benchmark.md) | current guide |
| [docs/staging-evidence.md](staging-evidence.md) | current guide |
| [docs/triage-profile.md](triage-profile.md) | current guide |

## Maintenance rule

Every reusable behavior change updates its public guide and regression coverage in OSS. Every
claim update binds to a reviewed artifact and updates the evidence register. The proof layer
records private evaluation detail and its matching public artifact identity. Do not copy private
payloads or operational configuration upstream. Keep historical observations labeled when they
cannot be reconciled; do not manufacture proof through documentation wording.

## Artifact handoff follow-up

[proof-handoff.md](proof-handoff.md) records the restricted proof runner’s opt-in wheel binding
and its limited synthetic verification. No production effectiveness claim or release was added.

## Current white paper

The [Cascade Compression white paper](cascade-compression-whitepaper.md) is the current review
edition dated 2026-10-13. It centers validated decision reuse, governed lifecycle, and total-work
evaluation. It excludes CDM research and does not claim production qualification or savings.
