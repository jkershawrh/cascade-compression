# Claims register

Single source of truth for every quantitative claim made in this repository's
README, whitepapers, and blog drafts. **If a number appears in a doc, it must
appear here with a source.** When a figure changes, change it here first, then
propagate.

Status values:

- **verified** — checkable from this repository right now, by the command or file named
- **documented** — real run, methodology published, raw artifact not in the repo
- **needs confirmation** — quoted in docs but not yet reconciled against run records

## Repository facts (verified)

| Claim | Value | How to check |
|---|---|---|
| Test count | **787** | `python -m pytest tests/ -q` |
| Domain packs | **10** | `ls cascade_compression/domains/*.py` (excl. `__init__`) |
| Collector modules | **23** | `ls cascade_compression/collectors/*.py` (excl. `__init__`, `base`, `memory_parsers*`) |
| Collector sidecar modes | **17** | `_builtin_modes` (3) + `_COLLECTOR_REGISTRY` (14) in `collector_sidecar.py` |
| Routing lanes | **6** | `config/lane_prompts.yaml` — classification, extraction, json, generation, reasoning, code |
| Models with measured throughput | **24**, all on `xeon6-6780e` | `data/hardware_profiles.json` |
| Adversarial edge scenarios in-repo | **19** | `cascade_compression/edge_scenarios.py` — 13 in lists, 6 across two dicts |
| Promotion gate | 200+ samples, `max_false_negative: 0.0` for nano | `cascade/promotion.py` |
| Shadow validation rate | 5% default | `CASCADE_SHADOW_RATE`, `bridge.py` |
| Activation TTL | 72h default | `CASCADE_ACTIVATION_TTL_HOURS`, `bridge.py` |

## Run results

| Claim | Value | Status | Source |
|---|---|---|---|
| K8s replay — signals | 142.4M | documented | [REPLAY-METHODOLOGY.md](REPLAY-METHODOLOGY.md) — raw artifact not published |
| K8s replay — compression | 99.1% | documented | as above |
| K8s replay — LLM share | 9,685 signals (0.007%) | documented | as above |
| K8s run — signals (committed artifact) | 68,686,111 | verified | `benchmarks/results/cascade-k8s-final-state.json` |
| K8s run — compression (committed artifact) | 99.47% | verified | as above, pre-hardening engine |
| Nano plateau run | 243,311 signals, 72.46% | verified | `benchmarks/results/cascade-nano-plateau-241k.json`, pre-hardening |
| AAP — compression | 98.1% | needs confirmation | no artifact in repo |
| AAP — shadow demotions | 63 | needs confirmation | no artifact in repo |
| Org knowledge — compression | 83% | needs confirmation | no artifact in repo |
| Full adversarial suite | 61 scenarios | needs confirmation | 19 ship in-repo; the rest were run against live instances and are not published |

### Rows that must be reconciled before the next doc pass

These are quoted inconsistently across documents today. Pick one value per row,
record it here, then propagate.

| Claim | Values currently in docs | Decision |
|---|---|---|
| Live production signals | 5.5M+ / 6.2M | `TODO` |
| OpenShift cluster count | 6 / 9 / 10 | `TODO` |
| Live compression | 82% | `TODO` — confirm it pairs with the chosen cluster count |
| Aggregated memories | 20,900+ | `TODO` |
| Memory evictions | 700K+ | `TODO` |
| GPU deep analyses | 19,000+ | `TODO` |
| Contextual suppressors discovered | 101 | `TODO` |

## Model benchmarks

| Claim | Value | Status | Note |
|---|---|---|---|
| granite-3-2-8b-instruct | 14/20, 860ms, 0 dangerous misses | verified | `docs/model-benchmarks.md` |
| phi4-mini | 14/20, 734ms, 0 dangerous misses | verified | as above |
| Grading set size | **n = 20** | verified | A one-signal difference separates 14/20 from 13/20. Quote the sample size whenever the leaderboard is cited. |
| Prompt sensitivity | noise rate 0.9% → 37.3% on retune | verified | `docs/model-benchmarks.md`. Compression is a property of (signals, model, prompt), not the framework alone. |
| Tier-1 rubric gate | FAIL, 5 reds | verified | `benchmarks/results/tier1-rubric-20260803.json`, 2026-08-03 |

## TCO

| Claim | Status | Note |
|---|---|---|
| Cascade on Xeon 6 — $33K / 3yr | assumption-based | Built on `data/model_profiles.json`, whose `_note` states throughput is **ESTIMATED**, and on operator-supplied hardware prices |
| GPU (H100) — $266K / 3yr | **not measured by this framework** | `h100-sxm` has **zero** measured models in `data/hardware_profiles.json`. List price + published third-party throughput. |
| Cloud API — $540K / 3yr | **not measured by this framework** | Both cloud profiles have zero measured models. Published token pricing. |
| "~93% cost reduction" / "8x lower cost" | derived from the above | Inherits every assumption in the two rows above. Not a validated cost guarantee. |

The calculator itself is honest about this: `TCOResult` carries an
`unsupported_options` field and refuses to size a model/hardware pair it has no
measured throughput for. Documents quoting the dollar figures must carry the same
caveat.

## On "zero false negatives"

Everywhere this appears it means **zero shadow-detected disagreements with the
oracle model**, not zero signals wrongly suppressed in an absolute sense. The
oracle is a small CPU model scoring 14/20 on the 20-signal grading set. The
promotion gate genuinely enforces `max_false_negative: 0.0` at 200+ samples
(`cascade/promotion.py`) — the guarantee is real, but it is relative to the
oracle and should be stated that way.

Note also that `bridge.py` relaxes to a **2% important-rate threshold** when
re-activating agents from historical counts on state restore
(`_promote_orphaned_noise_types`), rather than the zero-tolerance live gate. The
docstring explains why; any doc quoting the zero-FN gate should acknowledge the
restore path exists.
