"""BDD and counterfactual tests for the VEF replay exporter."""

import pytest

from cascade_compression.value_evidence import (
    CONTRACT_VERSION,
    CustomerEconomics,
    EngineeringEffort,
    ReplayArm,
    ReplayEvidence,
    RouteUsage,
    build_vef_claim,
)


def paired_replay(*, baseline_calls=1000, cascade_calls=180, dangerous_misses=0):
    return ReplayEvidence(
        baseline=ReplayArm("sha256:workload-a", 1000, baseline_calls, 0),
        cascade=ReplayArm("sha256:workload-a", 1000, cascade_calls, dangerous_misses),
        shadow_sampled_suppressions=820,
        total_suppressions=820,
    )


def economics(*, validated=True):
    return CustomerEconomics(0.05, 25.0, customer_validated=validated)


def test_matched_safe_replay_exports_value_layers():
    """GIVEN the same workload in both arms WHEN safety passes THEN avoided calls are eligible."""
    claim = build_vef_claim(paired_replay(), economics(), period="2026-Q3")
    assert claim["schema_version"] == CONTRACT_VERSION
    assert claim["measurement"]["observed"] == 820
    assert claim["financial_model"]["gross_value"] == 41.0
    assert claim["counterfactual"]["method"] == "matched_control"
    assert claim["evidence"]["value_eligible"] is True


def test_dangerous_miss_zeroes_claimable_value_but_preserves_raw_result():
    """GIVEN any dangerous miss THEN no avoided-call value is claimed."""
    claim = build_vef_claim(
        paired_replay(dangerous_misses=1), economics(), period="2026-Q3"
    )
    assert claim["measurement"]["raw_calls_avoided"] == 820
    assert claim["measurement"]["raw_inference_cost_avoided_usd"] == 41
    assert claim["measurement"]["observed"] == 0
    assert claim["financial_model"]["gross_value"] == 0
    assert claim["evidence"]["value_eligible"] is False


def test_unmatched_replay_cannot_claim_observed_savings():
    """GIVEN different workload identities THEN the counterfactual is not treated as matched."""
    replay = paired_replay()
    replay = ReplayEvidence(
        baseline=replay.baseline,
        cascade=ReplayArm("sha256:different", 1000, 180, 0),
        shadow_sampled_suppressions=820,
        total_suppressions=820,
    )
    claim = build_vef_claim(replay, economics(), period="2026-Q3")
    assert claim["counterfactual"]["method"] == "expert_estimate"
    assert claim["measurement"]["observed"] == 0
    assert claim["evidence"]["reproducible"] is False


def test_outcome_identity_prevents_cross_period_double_counting():
    q3 = build_vef_claim(paired_replay(), economics(), period="2026-Q3")
    q4 = build_vef_claim(paired_replay(), economics(), period="2026-Q4")
    assert q3["outcome_id"] != q4["outcome_id"]
    assert (
        q3["evidence"]["provenance"]["input_digest"]
        != q4["evidence"]["provenance"]["input_digest"]
    )


def test_invalid_economics_fail_closed():
    with pytest.raises(ValueError, match="non-negative"):
        CustomerEconomics(-0.01, 0)


def test_route_ledger_measures_actual_ai_eligible_population_and_cost():
    baseline_routes = (
        RouteUsage("existing_rules", 400, False),
        RouteUsage("baseline_ai", 600, True, 600, 90000, 12000, 480, 60),
    )
    cascade_routes = (
        RouteUsage("nano", 470, True),
        RouteUsage("micro", 450, True, 80, 12000, 1600, 20, 4),
        RouteUsage("macro", 80, True, 50, 10000, 2500, 45, 10),
    )
    replay = ReplayEvidence(
        ReplayArm("sha256:routes", 1000, 600, 0, baseline_routes),
        ReplayArm("sha256:routes", 1000, 130, 0, cascade_routes),
        470, 470,
    )
    effort = (
        EngineeringEffort("rule_authoring", "engineer", 2, 100, "initial", "work_log"),
        EngineeringEffort("drift_review", "engineer", 1, 100, "recurring", "work_log"),
    )
    claim = build_vef_claim(
        replay, CustomerEconomics(999, 50, True, effort), period="pilot"
    )
    assert claim["measurement"]["baseline_route_ledger"]["ai_eligible_signals"] == 600
    assert claim["measurement"]["baseline_route_ledger"]["ai_eligibility_rate"] == 0.6
    assert claim["measurement"]["cascade_route_ledger"]["actual_ai_calls"] == 130
    assert claim["financial_model"]["gross_value"] == 46
    assert claim["financial_model"]["cost_basis"] == "observed_route_cost"
    assert claim["realization_cost"] == 350


