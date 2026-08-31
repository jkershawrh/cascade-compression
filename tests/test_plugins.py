"""Tests for the public collector plugin loading contract."""

from importlib.metadata import EntryPoint

import pytest

from cascade_compression.collectors.base import BaseCollector
from cascade_compression.plugins import CollectorPluginError, discover_collector_plugins


class ExampleCollector(BaseCollector):
    name = "example"
    capabilities = ("batch", "stream")
    signal_types = ("example.event",)

    def connect(self, config: dict) -> bool:
        return True

    def collect(self) -> list:
        return []

    def collect_all(self) -> list:
        return []


def entry(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group="cascade_compression.collectors")


def test_discovers_and_instantiates_valid_collector_plugin():
    plugin = discover_collector_plugins(
        [entry("example", "tests.test_plugins:ExampleCollector")]
    )["example"]
    assert plugin.descriptor.supports_stream is True
    assert isinstance(plugin.create(), ExampleCollector)


def test_rejects_entry_point_name_mismatch():
    with pytest.raises(CollectorPluginError, match="declares name"):
        discover_collector_plugins(
            [entry("different", "tests.test_plugins:ExampleCollector")]
        )


def test_rejects_non_collector_entry_point():
    with pytest.raises(CollectorPluginError, match="BaseCollector"):
        discover_collector_plugins([entry("invalid", "builtins:dict")])


def test_rejects_duplicate_plugin_names():
    candidate = entry("example", "tests.test_plugins:ExampleCollector")
    with pytest.raises(CollectorPluginError, match="duplicate"):
        discover_collector_plugins([candidate, candidate])


def test_coalesces_identical_duplicates_reported_by_installed_metadata(monkeypatch):
    candidate = entry("example", "tests.test_plugins:ExampleCollector")
    monkeypatch.setattr(
        "cascade_compression.plugins._installed_entry_points",
        lambda: [candidate, candidate],
    )
    assert set(discover_collector_plugins()) == {"example"}
