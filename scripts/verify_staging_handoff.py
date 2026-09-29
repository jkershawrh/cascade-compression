#!/usr/bin/env python3
"""Verify the sanitized staging-to-release handoff and candidate binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _closed_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> dict:
    document = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_closed_object,
    )
    if not isinstance(document, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return document


def _digest(document: dict) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_evidence(
    evidence_path: Path,
    expected_commit: str,
    expected_repository: str,
) -> tuple[dict, dict]:
    if not COMMIT.fullmatch(expected_commit):
        raise ValueError("expected commit must be a full lowercase Git SHA")
    if not expected_repository or expected_repository.strip() != expected_repository:
        raise ValueError("expected repository must be non-empty and normalized")

    evidence = _load(evidence_path)
    evidence_schema = _load(
        ROOT / "contracts" / "schemas" / "staging-evidence.json",
    )
    jsonschema.Draft202012Validator(
        evidence_schema, format_checker=jsonschema.FormatChecker(),
    ).validate(evidence)

    if evidence.get("status") != "staging_success":
        raise ValueError("staging evidence is not successful")
    if evidence.get("failed_gates") != []:
        raise ValueError("staging evidence contains failed gates")
    gates = evidence.get("gates") or []
    gate_names = [gate.get("name") for gate in gates]
    if not gates or len(gate_names) != len(set(gate_names)):
        raise ValueError("staging evidence gates must be non-empty and unique")
    if any(gate.get("passed") is not True for gate in gates):
        raise ValueError("every staging evidence gate must pass")
    if evidence.get("contains_raw_records") is not False:
        raise ValueError("staging evidence must be sanitized")

    run = evidence["run"]
    summary = evidence["candidate"]
    if run.get("commit") != expected_commit:
        raise ValueError("staging evidence is not bound to the release commit")
    if summary.get("repository") != expected_repository:
        raise ValueError("staging evidence repository does not match release repository")
    return evidence, summary


def verify_handoff(
    evidence_path: Path,
    candidate_path: Path,
    expected_commit: str,
    expected_repository: str,
    candidate_package: Optional[Path] = None,
) -> dict:
    evidence, summary = verify_evidence(
        evidence_path, expected_commit, expected_repository,
    )
    candidate = _load(candidate_path)
    manifest_schema = _load(
        ROOT / "contracts" / "schemas" / "staging-manifest.json",
    )
    jsonschema.Draft202012Validator(
        manifest_schema["properties"]["candidate"],
        format_checker=jsonschema.FormatChecker(),
    ).validate(candidate)
    if candidate.get("commit") != expected_commit:
        raise ValueError("candidate manifest is not bound to the release commit")
    if candidate.get("repository") != expected_repository:
        raise ValueError("candidate manifest repository does not match release repository")
    if candidate.get("image") != f"ghcr.io/{expected_repository}":
        raise ValueError("candidate image is outside the release repository")

    candidate_digest = _digest(candidate)
    if not SHA256.fullmatch(candidate_digest):
        raise ValueError("candidate manifest digest is malformed")
    if summary.get("manifest_digest") != candidate_digest:
        raise ValueError("candidate summary digest does not match candidate manifest")
    if evidence["artifact_digests"].get("candidate") != candidate_digest:
        raise ValueError("artifact digest does not match candidate manifest")

    bound_fields = (
        "repository", "image", "image_digest", "workflow_run_id",
        "workflow_run_attempt",
    )
    if any(summary.get(field) != candidate.get(field) for field in bound_fields):
        raise ValueError("candidate summary does not match candidate manifest")
    if evidence["run"].get("image_digest") != candidate.get("image_digest"):
        raise ValueError("tested image digest does not match candidate manifest")

    if candidate_package is not None:
        if not candidate_package.is_dir():
            raise ValueError("candidate package directory does not exist")
        expected_artifacts = {
            item["name"]: item for item in candidate["package_artifacts"]
        }
        if len(expected_artifacts) != len(candidate["package_artifacts"]):
            raise ValueError("candidate manifest contains duplicate artifact names")
        actual_paths = {
            path.name: path for path in candidate_package.iterdir() if path.is_file()
        }
        if set(actual_paths) != set(expected_artifacts):
            raise ValueError("candidate package contents do not match manifest")
        for name, path in actual_paths.items():
            payload = path.read_bytes()
            expected = expected_artifacts[name]
            observed_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            if observed_digest != expected["sha256"]:
                raise ValueError(f"candidate package digest mismatch: {name}")
            if len(payload) != expected["bytes"]:
                raise ValueError(f"candidate package size mismatch: {name}")

    return {
        "commit": expected_commit,
        "repository": expected_repository,
        "image": candidate["image"],
        "image_digest": candidate["image_digest"],
        "candidate_manifest_digest": candidate_digest,
        "candidate_workflow_run_id": candidate["workflow_run_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--candidate-package", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.candidate_manifest:
        summary = verify_handoff(
            args.evidence,
            args.candidate_manifest,
            args.expected_commit,
            args.expected_repository,
            args.candidate_package,
        )
    else:
        evidence, candidate = verify_evidence(
            args.evidence, args.expected_commit, args.expected_repository,
        )
        summary = {
            "commit": args.expected_commit,
            "repository": args.expected_repository,
            "image": candidate["image"],
            "image_digest": candidate["image_digest"],
            "candidate_manifest_digest": candidate["manifest_digest"],
            "candidate_workflow_run_id": candidate["workflow_run_id"],
        }
    payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
