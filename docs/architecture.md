# Architecture

## Executive Summary

Cascade compression is a three-tier signal processing framework. Most enterprise AI signals are routine noise — the cascade eliminates them with deterministic rules before anything touches an LLM. The 1-15% that survive get classified by a small CPU-hosted model. No GPU required.

> **Historical document:** The benchmark figures below describe preserved experiments, not current release guarantees. Raw artifacts contain non-zero false-negative results. Validate safety and measured model/hardware throughput before using compression or TCO figures operationally.

---

## How It Works (Non-Technical)

```
Raw signals (millions/day)
    │
    ▼
┌─────────────────────────────┐
│  RULES (85-99% handled)     │  Deduplication, severity filtering,
│  Zero inference cost        │  pattern matching, learned rules
│  Sub-millisecond            │  No model involved
└──────────────┬──────────────┘
               │ 1-15% survive
               ▼
┌─────────────────────────────┐
│  SMALL MODEL ON CPU         │  IBM Granite 8B or phi4-mini
│  ~600ms per classification  │  4 buckets: noise, known, attention, incident
│  Runs on standard servers   │  No GPU hardware needed
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│  SELF-TUNING                │  Watches what the model classifies as noise
│  Discovers new rules        │  Promotes them automatically
│  Cascade gets smarter       │  Zero human intervention
└─────────────────────────────┘
```

**The cost story:** If 90% of signals never reach a model, you don't need GPU infrastructure to handle them. A single Intel Xeon 6 server runs the entire system at 10% utilization.

---

## Technical Architecture

### Signal Protocol

Everything flows through a generic `Signal` object:

```python
Signal(
    signal_id,      # UUID
    signal_type,    # domain-specific (e.g. "pod_crashloop", "wire_transfer")
    severity,       # info | low | medium | high | critical
    source,         # resource that generated the signal
    content,        # dict — arbitrary evidence payload
    labels,         # dict — metadata for routing
    namespace,      # grouping identifier
    cluster,        # source system identifier
)
```

The framework doesn't know what domain produced the signal. Domain-specific knowledge lives in three pluggable components: collector, prompt, and signal mapping.

### Three-Tier Pipeline

