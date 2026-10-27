# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## Unreleased

- Add a read-only MCP server (`cascade-mcp`, optional `mcp` extra) exposing the
  federated shared memory to users and agents: recall with score breakdowns and
  an exact-match flag, stats with verification counts, filtered queries, and
  bounded export. Every response carries verification status and an explicit
  advisory note; agents can never write memories or obtain suppression
  authority through the interface. Supports offline bridge-state snapshots and
  live service proxying.

## 0.2.0rc21 - 2026-10-27

- Add truth-informed few-shot exemplars to the generative classifier.
  Domain plugins can now carry `FEW_SHOT_EXEMPLARS` (concrete adjudicated
  message examples per label) that are augmented into the system prompt.
  Validated at 6x agreement improvement on held-out cases with
  needs_attention recall 0% to 71%. The prompt revision hash changes
  when exemplars change, keeping measurements bound to their prompt.
- Add a Helm chart for one-command deployment with safe defaults
  (qualified_repeat_update profile, 72h TTL, 5% shadow validation).
- Fix the GCL verdict poll: paginate the backlog (previously a fixed
  100-entry read could silently drop FAILS verdicts under sustained
  stream volume via the 20-minute stale-watermark skip).

## 0.2.0rc19 - 2026-10-26

- Add the `qualified_repeat_update` nano profile: learned suppression
  gated by a per-decision qualification callback (activated type, pattern
  match, 200+ noise / 0 important evidence, durable state, protected
  labels, severity band). The bounded `verified_repeat_update` profile
  is unchanged; suppression under it remains discarded by design.
- Harden the repeat-update fold semantics: within-batch count growth
  folds into the retained observation and reaches HTTP survivors;
  across-batch growth is delivered as a new observation (acknowledged
  responses are immutable). Retained snapshots are deep-copied so
  caller mutation cannot rewrite delivered state.
- Fix the service adapter to expose `source` for dynamic suppressors
  (previously crashed silently on every batch).

## 0.2.0rc17 - 2026-10-24

- Add per-domain dedup windows via `CASCADE_DEDUP_WINDOW_<DOMAIN>` env vars.
- Add the anchor pipeline's `/classifier/stats` and `/classifier/comparisons`
  routes to the service (previously 404, silently closing the anchor gate).
- Add the fast-forward replay harness with a deterministic model stub.
- Fix dynamic suppressors to accept medium severity (the promotion band).

## 0.2.0rc7 - 2026-10-21

- Harden deterministic suppression behind complete event identity: exact-repeat suppression
  requires full identity fields, and the safety escalator keeps uncertain signals retained.
- Add validated lifecycle authority: restored activations must carry qualification evidence,
  current configuration bindings, and unexpired timestamps; rejected or expired qualification
  is withdrawn instead of reused, and confirmed-negative feedback demotes active suppressors.
- Preserve observation identity across serialization boundaries: exported records restore the
  identity their content hash was bound to, so state restore, federation import, deduplication,
  reinforcement, and rejection sets stay consistent across restarts. Distinct observations
  without complete event identity never merge.
- Succeed the 0.2.0rc6 evaluation line: this candidate carries the rc6 suppression-safety and
  classifier-evidence fixes forward together with the lifecycle, memory-serialization, and
  test-contract repairs listed above.

## 0.2.0rc6 - 2026-10-20

- Prepare a new incubating OSS candidate with suppression-safety and classifier-evidence fixes.
  This candidate does not establish classification accuracy, reduced LLM fallback, natural
  compression rates, staging readiness, or production readiness.
- Generate missing collector IDs and reject ambiguous batch IDs before agents run, preventing
  unrelated critical events from being removed together.
- Run protected-label, critical-content, and numeric safety checks before suppression; remove the
  pipeline's implicit info drop and retain explicit decisions for every handled signal.
- Check learned-rule expiry before every batch, independently of promotion maintenance.
- Preserve measurements and structured evidence in classifier input revision v3, including shadow
  checks. Older learned rules require fresh qualification; old calibration and runtime evidence
  must be regenerated. The legacy cache-hit benchmark now repeats identical evidence.
- Add fail-closed memory verification provenance so classification, similarity, strength, recall,
  and federated approval remain advisory until an exact match has a current locally governed
  benign outcome.
- Add verification lifecycle contracts and audit events while preserving backward compatibility:
  existing and imported memories default to unverified.
- Add a disabled-by-default experimental blend contract in which llm-d-sc proposes a suppressive
  candidate, governed memory supplies admissible evidence, and a Vela-compatible Noul verifier
  provides a thresholded second check without receiving final policy authority.
- Add a private-receipt outcome-evidence contract and validator that derives benign memory status
  only from no intervention, no service impact, and observed self-resolution.
- Return service admission failures as retained work (HTTP 503 with full retention) instead of
  acknowledging compressed delivery.
- Revise native test contracts to the validated-authority semantics: activation, TTL expiry, and
  contextual suppression are exercised through real qualification paths, verifier fixtures carry
  complete signal contexts, and merge semantics require identified events.
- Make the source distribution self-contained for the packaged test suite: conftest, fixtures,
  the validation matrix, the OpenAPI specification, public scripts, and the tests package marker
  are included.
- Align documentation on the production-proof handoff, exact-artifact qualification, current
  evidence limits, and the ordered product-sharpening roadmap; no new effectiveness claim.

## Unreleased

## 0.2.0rc5 - 2026-10-06

- Publish the verified public mechanics as an explicitly incubating OSS prerelease without
  classification-effectiveness, natural-compression-rate, staging-readiness, production-readiness,
  or customer-outcome claims.
