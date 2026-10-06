# Reproducible Cascade runtime benchmark

This synthetic benchmark separates four costs: the in-process deterministic
nano pipeline, the HTTP ingestion path, a direct synchronous llm-d-sc gRPC
call, and exact-precedent memory recall. Cascade's HTTP endpoint queues survivor
classification asynchronously, so
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

Add `--recall-sizes 100 1000 10000` to measure memory retrieval scaling. The
recall cell reports cold-start and steady-state tail latency and fails if the
known stored precedent is not ranked first. This is a retrieval mechanics
oracle, not a relevance judgment over human-authored queries.

Add `--http-url http://127.0.0.1:8090 --mixed-http-samples 100` to check the
same survivor oracle through an isolated local API service. Each HTTP sample
sends 100 signals. The mixed HTTP benchmark is serial by design; it keeps
correctness and request-path cost coupled before concurrency sweeps. The service
and benchmark client should share an explicit CPU allocation when reporting
per-core throughput. The request returns before any asynchronous LLM work.

For publishable throughput, run the same revision in an **isolated** allocation
with a pinned CPU quota, at least 100 warm-up batches and 500 measured batches.
Record the exact image or commit, CPU model, quota, Python version, batch sizes,
and raw JSON. The artifact records both start and completion timestamps so the staging gate can
bind the complete benchmark to its declared audit window. The harness reports per-core throughput
only when it detects a
cgroup quota or you provide a verified `--cpu-cores` value. An unconstrained
developer-laptop result is a smoke test, not a hardware comparison.

The fixed `oss-rc-runtime-v1` staging profile requires 500 baseline samples/iterations, 100 warm-up
runs, 500 mixed-route iterations, 100 mixed HTTP samples, and HTTP plus semantic concurrency of at
least 8. It requires the nano batch-1, serial HTTP, mixed nano/HTTP, llm-d-sc normalized-hit and
unique-miss, and recall-at-1K/10K cells. Conservative acceptance limits are: nano batch-1 p95 at
most 5 ms and at least 1,000 signals/s/core; serial HTTP p95 at most 100 ms; mixed HTTP p95 at most
1,000 ms per 100-signal batch; semantic cache-hit p95 at most 250 ms; semantic unique-miss p95 at
most 2,000 ms; and recall p95 at most 50 ms at 1K and 250 ms at 10K memories. These are release
policy thresholds, not universal hardware guarantees.

Optional `--http-url` and `--sc-address` activate external tests. Point them
only at benchmark services you own; do not benchmark a shared production
endpoint. The semantic section intentionally distinguishes normalized cache
hits from unique misses and reports failures and tail latency for each
concurrency. Start with a low concurrency and increase only within the
endpoint's assigned CPU budget. The optional semantic path requires
`pip install -e ".[semantic-classifier]"`.

For an llm-d-sc runtime comparison, bind the benchmark to the exact runtime and
artifact contract rather than a floating image tag:

```bash
python -m cascade_compression.benchmarks.cascade_runtime \
  --output .benchmark-results/llm-d-sc-v02.json \
  --environment-label isolated-cpu \
  --sc-address llm-d-sc.example:50051 \
  --sc-source-revision 5c4bb80b732065c765d8ac2ae75e07fba19546cd \
  --sc-expected-model-revision c5f55ef419d268ba843c544dc00988d1e9878044 \
  --sc-expected-tokenizer-revision c5f55ef419d268ba843c544dc00988d1e9878044 \
  --sc-expected-taxonomy-revision cascade-classification-anchors-v1 \
  --sc-expected-classifier-id cascade-classification \
  --sc-scoring-mode anchor_cosine
```

The run aborts during warmup if the response identity, model revision,
taxonomy revision, label set, ranking order, or declared score semantics do not
match the contract. Classification-head probability mode is a separate
experiment and must not reuse anchor-cosine confidence thresholds without an
adjudicated recalibration.

After collecting the same matrix from the old and candidate runtimes, apply the
runtime-only regression gate:

```bash
python -m cascade_compression.benchmarks.compare_semantic_runtime \
  .benchmark-results/llm-d-sc-old.json \
  .benchmark-results/llm-d-sc-v02.json \
  --output .benchmark-results/llm-d-sc-v02-comparison.json
```

The default gate requires identical classifier artifact identity, 100% request
success, no error increase, no more than 10% p95 regression, and at least 90%
of baseline throughput in every workload/concurrency cell. Passing this gate
does not establish classification quality or safe compression.

Raw output belongs under the ignored `.benchmark-results/` directory. Review
and sanitize any aggregate result before publication. This runtime benchmark
must be paired with a frozen, adjudicated end-to-end evaluation before making
compression or safety claims.
