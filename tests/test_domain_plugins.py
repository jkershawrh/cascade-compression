"""Tests for the public domain-pack plugin contract."""

from importlib.metadata import EntryPoint
from types import SimpleNamespace

import pytest

from cascade_compression.domain_plugins import (
    DomainPluginError,
    discover_domain_plugins,
)


def entry(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group="cascade_compression.domains")


def test_discovers_valid_domain_module():
    plugin = discover_domain_plugins(
        [entry("finance", "cascade_compression.domains.finance")]
    )["finance"]
    assert plugin.name == "finance"
    assert plugin.system_prompt


def test_rejects_domain_name_mismatch():
    with pytest.raises(DomainPluginError, match="declares name"):
        discover_domain_plugins(
            [entry("different", "cascade_compression.domains.finance")]
        )


def test_rejects_non_module_entry_point():
    with pytest.raises(DomainPluginError, match="must load a module"):
        discover_domain_plugins([entry("invalid", "builtins:dict")])


def test_rejects_explicit_duplicate_names():
    candidate = entry("finance", "cascade_compression.domains.finance")
    with pytest.raises(DomainPluginError, match="duplicate"):
        discover_domain_plugins([candidate, candidate])


def test_coalesces_identical_installed_metadata(monkeypatch):
    candidate = entry("finance", "cascade_compression.domains.finance")
    monkeypatch.setattr(
        "cascade_compression.domain_plugins._installed_entry_points",
        lambda: [candidate, candidate],
    )
    assert set(discover_domain_plugins()) == {"finance"}


def test_cli_loads_external_domain_entry_point(monkeypatch):
    from cascade_compression.cli import _load_domain

    module = SimpleNamespace(DOMAIN="external")
    monkeypatch.setattr(
        "cascade_compression.domain_plugins.load_domain_plugin",
        lambda name: SimpleNamespace(module=module),
    )
    assert _load_domain("external") is module
