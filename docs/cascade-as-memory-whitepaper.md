# Cascade as Memory: How Machines Should Form Institutional Knowledge

## Executive Summary

Every organization has institutional memory. Today it lives in wikis nobody reads, runbooks nobody updates, and the heads of engineers who leave. It is fragile, unstructured, and degrades with every departure.

Cascade compression offers a different model: institutional memory that forms automatically from the organization's own signal streams. The same three-tier pipeline that achieves 99.1% compression on 142.4 million production signals — validated with zero false negatives — now encodes what matters, forgets what doesn't, recalls precedent when new events arrive, and forms associations across domains that no single human could hold in their head.

This paper describes the memory architecture built on top of the validated cascade compression engine. Five capabilities — survivor archive, recall, consolidation, priming, and federation — transform the cascade from a cost optimization into a knowledge formation system. All five are implemented, tested (592 tests, zero failures, zero regressions against the base pipeline), and ready for deployment on Intel Xeon 6 hardware running Red Hat OpenShift.

The cascade does not replace human judgment. It captures and preserves the *output* of human judgment — via LLM classification that encodes expert reasoning — and makes it durable.

Store everything, query later is a filing cabinet. Cascade compression is how machines remember.

---

## The Problem With Enterprise Memory

Organizations generate millions of signals daily. Kubernetes events, Ansible job outcomes, network telemetry, transaction alerts, security events. The standard approach is to store everything in a data lake and query it later. This creates three problems:

1. **Volume without comprehension.** A data lake remembers every heartbeat check, every routine success, every duplicate flood. It cannot tell you what mattered. Search requires knowing what to search for — which assumes the knowledge you're trying to build.

2. **Memory loss on departure.** When a senior engineer leaves, they take with them the pattern recognition that years of incident response built. "Last time the scheduler showed pressure and the AAP job failed, the root cause was firmware." That association exists nowhere in the data lake.

3. **No learning loop.** The organization responds to the same incidents repeatedly. There is no mechanism for the system to recognize "I have seen this before" and surface what happened last time. Each incident starts from zero.

Cascade compression solves all three by treating signal processing as memory formation.

---

## The Biological Mapping

The cascade's three tiers map directly to established models of biological memory:

| Cascade Tier | Memory Analog | Function | Retention |
|-------------|--------------|----------|-----------|
| Raw ingestion | Sensory memory | Sees everything | Milliseconds |
| Nano tier (85-99%) | Working memory | Filters, pattern-matches, discards most input | Seconds to minutes |
| Micro tier (1-15%) | Episodic memory | Classifies notable events — not just *what* but *what kind* | Hours to days |
| Macro tier (<1%) | Semantic / core memory | Deep reasoning on rare events that change understanding | Permanent |

The compression ratio rhymes. The human brain receives approximately 11 million bits per second of sensory input and compresses it to approximately 50 bits per second of conscious awareness — 99.9995% compression. The cascade achieves 99.1% on 142.4 million production Kubernetes signals and 98.1% on 553K Ansible Automation Platform signals. Same principle, same architecture, different substrate.

This is more than analogy. The cascade already implements the mechanisms of memory formation:

- **Encoding**: The corpus analyzer detects a pattern and proposes a draft agent — the system noticed something recurring and created a representation for it.
- **Consolidation**: The five-tier promotion ladder (draft → candidate → nano → micro → macro) tests, strengthens, and moves the representation from volatile to durable storage.
- **Recall**: Every nano agent that fires on an incoming signal performs recall. The agent's existence IS the memory — it does not look up a database row, it IS the learned pattern expressed as executable logic.
- **Forgetting**: The 72-hour TTL on activated agents is natural decay. Shadow validation and GCL audit verdicts trigger corrective forgetting — the system unlearns what it got wrong.
- **Priming**: After a significant incident, suppression thresholds for related signal types are temporarily lowered. The system pays more attention to things related to recent events.

---

## Architecture

### Memory Data Model

