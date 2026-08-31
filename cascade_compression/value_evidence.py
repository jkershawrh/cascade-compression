"""Export observed Cascade replay results as a Value Evidence Framework claim.

This module does not infer customer savings from the configured TCO calculator. It requires a
paired replay and explicit customer economics, and preserves those inputs in the exported claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any


CONTRACT_VERSION = "vef.claim.v1alpha1"
CALCULATION_VERSION = "cascade.replay-value.v2"


@dataclass(frozen=True)
class RouteUsage:
    """Terminal route totals for one replay arm.

    ``signal_count`` partitions the input population. Calls, tokens, runtime, and cost describe
    actual AI use on that route; none are inferred from compression.
    """

    route: str
    signal_count: int
    ai_eligible: bool
    ai_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    inference_seconds: float = 0.0
    inference_cost_usd: float | None = None

    def __post_init__(self) -> None:
        values = (self.signal_count, self.ai_calls, self.input_tokens, self.output_tokens,
                  self.inference_seconds)
        if not self.route:
            raise ValueError("route is required")
        if min(values) < 0:
            raise ValueError("route usage values must be non-negative")
        if self.inference_cost_usd is not None and self.inference_cost_usd < 0:
            raise ValueError("route usage values must be non-negative")
        if self.ai_calls > self.signal_count:
            raise ValueError("route ai_calls cannot exceed signal_count")
        if not self.ai_eligible and self.ai_calls:
            raise ValueError("a non-AI-eligible route cannot contain AI calls")


@dataclass(frozen=True)
class EngineeringEffort:
    """Fully loaded human effort required to realize or operate Cascade value."""

    activity: str
    role: str
    hours: float
    loaded_rate_usd: float
    lifecycle: str  # initial or recurring
    source: str

    def __post_init__(self) -> None:
        if not self.activity or not self.role or not self.source:
            raise ValueError("effort activity, role, and source are required")
        if self.lifecycle not in {"initial", "recurring"}:
            raise ValueError("effort lifecycle must be initial or recurring")
        if self.hours < 0 or self.loaded_rate_usd < 0:
            raise ValueError("effort hours and rate must be non-negative")


@dataclass(frozen=True)
class ReplayArm:
    """One arm of a replay over a named, immutable workload."""

    workload_digest: str
    total_signals: int
    model_calls: int
    dangerous_misses: int
    routes: tuple[RouteUsage, ...] = ()
    dangerous_misses_measured: bool = True
    ai_work_complete: bool = True

    def __post_init__(self) -> None:
        if not self.workload_digest:
            raise ValueError("workload_digest is required")
        if min(self.total_signals, self.model_calls, self.dangerous_misses) < 0:
            raise ValueError("replay counts must be non-negative")
        if self.model_calls > self.total_signals:
            raise ValueError("model_calls cannot exceed total_signals")
        if self.routes:
            if sum(route.signal_count for route in self.routes) != self.total_signals:
                raise ValueError("route signal counts must partition total_signals")
            if sum(route.ai_calls for route in self.routes) != self.model_calls:
                raise ValueError("route AI calls must equal model_calls")


@dataclass(frozen=True)
class ReplayEvidence:
    baseline: ReplayArm
    cascade: ReplayArm
    shadow_sampled_suppressions: int
    total_suppressions: int

    def __post_init__(self) -> None:
        if min(self.shadow_sampled_suppressions, self.total_suppressions) < 0:
            raise ValueError("shadow counts must be non-negative")
        if self.shadow_sampled_suppressions > self.total_suppressions:
            raise ValueError("shadow sample cannot exceed total suppressions")


@dataclass(frozen=True)
class CustomerEconomics:
    model_call_cost_usd: float
    realization_cost_usd: float
    customer_validated: bool = False
    engineering_effort: tuple[EngineeringEffort, ...] = ()

    def __post_init__(self) -> None:
        if self.model_call_cost_usd < 0 or self.realization_cost_usd < 0:
            raise ValueError("economic inputs must be non-negative")


def _is_matched(evidence: ReplayEvidence) -> bool:
    return (
        evidence.baseline.workload_digest == evidence.cascade.workload_digest
        and evidence.baseline.total_signals == evidence.cascade.total_signals
        and evidence.baseline.total_signals > 0
    )


def _known_ai_calls(arm: ReplayArm) -> int:
    """Return calls with explicit AI eligibility, excluding unknown routes."""

    if not arm.routes:
        return arm.model_calls
    return sum(route.ai_calls for route in arm.routes if route.route != "unknown")


def _known_route_cost_available(arm: ReplayArm) -> bool:
    """Require an explicit cost for every known route that made an AI call."""

    return bool(arm.routes) and all(
        route.inference_cost_usd is not None
        for route in arm.routes
        if route.route != "unknown" and route.ai_calls
    )


def _known_inference_cost(arm: ReplayArm) -> float:
    return sum(
        route.inference_cost_usd or 0.0
        for route in arm.routes
        if route.route != "unknown"
    )


def build_vef_claim(
    evidence: ReplayEvidence,
    economics: CustomerEconomics,
    *,
    period: str,
    product_share: float = 1.0,
) -> dict[str, Any]:
    """Build a portable VEF claim without overstating modeled or unsafe results."""

    if not period:
        raise ValueError("period is required")
    if not 0 <= product_share <= 1:
        raise ValueError("product_share must be between 0 and 1")

    matched = _is_matched(evidence)
    calls_avoided = max(0, evidence.baseline.model_calls - evidence.cascade.model_calls)
    safe = (
        evidence.cascade.dangerous_misses == 0
        and evidence.cascade.dangerous_misses_measured
        and evidence.cascade.ai_work_complete
    )
    known_calls_avoided = max(
        0, _known_ai_calls(evidence.baseline) - _known_ai_calls(evidence.cascade)
    )
    claimable_calls = min(calls_avoided, known_calls_avoided)
    eligible_calls = claimable_calls if matched and safe else 0
    route_cost_available = (
        _known_route_cost_available(evidence.baseline)
        and _known_route_cost_available(evidence.cascade)
    )
    baseline_inference_cost = _known_inference_cost(evidence.baseline)
    cascade_inference_cost = _known_inference_cost(evidence.cascade)
    raw_avoided_cost = (
        max(0.0, baseline_inference_cost - cascade_inference_cost)
        if route_cost_available
        else claimable_calls * economics.model_call_cost_usd
    )
    gross_value = round(raw_avoided_cost if matched and safe else 0.0, 2)
    effort_rows = [
        {**asdict(item), "cost_usd": round(item.hours * item.loaded_rate_usd, 2)}
        for item in economics.engineering_effort
    ]
    initial_effort_cost = sum(row["cost_usd"] for row in effort_rows
                              if row["lifecycle"] == "initial")
    recurring_effort_cost = sum(row["cost_usd"] for row in effort_rows
                                if row["lifecycle"] == "recurring")
    realization_cost = round(
        economics.realization_cost_usd + initial_effort_cost + recurring_effort_cost, 2
    )
    coverage = (
        evidence.shadow_sampled_suppressions / evidence.total_suppressions
        if evidence.total_suppressions else 0.0
    )

    confidence = "medium" if matched and safe else "low"
    sources = ["paired_replay", "customer_economics"]
    if coverage:
        sources.append("shadow_validation")

    provenance = {
        "contract_version": CONTRACT_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "workload_digest": evidence.cascade.workload_digest,
        "input_digest": sha256(
            json.dumps(
                {
                    "evidence": asdict(evidence),
                    "economics": asdict(economics),
                    "period": period,
                    "product_share": product_share,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }

    def route_summary(arm: ReplayArm) -> dict[str, Any]:
        eligible = sum(r.signal_count for r in arm.routes if r.ai_eligible)
        unknown = sum(r.signal_count for r in arm.routes if r.route == "unknown")
        route_cost_available = _known_route_cost_available(arm)
        return {
            "total_signals": arm.total_signals,
            "ai_eligible_signals": eligible if arm.routes else None,
            "ai_eligibility_rate": round(eligible / arm.total_signals, 6)
            if arm.routes and arm.total_signals else None,
            "actual_ai_calls": arm.model_calls,
            "ai_call_rate": round(arm.model_calls / eligible, 6) if eligible else None,
            "unknown_signals": unknown,
            "input_tokens": sum(r.input_tokens for r in arm.routes),
            "output_tokens": sum(r.output_tokens for r in arm.routes),
            "inference_seconds": round(sum(r.inference_seconds for r in arm.routes), 6),
            "inference_cost_usd": round(_known_inference_cost(arm), 2)
            if route_cost_available else None,
            "routes": [asdict(r) for r in arm.routes],
        }

    return {
        "schema_version": CONTRACT_VERSION,
        "id": "cascade.model-calls-avoided",
        "product": "cascade-compression",
        "outcome_id": f"inference-cost:{evidence.cascade.workload_digest}:{period}",
        "value_type": "cost_avoidance",
        "measurement": {
            "observed": eligible_calls,
            "unit": "model_calls_avoided",
            "period": period,
            "raw_calls_avoided": calls_avoided,
            "raw_inference_cost_avoided_usd": round(raw_avoided_cost, 2),
            "baseline_route_ledger": route_summary(evidence.baseline),
            "cascade_route_ledger": route_summary(evidence.cascade),
        },
        "counterfactual": {
            "method": "matched_control" if matched else "expert_estimate",
            "expected_without_product": evidence.baseline.model_calls,
            "matched_workload": matched,
        },
        "attribution": {
            "product_share": product_share,
            "competing_factors": ["workload_mix", "model_routing", "unit_cost_assumption"],
        },
        "financial_model": {
            "gross_value": gross_value,
            "currency": "USD",
            "customer_validated": economics.customer_validated,
            "model_call_cost_usd": economics.model_call_cost_usd,
            "cost_basis": "observed_route_cost" if route_cost_available else "average_call_cost",
            "engineering_effort": effort_rows,
            "initial_engineering_cost_usd": round(initial_effort_cost, 2),
            "recurring_engineering_cost_usd": round(recurring_effort_cost, 2),
        },
        "evidence": {
            "confidence": confidence,
            "sources": sources,
            "reproducible": matched,
            "dangerous_misses": evidence.cascade.dangerous_misses,
            "dangerous_misses_measured": evidence.cascade.dangerous_misses_measured,
            "ai_work_complete": evidence.cascade.ai_work_complete,
            "shadow_validation_coverage": round(coverage, 6),
            "value_eligible": matched and safe,
            "provenance": provenance,
        },
        "realization_cost": realization_cost,
    }
