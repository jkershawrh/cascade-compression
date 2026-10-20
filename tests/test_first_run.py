"""Exercise an outside consumer's bounded replay and error contract."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def replay(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CASCADE_", "LITELLM_"))}
    env["CASCADE_LLM_URL"] = "http://127.0.0.1:9/v1"
    env["CASCADE_LLM_MODEL"] = "must-not-be-called"
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-c", "from cascade_compression.cli import replay; replay()", *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )


def test_bundled_first_run_is_nonempty_and_never_calls_ambient_model(tmp_path):
    result = replay(tmp_path, "--domain", "finance", "--data", str(ROOT / "data/first-run-finance.json"),
                    "--deterministic-only", "--export-route-ledger", "result.json")
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "result.json").read_text())
    assert data["total_signals"] == 2
    assert data["model_calls"] == 0
    assert data["classification_status"] == "not_requested"
    assert data["dangerous_misses_measured"] is False


def test_no_source_fails_instead_of_successful_zero_record_replay(tmp_path):
    result = replay(tmp_path, "--domain", "finance", "--deterministic-only")
    assert result.returncode != 0
    assert "no usable data source" in result.stderr


def test_empty_input_requires_explicit_opt_in(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("[]")
    args = ("--domain", "finance", "--data", str(path), "--deterministic-only")
    assert replay(tmp_path, *args).returncode != 0
    assert replay(tmp_path, *args, "--allow-empty").returncode == 0


def test_incomplete_model_replay_writes_evidence_and_fails(tmp_path):
    result = replay(tmp_path, "--domain", "finance", "--data", str(ROOT / "data/first-run-finance.json"),
                    "--llm-drain-timeout", "0", "--export-route-ledger", "result.json")
    assert result.returncode == 2
    data = json.loads((tmp_path / "result.json").read_text())
    assert data["classification_status"] == "incomplete"
    assert data["ai_work_complete"] is False


def test_allow_empty_does_not_hide_a_failed_explicit_source(tmp_path):
    result = replay(tmp_path, "--domain", "finance", "--data", str(tmp_path / "missing.json"),
                    "--deterministic-only", "--allow-empty")
    assert result.returncode != 0


def test_failed_classification_is_not_reported_as_complete(tmp_path, monkeypatch):
    from cascade_compression import cli
    from cascade_compression.bridge import CascadeBridge

    class FailedClassificationBridge(CascadeBridge):
        def wait_for_llm(self, timeout_seconds=60):
            with self._llm_queue_lock:
                enqueued = self._llm_accounting_enqueued
                self._llm_buffer.clear()
                self._llm_priority_buffer.clear()
                self._llm_accounting_dispatched = enqueued
                self._llm_accounting_failed = enqueued
            return True

    for key in list(os.environ):
        if key.startswith(("CASCADE_", "LITELLM_")):
            monkeypatch.delenv(key)
    monkeypatch.setattr("cascade_compression.bridge.CascadeBridge", FailedClassificationBridge)
    monkeypatch.setattr(sys, "argv", [
        "cascade-replay", "--domain", "finance", "--data", str(ROOT / "data/first-run-finance.json"),
        "--export-route-ledger", str(tmp_path / "result.json"),
    ])
    import pytest
    with pytest.raises(SystemExit) as failure:
        cli.replay()
    assert failure.value.code == 2
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["ai_work_complete"] is False
    assert result["classification_status"] == "incomplete"
