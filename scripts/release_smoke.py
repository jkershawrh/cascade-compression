#!/usr/bin/env python3
"""Smoke-test an installed OSS package without repository import paths."""

import json
from importlib.metadata import distribution

from fastapi.testclient import TestClient

from cascade_compression import __version__
from cascade_compression.anchor_engineering import validate_taxonomy
from cascade_compression.classifier import normalize_label
from cascade_compression.collectors.finance import FinanceCollector
from cascade_compression.contracts import contract_schema
from cascade_compression.domain_plugins import DOMAIN_ENTRY_POINT_GROUP, discover_domain_plugins
from cascade_compression.plugins import COLLECTOR_ENTRY_POINT_GROUP, discover_collector_plugins
from cascade_compression.resources import resource_dir, resource_path
from cascade_compression.service import app


def check_plugins(package, group, discovered, required):
    """Check the bundled public names and loadability of every shipped entry."""
    shipped = {entry.name for entry in package.entry_points if entry.group == group}
    missing = required - shipped
    assert not missing, f"{group} missing from wheel metadata: {sorted(missing)}"
    unloaded = shipped - discovered.keys()
    assert not unloaded, f"{group} missing from discovery: {sorted(unloaded)}"


def main() -> None:
    package = distribution("cascade-compression")
    assert __version__ == package.version
    check_plugins(package, COLLECTOR_ENTRY_POINT_GROUP, discover_collector_plugins(), {
        "finance", "healthcare", "insurance", "kubernetes", "macos",
        "prometheus", "retail", "telecom",
    })
    check_plugins(package, DOMAIN_ENTRY_POINT_GROUP, discover_domain_plugins(), {
        "aap", "cloud", "distributed", "finance", "healthcare", "insurance",
        "kubernetes", "macos", "retail", "robotic", "supercomputer", "telecom",
    })
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

    # Enter the app lifespan so the service bridge is created; /health is
    # readiness-honest (503 while the bridge is absent or state is unhealthy).
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
    print("release smoke: PASS")


if __name__ == "__main__":
    main()
