# PR to llm-d-sc: Add Cascade Compression as a known integration

**Target repo:** https://github.com/llm-d-incubation/llm-d-semantic-classifier
**PR title:** docs: add Cascade Compression as a measured integration
**Files changed:** `README.md` (one section added)

---

## The diff (what gets added to their README)

```markdown
## Known integrations

### Cascade Compression

[Cascade Compression](https://github.com/jkershawrh/cascade-compression)
is a signal-compression engine that uses llm-d-sc as a margin-gated
CPU fast path within a three-tier classification pipeline. Cascade
routes the repetitive majority of operational signals (Kubernetes
events, Ansible task outcomes, IoT telemetry) through deterministic
tiers; llm-d-sc serves the confident remainder on CPU; the LLM handles
only what anchor similarity can't discriminate.

**Measured on 160 adjudicated production signals** (Kubernetes event
stream, multi-tenant demo platform): llm-d-sc's anchor-based
classification achieved 55% agreement with human adjudication —
equivalent to a 3-model judge panel (55.6%). Needs_attention recall:
91%. The integration is deployed as a margin-gated fast path: confident
anchor matches are served on CPU (zero model calls); ambiguous cases
fall through to the LLM's fact-extraction rubric.

In the full pipeline (nano tier + value rules + llm-d-sc + LLM): 92%
end-to-end signal elimination with zero dangerous misses on a 24-hour
platform pilot.

```bash
helm install cascade ./chart \
  --set llm.url=https://your-gateway \
  --set sc.address=your-llm-d-sc-instance:50051
```

See the [Cascade Compression README](https://github.com/jkershawrh/cascade-compression)
and the
[platform pilot case study](https://github.com/jkershawrh/cascade-compression/blob/main/docs/case-study-platform-pilot.md)
for full architecture and measured results.
```

---

## PR description

```markdown
## Summary

Adds Cascade Compression as a measured integration of llm-d-sc.
Cascade is an open-source signal-compression engine that deploys
llm-d-sc as a margin-gated CPU classification fast path, wrapping it
in a governed lifecycle (qualified suppression, shadow validation,
graduated authority, audit).

## What this demonstrates

- llm-d-sc's anchor-based classification running on structured
  operational signals at production volume (~7.4M signals/day on the
  referenced deployment)
- 55% agreement with human adjudication on 160 production signals —
  equivalent to a 3-model judge panel
- 91% needs_attention recall — effective at identifying genuinely
  important signals
- The margin-gated integration pattern: confident anchor matches
  served on CPU, ambiguous cases routed to the LLM

## Feedback for llm-d-sc

- The anchor-topk-mean approach discriminates effectively on
  structured signals with truth-informed anchors (an improvement over
  earlier testing with serialized JSON blobs)
- Known_pattern recall remains low (4%) — embedding similarity between
  "expected operational pattern" and "genuine failure" is small.
  Domain-specific anchor enrichment or a secondary discrimination
  layer could address this.

## Changes

- Adds a "Known integrations" section to the README with Cascade
  Compression, including the measured results and install instructions.
