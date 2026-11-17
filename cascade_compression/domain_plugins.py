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
    few_shot_exemplars: dict | None = None
    value_rules: dict | None = None


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
    _register_repeat_identities(entry_point.name, module)
    return DomainPlugin(
        name=entry_point.name,
        module=module,
        system_prompt=prompt,
        memory_config=getattr(module, "MEMORY_CONFIG", None),
        few_shot_exemplars=getattr(module, "FEW_SHOT_EXEMPLARS", None),
        value_rules=getattr(module, "VALUE_RULES", None),
    )


def _register_repeat_identities(name: str, module: ModuleType) -> None:
    """Register a pack's identity policy under every domain label it covers.

    One pack can emit several domain labels (the robotic pack emits robotic,
    plc and vision), and the policy is keyed by the signal's label. The engine
    owns the mechanism; the pack owns the policy and the labels it applies to.
    """
    from .cascade.repeat_integrity import RepeatIdentity, register_repeat_identity

    policy = getattr(module, "REPEAT_IDENTITY", None)
    if not isinstance(policy, RepeatIdentity):
        return
    register_repeat_identity(name, policy)
    extra = getattr(module, "REPEAT_IDENTITY_DOMAINS", ())
    if isinstance(extra, str):
        extra = (extra,)
    for label in extra:
        if isinstance(label, str) and label.strip() and label != name:
            register_repeat_identity(label, policy)


def register_all_repeat_identities() -> dict[str, str]:
    """Register every installed pack's repeat identity policy.

    A process can see more than one domain (the federated memory aggregator
    receives several), so registration cannot depend on which pack the local
    instance declares. Idempotent; a pack that declares nothing is skipped and
    its domain therefore takes no repeat path.
    """
    from .cascade.repeat_integrity import RepeatIdentity, register_repeat_identity

    registered: dict[str, str] = {}
    for name, plugin in discover_domain_plugins().items():
        _register_repeat_identities(name, plugin.module)
        if isinstance(getattr(plugin.module, "REPEAT_IDENTITY", None), RepeatIdentity):
            registered[name] = "declared"
    return registered


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
