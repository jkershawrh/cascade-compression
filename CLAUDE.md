# CLAUDE.md — Cascade Compression

## What This Is

Three-tier cascade compression engine for CPU inference at scale.
Consolidates the cascade pipeline, routing engine, TCO calculator, and benchmark harness
into a single repo. Built for Intel FSI engagements — proves that most AI signals
don't need a GPU.

## Architecture

**Cascade Pipeline** (`cascade_compression/cascade/`)
- Nano tier (85%+): Deterministic agents — dedup, transient suppression, severity gate, pattern/threshold classifiers
- Micro tier (10-12%): Small CPU models (360M-3B), classification/extraction lanes
- Macro tier (3-5%): Larger CPU models (3.8B-8B), generation/reasoning lanes
- Signal protocol is domain-agnostic — K8s events, AAP job runs, or any structured signal

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

## Next Steps

- Wire AAP (Ansible Automation Platform) as second signal source — proves domain-agnostic cascade
- Historical replay of 130M+ K8s signals through cascade
- Whitepaper with benchmark proof points
