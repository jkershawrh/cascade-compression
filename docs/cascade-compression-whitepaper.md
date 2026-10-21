# Cascade Compression
## Turning validated recurring decisions into reusable computation

White paper | October 2026 | Review edition

## Abstract

Cascade Compression proposes that a signal-processing system should spend model effort in proportion to unresolved decision work. Recurring cases with validated handling can follow a deterministic path; uncertain cases need classification or analysis. The goal is to reduce total work while preserving decisions that matter, with evidence that explains what happened and supports correction when conditions change.

The product combines tiered processing with a lifecycle for reusable decisions: observation, candidate formation, validation, activation, monitoring, expiry, and demotion. Its durable value depends on that lifecycle and its evidence, rather than a particular model or a large headline compression percentage. The central test is whether a bounded deployment produces useful decisions with less total work than the actual prior workflow and a simple deterministic baseline.

The public engine provides experimental product mechanics. The separate production-proof repository contains integration and evaluation work, including a differing local engine. An opt-in evaluation runner now identifies and verifies the exact public wheel used for a run. This does not complete the service migration or establish production qualification. This paper describes the thesis, implemented mechanisms, and the evidence still required; it makes no production safety, universal compression, or realized savings claim.

## 1. The problem: repeated inference for repeated decisions

Signal streams mix recurring conditions with unusual events. Repeated status changes, repeated observations of the same condition, and known operating patterns can consume the same inference path as an unfamiliar failure. Applying a model repeatedly to an already resolved decision can add processing, queues, and review work without adding useful information.

The proportion of reusable decisions depends on the workload, its context, and its policy. A repeated event may still require separate handling for compliance or because its origin or severity changed. Low severity alone does not establish that a signal is benign. A system that reduces volume without preserving these distinctions can hide important work.

Cascade therefore treats compression as a decision problem. It asks which work can be handled through a validated reusable path, which work remains uncertain, and what evidence justifies the distinction. Its intended output is a useful and reviewable decision stream, not merely a smaller stream.

## 2. The Cascade thesis

The thesis is that validated recurring decisions can become reusable computation, reducing repeated model work while retaining the ability to detect and respond to change.

This has three parts. First, processing should be tiered: inexpensive deterministic handling precedes bounded classification and more expensive analysis where needed. Second, reuse must have a lifecycle: a learned pattern is a candidate for validation, not immediate authority. Third, the system must preserve evidence and paths for reversal so that a decision can be reviewed, expired, or withdrawn.

A lower model-call count is an intermediate measure. The thesis succeeds only when useful final decisions are preserved and total work falls after accounting for validation, fallback, queueing, storage, audit delivery, recovery, and human review. If those costs offset the avoided inference, or important decisions are lost, the deployment has not demonstrated the intended value.

## 3. Product architecture

### Nano: reusable deterministic handling

The nano path handles cases for which configured rules or validated agents provide an appropriate decision. Built-in mechanics include deduplication, transient handling, severity-related routing, pattern classification, and threshold classification. Learned candidates extend the idea to recurring observations.

The existence of these mechanics does not prove that their defaults suit every deployment. Suppression policies must be evaluated against that deployment's actionability requirements. Deterministic classification, duplicate handling, and suppression are different outcomes and should remain distinguishable in evidence.

### Micro: classification of unresolved cases

Cases that remain unresolved can use a bounded classification step. The classifier should expose its revision, taxonomy, score semantics, and failure behavior. Its output informs the decision path according to explicit policy. Agreement between models is not independent truth, and a confident label does not by itself justify suppressing a signal.

Classifier selection is an optimization within the product. A particular vendor or model family is not a prerequisite for the Cascade thesis. The current public service uses its documented taxonomy; model interchangeability does not imply that every taxonomy is already supported by the shipped service.

### Macro: analysis where uncertainty remains

Cases requiring richer context or reasoning can be escalated for deeper analysis or human handling. Escalation and fallback add cost and must appear in the evaluation. A failure to classify should be recorded as a failure or unresolved outcome, rather than counted as a successful reduction in work.

