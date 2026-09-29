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
        rows.append({
            "case_id": f"case-{index}",
            "signal_sha256": "sha256:" + str(index) * 64,
            "actionability": (
                "actionable" if label in {"needs_attention", "real_incident"}
                else "suppressible"
            ),
            "classification": label,
            "expected_memory": label in {"needs_attention", "real_incident"},
            "reviewer_ref": reviewer,
            "reviewed_at": "2026-09-01T00:00:00Z",
            "source": "independent_human_review",
            "source_record_ref": None,
            "rationale": "Observable evidence supports this label.",
            "independent_of_evaluated_arms": True,
            "evidence_ref": "sha256:" + "a" * 64,
        })
    return rows


def test_two_complete_independent_reviews_form_ground_truth():
    merged, summary, unresolved = merge_independent_reviews(
        corpus(), reviews("reviewer-a"), reviews("reviewer-b"),
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
    )
    assert summary["status"] == "complete"
    assert summary["resolution_reviewers"] == 1
    assert not unresolved
    resolved = next(row for row in merged if row["case_id"] == "case-0")
    assert resolved["ground_truth"]["classification"] == "known_pattern"
    assert resolved["ground_truth"]["provenance"]["reviewers"] == 3


def test_same_reviewer_cannot_supply_both_reviews():
    with pytest.raises(ValueError, match="different reviewers"):
        merge_independent_reviews(
            corpus(), reviews("same"), reviews("same"),
        )
