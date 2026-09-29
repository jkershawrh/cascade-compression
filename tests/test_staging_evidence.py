import json
from pathlib import Path

import jsonschema

from cascade_compression.evaluation import evaluate_classifiers
from cascade_compression.holdout import canonical_digest
from cascade_compression.staging_evidence import build_staging_evidence


ROOT = Path(__file__).resolve().parent.parent
COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64


def inputs():
    run = {
        "commit": COMMIT,
        "image_digest": IMAGE,
        "config_digest": CONFIG,
        "taxonomy_revision": "taxonomy-v1",
        "window_start": "2026-09-01T00:00:00Z",
        "window_end": "2026-09-01T01:00:00Z",
    }
    records = [
        {
            "record_id": f"r{index}",
            "signal_sha256": "sha256:" + f"{index:064x}",
            "expected": label,
            "predictions": {
                "generative": {
                    "label": label, "confidence": 1.0,
                    "authoritative": True,
                },
                "semantic": {
                    "label": label, "confidence": 1.0,
                    "authoritative": False,
                },
                "hybrid": {
                    "label": label, "confidence": 1.0,
                    "authoritative": True,
                },
            },
        }
        for index, label in enumerate(
            ["routine_noise", "known_pattern", "needs_attention", "real_incident"]
            * 50
        )
    ]
    corpus_digest = canonical_digest(sorted(({
        "case_id": row["record_id"],
        "signal_sha256": row["signal_sha256"],
    } for row in records), key=lambda item: item["case_id"]))
    classification = evaluate_classifiers({
        "schema_version": "cascade.classification-input.v1alpha1",
        "dataset": {
            "name": "held-out", "revision": "v1",
            "holdout_digest": corpus_digest,
            "adjudication": {
                "status": "complete", "method": "double-review",
                "independent": True, "reviewers": 2,
                "corpus_digest": corpus_digest,
                "review_evidence_digest": "sha256:" + "d" * 64,
                "summary_digest": "sha256:" + "e" * 64,
            },
        },
        "run": {**run, "model_revisions": {
            "generative": "generative-v1",
            "semantic": "semantic-v1",
            "hybrid": "hybrid-v1",
        }},
        "records": records,
    })
    manifest = {
        "schema_version": "cascade.staging-manifest.v1alpha3",
        "run": run,
        "candidate": {
            "schema_version": "cascade.staging-candidate.v1alpha1",
            "repository": "example/cascade-compression",
            "commit": COMMIT,
            "image": "ghcr.io/example/cascade-compression",
            "image_digest": IMAGE,
            "workflow_run_id": "123456",
            "workflow_run_attempt": 1,
            "generated_at": "2026-08-31T23:30:00Z",
            "multi_arch": ["linux/amd64", "linux/arm64"],
            "sbom": True,
            "provenance": True,
        },
        "classification_arm": "hybrid",
        "model_revisions": {
            "generative": "generative-v1",
            "semantic": "semantic-v1",
            "hybrid": "hybrid-v1",
        },
        "runtime_run_id": "runtime-run-1",
        "audit_window": {
            "stats_before_at": "2026-08-31T23:59:00Z",
            "stats_after_at": "2026-09-01T01:01:00Z",
        },
        "ledger": {
            "capacity_measured": True,
            "used_fraction": 0.4,
            "alert_threshold": 0.8,
            "outbox": {
                "status": "healthy",
                "pending": 0,
                "inflight": 0,
                "failed": 0,
                "oldest_pending_seconds": 0,
                "policy": "immutable-relay-and-archive-v1",
                "archive_verified": True,
                "undelivered_deleted": 0,
                "last_relay_success_at": "2026-09-01T00:50:00Z",
                "recovery_drill_at": "2026-08-15T00:00:00Z",
            },
        },
        "verification": {
            "clean_clone_passed": True,
            "clean_clone_minutes": 4.2,
            "tests_passed": 761,
            "tests_failed": 0,
            "tests_skipped": 1,
            "package_smoke": True,
            "container_smoke": True,
            "sbom": True,
            "provenance": True,
            "ci_conclusion": "success",
        },
    }
    runtime = {
        "schema_version": 1,
        "generated_at": "2026-09-01T00:30:00Z",
        "completed_at": "2026-09-01T00:45:00Z",
        "environment": {
            "cpu_allocation_verified": True,
            "git_revision": COMMIT,
        },
        "config": {
            "run_id": "runtime-run-1", "iterations": 500, "samples": 500,
            "warmup": 100, "mixed_iterations": 500,
            "mixed_http_samples": 100,
        },
        "results": {
            "nano": [{
                "errors": 0, "samples": 500, "batch_size": 1,
                "throughput_signals_per_second_per_core": 5000.0,
                "latency_ms": {"p95": 1.0},
            }],
            "http": [
                {"errors": 0, "samples": 500, "concurrency": 1,
                 "latency_ms": {"p95": 20.0}},
                {"errors": 0, "samples": 500, "concurrency": 8,
                 "latency_ms": {"p95": 40.0}},
            ],
            "semantic": [
                {"errors": 0, "samples": 500,
                 "workload": "normalized_cache_hit", "concurrency": 1,
                 "latency_ms": {"p95": 20.0}},
                {"errors": 0, "samples": 500,
                 "workload": "normalized_cache_hit", "concurrency": 8,
                 "latency_ms": {"p95": 50.0}},
                {"errors": 0, "samples": 500,
                 "workload": "unique_cache_miss", "concurrency": 1,
                 "latency_ms": {"p95": 500.0}},
                {"errors": 0, "samples": 500,
                 "workload": "unique_cache_miss", "concurrency": 8,
                 "latency_ms": {"p95": 900.0}},
            ],
            "mixed_nano": {
                "errors": 0, "samples": 500, "survivor_oracle_passed": True,
                "latency_ms": {"p95": 20.0},
            },
            "mixed_http": {
                "errors": 0, "samples": 100, "survivor_oracle_passed": True,
                "latency_ms": {"p95": 200.0},
            },
            "recall": [
                {"errors": 0, "samples": 500, "memory_count": 1000,
                 "recall_oracle_passed": True, "latency_ms": {"p95": 10.0}},
                {"errors": 0, "samples": 500, "memory_count": 10000,
                 "recall_oracle_passed": True, "latency_ms": {"p95": 100.0}},
            ],
        },
    }
    stats = {
        "ledger_writes_dropped": 10,
        "ledger_memory_events_dropped": 20,
        "ledger_receipt_queue_durability": "sqlite",
        "ledger_memory_queue_durability": "sqlite",
        "ledger_receipt_pending": 0,
        "ledger_memory_pending": 0,
        "ledger_receipt_consecutive_failures": 0,
        "ledger_memory_consecutive_failures": 0,
    }
    return manifest, classification, runtime, dict(stats), dict(stats)


