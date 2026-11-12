# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## 0.2.1rc1 - Unreleased

- Fix `MemoryArchive.stats()` reading `federated_sources` outside the archive
  lock. The unlocked set comprehension over the live `_memories` dict raised
  `RuntimeError: dictionary changed size during iteration` under concurrent
  `store()` traffic, surfacing as intermittent HTTP 500s on `/stats` and
  `/memories/stats` (observed on infra01 cascade-aap, ~1% of reads with a
  1s dashboard poll). The whole stats read now holds `self._lock`, matching
  the strengths comprehension. Regression test:
  `tests/test_stats_concurrency.py`.
- Checkpoint final memory labels with their linked classification-update audit
  events. Recover retained results without treating a failed write as completed.
- Consume delayed governance verdicts from a bounded, persisted cursor. In
  strict mode, incomplete catch-up withholds learned suppression authority.

- Set the local candidate to `0.2.1rc1` with the `incubating_oss` profile.
  The corresponding Helm chart version is `0.2.1-rc.1`.
- Correct the public evidence register and pilot claims.
- Repair the project lock and installed-wheel plugin smoke.
  Include NumPy in the dev extra for the head contract tests.
- Require an unambiguous fact response with a JSON boolean `designed`.
  Malformed evidence abstains instead of acquiring a benign label.
- Reuse results only on matching decision evidence and backend settings.
  Changed or lossy queue inputs cannot claim an exact repeat; cache hits
  do not earn a fresh positive qualification sample.
- Preserve severity validity through classification work. An invalid
  severity or unverified restored rule cannot take a healthy-by-spec
  shortcut. Successful value-rule work is counted separately from inference.
- Omit the authorization header on credential-free model endpoints.
  Configured bearer credentials and authentication failures are unchanged.

## 0.2.0 - 2026-11-02

The published tag points to `6800cd270a5e14cd419c65c760048c3fda8c5617`.
Its profile declared `staging_qualified`, but tag CI and release verification
failed. The published wheel has no verified, candidate-bound staging handoff.
[Published wheel SHA-256 and workflow records](docs/evidence-register.md)

### Classification and pilot evidence

- Added platform context to fact extraction and the Kubernetes domain-pack
  prompt. Agreement rose from 41.9% to 78.1% on the 160 tuning cases, with
  four dangerous classifications. On the held-out 100, 84% agreement tied
  the always-needs_attention baseline.
- The earlier rc7 replay retained 748,787/817,479 and preserved the final
  count for all 215 recurring events. The separate ~92% figure was a
  reported estimate of live traffic avoiding inference. [Case study](docs/case-study-platform-pilot.md)

The measurements below describe the historical experiments cited with the tag.

### Value rules (deterministic sensor lanes)

- `ValueRuleAgent` (engine, domain-agnostic): declarative threshold rules
  over structured numeric content fields with derived quantities
  (expression trees), categorical gating, and range/membership ops.
- Healthy-by-spec else labels: covered signal types with no rule match
  resolve deterministically without a model call. 100% deterministic
  resolution on the multi-sensory corpus (1,880 rule + 18,120 else =
  zero model calls).
- AI4I modality boundary closed: 97.3% deterministic recall (the earlier
  91.2% fact-first number was leak-inflated; clean-input fact-first is 0%
  — the modality boundary is total). Backblaze: 87.3% failure-day, 86.7%
  7-day lead-up.

### Sensor adapters (numeric-sense front ends)

- `sensor_fields` module: vibration/acoustic (pure-Python radix-2 FFT),
  force/tactile, proprioception. Validated on the 20K multi-sensory
  corpus: 100% recall, 0% false positives, 57K signals/second.

### Small-model comparison

- The event-lane comparison reported for Qwen2.5-3B: 100% fact-level agreement
  with the 8B on K8s doc-truth, 97.4% on BGL, 3.7GB RSS (5x smaller),
  1.9x faster at equal CPU. granite-3.2-2b rejected (0% fact agreement,
  systematic impact inflation).

### Cross-domain truth measurements

- Network intrusion (RT-IoT2022, 12 classes): fact-first 88.6% attack
  recall, 0.0% normal FP. Dev-fitted single-feature flow rules do not
  transfer (43.5%/73.9%).
- Credit-card fraud (rare-class, 0.173% prevalence): value rules 73.6%
  recall at 76.1% at-prevalence precision; combined 82.5%/78.1%.
- Satellite telemetry (SMAP/MSL): 43% holdout recall — the
  temporal-context boundary (contextual anomalies invisible to
  window-local rules).

### Memory and governance

- Memory durability: SQLite-backed durable memory queue (survives
  restarts), LLM request coalescing (in-flight dedup).
- Digest/RAG surface: memory digests, grounded RAG with canonicalized
  citations, read-only workspace API.
- MCP server: 8 read-only tools (recall, memories_stats, query_memories,
  export_memories, system_perspective, explain_signal, search_knowledge,
  system_narrative). Standalone-file deployment fix (import fallback).
- Truth-regression monitor: weekly CronJob comparing current classifier
  against a frozen baseline.
- Drift-watcher v2: webhook-ready alert channel.

### Documentation and distribution

- Added a quickstart and MCP server documentation to the README.
- Case study published: `docs/case-study-platform-pilot.md` — platform pilot observations.
- Helm chart: version 0.2.0, appVersion 0.2.0.

## 0.2.0rc28 - 2026-10-28

- Template cache: facts are template-intrinsic by default — context
  scoping is now opt-in (`CASCADE_TEMPLATE_CACHE_CONTEXT_KEYS`) and
  separate from the suppression context keys, because context-dependent
  handling belongs to the contextual suppressors downstream. Template
  normalization is a deployment convention
  (`CASCADE_TEMPLATE_CACHE_NORMALIZE=digits|none`): numbers-as-parameters
  for templated event streams, verbatim messages for streams where
  numbers are semantics (amounts, measurements).

## 0.2.0rc27 - 2026-10-28

- Add the template cache: deterministic per-template classification reuse
  in the engine (`CASCADE_TEMPLATE_CACHE=1`). The first occurrence of a
  template pays the generative backend's cost; repeats within the TTL
  are served from cache with full provenance.