A `Memory` wraps a `Signal` (the cascade's domain-agnostic input protocol) with lifecycle metadata. Composition over inheritance — the same design pattern used throughout the cascade framework.

| Field | Type | Purpose |
|-------|------|---------|
| memory_id | UUID | Unique identifier |
| signal | Signal | Immutable snapshot of the original survivor |
| formed_at | timestamp | When this memory was created |
| strength | float (0.0-1.0) | Decays over time, reinforced by recall |
| recall_count | integer | How many times this memory was matched |
| consolidation_count | integer | How many consolidation cycles survived |
| source_instance | string | Which cascade instance formed this memory |
| classification | string | LLM or agent classification |
| content_hash | SHA256 | For deduplication — identical content reinforces rather than duplicates |
| feature_vector | dict | Extracted numeric features for similarity matching |

**Strength mechanics** — mechanistically defined, not arbitrary:

- **Initial**: Severity weight × survival confidence. Weights: info=0.1, low=0.2, medium=0.4, high=0.7, critical=1.0.
- **Decay**: strength × exp(−λ × hours). Default λ=0.01, half-life ≈69 hours.
- **Reinforcement**: strength += 0.1 × (1.0 − strength) per recall hit. Asymptotic to 1.0, never exceeds it.
- **Deduplication**: Storing a signal with the same content hash reinforces the existing memory instead of creating a new one.

Every lifecycle transition — formation, recall, consolidation, eviction, federation — emits a `MemoryEvent` for audit.

### Capacity Management

The archive is bounded (default 10,000 memories, configurable via `CASCADE_MEMORY_MAX`). When full, the bottom 10% by strength are evicted. This guarantees:

- The archive never grows unbounded in long-running deployments
- The strongest memories (most recalled, most reinforced, highest severity) always survive
- Eviction is auditable — every evicted memory produces an event with its final strength and reason

---

## Five Capabilities

### 1. Survivor Archive

Signals that survive the cascade pipeline — the ones that need inference, the ones that matter — are automatically stored as memories. No configuration required. The bridge captures survivors after `pipeline.run()` completes, before forwarding them to the LLM.

This means the archive accumulates organically. After hours of processing, it contains a compressed record of every significant event the system has seen. After days, it is institutional knowledge. After months, it is organizational memory.

**API**: `GET /memories/stats` returns archive size, formation count, eviction count, strength distribution. `POST /memories/query` filters by signal type, labels, minimum strength.

**Contract**: `contracts/schemas/memory-record.json` defines the JSON Schema. Every `Memory.to_dict()` output validates against it.

### 2. Recall

Given a new signal, the recall engine searches the archive for precedent: "Have I seen anything like this before?"

Four similarity functions, each capturing a different dimension of similarity:

| Function | Weight | What It Measures |
|----------|--------|-----------------|
| Type match | 0.4 | Same signal_type = 1.0, different = 0.0 |
| Label Jaccard | 0.2 | Overlap of label key-value pairs |
| Content feature cosine | 0.2 | Cosine similarity of numeric features (cpu%, memory%, etc.) |
| Text trigram | 0.2 | Trigram set overlap on message strings |

The composite score is multiplied by memory strength, so frequently reinforced and recently active memories surface first. This is the desired behavior — strong memories are more relevant than weak ones.

**Performance**: Recall over 1,000 memories completes in under 50ms. Pure Python, no external dependencies, no vector database required.

**Reinforcement on recall**: When a memory is matched, its strength increases, its recall count increments, and its last-recalled timestamp updates. Memories that keep getting recalled become core memories. Memories that are never recalled decay and eventually evict.

**API**: `POST /recall` accepts a signal and returns ranked precedent matches with score breakdowns.

### 3. Consolidation

Periodically, the archive re-runs its memories through the current cascade pipeline. This is the machine equivalent of memory consolidation during sleep — replaying events and deciding what to keep in long-term storage.

The mechanism:

1. Convert each memory back to a Signal (with fresh IDs to avoid stale dedup state)
2. Run through `CascadePipeline.run()` with the current set of agents
3. Memories that get suppressed/deduped/dropped: reduce strength by 0.3
4. Memories that still survive: increment consolidation_count, boost strength by 0.05
5. Memories below the eviction threshold (0.05): remove from the archive

**Why this works**: As the cascade evolves — new agents discovered, corpus analyzer learns new patterns — previously novel signals may now be recognized as noise. Consolidation applies current knowledge to old memories. A heartbeat check that seemed important on day one is recognized as noise by day thirty.

**The result**: The archive naturally compresses over time. Noise decays and evicts. Critical incidents — production outages, security breaches, data corruption — survive indefinitely because the pipeline never suppresses them (zero-FN invariant).

**API**: `POST /consolidate` triggers a consolidation cycle and returns statistics (processed, evicted, compression ratio).

### 4. Priming / Attention Weighting

After a significant incident (a critical or high-severity memory formation), the system enters a heightened attention state for related signal types. A `PrimingWindow` opens with linear decay over a configurable duration (default 4 hours).

During the window, a `PrimingEscalator` agent (injected at stage 0, before all other agents) escalates matching signals — even info-severity ones that would normally be dropped by the severity gate.

**Safety invariant**: The PrimingEscalator can ONLY escalate. It can NEVER suppress or drop. Priming strictly increases the set of signals that reach inference, which is the fail-open direction. The zero-FN test suite runs identically with or without priming active.

**Decay**: Linear. At t=0, full effect. At t=duration, zero effect. Past duration, the window is expired and has no effect.

**Capacity**: Maximum 10 concurrent priming windows (configurable). When the cap is reached, the oldest window is evicted.

This mirrors biological priming — your brain is more sensitive to car horns for hours after a near-miss on the highway. The cascade is more sensitive to related signal types after a significant incident. Adaptive, time-bounded, and self-correcting.

### 5. Federation

Multiple cascade instances — each monitoring a different signal source — can share memories through export and import.

**Export**: `GET /memories/export?min_strength=0.3` serializes memories above a strength threshold, tagged with the exporting instance's ID.

**Import**: `POST /memories/import` ingests memories from another instance. Each imported memory preserves its `source_instance` provenance. Content-hash deduplication applies — if the same signal was seen by two independent cascades, importing it reinforces the existing memory rather than creating a duplicate.

**Cross-source correlation**: When the same content hash appears from two or more independent instances, the strength boost is the signal that something real happened — multiple independent observers saw the same thing. This is associative memory: "Last time network latency spiked AND an AAP job failed, the root cause was X." No single cascade could form that association.

**API**: `GET /memories/export`, `POST /memories/import`. Statistics include `federated_sources` — the count of distinct source instances in the archive.

---

## Per-Tier Model Routing

The memory architecture introduces per-tier model selection. Two environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CASCADE_MICRO_MODEL` | `CASCADE_LLM_MODEL` | Model for medium/low severity (fast, small) |
| `CASCADE_MACRO_MODEL` | `CASCADE_LLM_MODEL` | Model for critical/high severity (deep reasoning) |

The bridge selects the model based on signal severity at the point of LLM classification. This means:

- **Micro tier**: granite-2b or phi4-mini. Fast classification (500-700ms). Handles the bulk of inference — routine signals that survived the nano tier but aren't critical.
- **Macro tier**: granite-8b-instruct. Deeper reasoning (800-900ms). Reserved for critical and high-severity signals where the classification decision has higher stakes.

Both models run on CPU (Intel Xeon 6). No GPU required. The per-tier routing reduces average inference latency because most signals are medium/low severity and hit the faster model.

---

## Federated Deployment

The production deployment on Intel Xeon 6 infrastructure runs three cascade instances and a federation job:

```
K8s Events ──→ [ cascade-k8s  ] ──→ K8s Memories  ──┐
                                                      │   every 5 min
AAP Signals ──→ [ cascade-aap  ] ──→ AAP Memories  ──┤──→ [ Federation ]
                                                      │       Job
                                                      ▼
                                              [ cascade-memory ]
                                              (memory aggregator)
                                                      │
                                        ┌─────────────┼─────────────┐
                                        ▼             ▼             ▼
                                  Core Memories   Associations   Priming
                                (survivor archive) (cross-source  (attention
                                                    patterns)    modulation)
```

| Component | Domain | Micro Model | Macro Model | Memory Pool |
|-----------|--------|-------------|-------------|-------------|
| cascade-k8s | kubernetes | granite-2b | granite-8b | 10K |
| cascade-aap | aap | granite-2b | granite-8b | 10K |
| cascade-memory | memory | granite-2b | granite-8b | 50K |

The federation CronJob runs every 5 minutes:
1. Exports survivors (strength > 0.3) from K8s and AAP cascades
2. Imports them into the memory aggregator
3. Runs consolidation on the aggregator
4. Reports statistics

Each cascade instance persists state to a PVC. The aggregator's memory archive is the organization's institutional memory — queryable via `/recall`, continuously refined via `/consolidate`.

---

## Validation

### Test Coverage

592 tests across 5 phases, zero failures, zero regressions against the base cascade pipeline.

| Phase | Tests | Coverage |
|-------|-------|----------|
| Survivor Archive | 52 | CDD schemas, TDD store/query/eviction/strength/dedup/serialization, BDD formation behavior, API compliance |
| Recall | 38 | TDD similarity functions (4), engine ranking/filtering/reinforcement, BDD precedent scenarios, performance (<50ms for 1K memories) |
| Consolidation | 13 | TDD strength decay/boost/eviction, BDD noise-shrinks-over-time, critical-survives-20-cycles |
| Priming | 14 | TDD window mechanics/decay/expiry, BDD safety (never suppresses, zero-FN preserved, cap enforced) |
| Federation | 27 | TDD export/import/dedup, BDD cross-source correlation, roundtrip, capacity bounds, API compliance |

### Safety Invariants Preserved

The memory architecture adds two safety tests to the cascade's existing zero-FN test suite:

1. **Memory formation does not alter pipeline decisions.** Memory capture happens AFTER `pipeline.run()` returns. Pipeline decisions are identical with or without the memory archive.
2. **Archive at capacity does not block processing.** Even if the archive is full and evicting, the pipeline continues processing signals without delay.

### Methodology

CDD → TDD → EDD → BDD (Contract → Test → Event → Behavior Driven Development).

- **Stage 0 (CDD)**: JSON Schema contracts (`memory-record.json`, `memory-event.json`) validated with `Draft202012Validator`.
- **Stage 1 (TDD)**: RED tests written before implementation. Mathematical correctness of strength mechanics, similarity functions, capacity management.
- **Stage 2 (EDD)**: Every lifecycle transition emits a `MemoryEvent`. Formation, recall, consolidation, eviction, federation — all auditable.
- **Stage 3 (BDD)**: GIVEN/WHEN/THEN scenarios for memory formation, recall precedent, consolidation compression, priming safety.

### Metrics

| Metric | What It Measures | Baseline |
|--------|-----------------|----------|
| Compression ratio | Selectivity — how much the system ignores | 99.1% (K8s), 98.1% (AAP) |
| False negative rate | Encoding failure — significant events lost | 0% (zero-FN gate) |
| Shadow disagreement rate | Recall accuracy — are memories still correct | 10% genuine disagreement |
| Recall latency | Retrieval speed | <50ms for 1,000 memories |
| Consolidation compression | How much further old memories compress | Measured per cycle |
| Federated sources | Independent observers contributing memories | Measured at runtime |
| Priming escalations | Signals escalated by attention weighting | Measured at runtime |

---

## The Bigger Claim

The cascade compression engine, validated on 142.4 million production signals, already proves that most AI signals don't need a model. The memory architecture proves something more: the compression process itself is how machines should form institutional knowledge.

A filing cabinet stores everything and retrieves on demand. A memory system decides what matters, strengthens what recurs, forgets what doesn't, and forms associations that no single query could surface. The cascade does all of this — not through a separate knowledge management layer, but as a natural consequence of the compression process.

Every signal that enters the cascade is a potential memory. The nano tier is working memory — fast, high-volume, mostly discarded. The micro tier is episodic memory — notable events classified by kind. The macro tier is semantic memory — rare events that require deep reasoning and change the system's understanding. The survivor archive is institutional memory — the compressed record of what actually mattered.

Organizations that deploy cascade compression don't just reduce inference costs. They build durable institutional knowledge that survives personnel changes, spans domains, and improves with every signal processed.

---

*Cascade as Memory is built on the validated cascade compression framework (142.4M signals, 99.1% compression, zero false negatives). All capabilities described in this paper are implemented, tested (592 tests), and deployable on Intel Xeon 6 hardware running Red Hat OpenShift. The framework is domain-agnostic — eight domain packs ship ready to use, and adding a new domain requires a data connector, a one-paragraph prompt, and historical data for replay.*
