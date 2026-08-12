# CLAUDE.md — Cascade Compression

## What This Is

Three-tier cascade compression engine for CPU inference at scale.
Consolidates the cascade pipeline, routing engine, TCO calculator, and benchmark harness
into a single repo. Built for Intel FSI engagements — proves that most AI signals
don't need a GPU.

## Architecture

**Cascade Pipeline** (`cascade_compression/cascade/`)
- Nano tier (85-99%): Deterministic agents — dedup, transient suppression, severity gate, pattern/threshold classifiers
- Micro tier (1-15%): Small CPU models (360M-3B), classification/extraction lanes
- Macro tier (<1%): Larger CPU models (3.8B-8B), generation/reasoning lanes
- Signal protocol is domain-agnostic — K8s events, AAP job runs, or any structured signal
- Hardened promotion engine: zero-FN gate, instant demotion, cooling-off, PromotionEvent provenance
- Shadow validation: 5% of suppressed signals re-checked by LLM, disagreement triggers demotion
- Time-bounded activation: 72h TTL, agents expire and must re-qualify
- GCL verdict polling: FAILS verdicts trigger automatic demotion
- Optional human gate: pending_approval state for regulated environments

**Routing Engine** (`cascade_compression/routing/`)
- Benchmark-graded model selection via corpora (19 models, 6 industries, 5 lanes)
- Strategy router with 10 optimization profiles
- Workload bootstrapper (cosine similarity classification)
- Task mapping (14 deepfield types → 7 benchmark shapes)

**Infrastructure** (`cascade_compression/infra/`)
- Pressure-aware scaler (Linux PSI + cgroup v2, green/yellow/red rubric)
- Fleet manager (deployment planning, replica allocation, memory budgeting)

**TCO Calculator** (`cascade_compression/tco/`)
- Xeon 6 vs H100 vs Cloud API cost comparison
- Cascade compression math — proves 15-20x effective volume reduction
- FastAPI on port 8090 with frontend dashboard

**Benchmark Harness** (`cascade_compression/benchmarks/`)
- 9 optimization levers tested on Oberon (Xeon 6767P, 128c)
- Model shootout (13+ models), soak tests, fleet patterns
- Industry-standard prompts (ISO 20022, TMF621, ACORD, GS1, HL7 FHIR)

## Running Tests

```bash
make test-cascade      # Cascade pipeline + safety + promotion
make test-routing      # Corpora, strategies, bootstrapper, routing
make test-infra        # Scaler, fleet manager
make test-tco          # TCO contracts, calculations, scenarios, API
make test-all          # Everything
```

## Running the App

```bash
make up                # FastAPI on port 8090
```

## Key Data

- `config/` — Strategies, verticals, workload profiles, scaler thresholds, lane prompts
- `data/` — Hardware profiles, workload profiles, model profiles, benchmark matrix
- `benchmarks/results/` — Raw benchmark JSON from Oberon cluster runs

## Methodology

CDD → TDD → EDD → BDD (Contract → Test → Event → Behavior Driven)

**Standalone Service** (`cascade_compression/service.py`)
- Single-container deployment via Containerfile
- FastAPI with /health, /stats, /cascade, /agents endpoints
- Real-time dashboard at / (frontend/index.html)
- OpenShift manifests in deploy/openshift.yaml

## Running the Service

```bash
# Standalone service with dashboard
python3 -m uvicorn cascade_compression.service:app --port 8090

# Or deploy on OpenShift
oc new-app https://github.com/jkershawrh/cascade-compression \
  -e CASCADE_LLM_URL=https://your-llm/v1 -e CASCADE_LLM_KEY=sk-...
```

## Next Steps

- Customer pilot (Amex via Ron) with hardened engine
- OCP Operator packaging (single Helm chart for cascade + governance)
- Edge deployment validation (robotics/IoT domain pack)
- Whitepaper update with final replay numbers
