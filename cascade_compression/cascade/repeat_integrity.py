"""Integrity admission for the opt-in bounded Pod Event repeat profile.

No payload-provided trust flag is accepted. The complete supplied message must
reproduce its digest. This binds bytes, not source authenticity or event truth.
Missing/excerpt-only evidence without a matching complete message fails open.
"""
import hashlib
import json

VOLATILE_FIELDS = frozenset({'timestamp', 'firstTimestamp', 'lastTimestamp', 'observed_at'})
PROTECTED = frozenset({'compliance', 'fraud', 'sanctions'})


def _verified_repeat_admission(signal, require_pod_kind: bool = True):
    """Shared admission gates for the verified repeat profiles.

    Returns (identity, labels, content) when the signal passes every gate,
    else None. No payload-provided trust flag is accepted; the complete
    message must reproduce its digest. Normalized medium alone is not
    evidence of a valid original severity; older signal object
    implementations without the marker fail open.

    The exact-repeat profile keeps the conservative Pod-only scope it was
    qualified under (require_pod_kind=True). The update profile admits any
    involved-object kind with complete identity; the kind then participates
    in the key material so cross-kind observations can never merge.
    """
    if getattr(signal, 'severity_input_valid', False) is not True:
        return None
    content, labels = signal.content, signal.labels
    if not isinstance(content, dict) or not isinstance(labels, dict):
        return None
    if (labels.get('domain') != 'kubernetes' or labels.get('label') in PROTECTED
            or signal.severity != 'medium'
            or not isinstance(signal.signal_type, str) or not signal.signal_type.startswith('event_')
            or (require_pod_kind and content.get('kind') != 'Pod')
            or (not require_pod_kind and not (isinstance(content.get('kind'), str)
                                              and content.get('kind').strip()))):
        return None
    identity = [signal.source, signal.cluster, signal.namespace,
                content.get('uid'), content.get('event_uid'), content.get('reason')]
    if not all(isinstance(value, str) and value.strip() for value in identity):
        return None
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
    stable = {key: value for key, value in content.items()
              if key not in VOLATILE_FIELDS and key not in exclude}
    serialized = json.dumps([signal.signal_type, signal.severity, identity, labels, stable],
                            sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                            allow_nan=False)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def verified_repeat_key(signal):
    """Exact-repeat identity: the full stable content, count included."""
    admitted = _verified_repeat_admission(signal)
    if admitted is None:
        return None
    identity, labels, content = admitted
    try:
        return _serialize_key(signal, identity, labels, content)
    except (ValueError, TypeError):
        return None


def verified_repeat_update_key(signal):
    """Latest-wins identity for the repeat-with-update profile.

    Same admission gates, but count is excluded from the key material: a
    monotonic count increase on the same identity is an update to fold into
    the retained observation, not new information to deliver. Admission
    still requires an int count >= 1 so decreases and malformed counts
    fail open to delivery.
    """
    admitted = _verified_repeat_admission(signal, require_pod_kind=False)
    if admitted is None:
        return None
    identity, labels, content = admitted
    try:
        return _serialize_key(signal, identity, labels, content, exclude=('count',))
    except (ValueError, TypeError):
        return None
