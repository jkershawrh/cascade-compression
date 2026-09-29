# Domain pack guide

A domain pack maps a source system into Cascade's public `Signal` protocol and supplies a short
four-label classifier prompt. It does not place source credentials, deployment manifests, customer
records, or benchmark results in this repository.

## Package boundary

A domain-pack distribution normally provides:

1. a `BaseCollector` implementation;
2. a domain module declaring `DOMAIN`, `COLLECTOR_CLASS`, and `SYSTEM_PROMPT`; and
3. two Python entry points so Cascade can discover both pieces.

```toml
[project.entry-points."cascade_compression.collectors"]
example = "example_pack.collector:ExampleCollector"

[project.entry-points."cascade_compression.domains"]
example = "example_pack.domain"
```

The collector descriptor and domain loader validate these public seams. A private integration can
live in a separate package without changing the OSS engine.

## Map records to `Signal`

Collectors extend `BaseCollector` and return `Signal` objects from `collect()` and `collect_all()`.
The stable fields are:

- `signal_id`: UUID identity; omit it to generate one;
- `signal_type`: a stable domain event type;
- `severity`: `info`, `low`, `medium`, `high`, or `critical`;
- `source`: source-system identity;
- `content`: structured evidence, including a useful `message` when available;
- `labels`: low-cardinality matching context;
- `namespace`: tenant, account, team, or other grouping boundary; and
- `cluster`: source instance or location when that distinction affects identity.

```python
from cascade_compression.cascade.protocol import Signal
from cascade_compression.collectors.base import BaseCollector


class ExampleCollector(BaseCollector):
    name = "example"
    capabilities = ("batch",)
    signal_types = ("example.transaction",)

    def __init__(self):
        self._records = []

    def connect(self, config: dict) -> bool:
        self._records = list(config.get("records", []))
        return True

    def _map(self, record: dict) -> Signal:
        return Signal(
            signal_type="example.transaction",
            severity=record.get("severity", "info"),
            source="example-api",
            namespace=str(record.get("account_id", "unknown")),
            cluster=str(record.get("region", "")),
            content={
                "message": record.get("message", "Transaction observed"),
                "amount": record.get("amount", 0),
            },
            labels={"domain": "example"},
        )

    def collect(self) -> list[Signal]:
        records, self._records = self._records[:500], self._records[500:]
        return [self._map(record) for record in records]

    def collect_all(self) -> list[Signal]:
        return [self._map(record) for record in self._records]
```

Use stable identity fields. Deduplication cannot distinguish two genuine incidents if the collector
maps them to the same source, cluster, namespace, and content.

## Define the domain module

```python
from example_pack.collector import ExampleCollector

DOMAIN = "example"
COLLECTOR_CLASS = ExampleCollector
SYSTEM_PROMPT = """Classify the signal as exactly one of: routine_noise,
known_pattern, needs_attention, real_incident. Answer with one label only."""
```

The prompt is configuration, not ground truth. Keep its revision with evaluation artifacts. Do not
assume that a terse prompt, a model's general knowledge, or agreement between two classifiers is
evidence of accuracy.

## Run and replay

After installing the domain-pack distribution:

```bash
cascade-run --domain example --llm-url http://127.0.0.1:8000/v1
cascade-replay --domain example --data held-out.json --export-route-ledger replay.json
```

Historical replay exercises mechanics and can produce candidate patterns. It does not automatically
make an agent safe to activate. Promotion still requires qualifying feedback, and an effectiveness
claim still requires a frozen independently adjudicated holdout.

## Evaluation checklist

- [ ] Collector and domain entry points load from an isolated wheel installation.
- [ ] Collector returns valid `Signal` objects with stable identity and source boundaries.
- [ ] High/critical and escalation fixtures survive the deterministic path.
- [ ] A mixed route-oracle test confirms the expected survivors.
- [ ] All classifier arms run over the same frozen corpus.
- [ ] At least two independent reviewers complete adjudication.
- [ ] `cascade-evaluate` reports per-label precision/recall/F1, coverage, calibration, and dangerous
      misses.
- [ ] The selected arm meets the fixed `oss-rc-a-v1` gate in `cascade-stage-evidence`.
- [ ] Raw records, credentials, and environment-specific evidence remain outside the public repo.

## Bundled examples

The OSS package includes generic finance, healthcare, insurance, Kubernetes, macOS, retail, and
telecom domain modules plus synthetic generators for mechanics testing. They are examples, not
published customer outcomes. No bundled synthetic compression percentage should be used as a
production forecast.
