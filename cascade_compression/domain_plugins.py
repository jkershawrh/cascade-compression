"""Discovery and validation for Cascade domain-pack plugins."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from types import ModuleType
from typing import Any, Iterable


DOMAIN_ENTRY_POINT_GROUP = "cascade_compression.domains"


class DomainPluginError(RuntimeError):
    """Raised when an installed domain pack violates the public plugin contract."""


@dataclass(frozen=True)
class DomainPlugin:
    name: str
    module: ModuleType
    system_prompt: str
    memory_config: Any = None


def _installed_entry_points() -> Iterable[EntryPoint]:
    discovered = entry_points()
    if hasattr(discovered, "select"):
        return discovered.select(group=DOMAIN_ENTRY_POINT_GROUP)
    return discovered.get(DOMAIN_ENTRY_POINT_GROUP, ())


def _validate(entry_point: EntryPoint) -> DomainPlugin:
    module = entry_point.load()
    if not isinstance(module, ModuleType):
        raise DomainPluginError(
            f"domain entry point {entry_point.name!r} must load a module"
        )
    declared_name = getattr(module, "DOMAIN", None)
    if declared_name != entry_point.name:
        raise DomainPluginError(
            f"domain entry point {entry_point.name!r} declares name {declared_name!r}"
        )
    prompt = getattr(module, "SYSTEM_PROMPT", "")
    if not isinstance(prompt, str):
        raise DomainPluginError(
            f"domain {entry_point.name!r} SYSTEM_PROMPT must be a string"
        )
    return DomainPlugin(
        name=entry_point.name,
        module=module,
        system_prompt=prompt,
        memory_config=getattr(module, "MEMORY_CONFIG", None),
    )


def discover_domain_plugins(
    candidates: Iterable[EntryPoint] | None = None,
) -> dict[str, DomainPlugin]:
    plugins: dict[str, DomainPlugin] = {}
    for entry_point in candidates if candidates is not None else _installed_entry_points():
        plugin = _validate(entry_point)
        if entry_point.name in plugins:
            if candidates is not None or plugins[entry_point.name].module is not plugin.module:
                raise DomainPluginError(f"duplicate domain plugin {entry_point.name!r}")
            continue
        plugins[entry_point.name] = plugin
    return plugins


def load_domain_plugin(name: str) -> DomainPlugin:
    try:
        return discover_domain_plugins()[name]
    except KeyError as exc:
        raise DomainPluginError(f"domain plugin {name!r} is not installed") from exc
