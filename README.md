# Cascade Compression

Cascade Compression is a domain-agnostic, three-tier signal compression engine for CPU inference.
It learns deterministic rules for routine traffic, continuously validates those rules against a
model oracle, and escalates uncertain or important signals. One false negative demotes an active
rule immediately.

The project is not another classifier. It is the governed lifecycle around classification: fast
nano agents remove duplicates, transient conditions, known patterns, and validated repetitive
traffic; model capacity is reserved for signals that are new or ambiguous; repeated judgments can
be promoted into inspectable, expiring, and revocable rules; and surviving signals can become
durable memory. [llm-d-sc](https://github.com/llm-d/llm-d-semantic-router) can be used as an optional
semantic classification fast path, while Cascade retains authority over confidence policy,
fallback, custom anchor engineering, promotion, demotion, provenance, memory, and federation.

This repository is the clean OSS distribution. It contains reusable engine code, public contracts,
generic collectors, synthetic examples, and reproducible tests. It does not contain production
deployment configuration, customer data, raw operational evidence, or environment-specific
collectors.

## Install and test

The first supported OSS release is `0.1.0`. The Python package supports Python 3.9 through 3.13.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

To evaluate the optional llm-d-sc gRPC adapter, install the semantic extra:

```bash
pip install -e ".[dev,semantic-classifier]"
```

Run the API and dashboard locally:

```bash
python -m uvicorn cascade_compression.service:app --port 8090
```

Then open <http://localhost:8090>. The service starts without an LLM for deterministic and
synthetic evaluation; configure an OpenAI-compatible endpoint only when testing model-backed
classification.

## How signal compression works

1. Collectors normalize domain records into the stable `Signal` protocol.
2. Deterministic nano agents deduplicate, suppress safe transients, protect severe signals, and
   tag known patterns.
3. Every survivor is preserved before model-backed classification begins.
4. The classifier uses one of four policies: `generative`, `compare`, `semantic`, or `hybrid`.
   Hybrid mode grants llm-d-sc authority only when its ranked-label margin clears Cascade's policy
   gates; otherwise the generative classifier remains the safe fallback.
5. The corpus analyzer turns repeated classifications into candidate rules. Promotion requires
   sufficient evidence and zero known important misses; active rules are shadow-checked, expire,
   and demote immediately on a false negative.
6. A separate anchor-engineering loop can propose taxonomy improvements from low-margin patterns.
   Candidates require adjudicated held-out evaluation and explicit approval before activation.
7. Optional memory and federation retain important survivors and share them across Cascade
   instances without changing the compression contract.

See [the architecture](docs/architecture.md) and [the event workflow](docs/event-workflow.md) for
the full mechanics. [Custom anchor engineering](docs/anchor-engineering.md) documents the separate
proposal, evaluation, approval, activation, and rollback lifecycle.

## Optional llm-d-sc integration

The default is `generative`, so installing or running Cascade does not require llm-d-sc. A safe
rollout normally progresses from observation to bounded authority:

```bash
# Observe agreement; the generative result remains authoritative.
CASCADE_CLASSIFIER_MODE=compare \
CASCADE_SC_ADDRESS=127.0.0.1:50051 \
CASCADE_SC_TAXONOMY=config/cascade-sc-taxonomy.json \
python -m uvicorn cascade_compression.service:app --port 8090

# Grant semantic authority only above Cascade's confidence margins.
CASCADE_CLASSIFIER_MODE=hybrid \
CASCADE_SC_ADDRESS=127.0.0.1:50051 \
CASCADE_SC_TAXONOMY=config/cascade-sc-taxonomy.json \
CASCADE_SC_HYBRID_MARGIN=0.20 \
CASCADE_SC_HYBRID_SUPPRESS_MARGIN=0.40 \
python -m uvicorn cascade_compression.service:app --port 8090
```

`semantic` mode is available for deployments that intentionally want llm-d-sc to classify without
a generative endpoint; Cascade still applies margin and severity gates, and service errors produce
explicit failures rather than silent drops.
The generated protocol client is pinned by the `semantic-classifier` optional dependency group.
Loopback development may use plaintext gRPC. Remote endpoints require `CASCADE_SC_TLS=1`; optional
`CASCADE_SC_TLS_CA`, `CASCADE_SC_TLS_CERT`, and `CASCADE_SC_TLS_KEY` configure private CAs or mTLS.

## Governance boundary

Safety controls such as severity protection, promotion evidence, shadow validation, agent expiry,
and immediate demotion are built into Cascade. An external ledger and GCL auditor are optional
assurance layers: configure them when independent receipts or policy verdicts are required. They
are not prerequisites for local compression, classification, memory, or llm-d-sc evaluation.

Container images for tagged releases are published at
`ghcr.io/jkershawrh/cascade-compression`. Release artifacts include an SPDX SBOM and signed GitHub
artifact attestations. See `RELEASING.md` for verification and release policy.

## Public contracts

`contracts/manifest.json` versions the signal, decision, collector plugin, memory, promotion, and
value-evidence contracts. Stable contracts follow semantic versioning; compatibility rules are in
`docs/contract-compatibility.md`.

## Repository boundary

- Public: reusable source, contracts, generic configuration, synthetic fixtures, and tests.
- Private elsewhere: deployment overlays, secrets, cluster inventories, raw memories, biographies,
  replay artifacts, classifier reviews, work logs, and customer economics.

Please report security issues through GitHub private vulnerability reporting rather than a public
issue. See `SECURITY.md`.

Licensed under Apache-2.0.
