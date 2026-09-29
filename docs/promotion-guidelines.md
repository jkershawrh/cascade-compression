# Nano-agent promotion guidelines

Cascade can move a repeated model-backed judgment into a deterministic rule. This is a governed
optimization, not autonomous truth creation: the corpus analyzer proposes, the promotion engine
qualifies, and runtime safety controls can deactivate the result.

## Lifecycle

```text
observed pattern
    -> draft proposal (inactive)
    -> candidate (inactive)
    -> pending approval (optional, inactive)
    -> nano (active)
    -> micro / macro evidence tiers
```

Default thresholds are:

| Tier | Minimum samples | Minimum accuracy | Maximum FP rate | Maximum FN rate | Human reviewed |
|---|---:|---:|---:|---:|---|
| Candidate | 50 | 0.60 | 0.30 | 0.20 | No |
| Nano | 200 | 0.75 | 0.15 | 0 | Optional gate |
| Micro | 500 | 0.85 | 0.10 | 0 | Yes |
| Macro | 1,000 | 0.85 | 0.05 | 0 | Yes |

Nano is the first active tier. When `CASCADE_HUMAN_GATE` is enabled, a qualifying candidate pauses
at `pending_approval` before nano activation. Approval records a reviewer reference and timestamp,
and the engine re-checks the complete nano thresholds at activation time. Regressed evidence
invalidates the approval. Thresholds are policy defaults, not statistical proof.

## Discovery

The `CorpusAnalyzer` maintains a bounded observation buffer and reports:

- repeated type/source/namespace patterns;
- signal types that dominate the observed window; and
- strong type/severity regularities.

The current bridge turns only repeat-flood and dominant-type proposals into activation candidates.
Mono-severity patterns remain discovery evidence because severity alone is not a safe suppression
rule.

Every proposal begins inactive. Frequency establishes that a pattern exists; it does not establish
that the pattern is unimportant.

## Qualification in the bridge

Authoritative classifier outcomes are counted per signal type as suppressive (`routine_noise` or
`known_pattern`) or important (`needs_attention` or `real_incident`). For a discovered repeat or
dominant pattern, the bridge supplies those counts to the promotion engine.

Activation requires the promotion thresholds plus the bridge's zero-known-important policy. A
model answer is evidence, not ground truth, so decision-grade evaluation also requires independent
held-out adjudication. Production feedback coverage must be measured separately.

Restored historical counts remain inactive until fresh qualification; restart recovery cannot turn
orphaned counts into an active suppressor. Contextual patterns also require zero known important
examples. When the human gate is enabled, automatic contextual activation remains disabled until a
separately scoped approval mechanism is available.

## Active rule types

- `RepeatFloodSuppressor` handles a validated repeating signal type after its repeat threshold.
- `DominantNoiseSuppressor` handles a validated noise type.
- `ContextualNoiseSuppressor` narrows a mixed type to a validated context value.

Built-in deterministic agents are not part of this promotion ladder. They include deduplication,
transient suppression, severity handling, pattern classification, numeric threshold classification,
and escalate-only trend or priming behavior.

## Demotion and expiry

An active learned rule is deactivated when:

- external feedback confirms that it suppressed an important signal;
- sampled shadow classification identifies a disagreement treated as a miss;
- its activation TTL expires; or
- an enabled external audit returns a failed verdict.

A confirmed miss demotes the rule directly to draft and resets its qualifying sample count. TTL
expiry also demotes it and makes it eligible to begin re-qualification. Demotion is automatic;
reactivation is never an automatic restoration of the previous active tier.
Missing, malformed, or timezone-free activation timestamps fail closed by deactivating the rule.

These controls detect known mistakes. They cannot detect an error that is never sampled, labeled,
or returned through feedback.

## Audit trail

Promotion and demotion events include the agent, transition, timestamp, sample count, accuracy,
false-positive and false-negative rates, reason, and optional batch identity. They remain in local
state. When a ledger is configured with persisted Cascade state, the bridge first writes the event
to its durable receipt spool and acknowledges it only after successful remote delivery.

An external ledger and GCL are optional assurance layers. Documentation must not imply an immutable
receipt or independent verdict when those integrations are not configured and healthy.

## Acceptance checklist

Before activating a learned rule:

- [ ] Input identity prevents unrelated events from collapsing together.
- [ ] The proposal is narrow enough to inspect and reverse.
- [ ] The qualifying sample meets the configured threshold.
- [ ] No known important example appears in that sample.
- [ ] The holdout includes adequate support for every classification label.
- [ ] Authoritative dangerous misses are zero on the adjudicated holdout.
- [ ] Shadow validation, feedback accounting, TTL expiry, and demotion are observable.
- [ ] Optional audit delivery is durable and drained if a governed staging claim requires it.

The separately maintained pilot may supply operational evidence, but public OSS examples and tests
establish mechanics only. Publish only sanitized aggregates bound to their observation window,
configuration, taxonomy, denominator, and adjudication method.
