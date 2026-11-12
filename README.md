# Cascade Compression

A recurring signal can take a cheaper path once its handling has been
validated. Cascade can withdraw that authority when later evidence
disagrees and keep what survived for the next investigation.

Cascade Compression is a domain-agnostic signal-compression engine for
CPU inference. It sits between your signal sources (Kubernetes events,
Ansible task outcomes, IoT telemetry, application logs) and your LLM,
routing eligible repeats through deterministic tiers and reserving
model capacity for unresolved cases. The public v0.2.0 wheel lacks a
verified staging handoff. [Release evidence](docs/evidence-register.md)

## Try it in 60 seconds

```bash
pip install .  # from this checkout
python -m uvicorn cascade_compression.service:app --port 8090
```

Open <http://localhost:8090> — the dashboard starts without an LLM,
running the full pipeline (dedup, suppression, governance, memory) on
synthetic signals. Configure an OpenAI-compatible endpoint when you're
ready for model-backed classification.

Or deploy with Helm:

```bash
helm install cascade ./chart \
  --set image.repository=your-registry.example.com/cascade \
  --set llm.url=https://your-gateway \
  --set llm.key=your-key \
  --set llm.model=granite-3-2-8b-instruct
```

## How it works

```
signals ──→ nano tier (dedup, qualified suppression, severity gate)
                │  eligible repeats can be folded or suppressed
                ▼
            value rules (deterministic spec-sheet thresholds)
                │  covered measurements can resolve without an LLM call
                ▼
            trained heads (per-domain CPU classifiers)
                │  confident classifications can take the CPU path
                ▼
            fact-first rubric (LLM extracts designed/impact/recovery)
                │  template cache can reuse prior fact extraction
                ▼
            LLM (unresolved cases)
                ▼
            memory (survivors become durable, queryable, federated)
```

Every tier is **governed**: suppressor authority is earned by LLM-confirmed
noise, re-earned after restart, and withdrawable by three independent paths
(negative validation, GCL audit verdict, shadow disagreement). The pilot
reported shadow rechecks with zero demotions during observed windows.

## Historical measurements

| Domain | Result | Source |
|---|---|---|
| OpenShift events (24h rc7 replay) | 748,787/817,479 retained. All 215 recurring final counts preserved. | [case study](docs/case-study-platform-pilot.md) |
| BGL supercomputer logs | 100% anomaly recall, 0 dangerous | [evidence](docs/evidence-register.md) |
| Network intrusion (RT-IoT) | 88.6% attack recall, 0% FP | [evidence](docs/evidence-register.md) |
| AI4I predictive maintenance | 97.3% telemetry recall (value rules) | [evidence](docs/evidence-register.md) |
| Backblaze drive failures | 87.3% failure-day, 86.7% 7-day lead-up | [evidence](docs/evidence-register.md) |
| Multi-sensory (vibration/force/proprioception) | 100% deterministic, zero model calls | [evidence](docs/evidence-register.md) |

Honest boundaries: [satellite telemetry](docs/evidence-register.md) (43%
ceiling — temporal-context anomalies need sequence models, not value
rules); [credit-card fraud](docs/evidence-register.md) (73.6% — rare-class
detection is the value-rule lane's territory, combined with semantics 82.5%).

## MCP server (agent interface)

Cascade ships an MCP server — agents can query the memory, recall signals,
and generate summaries:

```bash
pip install ".[mcp]"
python -m cascade_compression.mcp_server --service http://localhost:8090
```

Eight read-only tools: `recall`, `memories_stats`, `query_memories`,
`export_memories`, `system_perspective`, `explain_signal`,
`search_knowledge`, `system_narrative`. See
[docs/mcp-server.md](docs/mcp-server.md) for the full API.

## Domain packs

Cascade is domain-agnostic by construction — the engine has zero domain
logic. Domain knowledge lives in swappable packs: Kubernetes, Ansible
Automation Platform, robotic workcells (vibration, force, proprioception),
finance, healthcare, telecom, and more. Each pack provides the system
prompt, few-shot exemplars, dedup windows, and (for numeric-sensor domains)
deterministic value rules.

See [docs/domain-pack-guide.md](docs/domain-pack-guide.md) to write your own.

## Architecture

Three tiers, each earning its authority:

- **Nano** (deterministic): identity-based dedup with count-folding,
  qualified noise suppressors, severity gate. Suppression requires
  LLM-confirmed noise — it's earned, not assumed.
- **Micro** (CPU): per-domain trained classification heads, margin-gated.
  [llm-d-sc](https://github.com/llm-d-incubation/llm-d-semantic-classifier)
  can serve as the semantic fast path. On 160 development cases the anchor
  classifier had 55% agreement, 91% needs_attention recall and 4%
  known_pattern recall.
- **Macro** (LLM): fact extraction (designed/impact/recovery) with a
  versioned rubric, template cache for repeated inputs, and a hybrid
  router that prefers cheaper tiers when confident.

Survivors become **memory**: strength-weighted, capacity-bounded,
content-hash deduplicated, federated across instances, queryable via MCP.

## Testing

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Tests cover the pipeline, safety contracts, domain packs, sensor adapters,
MCP server, and the fast-forward harness.

## Evidence and governance

The evidence register identifies the published artifact and the checks
that ran. The case study separates the rc7 replay, the live pilot's ~92%
estimate of traffic avoiding inference, and the classifier experiments.
[Evidence register](docs/evidence-register.md) · [Case study](docs/case-study-platform-pilot.md)

Governance mechanisms are structural, not classifier-dependent:
first-occurrence delivery, fail-open severity bands, shadow validation,
GCL audit with hash-chained receipts, graduated suppression tiers with
time-bounded authority, and three independent withdrawal paths.

For the full governance contract, see
[docs/graduated-authority.md](https://github.com/jkershawrh/governed-cognitive-loop/blob/main/docs/graduated-authority.md)
in the GCL repository.

## Documentation

- [Platform pilot and frozen replay](docs/case-study-platform-pilot.md)
- [White paper](docs/cascade-compression-whitepaper.md)
- [Architecture](docs/architecture.md)
- [Domain pack guide](docs/domain-pack-guide.md)
- [MCP server](docs/mcp-server.md)
- [Evidence register](docs/evidence-register.md)
- [Roadmap](docs/roadmap.md)

## Status

The `v0.2.1rc1` tag is a production-hardened release candidate: it carries six
fixes for defects found by deploying the candidate on a production cluster
(2026-11-12), every one regression-tested — the stats federated-sources race,
restore-time and runtime handling of capacity-evicted classification targets,
cross-build journal binding, the audit-backpressure health death spiral, the
admission deadlock on unapplicable retained work, and ledger receipt
envelopes above the gateway's content limit. All four production instances
run it. Python 3.9–3.13.

On 160 tuning cases, the platform-context classifier had 78.1% agreement
and four dangerous errors. On the held-out 100 it got 84/84 needs_attention
and 0/16 known_pattern correct, tying the always-needs_attention baseline.
The holdout contained no real_incident or routine_noise cases.
[Case study](docs/case-study-platform-pilot.md)

## License

Apache 2.0
