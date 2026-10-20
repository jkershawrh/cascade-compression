#!/usr/bin/env python3
"""Smoke-test an installed OSS package without repository import paths."""

import json
import os
import subprocess
import sys
import tempfile
from importlib.metadata import version

from fastapi.testclient import TestClient

from cascade_compression import __version__
from cascade_compression.anchor_engineering import validate_taxonomy
from cascade_compression.classifier import normalize_label
from cascade_compression.collectors.finance import FinanceCollector
from cascade_compression.contracts import contract_schema
from cascade_compression.domain_plugins import discover_domain_plugins
from cascade_compression.plugins import discover_collector_plugins
from cascade_compression.resources import resource_dir, resource_path
from cascade_compression.service import app


def main() -> None:
    assert __version__ == version("cascade-compression")
    assert len(discover_collector_plugins()) == 8
    assert len(discover_domain_plugins()) == 7
    assert resource_path("config", "strategies.yaml").is_file()
    taxonomy_path = resource_path("config", "cascade-sc-taxonomy.json")
    assert taxonomy_path.is_file()
    validate_taxonomy(json.loads(taxonomy_path.read_text(encoding="utf-8")))
    assert normalize_label("needs_attention") == "needs_attention"
    assert resource_path("contracts", "manifest.json").is_file()
    assert contract_schema("cascade.classification-evaluation").is_file()
    assert contract_schema("cascade.adjudication-summary").is_file()
    assert contract_schema("cascade.holdout-manifest").is_file()
    assert contract_schema("cascade.staging-manifest").is_file()
    assert contract_schema("cascade.staging-evidence").is_file()
    frontend = resource_dir("frontend")
    assert frontend is not None and (frontend / "index.html").is_file()

    collector = FinanceCollector(synthetic_count=3)
    assert collector.connect({})
    assert collector.collect_all()

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["package_version"] == __version__
        assert client.get("/").status_code == 200
    with tempfile.TemporaryDirectory() as directory:
        env = {key: value for key, value in os.environ.items() if not key.startswith(("CASCADE_", "LITELLM_"))}
        result = subprocess.run([
            sys.executable, "-c", "from cascade_compression.cli import replay; replay()",
            "--domain", "finance", "--data", str(resource_path("data", "first-run-finance.json")),
            "--deterministic-only", "--export-route-ledger", "first-run.json",
        ], cwd=directory, env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        from pathlib import Path
        replay = json.loads((Path(directory) / "first-run.json").read_text())
        assert replay["total_signals"] == 2 and replay["model_calls"] == 0
        assert replay["classification_status"] == "not_requested"
    print("release smoke: PASS")


if __name__ == "__main__":
    main()
