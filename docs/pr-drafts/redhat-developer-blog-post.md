# How our demo platform earns and withdraws the right to suppress triage signals

*Draft for Red Hat Developer. Pilot observations and the scope of the
frozen replay are documented in the [platform case study](https://github.com/jkershawrh/cascade-compression/blob/main/docs/case-study-platform-pilot.md).*

---

Our team runs a multi-cluster OpenShift platform that provisions demo
environments for partners and runs Ansible Automation Platform playbooks
to build and tear down workloads. A pilot summary estimated 7.4 million
observations in a day. Kubernetes events, task outcomes, deployment
lifecycle notifications, the usual platform noise.

Every one of those signals is a triage candidate. Is this important?
Does someone need to look at it? Is it just another pod restarting?

We tried sending traffic down the LLM lane. In one 24-hour window the
pilot reported 3.3 million buffer drops when that lane couldn't keep up.
The team estimated about $60 a day in inference under that scenario.
Cutting the queue sounded straightforward. Deciding which signals a
system could safely stop sending was harder.

## Why a filter isn't enough

We tested the obvious fix first. On a frozen capture, a simple dedup
filter suppressed 99.3% of observations. It also lost the final count
for 145 recurring events. An operator could see that an event happened
and still miss how many times it recurred.

A static filter encodes **what looked repetitive yesterday**.
It can't learn that a new signal type is routine. It can't adapt when
the platform's operating rhythm changes. It can't tell you *why* it
suppressed something, or *what* survived, or *whether* its assumptions
still hold. And it has no memory — the compressed signals are gone.

## What Cascade actually does

Cascade Compression puts a **governed lifecycle around
classification**. It learns what's routine, earns the right
to suppress it, remembers what survived, and can withdraw that right
the moment it's wrong.

**It learns.** A suppressor can only act on a signal type after the
LLM has confirmed that type as noise. New patterns get discovered,
promoted, and become suppressible. When the platform changes — a new
namespace, a new automation job, a new failure mode — the system adapts.
After a restart, the pilot's suppressors had to earn authority again.
We watched the reported suppression rate climb from 0% to about 46%
as they re-qualified.

**It routes by what the signal IS.** Text events (Kubernetes warnings,
Ansible failures) go to an LLM rubric that extracts facts. Numeric
telemetry (vibration, temperature, drive health) goes to deterministic
spec-sheet rules — because we measured that LLMs can't distinguish
"the temperature is 298K" from "the temperature is dangerously high."
For text events with a confident match, a margin-gated head can classify
on CPU. The LLM handles cases the cheaper lane leaves unresolved.

**It remembers.** Signals that survive compression become an
institutional memory — strength-weighted, federated across instances,
queryable by agents through MCP tools. When someone asks "why did this
environment fail?", the answer comes from what the system actually
observed. Retaining that context gives the next investigation a place
to start.

**It's accountable.** A hash-chained ledger records decisions. Shadow
validation samples suppressions for a second look. Three independent
withdrawal paths — negative validation, GCL audit verdicts, shadow
disagreement — can strip a suppressor of its authority.

## The results

We ran a three-arm comparison on one frozen 24-hour capture. The rc7
replay tested the deterministic tier and made zero model calls.

| Arm | Observations retained | Share suppressed | Recurring events missing their final count (of 215) |
|---|---:|---:|---:|
| No cascade (baseline) | 817,479 | 0% | 0 |
| Simple dedup filter, keep first | 5,930 | 99.3% | 145 |
| rc7 verified-repeat-only profile | 748,787 | 8.4% | 0 |

A separate live summary estimated that ~620K of ~7.4M observations
reached inference over a day (~92% avoided). It also reported ~600
versus ~140 daily model calls. The corresponding ~$60 and ~$14 daily
costs were pricing estimates. A matched cost comparison is still open.

Pilot accounts reported shadow rechecks with zero demotions during
observed windows. A recheck tells us whether the verifier agreed with
the suppression. Incident detection needs independent incident labels.

## The part that made us trust it: the evidence contract

The kind of evidence matters when deciding how much authority to grant.

- The replay checks a specific property of recorded events. The live
  restart observations show suppressors qualifying again as traffic
  arrives. They use different windows and answer different questions.
- The platform-context classifier agreed with the reference on 78.1%
  of 160 tuning cases, with four dangerous classification errors. On
  the held-out 100, it got 84/84 needs_attention and 0/16 known_pattern
  correct. Always choosing needs_attention also scores 84%. There were
  no real_incident or routine_noise cases in that holdout.
- Separate experiments examined how the lanes perform across domains — BGL
  supercomputer logs (100% recall), network intrusion (88.6%, 0% FP),
  predictive maintenance telemetry (97.3% via value rules), drive
  failure prediction (87.3%), multi-sensory robotics (100%
  deterministic), and credit-card fraud (73.6% on the value-rule lane).
  Different signal types, different truth sources, same engine.
- A post-hoc latest-wins dedup filter also preserved every recurring
  final count in the replay. This check alone cannot establish which
  approach best supports an operator's decisions.

The governance contract gives a reviewer something to inspect: the
evidence used to grant authority, the decisions recorded in the ledger,
and the conditions that withdraw it. For the published v0.2.0 wheel,
release verification failed before the formal staging handoff.
[Governance contract](https://github.com/jkershawrh/governed-cognitive-loop)
· [Release evidence](https://github.com/jkershawrh/cascade-compression/blob/main/docs/evidence-register.md)

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
suppressor authority. An expired suppressor has to re-earn the right
to act. An operator reviewing a rule can therefore ask when it was
last validated and what would cause its withdrawal.

**The memory is the product.** Signals that survive compression become
a queryable institutional memory — strength-weighted, federated across
instances, accessible via MCP tools that let agents ask "why did this
environment fail?" and get an answer grounded in what actually
happened. That history is useful even after the rule that retained it
has expired.

## Try it

The [full case study with all limitations](https://github.com/jkershawrh/cascade-compression/blob/main/docs/case-study-platform-pilot.md)
is in the repo. The engine runs without an LLM for evaluation:

```bash
pip install .  # from the Cascade checkout
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
is an Apache 2.0 open-source project. The fast-forward harness exercises
the governance lifecycle on synthetic inputs.*
