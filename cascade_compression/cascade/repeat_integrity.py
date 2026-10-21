"""Integrity admission for the opt-in bounded Pod Event repeat profile.

No payload-provided trust flag is accepted. The complete supplied message must
reproduce its digest. This binds bytes, not source authenticity or event truth.
Missing/excerpt-only evidence without a matching complete message fails open.
"""
import hashlib
import json

VOLATILE_FIELDS = frozenset({'timestamp', 'firstTimestamp', 'lastTimestamp', 'observed_at'})
PROTECTED = frozenset({'compliance', 'fraud', 'sanctions'})


def verified_repeat_key(signal):
    # Normalized medium alone is not evidence of a valid original severity.
    # Unknown/older signal object implementations without the marker fail open.
    if getattr(signal, 'severity_input_valid', False) is not True:
        return None
    content, labels = signal.content, signal.labels
    if not isinstance(content, dict) or not isinstance(labels, dict):
        return None
    if (labels.get('domain') != 'kubernetes' or labels.get('label') in PROTECTED
            or signal.severity != 'medium'
            or not isinstance(signal.signal_type, str) or not signal.signal_type.startswith('event_')
            or content.get('kind') != 'Pod'):
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
        computed = hashlib.sha256(message.encode('utf-8')).hexdigest()
        if claimed != computed:
            return None
        stable = {key: value for key, value in content.items() if key not in VOLATILE_FIELDS}
        serialized = json.dumps([signal.signal_type, signal.severity, identity, labels, stable],
                                sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                allow_nan=False)
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    except (UnicodeError, ValueError, TypeError):
        return None