- Preserve the independent staging-qualified gate for human-adjudicated three-arm classification,
  governed audit delivery, ledger archive/recovery, and candidate-bound operational evidence.
- Carry forward the exact CPU runtime benchmark, packaging, SBOM, provenance, and public/private
  boundary mechanics while keeping raw evidence and deployment configuration private.

## 0.2.0rc4 - 2026-10-06

- Prepare an immutable staging-qualified candidate whose release remains fail-closed until the
  complete human-adjudicated classification, CPU runtime, audit-delivery, ledger-capacity,
  clean-clone, package/container, SBOM, provenance, and CI evidence chain reports
  `staging_success`.
- Preserve the separation between the public candidate and private review records, operational
  logs, environment configuration, and deployment manifests.

## 0.2.0rc3 - 2026-09-29

- Separate incubating OSS publication from staging qualification: prereleases may publish verified,
  attested mechanics without effectiveness claims, while the complete human-adjudicated staging
  handoff remains mandatory for any staging-ready claim.
- Add deterministic model-blind holdout freezing, two-reviewer adjudication, third-reviewer
  disagreement resolution, canonical signal binding, and a documented label rubric.
- Distinguish representative-prevalence holdouts from label-coverage challenge holdouts, preflight
  unique per-label candidates and authoritative `known_pattern` records, and prevent representative
  samples from accidentally satisfying the release label-coverage gate.
- Add same-corpus generative/semantic/hybrid evaluation with per-label metrics, dangerous-miss
  accounting, confidence coverage, calibration, cryptographically bound independent-review
  receipts, and fail-closed decision-grade evidence.
- Add a fixed `oss-rc-a-v1` staging gate that binds classification, CPU runtime, audit delivery,
  ledger capacity, clean-clone/package/container checks, SBOM, provenance, and CI to one run.
- Add durable bounded SQLite spools for decision, promotion, and memory audit delivery with restart
  recovery, retry/backoff, byte limits, and observable loss/failure counters.
- Add a manual pre-release candidate workflow for immutable multi-architecture images with SBOM and
  provenance plus attested wheel/source/package-SBOM artifacts, allowing soak evidence before a
  release tag is created.
- Require non-destructive immutable-ledger outbox recovery evidence: idempotent relay, append-only
  archival, no deletion of undelivered work, drained state, an in-window relay success, and a recent
  recovery drill.
- Add synthetic mixed-route and recall-scaling runtime benchmarks with correctness oracles and
  verified CPU-allocation reporting.
- Add opt-in advisory triage and verified exact-repeat evaluation profiles.
- Include full cluster, resource UID, and stable content in duplicate identity
  so distinct incidents cannot collapse across clusters or replacement resources.
- Preserve request-local survivor metadata in the API response.
- Expose process-scoped original-signal classifier outcomes so queue,
  completion, failure, and drop counts can be reconciled during evaluation.
- Add a public evidence register and machine-checked evidence classifications for historical
  benchmark snapshots and calculator assumptions.
- Enforce private-path, environment-endpoint, credential-shape, and local-home-path boundaries on
  every tracked file before candidate or release publication.
- Gate tagged container publication on package/version verification and create the GitHub release
  only after both package and multi-architecture container artifacts succeed, with bound manifests.
- Attest the immutable staging-candidate manifest itself and require its provenance in the
  versioned staging-success contract.
- Preserve queued undelivered audit records when durable spool capacity is exhausted; reject and
  count new arrivals instead of deleting old history, and require that policy for staging success.
- Persist durable-spool rejection totals transactionally so restart cannot erase audit-loss
  accounting during a governed staging window.
- Gate staging on path-free audit-spool filesystem headroom, including combined queue budgets when
  both durable queues share one volume.
- Correct macro-F1 so supported but never-predicted classes contribute zero, close prediction
  metadata validation, and require independent review to finish before model evaluation; these
  semantics were published as `cascade.classification-evaluation.v1alpha4`.
- Harden learned-agent promotion with cumulative validation evidence, attributable time-bound
  approvals, activation-time threshold rechecks, fail-closed TTL handling, and type/context-scoped
  demotion. Restored counts and contextual suppressors must re-qualify before activation.
- Bind anchor candidates, evaluations, comparisons, and approvals to exact taxonomy revisions,
  holdout and evaluation digests, complete label support, and strict authority-margin types.
- Add an attested staging-evidence handoff that binds successful soak evidence to the exact tested
  candidate package and image; tagged releases promote those immutable artifacts without rebuilding.

## 0.2.0rc1 - 2026-09-11

Release candidate for public evaluation. This is not the final 0.2.0 release.

- Add an optional llm-d-sc gRPC classifier with generative, comparison, semantic, and hybrid
  policies, conservative confidence and severity gates, TLS support, and observable fallback.
- Add a public-safe custom anchor lifecycle with proposal validation, content-addressed candidate
  revisions, same-holdout evaluation, explicit approval, and rollback provenance.
- Preserve survivors before asynchronous classification and write authoritative results back to
  the exact memory record.
- Update the public architecture and event workflow around signal compression, learning, memory,
  federation, and optional governance.

## 0.1.0 - 2026-08-31

Initial OSS release.

- Three-tier deterministic, CPU-model, and reasoning-model cascade pipeline.
- Hardened promotion, shadow validation, TTL expiry, demotion, and audit provenance.
- Memory archive, recall, consolidation, priming, and federation APIs.
- Versioned public signal, decision, memory, promotion, collector, and evidence contracts.
- Entry-point interfaces for external collector and domain-pack plugins.
- Generic collectors, domain packs, and synthetic generators for reproducible evaluation.
- Routing, infrastructure pressure, fleet-planning, and TCO components.
- Standalone FastAPI service and dashboard.
