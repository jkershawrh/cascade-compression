"""Reuse a classifier result only for exact decision evidence."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import re
import threading
import time
from typing import Any, Dict

from ..classifier import ClassificationResult, SERIALIZER_REVISION
from ..outcome_evidence import _canonical_json
from .protocol import Signal, severity_input_is_valid

_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_IPV4 = re.compile(r"\b\d+\.\d+\.\d+\.\d+\b")
_DIGITS = re.compile(r"\d+")


def normalize_template(message: str, level: str = "digits") -> str:
    """Group messages for analysis. This signature cannot authorize result reuse.

    ``level`` is a deployment convention, not engine knowledge:
    - "digits": numbers/IPs/hex are parameters (templated event streams,
      robotics cells, structured logs)
    - "none": the message is used verbatim (streams where numbers are
      semantics — transaction amounts, measurements)
    """
    if level == "none":
        return message[:200]
    t = _HEX.sub("H", message)
    t = _IPV4.sub("IP", t)
    t = _DIGITS.sub("N", t)
    return t[:200]


# These fields belong to queue delivery, never to the producer's nested content.
_QUEUE_METADATA = frozenset({
    "signal_id", "_work_id", "_signal_ids", "_signal_ids_bytes", "_enqueued_at",
    "_queue_bytes", "_memory_ids", "_aggregation", "_qualification_bindings",
})


class AdmittedQueueEntry(dict):
    """A live bridge delivery. Its pre-projection fingerprint never enters JSON."""

    __slots__ = ("admission_key",)

    def __init__(self, signal: Signal):
        super().__init__(
            signal_type=signal.signal_type, severity=signal.severity,
            severity_input_valid=severity_input_is_valid(signal.severity, signal.severity_input_valid),
            source=signal.source, namespace=signal.namespace, cluster=signal.cluster,
            content=signal.content, labels=signal.labels,
        )
        # If a signal extension carries evidence the bridge cannot deliver,
        # even identical delivered fields cannot establish equivalence.
        self.admission_key = None
        if type(signal) is Signal and not (vars(signal).keys() - self.keys() - {"signal_id"}):
            self.admission_key = self._digest()

    def _digest(self) -> bytes | None:
        if (not severity_input_is_valid(self.get("severity"), self.get("severity_input_valid"))
                or self["severity"] in ("high", "critical")
                or not isinstance(self.get("content"), dict) or self["content"].get("_truncated")
                or not isinstance(self.get("labels"), dict)):
            return None
        evidence = {k: v for k, v in self.items() if k not in _QUEUE_METADATA}
        try:
            return hashlib.sha256(_canonical_json(evidence)).digest()
        except (TypeError, ValueError, RecursionError):
            return None

    def verified_key(self) -> bytes | None:
        """Recheck the actual delivery before folding work or reusing a result."""
        if self.admission_key is None or self.admission_key != self._digest():
            return None
        return self.admission_key


class TemplateCachedClassifierBackend:
    """Reuse exact inputs on backends declaring decision settings via cache_identity().

    A cacheable backend treats signal_id and bridge qualification bookkeeping
    as metadata. A configured context key can make even those fields significant.
    """

    def __init__(self, inner: Any, *,
                 context_keys: tuple = (),
                 ttl_seconds: float = 86400.0,
                 max_entries: int = 100_000,
                 normalize_level: str = "digits"):
        self._inner = inner
        # Keep both legacy normalization settings compatible with exact reuse.
        self._metadata = {"signal_id", "_qualification_bindings"}.difference(context_keys)
        self.queue_context_keys = _QUEUE_METADATA.intersection(context_keys)
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._cache: OrderedDict[bytes, tuple[float, ClassificationResult]] = OrderedDict()
        self._lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_bypassed = 0
        self.cache_stale_prompt = 0

    # Delegate backend attributes (url, models, prompts...) to the inner
    # backend so the factory, bridge and comparison recorder see through.
    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _key(self, signal: Dict[str, Any], text: str) -> bytes | None:
        severity = signal.get("severity")
        if (not severity_input_is_valid(severity, signal.get("severity_input_valid", True))
                or severity in ("high", "critical") or type(text) is not str):
            return None
        if isinstance(signal, AdmittedQueueEntry):
            admitted = signal.verified_key()
            if admitted is None:
                return None
            evidence = [admitted.hex(), {k: signal[k] for k in self.queue_context_keys if k in signal}]
        elif "_work_id" in signal or "_signal_ids" in signal:
            # Legacy, replayed, or caller-supplied delivery metadata has no witness.
            return None
        else:
            evidence = {k: v for k, v in signal.items() if k not in self._metadata}
        content = signal.get("content")
        if not isinstance(content, dict) or content.get("_truncated"):
            return None

        try:
            settings = self._inner.cache_identity()
        except Exception:
            # Third-party backends may lack a revision or be unable to resolve it.
            # Only key construction is optional; the actual classifier still runs.
            return None
        try:
            if not isinstance(settings, dict) or not settings:
                return None
            # The wrapper revision also binds fact extraction prompts and rubrics,
            # even when the transport identity is delegated to the inner backend.
            material = [SERIALIZER_REVISION, getattr(self._inner, "prompt_revision", ""),
                        settings, text, evidence]
            return hashlib.sha256(_canonical_json(material)).digest()
        except (TypeError, ValueError, RecursionError):
            # Lossy stringification (e.g. integer object keys) is unsafe here.
            return None

    def classify(self, signal: Dict[str, Any], text: str,
                 client: Any = None) -> ClassificationResult:
        started = time.monotonic()
        key = self._key(signal, text)
        with self._lock:
            if key is None or self._max <= 0:
                self.cache_bypassed += 1
            else:
                hit = self._cache.get(key)
                if hit is not None:
                    expires, cached = hit
                    if expires > time.monotonic():
                        result = deepcopy(cached)
                        result.cache_hit = True
                        result.latency_ms = (time.monotonic() - started) * 1000
                        self.cache_hits += 1
                        return result
                    del self._cache[key]
                self.cache_misses += 1

        result = self._inner.classify(signal, text, client)
        if (key is not None and self._max > 0 and result.ok and not result.cache_hit
                and not result.error_code and key == self._key(signal, text)):
            expires = started + self._ttl
            with self._lock:
                if expires > time.monotonic():
                    if key not in self._cache and len(self._cache) >= self._max:
                        self._cache.popitem(last=False)
                    self._cache[key] = (expires, deepcopy(result))
        return result

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"template_cache_entries": len(self._cache),
                    "template_cache_hits": self.cache_hits,
                    "template_cache_misses": self.cache_misses,
                    "template_cache_bypassed": self.cache_bypassed,
                    "template_cache_ttl_seconds": self._ttl}
