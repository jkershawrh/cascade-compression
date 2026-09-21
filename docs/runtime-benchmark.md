# Reproducible Cascade runtime benchmark

This synthetic benchmark separates three costs: the in-process deterministic
nano pipeline, the HTTP ingestion path, and a direct synchronous llm-d-sc gRPC
call. Cascade's HTTP endpoint queues survivor classification asynchronously, so
the HTTP latency is **not** end-to-end classification latency. None of these
measurements establishes classification accuracy, safe suppression, anchor
quality, or production value.

Run the local nano smoke test without external services:

```bash
python -m cascade_compression.benchmarks.cascade_runtime \
  --output .benchmark-results/nano.json \
  --environment-label local-smoke \
  --iterations 100 --warmup 20 --batch-sizes 1 16 128
```

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
