"""Access the versioned public Cascade contracts from source or installed packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .resources import resource_path


CONTRACT_MANIFEST_VERSION = "1.0.0"


def contract_manifest() -> dict[str, Any]:
    with resource_path("contracts", "manifest.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("manifest_version") != CONTRACT_MANIFEST_VERSION:
        raise ValueError("unsupported Cascade contract manifest version")
    return manifest


def contract_schema(contract_id: str) -> Path:
    matches = [
        item for item in contract_manifest()["contracts"] if item["id"] == contract_id
    ]
    if len(matches) != 1:
        raise KeyError(f"unknown Cascade contract: {contract_id}")
    return resource_path("contracts", matches[0]["schema"])
