# Replay methodology — the 142.4M-signal Kubernetes run

The headline figure quoted across this repository — **142.4M signals replayed at
99.1% compression** — comes from a replay run whose raw artifact is **not
published in this repository**. This document states how the run was performed
and, just as importantly, what a reader cannot verify from the repo alone.

If you need a number you can check against a committed file today, use the
68.7M-signal run in
[`benchmarks/results/cascade-k8s-final-state.json`](../benchmarks/results/cascade-k8s-final-state.json)
(99.47%, 2026-08-06, pre-hardening engine) and read
[`benchmarks/results/README.md`](../benchmarks/results/README.md) first — several
of its field names have since changed meaning.

## What a replay is

A replay reprocesses historical signals that were already collected from
production clusters, rather than reading a live stream. The cascade pipeline,
promotion engine, and LLM classification path are the same code as a live run;
only the collector differs — it reads from a stored corpus instead of the
Kubernetes API. Replay is how a compression figure gets measured over a signal
volume that would otherwise take weeks of wall-clock time to accumulate.

Replay is therefore **not** the same claim as a live soak. It demonstrates that
the cascade compresses a recorded distribution of signals at a given rate. It
does not demonstrate sustained behaviour against a moving production
distribution — that is what the live-cluster figures are for.

## Run parameters

> **These fields need to be filled in from the run records before this document
> is published.** Each one is a claim a reader is entitled to check.

| Parameter | Value |
|---|---|
| Source clusters | `TODO — how many, and what kind of workload` |
| Collection window | `TODO — date range the replayed signals were captured over` |
| Replay date | `TODO` |
| Engine version / commit | `TODO — hardened or pre-hardening?` |
| Collector | `TODO — e.g. cascade_compression/collectors/kubernetes.py` |
| Corpus source and size | `TODO — where the stored signals came from, on-disk size` |
| Micro-tier model | `TODO — e.g. granite-3-2-8b-instruct-cpu` |
| Hardware | `TODO — e.g. Intel Xeon 6767P, 128 cores` |
| Signals processed | 142.4M |
| Compression | 99.1% |
| Signals reaching the LLM | 9,685 (0.007%) |
| Agents activated | 3 |
| Shadow demotions | 0 |
| GCL verdicts | 1 FAILS |

## Reproducing it

The replay path itself is in the repository and is runnable:

```bash
cascade-replay --domain kubernetes \
  --data <path-to-signal-corpus> \
  --llm-url https://your-llm \
  --state-file /tmp/replay-state.json \
  --export-memories /tmp/replay-memories.json
```

What is **not** in the repository is the 142.4M-signal corpus itself. It contains
raw production events from live clusters — namespaces, resource names, node
identifiers, and event messages — and is not ours to publish. Anyone wanting to
reproduce the figure needs to point `--data` at their own captured signal stream;
the compression ratio they get will depend on their signal distribution, which is
the honest answer.

## What the compression number depends on

Two things materially move the compression ratio, and both should be stated
whenever the figure is quoted:

1. **The signal distribution.** 99.1% reflects a Kubernetes event stream heavily
   dominated by a small number of recurring event types (see the `fn_types`
   breakdown in `cascade-k8s-final-state.json`, where the top type alone accounts
   for tens of millions of signals). A flatter distribution compresses less.

2. **How the oracle model is prompted.** As recorded in
   [model-benchmarks.md](model-benchmarks.md), retuning the classification prompt
   for the same model and the same signals moved the measured noise rate from
   0.9% to 37.3%. Compression is a property of a *(signal stream, model, prompt)*
   triple, not of the framework alone.

## On "zero false negatives"

The zero-false-negative claim attached to this run means **zero shadow-detected
disagreements with the oracle model** — the LLM re-checked a sample of suppressed
signals and did not flag any as important. It does not mean zero signals were
wrongly suppressed in an absolute sense. The oracle is a small CPU model scoring
14/20 on the 20-signal grading set in [model-benchmarks.md](model-benchmarks.md),
so the guarantee is bounded by the oracle's own accuracy.

This is a real safety property — the promotion gate in
`cascade_compression/cascade/promotion.py` enforces `max_false_negative: 0.0` at
200+ samples before an agent activates — but it is a relative one, and it should
be stated that way.
