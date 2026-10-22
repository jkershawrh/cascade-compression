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
  message digest proof, medium severity, kubernetes domain, Pod kind.
- On an unchanged repeat: suppress (identical to today).
- On a count increase for the same identity: suppress the re-delivery AND
  refresh the retained observation's count and timestamp, with the transition
  recorded in the decision evidence (`verified repeat, count 5 -> 12`).
- On a count decrease: deliver (non-monotonic counts are treated as suspect,
  fail-open).
- On any other change (message digest, identity fields): deliver.
- Fail-open severity gates are unchanged: high/critical signals are never
  suppressed or updated away by this profile.

## Safety properties (tested adversarially)

1. The retained observation always reflects the event's final observed count
   — the pre-registered dangerous-miss definition holds by construction.
2. Every suppression decision records the count transition it folded in;
   updates are auditable, not silent.
3. Decreasing counts, changed messages, and new event identities deliver.
4. The profile is mutually exclusive with the other nano profiles.

## Claim boundary

This profile raises deterministic-tier suppression on recurrence-heavy
streams toward what a latest-wins filter achieves, under the same identity
admission and provenance contracts. It does not establish field
effectiveness, and learned tiers remain separately gated.
