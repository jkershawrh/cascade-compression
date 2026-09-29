# Classification evaluation

Classifier agreement is not accuracy. Cascade reports accuracy only when every arm is evaluated
against the same frozen records with adjudicated truth labels. The public evaluator accepts local
JSON containing record identifiers, truth labels, and predictions, then emits a sanitized aggregate
report with a corpus digest, confusion matrices, per-label precision/recall/F1, dangerous misses,
coverage, top-label calibration, and pairwise agreement.

Calibration reports both its error and its coverage. An arm with confidence values on only a small
subset cannot satisfy the release gate; `oss-rc-a-v1` requires confidence coverage of at least 95%.
Use `CASCADE_GENERATIVE_STRUCTURED=1` during the held-out run to request a bounded confidence value
from the generative backend. Self-reported confidence is not assumed to be reliable; the reported
ECE and Brier score measure how well it matches observed correctness.

```bash
cascade-evaluate private-held-out.json --output evaluation-report.json
```

The input format is `cascade.classification-input.v1alpha1`:

```json
{
  "schema_version": "cascade.classification-input.v1alpha1",
  "dataset": {
    "name": "held-out-v1",
    "revision": "review-2026-09",
    "holdout_digest": "sha256:FROZEN_CORPUS_DIGEST",
    "adjudication": {
      "status": "complete",
      "method": "independent-double-review",
      "independent": true,
      "reviewers": 2,
      "corpus_digest": "sha256:FROZEN_CORPUS_DIGEST",
      "holdout_manifest_digest": "sha256:HOLDOUT_MANIFEST_DIGEST",
      "review_evidence_digest": "sha256:PRIVATE_REVIEW_EVIDENCE_DIGEST",
      "review_window": {
        "started_at": "2026-08-30T00:00:00Z",
        "completed_at": "2026-08-31T00:00:00Z"
      },
      "summary_digest": "sha256:ADJUDICATION_SUMMARY_DIGEST"
    }
  },
  "run": {
    "commit": "FULL_GIT_SHA",
    "image_digest": "sha256:IMAGE_DIGEST",
    "config_digest": "sha256:CONFIG_DIGEST",
    "taxonomy_revision": "taxonomy-v1",
    "window_start": "2026-09-01T00:00:00Z",
    "window_end": "2026-09-01T01:00:00Z",
    "model_revisions": {
      "generative": "model-revision",
      "semantic": "taxonomy-and-encoder-revision",
      "hybrid": "policy-and-backend-revision"
    }
  },
  "records": [
    {
      "record_id": "opaque-001",
      "signal_sha256": "sha256:SIGNAL_DIGEST",
      "expected": "real_incident",
      "predictions": {
        "generative": {"label": "real_incident", "confidence": 0.91, "authoritative": true},
        "semantic": {"label": "needs_attention", "confidence": 0.62, "authoritative": false},
        "hybrid": {"label": "real_incident", "confidence": 0.91, "authoritative": true}
      }
    }
  ]
}
```

The `cascade.classification-evaluation.v1alpha3` output never includes record identifiers or signal
payloads. Its dataset digest binds the report to the set of opaque identifiers and adjudicated
labels. Its computed corpus digest must also match both the frozen holdout manifest and adjudication
summary. A report is marked `decision_grade` only when that binding succeeds, adjudication is
complete and independent with at least two reviewers, the report commits to both the private review
evidence and complete adjudication summary, and the exact run metadata is frozen. Run identity
requires a full Git SHA, SHA-256 image and config digests, a non-empty taxonomy revision and model
revision map, and a timezone-aware positive evaluation window. Synthetic, unbound, or incompletely
adjudicated inputs remain `mechanics_only`.

`authoritative_dangerous_misses` counts important truth labels that an authoritative arm classified
as suppressive. Non-authoritative semantic suggestions are still represented in the confusion
matrix but do not count as Cascade suppression decisions. Pairwise agreement is explicitly marked
as not being accuracy.

## Independent review merge

Freeze the private review corpus before running the evaluated arms. Each candidate JSONL record
contains a stable `case_id`, a `sampling_stratum`, and a `signal` object. The freezer ranks records
deterministically within every stratum, replaces candidate case identifiers, and removes strata,
expected labels, and predictions from reviewer material:

