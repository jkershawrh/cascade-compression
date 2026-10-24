# Verified repeat-with-update (rc8 candidate design)

## Finding that motivates this

The Phase 2 pre-registered three-arm comparison (817,479 observations, 24h,
one cluster) measured the verified-repeat-only profile at 8.4% suppression
with zero dangerous misses, while a post-hoc latest-wins simple filter
achieved 99.3% with zero dangerous misses under the same definition. The gap:
the profile treats a count increment on an existing event as new information
to deliver, when an operator needs only the refreshed state.

## Semantics

A new nano profile, `verified_repeat_update`, extending the identity
admission of `verified_repeat_key`:

- Identity for matching excludes `count` (everything else is identical:
  severity, domain, kind, involved object, event_uid, reason, message digest,
  scope). Admission gates are unchanged: no payload trust flags, complete
  message digest proof and valid medium severity. The update profile accepts
  any non-empty kind and is domain-neutral; domain and type remain part of
  identity.
- On an unchanged repeat: suppress (identical to today).
- Within a single batch, count growth may fold into the first retained
  observation. HTTP survivors, archived memory and queued classifier evidence
  receive that updated content; the decision records the transition.
- Across batches, a count increase is delivered as a new observation. An
  acknowledged response cannot be updated by mutating a private agent object.
  Old and new memory snapshots remain distinct and prior verification is not
  transferred. Subsequent unchanged repeats may be suppressed.
- On a count decrease: deliver (non-monotonic counts are treated as suspect,
  fail-open).
- On any other change (message digest, identity fields): deliver.
- Fail-open severity gates are unchanged: high/critical signals are never
  suppressed or updated away by this profile.

## Safety properties (tested adversarially)

1. Count growth reaches downstream consumers and memory: within-batch updates
   appear in the survivor; later growth is delivered explicitly. This is a
   tested behavior contract, not independent incident-safety proof.
2. Every suppression decision records the count transition it folded in;
   updates are auditable, not silent.
3. Decreasing counts, changed messages, and new event identities deliver.
4. The profile is mutually exclusive with the other nano profiles.

## Qualified learned suppression (opt-in)

`CASCADE_NANO_PROFILE=qualified_repeat_update` combines the same repeat
handling with current local qualification checks for the bridge's own learned
type and context suppressors. It is not the default and does not change the
bounded `verified_repeat_update` profile into an adaptive suppressor.

Admission requires a recognized agent instance, complete source/scope, valid
raw severity, non-protected labels, current rule/configuration/model bindings,
200 successful noise samples with zero important classifications, and valid
activation TTL and any applicable human approval. Missing or changed evidence,
negative feedback, invalid severity and high/critical inputs retain work.
Classifier-derived qualification is not independent ground truth. Contextual
suppression remains disabled when the human gate is configured.

Only `SUPPRESS` decisions can use that authority hook. Arbitrary `DROP`,
`CLASSIFY` or payload trust flags do not authorize removal in either profile.
Dedup windows are finite, non-negative durations; per-domain overrides are
configured with `CASCADE_DEDUP_WINDOW_<DOMAIN>`. Zero disables the window.
Dedup state is process-local, so restart may redeliver observations.

## Claim boundary

This profile raises deterministic-tier suppression on recurrence-heavy
streams toward what a latest-wins filter achieves, under the same identity
admission and provenance contracts. It does not establish field
effectiveness, and learned tiers remain separately gated.
