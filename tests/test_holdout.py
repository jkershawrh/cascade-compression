import json

import jsonschema
import pytest

from cascade_compression.adjudication import merge_independent_reviews
from cascade_compression.contracts import contract_schema
from cascade_compression.holdout import canonical_digest, freeze_stratified_holdout


def candidates():
    rows = []
    for stratum in ("source-a", "source-b"):
        for index in range(5):
            rows.append({
                "case_id": f"internal-{stratum}-{index}",
                "sampling_stratum": stratum,
                "signal": {
                    "signal_type": "service_health",
                    "severity": "medium",
                    "content": {"message": f"observable event {index}"},
                },
                "predictions": {"hybrid": "must-not-leak"},
                "expected": "must-not-leak",
            })
    return rows


def freeze(rows=None, quotas=None, seed="private-seed"):
    return freeze_stratified_holdout(
        rows or candidates(), quotas or {"source-a": 3, "source-b": 3},
        seed=seed,
        dataset_name="blind-holdout",
        dataset_revision="v1",
        stratification_basis="source family",
        source_window_start="2026-09-01T00:00:00Z",
        source_window_end="2026-09-01T01:00:00Z",
        frozen_at="2026-09-01T02:00:00Z",
    )


def test_holdout_is_deterministic_blinded_and_schema_valid():
    first, first_manifest = freeze()
    second, second_manifest = freeze(list(reversed(candidates())))
    assert first == second
    assert first_manifest == second_manifest
    assert len(first) == 6
    assert all(set(row) == {
        "schema_version", "case_id", "signal", "signal_sha256",
    } for row in first)
    assert all("internal-" not in row["case_id"] for row in first)
    assert first_manifest["contains_raw_records"] is False
    assert set(first_manifest["selection"]["strata"]) == {
        "stratum-001", "stratum-002",
    }
    jsonschema.validate(
        first_manifest,
        json.loads(contract_schema("cascade.holdout-manifest").read_text()),
    )


def test_seed_changes_selection_or_opaque_identity():
    first, _ = freeze(seed="seed-one")
    second, _ = freeze(seed="seed-two")
    assert first != second


def test_every_candidate_stratum_requires_an_explicit_quota():
    with pytest.raises(ValueError, match="exact candidate strata"):
        freeze(quotas={"source-a": 3})


def test_insufficient_stratum_fails_closed():
    with pytest.raises(ValueError, match="needs 6"):
        freeze(quotas={"source-a": 6, "source-b": 3})


def test_model_output_nested_in_signal_cannot_reach_reviewers():
    rows = candidates()
    rows[0]["signal"]["content"]["predictions"] = {"hybrid": "routine_noise"}
    with pytest.raises(ValueError, match="evaluation fields"):
        freeze(rows=rows)


@pytest.mark.parametrize(
    "field",
    ["classification", "decision", "outcome", "verdict", "llm_route"],
)
def test_label_bearing_signal_fields_cannot_reach_reviewers(field):
    rows = candidates()
    rows[0]["signal"]["content"][field] = "must-not-leak"
    with pytest.raises(ValueError, match="evaluation fields"):
        freeze(rows=rows)


def test_freeze_timestamp_requires_timezone():
    with pytest.raises(ValueError, match="timezone"):
        freeze_stratified_holdout(
            candidates(), {"source-a": 3, "source-b": 3},
            seed="seed", dataset_name="held-out", dataset_revision="v1",
            stratification_basis="source family",
            source_window_start="2026-09-01T00:00:00Z",
            source_window_end="2026-09-01T01:00:00Z",
            frozen_at="2026-09-01T00:00:00",
        )


def test_source_observation_window_must_be_positive():
    with pytest.raises(ValueError, match="observation window"):
        freeze_stratified_holdout(
            candidates(), {"source-a": 3, "source-b": 3},
            seed="seed", dataset_name="held-out", dataset_revision="v1",
            stratification_basis="source family",
            source_window_start="2026-09-01T01:00:00Z",
            source_window_end="2026-09-01T00:00:00Z",
        )


def test_holdout_digest_binds_to_adjudication_summary():
    corpus, manifest = freeze()

    def receipts(reviewer):
        result = []
        for row in corpus:
            receipt = {
                "schema_version": "cascade.review-receipt.v1alpha1",
                "case_id": row["case_id"],
                "signal_sha256": row["signal_sha256"],
                "actionability": "actionable",
                "classification": "needs_attention",
                "expected_memory": True,
                "reviewer_ref": reviewer,
                "reviewed_at": "2026-09-02T00:00:00Z",
                "source": "independent_human_review",
                "source_record_ref": None,
                "rationale": "The frozen evidence requires investigation.",
                "independent_of_evaluated_arms": True,
            }
            receipt["evidence_ref"] = canonical_digest(receipt)
            result.append(receipt)
        return result

    _, summary, unresolved = merge_independent_reviews(
        corpus, receipts("reviewer-a"), receipts("reviewer-b"),
        holdout_manifest=manifest,
    )
    assert not unresolved
    assert summary["corpus_digest"] == manifest["holdout_digest"]
    assert summary["holdout_manifest_digest"] == canonical_digest(manifest)


