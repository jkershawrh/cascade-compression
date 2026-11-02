# PR to llm-d-sc: Add Cascade Compression as a known integration

**Target repo:** https://github.com/llm-d-incubation/llm-d-semantic-classifier
**PR title:** docs: add Cascade Compression as a known integration
**Files changed:** `README.md` (one section added after existing integrations/examples)

---

## The diff (what gets added to their README)

```markdown
## Known integrations

### Cascade Compression

[Cascade Compression](https://github.com/jkershawrh/cascade-compression)
uses llm-d-sc as an optional semantic-classification fast path within a
three-tier signal-compression engine. Cascade routes the repetitive
majority of operational signals (Kubernetes events, Ansible task
outcomes, IoT telemetry) through deterministic tiers and reserves LLM
capacity for what's genuinely ambiguous — llm-d-sc serves as the CPU
classification layer for the traffic that survives deterministic
filtering.

In a 24-hour platform pilot, the combined system eliminated 92% of raw
observations before they reached LLM inference, with zero dangerous
misses verified against a three-arm comparison. Cascade retains
authority over confidence policy, fallback, promotion, demotion, and
provenance; llm-d-sc provides the semantic classification within that
governed lifecycle.

```bash
helm install cascade ./chart \
  --set llm.url=https://your-gateway \
  --set sc.address=your-llm-d-sc-instance:50051
```

See the [Cascade Compression README](https://github.com/jkershawrh/cascade-compression)
for the full architecture, and the
[platform pilot case study](https://github.com/jkershawrh/cascade-compression/blob/main/docs/case-study-platform-pilot.md)
for measured results.
```

---

## PR description

```markdown
## Summary

Adds Cascade Compression as a known integration of llm-d-sc. Cascade is
an open-source signal-compression engine that uses llm-d-sc as its
semantic-classification fast path, wrapping it in a governed lifecycle
(qualified suppression, shadow validation, graduated authority, audit).

## Why this is useful to llm-d-sc users

Demonstrates llm-d-sc running in a production-shaped pipeline at volume
(~7.4M signals/day on the referenced deployment), with measured results.
Also shows the gRPC integration pattern for projects that want to use
llm-d-sc as a component rather than a standalone service.

## Changes

- Adds a "Known integrations" section to the README with Cascade
  Compression, including install instructions and a link to the
  measured case study.
