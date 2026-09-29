# Cascade compression: measurement and mathematical model

This note defines Cascade's metrics and the assumptions behind its safety controls. Equations are
models, not production results. Public synthetic fixtures demonstrate mechanics; operational
effectiveness must be measured on a frozen, independently adjudicated workload.

## 1. Signal and decision sets

For a finite evaluation window, let the input set be (S). The deterministic pipeline partitions
it into handled signals (H_n) and survivors (R_n):

```text
S = H_n ∪ R_n,       H_n ∩ R_n = ∅
```

The nano handling ratio is:

```text
rho_n = |H_n| / |S|
```

This is a volume measure. It is not accuracy and it is not a safety score. A valid report also
states the denominator, input identity policy, observation window, configuration revision, and
ground-truth method.

Survivors may be classified by a model-backed policy. Let (A) be the set receiving an
authoritative classification and (U) the set that abstains, fails, times out, or remains queued.
Classifier coverage is:

```text
coverage = |A| / |R_n|
```

Model work is asynchronous in the current service. HTTP ingestion latency, classifier latency,
and time-to-authoritative-result are therefore different measurements.

## 2. Classification quality

For each label (l), report:

```text
precision_l = TP_l / (TP_l + FP_l)
recall_l    = TP_l / (TP_l + FN_l)
F1_l        = 2 * precision_l * recall_l / (precision_l + recall_l)
```

Balanced accuracy is the mean recall over represented labels. Macro F1 is the mean per-label F1.
Overall accuracy alone is insufficient when routine traffic dominates the corpus.

Cascade treats an authoritative prediction as a dangerous miss when adjudicated truth is
`needs_attention` or `real_incident` but the authoritative prediction is `routine_noise` or
`known_pattern`. The staging-qualified gate requires zero observed dangerous misses. “Zero
observed” does
not mean the unknown population rate is zero.

Calibration is reported with top-label Brier score and expected calibration error (ECE). These
metrics require real confidence values on the same frozen records used for accuracy.

## 3. What zero observed misses means

If (N) examples are independent and representative, and no miss is observed, the one-sided
Clopper-Pearson upper bound on an unknown miss probability (p) at confidence (1-alpha) is:

```text
p_upper = 1 - alpha^(1/N)
```

At (N=200) and 95% confidence, this is approximately 1.49%. The bound does not apply when labels
are correlated, sampling is biased, reviewer truth is unreliable, or feedback covers only an
unknown subset. This is why Cascade reports sample support and requires held-out adjudication;
the zero-known-miss promotion policy is an operational guard, not a proof of zero population risk.

## 4. Learned-rule activation

A candidate rule accumulates reviewed outcomes. In the default promotion configuration it cannot
activate until it reaches the minimum sample count and has no known important outcomes in its
qualifying sample. Activation is reversible:

- a confirmed false negative causes immediate demotion;
- sampled shadow disagreement can cause demotion;
- a configured failed external audit verdict can cause demotion; and
- activation expires after its TTL and must re-qualify.

Adding an active suppression rule cannot increase the survivor count for the same fixed input and
pipeline ordering. It does **not** follow that compression rises monotonically over calendar time:
traffic changes, overlapping rules, expiry, and demotion can all reduce measured compression.

## 5. Memory strength

A memory begins with severity-dependent strength (phi_0) in ([0,1]). A reinforcement applies:

```text
phi_next = phi + alpha * (1 - phi)
```

After (n) reinforcements with constant (alpha):

```text
phi_n = 1 - (1 - alpha)^n * (1 - phi_0)
```

This approaches 1 without exceeding it. Consolidation and configured decay can reduce strength;
weak memories are evicted when capacity is exceeded. Strength is a retention heuristic influenced
by severity, repetition, recall, and consolidation. It is not a probability that a memory is true
or important.

Recall currently scans the bounded archive and computes a weighted score from type match, label
overlap, numeric-feature cosine similarity, and text-trigram similarity, then multiplies by memory
strength. With archive capacity (K), exact recall is (O(K)) per query. The runtime harness
measures cold and steady-state latency at selected values of (K).

## 6. Federation

Federation exports and imports memory records while retaining source provenance. A matching
content hash reinforces an existing memory; observations from multiple source instances can add a
correlation boost. This is corroboration evidence, not independence proof: two instances may share
the same upstream cause or duplicate feed.

Each Cascade keeps its local fast path. An aggregate Cascade can build wider context without
becoming a synchronous dependency for local ingestion.

## 7. Runtime cost model

For (|S|) signals, (k) deterministic agents, (|R_n|) nano survivors, and average classifier
cost (c_m), a simplified work model is:

```text
work ≈ O(|S| * k) + |R_n| * c_m
```

The deterministic component is not literally free. Measure it as latency, throughput, CPU time,
and RSS under a verified CPU allocation. The classifier cost must be measured separately for
normalized cache hits and unique cache misses. Request-path results must not silently include or
exclude asynchronous model work.

The public benchmark reports:

- nano pipeline p50/p95/p99 and signals per second per allocated core;
- HTTP request-path latency and throughput;
- llm-d-sc latency, failures, and throughput by concurrency and cache condition;
- a mixed synthetic survivor oracle; and
- exact-precedent recall latency by archive size.

Synthetic route composition is fixed by the harness and is not a production compression claim.

## 8. Decision-grade evidence

A staging-qualified claim requires all of the following to refer to the same immutable run:

- commit, image, configuration, taxonomy, and model revisions;
- a frozen corpus with complete independent adjudication by at least two reviewers;
- per-label support, precision, recall, F1, coverage, calibration, and dangerous misses;
- CPU-bound runtime measurements with route/retrieval oracles and zero execution errors;
- durable, drained audit queues with no new drops across the window;
- ledger capacity and outbox health;
- clean-clone, package, container, SBOM, provenance, and successful CI checks.

`cascade-stage-evidence` applies these gates and emits a sanitized aggregate. Its
`oss-rc-a-v1` profile currently requires a declared label-coverage challenge corpus with
prequalified authoritative evidence for `known_pattern`, at least 200 records, at least 25
adjudicated examples per label, at
least 50 important examples, at least 25 authoritative suppressions, 95% coverage, 0.85 balanced
accuracy, 0.80 macro F1, 0.98 authoritative-suppression precision, confidence coverage of at least
95%, ECE no greater than 0.10, and zero observed authoritative dangerous misses.

Those thresholds are a release policy, not a universal guarantee. A deployment with higher impact
or regulatory requirements should impose stricter acceptance criteria and an explicit human gate.
The challenge corpus cannot establish natural prevalence or compression rate; those claims require
a separately frozen representative-prevalence corpus.
