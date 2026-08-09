# Architecture

## System Overview

Cascade compression is a three-tier signal processing framework that reduces inference volume by eliminating noise before it reaches an LLM. The framework is domain-agnostic — domain-specific adapters (collectors, prompts, signal mappings) plug into a generic pipeline.

```
                                DOMAIN PACK
                         ┌────────────────────-─┐
                         │  Collector           │
                         │  (reads data source) │
                         └──────────┬───────────┘
                                    │
                              Raw Signals
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────┐
│                        CASCADE FRAMEWORK                          │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                    NANO TIER (85-99%)                       │  │
│  │                                                             │  │
│  │  Stage 1: Noise Elimination          Stage 2: Pattern       │  │
│  │  ┌──────────────┐                    ┌──────────────────┐   │  │
│  │  │ Deduplicator │ content hash,      │ Pattern          │   │  │
│  │  │              │ 60s window         │ Classifier       │   │  │
│  │  ├──────────────┤                    │ (7 regex)        │   │  │
│  │  │ Transient    │ type+severity      ├──────────────────┤   │  │
│  │  │ Suppressor   │ filter, fail-open  │ Threshold        │   │  │
│  │  ├──────────────┤                    │ Classifier       │   │  │
│  │  │ Severity     │ drops info unless  │ (CPU/mem/disk)   │   │  │
│  │  │ Gate         │ escalation match   └──────────────────┘   │  │
│  │  └──────────────┘                                           │  │
│  │                                                             │  │
│  │  Stage 3: Learned Agents (discovered at runtime)            │  │
│  │  ┌────────────────────┐  ┌─────────────────────┐            │  │ 
│  │  │ Repeat Flood       │  │ Dominant Noise      │            │  │
│  │  │ Suppressor         │  │ Suppressor          │            │  │
│  │  └────────────────────┘  └─────────────────────┘            │  │
│  └─────────────────────────────┬───────────────────────────────┘  │
│                                │                                  │
│                          Survivors (1-15%)                        │
│                                │                                  │
│  ┌─────────────────────────────▼───────────────────────────────┐  │
│  │                   MICRO TIER (10-12%)                       │  │
│  │                                                             │  │
│  │  LLM Classification (granite-8b / phi4-mini on CPU)         │  │
│  │  ┌──────────────────────────────────────────────┐           │  │
│  │  │ routine_noise | known_pattern |              │           │  │
│  │  │ needs_attention | real_incident              │           │  │
│  │  └──────────────────────────────────────────────┘           │  │
│  │                                                             │  │
│  │  Five Inference Lanes:                                      │  │
│  │  Classification | Extraction | Generation | Reasoning |     │  │
│  │  Embedding                                                  │  │
│  └─────────────────────────────┬───────────────────────────────┘  │
│                                │                                  │
│                     Important signals only                        │
│                                │                                  │
│  ┌─────────────────────────────▼───────────────────────────────┐  │
│  │                   MACRO TIER (3-5%)                         │  │
│  │                                                             │  │
│  │  Larger models for complex reasoning                        │  │
│  │  (granite-8b, mistral-7b, phi4-full-14b)                    │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  SELF-TUNING                                                │  │
│  │                                                             │  │
│  │  Corpus Analyzer ──→ discovers patterns ──→ proposes agents │  │
│  │  Promotion Engine ──→ validates agents ──→ promotes/demotes │  │
│  │  LLM feedback ──→ confirms noise types ──→ activates agents │  │
│  └─────────────────────────────────────────────────────────────┘  │ 
└───────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │   GOVERNANCE (opt.)   │
                    │                       │
                    │  Immutable Ledger     │
                    │  (append-only, hash-  │
                    │   chained decisions)  │
                    │                       │
                    │  Audit Loop (GCL)     │
                    │  (1% sample, LLM      │
                    │   adversary probe)    │
                    │                       │
                    │  Precision Metric     │
                    │  (metrics/            │
                    │   precision.py)       │
                    └───────────────────────┘
```

## Component Map