def test_route_ledger_rejects_unaccounted_signals():
    with pytest.raises(ValueError, match="partition"):
        ReplayArm("sha256:bad", 10, 1, 0, (RouteUsage("micro", 9, True, 1),))


def test_unmeasured_safety_or_incomplete_ai_work_zeroes_value():
    replay = paired_replay()
    incomplete = ReplayEvidence(
        replay.baseline,
        ReplayArm(replay.cascade.workload_digest, replay.cascade.total_signals,
                  replay.cascade.model_calls, 0, (), False, False),
        replay.shadow_sampled_suppressions,
        replay.total_suppressions,
    )
    claim = build_vef_claim(incomplete, economics(), period="pilot")
    assert claim["financial_model"]["gross_value"] == 0
    assert claim["evidence"]["value_eligible"] is False


def test_missing_route_cost_falls_back_to_average_call_cost():
    replay = ReplayEvidence(
        ReplayArm(
            "sha256:cost-fallback",
            10,
            10,
            0,
            (RouteUsage("baseline_ai", 10, True, 10),),
        ),
        ReplayArm(
            "sha256:cost-fallback",
            10,
            2,
            0,
            (RouteUsage("micro", 10, True, 2),),
        ),
        8,
        8,
    )
    claim = build_vef_claim(replay, economics(), period="pilot")
    assert claim["financial_model"]["cost_basis"] == "average_call_cost"
    assert claim["financial_model"]["gross_value"] == 0.4
    assert claim["measurement"]["baseline_route_ledger"]["inference_cost_usd"] is None


def test_explicit_zero_route_cost_is_observed():
    replay = ReplayEvidence(
        ReplayArm(
            "sha256:zero-cost",
            10,
            10,
            0,
            (RouteUsage("baseline_ai", 10, True, 10, inference_cost_usd=0.0),),
        ),
        ReplayArm(
            "sha256:zero-cost",
            10,
            2,
            0,
            (RouteUsage("micro", 10, True, 2, inference_cost_usd=0.0),),
        ),
        8,
        8,
    )
    claim = build_vef_claim(replay, economics(), period="pilot")
    assert claim["financial_model"]["cost_basis"] == "observed_route_cost"
    assert claim["financial_model"]["gross_value"] == 0


def test_unknown_route_calls_do_not_become_claimed_savings():
    replay = ReplayEvidence(
        ReplayArm(
            "sha256:unknown",
            10,
            10,
            0,
            (RouteUsage("unknown", 10, True, 10, inference_cost_usd=5),),
        ),
        ReplayArm(
            "sha256:unknown",
            10,
            0,
            0,
            (RouteUsage("nano", 10, True),),
        ),
        10,
        10,
    )
    claim = build_vef_claim(replay, economics(), period="pilot")
    assert claim["measurement"]["raw_calls_avoided"] == 10
    assert claim["measurement"]["observed"] == 0
    assert claim["financial_model"]["gross_value"] == 0


def test_product_share_is_bound_into_provenance():
    full = build_vef_claim(paired_replay(), economics(), period="pilot", product_share=1)
    partial = build_vef_claim(
        paired_replay(), economics(), period="pilot", product_share=0.5
    )
    assert (
        full["evidence"]["provenance"]["input_digest"]
        != partial["evidence"]["provenance"]["input_digest"]
    )