def test_complete_bundle_passes_every_gate_and_schema():
    manifest, classification, runtime, before, after = inputs()
    manifest_schema = json.loads(
        (ROOT / "contracts" / "schemas" / "staging-manifest.json").read_text()
    )
    jsonschema.Draft202012Validator(manifest_schema).validate(manifest)
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert report["status"] == "staging_success"
    assert report["failed_gates"] == []
    assert report["contains_raw_records"] is False
    schema = json.loads(
        (ROOT / "contracts" / "schemas" / "staging-evidence.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(report)


def test_new_audit_drop_fails_closed():
    manifest, classification, runtime, before, after = inputs()
    after["ledger_writes_dropped"] += 1
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert report["status"] == "incomplete"
    assert "audit_delivery_healthy" in report["failed_gates"]


def test_classifier_agreement_cannot_replace_decision_grade_truth():
    manifest, classification, runtime, before, after = inputs()
    classification["evidence"]["status"] = "mechanics_only"
    classification["pairwise_agreement"] = {
        "generative__semantic": {"agreement_rate": 1.0}
    }
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_decision_grade" in report["failed_gates"]


def test_missing_comparison_arm_cannot_pass_a_profile():
    manifest, classification, runtime, before, after = inputs()
    classification["arms"].pop("semantic")
    classification["model_revisions"].pop("semantic")
    manifest["model_revisions"].pop("semantic")
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_three_arm_comparison" in report["failed_gates"]


def test_small_perfect_corpus_cannot_pass_a_profile():
    manifest, classification, runtime, before, after = inputs()
    arm = classification["arms"]["hybrid"]
    arm["records"] = 20
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_oss_rc_a_profile" in report["failed_gates"]


def test_tampered_classification_binding_cannot_pass():
    manifest, classification, runtime, before, after = inputs()
    classification["evidence"]["corpus_binding"] = False
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_artifact_consistent" in report["failed_gates"]


def test_non_finite_quality_metric_cannot_pass():
    manifest, classification, runtime, before, after = inputs()
    classification["arms"]["hybrid"]["balanced_accuracy"] = float("inf")
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_oss_rc_a_profile" in report["failed_gates"]


def test_unfrozen_model_revision_blocks_success():
    manifest, classification, runtime, before, after = inputs()
    manifest["model_revisions"]["hybrid"] = "different-model"
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_models_frozen" in report["failed_gates"]


def test_sparse_confidence_values_cannot_pass_calibration_gate():
    manifest, classification, runtime, before, after = inputs()
    classification["arms"]["hybrid"]["calibration"]["coverage"] = 0.25
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_oss_rc_a_profile" in report["failed_gates"]


def test_stale_outbox_blocks_staging_success():
    manifest, classification, runtime, before, after = inputs()
    manifest["ledger"]["outbox"].update({"status": "stalled", "pending": 50})
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "ledger_outbox_healthy" in report["failed_gates"]


def test_destructive_outbox_policy_blocks_staging_success():
    manifest, classification, runtime, before, after = inputs()
    manifest["ledger"]["outbox"].update({
        "policy": "delete-stale-rows", "undelivered_deleted": 50,
    })
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "ledger_outbox_healthy" in report["failed_gates"]


def test_stale_recovery_drill_blocks_staging_success():
    manifest, classification, runtime, before, after = inputs()
    manifest["ledger"]["outbox"]["recovery_drill_at"] = "2025-01-01T00:00:00Z"
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "ledger_outbox_healthy" in report["failed_gates"]


def test_artifact_from_different_commit_is_rejected():
    manifest, classification, runtime, before, after = inputs()
    classification["run"]["commit"] = "d" * 40
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_same_run" in report["failed_gates"]


def test_candidate_manifest_must_match_tested_image():
    manifest, classification, runtime, before, after = inputs()
    manifest["candidate"]["image_digest"] = "sha256:" + "d" * 64
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "candidate_artifact_bound" in report["failed_gates"]


def test_malformed_manifest_identity_is_rejected():
    manifest, classification, runtime, before, after = inputs()
    manifest["run"]["commit"] = "main"
    classification["run"]["commit"] = "main"
    runtime["environment"]["git_revision"] = "main"
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "immutable_run_identity" in report["failed_gates"]


def test_missing_supply_chain_evidence_is_incomplete():
    manifest, classification, runtime, before, after = inputs()
    manifest["verification"]["provenance"] = False
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "supply_chain_evidence" in report["failed_gates"]


def test_missing_semantic_runtime_cells_cannot_pass_a_profile():
    manifest, classification, runtime, before, after = inputs()
    runtime["results"]["semantic"] = []
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "runtime_oss_rc_a_profile" in report["failed_gates"]
    json.dumps(report, allow_nan=False)


def test_slow_runtime_cell_cannot_pass_a_profile():
    manifest, classification, runtime, before, after = inputs()
    runtime["results"]["recall"][1]["latency_ms"]["p95"] = 500.0
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "runtime_oss_rc_a_profile" in report["failed_gates"]


def test_each_external_path_requires_parallel_measurement():
    manifest, classification, runtime, before, after = inputs()
    runtime["results"]["semantic"] = [
        cell for cell in runtime["results"]["semantic"]
        if cell["concurrency"] == 1
    ]
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "runtime_oss_rc_a_profile" in report["failed_gates"]


def test_undersampled_runtime_cell_cannot_pass_a_profile():
    manifest, classification, runtime, before, after = inputs()
    runtime["results"]["semantic"][0]["samples"] = 10
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "runtime_oss_rc_a_profile" in report["failed_gates"]


def test_benchmark_must_complete_inside_declared_window():
    manifest, classification, runtime, before, after = inputs()
    runtime["completed_at"] = "2026-09-01T01:30:00Z"
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "runtime_reproducible" in report["failed_gates"]
