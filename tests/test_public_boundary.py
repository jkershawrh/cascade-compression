import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_public_boundary.py"
SPEC = importlib.util.spec_from_file_location("check_public_boundary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BOUNDARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOUNDARY)


def test_current_tracked_repository_respects_public_boundary():
    assert BOUNDARY.check_repository(ROOT) == []


def test_private_paths_are_rejected():
    assert BOUNDARY.path_violations("deploy/cluster.yaml")
    assert BOUNDARY.path_violations("private/review.jsonl")
    assert BOUNDARY.path_violations("local-evidence/run.json")
    assert BOUNDARY.path_violations("config/.env.production")
    assert BOUNDARY.path_violations("certs/client.pem")


def test_sensitive_content_shapes_are_rejected_without_storing_examples():
    token = ("sha256" + "~" + "x" * 24).encode()
    endpoint = ("https://api." + "example.invalid:6443").encode()
    home = ("/" + "Users/example/private.json").encode()
    alias = ("infra" + "01").encode()
    assert BOUNDARY.content_violations(token)
    assert BOUNDARY.content_violations(endpoint)
    assert BOUNDARY.content_violations(home)
    assert BOUNDARY.content_violations(alias)


def test_binary_payload_is_not_treated_as_text():
    assert BOUNDARY.content_violations(b"binary\0payload") == []