```
cascade_compression/
├── cascade/              NANO TIER — signal pipeline
│   ├── protocol.py       Signal, CascadeDecision, Outcome, CascadeAgent interface
│   ├── pipeline.py       CascadePipeline — runs agents in stage order
│   ├── agents.py         5 built-in agents (dedup, transient, severity, pattern, threshold)
│   ├── dynamic_agents.py RepeatFloodSuppressor, DominantNoiseSuppressor (runtime-discovered)
│   ├── router.py         CascadeRouter — routes survivors by tier and lane
│   ├── promotion.py      PromotionEngine — validates and promotes/demotes agents
│   ├── corpus_analyzer.py CorpusAnalyzer — discovers patterns in signal streams
│   └── service.py        FastAPI cascade service (sits in front of model services)
│
├── collectors/           DATA SOURCE ADAPTERS
│   ├── base.py           BaseCollector ABC (connect, collect, collect_all)
│   ├── kubernetes.py     K8s API — pods, events, nodes
│   ├── aap.py            AAP DB — jobs, task events, activity stream
│   ├── finance.py        Transactions, fraud signals, compliance events
│   ├── healthcare.py     Patient alerts, clinical events, compliance
│   ├── insurance.py      Claims, fraud indicators, policy events
│   ├── retail.py         POS transactions, shrinkage, inventory
│   └── telecom.py        Network events, incidents, SLA metrics
│
├── domains/              DOMAIN PACK CONFIGS
│   ├── kubernetes.py     K8s prompt, model, collector class
│   ├── aap.py            AAP prompt, model, collector class
│   ├── finance.py        Finance prompt, model, collector class
│   ├── healthcare.py     Healthcare prompt, model, collector class
│   ├── insurance.py      Insurance prompt, model, collector class
│   ├── retail.py         Retail prompt, model, collector class
│   └── telecom.py        Telecom prompt, model, collector class
│
├── routing/              MODEL SELECTION — benchmark-graded
│   ├── corpora.py        RoutingCorpora — 19 models, 5 lanes, 6 industries, fallback chains
│   ├── strategy_router.py StrategyRouter — 10 optimization profiles
│   ├── bootstrapper.py   WorkloadBootstrapper — cosine similarity workload classification
│   ├── task_mapping.py   Task type resolution (14 deepfield types → 7 benchmark shapes)
│   ├── models.py         RoutingDecision audit trail
│   └── compile_corpora.py Benchmark results → corpora.json compiler
│
├── infra/                INFRASTRUCTURE — pressure-aware
│   ├── scaler.py         InferenceScaler — Linux PSI + cgroup v2, green/yellow/red rubric
│   └── fleet_manager.py  FleetManager — deployment planning, replica allocation
│
├── tco/                  TCO CALCULATOR — cost comparison
│   ├── calculator.py     Cascade math, hardware TCO, cloud TCO
│   ├── models.py         Pydantic models (WorkloadProfile, TCOResult, etc.)
│   ├── scenarios.py      4 pre-built FSI scenarios
│   └── api.py            FastAPI on port 8090
│
├── integrations/         EXTERNAL SYSTEMS
│   └── ledger.py         Immutable ledger client (hash-chained decision log)
│
├── metrics/              QUALITY METRICS
│   └── precision.py      Precision metric — FN/FP tracking across domains
│
├── benchmarks/           BENCHMARK HARNESS
│   ├── harness.py        9 optimization levers, async benchmark runner
│   ├── metrics.py        SampleResult, AggregatedMetrics (p50/p95/p99)
│   ├── rubric.py         Red/yellow/green matrix evaluator
│   ├── industry_prompts.py ISO 20022, TMF621, ACORD, GS1, HL7 FHIR prompts
│   ├── synthetic_finance.py    Synthetic signal generator for finance
│   ├── synthetic_healthcare.py Synthetic signal generator for healthcare
│   ├── synthetic_insurance.py  Synthetic signal generator for insurance
│   ├── synthetic_retail.py     Synthetic signal generator for retail
│   └── synthetic_telecom.py    Synthetic signal generator for telecom
│
├── bridge.py             STANDALONE BRIDGE — collector → pipeline → LLM
└── cli.py                CLI ENTRYPOINTS — cascade-run, cascade-replay
```

