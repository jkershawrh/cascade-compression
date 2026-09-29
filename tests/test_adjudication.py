import hashlib
import json

import jsonschema
import pytest

from cascade_compression.adjudication import merge_independent_reviews
from cascade_compression.contracts import contract_schema


LABELS = ["routine_noise", "known_pattern", "needs_attention", "real_incident"]


def corpus():
    return [
        {
            "schema_version": "cascade.system-evaluation-case.v1alpha1",
            "case_id": f"case-{index}",
            "signal": {"signal_type": "example", "content": {"index": index}},
        }
        for index in range(4)
    ]


def reviews(reviewer, overrides=None):
    overrides = overrides or {}
    rows = []
    for index, label in enumerate(LABELS):
        label = overrides.get(index, label)
        authoritative = label == "known_pattern"
        receipt = {
            "schema_version": "cascade.review-receipt.v1alpha1",
            "case_id": f"case-{index}",
            "signal_sha256": signal_digest(index),
            "actionability": (
                "actionable" if label in {"needs_attention", "real_incident"}
                else "suppressible"
            ),
            "classification": label,
            "expected_memory": label in {"needs_attention", "real_incident"},
            "reviewer_ref": reviewer,
            "reviewed_at": "2026-09-01T00:00:00Z",
            "source": (
                "authoritative_record" if authoritative
                else "independent_human_review"
            ),
            "source_record_ref": "documented-pattern-1" if authoritative else None,
            "rationale": "Observable evidence supports this label.",
            "independent_of_evaluated_arms": True,
        }
        receipt["evidence_ref"] = evidence_digest(receipt)
        rows.append(receipt)
    return rows


