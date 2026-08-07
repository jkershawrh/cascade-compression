# Cascade Compression

Three-tier signal compression engine for CPU inference. Most AI signals don't need a model — the cascade proves it, then routes the survivors to the right model on the right hardware.

Standalone framework with CLI, 7 domain packs, and a benchmark harness. Proves Xeon 6 TCO beats GPU when cascade compression reduces effective inference volume by 15-20x.

## How It Works

```
Signals ─→ Cascade Pipeline ─→ Routing Engine ─→ CPU Inference
              │                      │                │
         Nano (85%+)           Strategy +         phi4-mini
         Rules only            Corpora            granite-8b
         Zero cost             5 lanes            granite-2b
              │                      │                │
         Micro (10-12%)        Bootstrapper       gemma3-4b
         Small CPU models      Workload ID        smollm2-360m
              │                      │
         Macro (3-5%)          Fleet Manager
         Larger models         Pressure Scaler
```

**Nano tier** — deterministic agents (dedup, transient suppression, severity gate, pattern/threshold classifiers). Handles 85%+ of signals at sub-millisecond latency with zero inference cost.

**Micro tier** — small CPU models (360M-3B params) classify the survivors. Classification, extraction, embedding lanes.

**Macro tier** — larger CPU models (3.8B-8B params) for generation and reasoning. Only the genuinely hard cases.

## 7 Domain Packs

The cascade framework processes `Signal` objects — it doesn't care where they come from. Domain packs provide the adapter: a collector, a prompt, and a signal mapping. The cascade pipeline, agents, promotion engine, and routing corpora stay untouched.

| Domain | Collector | Compression | Key Metric | Source |
|--------|-----------|-------------|------------|--------|
| Kubernetes | `KubernetesCollector` | 72.9% | 37.3% noise rate, 0 FN | Live (infra01) |
| AAP (Ansible) | `AAPCollector` | 96.0% | 0 FN | Live (infra01/prod0) |
| Finance | `FinanceCollector` | 61.1% | 92.7% fraud survival, 100% compliance | Synthetic |
| Healthcare | `HealthcareCollector` | 91.0% | 96.6% critical, 99.0% compliance | Synthetic |
| Insurance | `InsuranceCollector` | 81.2% | 100% fraud, 99.8% compliance | Synthetic |
| Retail | `RetailCollector` | 88.3% | 100% shrinkage, 100% compliance | Synthetic |
| Telecom | `TelecomCollector` | 94.3% | 92.1% incidents, 80.7% compliance | Synthetic |

## Quick Start

```bash
# Install
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Test
make test-all          # 382 tests across cascade, routing, infra, TCO, domains

# Run via CLI
cascade-run --domain aap --llm-url https://maas/v1 --llm-key sk-...
cascade-replay --domain finance --data transactions.csv --llm-url https://maas/v1

# Run TCO dashboard
make up                # FastAPI on http://localhost:8090
```

## CLI

Two entrypoints installed via the package:

- **`cascade-run`** — live mode. Connects a collector to a data source, runs the cascade pipeline, forwards survivors to an LLM.
- **`cascade-replay`** — replay mode. Feeds historical/synthetic data through the cascade for benchmarking and bootstrapping.

Both accept `--domain`, `--llm-url`, `--llm-model`, `--ledger-url`, and `--state-file` flags.

## Test Suites

```bash
make test-cascade      # Pipeline, safety, promotion (nano agents)
make test-routing      # Corpora, strategies, bootstrapper, task mapping
make test-infra        # Pressure scaler, fleet manager
make test-tco          # Contracts, calculations, scenarios, API
make test-all          # All 382 tests
```

## Package Structure

```
cascade_compression/
  cascade/           Pipeline, agents, promotion, corpus analyzer
  routing/           Benchmark-graded corpora, strategy router, bootstrapper
  infra/             Pressure-aware scaler, fleet manager
  tco/               TCO calculator, FastAPI API, FSI scenarios
  collectors/        Base + 7 domain collectors (k8s, aap, finance, healthcare, insurance, retail, telecom)
  domains/           Domain pack configs (prompt, model, collector class per domain)
  integrations/      Immutable ledger client (optional)
  metrics/           Precision metric (FN/FP tracking)
  benchmarks/        Harness, shootouts, synthetic generators (finance, healthcare, insurance, retail, telecom)
  bridge.py          Standalone CascadeBridge — collector -> pipeline -> LLM
  cli.py             cascade-run, cascade-replay entrypoints

config/              Strategies, verticals, workload profiles, scaler thresholds
data/                Hardware profiles, workload profiles, benchmark matrix
contracts/           OpenAPI spec, JSON schemas
frontend/            Single-page TCO dashboard
```

## Benchmark Data

`benchmarks/results/` contains 30+ JSON files from live runs on Oberon (Intel Xeon 6767P, 128 cores) and racmaas:

- 18-model sweep across 6 industry verticals
- Model shootout (13 models, 20 real classification tasks)
- 4-hour soak tests (5 RPS sustained, drift detection)
- Five-lane simulation with routing
- K8s live cascade: 3.3M signals, 72.9% compression, tuned granite prompt
- AAP live cascade: 441K signals, 96% compression, 0 FN
- Precision metric: 100% (30/30 "important" signals confirmed)

### Model Leaderboard (20-signal AAP test, CPU)

| Model | Score | Latency | Dangerous | Platform |
|-------|-------|---------|-----------|----------|
| granite-3-2-8b-instruct-cpu | 14/20 | 860ms | 0 | racmaas |
| phi4-mini | 14/20 | 734ms | 0 | Oberon |
| granite-4.1-3b | 14/20 | 888ms | 3 | Oberon |
| granite-2b-cpu | 13/20 | 677ms | 1 | racmaas |

## Hardware Reference

| Platform | Cost | Power | Role |
|----------|------|-------|------|
| Xeon 6 server | ~$30K | ~1200W | Cascade + micro/macro inference |
| H100 GPU | ~$50K/card | ~6kW/card | Baseline comparison |
| Cloud API | Per-token | N/A | Frontier + economy tiers |

Full system footprint: 16.5 CPU, 15.8 GB including governance (GCL + ledger). One Xeon 6 at 13% utilization. 3-year TCO: $33K vs $266K GPU vs $540K cloud API.

## Known Gaps

- **Placeholder throughput**: some model throughput numbers are estimated. Replace with RHAIIS 3.5 benchmarks when available.
- **No PUE multiplier**: power calculations don't include cooling overhead (typically 1.3-1.5x).
- **Telecom compliance**: 80.7% — needs prompt tuning to match other domains.

## Next Steps

- Whitepaper with benchmark proof points
- Constrained decoding to rescue 0% models (they know the answer, can't format it)
- RHAIIS 3.5 throughput benchmarks to replace estimates
