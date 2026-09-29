import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

from cascade_compression.staging_evidence import build_staging_evidence


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_staging_handoff", ROOT / "scripts" / "verify_staging_handoff.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
INPUTS_SPEC = importlib.util.spec_from_file_location(
    "staging_evidence_test_inputs", ROOT / "tests" / "test_staging_evidence.py",
)
INPUTS_MODULE = importlib.util.module_from_spec(INPUTS_SPEC)
assert INPUTS_SPEC.loader is not None
INPUTS_SPEC.loader.exec_module(INPUTS_MODULE)
inputs = INPUTS_MODULE.inputs


def handoff_files(tmp_path):
    manifest, classification, runtime, before, after = inputs()
    package_path = tmp_path / "candidate-package"
    package_path.mkdir()
    payloads = {
        "cascade.whl": b"wheel",
        "cascade.tar.gz": b"source",
        "cascade.spdx.json": b"{}",
    }
    manifest["candidate"]["package_artifacts"] = []
    for name, payload in payloads.items():
        (package_path / name).write_bytes(payload)
        manifest["candidate"]["package_artifacts"].append({
            "name": name,
            "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        })
    evidence = build_staging_evidence(
        manifest, classification, runtime, before, after,
    )
    evidence_path = tmp_path / "staging-evidence.json"
    candidate_path = tmp_path / "staging-candidate-manifest.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    candidate_path.write_text(
        json.dumps(manifest["candidate"]), encoding="utf-8",
    )
    return (
        evidence, manifest["candidate"], evidence_path, candidate_path,
        package_path,
    )


def test_successful_handoff_binds_exact_candidate(tmp_path):
    evidence, candidate, evidence_path, candidate_path, package_path = (
        handoff_files(tmp_path)
    )
    summary = MODULE.verify_handoff(
        evidence_path, candidate_path, evidence["run"]["commit"],
        candidate["repository"], package_path,
    )
    assert summary["image_digest"] == evidence["run"]["image_digest"]
    assert summary["candidate_manifest_digest"] == evidence["candidate"][
        "manifest_digest"
    ]


@pytest.mark.parametrize("tamper", ["status", "commit", "image", "candidate"])
def test_tampered_handoff_is_rejected(tmp_path, tamper):
    evidence, candidate, evidence_path, candidate_path, _ = handoff_files(tmp_path)
    if tamper == "status":
        evidence["status"] = "incomplete"
    elif tamper == "commit":
        evidence["run"]["commit"] = "f" * 40
    elif tamper == "image":
        evidence["run"]["image_digest"] = "sha256:" + "f" * 64
    else:
        candidate["workflow_run_attempt"] = 2
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises((ValueError, KeyError)):
        MODULE.verify_handoff(
            evidence_path, candidate_path, "a" * 40,
            "example/cascade-compression",
        )


def test_duplicate_json_keys_are_rejected(tmp_path):
    _, candidate, evidence_path, candidate_path, _ = handoff_files(tmp_path)
    evidence_path.write_text(
        '{"schema_version":"cascade.staging-evidence.v1alpha3",'
        '"status":"staging_success","status":"incomplete"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        MODULE.verify_handoff(
            evidence_path, candidate_path, "a" * 40,
            candidate["repository"],
        )


def test_candidate_package_tampering_is_rejected(tmp_path):
    evidence, candidate, evidence_path, candidate_path, package_path = (
        handoff_files(tmp_path)
    )
    (package_path / "cascade.whl").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        MODULE.verify_handoff(
            evidence_path, candidate_path, evidence["run"]["commit"],
            candidate["repository"], package_path,
        )
