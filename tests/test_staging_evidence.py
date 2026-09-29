import json
from pathlib import Path

import jsonschema

from cascade_compression.evaluation import evaluate_classifiers
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
    classification = evaluate_classifiers({
        "schema_version": "cascade.classification-input.v1alpha1",
        "dataset": {
            "name": "held-out", "revision": "v1",
            "adjudication": {
                "status": "complete", "method": "double-review",
                "independent": True, "reviewers": 2,
            },
        },
        "run": {**run, "model_revisions": {"hybrid": "model-v1"}},
        "records": [
            {
                "record_id": f"r{index}", "expected": label,
                "predictions": {"hybrid": {
                    "label": label, "confidence": 1.0,
                    "authoritative": True,
                }},
            }
            for index, label in enumerate(
                ["routine_noise", "known_pattern", "needs_attention", "real_incident"]
                * 50
            )
        ],
    })
    manifest = {
        "schema_version": "cascade.staging-manifest.v1alpha1",
        "run": run,
        "classification_arm": "hybrid",
        "model_revisions": {"hybrid": "model-v1"},
        "runtime_run_id": "runtime-run-1",
        "audit_window": {
            "stats_before_at": "2026-08-31T23:59:00Z",
            "stats_after_at": "2026-09-01T01:01:00Z",
        },
        "ledger": {
            "capacity_measured": True,
            "used_fraction": 0.4,
            "alert_threshold": 0.8,
            "outbox": {"status": "healthy", "pending": 0, "policy": "relay-and-archive"},
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
        "environment": {
            "cpu_allocation_verified": True,
            "git_revision": COMMIT,
        },
        "config": {"run_id": "runtime-run-1"},
        "results": {"nano": [{"errors": 0}]},
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


def test_small_perfect_corpus_cannot_pass_a_profile():
    manifest, classification, runtime, before, after = inputs()
    arm = classification["arms"]["hybrid"]
    arm["records"] = 20
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


def test_artifact_from_different_commit_is_rejected():
    manifest, classification, runtime, before, after = inputs()
    classification["run"]["commit"] = "d" * 40
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "classification_same_run" in report["failed_gates"]


def test_missing_supply_chain_evidence_is_incomplete():
    manifest, classification, runtime, before, after = inputs()
    manifest["verification"]["provenance"] = False
    report = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    assert "supply_chain_evidence" in report["failed_gates"]
