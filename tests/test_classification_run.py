from datetime import datetime, timedelta, timezone

import pytest

from cascade_compression.classification_run import run_and_evaluate
from cascade_compression.classifier import ClassificationResult, RankedLabel
from cascade_compression.holdout import canonical_digest


LABELS = ["routine_noise", "known_pattern", "needs_attention", "real_incident"]


class FakeGenerative:
    structured_confidence = True

    def classify(self, signal, text, client=None):
        del text, client
        return ClassificationResult(
            label=signal["signal_type"], confidence=0.9,
            backend="generative", model_revision="generative-v1",
            prompt_revision="prompt-v1",
        )


class FakeSemantic:
    def classify(self, signal, text):
        del text
        label = signal["signal_type"]
        ranked = [RankedLabel(label, 0.9)] + [
            RankedLabel(other, 0.05) for other in LABELS if other != label
        ]
        return ClassificationResult(
            label=label, ranked=ranked, confidence=0.85, margin=0.85,
            backend="semantic", classifier_id="classifier-v1",
            model_revision="encoder-v1", tokenizer_revision="tokenizer-v1",
            taxonomy_revision="taxonomy-v1",
        )


class DriftingSemantic(FakeSemantic):
    def __init__(self):
        self.calls = 0

    def classify(self, signal, text):
        result = super().classify(signal, text)
        self.calls += 1
        if self.calls > len(LABELS):
            result.taxonomy_revision = "taxonomy-v2"
        return result


def inputs():
    rows = []
    for index, label in enumerate(LABELS):
        signal = {
            "signal_type": label, "severity": "medium", "namespace": "test",
            "content": {"message": f"Synthetic case {index}"},
        }
        rows.append({
            "schema_version": "cascade.system-evaluation-case.v1alpha1",
            "case_id": f"case-{index}",
            "signal": signal,
            "signal_sha256": canonical_digest(signal),
            "ground_truth": {
                "classification": label,
                "actionability": (
                    "actionable" if label in {"needs_attention", "real_incident"}
                    else "suppressible"
                ),
                "expected_memory": label in {"needs_attention", "real_incident"},
            },
        })
    identity = sorted(({
        "case_id": row["case_id"], "signal_sha256": row["signal_sha256"],
    } for row in rows), key=lambda item: item["case_id"])
    digest = canonical_digest(identity)
    holdout = {
        "schema_version": "cascade.holdout-manifest.v1alpha1",
        "dataset": {"name": "synthetic", "revision": "v1"},
        "selection": {"selected_records": 4},
        "holdout_digest": digest,
    }
    summary = {
        "schema_version": "cascade.adjudication-summary.v1alpha1",
        "status": "complete", "unresolved": 0, "records": 4,
        "independent_reviewers": 2, "corpus_digest": digest,
    }
    now = datetime.now(timezone.utc)
    run = {
        "commit": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "config_digest": "sha256:" + "c" * 64,
        "taxonomy_revision": "taxonomy-v1",
        "window_start": (now - timedelta(minutes=1)).isoformat(),
        "window_end": (now + timedelta(minutes=1)).isoformat(),
    }
    return rows, holdout, summary, run


def test_same_corpus_runner_produces_decision_grade_sanitized_report():
    rows, holdout, summary, run = inputs()
    private_input, report = run_and_evaluate(
        rows, holdout, summary,
        generative=FakeGenerative(), semantic=FakeSemantic(), run=run,
        hybrid_margin=0.2, hybrid_suppress_margin=0.4, workers=2,
    )
    assert report["evidence"]["status"] == "decision_grade"
    assert report["evidence"]["corpus_binding"] is True
    assert set(report["arms"]) == {"generative", "semantic", "hybrid"}
    assert all("signal" not in row for row in private_input["records"])
    assert private_input["execution"]["contains_raw_records"] is False
    assert set(private_input["run"]["model_revisions"]) == {
        "generative", "semantic", "hybrid",
    }


def test_runner_rejects_corpus_not_bound_to_holdout():
    rows, holdout, summary, run = inputs()
    rows[0]["signal"]["content"]["message"] = "mutated"
    with pytest.raises(ValueError, match="signal digest mismatch"):
        run_and_evaluate(
            rows, holdout, summary,
            generative=FakeGenerative(), semantic=FakeSemantic(), run=run,
            hybrid_margin=0.2, hybrid_suppress_margin=0.4,
        )


def test_runner_requires_structured_generative_confidence():
    rows, holdout, summary, run = inputs()
    generative = FakeGenerative()
    generative.structured_confidence = False
    with pytest.raises(ValueError, match="structured generative confidence"):
        run_and_evaluate(
            rows, holdout, summary,
            generative=generative, semantic=FakeSemantic(), run=run,
            hybrid_margin=0.2, hybrid_suppress_margin=0.4,
        )


def test_runner_rejects_observed_taxonomy_mismatch():
    rows, holdout, summary, run = inputs()
    run["taxonomy_revision"] = "different-taxonomy"
    with pytest.raises(ValueError, match="taxonomy does not match"):
        run_and_evaluate(
            rows, holdout, summary,
            generative=FakeGenerative(), semantic=FakeSemantic(), run=run,
            hybrid_margin=0.2, hybrid_suppress_margin=0.4,
        )


def test_runner_detects_taxonomy_drift_in_hybrid_execution():
    rows, holdout, summary, run = inputs()
    with pytest.raises(ValueError, match="taxonomy does not match"):
        run_and_evaluate(
            rows, holdout, summary,
            generative=FakeGenerative(), semantic=DriftingSemantic(), run=run,
            hybrid_margin=0.2, hybrid_suppress_margin=0.4,
        )
