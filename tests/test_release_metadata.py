from pathlib import Path
import json
import re
import cascade_compression
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_package_and_runtime_versions_match():
    project = (ROOT / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
    assert declared is not None
    assert declared.group(1) == cascade_compression.__version__
    assert re.fullmatch(
        r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?",
        cascade_compression.__version__,
    )


def test_first_release_policy_files_exist():
    required = {
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "GOVERNANCE.md",
        "LICENSE",
        "RELEASING.md",
        "SECURITY.md",
        "SUPPORT.md",
    }
    assert all((ROOT / name).is_file() for name in required)


def test_container_inputs_are_immutable_and_hash_locked():
    containerfile = (ROOT / "Containerfile").read_text()
    bases = re.findall(r"^FROM\s+(\S+)", containerfile, re.MULTILINE)
    assert len(bases) == 2
    assert len(set(bases)) == 1
    assert re.fullmatch(r"[^:]+(?:/[^:]+)+@sha256:[0-9a-f]{64}", bases[0])
    assert ":latest" not in containerfile
    for filename in ("requirements-build.lock", "requirements-container.lock"):
        lock = (ROOT / filename).read_text()
        assert "--hash=sha256:" in lock

    runtime_lock = (ROOT / "requirements-container.lock").read_text()
    assert re.search(r"^grpcio==", runtime_lock, re.MULTILINE)
    assert re.search(r"^protobuf==", runtime_lock, re.MULTILINE)
    assert "scripts/semantic_adapter_smoke.py" in containerfile


def test_release_workflows_verify_the_semantic_container_lock():
    for filename in ("ci.yml", "staging-candidate.yml", "release.yml"):
        workflow = (ROOT / ".github" / "workflows" / filename).read_text()
        assert "--extra semantic-classifier" in workflow

    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    semantic_steps = str(ci["jobs"]["semantic-adapter"]["steps"])
    assert "--extra semantic-classifier" in semantic_steps
    assert "semantic_adapter_smoke.py" in semantic_steps


def test_workflow_actions_and_runner_are_pinned():
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        text = workflow.read_text()
        assert "runs-on: ubuntu-latest" not in text
        assert "runs-on: ubuntu-24.04" in text
        action_refs = re.findall(r"uses:\s+[^\s@]+@([^\s#]+)", text)
        assert action_refs
        assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)


def test_contextual_data_cannot_masquerade_as_release_evidence():
    json_artifacts = (
        "config/corpora.json",
        "data/benchmark_matrix.json",
        "data/hardware_profiles.json",
        "data/model_profiles.json",
        "data/workload_profiles.json",
    )
    for filename in json_artifacts:
        payload = json.loads((ROOT / filename).read_text())
        evidence = payload.get("evidence")
        assert isinstance(evidence, dict), filename
        assert evidence.get("release_evidence") is False, filename
        assert evidence.get("raw_sources_in_repository") is False, filename
        assert evidence.get("class"), filename
        assert evidence.get("limitations"), filename

    register = (ROOT / "docs" / "evidence-register.md").read_text()
    for filename in json_artifacts:
        assert filename in register


def test_release_publication_is_gated_by_verified_package_and_container():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["container"]["needs"] == "verify"
    assert set(jobs["release"]["needs"]) == {"verify", "container"}

    verify_steps = str(jobs["verify"]["steps"])
    container_steps = str(jobs["container"]["steps"])
    release_steps = str(jobs["release"]["steps"])
    container_push = next(step for step in jobs["container"]["steps"] if step.get("id") == "push")
    assert "Verify tag and package version" in verify_steps
    assert "check_public_boundary.py" in verify_steps
    assert container_push["with"]["push"] is True
    assert "release-container-manifest.json" in container_steps
    assert "gh release create" not in verify_steps
    assert "gh release create" not in container_steps
    assert "gh release create" in release_steps
    assert "release-artifact-manifest.json" in release_steps


def test_candidate_manifest_is_attested_before_upload():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "staging-candidate.yml").read_text()
    )
    steps = workflow["jobs"]["container"]["steps"]
    names = [step.get("name", "") for step in steps]
    write_index = names.index("Write immutable candidate manifest")
    attest_index = names.index("Attest immutable candidate manifest")
    upload_index = next(
        index for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert write_index < attest_index < upload_index
    workflow_text = (ROOT / ".github" / "workflows" / "staging-candidate.yml").read_text()
    assert '"schema_version": "cascade.staging-candidate.v1alpha3"' in workflow_text
    assert '"manifest_provenance": True' in workflow_text