The proportions routed through these tiers are measured properties of a particular workload and configuration. This paper assigns no universal percentage, latency, or hardware requirement to any tier.

## 4. Learning requires governed activation

A recurring pattern can suggest a useful candidate, but recurrence does not establish correctness. Validation must test whether the proposed handling is appropriate, especially for cases where a mistaken suppression would matter.

Cascade includes promotion, shadow validation, time-bounded activation, demotion, and optional review controls. These provide mechanisms for governing reusable decisions. Their effectiveness depends on the quality and independence of the evidence, the representativeness of the tested cases, and the behavior of the actual deployed artifact.

An activation gate based on model labels establishes agreement with that model under the tested conditions. It does not establish a population-wide false-negative rate or an absolute safety guarantee. Independent review and authoritative outcomes are needed when a claim concerns real actionability.

Monitoring and expiry address change after activation. New behavior, conflicting evidence, drift, or unavailable dependencies may require requalification or demotion. Restoration after restart also needs evaluation: persisted historical counts are not a substitute for establishing that the restored rule remains appropriate.

## 5. Memory as evidence, not automatic truth

Surviving signals can become retained context for later retrieval and review. The product includes memory storage, recall, consolidation, and federation mechanics. These can help preserve operational history and make relevant prior observations accessible across sessions and sources.

Memory usefulness must be evaluated separately from memory volume. A large archive, high strength score, or repeated reinforcement does not establish that its contents are accurate, causally explanatory, or useful for the next decision. Retrieved context needs provenance, appropriate scope, and review when it changes action.

The public engine distinguishes unverified memory from narrowly governed evidence that can support suppression. A locally approved benign outcome within an exact permitted context has a different status from a similar-looking historical observation. That boundary is essential: memory should help the system learn without turning repetition into authority.

The intended product benefit is useful institutional context with a reviewable chain of evidence. Durable knowledge requires measured retrieval usefulness, correction, and recovery, not simply durable storage.

## 6. Domain independence and product boundaries

The reusable framework is built around structured signals, processing policies, decision evidence, and lifecycle controls. Domain integrations supply sources and context; deployment policy defines what requires action and which mistakes are unacceptable.

Domain independence does not eliminate domain-specific validation. A financial compliance event, a service outage, and a documentation change can share processing mechanics while requiring different decisions, retention rules, and review standards. Reusing the framework should not silently reuse the authority of a policy validated elsewhere.

The public repository is the reusable engine. The production-proof repository is the integration and evidence environment. Raw operational payloads, customer economics, credentials, reviewer records, and deployment state remain restricted. Generic fixes can move upstream with synthetic reproductions; reviewed aggregates can support bounded claims when they identify the exact candidate.

## 7. Current evidence and limits

### Implemented mechanics

Source and contracts describe tiered handling, promotion and demotion, memory lifecycle, classifier interfaces, and governed evidence boundaries. Tests can verify named behavior on a specific revision. That is evidence of executable mechanics, rather than proof of effectiveness across a population of operational signals.

The public source package is on the 0.2.0rc5 experimental line. Its release profile is incubating_oss and explicitly excludes classification-effectiveness, natural compression, staging-readiness, production-readiness, and customer-outcome claims. A version string is not evidence that a particular release has passed qualification.

### Exact-artifact evaluation

The opt-in proof runner verifies a supplied OSS wheel's digest and package version, loads that engine in an isolated interpreter, checks loaded module origins, and records engine and proof-runner hashes. Existing direct evaluation calls identify their engine as local-unverified.

Focused verification passed 22 tests and exercised a two-case synthetic static arm against a locally built OSS 0.2.0rc5 wheel. The arm made no model calls and had no execution failures. This verifies the bounded integration path and artifact identity. It does not measure classification quality, operational prevalence, production safety, or customer savings.

The proof service and replay paths still use the local engine, and full package-dependency migration remains pending. Historical proof results cannot automatically qualify the public engine, because the implementations differ. Candidate source, image, configuration, contracts, models, and observation window still need to reconcile to a frozen manifest before qualification.

### Historical results

