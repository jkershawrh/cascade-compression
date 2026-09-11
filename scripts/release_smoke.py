#!/usr/bin/env python3
"""Smoke-test an installed OSS package without repository import paths."""

import json

from fastapi.testclient import TestClient

from cascade_compression import __version__
from cascade_compression.anchor_engineering import validate_taxonomy
from cascade_compression.classifier import normalize_label
from cascade_compression.collectors.finance import FinanceCollector
from cascade_compression.domain_plugins import discover_domain_plugins
from cascade_compression.plugins import discover_collector_plugins
from cascade_compression.resources import resource_dir, resource_path
from cascade_compression.service import app


def main() -> None:
    assert __version__ == "0.1.0"
    assert len(discover_collector_plugins()) == 8
    assert len(discover_domain_plugins()) == 7
    assert resource_path("config", "strategies.yaml").is_file()
    taxonomy_path = resource_path("config", "cascade-sc-taxonomy.json")
    assert taxonomy_path.is_file()
    validate_taxonomy(json.loads(taxonomy_path.read_text(encoding="utf-8")))
    assert normalize_label("needs_attention") == "needs_attention"
    assert resource_path("contracts", "manifest.json").is_file()
    frontend = resource_dir("frontend")
    assert frontend is not None and (frontend / "index.html").is_file()

    collector = FinanceCollector(synthetic_count=3)
    assert collector.connect({})
    assert collector.collect_all()

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    print("release smoke: PASS")


if __name__ == "__main__":
    main()
