# Cascade Compression roadmap

## Release work

The published `v0.2.0` wheel has an identifiable digest, but release
verification failed. The local `0.2.1rc1` candidate is incubating.
Publishing it requires a frozen commit and attested artifacts. A future
staging-qualified release also requires independent adjudication and
the candidate-bound staging handoff. [Release policy](../RELEASING.md)
· [Evidence register](evidence-register.md)

## Track 1: Distribution and adoption

| # | Item | Effort | Status |
|---|---|---|---|
| 1.1 | Submit the llm-d-sc PR | 5 min | draft for review (`docs/pr-drafts/llm-d-sc-integration-pr.md`) |
| 1.2 | Publish the Red Hat Developer blog post | 1 review | draft for review (`docs/pr-drafts/redhat-developer-blog-post.md`) |
| 1.3 | List MCP in registries (Smithery, PulseMCP, official MCP registry) | 30 min | needs account setup |
| 1.4 | Get the first external contributor | ongoing | the "PR #1 from someone else" milestone |
| 1.5 | Close one outcome loop on the production platform | 1 session | first loop closed: [anchor proposal retention](outcome-loops/2026-11-17-anchor-proposal-retention.md), with the detection half added same day (`metric_pvc_projected_full`, validated on 118 live volumes) |

**1.5 is the one that matters most** — one chronic issue from the memory, remediated, with the effect visible on the dashboard. That's the lighthouse-user story. The first loop (2026-11-17) bounded a volume that was nine days from exhaustion and measured the before/after, but it was found by review: the engine has no capacity rule and the digest does not report growth trends. Closing the detection half is what turns this into the story.

---

## Track 2: Classification refinement

| # | Item | What it fixes | Effort |
|---|---|---|---|
| 2.1 | image-churn namespace exemplars | 27% → target 70%+ (16 cases) | domain-pack change |
| 2.2 | Real_incident label discrimination | 0% RI recall (all RI → NA) | rubric or prompt refinement |
| 2.3 | Freeze a fresh label-coverage challenge | test all four labels under independent review | pending |
| 2.4 | AAP head retrain (v2) | the margin-gate hedge | needs better training data, not more data |

The historical held-out 100 tied the always-needs_attention baseline. Its
reference set contained no real_incident or routine_noise cases. A fresh
challenge and a separate prevalence sample serve different purposes.
[Classification evaluation](classification-evaluation.md)

---

### AAP volume: resolved via a pack-declared repeat identity (2026-11-17)

Enabling domain deduplication alone did nothing: `cascade_handled` stayed 0.
Two causes, both architectural rather than configurability:

1. The engine's repeat admission hardcoded Kubernetes shape — `content.kind`,
   `uid`/`event_uid`/`reason`, an int `count`, and medium severity only. AAP task
   output carries `event`/`play`/`host`/`job_id` at info severity, so it could
   never be admitted.
2. The AAP dedup window was 0, so a repeat expired immediately even when it was
   admitted.

The fix keeps the engine domain-agnostic: the *mechanism* (severity and
protected gates, message digest binding, key serialization, windows) stays in
the engine and packs declare a `RepeatIdentity` policy — identity fields,
optional kind discriminator, count requirement, volatile fields to exclude from
the key, and the severities the profile is qualified for. Kubernetes and robotic
declare exactly the rules the engine previously assumed (robotic covers three
domain labels: robotic, plc, vision); AAP declares `event`/`play` identity and
ignores `event_id`/`observed_at`/`original_event_time` in the key while keeping
them in the stored evidence.

There is **no engine-side default**. A domain with no declared policy takes no
repeat path and delivers every observation. Every installed pack registers at
startup, so a process that sees several domains (the federated aggregator) still
has all declared policies — but nothing inherits Kubernetes shape implicitly.

Measured on the AAP instance with the policy declared and
`CASCADE_DEDUP_WINDOW_AAP=3600`: compression went from **0.0% to 20.5%**
(5,499 of 26,809 signals folded), where the same window alone had produced
nothing. Volatile observation fields remain in evidence, so the fold is
auditable.

## Track 3: Memory and consumer activation

*The memory is write-only (recall_count ≈ 0). Value exists but is unread.*

The digest now collects local formations (not just federated imports), buffers
arrivals instead of rewriting the spool per memory, and runs without a mailer —
`/memories/hourly-summary` returns counts by severity, classification and
pattern over a rolling hour. Observed on the first live run: 562 formations in
three minutes, 500 `needs_attention`, with 25 `disk_pressure`, 20
`auth_failure` and 16 `network_failure` among them.

| # | Item | What it does | Effort |
|---|---|---|---|
| 3.1 | Turn on the digest | hourly summaries of what cascade caught → the team SEES the value | shipped 2026-11-17 — **not** config only: local formations never reached the digest and it rewrote the whole spool per memory |
| 3.2 | Economics panel on the live view | $/day avoided vs the naive baseline | frontend extension |
| 3.3 | Agent workflow integration | "why did cell 3 stop?" answered from memory via MCP | consumer-side work |

---

## Track 4: Edge deployment (verge)

*The robotics play: the full stack on a NUC.*

| # | Item | What it proves | Effort |
|---|---|---|---|
| 4.1 | NUC-edge resource envelope | the full stack (cascade + heads + Qwen-3B + MCP + live view) in a 4-8 core / 8-16GB namespace | 1 session |
| 4.2 | Robotics head training | the sc-head for the robotic domain (corpora exist: 50K + 20K) | 1 hour |
| 4.3 | ROS 2 bridge collector | `/diagnostic_msgs`, `/joint_states`, `/controller_status` → signals | ~100 lines |
| 4.4 | Real NUC + MicroShift | the physical deployment | procurement + 1 session |
| 4.5 | Fleet via RHACM + GitOps | multi-cell, memory federation, fleet-level GCL | the phase-4 arc |

**4.1 can run TODAY** — every component exists and is validated. The NUC envelope proves the economics; the hardware makes it physical.

---

## Track 5: Infrastructure parity

| # | Item | Why | Effort |
|---|---|---|---|
| 5.1 | Select a qualified revision before considering rollout | bind the proposed deployment to tested artifacts | pending |
| 5.2 | AAP estate stabilization | the 4 new collectors need state to stabilize; the malformed memories (from the wire-format bug) will decay through consolidation | wait |
| 5.3 | Memory archive O(n) eviction | 8s/POST at archive capacity — heap-indexed eviction | optimization |
| 5.4 | Backblaze pack formalization | candidate spec validated (87.3%); formalize if a replay deploys | optional |

---

## What I'd do next session, in order

1. **Submit the llm-d-sc PR** (5 minutes, highest distribution leverage)
2. **Review candidate identity and release gates** before considering rollout
3. **Obtain independently adjudicated coverage** of all four labels
4. **Turn on the digest** (config only — the first consumer activation)
5. **Build the NUC-edge envelope** (the verge proof)

---

## What we're NOT doing (and why)

- **Not training more heads** — the AAP retrain showed that more real data ≠ better head quality. The prompt is the product.
- **Not building a temporal-context lane** — the satellite boundary (43%) is documented; sequence models are a different architecture, not a Cascade extension.
- **Not chasing more truth corpora** — zero public truth left. The remaining truth is human (the held-out panel) and operational (live deployment outcomes).
- **Not doing Rust** — the engine is <1% of the edge footprint; the model is the lever (Qwen-3B already solved it).
- **Use the existing qualification gates.** The staging evidence and attestation are still outstanding.
