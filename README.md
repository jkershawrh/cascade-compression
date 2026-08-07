# Cascade Compression

Three-tier signal compression engine for CPU inference. Most AI signals don't need a model — the cascade proves it, then routes the survivors to the right model on the right hardware.

Built for Intel FSI engagements. Proves Xeon 6 TCO beats GPU when cascade compression reduces effective inference volume by 15-20x.

## How It Works

```
Signals ─→ Cascade Pipeline ─→ Routing Engine ─→ CPU Inference
              │                      │                │
         Nano (85%+)           Strategy +         phi4-mini
         Rules only            Corpora            gemma3-4b
         Zero cost             5 lanes            smollm2-360m
              │                      │                │
         Micro (10-12%)        Bootstrapper       llama32-1b
         Small CPU models      Workload ID        granite-2b
              │                      │
         Macro (3-5%)          Fleet Manager
         Larger models         Pressure Scaler
```

**Nano tier** — deterministic agents (dedup, transient suppression, severity gate, pattern/threshold classifiers). Handles 85%+ of signals at sub-millisecond latency with zero inference cost.

**Micro tier** — small CPU models (360M-3B params) classify the survivors. Classification, extraction, embedding lanes.

**Macro tier** — larger CPU models (3.8B-8B params) for generation and reasoning. Only the genuinely hard cases.

## Domain Agnostic

The cascade framework processes `Signal` objects — it doesn't care where they come from. Domain packs provide the adapter:

| Domain | Collector | Signal Types | Tested On |
|--------|-----------|-------------|-----------|
| Kubernetes | `KubernetesConnector` | pod status, events, node health | infra01 (513K+ signals, 74% compression) |
| AAP (Ansible) | `AAPCollector` | job events, task results, config changes | infra01/prod0 (894K signals, 99.9% compression) |
| FSI | TCO scenarios | dispute, fraud, compliance, mortgage | Simulated (4 scenarios) |

Each domain pack is a collector + a phi-4 prompt + a signal mapping. The cascade pipeline, agents, promotion engine, and routing corpora stay untouched.

## Quick Start

```bash
# Install
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Test
make test-all          # 349 tests across cascade, routing, infra, TCO

# Run
make up                # FastAPI on http://localhost:8090
```

## Test Suites

```bash
make test-cascade      # Pipeline, safety, promotion (nano agents)
make test-routing      # Corpora, strategies, bootstrapper, task mapping
make test-infra        # Pressure scaler, fleet manager
make test-tco          # Contracts, calculations, scenarios, API
```

## Package Structure

```
cascade_compression/
  cascade/          # Signal pipeline, agents, promotion, corpus analyzer
  routing/          # Benchmark-graded corpora, strategy router, bootstrapper
  infra/            # Pressure-aware scaler, fleet manager
  tco/              # TCO calculator, FastAPI API, FSI scenarios
  benchmarks/       # Harness, shootouts, soak tests, metrics

config/             # Strategies, verticals, workload profiles, scaler thresholds
data/               # Hardware profiles, workload profiles, benchmark matrix
benchmarks/         # Configs, levers, workloads, k8s manifests, results
contracts/          # OpenAPI spec, JSON schemas
frontend/           # Single-page TCO dashboard
```

## Benchmark Data

`benchmarks/results/` contains 30+ JSON files from live runs on Oberon (Intel Xeon 6767P, 128 cores):

- 18-model sweep across 6 industry verticals
- Model shootout (13 models, 20 real classification tasks)
- 4-hour soak tests (5 RPS sustained, drift detection)
- Five-lane simulation with routing
- Cascade K8s test: 68.7M signals, 99.47% compression, 23 activated nano agents

## Hardware Reference

| Platform | Cost | Power | Role |
|----------|------|-------|------|
| Xeon 6 server | ~$30K | ~1200W | Cascade + micro/macro inference |
| H100 GPU | ~$50K/card | ~6kW/card | Baseline comparison |
| Cloud API | Per-token | N/A | Frontier + economy tiers |

## Known Gaps

- **Self-reported FN**: cascade checks its own false negatives. Independent audit via GCL + immutable ledger is planned but not wired yet.
- **Placeholder throughput**: some model throughput numbers are estimated. Replace with RHAIIS 3.5 benchmarks when available.
- **No PUE multiplier**: power calculations don't include cooling overhead (typically 1.3-1.5x).

## Next Steps

- Immutable ledger + GCL audit loop for independent FN verification
- AAP signal enrichment (task-level events, activity stream correlation)
- Whitepaper with benchmark proof points
- Constrained decoding to rescue 0% models (they know the answer, can't format it)
