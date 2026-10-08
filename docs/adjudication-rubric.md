# Independent classification adjudication rubric

This rubric creates ground truth for evaluation; it does not tune the evaluated classifier. Each
reviewer works independently, without seeing generative, semantic, or hybrid predictions. Base the
decision on the frozen signal and cited external evidence available at the review cutoff.

## Label decision order

Apply the first supported outcome in this order:

1. **`real_incident`** — evidence confirms an active harmful, blocking, security, availability, or
   integrity condition. Severity text alone is not confirmation.
2. **`needs_attention`** — the event is abnormal, ambiguous, missing decisive context, or warrants
   investigation, but active incident-level impact is not established. When uncertainty could make
   suppression unsafe, use this label.
3. **`known_pattern`** — the current evidence matches a documented pattern with an established
   interpretation and bounded conditions. Cite that authoritative record. Recurrence, similarity,
   or a familiar signal type by itself is insufficient.
4. **`routine_noise`** — the evidence shows expected behavior by design, successful progress, or a
   normal status update that requires no investigation. Do not use this label merely because the
   event is common.

`routine_noise` and `known_pattern` are suppressible. `needs_attention` and `real_incident` are
actionable. The adjudicator rejects a receipt whose label and actionability conflict.

## Evidence requirements

- Every receipt declares `cascade.review-receipt.v1alpha1` and records the exact frozen signal
  digest, reviewer reference, UTC review time, rationale, truth source, and evidence digest.
- `known_pattern` must use `source: authoritative_record` and a non-empty `source_record_ref`. If no
  authoritative record exists, it is not release-grade `known_pattern` truth.
- `independent_human_review` may support the other labels. Use `authoritative_record` whenever a
  timestamped incident, change, runbook, or service record directly establishes the outcome.
- Missing context is not evidence of safety. Choose `needs_attention` when the absent context could
  change a suppressive decision into an actionable one.
- `expected_memory` is a separate judgment: set it when retaining the case would provide useful
  durable precedent, even if the event itself is no longer actionable.

## Review protocol

Two reviewers complete the entire frozen corpus without discussing cases or seeing evaluated model
outputs. The merge command accepts consensus and sends every disagreement to a third, distinct
reviewer. Do not replace a disputed case, change the frozen corpus, or use model agreement as truth.
The holdout manifest digest, review receipts, and adjudication summary must all bind to the same
signal evidence before the evaluator can report `decision_grade`.

Balanced or challenge sampling is suitable for per-label recall, balanced accuracy, and safety
testing. It does not preserve production prevalence, so its overall accuracy and label mix must not
be presented as natural production rates.

## Outcome evidence for memory-assisted suppression

Classification receipts are not sufficient to authorize future suppression. A separate private
`cascade.outcome-evidence.v1alpha1` receipt records what happened after the signal: whether an
intervention occurred, whether service impact occurred, how the condition resolved, the complete
observation window, and an authoritative source reference. The receipt is independently recorded,
content-addressed, and bound to the frozen signal digest.

Cascade derives `benign` only when the observation shows no intervention, no service impact, and
self-resolution. Automated recovery, degraded service, an ongoing condition, an unknown value, or
any operator intervention remains actionable. The public repository contains only the schema and
validation mechanics; completed receipts and internal source references remain private.
