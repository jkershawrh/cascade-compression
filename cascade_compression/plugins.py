"""Discovery and validation for external Cascade collector plugins."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Iterable

from .collectors.base import BaseCollector, CollectorDescriptor


COLLECTOR_ENTRY_POINT_GROUP = "cascade_compression.collectors"
SUPPORTED_COLLECTOR_API_MAJOR = "1"
SUPPORTED_CAPABILITIES = {"batch", "stream", "replay", "multi_cluster"}


class CollectorPluginError(RuntimeError):
    """Raised when an installed collector violates the public plugin contract."""


@dataclass(frozen=True)
class CollectorPlugin:
    name: str
    collector_type: type[BaseCollector]
    descriptor: CollectorDescriptor

    def create(self, **kwargs) -> BaseCollector:
        return self.collector_type(**kwargs)


def _installed_entry_points() -> Iterable[EntryPoint]:
    discovered = entry_points()
    if hasattr(discovered, "select"):
        return discovered.select(group=COLLECTOR_ENTRY_POINT_GROUP)
    return discovered.get(COLLECTOR_ENTRY_POINT_GROUP, ())


def _validate(entry_point: EntryPoint) -> CollectorPlugin:
    collector_type = entry_point.load()
    if not isinstance(collector_type, type) or not issubclass(collector_type, BaseCollector):
        raise CollectorPluginError(
            f"collector entry point {entry_point.name!r} must load a BaseCollector subclass"
        )
    descriptor = collector_type.descriptor()
    if descriptor.name != entry_point.name:
        raise CollectorPluginError(
            f"collector entry point {entry_point.name!r} declares name {descriptor.name!r}"
        )
    if descriptor.api_version.split(".", 1)[0] != SUPPORTED_COLLECTOR_API_MAJOR:
        raise CollectorPluginError(
            f"collector {entry_point.name!r} uses unsupported API {descriptor.api_version!r}"
        )
    unsupported = set(descriptor.capabilities) - SUPPORTED_CAPABILITIES
    if unsupported:
        raise CollectorPluginError(
            f"collector {entry_point.name!r} has unsupported capabilities {sorted(unsupported)}"
        )
    return CollectorPlugin(entry_point.name, collector_type, descriptor)


def discover_collector_plugins(
    candidates: Iterable[EntryPoint] | None = None,
) -> dict[str, CollectorPlugin]:
    """Load installed collector entry points and fail on duplicates or invalid plugins."""

    plugins: dict[str, CollectorPlugin] = {}
    for entry_point in candidates if candidates is not None else _installed_entry_points():
        plugin = _validate(entry_point)
        if entry_point.name in plugins:
            if candidates is not None or plugins[entry_point.name] != plugin:
                raise CollectorPluginError(f"duplicate collector plugin {entry_point.name!r}")
            continue
        plugins[entry_point.name] = plugin
    return plugins


def load_collector_plugin(name: str) -> CollectorPlugin:
    try:
        return discover_collector_plugins()[name]
    except KeyError as exc:
        raise CollectorPluginError(f"collector plugin {name!r} is not installed") from exc
