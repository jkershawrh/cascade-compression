# Cascade Compression

Most of what you send to an LLM for triage is a repeat. In a 24-hour
platform pilot, 92% of raw observations were duplicates, known patterns,
or confidently classifiable on CPU — so Cascade routed them deterministically
(~25ms, $0) and sent only the ambiguous remainder to the model.
[Read the bounded claim and its limits →](docs/case-study-platform-pilot.md)

Cascade Compression is a domain-agnostic signal-compression engine for
CPU inference. It sits between your signal sources (Kubernetes events,
Ansible task outcomes, IoT telemetry, application logs) and your LLM,
routing the repetitive majority through deterministic tiers and reserving
model capacity for what's genuinely new or ambiguous.

## Try it in 60 seconds

```bash
pip install cascade-compression
python -m uvicorn cascade_compression.service:app --port 8090
```

Open <http://localhost:8090> — the dashboard starts without an LLM,
running the full pipeline (dedup, suppression, governance, memory) on
synthetic signals. Configure an OpenAI-compatible endpoint when you're
ready for model-backed classification.

Or deploy with Helm:

```bash
helm install cascade ./chart \
  --set llm.url=https://your-gateway \
  --set llm.key=your-key \
  --set llm.model=granite-3-2-8b-instruct
```

## How it works

```
signals ──→ nano tier (dedup, qualified suppression, severity gate)
                │  ~46% eliminated, 0 ms, $0
                ▼
            value rules (deterministic spec-sheet thresholds)
                │  sensor lanes: 100% resolved, 0 model calls
                ▼
            trained heads (per-domain CPU classifiers, 21–25 ms)
                │  ~27–71% of classified volume
                ▼
            fact-first rubric (LLM extracts designed/impact/recovery)
                │  template cache absorbs most repeats
                ▼
            LLM (the ambiguous remainder — ~1–2/second sustained)
                ▼
            memory (survivors become durable, queryable, federated)
```

Every tier is **governed**: suppressor authority is earned by LLM-confirmed
noise, re-earned after restart, and withdrawable by three independent paths
(negative validation, GCL audit verdict, shadow disagreement). In the pilot:
1,947 shadow re-checks, zero demotions.

## What's proven

| Domain | Result | Source |
|---|---|---|
| OpenShift platform (24h pilot) | 92% end-to-end elimination, 0 dangerous misses | [case study](docs/case-study-platform-pilot.md) |
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
pip install cascade-compression[mcp]
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

- **Nano** (deterministic, ~0ms): identity-based dedup with count-folding,
  qualified noise suppressors, severity gate. Suppression requires
  LLM-confirmed noise — it's earned, not assumed.
- **Micro** (CPU, 21–25ms): per-domain trained classification heads,
  margin-gated. [llm-d-sc](https://github.com/llm-d-incubation/llm-d-semantic-classifier)
  can serve as the semantic fast path — measured at 55% agreement on
  adjudicated production signals (91% needs_attention recall), equivalent
  to a 3-model judge panel.
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

1,420 tests covering the full pipeline, safety guarantees, domain packs,
sensor adapters, MCP server, and the fast-forward harness.

## Evidence and governance

Every quantitative claim in this repository traces to an evidence file.
The [evidence register](docs/evidence-register.md) tracks claim class
(verified / corrected / candidate) for every finding. The
[case study](docs/case-study-platform-pilot.md) publishes the bounded
24-hour pilot claim with full limitations.

Governance mechanisms are structural, not classifier-dependent:
first-occurrence delivery, fail-open severity bands, shadow validation,
GCL audit with hash-chained receipts, graduated suppression tiers with
time-bounded authority, and three independent withdrawal paths — all
demonstrated live.

For the full governance contract, see
[docs/graduated-authority.md](https://github.com/jkershawrh/governed-cognitive-loop/blob/main/docs/graduated-authority.md)
in the GCL repository.

## Documentation

- [Case study: 92% elimination, 24h pilot](docs/case-study-platform-pilot.md)
- [White paper](docs/cascade-compression-whitepaper.md)
- [Architecture](docs/architecture.md)
- [Domain pack guide](docs/domain-pack-guide.md)
- [MCP server](docs/mcp-server.md)
- [Evidence register](docs/evidence-register.md)
- [Roadmap](docs/roadmap.md)

## Status

**0.2.0** — staging qualified. The engine, governance, Helm chart, and MCP
server are complete and tested. Classification quality is actively
improving (41.9% agreement on the pilot's adjudicated subset — see the
case study for honest scope). Python 3.9–3.13.

## License

Apache 2.0