Earlier narratives include large replay volumes, compression percentages, model comparisons, and cost scenarios. Their provenance and status differ: some have local source artifacts, some are documented without a completed public evidence package, and others need reconciliation. They remain historical context and are not reused here as current product guarantees.

Cost scenarios depend on explicit assumptions. Neither reduced model calls nor a projected hardware comparison establishes realized savings. Claims about total cost require observed workload costs and a matched baseline, including operational and review effort.

## 8. How to test the thesis

Evaluate the actual prior workflow, a simple deterministic filtering baseline, and full Cascade on identical frozen inputs. All arms should record final dispositions, unresolved and failed cases, inference calls and tokens where available, runtime, queue behavior, and human effort. Representative inputs are needed for prevalence and natural compression claims; a deliberately balanced challenge corpus serves a different purpose.

Define actionability, dangerous misses, and acceptance criteria before evaluating models. Independent reviewers should resolve disagreements using authoritative evidence where possible. The evaluated classifier must not supply its own ground truth. Report the denominator, observation window, exclusions, and uncertainty for every quality claim.

Measure reusable-decision value over time. Compare activated handling with continued model processing on reviewed cases, including the costs of observation, validation, shadow checks, expiry, and demotion. Track whether reuse remains appropriate as conditions change. Lower inference volume is valuable only when the intended decisions survive.

Exercise restart, dependency outage, overload, storage failure, stale rules, expiry, rollback, and audit recovery on the selected artifact. Reconcile losses, repeated processing, dropped or expired work, and delayed decisions. Classifier pending-work persistence and audit-delivery spools are separate mechanisms; qualification of one does not qualify the other.

Evaluate memory with reviewed retrieval and downstream decision tasks. Ask whether retained context improves a later decision or reduces repeated investigation, and whether incorrect context can be corrected or removed. Count useful outcomes rather than treating archive size as proof of value.

## 9. Product maturity and defensibility

Cascade has an implemented product core suitable for further bounded evaluation. Production readiness and a durable operational product require evidence of reliability and usefulness under declared conditions. Classification is one component of that work; completing a classifier alone does not establish completion of the product.

The strongest prospective source of defensibility is the accumulated ability to turn reviewed recurring decisions into governed reusable handling, with provenance, recovery, and measured value. Model selection alone is unlikely to establish that advantage. This is a product strategy hypothesis, not a demonstrated competitive moat.

Sharpening the product means making adoption and inspection easier: replay before activation, clear decision explanations, reviewable evidence, visible failure states, and a value report grounded in the user's prior workflow. These should take priority over adding domains, deployment options, or model families without a measured need.

## 10. Next-step roadmap

1. Complete artifact binding. Migrate service and replay integrations, reconcile the declared dependency, freeze the candidate, and capture source, package, image, configuration, and environment provenance.
2. Demonstrate incremental value. Run independently reviewed matched arms over a bounded workload and report useful final decisions alongside total work and failures.
3. Demonstrate durability and useful memory. Complete interruption, recovery, drift, audit, and retrieval evaluations with reconciled outcomes.
4. Transfer reusable improvements. Bring generic fixes upstream with synthetic regressions and publish only reviewed, candidate-bound aggregates under the public evidence policy.
5. Optimize measured bottlenecks. Improve classification, routing, or storage where results show that a change reduces total work without exceeding the declared dangerous-miss threshold.

The thesis remains testable: validated reuse should reduce total decision work while preserving what matters. The next phase is to demonstrate that result on a frozen candidate and bounded workload, then make the evidence and limits easy to inspect.

## Evidence references

The repository edition links to current contracts, evidence policy, artifact handoff, and roadmap. The companion evidence note identifies the local integration verification used in this review edition. Historical narratives are archived separately and do not supply current qualification claims.

- [Public evidence register](evidence-register.md): allowed claims and pending gates.
- [Proof handoff](proof-handoff.md): exact-artifact qualification boundary.
- [Classification evaluation](classification-evaluation.md): independent quality evaluation.
- [Staging evidence](staging-evidence.md): candidate-bound qualification requirements.
- [Roadmap](roadmap.md): ordered validation and product priorities.
- [Documentation status](documentation-status.md): current documentation authority.