def test_freeze_cannot_predate_observation_window():
    with pytest.raises(ValueError, match="cannot predate"):
        freeze_stratified_holdout(
            candidates(), {"source-a": 3, "source-b": 3},
            seed="seed", dataset_name="held-out", dataset_revision="v1",
            stratification_basis="source family",
            source_window_start="2026-09-01T00:00:00Z",
            source_window_end="2026-09-01T01:00:00Z",
            frozen_at="2026-09-01T00:30:00Z",
        )


def test_challenge_holdout_preflights_label_candidates_and_authority():
    rows = []
    quotas = {}
    minimums = {}
    labels = ("routine_noise", "known_pattern", "needs_attention", "real_incident")
    for label in labels:
        quotas[label] = 2
        minimums[label] = 2
        for index in range(2):
            rows.append({
                "case_id": f"{label}-{index}",
                "sampling_stratum": label,
                "coverage_target": label,
                "source_record_ref": (
                    f"authority://pattern/{index}" if label == "known_pattern" else None
                ),
                "signal": {
                    "signal_type": f"candidate_{label}",
                    "severity": "medium",
                    "content": {"message": f"candidate evidence {index}"},
                },
            })
    corpus, manifest = freeze_stratified_holdout(
        rows, quotas, seed="challenge-seed", dataset_name="challenge",
        dataset_revision="v1", stratification_basis="candidate coverage target",
        source_window_start="2026-09-01T00:00:00Z",
        source_window_end="2026-09-01T01:00:00Z",
        frozen_at="2026-09-01T02:00:00Z",
        evaluation_purpose="label_coverage_challenge",
        minimum_label_candidates=minimums,
    )
    assert len(corpus) == 8
    assert all("coverage_target" not in row for row in corpus)
    assert sum("evidence_refs" in row for row in corpus) == 2
    design = manifest["evaluation_design"]
    assert design["prevalence_claim_permitted"] is False
    assert design["candidate_targets_are_ground_truth"] is False
    assert design["selected_candidate_targets"] == minimums
    assert design["known_pattern_authority_prequalified"] is True


def test_challenge_holdout_rejects_unsubstantiated_known_pattern_candidate():
    rows = []
    labels = ("routine_noise", "known_pattern", "needs_attention", "real_incident")
    for label in labels:
        rows.append({
            "case_id": label,
            "sampling_stratum": label,
            "coverage_target": label,
            "source_record_ref": None,
            "signal": {"signal_type": label, "severity": "medium"},
        })
    with pytest.raises(ValueError, match="authoritative source_record_ref"):
        freeze_stratified_holdout(
            rows, {label: 1 for label in labels},
            seed="challenge-seed", dataset_name="challenge",
            dataset_revision="v1", stratification_basis="candidate coverage target",
            source_window_start="2026-09-01T00:00:00Z",
            source_window_end="2026-09-01T01:00:00Z",
            frozen_at="2026-09-01T02:00:00Z",
            evaluation_purpose="label_coverage_challenge",
            minimum_label_candidates={label: 1 for label in labels},
        )


def test_challenge_holdout_rejects_duplicate_signal_support():
    rows = []
    labels = ("routine_noise", "known_pattern", "needs_attention", "real_incident")
    for label in labels:
        rows.append({
            "case_id": label,
            "sampling_stratum": label,
            "coverage_target": label,
            "source_record_ref": (
                "authority://shared" if label == "known_pattern" else None
            ),
            "signal": {"signal_type": "duplicate", "severity": "medium"},
        })
    with pytest.raises(ValueError, match="unique signals"):
        freeze_stratified_holdout(
            rows, {label: 1 for label in labels}, seed="challenge-seed",
            dataset_name="challenge", dataset_revision="v1",
            stratification_basis="candidate coverage target",
            source_window_start="2026-09-01T00:00:00Z",
            source_window_end="2026-09-01T01:00:00Z",
            frozen_at="2026-09-01T02:00:00Z",
            evaluation_purpose="label_coverage_challenge",
            minimum_label_candidates={label: 1 for label in labels},
        )
