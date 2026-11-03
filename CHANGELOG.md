# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## 0.2.0 - 2026-11-02

First staging-qualified release. Classification agreement on adjudicated
production cases: **78.1%** (better than the 3-model judge panel at 55.6%
and the human panel at 48.8%). Platform-pilot compression: **92%
end-to-end elimination with zero dangerous misses**.

### Classification

- Platform-context fact extraction: the DEFAULT_FACT_PROMPT now includes
  multi-tenant demo-platform semantics — provisioning lifecycle and image
  churn are known_pattern, infrastructure failures are needs_attention.
  This took classification agreement from 41.9% to 78.1% on 160
  adjudicated cases (98% needs_attention recall, 4 dangerous).
- Matching platform context in the Kubernetes domain-pack system prompt.
- Release profile upgraded to `staging_qualified` with updated limitations.

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

### Small-model qualification

- Qwen2.5-3B qualified as the event-lane model: 100% fact-level agreement
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

- README rewritten: value-first, one number in the first paragraph,
  60-second quickstart at the top, MCP server surfaced.
- Case study published: `docs/case-study-platform-pilot.md` — the bounded
  24-hour platform claim with full limitations.
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
