# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## Unreleased

- Add deterministic model-blind holdout freezing, two-reviewer adjudication, third-reviewer
  disagreement resolution, canonical signal binding, and a documented label rubric.
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
