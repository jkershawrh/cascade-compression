# Platform pilot evidence

## What survived the frozen replay?

A deterministic replay of a 24-hour Kubernetes event capture compared three
ways to handle the same 817,479 observations. It used the
`0.2.0rc7` wheel with the `verified_repeat_only` profile and made zero
model calls. A recurring event passed the count check if the retained
observations represented its final observed count.

| Replay arm | Observations retained | Share suppressed | Recurring events missing their final count (of 215) |
|---|---:|---:|---:|
| A · retain all | 817,479 | 0% | 0 |
| B · simple deduplication, keep first | 5,930 | 99.3% | 145 |
| C · rc7 verified repeats only | 748,787 | 8.4% | 0 |

Arm C removed 68,692 observations. Keeping the first observation can lose
the updated count when an event recurs. A separate, post-hoc latest-wins
comparison reported 99.3% suppression and zero missed final counts under
the same definition. The choice of baseline matters. [Repeat-with-update design](repeat-with-update.md)

The replay clock compressed the deduplication window, favoring arm C, and
the capture recorded six failed polls. The check covered count preservation
among observed events. Determining whether an incident went unnoticed would
require independent incident truth. An earlier summary listed 30,966 retained
for a full-cascade arm (96.2% suppression). The retained same-capture report
does not substantiate that figure.

## What the live pilot reported

A separate operational summary estimated that ~620,000 of ~7.4 million raw
observations reached inference over a day, or about 92% avoided. Its
denominator was live traffic across signal sources. The frozen replay above
counted retained Kubernetes observations. The two measurements cannot be
combined to calculate inference cost or incident recall.

Pilot accounts also reported ~600 versus ~140 model calls per day, estimated
daily inference spend of ~$60 versus ~$14, and more than 18,000 shadow
rechecks with zero demotions in one reporting window. Those are historical operational observations.
Shadow checks measure agreement with a verifier. A savings claim still needs
a matched workload and observed total costs.

## How well did the classifier distinguish the labels?

An earlier classifier matched 3/70 labels (4.3%) on a deliberately selected
set of divergent cases. The rc21 few-shot classifier matched 67/160 (41.9%)
on the development set. Those samples used different selection criteria.

- **Tuning, 160 single-reviewer cases.** The platform-context prompt agreed
  on 125/160 (78.1%), including 93/95 needs_attention and 32/56
  known_pattern cases. It made four dangerous classification errors. The
  same cases informed prompt selection.
- **Held out, 100 cases with document-derived reference labels.** The
  classifier got 84/84 needs_attention and 0/16 known_pattern correct.
  Agreement was 84%, equal to always predicting needs_attention. The run
  reported zero dangerous classifications. There were no real_incident or
  routine_noise reference cases, so recall for those labels is unmeasured.

The single-reviewer pilot and the two-label holdout both fall short of the
independently adjudicated label-coverage challenge required for staging.
Raw observations, environment settings and reviewer records stay private.
The public register identifies the published wheel and the outstanding
qualification evidence. [Evidence register](evidence-register.md) · [Staging requirements](staging-evidence.md)
