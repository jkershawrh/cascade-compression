# Cascade Compression — Roadmap (2026-11-03)

**Where we are:** 0.2.0 staging qualified. Classification 78.1%. Compression 92% (platform pilot). Nine domains truth-mapped. Zero public truth left. Distribution channel opened (GitHub release, case study, README rewritten). The engineering is complete; the remaining work is adoption, refinement, and the edge deployment.

---

## Track 1: Distribution and adoption (the #1 lever)

*Your cold-adopter review diagnosed this: "the ratio of engineering to claims is roughly infinity:1." We fired the claim machine. Now fire the distribution.*

| # | Item | Effort | Status |
|---|---|---|---|
| 1.1 | Submit the llm-d-sc PR | 5 min | draft ready (`docs/pr-drafts/llm-d-sc-integration-pr.md`) |
| 1.2 | Publish the Red Hat Developer blog post | 1 review | draft ready (`docs/pr-drafts/redhat-developer-blog-post.md`) |
| 1.3 | List MCP in registries (Smithery, PulseMCP, official MCP registry) | 30 min | needs account setup |
| 1.4 | Get the first external contributor | ongoing | the "PR #1 from someone else" milestone |
| 1.5 | Close one outcome loop on infra01 | 1 session | fix `pool_exhausted`, show the signal drop on the live view |

**1.5 is the one that matters most** — one chronic issue from the memory, remediated, with the effect visible on the dashboard. That's the lighthouse-user story.

---

## Track 2: Classification refinement (0.2.1)

| # | Item | What it fixes | Effort |
|---|---|---|---|
| 2.1 | cnv-images namespace exemplars | 27% → target 70%+ (16 cases) | domain-pack change |
| 2.2 | Real_incident label discrimination | 0% RI recall (all RI → NA) | rubric or prompt refinement |
| 2.3 | Run the held-out 100 | the generalization number | 1 measurement session |
| 2.4 | AAP head retrain (v2) | the margin-gate hedge | needs better training data, not more data |

**2.3 is the most important** — the held-out 100 (zero overlap with tuning cases, verified four ways) is the honest generalization number. Everything else is optimization on the same 160 cases.

---

## Track 3: Memory and consumer activation

*The memory is write-only (recall_count ≈ 0). Value exists but is unread.*

| # | Item | What it does | Effort |
|---|---|---|---|
| 3.1 | Turn on the digest | hourly summaries of what cascade caught → the team SEES the value | config only |
| 3.2 | Economics panel on the live view | $/day avoided vs the naive baseline | frontend extension |
| 3.3 | Agent workflow integration | "why did cell 3 stop?" answered from memory via MCP | consumer-side work |

---

## Track 4: Edge deployment (verge)

*The robotics play: the full stack on a NUC.*

| # | Item | What it proves | Effort |
|---|---|---|---|
| 4.1 | NUC-edge envelope on Flightpath | the full stack (cascade + heads + Qwen-3B + MCP + live view) in a 4-8 core / 8-16GB namespace | 1 session |
| 4.2 | Robotics head training | the sc-head for the robotic domain (corpora exist: 50K + 20K) | 1 hour |
| 4.3 | ROS 2 bridge collector | `/diagnostic_msgs`, `/joint_states`, `/controller_status` → signals | ~100 lines |
| 4.4 | Real NUC + MicroShift | the physical deployment | procurement + 1 session |
| 4.5 | Fleet via RHACM + GitOps | multi-cell, memory federation, fleet-level GCL | the phase-4 arc |

**4.1 can run TODAY** — every component exists and is validated. The NUC envelope proves the economics; the hardware makes it physical.

---

## Track 5: Infrastructure parity

| # | Item | Why | Effort |
|---|---|---|---|
| 5.1 | Roll infra01 to 0.2.0 | both clusters on the same revision | 30 min |
| 5.2 | AAP estate stabilization | the 4 new collectors need state to stabilize; the malformed memories (from the wire-format bug) will decay through consolidation | wait |
| 5.3 | Memory archive O(n) eviction | 8s/POST at archive capacity — heap-indexed eviction | optimization |
| 5.4 | Backblaze pack formalization | candidate spec validated (87.3%); formalize if a replay deploys | optional |

---

## What I'd do next session, in order

1. **Submit the llm-d-sc PR** (5 minutes, highest distribution leverage)
2. **Roll infra01 to 0.2.0** (30 minutes, parity)
3. **Run the held-out 100** (the generalization number — the most important measurement remaining)
4. **Turn on the digest** (config only — the first consumer activation)
5. **Build the NUC-edge envelope** (the verge proof)

---

## What we're NOT doing (and why)

- **Not training more heads** — the AAP retrain showed that more real data ≠ better head quality. The prompt is the product.
- **Not building a temporal-context lane** — the satellite boundary (43%) is documented; sequence models are a different architecture, not a Cascade extension.
- **Not chasing more truth corpora** — zero public truth left. The remaining truth is human (the held-out panel) and operational (live deployment outcomes).
- **Not doing Rust** — the engine is <1% of the edge footprint; the model is the lever (Qwen-3B already solved it).
- **Not adding more qualification gates** — the staging gate is passed. The next unit of leverage is adoption, not governance.
