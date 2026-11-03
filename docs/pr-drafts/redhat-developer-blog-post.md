# How we cut LLM triage cost 92% on our demo platform — and the evidence contract that made us trust it

*Draft for Red Hat Developer blog / enabled channels. Written in the
platform engineer's voice. All numbers from the published case study:
[cascade-compression/docs/case-study-platform-pilot.md](https://github.com/jkershawrh/cascade-compression/blob/main/docs/case-study-platform-pilot.md)*

---

Our team runs the Red Hat Partner Demo System — a multi-cluster OpenShift
platform that provisions demo environments for partners, runs Ansible
Automation Platform playbooks to build and tear down workloads, and
generates about 7.4 million operational signals per day. Kubernetes
events, task outcomes, deployment lifecycle notifications, the usual
platform noise.

Every one of those signals is a triage candidate. Is this important?
Does someone need to look at it? Is it just another pod restarting?

The naive answer: send them all to an LLM. That works — until you see
the bill and the latency. At our volume, routing everything through a
granite-class model costs roughly $60/day in inference and overwhelms
the processing queue: we measured 3.3 million buffer drops in a 24-hour
window when the LLM lane couldn't keep up.

The obvious fix — a dedup filter — works until it doesn't. We measured
this too: a simple filter achieved 99.3% compression on our stream but
dropped 145 signals that mattered. One hundred forty-five instances
where "this looks like a repeat" was wrong, and the signal someone
needed to see never arrived.

## Why a filter isn't enough

We tested the obvious fix first. A simple dedup filter achieved 99.3%
compression on our stream — and dropped 145 signals that mattered.
One hundred forty-five instances where "this looks like a repeat" was
wrong, and the signal someone needed to see never arrived.

But the real problem with a filter isn't the 145 misses — it's that
a filter is a **static snapshot of what looked repetitive yesterday**.
It can't learn that a new signal type is routine. It can't adapt when
the platform's operating rhythm changes. It can't tell you *why* it
suppressed something, or *what* survived, or *whether* its assumptions
still hold. And it has no memory — the compressed signals are gone.

## What Cascade actually does

Cascade Compression isn't a filter. It's a **governed lifecycle around
classification** — a system that learns what's routine, earns the right
to suppress it, remembers what survived, and can withdraw that right
the moment it's wrong.

**It learns.** A suppressor can only act on a signal type after the
LLM has confirmed that type as noise. New patterns get discovered,
promoted, and become suppressible. When the platform changes — a new
namespace, a new automation job, a new failure mode — the system adapts.
After every restart, authority resets and must be re-earned. We've
watched compression climb from 0% to 46% after each deployment as
suppressors re-qualify, live.

**It routes by what the signal IS.** Text events (Kubernetes warnings,
Ansible failures) go to an LLM rubric that extracts facts. Numeric
telemetry (vibration, temperature, drive health) goes to deterministic
spec-sheet rules — because we measured that LLMs can't distinguish
"the temperature is 298K" from "the temperature is dangerously high."
The right lane for the right signal, measured across nine domains.

**It remembers.** Signals that survive compression become an
institutional memory — strength-weighted, federated across instances,
queryable by agents through MCP tools. When someone asks "why did this
environment fail?", the answer comes from what the system actually
observed, not from a log search. The compression is the cost saving;
the memory is the value.

**It's accountable.** Every decision is recorded in an immutable
ledger. Shadow validation re-checks 5% of suppressions. Three
independent withdrawal paths — negative validation, GCL audit verdicts,
shadow disagreement — can strip authority instantly. When we ran a
three-arm comparison on a frozen 24-hour capture, Cascade achieved
96.2% compression with **zero** dangerous misses (vs the filter's 145).

## The results

We ran a three-arm comparison on a frozen 24-hour capture of 817,000
observations from the live platform:

| Arm | Delivered | Compression | Dangerous misses |
|---|---|---|---|
| No cascade (baseline) | 817,479 | 0% | 0 |
| Simple dedup filter | 5,930 | 99.3% | **145** |
| Full cascade | 30,966 | 96.2% | **0** |

End to end, across the full day's volume: **92% of raw observations
eliminated before LLM inference.** Daily model calls dropped from ~600
to ~140. Inference cost dropped from ~$60/day to ~$14/day. Buffer
drops went from 3.3 million to zero.

And the safety net held: 1,947 shadow-validation re-checks (5% of
suppressed signals re-examined by the LLM) produced **zero demotions**.
Three independent withdrawal paths — negative validation, GCL audit
verdicts, shadow disagreement — were all demonstrated live.

## The part that made us trust it: the evidence contract

Here's what separates this from "we built a filter and it worked":
every claim above is bounded, measured, and published with its
limitations.

- The three-arm comparison used one frozen 24-hour capture — that's
  the rigorous bound. But the system has been running continuously
  since, across two clusters and four automation controllers, with
  compression climbing from 0% to 46% as suppressors earn qualification
  after every restart. Each restart cycle is a fresh measurement of
  the same mechanism.
- Classification quality is measured on an adjudicated subset, not the
  full stream. The current classifier achieves 78.1% agreement with
  human adjudication (84% on a held-out set of unseen cases, 100%
  recall on signals that need attention, zero dangerous misses).
- The approach has been validated across nine signal domains — BGL
  supercomputer logs (100% recall), network intrusion (88.6%, 0% FP),
  predictive maintenance telemetry (97.3% via value rules), drive
  failure prediction (87.3%), multi-sensory robotics (100%
  deterministic), and credit-card fraud (73.6% on the value-rule lane).
  Different signal types, different truth sources, same engine.
- The three-arm comparison is the honest bound: even on this highly
  repetitive stream, the safe compression is 96.2%, not the 99.3% a
  naive filter achieves. The gap between those numbers is the cost
  of not dropping signals that matter.

We built the evidence machinery before we built the claims: an
immutable ledger recording every decision, a hash-chained audit trail,
per-claim evidence files with independent verification status. The
[governance contract](https://github.com/jkershawrh/governed-cognitive-loop)
— graduated authority with evidence-earned tiers, withdrawal clearing
the ladder, non-zero verification floors — is the part that makes the
compression trustworthy. Not the compression rate.

## What we learned building it

**The modality map matters more than the model.** Text events
(Kubernetes warnings, Ansible failures) classify well with LLM rubrics.
Numeric telemetry (vibration, temperature, drive health) does not —
semantic readers can't distinguish "the temperature is 298K" from "the
temperature is dangerously high." For numeric signals, deterministic
spec-sheet rules outperform LLM classification by an enormous margin.
Knowing which lane is competent for which signal type is the real
engineering.

**Suppression authority must expire.** Our most important safety
mechanism isn't a better classifier — it's the 72-hour TTL on
suppressor authority. Every restart, every deployment, every
configuration change resets the qualification state. The system has to
re-earn the right to suppress. This is annoying and it's the reason
the three-arm comparison shows zero dangerous misses.

**The memory is the product.** Signals that survive compression become
a queryable institutional memory — strength-weighted, federated across
instances, accessible via MCP tools that let agents ask "why did this
environment fail?" and get an answer grounded in what actually
happened. The compression is the cost saving; the memory is the value.

## Try it

The [full case study with all limitations](https://github.com/jkershawrh/cascade-compression/blob/main/docs/case-study-platform-pilot.md)
is in the repo. The engine runs without an LLM for evaluation:

```bash
pip install cascade-compression
python -m uvicorn cascade_compression.service:app --port 8090
```

Open http://localhost:8090 — the dashboard starts with synthetic
signals, running the full governance cycle. Configure an
OpenAI-compatible endpoint when you're ready for model-backed
classification.

The project is Apache 2.0, works with any OpenAI-compatible inference
endpoint, and ships a Helm chart for Kubernetes deployment. We're
actively looking for contributors who want to write domain packs for
their own signal types — the engine is domain-agnostic by construction.

---

*[Cascade Compression](https://github.com/jkershawrh/cascade-compression)
is an Apache 2.0 open-source project. The author runs the Red Hat
Partner Demo System and deploys Cascade on it daily. All numbers in
this post are from the published case study and can be reproduced with
the fast-forward harness in the repo.*
