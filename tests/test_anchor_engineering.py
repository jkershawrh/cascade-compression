"""Tests for the public-safe custom anchor lifecycle."""

import pytest

from cascade_compression.anchor_engineering import (
    approve_candidate,
    build_candidate_taxonomy,
    collect_gap_summary,
    compare_evaluations,
    evaluate_holdout,
    rollback_revision,
    validate_anchor_proposals,
    validate_taxonomy,
)


@pytest.fixture
def taxonomy():
    return {
        "labels": [
            "routine_noise", "known_pattern", "needs_attention", "real_incident"
        ],
        "anchors": {
            "routine_noise": ["Routine operation completed normally"],
            "known_pattern": ["Recurring condition with a known interpretation"],
            "needs_attention": ["Unexpected behavior requires investigation"],
            "real_incident": ["Active harmful condition needs immediate response"],
        },
        "top_k": 1,
        "taxonomy_revision": "cascade-classification-anchors-v1",
    }


def test_gap_summary_uses_only_fallback_metadata():
    comparisons = [
        {
            "signal_type": "service_health",
            "fallback_used": True,
            "semantic": {"label": "needs_attention", "margin": 0.11},
            "generative": {"label": "needs_attention"},
        },
        {
            "signal_type": "service_health",
            "fallback_used": False,
            "semantic": {"label": "needs_attention", "margin": 0.50},
            "generative": None,
        },
    ]
    assert collect_gap_summary(comparisons) == [{
        "signal_type": "service_health",
        "count": 1,
        "agreement_rate": 1.0,
        "mean_margin": 0.11,
        "semantic_label_distribution": {"needs_attention": 1},
        "generative_label_distribution": {"needs_attention": 1},
    }]


def test_proposals_require_agreement_and_reject_unsafe_suppression(taxonomy):
    gaps = [{
        "agreement_rate": 0.95,
        "generative_label_distribution": {"routine_noise": 20},
    }]
    accepted, rejected = validate_anchor_proposals(
        {
            "routine_noise": [
                "Expected periodic update requiring no action",
                "Service outage can safely be ignored",
            ],
            "real_incident": ["Immediate harmful behavior is occurring"],
        },
        gaps,
        current_taxonomy=taxonomy,
    )
    assert accepted["routine_noise"] == [
        "Expected periodic update requiring no action"
    ]
    assert {item["reason"] for item in rejected} == {
        "incident_language_in_suppressive_anchor",
        "no_high_agreement_proposal_evidence",
    }


def test_candidate_is_versioned_but_not_approved(taxonomy):
    candidate = build_candidate_taxonomy(
        taxonomy,
        {"needs_attention": ["A novel deviation needs human investigation"]},
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert "-v2-candidate-" in candidate["taxonomy_revision"]
    assert candidate["lifecycle"]["status"] == "candidate"
    assert candidate["lifecycle"]["parent_revision"] == taxonomy["taxonomy_revision"]
    assert candidate["lifecycle"]["requires_explicit_approval"] is True


def test_taxonomy_rejects_cross_label_duplicate_anchor(taxonomy):
    taxonomy["anchors"]["real_incident"] = taxonomy["anchors"]["needs_attention"]
    with pytest.raises(ValueError, match="appears in both"):
        validate_taxonomy(taxonomy)


def holdout(predictions):
    expected = [
        "routine_noise", "known_pattern", "needs_attention", "real_incident"
    ]
    return evaluate_holdout([
        {
            "record_id": f"record-{index}",
            "expected": truth,
            "predicted": prediction,
            "margin": 0.60,
        }
        for index, (truth, prediction) in enumerate(zip(expected, predictions))
    ])


def test_holdout_evaluation_detects_authoritative_false_suppression():
    evaluation = holdout([
        "routine_noise", "known_pattern", "routine_noise", "real_incident"
    ])
    assert evaluation["authoritative_false_suppressions"] == 1
    assert evaluation["per_label_recall"]["needs_attention"] == 0.0


def test_comparison_requires_zero_false_suppression_and_no_regression():
    current = holdout([
        "routine_noise", "known_pattern", "needs_attention", "real_incident"
    ])
    unsafe = holdout([
        "routine_noise", "known_pattern", "routine_noise", "real_incident"
    ])
    comparison = compare_evaluations(current, unsafe)
    assert not comparison["eligible_for_approval"]
    assert not comparison["gates"]["zero_authoritative_false_suppressions"]


def test_comparison_rejects_different_holdouts_with_equal_counts():
    current = evaluate_holdout([{
        "record_id": "record-a", "expected": "routine_noise",
        "predicted": "routine_noise", "margin": 0.60,
    }])
    candidate = evaluate_holdout([{
        "record_id": "record-b", "expected": "routine_noise",
        "predicted": "routine_noise", "margin": 0.60,
    }])
    with pytest.raises(ValueError, match="same holdout records"):
        compare_evaluations(current, candidate)


def test_approval_is_explicit_and_activation_remains_separate(taxonomy):
    candidate = build_candidate_taxonomy(
        taxonomy,
        {"needs_attention": ["A novel deviation needs human investigation"]},
    )
    evaluation = holdout([
        "routine_noise", "known_pattern", "needs_attention", "real_incident"
    ])
    comparison = compare_evaluations(evaluation, evaluation)
    approved = approve_candidate(
        candidate,
        comparison,
        approved_by="reviewer",
        approved_at="2026-01-02T00:00:00+00:00",
    )
    assert approved["lifecycle"]["status"] == "approved"
    assert approved["lifecycle"]["activation_required"] is True
    assert rollback_revision(approved) == taxonomy["taxonomy_revision"]


def test_failed_candidate_cannot_be_approved(taxonomy):
    candidate = build_candidate_taxonomy(taxonomy, {})
    with pytest.raises(ValueError, match="did not pass"):
        approve_candidate(
            candidate,
            {"eligible_for_approval": False},
            approved_by="reviewer",
        )
