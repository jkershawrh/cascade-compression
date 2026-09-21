# Reproducible Cascade runtime benchmark

This synthetic benchmark separates three costs: the in-process deterministic
nano pipeline, the HTTP ingestion path, and a direct synchronous llm-d-sc gRPC
call. Cascade's HTTP endpoint queues survivor classification asynchronously, so
the HTTP latency is **not** end-to-end classification latency. None of these
measurements establishes classification accuracy, safe suppression, anchor
quality, or production value.

The optional mixed workload adds a **synthetic route oracle**: each 100-signal
batch has 50 routine info events, 20 low transient events, 10 pairs of repeated
medium events, 5 medium pattern events, 3 high events, and 2 info events with
an escalation pattern. Exactly 20 should survive and 80 should be handled
without inference. The benchmark aborts if any expected survivor is missing or
an unexpected survivor appears. This checks routing mechanics against a known
fixture; it is **not** independently adjudicated classification accuracy, and
the 80% handling figure is a fixture design, not a production compression claim.

Run the local nano smoke test without external services:

```bash
python -m cascade_compression.benchmarks.cascade_runtime \
  --output .benchmark-results/nano.json \
  --environment-label local-smoke \
  --iterations 100 --warmup 20 --batch-sizes 1 16 128
```

Run the local mixed-route correctness smoke test without external services:

```bash
python -m cascade_compression.benchmarks.cascade_runtime \
  --output .benchmark-results/mixed-local.json \
  --environment-label local-smoke \
  --mixed-only --mixed-iterations 100 --warmup 20
```

Add `--http-url http://127.0.0.1:8090 --mixed-http-samples 100` to check the
same survivor oracle through an isolated local API service. Each HTTP sample
sends 100 signals. The mixed HTTP benchmark is serial by design; it keeps
correctness and request-path cost coupled before concurrency sweeps. The service
and benchmark client should share an explicit CPU allocation when reporting
per-core throughput. The request returns before any asynchronous LLM work.

For publishable throughput, run the same revision in an **isolated** allocation
with a pinned CPU quota, at least 100 warm-up batches and 500 measured batches.
Record the exact image or commit, CPU model, quota, Python version, batch sizes,
and raw JSON. The harness reports per-core throughput only when it detects a
cgroup quota or you provide a verified `--cpu-cores` value. An unconstrained
developer-laptop result is a smoke test, not a hardware comparison.

Optional `--http-url` and `--sc-address` activate external tests. Point them
only at benchmark services you own; do not benchmark a shared production
endpoint. The semantic section intentionally distinguishes normalized cache
hits from unique misses and reports failures and tail latency for each
concurrency. Start with a low concurrency and increase only within the
endpoint's assigned CPU budget. The optional semantic path requires
`pip install -e ".[semantic-classifier]"`.

Raw output belongs under the ignored `.benchmark-results/` directory. Review
and sanitize any aggregate result before publication. This runtime benchmark
must be paired with a frozen, adjudicated end-to-end evaluation before making
compression or safety claims.
