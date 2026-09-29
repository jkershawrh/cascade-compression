"""Build a sanitized, fail-closed staging-success evidence bundle."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable

from .evaluation import REQUIRED_RUN_FIELDS


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


def _digest(document: Dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _runtime_cells(runtime: dict) -> Iterable[dict]:
    for section in (runtime.get("results") or {}).values():
        if isinstance(section, list):
            yield from (item for item in section if isinstance(item, dict))
        elif isinstance(section, dict):
            yield section


def _gate(name: str, passed: bool, evidence: Any) -> dict:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def build_staging_evidence(
    manifest: Dict[str, Any],
    classification: Dict[str, Any],
    runtime: Dict[str, Any],
    stats_before: Dict[str, Any],
    stats_after: Dict[str, Any],
) -> dict:
    """Bind sanitized artifacts and apply explicit release-proof gates."""
    if manifest.get("schema_version") != "cascade.staging-manifest.v1alpha1":
        raise ValueError("unsupported staging manifest version")
    if classification.get("schema_version") != "cascade.classification-evaluation.v1alpha1":
        raise ValueError("classification artifact has an unsupported version")
    if runtime.get("schema_version") != 1:
        raise ValueError("runtime artifact has an unsupported version")

    run = manifest.get("run") or {}
    missing_run = [field for field in REQUIRED_RUN_FIELDS if not run.get(field)]
    try:
        positive_window = (
            not missing_run
            and _parse_time(run["window_end"]) > _parse_time(run["window_start"])
        )
    except (TypeError, ValueError):
        positive_window = False

    classification_arm = str(manifest.get("classification_arm") or "hybrid")
    arm = (classification.get("arms") or {}).get(classification_arm)
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

    calibration = arm.get("calibration") or {}
    suppression_precision = arm.get("authoritative_suppression_precision")
    a_profile = OSS_RC_A_PROFILE
    per_label = arm.get("per_label") or {}
    per_label_support = {
        label: int(metrics.get("support") or 0)
        for label, metrics in per_label.items()
    }
    a_profile_passed = (
        int(arm.get("records") or 0) >= a_profile["minimum_records"]
        and bool(per_label_support)
        and all(
            support >= a_profile["minimum_per_label_support"]
            for support in per_label_support.values()
        )
        and int(arm.get("important_support") or 0)
        >= a_profile["minimum_important_support"]
        and int(arm.get("authoritative_suppressions") or 0)
        >= a_profile["minimum_authoritative_suppressions"]
        and float(arm.get("coverage") or 0) >= a_profile["minimum_coverage"]
        and float(arm.get("balanced_accuracy") or 0)
        >= a_profile["minimum_balanced_accuracy"]
        and float(arm.get("macro_f1") or 0) >= a_profile["minimum_macro_f1"]
        and suppression_precision is not None
        and float(suppression_precision)
        >= a_profile["minimum_authoritative_suppression_precision"]
        and calibration.get("status") == "measured"
        and float(calibration.get("coverage") or 0)
        >= a_profile["minimum_calibration_coverage"]
        and float(calibration.get("expected_calibration_error", 2))
        <= a_profile["maximum_expected_calibration_error"]
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

    verification = manifest.get("verification") or {}
    audit_window = manifest.get("audit_window") or {}
    try:
        run_start = _parse_time(run["window_start"])
        run_end = _parse_time(run["window_end"])
        runtime_in_window = (
            run_start <= _parse_time(runtime.get("generated_at", "")) <= run_end
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
    outbox_healthy = (
        outbox.get("status") == "healthy"
        and int(outbox.get("pending") or 0) == 0
        and bool(outbox.get("policy"))
    )

    gates = [
        _gate("immutable_run_identity", not missing_run and positive_window, {
            "missing_fields": missing_run, "positive_window": positive_window,
        }),
        _gate("classification_same_run", run_matches, {
            "arm": classification_arm,
            "dataset_digest": (classification.get("dataset") or {}).get("digest"),
        }),
        _gate("classification_models_frozen", models_frozen, {
            "declared": model_revisions,
            "evaluated": classification_models,
        }),
        _gate(
            "classification_decision_grade",
            (classification.get("evidence") or {}).get("status") == "decision_grade",
            (classification.get("evidence") or {}).get("status"),
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
            "policy_declared": bool(outbox.get("policy")),
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
            "records": arm.get("records"),
            "coverage": arm.get("coverage"),
            "accuracy": arm.get("overall_accuracy"),
            "balanced_accuracy": arm.get("balanced_accuracy"),
            "macro_f1": arm.get("macro_f1"),
            "dangerous_misses": arm.get("authoritative_dangerous_misses"),
            "calibration": arm.get("calibration"),
        },
        "runtime": {
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
            "classification": _digest(classification),
            "runtime": _digest(runtime),
            "stats_before": _digest(stats_before),
            "stats_after": _digest(stats_after),
        },
        "gates": gates,
        "failed_gates": failed,
        "contains_raw_records": False,
    }
