# Benchmark results — what each artifact is

This directory holds raw output from benchmark and cascade runs. Files are kept
verbatim as they were produced, including field names that have since changed
meaning. **Read this file before quoting any number out of them.**

## Cascade run artifacts

Two files record end-to-end cascade runs. Both **predate the hardened engine**
(zero-FN gate, shadow validation, GCL audit loop, 72h TTL — see
[promotion-guidelines.md](../../docs/promotion-guidelines.md)), so neither one
demonstrates the current safety behaviour.

| File | Date | Signals | Compression | Engine |
|---|---|---:|---:|---|
| `cascade-k8s-final-state.json` | 2026-08-06 | 68,686,111 | 99.47% | pre-hardening |
| `cascade-nano-plateau-241k.json` | 2026-08-05 | 243,311 | 72.46% | pre-hardening |

### `fn_count` / `fn_rate` do not mean false negatives in these files

This is the most important caveat in the directory.

In `cascade-k8s-final-state.json`, `stats.fn_count` is **68,129,834** and
`stats.fn_rate` is **99.19**. Those are *noise-filtered count and rate*, not
false negatives:

```
68,129,834 / 68,686,111 = 99.19%
```

The field was renamed when the hardened engine landed. In the current code
(`cascade_compression/bridge.py`, `get_stats`), the same names mean something
entirely different:

- `fn_evaluated` — suppressed signals re-checked by shadow validation
- `fn_count` — of those, how many the oracle said were actually important
- `fn_rate` — `fn_count / fn_evaluated * 100`
- `fn_status` — `"measured"` only when `fn_evaluated > 0`, otherwise `"not_measured"`

A current run with no shadow traffic reports `fn_count: null` and
`fn_status: "not_measured"` rather than zero. If you see a large `fn_rate` in an
archived artifact, it is the old noise-filter metric.

### `cascade-nano-plateau-241k.json` records real false negatives

This file **does** have a genuine `stats.false_negatives: 10204` against 243,311
signals, alongside `rubric.overall: "green"` and agents graded green at
`fn_rate: 1.0`. That is not a contradiction to explain away — it is what the
pre-hardening promotion rubric permitted. It is retained precisely because it
shows the behaviour that motivated the zero-FN gate. Do not cite this run as
evidence of the current engine's false-negative behaviour.

## Model benchmark artifacts

`benchmark-*.json` files are model/lever benchmark sweeps. `manifest.json` is the
run index — each entry carries `run_id`, start/finish timestamps, the lever and
industries exercised, models, API call counts, error counts, and the results file
it produced. Files ending `-evaluated.json` are graded against the rubric
thresholds in `../benchmark_matrix.yaml`.

`tier1-rubric-20260803.json` records `gate: "FAIL"` with 5 red metrics. It is a
real failing gate from 2026-08-03, kept for the record rather than removed.

## Provenance

Result files and `data/hardware_profiles.json` / `data/benchmark_matrix.json`
retain the cluster and namespace identifiers that were in effect when the
measurements were taken. These are deliberately **not** genericized — they say
where a number came from, and rewriting them would falsify provenance.

## Not in this directory

The 142.4M-signal Kubernetes replay is not published here. See
[docs/REPLAY-METHODOLOGY.md](../../docs/REPLAY-METHODOLOGY.md) for how that run
was performed and what can and cannot be checked from this repository.
