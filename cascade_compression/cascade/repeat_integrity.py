"""Integrity admission for the opt-in verified repeat profiles.

No payload-provided trust flag is accepted. The complete supplied message must
reproduce its digest. This binds bytes, not source authenticity or event truth.
Missing/excerpt-only evidence without a matching complete message fails open.

The engine owns the *mechanism*: severity/identity/label gates, message digest
binding, key serialization and the dedup windows. A domain pack owns the
*policy*: which content fields identify a repeat, whether a kind discriminator is
required, whether a count must be present, and which content fields are volatile
for that domain. Packs register a :class:`RepeatIdentity`; a domain with no
registered policy fails open (no repeat handling), which is the conservative
default.

This split exists because the previous implementation hardcoded Kubernetes
shape — ``kind``, ``uid``, ``event_uid``, ``reason`` — in code documented as
domain-agnostic, so no other domain could ever take the repeat path.
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Sequence

VOLATILE_FIELDS = frozenset({'timestamp', 'firstTimestamp', 'lastTimestamp', 'observed_at'})
PROTECTED = frozenset({'compliance', 'fraud', 'sanctions'})


@dataclass(frozen=True)
class RepeatIdentity:
    """A domain pack's repeat identity policy.

    ``identity_fields``   content keys that must be non-empty strings and that
                          participate in the key material.
    ``require_kind``      the signal must carry a non-empty string ``kind``.
    ``exact_kind``        the exact-repeat profile admits only this ``kind``.
    ``require_count``     an int ``count`` >= 1 must be present (latest-wins
                          profiles fold monotonic growth in).
    ``ignore_fields``     additional content keys that are volatile *for this
                          domain* and must not enter the key material. They stay
                          in the stored evidence either way.
    ``severities``        severities this profile is qualified for. Medium-only
                          was assumed in the engine; some domains repeat at
                          info/low (routine task output) and never at medium.
    """

    identity_fields: Sequence[str] = field(default_factory=tuple)
    require_kind: bool = False
    exact_kind: str | None = None
    require_count: bool = False
    ignore_fields: Sequence[str] = field(default_factory=tuple)
    severities: Sequence[str] = field(default_factory=lambda: ("medium",))

    def __post_init__(self):
        object.__setattr__(self, "identity_fields", tuple(self.identity_fields))
        object.__setattr__(self, "ignore_fields", tuple(self.ignore_fields))
        object.__setattr__(self, "severities", tuple(self.severities))
        if not self.severities:
            raise ValueError("severities must not be empty")
        for name in self.identity_fields:
            if not isinstance(name, str) or not name:
                raise ValueError("identity_fields must be non-empty strings")
        for name in self.ignore_fields:
            if not isinstance(name, str) or not name:
                raise ValueError("ignore_fields must be non-empty strings")
        if self.exact_kind is not None and not self.exact_kind.strip():
            raise ValueError("exact_kind must be a non-empty string")


# Compatibility default. This is the shape the verified-repeat profiles were
# qualified on: an involved-object ``kind``, event identity in
# ``uid``/``event_uid``/``reason``, and a monotonically increasing ``count``.
# It is applied to signals whose domain pack declares no ``REPEAT_IDENTITY``, so
# that admission stays domain-agnostic rather than gated on a pack being
# installed. Packs override it to describe their own observations.
DEFAULT_IDENTITY = RepeatIdentity(
    identity_fields=("uid", "event_uid", "reason"),
    require_kind=True,
    exact_kind="Pod",
    require_count=True,
    severities=("medium",),
)

_POLICIES: Dict[str, RepeatIdentity] = {}


def register_repeat_identity(domain: str, policy: RepeatIdentity) -> None:
    """Bind a pack's policy to its domain name. Called when a pack loads."""
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain must be a non-empty string")
    if not isinstance(policy, RepeatIdentity):
        raise ValueError("policy must be a RepeatIdentity")
    _POLICIES[domain.strip().lower()] = policy


def registered_repeat_identities() -> Dict[str, RepeatIdentity]:
    return dict(_POLICIES)


