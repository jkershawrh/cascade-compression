# Cascade Compression

Most AI signals don't need a model. Cascade compression eliminates 85-99% of signal volume with deterministic rules, then classifies the survivors on CPU. No GPU required.

**68.7M live signals processed. 99.5% compression. Zero false negatives. $33K 3-year TCO vs $266K GPU.**

## The Idea

```
10M signals/day → Cascade (rules) → 150K survivors → Small model on CPU → Alerts
                      ↑                                      │
                      └──── Learns from model feedback ──────┘
```

The cascade watches what the model classifies as noise, proposes rules to handle those patterns, validates the rules, and promotes them. After an hour, 96%+ of signals never reach the model again.

## Quick Start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Run the cascade on live K8s data
cascade-run --domain kubernetes --llm-url https://your-llm/v1 --llm-key sk-...

# Replay historical data for benchmarking
cascade-replay --domain finance --data transactions.csv --llm-url https://your-llm/v1

# Run tests (401 tests)
make test-all

# TCO dashboard
make up    # FastAPI on http://localhost:8090
```

## Seven Domains, Zero Framework Changes

| Domain | Source | Compression | Critical Survival |
|--------|--------|:-----------:|:-----------------:|
| **Kubernetes** | Live (68.7M) | **99.5%** | 0 FN |
| **AAP (Ansible)** | Live (1M+) | **96.0%** | 0 FN |
| Financial Services | Synthetic | 61.1% | 92.7% fraud, 100% compliance |
| Healthcare | Synthetic | 91.0% | 96.6% critical, 99.0% compliance |
| Insurance | Synthetic | 81.2% | 100% fraud, 99.8% compliance |
| Retail | Synthetic | 88.3% | 100% shrinkage, 100% compliance |
| Telecom | Synthetic | 94.3% | 92.1% incidents |

Each domain is a "domain pack" — a collector, a one-paragraph prompt, and historical data. The cascade framework stays untouched.

## Three Tiers

**Nano (85-99%)** — Deterministic agents: deduplication, transient suppression, severity gate, pattern matching, learned rules. Sub-millisecond, zero cost.

**Micro (1-15%)** — Small CPU model (granite-8b, phi4-mini) classifies survivors into four buckets: routine_noise, known_pattern, needs_attention, real_incident. ~600ms per classification.

**Self-tuning** — Corpus analyzer discovers patterns in the signal stream, proposes agents, promotion engine validates them against LLM feedback. Agents progress: draft → candidate → nano (activated). No human writes rules.

## Model Leaderboard (20-signal AAP test, Xeon 6 CPU)

| Model | Score | Latency | Dangerous Misses |
|-------|------:|--------:|-----------------:|
| granite-3-2-8b-instruct | 14/20 | 860ms | **0** |
| phi4-mini | 14/20 | 734ms | **0** |
| granite-4.1-3b | 14/20 | 888ms | 3 |
| granite-2b | 13/20 | 677ms | 1 |

granite-8b and phi4-mini: every error is over-escalation (safe failure), never dismissal.

## TCO

| Approach | 3-Year Cost |
|----------|------------:|
| **Cascade on Xeon 6** | **$33K** |
| GPU inference (H100) | $266K |
| Cloud API | $540K |

Cascade footprint: 13 CPU, 13 GB. One Xeon 6 at 10% utilization. Optional governance adds 3.5 CPU if needed for regulated industries.

## Documentation

| Doc | Audience | What |
|-----|----------|------|
| [Architecture](docs/architecture.md) | Both | How the cascade works — executive overview + technical deep-dive |
| [Whitepaper](docs/cascade-compression-whitepaper.md) | Executive | Full story with benchmark proof points and TCO |
| [Model Benchmarks](docs/model-benchmarks.md) | Technical | 6-model comparison, prompt tuning, live cascade stats |
| [Domain Pack Guide](docs/domain-pack-guide.md) | Technical | How to add a new domain in three files |
| [Promotion Guidelines](docs/promotion-guidelines.md) | Technical | How agents are discovered, validated, and promoted |
| [Event Workflow](docs/event-workflow.md) | Technical | Signal lifecycle from ingestion to feedback |

## Package Structure

```
cascade_compression/
  bridge.py          Orchestrator — collector → pipeline → LLM → feedback
  cli.py             cascade-run, cascade-replay entrypoints
  cascade/           Pipeline, agents, promotion, corpus analyzer
  collectors/        7 domain collectors (k8s, aap, finance, healthcare, insurance, retail, telecom)
  domains/           Domain pack configs (prompt, model, collector class)
  routing/           Benchmark-graded model selection (19 models, 5 lanes)
  infra/             Pressure-aware scaler, fleet manager
  tco/               TCO calculator, FastAPI API, FSI scenarios
  integrations/      Immutable ledger client
  metrics/           Precision metric (LLM-vs-LLM audit)
  benchmarks/        Harness, shootouts, synthetic generators
```
