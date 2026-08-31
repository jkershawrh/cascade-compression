"""Build a VEF claim from explicit replay and economics JSON artifacts."""

import argparse
import json
from pathlib import Path

from .value_evidence import (
    CustomerEconomics,
    EngineeringEffort,
    ReplayArm,
    ReplayEvidence,
    RouteUsage,
    build_vef_claim,
)


def _arm(data: dict) -> ReplayArm:
    routes = tuple(RouteUsage(**route) for route in data.get("routes", []))
    return ReplayArm(
        workload_digest=data["workload_digest"],
        total_signals=data["total_signals"],
        model_calls=data["model_calls"],
        dangerous_misses=data.get("dangerous_misses", 0),
        routes=routes,
        dangerous_misses_measured=data.get("dangerous_misses_measured", True),
        ai_work_complete=data.get("ai_work_complete", True),
    )


def export_claim(replay_data: dict, economics_data: dict, *, period: str) -> dict:
    replay = ReplayEvidence(
        baseline=_arm(replay_data["baseline"]),
        cascade=_arm(replay_data["cascade"]),
        shadow_sampled_suppressions=replay_data.get("shadow_sampled_suppressions", 0),
        total_suppressions=replay_data.get("total_suppressions", 0),
    )
    efforts = tuple(EngineeringEffort(**row) for row in economics_data.get("engineering_effort", []))
    economics = CustomerEconomics(
        model_call_cost_usd=economics_data.get("model_call_cost_usd", 0),
        realization_cost_usd=economics_data.get("other_realization_cost_usd", 0),
        customer_validated=economics_data.get("customer_validated", False),
        engineering_effort=efforts,
    )
    return build_vef_claim(
        replay, economics, period=period,
        product_share=economics_data.get("product_share", 1.0),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a Cascade replay as a VEF claim")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--replay-result",
                        help="Combined JSON containing baseline and cascade arms")
    source.add_argument("--cascade-result",
                        help="Cascade replay-arm JSON; requires --baseline-result")
    parser.add_argument("--baseline-result",
                        help="Observed baseline replay-arm JSON paired with --cascade-result")
    parser.add_argument("--economics", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.cascade_result:
        if not args.baseline_result:
            parser.error("--cascade-result requires --baseline-result")
        cascade_data = json.loads(Path(args.cascade_result).read_text())
        baseline_data = json.loads(Path(args.baseline_result).read_text())
        replay_data = {
            "baseline": baseline_data,
            "cascade": cascade_data,
            "shadow_sampled_suppressions": cascade_data.get(
                "shadow_sampled_suppressions", 0
            ),
            "total_suppressions": cascade_data.get("total_suppressions", 0),
        }
    else:
        replay_data = json.loads(Path(args.replay_result).read_text())
    economics_data = json.loads(Path(args.economics).read_text())
    claim = export_claim(replay_data, economics_data, period=args.period)
    Path(args.output).write_text(json.dumps(claim, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
