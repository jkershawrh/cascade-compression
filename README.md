# Cascade Compression

Cascade Compression is a domain-agnostic, three-tier signal compression engine for CPU inference.
It learns deterministic rules for routine traffic, continuously validates those rules against a
model oracle, and escalates uncertain or important signals. One false negative demotes an active
rule immediately.

This repository is the clean OSS distribution. It contains reusable engine code, public contracts,
generic collectors, synthetic examples, and reproducible tests. It does not contain production
deployment configuration, customer data, raw operational evidence, or environment-specific
collectors.

## Install and test

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Run the API and dashboard locally:

```bash
python -m uvicorn cascade_compression.service:app --port 8090
```

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