def _policy_for(signal) -> RepeatIdentity | None:
    labels = getattr(signal, "labels", None)
    if not isinstance(labels, dict):
        return None
    domain = labels.get("domain", "")
    if not isinstance(domain, str):
        return DEFAULT_IDENTITY
    return _POLICIES.get(domain.strip().lower(), DEFAULT_IDENTITY)


def _verified_repeat_admission(signal, *, exact: bool, policy: RepeatIdentity):
    """Shared admission gates for the verified repeat profiles.

    Returns (identity, labels, content) when the signal passes every gate,
    else None. No payload-provided trust flag is accepted; the complete
    message must reproduce its digest. Normalized medium alone is not
    evidence of a valid original severity; older signal object
    implementations without the marker fail open.

    The exact-repeat profile keeps whatever discriminator a pack declares
    (``exact_kind``); the update profile only requires a non-empty
    discriminator when the pack requires one, so cross-kind observations can
    never merge.
    """
    if getattr(signal, 'severity_input_valid', False) is not True:
        return None
    content, labels = signal.content, signal.labels
    if not isinstance(content, dict) or not isinstance(labels, dict):
        return None
    if (labels.get('label') in PROTECTED
            or signal.severity not in policy.severities
            or not isinstance(signal.signal_type, str) or not signal.signal_type.strip()):
        return None
    if policy.require_kind:
        kind = content.get('kind')
        if not (isinstance(kind, str) and kind.strip()):
            return None
        if exact and policy.exact_kind is not None and kind != policy.exact_kind:
            return None
    # The domain label and signal_type participate in the identity key so
    # cross-domain observations never merge, but no specific domain or
    # signal_type prefix is required for admission. The domain is optional
    # metadata: it differentiates keys but doesn't gate admission.
    required_identity = [signal.source, signal.cluster, signal.namespace]
    required_identity.extend(content.get(name) for name in policy.identity_fields)
    if not all(isinstance(value, str) and value.strip() for value in required_identity):
        return None
    identity = [signal.source, signal.cluster, signal.namespace,
                labels.get('domain', ''), signal.signal_type]
    identity.extend(content.get(name) for name in policy.identity_fields)
    if policy.require_count:
        if type(content.get('count')) is not int or content['count'] < 1:
            return None
    message, claimed = content.get('message'), content.get('message_sha256')
    if not isinstance(message, str) or not message.strip() or not isinstance(claimed, str):
        return None
    try:
        if hashlib.sha256(message.encode('utf-8')).hexdigest() != claimed:
            return None
    except UnicodeError:
        return None
    return identity, labels, content


def _serialize_key(signal, identity, labels, content, exclude=()):
    ignore = set(VOLATILE_FIELDS) | set(exclude)
    stable = {key: value for key, value in content.items()
              if key not in ignore}
    serialized = json.dumps([signal.signal_type, signal.severity, identity, labels, stable],
                            sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                            allow_nan=False)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def verified_repeat_key(signal):
    """Exact-repeat identity: the full stable content, count included."""
    policy = _policy_for(signal)
    if policy is None:
        return None
    admitted = _verified_repeat_admission(signal, exact=True, policy=policy)
    if admitted is None:
        return None
    identity, labels, content = admitted
    try:
        return _serialize_key(signal, identity, labels, content,
                              exclude=policy.ignore_fields)
    except (ValueError, TypeError):
        return None


def verified_repeat_update_key(signal):
    """Latest-wins identity for the repeat-with-update profile.

    Same admission gates, but count is excluded from the key material: a
    monotonic count increase on the same identity is an update to fold into
    the retained observation, not new information to deliver. Admission
    still requires an int count >= 1 when the pack requires a count, so
    decreases and malformed counts fail open to delivery.
    """
    policy = _policy_for(signal)
    if policy is None:
        return None
    admitted = _verified_repeat_admission(signal, exact=False, policy=policy)
    if admitted is None:
        return None
    identity, labels, content = admitted
    try:
        return _serialize_key(signal, identity, labels, content,
                              exclude=('count',) + tuple(policy.ignore_fields))
    except (ValueError, TypeError):
        return None
