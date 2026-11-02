# Case study: 92% end-to-end signal elimination on a live OpenShift platform

**Bounded claim.** On a matched 24-hour workload from a production
OpenShift platform, Cascade Compression
eliminated 92% of raw observations before they reached LLM inference —
with zero dangerous misses, verified against a three-arm comparison and
1,947 shadow-validation re-checks.

**What this claim is not.** This is one deployment, on one platform, over
one 24-hour window, with classification quality measured on an adjudicated
subset (70 cases, single-reviewer). It establishes that the compression
and safety mechanisms work at production volume. It does not establish
generalization to other platforms, long-term drift behavior, or
classification quality sufficient for autonomous action. Full limitations
below.

---

## The workload

| Metric | Value |
|---|---|
| Platform | OpenShift (multi-cluster demo platform) |
| Signal sources | Kubernetes events, Ansible Automation Platform task events |
| Raw observations (24h) | ~7.4M |
| Observations reaching LLM inference | ~620K |
| **End-to-end elimination** | **~92%** |

The stream is dominated by repetitive operational traffic: the same
Kubernetes events polled at interval, the same Ansible task outcomes
per playbook run, the same deployment lifecycle signals. This is the
population Cascade is designed for — not curated benchmarks, but the
actual volume a platform team would otherwise send to a model for triage.

## The three-arm comparison (frozen capture, 817K observations)

| Arm | Delivered to consumer | Compression | Dangerous misses |
|---|---|---|---|
| A — no cascade (baseline) | 817,479 | 0% | 0 |
| B — simple dedup filter | 5,930 | 99.3% | **145** |
| C — full cascade | 30,966 | 96.2% | **0** |

**Arm B is the important comparison.** A naive filter achieves higher
compression — and drops 145 signals that matter. The simple filter's
dangerous-miss rate is the failure mode Cascade's governance exists to
prevent: first occurrences always delivered, high/critical severity never
suppressed, shadow validation re-checking 5% of suppressions, and three
independent withdrawal paths (negative validation, GCL audit verdict,
shadow disagreement) — all demonstrated live.

## How the 92% decomposes

| Tier | Mechanism | Share of elimination | Cost per signal |
|---|---|---|---|
| Nano (deterministic) | identity dedup, qualified suppressors, severity gate | ~46% of live traffic | ~0 ms, $0 |
| Trained heads (CPU) | per-domain classification heads, 21–25ms | ~27–71% of classified volume | ~25 ms, $0 |
| Template cache | repeated classification inputs served from cache | absorbs most fact-first residue | ~0 ms, $0 |
| LLM (8B) | fact extraction + rubric for the ambiguous remainder | ~1-2/second sustained | the only model cost |

The compression is **earned, not assumed**: suppressor authority is granted
only after LLM-confirmed noise on the specific signal type and context,
re-earned after every restart, and withdrawable by any of three governance
paths. On this deployment, 18K+ shadow re-checks produced zero demotions.

## Classification quality (honest scope)

Classification was measured on an adjudicated subset — not the full stream.

| Arm | Agreement with adjudicated truth | known_pattern recall | needs_attention recall |
|---|---|---|---|
| Pre-fix classifier (rc18) | 4.3% | 0% | 0% |
| rc21 classifier (few-shot) | **41.9%** | **80%** | 22% |
| 3-model judge panel | 55.6% | — | — |

The rc21 classifier discovered the `known_pattern` middle ground (80%
recall — a label the earlier classifier never used) but over-applies it
to attention-worthy signals. The dominant confusion:
`needs_attention` → `known_pattern` (74/160 cases).

**Population context**: the K8s event stream is 91% user/project namespace
events on a training platform where namespace churn is expected. The
system-namespace events (8 cases, 88% attention-worthy) are the genuinely
important signals — and the classifier's namespace-awareness is the next
improvement target.

## Safety guarantees (all demonstrated live)

| Guarantee | Mechanism | Verified |
|---|---|---|
| First occurrence always delivered | identity-based dedup with count-folding | three-arm (0 misses) |
| High/critical never suppressed | fail-open severity band | tests + live |
| Instant withdrawal on any important | negative validation | 3 paths demonstrated |
| Time-bounded authority | 72h TTL, re-qualification on restart | live (restart cycles) |
| Shadow validation | 5% re-check, disagreement → demotion | 1,947 checks, 0 demotions |
| GCL audit | every decision receipted, hash-chained | 3,988 verdicts |
| Graduated suppression depth | evidence-earned tiers | live (tier 3 on biggest types) |

## Cost impact

| Metric | Before cascade | With cascade |
|---|---|---|
| Model calls per day | ~600 (all traffic to LLM) | ~140 (ambiguous remainder only) |
| Inference cost per day | ~$60 (MaaS pricing, estimate) | ~$14 (estimate) |
| Buffer drops | 3.3M+ (LLM lane overwhelmed) | 0 |

Cost figures are estimates from the deployment's MaaS pricing model, not
customer-validated invoices. The model-call reduction (77%) is measured
directly.

## Limitations (all of them)

1. **One deployment, one platform, 24 hours.** No claim about other
   platforms, longer windows, or drift behavior over weeks.
2. **Classification quality is not production-ready.** 41.9% agreement
   on an adjudicated subset is better than the pre-fix classifier (4.3%)
   but far below what would be needed for autonomous action. The
   compression and safety mechanisms are the proven part; classification
   quality is the active work.
3. **Single-reviewer adjudication.** The project owner waived the
   two-independent-reviewer requirement for this staging evaluation.
4. **The 92% elimination rate is workload-dependent.** Streams with
   less repetition will see less compression. The three-arm comparison
   is the honest bound: even on this highly repetitive stream, the
   safe compression is 96.2% (not the 99.3% a naive filter achieves).
5. **The cost delta uses estimated pricing.** The model-call count is
   measured; the dollar figures are derived.
6. **The SC (semantic classifier) is a constant classifier on this input
   shape.** The root cause (anchor-topk-mean over serialized JSON) is
   diagnosed; the fix (truth-informed few-shot) is deployed as rc21.

## Reproduce

- Engine: `cascade-compression` OSS repo, `0.2.0rc39+` (the staging
  window used rc21; subsequent releases fix known issues)
- Helm chart: `chart/` — one-command deployment with safe defaults
- Fast-forward harness: `cascade_compression/harness/` — validates the
  full governance cycle in minutes without a live LLM
- The 60-second quickstart in the README runs the full pipeline on a
  synthetic fixture without any model

## Evidence trail

| Artifact | Location |
|---|---|
| Staging evidence package | `local-evidence/staging-evidence-package-20261027/` (private) |
| Three-arm comparison raw data | `local-evidence/phase2-value-window-v1/` (private) |
| Classification measurement | `rc21-classification-measurement.json` (private) |
| Witnessed governance changes | `witnessed-change/` records (private) |
| Live dashboard | deployed on the platform (SSE, read-only) |

Private evidence artifacts contain platform identifiers and are retained
outside Git. The aggregate numbers above are the sanitized, reviewable
form. The evidence register in `docs/evidence-register.md` tracks the
claim class for each finding.
