import copy
import json
from pathlib import Path

import jsonschema
import pytest

from cascade_compression.evaluation import evaluate_classifiers
from cascade_compression.holdout import canonical_digest


ROOT = Path(__file__).resolve().parent.parent


def document():
    labels = ["routine_noise", "known_pattern", "needs_attention", "real_incident"]
    records = []
    for index, label in enumerate(labels):
        records.append({
            "record_id": f"opaque-{index}",
            "signal_sha256": "sha256:" + str(index) * 64,
            "expected": label,
            "predictions": {
                "generative": {"label": label, "confidence": 0.9, "authoritative": True},
                "semantic": {"label": label, "confidence": 0.8, "authoritative": True},
                "hybrid": {"label": label, "confidence": 0.9, "authoritative": True},
            },
        })
    corpus_digest = canonical_digest(sorted(({
        "case_id": row["record_id"],
        "signal_sha256": row["signal_sha256"],
    } for row in records), key=lambda item: item["case_id"]))
    return {
        "schema_version": "cascade.classification-input.v1alpha1",
        "dataset": {
            "name": "synthetic",
            "revision": "v1",
            "holdout_digest": corpus_digest,
            "adjudication": {
                "status": "complete",
                "method": "independent-double-review",
                "independent": True,
                "reviewers": 2,
                "corpus_digest": corpus_digest,
                "review_evidence_digest": "sha256:" + "d" * 64,
                "holdout_manifest_digest": "sha256:" + "f" * 64,
                "review_window": {
                    "started_at": "2026-08-31T00:00:00Z",
                    "completed_at": "2026-08-31T01:00:00Z",
                },
                "summary_digest": "sha256:" + "e" * 64,
            },
        },
        "run": {
            "commit": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "config_digest": "sha256:" + "c" * 64,
            "taxonomy_revision": "taxonomy-v1",
            "window_start": "2026-09-01T00:00:00Z",
            "window_end": "2026-09-01T01:00:00Z",
            "model_revisions": {"generative": "g1", "semantic": "s1"},
        },
        "records": records,
    }


def test_same_corpus_report_is_decision_grade_and_sanitized():
    report = evaluate_classifiers(document())
    assert report["evidence"]["status"] == "decision_grade"
    assert report["evidence"]["contains_raw_records"] is False
    assert "records" not in report
    assert report["arms"]["hybrid"]["overall_accuracy"] == 1.0
    assert report["arms"]["hybrid"]["authoritative_dangerous_misses"] == 0


def test_report_validates_against_public_contract():
    schema = json.loads(
        (ROOT / "contracts" / "schemas" / "classification-evaluation.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(
        evaluate_classifiers(document())
    )


def test_authoritative_false_suppression_is_a_dangerous_miss():
    source = document()
    source["records"][-1]["predictions"]["hybrid"] = {
        "label": "routine_noise", "confidence": 0.99, "authoritative": True,
    }
    report = evaluate_classifiers(source)
    arm = report["arms"]["hybrid"]
    assert arm["authoritative_dangerous_misses"] == 1
    assert arm["authoritative_dangerous_miss_rate"] == 0.5
    assert arm["confusion"]["real_incident"]["routine_noise"] == 1


def test_non_authoritative_error_is_not_a_cascade_suppression_decision():
    source = document()
    source["records"][-1]["predictions"]["semantic"] = {
        "label": "routine_noise", "confidence": 0.51, "authoritative": False,
    }
    arm = evaluate_classifiers(source)["arms"]["semantic"]
    assert arm["raw_false_suppressions"] == 1
    assert arm["authoritative_dangerous_misses"] == 0


def test_agreement_is_explicitly_not_accuracy():
    source = document()
    for row in source["records"]:
        row["predictions"]["generative"]["label"] = "routine_noise"
        row["predictions"]["semantic"]["label"] = "routine_noise"
    report = evaluate_classifiers(source)
    pair = report["pairwise_agreement"]["generative__semantic"]
    assert pair["agreement_rate"] == 1.0
    assert pair["is_accuracy"] is False
    assert report["arms"]["generative"]["overall_accuracy"] == 0.25


def test_digest_is_stable_when_record_order_changes():
    first = document()
    second = copy.deepcopy(first)
    second["records"].reverse()
    assert (
        evaluate_classifiers(first)["dataset"]["digest"]
        == evaluate_classifiers(second)["dataset"]["digest"]
    )


def test_incomplete_adjudication_cannot_claim_decision_grade():
    source = document()
    source["dataset"]["adjudication"]["independent"] = False
    report = evaluate_classifiers(source)
    assert report["evidence"]["status"] == "mechanics_only"


def test_invalid_review_window_cannot_claim_decision_grade():
    source = document()
    source["dataset"]["adjudication"]["review_window"] = {
        "started_at": "2026-09-01T01:00:00Z",
        "completed_at": "2026-09-01T00:00:00Z",
    }
    report = evaluate_classifiers(source)
    assert report["evidence"]["status"] == "mechanics_only"
    assert report["evidence"]["review_window_valid"] is False


def test_mismatched_holdout_digest_cannot_claim_decision_grade():
    source = document()
    source["dataset"]["holdout_digest"] = "sha256:" + "f" * 64
    report = evaluate_classifiers(source)
    assert report["evidence"]["status"] == "mechanics_only"
    assert report["evidence"]["corpus_binding"] is False


def test_missing_record_signal_digest_cannot_claim_decision_grade():
    source = document()
    del source["records"][0]["signal_sha256"]
    report = evaluate_classifiers(source)
    assert report["evidence"]["status"] == "mechanics_only"
    assert report["evidence"]["computed_corpus_digest"] is None


def test_malformed_declared_corpus_digest_fails_closed():
    source = document()
    source["dataset"]["adjudication"]["corpus_digest"] = "not-a-digest"
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_classifiers(source)


def test_missing_run_metadata_cannot_claim_decision_grade():
    source = document()
    del source["run"]["image_digest"]
    report = evaluate_classifiers(source)
    assert report["evidence"]["status"] == "mechanics_only"
    assert report["evidence"]["missing_run_fields"] == ["image_digest"]


def test_malformed_run_identity_cannot_claim_decision_grade():
    source = document()
    source["run"]["commit"] = "main"
    report = evaluate_classifiers(source)
    assert report["evidence"]["status"] == "mechanics_only"
    assert report["evidence"]["invalid_run_fields"] == ["commit"]


def test_model_revisions_are_required_for_decision_grade():
    source = document()
    source["run"]["model_revisions"] = {}
    report = evaluate_classifiers(source)
    assert report["evidence"]["status"] == "mechanics_only"
    assert report["evidence"]["model_revisions_frozen"] is False


def test_duplicate_record_ids_fail_closed():
    source = document()
    source["records"][1]["record_id"] = source["records"][0]["record_id"]
    with pytest.raises(ValueError, match="duplicate record_id"):
        evaluate_classifiers(source)


def test_invalid_confidence_fails_closed():
    source = document()
    source["records"][0]["predictions"]["hybrid"]["confidence"] = 1.1
    with pytest.raises(ValueError, match="between 0 and 1"):
        evaluate_classifiers(source)


def test_calibration_reports_missing_confidence_coverage():
    source = document()
    del source["records"][0]["predictions"]["hybrid"]["confidence"]
    calibration = evaluate_classifiers(source)["arms"]["hybrid"]["calibration"]
    assert calibration["samples"] == 3
    assert calibration["eligible_predictions"] == 4
    assert calibration["coverage"] == 0.75
