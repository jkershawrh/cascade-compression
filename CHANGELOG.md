# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## Unreleased

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