## Data Flow

```
Signal Source (K8s API, AAP DB, transaction feed, etc.)
       │
       ▼
   Collector (domain-specific)
       │ maps to Signal(signal_id, signal_type, severity, source, content, labels, namespace, cluster)
       ▼
   CascadePipeline.run(signals)
       │
       ├── Stage 1 agents: DeduplicateAgent → TransientSuppressor → SeverityGate
       │   Each agent returns CascadeDecision(outcome=DROP/SUPPRESS/KEEP/ESCALATE)
       │   Signals with DROP/SUPPRESS/DEDUPE outcomes are removed
       │
       ├── Stage 2 agents: PatternClassifier → ThresholdClassifier
       │   Surviving signals get classified/tagged
       │
       ├── Stage 3 agents: (dynamic, discovered at runtime)
       │   RepeatFloodSuppressor, DominantNoiseSuppressor
       │
       ▼
   CascadeResult
       │ survivors: signals that passed all agents
       │ decisions: full audit log of every agent decision
       │ compression_ratio: % of signals handled without LLM
       │
       ▼
   CascadeRouter.route(survivors)
       │ tier: severity → micro (medium/low) or macro (high/critical)
       │ lane: task_type → classification/extraction/generation/reasoning/embedding
       │
       ▼
   LLM Classification (granite / phi4-mini on CPU)
       │ routine_noise | known_pattern | needs_attention | real_incident
       │
       ▼
   Feedback Loop
       │ LLM says "noise" → CorpusAnalyzer proposes agent
       │ CorpusAnalyzer validates → PromotionEngine promotes
       │ Next time: nano tier handles it, LLM never sees it
```

## Multi-Domain Deployment

```
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐
│ K8s     │ │ AAP     │ │ Finance │ │Healthcare│ │Insurance │ │ Retail  │ │ Telecom │
│Collector│ │Collector│ │Collector│ │Collector │ │Collector │ │Collector│ │Collector│
└────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘ └────┬─────┘ └────┬────┘ └────┬────┘
     │           │           │           │            │            │           │
     ▼           ▼           ▼           ▼            ▼            ▼           ▼
┌────────────────────────────────────────────────────────────────────────────────────┐
│                              CascadeBridge (bridge.py)                             │
│    Each domain gets its own pipeline instance, agents, state, and LLM prompt       │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │
                              ┌─────────┴─────────┐
                              ▼                   ▼
                      Immutable Ledger     Precision Metrics
                      (decision audit)     (FN/FP tracking)
```

The CLI (`cascade-run`, `cascade-replay`) wires this automatically from `--domain` flag. Each cascade instance is fully isolated — own agents, own state, own LLM prompt. The framework code is shared. Domain packs provide:

1. **Collector** (`collectors/`) — reads the data source, maps to Signal protocol via `BaseCollector`
2. **Domain config** (`domains/`) — prompt, model name, collector class
3. **Data** — historical signals for replay, or synthetic generators (`benchmarks/synthetic_*.py`)

## Resource Footprint

| Component | CPU | RAM | Role |
|-----------|-----|-----|------|
| Cascade engine | 4 | 4 GB | Pipeline, agents, promotion, LLM client |
| Postgres | 1 | 1 GB | Signal store, agent state |
| Granite-8b (micro) | 8 | 8 GB | LLM classification, 0 dangerous misses |
| **Cascade total** | **13** | **13 GB** | |
| GCL (governance) | 1 | 1 GB | Hypothesis falsification, audit |
| Immutable Ledger | 1.5 | 1.8 GB | Hash-chained decision log |
| Ledger Postgres | 1 | 1 GB | Ledger storage |
| **Full system** | **16.5** | **15.8 GB** | |

Fits on a single Xeon 6 server (128 cores, 512 GB) at 13% utilization.
