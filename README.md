# Cascade Compression

Self-tuning signal compression for CPU inference at scale. Discovers what is noise in your signal stream, validates it empirically with zero false-negative tolerance, and continuously verifies that the validation still holds.

## The Idea

```
10M signals/day → Cascade (deterministic) → 100K survivors → Small model on CPU → Alerts
                        ↑                                           │
                        └──── Learns from model feedback ───────────┘
                        ↑                                           │
                        └──── Shadow validation re-checks 5% ──────┘
                        ↑                                           │
                        └──── Independent audit (GCL) verifies 1% ─┘
```

The cascade discovers what the LLM classifies as noise, promotes deterministic agents to handle those patterns, and continuously verifies they are still correct. Activated agents expire after 72 hours and must re-qualify against current data. One false negative from any source and the agent is instantly deactivated.

## Quick start

```bash
# Deploy on OpenShift (single container)
oc new-app https://github.com/jkershawrh/cascade-compression \
  -e CASCADE_LLM_URL=https://your-llm/v1 \
  -e CASCADE_LLM_KEY=sk-...

# Or run locally
pip install -e ".[dev]"
cascade-run --domain kubernetes --llm-url https://your-llm/v1 --llm-key sk-...

# Replay historical data
cascade-replay --domain finance --data transactions.csv --llm-url https://your-llm/v1

# Run tests (460+ tests)
make test-all

# Start the service with real-time dashboard
python3 -m uvicorn cascade_compression.service:app --port 8090
```

## Validated results (hardened engine, 2026-08-11)

| Domain | Source | Signals | Compression | Agents | Shadow demotions | GCL |
|--------|--------|---------|:-----------:|:------:|:----------------:|:---:|
| **Kubernetes** | Replay (infra01) | **142.4M** | **99.1%** | 3 | 0 | 1 FAILS |
| **AAP (Ansible)** | Live + replay | **553K** | **98.1%** | — | 63 | clean |

Hardened engine: zero-FN gate, shadow validation (5%), 72h TTL, GCL audit loop. LLM classified 9,685 signals out of 142M (0.007%).

## Synthetic domain benchmarks

Cold-start numbers from synthetic data — no learned agents, no LLM feedback loop.
| Financial Services | Synthetic | 61.1% | 92.7% fraud, 100% compliance |
| Healthcare | Synthetic | 91.0% | 96.6% critical, 99.0% compliance |
| Insurance | Synthetic | 81.2% | 100% fraud, 99.8% compliance |
| Retail | Synthetic | 88.3% | 100% shrinkage, 100% compliance |
| Telecom | Synthetic | 94.3% | 92.1% incidents |
| **Memory** | Live (2,485 claims) | N/A | 22 institutional topics across 63 projects |

Each domain is a "domain pack" — a collector, a one-paragraph prompt, and historical data. The cascade framework stays untouched. The memory domain pack extracts institutional knowledge from agent memory files across any agentic framework.

## Three Tiers

**Nano (85-99%)** — Deterministic agents: deduplication, transient suppression, severity gate, pattern matching, learned rules. Sub-millisecond, zero cost.

**Micro (1-15%)** — Small CPU model (granite-8b, phi4-mini) classifies survivors into four buckets: routine_noise, known_pattern, needs_attention, real_incident. ~600ms per classification.

**Self-tuning** — Corpus analyzer discovers patterns in the signal stream, proposes agents, promotion engine validates them against LLM feedback. Agents progress: draft → candidate → [pending_approval] → nano (activated). No human writes rules.

## Defense in depth

Five layers, none trusting each other:

| Layer | What it does | Trigger |
|-------|-------------|---------|
| **Zero-FN gate** | Agents need 200+ samples with 0% false negatives to activate | Promotion time |
| **Shadow validation** | 5% of suppressed signals re-checked by LLM | Continuous (configurable rate) |
| **GCL audit loop** | Independent system samples 1% of decisions, writes verdicts to immutable ledger | FAILS verdict triggers demotion |
| **72h TTL** | Activated agents expire and must re-qualify against current data | Every 72h (configurable) |
| **Human gate** | Optional approval step before agents activate (for regulated environments) | `CASCADE_HUMAN_GATE=1` |

One false negative from any source → agent demoted to draft, samples zeroed, evidence chain written to immutable ledger.

## Model Leaderboard (20-signal AAP test, Xeon 6 CPU)

| Model | Score | Latency | Dangerous Misses |
|-------|------:|--------:|-----------------:|
| granite-3-2-8b-instruct | 14/20 | 860ms | **0** |
| phi4-mini | 14/20 | 734ms | **0** |
| granite-4.1-3b | 14/20 | 888ms | 3 |
| granite-2b | 13/20 | 677ms | 1 |

granite-8b and phi4-mini: every error is over-escalation (safe failure), never dismissal.

## TCO

The calculator produces workload-specific estimates only when measured throughput exists for every requested model/hardware pair. Unsupported options are reported separately rather than being sized as one unit. Hardware prices and throughput data remain operator-supplied assumptions, not validated cost guarantees.

## Documentation

| Doc | Audience | What |
|-----|----------|------|
| [Architecture](docs/architecture.md) | Both | How the cascade works — executive overview + technical deep-dive |
| [Whitepaper](docs/cascade-compression-whitepaper.md) | Executive | Full story with benchmark proof points and TCO |
| [Model Benchmarks](docs/model-benchmarks.md) | Technical | 6-model comparison, prompt tuning, live cascade stats |
| [Domain Pack Guide](docs/domain-pack-guide.md) | Technical | How to add a new domain in three files |
| [Promotion Guidelines](docs/promotion-guidelines.md) | Technical | How agents are discovered, validated, and promoted |
| [Event Workflow](docs/event-workflow.md) | Technical | Signal lifecycle from ingestion to feedback |

## Package structure

```
Containerfile              Single-container deployment (UBI9 Python 3.11)
deploy/openshift.yaml      OpenShift Deployment + Service + Route
frontend/index.html        Real-time dashboard (polls /stats every 5s)
cascade_compression/
  service.py               Standalone FastAPI service (serves dashboard + API)
  bridge.py                Orchestrator — collector → pipeline → LLM → shadow → feedback
  cli.py                   cascade-run, cascade-replay entrypoints
  cascade/                 Pipeline, agents, promotion (hardened), corpus analyzer
  collectors/              8 domain collectors (k8s, aap, finance, healthcare, insurance, retail, telecom, memory)
  domains/                 8 domain packs (prompt, model, collector class)
  routing/                 Benchmark-graded model selection (19 models, 5 lanes)
  infra/                   Pressure-aware scaler, fleet manager
  tco/                     TCO calculator, FastAPI API, FSI scenarios
  integrations/            Immutable ledger client + promotion event writer
  metrics/                 Precision metric (LLM-vs-LLM audit)
  benchmarks/              Harness, shootouts, synthetic generators
```

## Platform

Pure Python — runs on ARM (Apple Silicon, Graviton, Ampere) and x86 (Xeon, EPYC) with no architecture-specific dependencies. The LLM is a separate service called over HTTP — deploy it on whatever hardware fits (Xeon 6 CPU, GPU, cloud API). The cascade itself doesn't care what serves the model.