def signal_digest(index):
    encoded = json.dumps(
        {"signal_type": "example", "content": {"index": index}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def holdout_manifest():
    identity = [
        {"case_id": f"case-{index}", "signal_sha256": signal_digest(index)}
        for index in range(4)
    ]
    return {
        "schema_version": "cascade.holdout-manifest.v1alpha2",
        "dataset": {"name": "test", "revision": "v1"},
        "frozen_at": "2026-08-31T00:00:00Z",
        "selection": {"selected_records": 4},
        "holdout_digest": evidence_digest(identity),
    }


def evidence_digest(receipt):
    encoded = json.dumps(
        receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_two_complete_independent_reviews_form_ground_truth():
    first = reviews("reviewer-a")
    jsonschema.validate(
        first[0],
        json.loads(contract_schema("cascade.review-receipt").read_text()),
    )
    merged, summary, unresolved = merge_independent_reviews(
        corpus(), first, reviews("reviewer-b"),
        holdout_manifest=holdout_manifest(),
    )
    assert summary["status"] == "complete"
    assert summary["labels"] == {label: 1 for label in sorted(LABELS)}
    assert summary["contains_raw_records"] is False
    jsonschema.validate(
        summary,
        json.loads(contract_schema("cascade.adjudication-summary").read_text()),
    )
    assert not unresolved
    assert all(
        row["ground_truth"]["provenance"]["method"] == "double_review_consensus"
        for row in merged
    )


def test_disagreement_fails_closed_until_independent_resolution():
    second = reviews("reviewer-b", {0: "known_pattern"})
    merged, summary, unresolved = merge_independent_reviews(
        corpus(), reviews("reviewer-a"), second,
        holdout_manifest=holdout_manifest(),
    )
    assert summary["status"] == "incomplete"
    assert summary["disagreements"] == 1
    assert summary["unresolved"] == 1
    assert len(merged) == 3
    assert unresolved[0]["case_id"] == "case-0"


def test_third_reviewer_can_resolve_only_the_disagreement():
    second = reviews("reviewer-b", {0: "known_pattern"})
    resolution = [reviews("reviewer-c", {0: "known_pattern"})[0]]
    merged, summary, unresolved = merge_independent_reviews(
        corpus(), reviews("reviewer-a"), second, resolution,
        holdout_manifest=holdout_manifest(),
    )
    assert summary["status"] == "complete"
    assert summary["resolution_reviewers"] == 1
    assert not unresolved
    resolved = next(row for row in merged if row["case_id"] == "case-0")
    assert resolved["ground_truth"]["classification"] == "known_pattern"
    assert resolved["ground_truth"]["provenance"]["reviewers"] == 3


def test_resolution_cannot_cover_an_agreed_case():
    with pytest.raises(ValueError, match="only disputed cases"):
        merge_independent_reviews(
            corpus(), reviews("reviewer-a"), reviews("reviewer-b"),
            [reviews("reviewer-c")[0]],
            holdout_manifest=holdout_manifest(),
        )


def test_all_disagreements_require_one_distinct_third_reviewer():
    second = reviews("reviewer-b", {0: "known_pattern", 2: "real_incident"})
    resolution = [
        reviews("reviewer-c", {0: "known_pattern"})[0],
        reviews("reviewer-d", {2: "real_incident"})[2],
    ]
    with pytest.raises(ValueError, match="one third reviewer"):
        merge_independent_reviews(
            corpus(), reviews("reviewer-a"), second, resolution,
            holdout_manifest=holdout_manifest(),
        )


def test_same_reviewer_cannot_supply_both_reviews():
    with pytest.raises(ValueError, match="different reviewers"):
        merge_independent_reviews(
            corpus(), reviews("same"), reviews("same"),
            holdout_manifest=holdout_manifest(),
        )


def test_review_receipt_must_bind_to_frozen_signal_evidence():
    first = reviews("reviewer-a")
    second = reviews("reviewer-b")
    first[0]["signal_sha256"] = "sha256:" + "f" * 64
    second[0]["signal_sha256"] = "sha256:" + "f" * 64
    first[0]["evidence_ref"] = evidence_digest({
        key: value for key, value in first[0].items() if key != "evidence_ref"
    })
    second[0]["evidence_ref"] = evidence_digest({
        key: value for key, value in second[0].items() if key != "evidence_ref"
    })
    with pytest.raises(ValueError, match="frozen signal evidence"):
        merge_independent_reviews(
            corpus(), first, second, holdout_manifest=holdout_manifest(),
        )


def test_known_pattern_requires_documented_authority():
    first = reviews("reviewer-a")
    second = reviews("reviewer-b")
    first[1]["source"] = "independent_human_review"
    first[1]["source_record_ref"] = None
    with pytest.raises(ValueError, match="known_pattern requires"):
        merge_independent_reviews(
            corpus(), first, second, holdout_manifest=holdout_manifest(),
        )


def test_challenge_known_pattern_must_cite_frozen_candidate_evidence():
    cases = corpus()
    cases[1]["evidence_refs"] = ["documented-pattern-2"]
    manifest = holdout_manifest()
    manifest["evaluation_design"] = {
        "purpose": "label_coverage_challenge",
        "prevalence_claim_permitted": False,
        "candidate_targets_are_ground_truth": False,
        "minimum_label_candidates": {label: 1 for label in LABELS},
        "selected_candidate_targets": {label: 1 for label in LABELS},
        "known_pattern_authority_prequalified": True,
    }
    with pytest.raises(ValueError, match="evidence frozen with the challenge case"):
        merge_independent_reviews(
            cases, reviews("reviewer-a"), reviews("reviewer-b"),
            holdout_manifest=manifest,
        )


def test_review_receipt_evidence_digest_must_match_contents():
    first = reviews("reviewer-a")
    first[0]["rationale"] = "Changed after the receipt was signed."
    with pytest.raises(ValueError, match="evidence digest does not match"):
        merge_independent_reviews(
            corpus(), first, reviews("reviewer-b"),
            holdout_manifest=holdout_manifest(),
        )


def test_review_receipt_requires_versioned_contract():
    first = reviews("reviewer-a")
    first[0].pop("schema_version")
    first[0]["evidence_ref"] = evidence_digest({
        key: value for key, value in first[0].items() if key != "evidence_ref"
    })
    with pytest.raises(ValueError, match="unsupported schema version"):
        merge_independent_reviews(
            corpus(), first, reviews("reviewer-b"),
            holdout_manifest=holdout_manifest(),
        )


def test_review_receipt_rejects_undeclared_model_output():
    first = reviews("reviewer-a")
    first[0]["model_prediction"] = {"label": "routine_noise", "confidence": 0.99}
    first[0]["evidence_ref"] = evidence_digest({
        key: value for key, value in first[0].items() if key != "evidence_ref"
    })
    with pytest.raises(ValueError, match="undeclared fields: model_prediction"):
        merge_independent_reviews(
            corpus(), first, reviews("reviewer-b"),
            holdout_manifest=holdout_manifest(),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("case_id", 7, "string case_id"),
        ("reviewer_ref", "   ", "string reviewer_ref"),
        ("reviewed_at", 1_788_134_400, "string reviewed_at"),
        ("rationale", {"text": "not a string"}, "string rationale"),
        ("source_record_ref", 42, "string or null"),
    ],
)
def test_review_receipt_enforces_published_field_types(field, value, message):
    first = reviews("reviewer-a")
    first[0][field] = value
    first[0]["evidence_ref"] = evidence_digest({
        key: item for key, item in first[0].items() if key != "evidence_ref"
    })
    with pytest.raises(ValueError, match=message):
        merge_independent_reviews(
            corpus(), first, reviews("reviewer-b"),
            holdout_manifest=holdout_manifest(),
        )


def test_review_receipt_timestamp_requires_timezone():
    first = reviews("reviewer-a")
    first[0]["reviewed_at"] = "2026-09-01T00:00:00"
    receipt = {key: value for key, value in first[0].items() if key != "evidence_ref"}
    first[0]["evidence_ref"] = evidence_digest(receipt)
    with pytest.raises(ValueError, match="must include a timezone"):
        merge_independent_reviews(
            corpus(), first, reviews("reviewer-b"),
            holdout_manifest=holdout_manifest(),
        )


def test_review_cannot_predate_frozen_holdout():
    first = reviews("reviewer-a")
    first[0]["reviewed_at"] = "2026-08-30T23:59:59Z"
    first[0]["evidence_ref"] = evidence_digest({
        key: value for key, value in first[0].items() if key != "evidence_ref"
    })
    with pytest.raises(ValueError, match="predates the frozen holdout"):
        merge_independent_reviews(
            corpus(), first, reviews("reviewer-b"),
            holdout_manifest=holdout_manifest(),
        )


def test_adjudication_requires_exact_holdout_manifest():
    manifest = holdout_manifest()
    manifest["holdout_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="does not match"):
        merge_independent_reviews(
            corpus(), reviews("reviewer-a"), reviews("reviewer-b"),
            holdout_manifest=manifest,
        )
