"""Template-cached classification backend.

Deterministic reuse for repetitive signal sources: the first occurrence of
a template pays the wrapped backend's cost; repeats within the TTL inherit
the result. This is the no-training fast tier — it composes with any
wrapped classifier (label or fact-extraction) and lives in the engine, not
behind a model artifact.

Key design properties:
- Keyed on (signal_type, normalized message template, context keys,
  prompt revision) — the engine's own identity vocabulary plus a
  representation-level template normalization (digit/IP/hex stripping).
  This is not domain knowledge: it treats parameter variation as
  representation, the same convention the corpus adapters use.
- High/critical severities bypass the cache: volatile facts (recovery
  progression) and safety-critical signals deserve fresh extraction.
- Prompt-revision changes invalidate every entry — a prompt or few-shot
  update can never serve stale judgments.
- Fail-open: any wrapped-backend failure is returned uncached.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict

from ..classifier import ClassificationResult

_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_IPV4 = re.compile(r"\b\d+\.\d+\.\d+\.\d+\b")
_DIGITS = re.compile(r"\d+")


def normalize_template(message: str, level: str = "digits") -> str:
    """Collapse parameter variation to a stable template signature.

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


class TemplateCachedClassifierBackend:
    """Wraps any classifier backend with per-template result reuse."""

    def __init__(self, inner: Any, *,
                 context_keys: tuple = (),
                 ttl_seconds: float = 86400.0,
                 max_entries: int = 100_000,
                 normalize_level: str = "digits"):
        self._inner = inner
        self._context_keys = tuple(context_keys)
        self._normalize_level = normalize_level
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_bypassed = 0
        self.cache_stale_prompt = 0

    # Delegate backend attributes (url, models, prompts...) to the inner
    # backend so the factory, bridge and comparison recorder see through.
    def __getattr__(self, name):
        return getattr(self._inner, name)

    # NOTE: classification facts are template-intrinsic. Context-dependent
    # handling (the same template meaning different things in different
    # namespaces or sources) belongs to the contextual suppressors and the
    # promotion engine downstream — not to the fact cache. Context-scoped
    # caching remains available via context_keys for deployments that
    # want it, but the default shares facts across contexts.

    def _key(self, signal: Dict[str, Any], prompt_revision: str) -> str:
        content = signal.get("content") or {}
        message = str(content.get("message", ""))
        parts = [str(signal.get("signal_type", "")),
                 normalize_template(message, self._normalize_level),
                 prompt_revision]
        for k in self._context_keys:
            parts.append(str(signal.get(k, "")))
        return "\x1f".join(parts)

    def _prune_locked(self, now: float) -> None:
        if len(self._cache) < self._max:
            return
        expired = [k for k, (exp, _) in self._cache.items() if exp <= now]
        for k in expired:
            del self._cache[k]
        if len(self._cache) >= self._max:
            for k in sorted(self._cache, key=lambda k: self._cache[k][0])[: self._max // 10]:
                del self._cache[k]

    def classify(self, signal: Dict[str, Any], text: str,
                 client: Any = None) -> ClassificationResult:
        severity = signal.get("severity", "medium")
        prompt_revision = getattr(self._inner, "prompt_revision", "")
        now = time.monotonic()
        key = self._key(signal, prompt_revision)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                expires, cached = hit
                if expires > now and severity not in ("high", "critical"):
                    self.cache_hits += 1
                    return cached
                del self._cache[key]
        if severity in ("high", "critical"):
            self.cache_bypassed += 1
        else:
            self.cache_misses += 1
        result = self._inner.classify(signal, text, client)
        # Only cache successful, non-volatile-severity classifications.
        if result.ok and severity not in ("high", "critical"):
            with self._lock:
                self._prune_locked(now)
                self._cache[key] = (now + self._ttl, result)
        return result

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"template_cache_entries": len(self._cache),
                    "template_cache_hits": self.cache_hits,
                    "template_cache_misses": self.cache_misses,
                    "template_cache_bypassed": self.cache_bypassed,
                    "template_cache_ttl_seconds": self._ttl}
