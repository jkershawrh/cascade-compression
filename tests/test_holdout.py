import json

import jsonschema
import pytest

from cascade_compression.adjudication import merge_independent_reviews
from cascade_compression.contracts import contract_schema
from cascade_compression.holdout import freeze_stratified_holdout


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
        frozen_at="2026-09-01T00:00:00Z",
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


def test_freeze_timestamp_requires_timezone():
    with pytest.raises(ValueError, match="timezone"):
        freeze_stratified_holdout(
            candidates(), {"source-a": 3, "source-b": 3},
            seed="seed", dataset_name="held-out", dataset_revision="v1",
            stratification_basis="source family",
            frozen_at="2026-09-01T00:00:00",
        )


def test_holdout_digest_binds_to_adjudication_summary():
    corpus, manifest = freeze()

    def receipts(reviewer):
        return [{
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
            "evidence_ref": "sha256:" + "a" * 64,
        } for row in corpus]

    _, summary, unresolved = merge_independent_reviews(
        corpus, receipts("reviewer-a"), receipts("reviewer-b"),
    )
    assert not unresolved
    assert summary["corpus_digest"] == manifest["holdout_digest"]
