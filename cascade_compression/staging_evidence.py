"""Build a sanitized, fail-closed staging-success evidence bundle."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from .classifier import CASCADE_LABELS
from .evaluation import REQUIRED_RUN_FIELDS, run_identity_errors


OSS_RC_A_PROFILE = {
    "minimum_records": 200,
    "minimum_per_label_support": 25,
    "minimum_important_support": 50,
    "minimum_authoritative_suppressions": 25,
    "minimum_coverage": 0.95,
    "minimum_balanced_accuracy": 0.85,
    "minimum_macro_f1": 0.80,
    "minimum_authoritative_suppression_precision": 0.98,
    "minimum_calibration_coverage": 0.95,
    "maximum_expected_calibration_error": 0.10,
}

REQUIRED_CLASSIFICATION_ARMS = frozenset({"generative", "semantic", "hybrid"})

OSS_RC_RUNTIME_PROFILE = {
    "minimum_iterations": 500,
    "minimum_samples": 500,
    "minimum_warmup": 100,
    "minimum_mixed_iterations": 500,
    "minimum_mixed_http_samples": 100,
    "minimum_parallel_concurrency": 8,
    "minimum_cell_samples": 500,
    "minimum_nano_signals_per_second_per_core": 1000.0,
    "maximum_nano_batch_1_p95_ms": 5.0,
    "maximum_http_concurrency_1_p95_ms": 100.0,
    "maximum_mixed_http_p95_ms": 1000.0,
    "maximum_semantic_cache_hit_p95_ms": 250.0,
    "maximum_semantic_unique_miss_p95_ms": 2000.0,
    "maximum_recall_1000_p95_ms": 50.0,
    "maximum_recall_10000_p95_ms": 250.0,
}

OUTBOX_POLICY = "immutable-relay-and-archive-v1"
MAX_RECOVERY_DRILL_AGE_SECONDS = 90 * 24 * 60 * 60


def _digest(document: Dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _runtime_cells(runtime: dict) -> Iterable[dict]:
    for section in (runtime.get("results") or {}).values():
        if isinstance(section, list):
            yield from (item for item in section if isinstance(item, dict))
        elif isinstance(section, dict):
            yield section


def _gate(name: str, passed: bool, evidence: Any) -> dict:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def _matching_cell(cells: Iterable[dict], **criteria: Any) -> dict:
    return next((
        cell for cell in cells
        if all(cell.get(key) == value for key, value in criteria.items())
    ), {})


def _p95(cell: dict) -> Optional[float]:
    try:
        value = float((cell.get("latency_ms") or {}).get("p95"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _within_p95(cell: dict, maximum: float) -> bool:
    value = _p95(cell)
    return value is not None and value <= maximum


def _samples_at_least(cell: dict, minimum: int) -> bool:
    return int(cell.get("samples") or 0) >= minimum


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _bounded_number(value: Any, minimum: float, maximum: float) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and minimum <= numeric <= maximum


def build_staging_evidence(
    manifest: Dict[str, Any],
    classification: Dict[str, Any],
    runtime: Dict[str, Any],
    stats_before: Dict[str, Any],
    stats_after: Dict[str, Any],
) -> dict:
    """Bind sanitized artifacts and apply explicit release-proof gates."""
    if manifest.get("schema_version") != "cascade.staging-manifest.v1alpha3":
        raise ValueError("unsupported staging manifest version")
    if classification.get("schema_version") != "cascade.classification-evaluation.v1alpha2":
        raise ValueError("classification artifact has an unsupported version")
    if runtime.get("schema_version") != 1:
        raise ValueError("runtime artifact has an unsupported version")

    run = manifest.get("run") or {}
    missing_run = [field for field in REQUIRED_RUN_FIELDS if not run.get(field)]
    invalid_run = run_identity_errors(run)
    try:
        positive_window = (
            not missing_run
            and _parse_time(run["window_end"]) > _parse_time(run["window_start"])
        )
    except (TypeError, ValueError):
        positive_window = False

    classification_arm = str(manifest.get("classification_arm") or "hybrid")
    classification_arms = classification.get("arms") or {}
    arm = classification_arms.get(classification_arm)
    if arm is None:
        raise ValueError(f"classification arm not found: {classification_arm}")
    classification_run = classification.get("run") or {}
    run_matches = all(
        classification_run.get(field) == run.get(field)
        for field in REQUIRED_RUN_FIELDS
    )
    model_revisions = manifest.get("model_revisions") or {}
    classification_models = classification.get("model_revisions") or {}
    models_frozen = bool(model_revisions) and classification_models == model_revisions
    candidate = manifest.get("candidate") or {}
    candidate_bound = (
        candidate.get("schema_version")
        == "cascade.staging-candidate.v1alpha1"
        and bool(candidate.get("repository"))
        and bool(candidate.get("image"))
        and candidate.get("commit") == run.get("commit")
        and candidate.get("image_digest") == run.get("image_digest")
        and _positive_int(candidate.get("workflow_run_id"))
        and _positive_int(candidate.get("workflow_run_attempt"))
        and candidate.get("sbom") is True
        and candidate.get("provenance") is True
        and {"linux/amd64", "linux/arm64"}
        <= set(candidate.get("multi_arch") or [])
    )
    try:
        candidate_precedes_run = (
            _parse_time(candidate.get("generated_at", ""))
            <= _parse_time(run.get("window_start", ""))
        )
    except (TypeError, ValueError):
        candidate_precedes_run = False
    pairwise = classification.get("pairwise_agreement") or {}
    required_pairs = {
        "generative__hybrid", "generative__semantic", "hybrid__semantic",
    }
    comparison_records = int(arm.get("records") or 0)
    minimum_pairwise_compared = math.ceil(
        comparison_records * OSS_RC_A_PROFILE["minimum_coverage"]
    )
    comparison_complete = (
        classification_arm == "hybrid"
        and REQUIRED_CLASSIFICATION_ARMS <= set(classification_arms)
        and REQUIRED_CLASSIFICATION_ARMS <= set(classification_models)
        and required_pairs <= set(pairwise)
        and all(
            int(classification_arms[name].get("records") or 0)
            == int(arm.get("records") or 0)
            and float(classification_arms[name].get("coverage") or 0)
            >= OSS_RC_A_PROFILE["minimum_coverage"]
            for name in REQUIRED_CLASSIFICATION_ARMS
        )
        and all(
            int(pairwise[name].get("compared") or 0)
            >= minimum_pairwise_compared
            and pairwise[name].get("is_accuracy") is False
            for name in required_pairs
        )
    )

    calibration = arm.get("calibration") or {}
    suppression_precision = arm.get("authoritative_suppression_precision")
    a_profile = OSS_RC_A_PROFILE
    per_label = arm.get("per_label") or {}
    per_label_support = {
        label: int(metrics.get("support") or 0)
        for label, metrics in per_label.items()
    }
    classification_evidence = classification.get("evidence") or {}
    classification_consistent = (
        classification_evidence.get("corpus_binding") is True
        and classification_evidence.get("adjudication_provenance_bound") is True
        and classification_evidence.get("contains_raw_records") is False
        and classification_evidence.get("model_revisions_frozen") is True
        and int((classification.get("dataset") or {}).get("records") or 0)
        == int(arm.get("records") or 0)
        and set(per_label_support) == set(CASCADE_LABELS)
    )
    a_profile_passed = (
        int(arm.get("records") or 0) >= a_profile["minimum_records"]
        and set(per_label_support) == set(CASCADE_LABELS)
        and all(
            support >= a_profile["minimum_per_label_support"]
            for support in per_label_support.values()
        )
        and int(arm.get("important_support") or 0)
        >= a_profile["minimum_important_support"]
        and int(arm.get("authoritative_suppressions") or 0)
        >= a_profile["minimum_authoritative_suppressions"]
        and _bounded_number(
            arm.get("coverage"), a_profile["minimum_coverage"], 1.0,
        )
        and _bounded_number(
            arm.get("balanced_accuracy"),
            a_profile["minimum_balanced_accuracy"], 1.0,
        )
        and _bounded_number(
            arm.get("macro_f1"), a_profile["minimum_macro_f1"], 1.0,
        )
        and suppression_precision is not None
        and _bounded_number(
            suppression_precision,
            a_profile["minimum_authoritative_suppression_precision"], 1.0,
        )
        and calibration.get("status") == "measured"
        and _bounded_number(
            calibration.get("coverage"),
            a_profile["minimum_calibration_coverage"], 1.0,
        )
        and _bounded_number(
            calibration.get("expected_calibration_error"), 0.0,
            a_profile["maximum_expected_calibration_error"],
        )
    )

    runtime_environment = runtime.get("environment") or {}
    runtime_config = runtime.get("config") or {}
    cells = list(_runtime_cells(runtime))
    runtime_errors = sum(int(cell.get("errors") or 0) for cell in cells)
    oracle_failures = sum(
        int(value is False)
        for cell in cells
        for key, value in cell.items()
        if key.endswith("_oracle_passed")
    )
    results = runtime.get("results") or {}
    nano_cells = results.get("nano") if isinstance(results.get("nano"), list) else []
    http_cells = results.get("http") if isinstance(results.get("http"), list) else []
    semantic_cells = (
        results.get("semantic") if isinstance(results.get("semantic"), list) else []
    )
    recall_cells = (
        results.get("recall") if isinstance(results.get("recall"), list) else []
    )
    nano_1 = _matching_cell(nano_cells, batch_size=1)
    http_1 = _matching_cell(http_cells, concurrency=1)
    semantic_hit_1 = _matching_cell(
        semantic_cells, workload="normalized_cache_hit", concurrency=1,
    )
    semantic_miss_1 = _matching_cell(
        semantic_cells, workload="unique_cache_miss", concurrency=1,
    )
    recall_1000 = _matching_cell(recall_cells, memory_count=1000)
    recall_10000 = _matching_cell(recall_cells, memory_count=10000)
    mixed_nano = results.get("mixed_nano") or {}
    mixed_http = results.get("mixed_http") or {}
    http_parallel = max(
        [int(cell.get("concurrency") or 0) for cell in http_cells], default=0,
    )
    semantic_hit_parallel = max([
        int(cell.get("concurrency") or 0) for cell in semantic_cells
        if cell.get("workload") == "normalized_cache_hit"
    ], default=0)
    semantic_miss_parallel = max([
        int(cell.get("concurrency") or 0) for cell in semantic_cells
        if cell.get("workload") == "unique_cache_miss"
    ], default=0)
    parallel_concurrency = min(
        http_parallel, semantic_hit_parallel, semantic_miss_parallel,
    )
    http_parallel_cell = _matching_cell(http_cells, concurrency=http_parallel)
    semantic_hit_parallel_cell = _matching_cell(
        semantic_cells,
        workload="normalized_cache_hit", concurrency=semantic_hit_parallel,
    )
    semantic_miss_parallel_cell = _matching_cell(
        semantic_cells,
        workload="unique_cache_miss", concurrency=semantic_miss_parallel,
    )
    runtime_profile = OSS_RC_RUNTIME_PROFILE
    runtime_profile_passed = (
        int(runtime_config.get("iterations") or 0)
        >= runtime_profile["minimum_iterations"]
        and int(runtime_config.get("samples") or 0)
        >= runtime_profile["minimum_samples"]
        and int(runtime_config.get("warmup") or 0)
        >= runtime_profile["minimum_warmup"]
        and int(runtime_config.get("mixed_iterations") or 0)
        >= runtime_profile["minimum_mixed_iterations"]
        and int(runtime_config.get("mixed_http_samples") or 0)
        >= runtime_profile["minimum_mixed_http_samples"]
        and parallel_concurrency >= runtime_profile["minimum_parallel_concurrency"]
        and bool(nano_1) and bool(http_1)
        and bool(semantic_hit_1) and bool(semantic_miss_1)
        and bool(mixed_nano) and bool(mixed_http)
        and bool(recall_1000) and bool(recall_10000)
        and all(_samples_at_least(cell, runtime_profile["minimum_cell_samples"])
                for cell in (
                    nano_1, http_1, http_parallel_cell,
                    semantic_hit_1, semantic_hit_parallel_cell,
                    semantic_miss_1, semantic_miss_parallel_cell,
                    mixed_nano, recall_1000, recall_10000,
                ))
        and _samples_at_least(
            mixed_http, runtime_profile["minimum_mixed_http_samples"],
        )
        and float(nano_1.get("throughput_signals_per_second_per_core") or 0)
        >= runtime_profile["minimum_nano_signals_per_second_per_core"]
        and _within_p95(nano_1, runtime_profile["maximum_nano_batch_1_p95_ms"])
        and _within_p95(http_1, runtime_profile["maximum_http_concurrency_1_p95_ms"])
        and _within_p95(mixed_http, runtime_profile["maximum_mixed_http_p95_ms"])
        and _within_p95(
            semantic_hit_1,
            runtime_profile["maximum_semantic_cache_hit_p95_ms"],
        )
        and _within_p95(
            semantic_miss_1,
            runtime_profile["maximum_semantic_unique_miss_p95_ms"],
        )
        and _within_p95(
            recall_1000, runtime_profile["maximum_recall_1000_p95_ms"],
        )
        and _within_p95(
            recall_10000, runtime_profile["maximum_recall_10000_p95_ms"],
        )
    )

    verification = manifest.get("verification") or {}
    audit_window = manifest.get("audit_window") or {}
    try:
        run_start = _parse_time(run["window_start"])
        run_end = _parse_time(run["window_end"])
        benchmark_started = _parse_time(runtime.get("generated_at", ""))
        benchmark_completed = _parse_time(runtime.get("completed_at", ""))
        runtime_in_window = (
            run_start <= benchmark_started <= benchmark_completed <= run_end
        )
        audit_brackets_window = (
            _parse_time(audit_window.get("stats_before_at", "")) <= run_start
            and _parse_time(audit_window.get("stats_after_at", "")) >= run_end
        )
    except (KeyError, TypeError, ValueError):
        runtime_in_window = False
        audit_brackets_window = False
    ledger = manifest.get("ledger") or {}
    outbox = ledger.get("outbox") or {}
    drop_fields = ("ledger_writes_dropped", "ledger_memory_events_dropped")
    drop_deltas = {
        field: int(stats_after.get(field) or 0) - int(stats_before.get(field) or 0)
        for field in drop_fields
    }
    audit_healthy = (
        stats_after.get("ledger_receipt_queue_durability") == "sqlite"
        and stats_after.get("ledger_memory_queue_durability") == "sqlite"
        and int(stats_after.get("ledger_receipt_pending") or 0) == 0
        and int(stats_after.get("ledger_memory_pending") or 0) == 0
        and int(stats_after.get("ledger_receipt_consecutive_failures") or 0) == 0
        and int(stats_after.get("ledger_memory_consecutive_failures") or 0) == 0
        and all(delta == 0 for delta in drop_deltas.values())
    )
    capacity_healthy = (
        ledger.get("capacity_measured") is True
        and 0 <= float(ledger.get("used_fraction", 2))
        < float(ledger.get("alert_threshold", 0)) <= 1
    )
    try:
        relay_success = _parse_time(outbox.get("last_relay_success_at", ""))
        recovery_drill = _parse_time(outbox.get("recovery_drill_at", ""))
        stats_before_at = _parse_time(audit_window.get("stats_before_at", ""))
        stats_after_at = _parse_time(audit_window.get("stats_after_at", ""))
        outbox_times_healthy = (
            stats_before_at <= relay_success <= stats_after_at
            and recovery_drill <= run_start
            and 0 <= (run_start - recovery_drill).total_seconds()
            <= MAX_RECOVERY_DRILL_AGE_SECONDS
        )
    except (TypeError, ValueError, UnboundLocalError):
        outbox_times_healthy = False
    outbox_healthy = (
        outbox.get("status") == "healthy"
        and outbox.get("policy") == OUTBOX_POLICY
        and int(outbox.get("pending") or 0) == 0
        and int(outbox.get("inflight") or 0) == 0
        and int(outbox.get("failed") or 0) == 0
        and float(outbox.get("oldest_pending_seconds") or 0) == 0
        and outbox.get("archive_verified") is True
        and int(outbox.get("undelivered_deleted") or 0) == 0
        and outbox_times_healthy
    )

    gates = [
        _gate("immutable_run_identity", (
            not missing_run and not invalid_run and positive_window
        ), {
            "missing_fields": missing_run, "invalid_fields": invalid_run,
            "positive_window": positive_window,
        }),
        _gate("candidate_artifact_bound", (
            candidate_bound and candidate_precedes_run
        ), {
            "schema_version": candidate.get("schema_version"),
            "repository": candidate.get("repository"),
            "commit_matches": candidate.get("commit") == run.get("commit"),
            "image_digest_matches": (
                candidate.get("image_digest") == run.get("image_digest")
            ),
            "workflow_run_id": candidate.get("workflow_run_id"),
            "workflow_run_attempt": candidate.get("workflow_run_attempt"),
            "candidate_precedes_run": candidate_precedes_run,
            "multi_arch": candidate.get("multi_arch"),
            "sbom": candidate.get("sbom"),
            "provenance": candidate.get("provenance"),
        }),
        _gate("classification_same_run", run_matches, {
            "arm": classification_arm,
            "dataset_digest": (classification.get("dataset") or {}).get("digest"),
            "holdout_digest": (classification.get("dataset") or {}).get(
                "holdout_digest"
            ),
        }),
        _gate("classification_models_frozen", models_frozen, {
            "declared": model_revisions,
            "evaluated": classification_models,
        }),
        _gate("classification_three_arm_comparison", comparison_complete, {
            "selected_arm": classification_arm,
            "required_arms": sorted(REQUIRED_CLASSIFICATION_ARMS),
            "observed_arms": sorted(classification_arms),
            "required_pairs": sorted(required_pairs),
            "observed_pairs": sorted(pairwise),
            "minimum_pairwise_compared": minimum_pairwise_compared,
        }),
        _gate(
            "classification_decision_grade",
            classification_evidence.get("status") == "decision_grade",
            classification_evidence.get("status"),
        ),
        _gate(
            "classification_artifact_consistent",
            classification_consistent,
            {
                "corpus_binding": classification_evidence.get("corpus_binding"),
                "adjudication_provenance_bound": classification_evidence.get(
                    "adjudication_provenance_bound"
                ),
                "contains_raw_records": classification_evidence.get(
                    "contains_raw_records"
                ),
                "model_revisions_frozen": classification_evidence.get(
                    "model_revisions_frozen"
                ),
                "dataset_records": (classification.get("dataset") or {}).get(
                    "records"
                ),
                "arm_records": arm.get("records"),
                "labels": sorted(per_label_support),
            },
        ),
        _gate("zero_authoritative_dangerous_misses", (
            int(arm.get("authoritative_dangerous_misses") or 0) == 0
            and int(arm.get("important_support") or 0) > 0
        ), {
            "dangerous_misses": arm.get("authoritative_dangerous_misses"),
            "important_support": arm.get("important_support"),
        }),
        _gate("classification_oss_rc_a_profile", a_profile_passed, {
            "profile": "oss-rc-a-v1",
            "thresholds": a_profile,
            "observed": {
                "records": arm.get("records"),
                "per_label_support": per_label_support,
                "important_support": arm.get("important_support"),
                "authoritative_suppressions": arm.get(
                    "authoritative_suppressions"
                ),
                "coverage": arm.get("coverage"),
                "balanced_accuracy": arm.get("balanced_accuracy"),
                "macro_f1": arm.get("macro_f1"),
                "authoritative_suppression_precision": suppression_precision,
                "expected_calibration_error": calibration.get(
                    "expected_calibration_error"
                ),
                "calibration_coverage": calibration.get("coverage"),
            },
        }),
        _gate(
            "calibration_measured",
            (arm.get("calibration") or {}).get("status") == "measured",
            (arm.get("calibration") or {}).get("samples", 0),
        ),
        _gate("runtime_reproducible", (
            runtime_environment.get("cpu_allocation_verified") is True
            and runtime_environment.get("git_revision") == run.get("commit")
            and runtime_config.get("run_id") == manifest.get("runtime_run_id")
            and runtime_in_window
            and bool(cells)
            and runtime_errors == 0
            and oracle_failures == 0
        ), {
            "cpu_allocation_verified": runtime_environment.get("cpu_allocation_verified"),
            "run_id_matches": (
                runtime_config.get("run_id") == manifest.get("runtime_run_id")
            ),
            "generated_in_window": runtime_in_window,
            "cells": len(cells), "errors": runtime_errors,
            "oracle_failures": oracle_failures,
        }),
        _gate("runtime_oss_rc_a_profile", runtime_profile_passed, {
            "profile": "oss-rc-runtime-v1",
            "thresholds": runtime_profile,
            "observed": {
                "iterations": runtime_config.get("iterations"),
                "samples": runtime_config.get("samples"),
                "warmup": runtime_config.get("warmup"),
                "mixed_iterations": runtime_config.get("mixed_iterations"),
                "mixed_http_samples": runtime_config.get("mixed_http_samples"),
                "parallel_concurrency": parallel_concurrency,
                "nano_per_core": nano_1.get(
                    "throughput_signals_per_second_per_core"
                ),
                "nano_p95_ms": _p95(nano_1),
                "http_p95_ms": _p95(http_1),
                "mixed_http_p95_ms": _p95(mixed_http),
                "semantic_cache_hit_p95_ms": _p95(semantic_hit_1),
                "semantic_unique_miss_p95_ms": _p95(semantic_miss_1),
                "recall_1000_p95_ms": _p95(recall_1000),
                "recall_10000_p95_ms": _p95(recall_10000),
            },
        }),
        _gate("audit_delivery_healthy", audit_healthy and audit_brackets_window, {
            "window_bracketed": audit_brackets_window,
            "drop_deltas": drop_deltas,
            "receipt_pending": stats_after.get("ledger_receipt_pending"),
            "memory_pending": stats_after.get("ledger_memory_pending"),
        }),
        _gate("ledger_capacity_healthy", capacity_healthy, {
            "used_fraction": ledger.get("used_fraction"),
            "alert_threshold": ledger.get("alert_threshold"),
        }),
        _gate("ledger_outbox_healthy", outbox_healthy, {
            "status": outbox.get("status"), "pending": outbox.get("pending"),
            "inflight": outbox.get("inflight"),
            "failed": outbox.get("failed"),
            "oldest_pending_seconds": outbox.get("oldest_pending_seconds"),
            "policy": outbox.get("policy"),
            "required_policy": OUTBOX_POLICY,
            "archive_verified": outbox.get("archive_verified"),
            "undelivered_deleted": outbox.get("undelivered_deleted"),
            "last_relay_success_at": outbox.get("last_relay_success_at"),
            "recovery_drill_at": outbox.get("recovery_drill_at"),
            "timing_verified": outbox_times_healthy,
        }),
        _gate("clean_clone_verified", (
            verification.get("clean_clone_passed") is True
            and 0 < float(verification.get("clean_clone_minutes") or 0) <= 15
        ), verification.get("clean_clone_minutes")),
        _gate("tests_passed", (
            int(verification.get("tests_passed") or 0) > 0
            and int(verification.get("tests_failed") or 0) == 0
        ), {
            "passed": verification.get("tests_passed"),
            "failed": verification.get("tests_failed"),
            "skipped": verification.get("tests_skipped"),
        }),
        _gate("package_and_container_smoke", (
            verification.get("package_smoke") is True
            and verification.get("container_smoke") is True
        ), {
            "package": verification.get("package_smoke"),
            "container": verification.get("container_smoke"),
        }),
        _gate("supply_chain_evidence", (
            verification.get("sbom") is True
            and verification.get("provenance") is True
            and verification.get("ci_conclusion") == "success"
        ), {
            "sbom": verification.get("sbom"),
            "provenance": verification.get("provenance"),
            "ci_conclusion": verification.get("ci_conclusion"),
        }),
    ]
    failed = [gate["name"] for gate in gates if not gate["passed"]]
    return {
        "schema_version": "cascade.staging-evidence.v1alpha1",
        "status": "staging_success" if not failed else "incomplete",
        "run": {field: run.get(field) for field in REQUIRED_RUN_FIELDS},
        "classification": {
            "arm": classification_arm,
            "quality_profile": "oss-rc-a-v1",
            "model_revisions": model_revisions,
            "dataset_digest": (classification.get("dataset") or {}).get("digest"),
            "holdout_digest": (classification.get("dataset") or {}).get(
                "holdout_digest"
            ),
            "records": arm.get("records"),
            "coverage": arm.get("coverage"),
            "accuracy": arm.get("overall_accuracy"),
            "balanced_accuracy": arm.get("balanced_accuracy"),
            "macro_f1": arm.get("macro_f1"),
            "dangerous_misses": arm.get("authoritative_dangerous_misses"),
            "calibration": arm.get("calibration"),
        },
        "runtime": {
            "quality_profile": "oss-rc-runtime-v1",
            "cells": len(cells),
            "errors": runtime_errors,
            "cpu_allocation_verified": runtime_environment.get(
                "cpu_allocation_verified"
            ),
        },
        "audit": {
            "drop_deltas": drop_deltas,
            "receipt_pending": stats_after.get("ledger_receipt_pending"),
            "memory_pending": stats_after.get("ledger_memory_pending"),
            "ledger_used_fraction": ledger.get("used_fraction"),
            "outbox_pending": outbox.get("pending"),
        },
        "artifact_digests": {
            "manifest": _digest(manifest),
            "candidate": _digest(candidate),
            "classification": _digest(classification),
            "runtime": _digest(runtime),
            "stats_before": _digest(stats_before),
            "stats_after": _digest(stats_after),
        },
        "gates": gates,
        "failed_gates": failed,
        "contains_raw_records": False,
    }
