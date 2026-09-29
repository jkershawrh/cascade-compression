"""Build a sanitized, fail-closed staging-success evidence bundle."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from .classifier import CASCADE_LABELS
from .durable_queue import DURABLE_QUEUE_OVERFLOW_POLICY
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
AUDIT_SPOOL_ALERT_FRACTION = 0.80


def _digest(document: Dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _runtime_cells(runtime: dict) -> Iterable[dict]:
    results = runtime.get("results")
    if not isinstance(results, dict):
        return
    for section in results.values():
        if isinstance(section, list):
            yield from (item for item in section if isinstance(item, dict))
        elif isinstance(section, dict):
            yield section


def _gate(name: str, passed: bool, evidence: Any) -> dict:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def _matching_cell(cells: Iterable[dict], **criteria: Any) -> dict:
    return next((
        cell for cell in cells
        if all(
            type(cell.get(key)) is type(value) and cell.get(key) == value
            for key, value in criteria.items()
        )
    ), {})


def _p95(cell: dict) -> Optional[float]:
    latency = cell.get("latency_ms")
    if not isinstance(latency, dict):
        return None
    value = latency.get("p95")
    if not _is_number(value, minimum=0):
        return None
    return float(value)


def _within_p95(cell: dict, maximum: float) -> bool:
    value = _p95(cell)
    return value is not None and value <= maximum


def _samples_at_least(cell: dict, minimum: int) -> bool:
    return _is_int(cell.get("samples"), minimum=minimum)


def _is_int(value: Any, minimum: Optional[int] = None) -> bool:
    if type(value) is not int:
        return False
    return minimum is None or value >= minimum


def _int_or(value: Any, default: int = 0) -> int:
    return value if type(value) is int else default


def _is_number(
    value: Any,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> bool:
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


def _bounded_number(value: Any, minimum: float, maximum: float) -> bool:
    return _is_number(value, minimum=minimum, maximum=maximum)


def _audit_spool_capacity(snapshot: dict, prefix: str) -> tuple[bool, dict]:
    """Validate queue bounds and filesystem headroom from one /stats snapshot."""
    integer_fields = (
        "pending", "pending_max", "payload_bytes", "bytes_max", "spool_bytes",
        "filesystem_total_bytes", "filesystem_free_bytes",
    )
    numeric_fields = (
        "utilization", "byte_utilization", "filesystem_used_fraction",
    )
    if not all(
        _is_int(snapshot.get(f"{prefix}_{field}")) for field in integer_fields
    ) or not all(
        _is_number(snapshot.get(f"{prefix}_{field}")) for field in numeric_fields
    ):
        return False, {"measurements_present": False}

    pending = snapshot[f"{prefix}_pending"]
    pending_max = snapshot[f"{prefix}_pending_max"]
    payload_bytes = snapshot[f"{prefix}_payload_bytes"]
    bytes_max = snapshot[f"{prefix}_bytes_max"]
    reported_rows = snapshot[f"{prefix}_utilization"]
    reported_bytes = snapshot[f"{prefix}_byte_utilization"]
    spool_bytes = snapshot[f"{prefix}_spool_bytes"]
    filesystem_total = snapshot[f"{prefix}_filesystem_total_bytes"]
    filesystem_free = snapshot[f"{prefix}_filesystem_free_bytes"]
    reported_filesystem_used = snapshot[f"{prefix}_filesystem_used_fraction"]

    computed_rows = pending / pending_max if pending_max > 0 else math.inf
    computed_bytes = payload_bytes / bytes_max if bytes_max > 0 else math.inf
    computed_filesystem_used = (
        (filesystem_total - filesystem_free) / filesystem_total
        if filesystem_total > 0 else math.inf
    )
    required_free = max(0, bytes_max - payload_bytes)
    measurements_valid = (
        pending >= 0
        and pending_max > 0
        and payload_bytes >= 0
        and bytes_max > 0
        and pending <= pending_max
        and payload_bytes <= bytes_max
        and math.isfinite(reported_rows)
        and math.isfinite(reported_bytes)
        and abs(reported_rows - computed_rows) <= 0.0001
        and abs(reported_bytes - computed_bytes) <= 0.0001
        and spool_bytes >= 0
        and filesystem_total > 0
        and 0 <= filesystem_free <= filesystem_total
        and math.isfinite(reported_filesystem_used)
        and abs(reported_filesystem_used - computed_filesystem_used) <= 0.0001
    )
    healthy = (
        measurements_valid
        and computed_rows < AUDIT_SPOOL_ALERT_FRACTION
        and computed_bytes < AUDIT_SPOOL_ALERT_FRACTION
        and computed_filesystem_used < AUDIT_SPOOL_ALERT_FRACTION
        and filesystem_free >= required_free
    )
    return healthy, {
        "measurements_present": True,
        "pending": pending,
        "pending_max": pending_max,
        "row_utilization": round(computed_rows, 4),
        "payload_bytes": payload_bytes,
        "bytes_max": bytes_max,
        "byte_utilization": round(computed_bytes, 4),
        "spool_bytes": spool_bytes,
        "filesystem_total_bytes": filesystem_total,
        "filesystem_free_bytes": filesystem_free,
        "filesystem_used_fraction": round(computed_filesystem_used, 4),
        "required_free_bytes": required_free,
        "configured_queue_budget_fits": filesystem_free >= required_free,
    }


def build_staging_evidence(
    manifest: Dict[str, Any],
    classification: Dict[str, Any],
    runtime: Dict[str, Any],
    stats_before: Dict[str, Any],
    stats_after: Dict[str, Any],
) -> dict:
    """Bind sanitized artifacts and apply explicit release-proof gates."""
    if not all(
        isinstance(document, dict)
        for document in (manifest, classification, runtime, stats_before, stats_after)
    ):
        raise ValueError("staging evidence inputs must be JSON objects")
    if manifest.get("schema_version") != "cascade.staging-manifest.v1alpha5":
        raise ValueError("unsupported staging manifest version")
    if classification.get("schema_version") != "cascade.classification-evaluation.v1alpha5":
        raise ValueError("classification artifact has an unsupported version")
    if runtime.get("schema_version") != 1:
        raise ValueError("runtime artifact has an unsupported version")

    run_value = manifest.get("run")
    run = run_value if isinstance(run_value, dict) else {}
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
    classification_arms_value = classification.get("arms")
    classification_arms = (
        classification_arms_value
        if isinstance(classification_arms_value, dict) else {}
    )
    arm = classification_arms.get(classification_arm)
    if arm is None:
        raise ValueError(f"classification arm not found: {classification_arm}")
    if not isinstance(arm, dict):
        arm = {}
    classification_run_value = classification.get("run")
    classification_run = (
        classification_run_value
        if isinstance(classification_run_value, dict) else {}
    )
    run_matches = all(
        classification_run.get(field) == run.get(field)
        for field in REQUIRED_RUN_FIELDS
    )
    model_revisions_value = manifest.get("model_revisions")
    model_revisions = (
        model_revisions_value if isinstance(model_revisions_value, dict) else {}
    )
    classification_models_value = classification.get("model_revisions")
    classification_models = (
        classification_models_value
        if isinstance(classification_models_value, dict) else {}
    )
    models_frozen = bool(model_revisions) and classification_models == model_revisions
    candidate_value = manifest.get("candidate")
    candidate = candidate_value if isinstance(candidate_value, dict) else {}
    package_artifacts = candidate.get("package_artifacts")
    multi_arch = candidate.get("multi_arch")
    candidate_bound = (
        isinstance(candidate_value, dict)
        and candidate.get("schema_version")
        == "cascade.staging-candidate.v1alpha3"
        and isinstance(candidate.get("repository"), str)
        and bool(candidate.get("repository"))
        and isinstance(candidate.get("image"), str)
        and bool(candidate.get("image"))
        and candidate.get("commit") == run.get("commit")
        and candidate.get("image_digest") == run.get("image_digest")
        and bool(re.fullmatch(r"[1-9][0-9]*", candidate.get("workflow_run_id", "")))
        and _is_int(candidate.get("workflow_run_attempt"), minimum=1)
        and candidate.get("container_sbom") is True
        and candidate.get("container_provenance") is True
        and candidate.get("package_sbom") is True
        and candidate.get("package_provenance") is True
        and candidate.get("manifest_provenance") is True
        and isinstance(package_artifacts, list)
        and len(package_artifacts) >= 3
        and all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and bool(item.get("name"))
            and re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(item.get("sha256") or ""),
            )
            and _is_int(item.get("bytes"), minimum=1)
            for item in package_artifacts
        )
        and any(
            str(item.get("name") or "").endswith(".whl")
            for item in package_artifacts
        )
        and any(
            str(item.get("name") or "").endswith(".tar.gz")
            for item in package_artifacts
        )
        and any(
            str(item.get("name") or "").endswith(".spdx.json")
            for item in package_artifacts
        )
        and isinstance(multi_arch, list)
        and all(isinstance(platform, str) for platform in multi_arch)
        and {"linux/amd64", "linux/arm64"}
        <= set(multi_arch)
    )
    try:
        candidate_precedes_run = (
            _parse_time(candidate.get("generated_at", ""))
            <= _parse_time(run.get("window_start", ""))
        )
    except (TypeError, ValueError):
        candidate_precedes_run = False
    pairwise_value = classification.get("pairwise_agreement")
    pairwise = pairwise_value if isinstance(pairwise_value, dict) else {}
    required_pairs = {
        "generative__hybrid", "generative__semantic", "hybrid__semantic",
    }
    comparison_records = _int_or(arm.get("records"), -1)
    minimum_pairwise_compared = math.ceil(
        comparison_records * OSS_RC_A_PROFILE["minimum_coverage"]
    )
    comparison_complete = (
        classification_arm == "hybrid"
        and REQUIRED_CLASSIFICATION_ARMS <= set(classification_arms)
        and REQUIRED_CLASSIFICATION_ARMS <= set(classification_models)
        and required_pairs <= set(pairwise)
        and all(
            isinstance(classification_arms[name], dict)
            and _is_int(classification_arms[name].get("records"), minimum=1)
            and classification_arms[name].get("records") == arm.get("records")
            and _is_number(
                classification_arms[name].get("coverage"),
                minimum=OSS_RC_A_PROFILE["minimum_coverage"], maximum=1.0,
            )
            for name in REQUIRED_CLASSIFICATION_ARMS
        )
        and all(
            isinstance(pairwise[name], dict)
            and _is_int(
                pairwise[name].get("compared"), minimum=minimum_pairwise_compared,
            )
            and pairwise[name].get("is_accuracy") is False
            for name in required_pairs
        )
    )

    calibration_value = arm.get("calibration")
    calibration = calibration_value if isinstance(calibration_value, dict) else {}
    suppression_precision = arm.get("authoritative_suppression_precision")
    a_profile = OSS_RC_A_PROFILE
    per_label_value = arm.get("per_label")
    per_label = per_label_value if isinstance(per_label_value, dict) else {}
    per_label_support = {
        label: _int_or(metrics.get("support"), -1)
        for label, metrics in per_label.items() if isinstance(metrics, dict)
    }
    classification_evidence_value = classification.get("evidence")
    classification_evidence = (
        classification_evidence_value
        if isinstance(classification_evidence_value, dict) else {}
    )
    dataset_value = classification.get("dataset")
    dataset = dataset_value if isinstance(dataset_value, dict) else {}
    evaluation_design_value = dataset.get("evaluation_design")
    evaluation_design = (
        evaluation_design_value if isinstance(evaluation_design_value, dict) else {}
    )
    challenge_design_valid = (
        evaluation_design.get("purpose") == "label_coverage_challenge"
        and evaluation_design.get("prevalence_claim_permitted") is False
        and evaluation_design.get("candidate_targets_are_ground_truth") is False
        and evaluation_design.get("known_pattern_authority_prequalified") is True
    )
    classification_consistent = (
        classification_evidence.get("corpus_binding") is True
        and classification_evidence.get("adjudication_provenance_bound") is True
        and classification_evidence.get("review_window_valid") is True
        and classification_evidence.get("review_precedes_run") is True
        and classification_evidence.get("contains_raw_records") is False
        and classification_evidence.get("model_revisions_frozen") is True
        and _is_int(dataset.get("records"), minimum=1)
        and dataset.get("records") == arm.get("records")
        and set(per_label_support) == set(CASCADE_LABELS)
    )
    a_profile_passed = (
        _is_int(arm.get("records"), minimum=a_profile["minimum_records"])
        and set(per_label_support) == set(CASCADE_LABELS)
        and all(
            support >= a_profile["minimum_per_label_support"]
            for support in per_label_support.values()
        )
        and _is_int(
            arm.get("important_support"),
            minimum=a_profile["minimum_important_support"],
        )
        and _is_int(
            arm.get("authoritative_suppressions"),
            minimum=a_profile["minimum_authoritative_suppressions"],
        )
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

    runtime_environment_value = runtime.get("environment")
    runtime_environment = (
        runtime_environment_value if isinstance(runtime_environment_value, dict) else {}
    )
    runtime_config_value = runtime.get("config")
    runtime_config = (
        runtime_config_value if isinstance(runtime_config_value, dict) else {}
    )
    cells = list(_runtime_cells(runtime))
    runtime_errors_well_formed = all(
        _is_int(cell.get("errors"), minimum=0) for cell in cells
    )
    runtime_errors = sum(_int_or(cell.get("errors"), -1) for cell in cells)
    oracle_failures = sum(
        int(value is False)
        for cell in cells
        for key, value in cell.items()
        if key.endswith("_oracle_passed")
    )
    results_value = runtime.get("results")
    results = results_value if isinstance(results_value, dict) else {}
    nano_cells = [
        cell for cell in results.get("nano", []) if isinstance(cell, dict)
    ] if isinstance(results.get("nano"), list) else []
    http_cells = [
        cell for cell in results.get("http", []) if isinstance(cell, dict)
    ] if isinstance(results.get("http"), list) else []
    semantic_cells = [
        cell for cell in results.get("semantic", []) if isinstance(cell, dict)
    ] if isinstance(results.get("semantic"), list) else []
    recall_cells = [
        cell for cell in results.get("recall", []) if isinstance(cell, dict)
    ] if isinstance(results.get("recall"), list) else []
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
    mixed_nano_value = results.get("mixed_nano")
    mixed_nano = mixed_nano_value if isinstance(mixed_nano_value, dict) else {}
    mixed_http_value = results.get("mixed_http")
    mixed_http = mixed_http_value if isinstance(mixed_http_value, dict) else {}
    http_parallel = max(
        [_int_or(cell.get("concurrency"), -1) for cell in http_cells], default=0,
    )
    semantic_hit_parallel = max([
        _int_or(cell.get("concurrency"), -1) for cell in semantic_cells
        if cell.get("workload") == "normalized_cache_hit"
    ], default=0)
    semantic_miss_parallel = max([
        _int_or(cell.get("concurrency"), -1) for cell in semantic_cells
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
    required_oracles_passed = (
        mixed_nano.get("survivor_oracle_passed") is True
        and mixed_http.get("survivor_oracle_passed") is True
        and recall_1000.get("recall_oracle_passed") is True
        and recall_10000.get("recall_oracle_passed") is True
    )
    runtime_profile = OSS_RC_RUNTIME_PROFILE
    runtime_profile_passed = (
        _is_int(
            runtime_config.get("iterations"),
            minimum=runtime_profile["minimum_iterations"],
        )
        and _is_int(
            runtime_config.get("samples"),
            minimum=runtime_profile["minimum_samples"],
        )
        and _is_int(
            runtime_config.get("warmup"), minimum=runtime_profile["minimum_warmup"],
        )
        and _is_int(
            runtime_config.get("mixed_iterations"),
            minimum=runtime_profile["minimum_mixed_iterations"],
        )
        and _is_int(
            runtime_config.get("mixed_http_samples"),
            minimum=runtime_profile["minimum_mixed_http_samples"],
        )
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
        and _is_number(
            nano_1.get("throughput_signals_per_second_per_core"),
            minimum=runtime_profile["minimum_nano_signals_per_second_per_core"],
        )
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

    verification_value = manifest.get("verification")
    verification = (
        verification_value if isinstance(verification_value, dict) else {}
    )
    audit_window_value = manifest.get("audit_window")
    audit_window = (
        audit_window_value if isinstance(audit_window_value, dict) else {}
    )
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
    ledger_value = manifest.get("ledger")
    ledger = ledger_value if isinstance(ledger_value, dict) else {}
    outbox_value = ledger.get("outbox")
    outbox = outbox_value if isinstance(outbox_value, dict) else {}
    drop_fields = (
        "ledger_writes_dropped",
        "ledger_memory_events_dropped",
        "ledger_receipt_rejected_total",
        "ledger_memory_rejected_total",
    )
    durable_loss_counters_present = all(
        _is_int(snapshot.get(field), minimum=0)
        for field in drop_fields
        for snapshot in (stats_before, stats_after)
    )
    drop_deltas = {
        field: (
            _int_or(stats_after.get(field), -1)
            - _int_or(stats_before.get(field), -1)
        )
        for field in drop_fields
    }
    audit_healthy = (
        stats_before.get("ledger_receipt_queue_durability") == "sqlite"
        and stats_before.get("ledger_memory_queue_durability") == "sqlite"
        and stats_after.get("ledger_receipt_queue_durability") == "sqlite"
        and stats_after.get("ledger_memory_queue_durability") == "sqlite"
        and stats_before.get("ledger_receipt_overflow_policy")
        == DURABLE_QUEUE_OVERFLOW_POLICY
        and stats_before.get("ledger_memory_overflow_policy")
        == DURABLE_QUEUE_OVERFLOW_POLICY
        and stats_after.get("ledger_receipt_overflow_policy")
        == DURABLE_QUEUE_OVERFLOW_POLICY
        and stats_after.get("ledger_memory_overflow_policy")
        == DURABLE_QUEUE_OVERFLOW_POLICY
        and _is_int(stats_after.get("ledger_receipt_pending"), minimum=0)
        and stats_after.get("ledger_receipt_pending") == 0
        and _is_int(stats_after.get("ledger_memory_pending"), minimum=0)
        and stats_after.get("ledger_memory_pending") == 0
        and _is_int(
            stats_after.get("ledger_receipt_consecutive_failures"), minimum=0,
        )
        and stats_after.get("ledger_receipt_consecutive_failures") == 0
        and _is_int(
            stats_after.get("ledger_memory_consecutive_failures"), minimum=0,
        )
        and stats_after.get("ledger_memory_consecutive_failures") == 0
        and durable_loss_counters_present
        and all(delta == 0 for delta in drop_deltas.values())
    )
    spool_capacity = {}
    spool_capacity_healthy = True
    for snapshot_name, snapshot in (
        ("before", stats_before), ("after", stats_after),
    ):
        for queue_name, prefix in (
            ("receipt", "ledger_receipt"),
            ("memory", "ledger_memory"),
        ):
            healthy, evidence = _audit_spool_capacity(snapshot, prefix)
            spool_capacity[f"{queue_name}_{snapshot_name}"] = evidence
            spool_capacity_healthy = spool_capacity_healthy and healthy
        shared_filesystem = snapshot.get("ledger_spools_share_filesystem")
        spool_capacity_healthy = (
            spool_capacity_healthy and type(shared_filesystem) is bool
        )
        if shared_filesystem is True:
            receipt_capacity = spool_capacity[f"receipt_{snapshot_name}"]
            memory_capacity = spool_capacity[f"memory_{snapshot_name}"]
            shared_required = int(receipt_capacity.get("required_free_bytes") or 0)
            shared_required += int(memory_capacity.get("required_free_bytes") or 0)
            shared_free = min(
                int(receipt_capacity.get("filesystem_free_bytes") or 0),
                int(memory_capacity.get("filesystem_free_bytes") or 0),
            )
            shared_healthy = shared_free >= shared_required
            spool_capacity[f"shared_filesystem_{snapshot_name}"] = {
                "queues_share_filesystem": True,
                "free_bytes": shared_free,
                "combined_required_free_bytes": shared_required,
                "configured_queue_budgets_fit": shared_healthy,
            }
            spool_capacity_healthy = spool_capacity_healthy and shared_healthy
    capacity_healthy = (
        ledger.get("capacity_measured") is True
        and _is_number(ledger.get("used_fraction"), minimum=0, maximum=1)
        and _is_number(
            ledger.get("alert_threshold"), minimum=0, maximum=1,
        )
        and ledger.get("alert_threshold") > 0
        and ledger.get("used_fraction") < ledger.get("alert_threshold")
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
        and _is_int(outbox.get("pending"), minimum=0)
        and outbox.get("pending") == 0
        and _is_int(outbox.get("inflight"), minimum=0)
        and outbox.get("inflight") == 0
        and _is_int(outbox.get("failed"), minimum=0)
        and outbox.get("failed") == 0
        and _is_number(outbox.get("oldest_pending_seconds"), minimum=0)
        and outbox.get("oldest_pending_seconds") == 0
        and outbox.get("archive_verified") is True
        and _is_int(outbox.get("undelivered_deleted"), minimum=0)
        and outbox.get("undelivered_deleted") == 0
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
            "container_sbom": candidate.get("container_sbom"),
            "container_provenance": candidate.get("container_provenance"),
            "package_sbom": candidate.get("package_sbom"),
            "package_provenance": candidate.get("package_provenance"),
            "manifest_provenance": candidate.get("manifest_provenance"),
            "package_artifacts": len(candidate.get("package_artifacts") or []),
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
        _gate("classification_challenge_design", challenge_design_valid, {
            "purpose": evaluation_design.get("purpose"),
            "prevalence_claim_permitted": evaluation_design.get(
                "prevalence_claim_permitted"
            ),
            "candidate_targets_are_ground_truth": evaluation_design.get(
                "candidate_targets_are_ground_truth"
            ),
            "known_pattern_authority_prequalified": evaluation_design.get(
                "known_pattern_authority_prequalified"
            ),
        }),
        _gate(
            "classification_artifact_consistent",
            classification_consistent,
            {
                "corpus_binding": classification_evidence.get("corpus_binding"),
                "adjudication_provenance_bound": classification_evidence.get(
                    "adjudication_provenance_bound"
                ),
                "review_window_valid": classification_evidence.get(
                    "review_window_valid"
                ),
                "review_precedes_run": classification_evidence.get(
                    "review_precedes_run"
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
            _is_int(arm.get("authoritative_dangerous_misses"), minimum=0)
            and arm.get("authoritative_dangerous_misses") == 0
            and _is_int(arm.get("important_support"), minimum=1)
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
            and runtime_errors_well_formed
            and runtime_errors == 0
            and oracle_failures == 0
            and required_oracles_passed
        ), {
            "cpu_allocation_verified": runtime_environment.get("cpu_allocation_verified"),
            "run_id_matches": (
                runtime_config.get("run_id") == manifest.get("runtime_run_id")
            ),
            "generated_in_window": runtime_in_window,
            "cells": len(cells), "errors": runtime_errors,
            "errors_well_formed": runtime_errors_well_formed,
            "oracle_failures": oracle_failures,
            "required_oracles_passed": required_oracles_passed,
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
            "durable_loss_counters_present": durable_loss_counters_present,
            "required_overflow_policy": DURABLE_QUEUE_OVERFLOW_POLICY,
            "receipt_overflow_policy_before": stats_before.get(
                "ledger_receipt_overflow_policy"),
            "receipt_overflow_policy_after": stats_after.get(
                "ledger_receipt_overflow_policy"),
            "memory_overflow_policy_before": stats_before.get(
                "ledger_memory_overflow_policy"),
            "memory_overflow_policy_after": stats_after.get(
                "ledger_memory_overflow_policy"),
            "receipt_pending": stats_after.get("ledger_receipt_pending"),
            "memory_pending": stats_after.get("ledger_memory_pending"),
        }),
        _gate("cascade_spool_capacity_healthy", spool_capacity_healthy, {
            "alert_fraction": AUDIT_SPOOL_ALERT_FRACTION,
            "snapshots": spool_capacity,
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
            and _is_number(
                verification.get("clean_clone_minutes"), minimum=0, maximum=15,
            )
            and verification.get("clean_clone_minutes") > 0
        ), verification.get("clean_clone_minutes")),
        _gate("tests_passed", (
            _is_int(verification.get("tests_passed"), minimum=1)
            and _is_int(verification.get("tests_failed"), minimum=0)
            and verification.get("tests_failed") == 0
            and _is_int(verification.get("tests_skipped"), minimum=0)
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
        "schema_version": "cascade.staging-evidence.v1alpha3",
        "status": "staging_success" if not failed else "incomplete",
        "run": {field: run.get(field) for field in REQUIRED_RUN_FIELDS},
        "candidate": {
            "repository": candidate.get("repository"),
            "image": candidate.get("image"),
            "image_digest": candidate.get("image_digest"),
            "workflow_run_id": candidate.get("workflow_run_id"),
            "workflow_run_attempt": candidate.get("workflow_run_attempt"),
            "manifest_digest": _digest(candidate),
        },
        "classification": {
            "arm": classification_arm,
            "quality_profile": "oss-rc-a-v1",
            "evaluation_design": evaluation_design,
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