```
┌───────────────────────────────────────────────────────────────────┐
│                        CASCADE PIPELINE                           │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                    NANO TIER (85-99%)                       │  │
│  │                                                             │  │
│  │  Stage 1: Noise Elimination                                 │  │
│  │  ┌──────────────┐ ┌───────────────┐ ┌──────────────┐       │  │
│  │  │ Deduplicator │ │   Transient   │ │   Severity   │       │  │
│  │  │ SHA256 hash  │ │  Suppressor   │ │     Gate     │       │  │
│  │  │  60s window  │ │  fail-open    │ │ drops info   │       │  │
│  │  └──────────────┘ └───────────────┘ └──────────────┘       │  │
│  │                                                             │  │
│  │  Stage 2: Pattern Classification                            │  │
│  │  ┌──────────────────┐ ┌───────────────────┐                │  │
│  │  │ Pattern (7 regex)│ │ Threshold (CPU/   │                │  │
│  │  │ OOM, disk, auth  │ │  mem/disk limits) │                │  │
│  │  └──────────────────┘ └───────────────────┘                │  │
│  │                                                             │  │
│  │  Stage 3: Learned Agents (discovered at runtime)            │  │
│  │  ┌────────────────────┐ ┌──────────────────────┐           │  │
│  │  │  Repeat Flood      │ │  Dominant Noise      │           │  │
│  │  │  Suppressor        │ │  Suppressor          │           │  │
│  │  └────────────────────┘ └──────────────────────┘           │  │
│  └─────────────────────────────┬───────────────────────────────┘  │
│                                │                                  │
│                          Survivors (1-15%)                        │
│                                │                                  │
│  ┌─────────────────────────────▼───────────────────────────────┐  │
│  │                   MICRO TIER                                │  │
│  │                                                             │  │
│  │  LLM Classification (granite-8b / phi4-mini on CPU)         │  │
│  │  ┌──────────────────────────────────────────────┐           │  │
│  │  │ routine_noise | known_pattern |              │           │  │
│  │  │ needs_attention | real_incident              │           │  │
│  │  └──────────────────────────────────────────────┘           │  │
│  └─────────────────────────────┬───────────────────────────────┘  │
│                                │                                  │
│  ┌─────────────────────────────▼───────────────────────────────┐  │
│  │  SELF-TUNING ENGINE                                         │  │
│  │                                                             │  │
│  │  Corpus Analyzer ──→ discovers patterns ──→ proposes agents │  │
│  │  Promotion Engine ──→ validates agents ──→ promotes/demotes │  │
│  │  LLM feedback ──→ confirms noise types ──→ activates agents │  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

### CascadeBridge — The Orchestrator

`CascadeBridge` is the main entry point. It wires everything together:

1. Receives signals from a collector
2. Converts them to the `Signal` protocol
3. Runs them through `CascadePipeline` (nano tier)
4. Buffers survivors and classifies them via LLM (micro tier)
5. Feeds LLM results back to `CorpusAnalyzer` (self-tuning)
6. Runs `PromotionEngine` periodically to promote/demote agents
7. Optionally writes decisions to the immutable ledger

State (activated agents, metrics) persists to a JSON file and restores on restart.

### Self-Tuning: How Agents Are Discovered

The cascade discovers its own rules. No human writes agents.

1. **CorpusAnalyzer** watches the signal stream (10K-signal buffer) and detects three pattern types:
   - **Repeat floods** — same signal type appears N+ times within a time window
   - **Dominant types** — one signal type is >5% of total volume
   - **Mono-severity** — a signal type always appears at the same severity

2. For each pattern, it proposes a draft agent (a `RuleAgent` with configurable conditions).

3. The **PromotionEngine** validates the agent against incoming signals using LLM classifications as ground truth. Agents progress through five tiers:

   ```
   draft → candidate (50+ samples, 60% accuracy)
         → nano (200+ samples, 75% accuracy) ← ACTIVATED
         → micro (500+ samples, human reviewed)
         → macro (1000+ samples, terminal)
   ```

4. Once at nano tier, the agent processes signals in Stage 3. The LLM never sees matching signals again. Compression ratio increases.

5. If accuracy degrades, the agent is automatically demoted. Safety invariant: over-escalate (safe) rather than under-escalate (dangerous).

### Domain Packs

Adding a new domain requires three things, zero framework changes:

| Component | What | Where |
|-----------|------|-------|
| **Collector** | Reads data source, maps to Signal | `collectors/your_domain.py` |
| **Prompt** | One paragraph telling the LLM what the buckets mean | `domains/your_domain.py` |
| **Data** | Historical signals for replay bootstrapping | CSV, DB, or synthetic generator |

Seven domains validated:

| Domain | Source | Peak Compression | Critical Survival |
|--------|--------|----------------:|------------------:|
| Kubernetes | Live (68.7M signals) | 99.5% | 0 FN |
| AAP (Ansible) | Live (1M+ signals) | 96.0% | 0 FN |
| Financial Services | Synthetic | 61.1% | 92.7% fraud, 100% compliance |
| Healthcare | Synthetic | 91.0% | 96.6% critical, 99.0% compliance |
| Insurance | Synthetic | 81.2% | 100% fraud, 99.8% compliance |
| Retail | Synthetic | 88.3% | 100% shrinkage, 100% compliance |
| Telecom | Synthetic | 94.3% | 92.1% incidents |

### Resource Footprint

| Component | CPU | RAM | Role |
|-----------|-----|-----|------|
| Cascade engine | 4 | 4 GB | Pipeline, agents, promotion, LLM client |
| Postgres | 1 | 1 GB | Signal store, agent state |
| Granite-8b (micro) | 8 | 8 GB | LLM classification |
| **Total** | **13** | **13 GB** | |

On a single Xeon 6 server (128 cores, 512 GB): **10% utilization.**

### Governance (If Asked)

For regulated industries, the cascade optionally integrates with an immutable ledger (hash-chained decision log) and an independent audit loop that samples drops and challenges them with an LLM probe. Details in [gcl-ledger-integration-plan.md](gcl-ledger-integration-plan.md). Adds 3.5 CPU / 2.8 GB.

### Component Map

```
cascade_compression/
├── bridge.py             Orchestrator — collector → pipeline → LLM → feedback
├── cli.py                cascade-run (live), cascade-replay (historical)
├── cascade/              NANO TIER
│   ├── protocol.py       Signal, CascadeDecision, Outcome, CascadeAgent
│   ├── pipeline.py       CascadePipeline — runs agents in stage order
│   ├── agents.py         5 built-in agents (dedup, transient, severity, pattern, threshold)
│   ├── dynamic_agents.py RepeatFloodSuppressor, DominantNoiseSuppressor
│   ├── promotion.py      5-tier promotion engine with rubric matrix
│   ├── corpus_analyzer.py Pattern discovery (floods, dominant types, mono-severity)
│   ├── router.py         Routes survivors by tier and lane
│   └── service.py        FastAPI cascade service
├── collectors/           7 domain collectors (k8s, aap, finance, healthcare, insurance, retail, telecom)
├── domains/              Domain configs (prompt, model, collector class per domain)
├── routing/              Benchmark-graded model selection (19 models, 5 lanes, 6 industries)
├── infra/                Pressure-aware scaler, fleet manager
├── tco/                  TCO calculator, FastAPI API, FSI scenarios
├── integrations/         Immutable ledger client
├── metrics/              Precision metric (LLM-vs-LLM audit)
└── benchmarks/           Harness, shootouts, synthetic generators
```

### Data Flow (Technical)

```
Collector.collect()
    │ returns List[Signal]
    ▼
CascadeBridge.process(signals)
    │
    ├── CascadePipeline.run(signals)
    │   ├── Stage 1: DeduplicateAgent → TransientSuppressor → SeverityGate
    │   ├── Stage 2: PatternClassifier → ThresholdClassifier
    │   └── Stage 3: [RepeatFloodSuppressor, DominantNoiseSuppressor] (if activated)
    │   └── Returns CascadeResult(survivors, decisions, compression_ratio)
    │
    ├── LLM classification (survivors only)
    │   POST /v1/chat/completions → routine_noise | known_pattern | needs_attention | real_incident
    │
    ├── CorpusAnalyzer.analyze() → discovers patterns → proposes draft agents
    │
    ├── PromotionEngine.evaluate() → promotes/demotes agents based on LLM feedback
    │
    └── LedgerClient.write_decisions() → POST /api/receipts (optional, fire-and-forget)
```

### Key Design Decisions

- **Agents are stateless per-batch.** Each `process()` call gets the full signal list. Agent state (dedup windows, flood counts) lives in the agent instance, not the pipeline.
- **LLM is the backstop, not the primary.** If no agent handles a signal, it passes through to the LLM. The cascade can only reduce LLM load, never increase it.
- **Fire-and-forget ledger writes.** Ledger failures are logged but never block the pipeline. The cascade's job is signal processing, not governance.
- **Terse prompts outperform detailed ones.** By the time signals reach the LLM, the cascade has eliminated 85-99% of noise. The remaining signals are ambiguous edge cases where general knowledge beats domain instructions.
- **Temperature=0, always.** All models are 100% deterministic. Three consecutive runs produce identical output.
