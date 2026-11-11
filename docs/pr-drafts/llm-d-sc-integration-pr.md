# PR to llm-d-sc: Add Cascade Compression as a known integration

**Target repo:** https://github.com/llm-d-incubation/llm-d-semantic-classifier
**PR title:** docs: add Cascade Compression as a measured integration
**Files changed:** `README.md` (one section added)

---

## The diff (what gets added to their README)

````markdown
## Known integrations

### Cascade Compression

[Cascade Compression](https://github.com/jkershawrh/cascade-compression)
is a signal-compression engine that uses llm-d-sc as a margin-gated
CPU fast path within a three-tier classification pipeline. Cascade
routes eligible repeats through deterministic tiers. llm-d-sc can
serve confident matches on CPU, and ambiguous cases fall through to
the LLM.

In a historical experiment on 160 single-reviewer development cases,
llm-d-sc's anchor-based classifier achieved 55% agreement, with 91%
needs_attention recall and 4% known_pattern recall. Confident anchor
matches can be served on CPU without an LLM call. Ambiguous cases fall
through to the LLM's fact-extraction rubric.

A separate operational estimate of traffic avoiding inference covered
the whole pilot. It does not isolate the contribution of llm-d-sc.

```bash
helm install cascade ./chart \
  --set image.repository=your-registry.example.com/cascade \
  --set llm.url=https://your-gateway \
  --set sc.address=your-llm-d-sc-instance:50051
```

See the [Cascade Compression README](https://github.com/jkershawrh/cascade-compression)
and the
[platform pilot case study](https://github.com/jkershawrh/cascade-compression/blob/main/docs/case-study-platform-pilot.md)
for full architecture and measured results.
````

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
  operational signals
- 55% agreement on the 160 development cases, with 91% needs_attention
  recall and 4% known_pattern recall
- The margin-gated integration pattern: confident anchor matches
  served on CPU, ambiguous cases routed to the LLM

## Feedback for llm-d-sc

- The anchor-topk-mean approach with truth-informed anchors produced
  more distinct labels on the development cases than the earlier
  serialized-JSON experiment.
- Known_pattern recall remains low (4%) — embedding similarity between
  "expected operational pattern" and "genuine failure" is small.
  Domain-specific anchor enrichment or a secondary discrimination
  layer could address this.

## Changes

- Adds a "Known integrations" section to the README with Cascade
  Compression, including the measured results and install instructions.
```
