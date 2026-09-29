# Classification evaluation

Classifier agreement is not accuracy. Cascade reports accuracy only when every arm is evaluated
against the same frozen records with adjudicated truth labels. The public evaluator accepts local
JSON containing record identifiers, truth labels, and predictions, then emits a sanitized aggregate
report with a corpus digest, confusion matrices, per-label precision/recall/F1, dangerous misses,
coverage, top-label calibration, and pairwise agreement.

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
    "adjudication": {
      "status": "complete",
      "method": "independent-double-review",
      "independent": true,
      "reviewers": 2
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
      "semantic": "taxonomy-and-encoder-revision"
    }
  },
  "records": [
    {
      "record_id": "opaque-001",
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

The output never includes record identifiers or signal payloads. Its dataset digest binds the report
to the set of opaque identifiers and adjudicated labels. A report is marked `decision_grade` only
when adjudication is complete, independent, performed by at least two reviewers, and the exact run
metadata is frozen. Synthetic or incompletely adjudicated inputs remain `mechanics_only`.

`authoritative_dangerous_misses` counts important truth labels that an authoritative arm classified
as suppressive. Non-authoritative semantic suggestions are still represented in the confusion
matrix but do not count as Cascade suppression decisions. Pairwise agreement is explicitly marked
as not being accuracy.

## Independent review merge

The private corpus should be reviewed twice, blind to evaluated model outputs, by two different
people. Merge their receipt exports locally:

```bash
cascade-adjudicate \
  --corpus private-blinded-corpus.jsonl \
  --review reviewer-a-receipts.jsonl \
  --review reviewer-b-receipts.jsonl \
  --output-corpus private-adjudicated-corpus.jsonl \
  --output-summary adjudication-summary.json \
  --output-disagreements private-disagreements.jsonl
```

The command fails closed with exit code 2 while any disagreement remains. A third independent
reviewer can review only that disagreement file; pass those receipts with `--resolution`. Review
files must cover the exact corpus, use distinct reviewer references, declare independence from the
evaluated arms, and agree on the signal digest. The merged corpus and disagreement file remain
private. The summary contains only counts and a digest and is suitable for sanitized evidence
review.

After adjudication, join each record's truth label to the predictions produced by the generative,
semantic, and hybrid arms on the exact same records, then run `cascade-evaluate`. A disagreement
queue selected because models differed is useful for error analysis but is not, by itself, a
representative holdout; use a separately frozen stratified sample for the release gate.
