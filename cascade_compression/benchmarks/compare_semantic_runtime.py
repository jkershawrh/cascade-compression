"""Compare two pinned llm-d-sc runtime benchmark artifacts.

This gate intentionally compares runtime behavior only. Classification quality
and compression safety require the separate frozen, human-adjudicated corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _semantic_cells(document: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    if document.get("schema_version") != 1:
        raise ValueError("runtime benchmark schema_version must be 1")
    cells = document.get("results", {}).get("semantic")
    if not isinstance(cells, list) or not cells:
        raise ValueError("runtime benchmark has no semantic results")
    indexed = {}
    for cell in cells:
        key = (str(cell.get("workload", "")), int(cell.get("concurrency", 0)))
        if not key[0] or key[1] < 1 or key in indexed:
            raise ValueError(f"invalid or duplicate semantic cell {key}")
        indexed[key] = cell
    return indexed


def _identity(document: dict[str, Any]) -> dict[str, Any]:
    contract = document.get("semantic_contract")
    if not isinstance(contract, dict):
        raise ValueError("runtime benchmark has no semantic_contract")
    if not str(contract.get("runtime_source_revision", "")).strip():
        raise ValueError("runtime_source_revision is required")
    observed = contract.get("observed")
    if not isinstance(observed, dict):
        raise ValueError("observed semantic identity is required")
    required = {
        "classifier_id", "model_revision", "tokenizer_revision",
        "taxonomy_revision", "scoring_mode",
    }
    if any(not str(observed.get(field, "")).strip() for field in required):
        raise ValueError("observed semantic identity is incomplete")
    return observed


def compare_runtime(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_p95_regression_pct: float = 10.0,
    min_throughput_ratio: float = 0.90,
    min_success_rate: float = 1.0,
) -> dict[str, Any]:
    baseline_identity = _identity(baseline)
    candidate_identity = _identity(candidate)
    if baseline_identity != candidate_identity:
        raise ValueError("runtime comparison changed classifier artifact identity")

    baseline_cells = _semantic_cells(baseline)
    candidate_cells = _semantic_cells(candidate)
    if set(baseline_cells) != set(candidate_cells):
        raise ValueError("runtime comparison cells do not match")

    comparisons = []
    failures = []
    for key in sorted(baseline_cells):
        before = baseline_cells[key]
        after = candidate_cells[key]
        before_p95 = float(before["latency_ms"]["p95"])
        after_p95 = float(after["latency_ms"]["p95"])
        before_tput = float(before["throughput_signals_per_second"])
        after_tput = float(after["throughput_signals_per_second"])
        p95_change_pct = (
            ((after_p95 - before_p95) / before_p95) * 100
            if before_p95 > 0 else 0.0
        )
        throughput_ratio = after_tput / before_tput if before_tput > 0 else 1.0
        success_rate = float(after.get("success_rate", 0.0))
        cell_failures = []
        if int(after.get("errors", 0)) > int(before.get("errors", 0)):
            cell_failures.append("errors increased")
        if success_rate < min_success_rate:
            cell_failures.append("success rate below gate")
        if p95_change_pct > max_p95_regression_pct:
            cell_failures.append("p95 latency regression exceeded gate")
        if throughput_ratio < min_throughput_ratio:
            cell_failures.append("throughput regression exceeded gate")
        label = f"{key[0]}@c{key[1]}"
        failures.extend(f"{label}: {reason}" for reason in cell_failures)
        comparisons.append({
            "workload": key[0],
            "concurrency": key[1],
            "baseline_p95_ms": before_p95,
            "candidate_p95_ms": after_p95,
            "p95_change_pct": round(p95_change_pct, 3),
            "baseline_throughput": before_tput,
            "candidate_throughput": after_tput,
            "throughput_ratio": round(throughput_ratio, 4),
            "candidate_success_rate": success_rate,
            "passed": not cell_failures,
        })

    return {
        "schema_version": "cascade.semantic-runtime-comparison.v1alpha1",
        "status": "pass" if not failures else "fail",
        "artifact_identity": baseline_identity,
        "baseline_runtime_source_revision": baseline["semantic_contract"][
            "runtime_source_revision"
        ],
        "candidate_runtime_source_revision": candidate["semantic_contract"][
            "runtime_source_revision"
        ],
        "gates": {
            "max_p95_regression_pct": max_p95_regression_pct,
            "min_throughput_ratio": min_throughput_ratio,
            "min_success_rate": min_success_rate,
        },
        "comparisons": comparisons,
        "failures": failures,
        "scope": "runtime-only; classification quality requires adjudicated evaluation",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-p95-regression-pct", type=float, default=10.0)
    parser.add_argument("--min-throughput-ratio", type=float, default=0.90)
    parser.add_argument("--min-success-rate", type=float, default=1.0)
    args = parser.parse_args()
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    report = compare_runtime(
        baseline,
        candidate,
        max_p95_regression_pct=args.max_p95_regression_pct,
        min_throughput_ratio=args.min_throughput_ratio,
        min_success_rate=args.min_success_rate,
    )
    Path(args.output).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "failures": report["failures"]}))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