```bash
cascade-freeze-holdout \
  --candidates private-candidates.jsonl \
  --quota source-a=75 --quota source-b=75 --quota source-c=75 --quota source-d=75 \
  --seed "$PRIVATE_HOLDOUT_SEED" \
  --dataset-name held-out-v1 --dataset-revision review-1 \
  --stratification-basis "source family and time window" \
  --source-window-start 2026-09-01T00:00:00Z \
  --source-window-end 2026-09-08T00:00:00Z \
  --output-corpus private-blinded-corpus.jsonl \
  --output-manifest holdout-manifest.json
```

The quota set must cover every candidate stratum, and undersized strata fail closed. Signals that
contain nested ground-truth, classification, model-output, decision, outcome, route, or verdict
fields are rejected instead of exposing potentially label-bearing data to reviewers. The freezer
enforces the review UI's denylist plus the exact `expected` field. The manifest aliases stratum
names and records the bounded source observation window,
selection counts, and digests but no raw records. The freezer rejects a `frozen_at` timestamp that
predates the end of the source observation window. Keep the seed and blinded corpus private; still
review all free-text manifest metadata before publishing it.
Stratification can ensure class coverage for balanced accuracy, but it changes prevalence; do not
present overall accuracy on a balanced corpus as the natural production rate.

The frozen corpus should then be reviewed twice, blind to evaluated model outputs, by two different
people using the [independent adjudication rubric](adjudication-rubric.md). Merge their receipt
exports locally:

```bash
cascade-adjudicate \
  --corpus private-blinded-corpus.jsonl \
  --holdout-manifest holdout-manifest.json \
  --review reviewer-a-receipts.jsonl \
  --review reviewer-b-receipts.jsonl \
  --output-corpus private-adjudicated-corpus.jsonl \
  --output-summary adjudication-summary.json \
  --output-disagreements private-disagreements.jsonl
```

The command fails closed with exit code 2 while any disagreement remains. A third independent
reviewer can review only that disagreement file; pass those receipts with `--resolution`. Review
files must cover the exact corpus, use distinct reviewer references, declare independence from the
evaluated arms, conform to the published `cascade.review-receipt.v1alpha1` contract, and bind their
signal digest to the exact frozen signal object. The runtime enforces the schema's closed field set;
undeclared fields such as model predictions are rejected even when included in a correctly hashed
receipt. The merged corpus
and disagreement file remain private. All resolved disagreements must be completed by the same
single third reviewer; the adjudicator rejects a mixture of additional reviewers. The
`cascade.adjudication-summary.v1alpha3` summary commits
to both the exact holdout manifest and the private receipt set. Adjudication recomputes each
receipt's evidence digest, requires a timezone-aware review timestamp after the holdout was frozen,
and rejects a corpus or record count that differs from the manifest. The sanitized summary includes
only counts, digests, and the review time window. A `known_pattern` receipt must cite an
authoritative source record; repetition alone cannot establish that label.

After adjudication, run every arm over the exact corpus with the bounded-concurrency runner. It
requires structured generative confidence, derives immutable revision digests from the backends
that actually answered, and invokes each backend exactly once per case. The hybrid policy is
applied to those same generative and semantic observations rather than making a second,
potentially nondeterministic request. The runner writes a private prediction input without signal
payloads and emits the sanitized report:

```bash
CASCADE_GENERATIVE_STRUCTURED=1 CASCADE_SC_ADDRESS=classifier.example:443 \
  cascade-classification-run \
  --corpus private-adjudicated-corpus.jsonl \
  --holdout-manifest holdout-manifest.json \
  --adjudication-summary adjudication-summary.json \
  --commit FULL_GIT_SHA --image-digest sha256:IMAGE_DIGEST \
  --config-digest sha256:CONFIG_DIGEST --taxonomy-revision taxonomy-v1 \
  --window-start 2026-09-01T00:00:00Z --window-end 2026-09-01T02:00:00Z \
  --workers 4 --output-private classification-run-private.json \
  --output-report classification-evaluation.json
```

The LLM URL/key/model and llm-d-sc TLS settings use the same environment variables as the service.
A disagreement queue selected because models differed is useful for error analysis but is not, by
itself, a representative holdout; use a separately frozen stratified sample for the release gate.
