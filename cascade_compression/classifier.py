"""Pluggable survivor classification for the Cascade bridge.

llm-d-sc produces ranked semantic evidence.  This module deliberately keeps
the decision about which result is authoritative inside Cascade.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from uuid import uuid4

from .cascade.protocol import llm_complete

log = logging.getLogger(__name__)

CASCADE_LABELS = frozenset({
    "routine_noise", "known_pattern", "needs_attention", "real_incident",
})
SUPPRESSIVE_LABELS = frozenset({"routine_noise", "known_pattern"})
SERIALIZER_REVISION = "cascade-classifier-text-v2"
VALID_MODES = frozenset({"generative", "compare", "semantic", "hybrid"})

# Signal normalization patterns — collapse variable tokens to placeholders so
# near-duplicate signals become exact cache hits on the SC.  Order matters:
# UUID before generic hex, specific patterns before general numeric.
_NORM_PATTERNS = [
    # UUIDs: 8-4-4-4-12 hex
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<UUID>"),
    # K8s-style pod/replicaset hashes (trailing 5-10 alphanum after a dash)
    (re.compile(r"(?<=-)[a-z0-9]{5,10}(?=\s|$|[\.\-/])"), "<HASH>"),
    # IP addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b"), "<IP>"),
    # Hostnames with numbered suffixes (e.g., ocp-virt7-host12, node-03)
    (re.compile(r"\b[a-z][\w.-]*\d+[-.][\w.-]*\d+\b", re.I), "<HOST>"),
    # Numeric values (e.g., value=338.00, count=42, restarts: 5)
    (re.compile(r"(?<=[=:\s])\d+(?:\.\d+)?(?=\s|$|[,;])"), "<N>"),
]


def normalize_signal_text(text: str) -> str:
    """Collapse variable tokens so near-duplicate signals share a cache key."""
    for pattern, replacement in _NORM_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def serialize_signal(signal: Dict[str, Any], *, normalize: bool = True) -> str:
    """Return the versioned text projection used by every classifier backend.

    When *normalize* is True (default), variable tokens like UUIDs, pod hashes,
    and numeric values are collapsed to placeholders. This improves exact-cache
    reuse for repeated signal patterns without changing Cascade's decision policy.
    """
    raw = (
        f"{signal.get('signal_type', '')} {signal.get('severity', 'medium')}: "
        f"{signal.get('content', {}).get('message', '')} "
        f"namespace={signal.get('namespace', '')}"
    )
    return normalize_signal_text(raw) if normalize else raw


def normalize_label(value: str) -> str:
    """Extract exactly one Cascade label from a terse model response."""
    text = (value or "").strip().lower()
    exact = text.strip("`'\" .,:;\n\t")
    if exact in CASCADE_LABELS:
        return exact
    matches = {label for label in CASCADE_LABELS if re.search(
        rf"(?<![a-z_]){re.escape(label)}(?![a-z_])", text,
    )}
    return next(iter(matches)) if len(matches) == 1 else ""


@dataclass(frozen=True)
class RankedLabel:
    label: str
    score: float


@dataclass
class ClassificationResult:
    label: str = ""
    ranked: List[RankedLabel] = field(default_factory=list)
    confidence: Optional[float] = None
    margin: Optional[float] = None
    latency_ms: float = 0.0
    backend: str = ""
    model_revision: str = ""
    prompt_revision: str = ""
    tokenizer_revision: str = ""
    taxonomy_revision: str = ""
    classifier_id: str = ""
    status: str = "ok"
    error_code: str = ""
    error: str = ""
    authoritative_backend: str = ""
    fallback_used: bool = False
    alternatives: Dict[str, "ClassificationResult"] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.label in CASCADE_LABELS

    def public_dict(self, include_alternatives: bool = True) -> dict:
        data = asdict(self)
        data["ranked"] = [asdict(item) for item in self.ranked]
        if not include_alternatives:
            data.pop("alternatives", None)
        return data


@runtime_checkable
class ClassifierBackend(Protocol):
    name: str

    def classify(self, signal: Dict[str, Any], text: str, client: Any = None) -> ClassificationResult: ...


class GenerativeClassifierBackend:
    name = "generative"

    def __init__(self, *, url: str, key: str, micro_model: str,
                 macro_model: str, system_prompt: str):
        self.url = url
        self.key = key
        self.micro_model = micro_model
        self.macro_model = macro_model
        self.system_prompt = system_prompt
        self.prompt_revision = hashlib.sha256(
            system_prompt.encode("utf-8")
        ).hexdigest()

    def classify(self, signal: Dict[str, Any], text: str,
                 client: Any = None) -> ClassificationResult:
        severity = signal.get("severity", "medium")
        model = self.macro_model if severity in {"critical", "high"} else self.micro_model
        started = time.monotonic()
        try:
            answer = self._complete(client, model, text)
            label = normalize_label(answer)
            if not label:
                return self._failure(started, "malformed_label", answer[:120], model)
            return ClassificationResult(
                label=label, backend=self.name, model_revision=model,
                prompt_revision=self.prompt_revision,
                latency_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as primary_exc:
            if model != self.macro_model:
                try:
                    answer = self._complete(client, self.macro_model, text)
                    label = normalize_label(answer)
                    if label:
                        return ClassificationResult(
                            label=label, backend=self.name,
                            model_revision=self.macro_model,
                            prompt_revision=self.prompt_revision,
                            latency_ms=(time.monotonic() - started) * 1000,
                        )
                except Exception as fallback_exc:
                    return self._failure(
                        started, "request_failed",
                        f"micro: {primary_exc}; macro: {fallback_exc}",
                        self.macro_model,
                    )
            return self._failure(started, "request_failed", str(primary_exc), model)

    def _complete(self, client: Any, model: str, text: str) -> str:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": text},
        ]
        if client is not None:
            return llm_complete(client, self.url, self.key, model, messages)
        import httpx
        with httpx.Client(timeout=60) as owned_client:
            return llm_complete(owned_client, self.url, self.key, model, messages)

    def _failure(self, started: float, code: str, error: str,
                 model: str) -> ClassificationResult:
        return ClassificationResult(
            backend=self.name, model_revision=model,
            prompt_revision=self.prompt_revision, status="error",
            error_code=code, error=error,
            latency_ms=(time.monotonic() - started) * 1000,
        )


class SemanticClassifierBackend:
    """Persistent synchronous gRPC client for llm-d-sc."""

    name = "semantic"

    def __init__(self, *, address: str, signal_name: str,
                 deadline_seconds: float, expected_taxonomy_revision: str = "",
                 tls_enabled: bool = False, tls_ca_path: str = "",
                 tls_cert_path: str = "", tls_key_path: str = "",
                 server_name: str = "", allow_insecure_remote: bool = False,
                 stub: Any = None):
        self.address = address
        self.signal_name = signal_name
        self.deadline_seconds = deadline_seconds
        self.expected_taxonomy_revision = expected_taxonomy_revision
        self._channel = None
        self._stub = stub
        if self._stub is None:
            import grpc
            from .integrations.llm_d_sc import classify_pb2_grpc
            if tls_enabled:
                root_certificates = (
                    Path(tls_ca_path).read_bytes() if tls_ca_path else None
                )
                certificate_chain = (
                    Path(tls_cert_path).read_bytes() if tls_cert_path else None
                )
                private_key = (
                    Path(tls_key_path).read_bytes() if tls_key_path else None
                )
                if bool(certificate_chain) != bool(private_key):
                    raise ValueError(
                        "CASCADE_SC_TLS_CERT and CASCADE_SC_TLS_KEY must be set together"
                    )
                credentials = grpc.ssl_channel_credentials(
                    root_certificates=root_certificates,
                    private_key=private_key,
                    certificate_chain=certificate_chain,
                )
                options = (
                    (("grpc.ssl_target_name_override", server_name),)
                    if server_name else None
                )
                self._channel = grpc.secure_channel(
                    address, credentials, options=options
                )
            else:
                host = address.rsplit(":", 1)[0].strip("[]")
                try:
                    loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    loopback = host.casefold() == "localhost"
                if not loopback and not allow_insecure_remote:
                    raise ValueError(
                        "remote llm-d-sc requires TLS; set CASCADE_SC_TLS=1 "
                        "or explicitly set CASCADE_SC_ALLOW_INSECURE_REMOTE=1"
                    )
                self._channel = grpc.insecure_channel(address)
            self._stub = classify_pb2_grpc.ClassifyStub(self._channel)

    def classify(self, signal: Dict[str, Any], text: str,
                 client: Any = None,
                 context_completeness: str = "full") -> ClassificationResult:
        del client
        from .integrations.llm_d_sc import classify_pb2
        started = time.monotonic()
        request_id = str(signal.get("signal_id") or uuid4())
        cc_map = {"full": classify_pb2.FULL, "delta": classify_pb2.DELTA}
        cc_value = cc_map.get(context_completeness, classify_pb2.FULL)
        try:
            response = self._stub.Classify(
                classify_pb2.ClassifyRequest(
                    request_id=request_id,
                    session_id="",
                    context=text,
                    signals=[self.signal_name],
                    context_completeness=cc_value,
                ),
                timeout=self.deadline_seconds,
            )
        except Exception as exc:
            code = "request_failed"
            try:
                import grpc
                grpc_code = exc.code()
                if grpc_code == grpc.StatusCode.DEADLINE_EXCEEDED:
                    code = "timeout"
                elif grpc_code == grpc.StatusCode.RESOURCE_EXHAUSTED:
                    code = "overloaded"
                elif grpc_code == grpc.StatusCode.UNAVAILABLE:
                    code = "unavailable"
            except Exception:
                pass
            return self._failure(started, code, str(exc))

        revisions = dict(
            classifier_id=response.classifier_id,
            model_revision=response.model_revision,
            tokenizer_revision=response.tokenizer_revision,
            taxonomy_revision=response.taxonomy_revision,
        )
        if response.status != classify_pb2.OK:
            code = "abstain" if response.status == classify_pb2.ABSTAIN else "unavailable"
            return self._failure(started, code, f"llm-d-sc status={response.status}", **revisions)
        if (self.expected_taxonomy_revision
                and response.taxonomy_revision != self.expected_taxonomy_revision):
            return self._failure(
                started, "revision_mismatch",
                f"expected {self.expected_taxonomy_revision}, got {response.taxonomy_revision}",
                **revisions,
            )
        ranked = [RankedLabel(normalize_label(item.label), float(item.score))
                  for item in response.ranked]
        if (not ranked or any(not item.label for item in ranked)
                or {item.label for item in ranked} != CASCADE_LABELS):
            return self._failure(started, "malformed_labels", "invalid ranked label set", **revisions)
        margin = ranked[0].score - ranked[1].score if len(ranked) > 1 else None
        return ClassificationResult(
            label=ranked[0].label, ranked=ranked,
            confidence=margin, margin=margin, backend=self.name,
            latency_ms=(time.monotonic() - started) * 1000,
            **revisions,
        )

    def _failure(self, started: float, code: str, error: str,
                 **revisions: str) -> ClassificationResult:
        return ClassificationResult(
            backend=self.name, status="error", error_code=code, error=error[:240],
            latency_ms=(time.monotonic() - started) * 1000, **revisions,
        )


_SENSITIVE_KEYS = re.compile(r"token|password|secret|authorization|api[_-]?key", re.I)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_INLINE_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;&]+)"
)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+@")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)


def redact(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _INLINE_SECRET.sub(r"\1\2[REDACTED]", value)
        value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
        return _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
    return value


class ComparisonRecorder:
    """Bounded evidence buffer with optional locked JSONL persistence."""

    def __init__(self, max_events: int = 5000, output_file: str = "",
                 max_file_bytes: int = 64 * 1024 * 1024):
        self._events = deque(maxlen=max(1, max_events))
        self._output_file = output_file
        self._max_file_bytes = max(1024, max_file_bytes)
        self._lock = threading.Lock()
        self._latencies = {
            "generative": deque(maxlen=max(1, max_events)),
            "semantic": deque(maxlen=max(1, max_events)),
        }
        self._counts = {"inputs": 0, "comparisons": 0, "dual_comparisons": 0,
                        "agreements": 0,
                        "disagreements": 0, "fallbacks": 0, "sc_failures": 0,
                        "write_failures": 0}
        self._last_write_error = ""

    def record_input(self, signal: Dict[str, Any], survived_nano: bool,
                     nano_decisions: List[dict]) -> None:
        if not self._output_file:
            return
        event = self._base("input", signal)
        event.update({"survived_nano": survived_nano,
                      "nano_decisions": redact(nano_decisions)})
        self._record(event, "inputs", memory=False)

    def record_comparison(self, signal: Dict[str, Any], text: str,
                          result: ClassificationResult, mode: str) -> None:
        gen = result.alternatives.get("generative")
        semantic = result.alternatives.get("semantic")
        dual_comparison = bool(gen and semantic)
        agreement = bool(dual_comparison and gen.ok and semantic.ok
                         and gen.label == semantic.label)
        event = self._base("comparison", signal)
        event.update({
            "serializer_revision": SERIALIZER_REVISION,
            "input_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "mode": mode, "agreement": agreement,
            "authoritative_backend": result.authoritative_backend,
            "final_label": result.label,
            "fallback_used": result.fallback_used,
            "generative": gen.public_dict(False) if gen else None,
            "semantic": semantic.public_dict(False) if semantic else None,
        })
        with self._lock:
            self._counts["comparisons"] += 1
            if dual_comparison:
                self._counts["dual_comparisons"] += 1
                self._counts["agreements" if agreement else "disagreements"] += 1
            self._counts["fallbacks"] += int(result.fallback_used)
            self._counts["sc_failures"] += int(bool(semantic and not semantic.ok))
            if gen:
                self._latencies["generative"].append(gen.latency_ms)
            if semantic:
                self._latencies["semantic"].append(semantic.latency_ms)
            self._events.append(redact(event))
            self._append_locked(redact(event))

    def _base(self, kind: str, signal: Dict[str, Any]) -> dict:
        return {
            "schema_version": 1, "event_type": kind,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_id": str(signal.get("signal_id", "")),
            "signal_hash": hashlib.sha256(json.dumps(
                redact(signal), sort_keys=True, default=str,
            ).encode("utf-8")).hexdigest(),
            "signal": redact(signal),
        }

    def _record(self, event: dict, count_key: str, memory: bool = True) -> None:
        with self._lock:
            self._counts[count_key] += 1
            if memory:
                self._events.append(redact(event))
            self._append_locked(redact(event))

    def _append_locked(self, event: dict) -> None:
        if not self._output_file:
            return
        try:
            path = Path(self._output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(event, sort_keys=True) + "\n"
            if (path.exists()
                    and path.stat().st_size + len(encoded.encode("utf-8"))
                    > self._max_file_bytes):
                rotated = path.with_suffix(path.suffix + ".1")
                os.replace(path, rotated)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
            self._last_write_error = ""
        except OSError as exc:
            # Evidence is diagnostic. It must never take down signal ingestion.
            self._counts["write_failures"] += 1
            self._last_write_error = f"{type(exc).__name__}: {exc}"
            log.error("Classifier evidence persistence failed: %s", exc)

    def stats(self) -> dict:
        with self._lock:
            comparisons = self._counts["comparisons"]
            dual_comparisons = self._counts["dual_comparisons"]
            margins = [
                event.get("semantic", {}).get("margin")
                for event in self._events
                if event.get("event_type") == "comparison" and event.get("semantic")
            ]
            margins = [float(value) for value in margins if value is not None]
            return {
                **self._counts, "buffered": len(self._events),
                "capacity": self._events.maxlen,
                "persistence_healthy": not self._last_write_error,
                "last_write_error": self._last_write_error,
                "agreement_rate": round(
                    self._counts["agreements"] / dual_comparisons, 4
                ) if dual_comparisons else 0.0,
                "fallback_rate": round(
                    self._counts["fallbacks"] / comparisons, 4
                ) if comparisons else 0.0,
                "semantic_failure_rate": round(
                    self._counts["sc_failures"] / comparisons, 4
                ) if comparisons else 0.0,
                "margin_distribution": {
                    "lt_0_10": sum(value < .10 for value in margins),
                    "0_10_to_0_20": sum(.10 <= value < .20 for value in margins),
                    "0_20_to_0_40": sum(.20 <= value < .40 for value in margins),
                    "gte_0_40": sum(value >= .40 for value in margins),
                },
                "latency_ms": {
                    backend: self._percentiles(list(values))
                    for backend, values in self._latencies.items()
                },
            }

    @staticmethod
    def _percentiles(values: list) -> dict:
        if not values:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        values.sort()
        at = lambda pct: values[min(len(values) - 1, int((len(values) - 1) * pct))]
        return {"p50": round(at(.50), 3), "p95": round(at(.95), 3),
                "p99": round(at(.99), 3)}

    def events(self, limit: int = 100) -> list:
        with self._lock:
            return list(self._events)[-limit:]

    def public_events(self, limit: int = 100) -> list:
        """Return comparison evidence safe for the operational dashboard."""
        with self._lock:
            events = [event for event in self._events
                      if event.get("event_type") == "comparison"][-limit:]
            return [{
                "timestamp": event.get("timestamp"),
                "signal_id": event.get("signal_id"),
                "signal_hash": event.get("signal_hash"),
                "signal_type": event.get("signal", {}).get("signal_type", ""),
                "severity": event.get("signal", {}).get("severity", ""),
                "agreement": event.get("agreement", False),
                "final_label": event.get("final_label", ""),
                "authoritative_backend": event.get("authoritative_backend", ""),
                "fallback_used": event.get("fallback_used", False),
                "generative": self._dashboard_result(event.get("generative")),
                "semantic": self._dashboard_result(event.get("semantic")),
            } for event in events]

    @staticmethod
    def _dashboard_result(result: Optional[dict]) -> Optional[dict]:
        if not result:
            return None
        return {
            "label": result.get("label", ""),
            "margin": result.get("margin"),
            "latency_ms": result.get("latency_ms", 0),
            "status": result.get("status", "error"),
            "error_code": result.get("error_code", ""),
            "model_revision": result.get("model_revision", ""),
            "taxonomy_revision": result.get("taxonomy_revision", ""),
        }


class CascadeClassifier:
    """Selects classifier evidence while retaining policy authority in Cascade."""

    GCL_ESCALATION_THRESHOLD = int(os.environ.get("CASCADE_GCL_ESCALATION_THRESHOLD", "10"))
    """Minimum GCL FAILS on SC-authoritative before margin escalation kicks in."""

    GCL_ESCALATED_MARGIN = float(os.environ.get("CASCADE_GCL_ESCALATED_MARGIN", "0.50"))
    """Margin required for SC authority on signal types that accumulate GCL fails."""

    def __init__(self, generative: ClassifierBackend,
                 semantic: Optional[ClassifierBackend], *, mode: str,
                 hybrid_margin: float, recorder: ComparisonRecorder,
                 hybrid_suppress_margin: Optional[float] = None):
        self.generative = generative
        self.semantic = semantic
        self.mode = mode if mode in VALID_MODES else "generative"
        self.hybrid_margin = hybrid_margin
        self.hybrid_suppress_margin = hybrid_suppress_margin if hybrid_suppress_margin is not None else hybrid_margin
        self.recorder = recorder
        self.config_error = ""
        self._coverage = {"total": 0, "sc_authoritative": 0,
                          "generative_authoritative": 0,
                          "llm_fallback": 0, "sc_failure": 0,
                          "severity_gated": 0, "margin_gated": 0,
                          "both_suppressive_relaxed": 0,
                          "gcl_fails_total": 0, "gcl_fails_sc": 0,
                          "gcl_fails_llm": 0, "gcl_fails_unattributed": 0,
                          "gcl_escalated": 0}
        self._gcl_fail_types: Dict[str, int] = {}
        self._gcl_fail_sc_types: Dict[str, int] = {}
        self._coverage_lock = threading.Lock()
        if mode not in VALID_MODES:
            self.config_error = f"invalid classifier mode: {mode}"
        if self.mode != "generative" and semantic is None:
            self.config_error = self.config_error or "semantic classifier is not configured"
            self.mode = "generative"

    def classify(self, signal: Dict[str, Any], client: Any = None) -> ClassificationResult:
        text = serialize_signal(signal)
        if self.mode == "generative":
            gen = self.generative.classify(signal, text, client)
            gen.authoritative_backend = "generative"
            return gen

        assert self.semantic is not None
        semantic = self.semantic.classify(signal, text)
        alternatives = {"semantic": semantic}
        selected = semantic
        fallback = False
        fallback_reason = ""
        _both_suppressive = False
        if self.mode == "compare":
            gen = self.generative.classify(signal, text, client)
            alternatives["generative"] = gen
            selected = gen
        elif self.mode == "semantic":
            is_suppressive = semantic.label in SUPPRESSIVE_LABELS
            severity = signal.get("severity", "low")
            required_margin = (
                self.hybrid_suppress_margin if is_suppressive
                else self.hybrid_margin
            )
            severity_gate = is_suppressive and severity in ("high", "critical")
            semantic_accepted = (
                semantic.ok
                and semantic.margin is not None
                and semantic.margin >= required_margin
                and not severity_gate
            )
            fallback = not semantic_accepted
            if fallback:
                if not semantic.ok:
                    fallback_reason = "sc_failure"
                elif severity_gate:
                    fallback_reason = "severity_gated"
                else:
                    fallback_reason = "margin_gated"
                gen = self.generative.classify(signal, text, client)
                alternatives["generative"] = gen
                selected = gen
        elif self.mode == "hybrid":
            is_suppressive = semantic.label in {"routine_noise", "known_pattern"}
            severity = signal.get("severity", "low")
            severity_gate = is_suppressive and severity in ("high", "critical")
            required_margin = self.hybrid_suppress_margin if is_suppressive else self.hybrid_margin
            # When the top-2 labels are BOTH suppressive, the effective action
            # is the same regardless of which one wins.  Relax the margin
            # requirement to the normal (non-suppress) threshold — the signal
            # will be suppressed either way, so demanding the higher suppress
            # margin just wastes LLM capacity on a foregone conclusion.
            if is_suppressive and len(semantic.ranked) >= 2:
                runner_up = semantic.ranked[1].label if semantic.ranked[1].label != semantic.label else (
                    semantic.ranked[2].label if len(semantic.ranked) > 2 else ""
                )
                if runner_up in {"routine_noise", "known_pattern"}:
                    required_margin = self.hybrid_margin
                    _both_suppressive = True
            # GCL-driven margin escalation: only failures exactly correlated
            # to an SC-authoritative decision may reduce SC authority. A
            # generative or unattributed failure is not evidence against SC.
            _gcl_escalated = False
            signal_type = signal.get("signal_type", "")
            gcl_type_fails = self._gcl_fail_sc_types.get(signal_type, 0)
            if gcl_type_fails >= self.GCL_ESCALATION_THRESHOLD:
                required_margin = max(required_margin, self.GCL_ESCALATED_MARGIN)
                _gcl_escalated = True
            semantic_accepted = (semantic.ok and semantic.margin is not None
                                 and semantic.margin >= required_margin
                                 and not severity_gate)
            fallback = not semantic_accepted
            if fallback:
                gen = self.generative.classify(signal, text, client)
                alternatives["generative"] = gen
                selected = gen
                if not semantic.ok:
                    fallback_reason = "sc_failure"
                elif severity_gate:
                    fallback_reason = "severity_gated"
                elif _gcl_escalated:
                    fallback_reason = "gcl_escalated"
                else:
                    fallback_reason = "margin_gated"
        # Track classification coverage SLI.
        with self._coverage_lock:
            self._coverage["total"] += 1
            if selected.backend == "semantic":
                self._coverage["sc_authoritative"] += 1
                if self.mode == "hybrid" and _both_suppressive:
                    self._coverage["both_suppressive_relaxed"] += 1
            else:
                self._coverage["generative_authoritative"] += 1
            if fallback:
                self._coverage["llm_fallback"] += 1
                if fallback_reason:
                    self._coverage[fallback_reason] = self._coverage.get(fallback_reason, 0) + 1
                if fallback_reason == "gcl_escalated":
                    self._coverage["gcl_escalated"] += 1
        # compare deliberately leaves the incumbent authoritative.
        result = ClassificationResult(**{
            **selected.public_dict(False),
            "authoritative_backend": selected.backend,
            "fallback_used": fallback,
            "alternatives": alternatives,
        })
        result.ranked = selected.ranked
        self.recorder.record_comparison(signal, text, result, self.mode)
        return result

    def record_gcl_verdict(self, signal_type: str, verdict: str,
                           signal_id: str = "") -> None:
        """Record a GCL verdict and correlate to the authoritative backend.

        Called by the bridge when a GCL FAILS verdict arrives.  Looks up the
        signal_type in the comparison buffer to determine whether the SC or
        LLM was authoritative for recent signals of that type.
        """
        if verdict != "FAILS":
            return
        # Exact decision correlation is required for backend-specific accuracy.
        # A type-only match can identify a policy hotspot, but cannot establish
        # whether SC or LLM made the audited decision.
        authoritative = ""
        for event in reversed(list(self.recorder._events)):
            if (event.get("event_type") == "comparison" and signal_id
                    and event.get("signal_id") == str(signal_id)):
                authoritative = event.get("authoritative_backend", "")
                break

        with self._coverage_lock:
            self._coverage["gcl_fails_total"] += 1
            if authoritative == "semantic":
                self._coverage["gcl_fails_sc"] += 1
                self._gcl_fail_sc_types[signal_type] = self._gcl_fail_sc_types.get(signal_type, 0) + 1
            elif authoritative == "generative":
                self._coverage["gcl_fails_llm"] += 1
            else:
                self._coverage["gcl_fails_unattributed"] += 1
            self._gcl_fail_types[signal_type] = self._gcl_fail_types.get(signal_type, 0) + 1

    def coverage_stats(self) -> dict:
        """Classification coverage SLI — fail-open is invisible without this."""
        with self._coverage_lock:
            total = self._coverage["total"]
            sc = self._coverage["sc_authoritative"]
            gcl_total = self._coverage["gcl_fails_total"]
            return {
                **self._coverage,
                "sc_coverage_rate": round(sc / total, 4) if total else 0.0,
                "llm_fallback_rate": round(self._coverage["llm_fallback"] / total, 4) if total else 0.0,
                "sc_failure_rate": round(self._coverage["sc_failure"] / total, 4) if total else 0.0,
                "gcl_fail_rate": round(gcl_total / total, 4) if total else 0.0,
                "gcl_fail_types": dict(self._gcl_fail_types),
                "gcl_escalated_types": {
                    signal_type: count
                    for signal_type, count in self._gcl_fail_sc_types.items()
                    if count >= self.GCL_ESCALATION_THRESHOLD
                },
                "gcl_fail_sc_types": dict(self._gcl_fail_sc_types),
                "gcl_escalation_threshold": self.GCL_ESCALATION_THRESHOLD,
                "gcl_escalated_margin": self.GCL_ESCALATED_MARGIN,
            }


def load_taxonomy_metadata(path: str) -> tuple[str, str]:
    if not path:
        return "cascade_classification", ""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    labels = {normalize_label(label) for label in data.get("labels", [])}
    if labels != CASCADE_LABELS:
        raise ValueError("taxonomy labels must exactly match Cascade labels")
    lifecycle_status = str(data.get("lifecycle", {}).get("status", ""))
    if lifecycle_status and lifecycle_status != "approved":
        raise ValueError(
            f"taxonomy lifecycle status must be approved, got {lifecycle_status}"
        )
    return str(data.get("signal", "cascade_classification")), str(
        data.get("taxonomy_revision", "")
    )


def classifier_from_environment(*, url: str, key: str, micro_model: str,
                                macro_model: str, system_prompt: str) -> CascadeClassifier:
    # Preserve the original OSS behavior unless the operator explicitly opts
    # into semantic comparison or authority.
    mode = os.getenv("CASCADE_CLASSIFIER_MODE", "generative").strip().lower()
    recorder = ComparisonRecorder(
        int(os.getenv("CASCADE_CLASSIFIER_EVENTS_MAX", "5000")),
        os.getenv("CASCADE_CLASSIFIER_EVIDENCE_FILE", ""),
        int(os.getenv("CASCADE_CLASSIFIER_EVIDENCE_MAX_BYTES", str(64 * 1024 * 1024))),
    )
    generative = GenerativeClassifierBackend(
        url=url, key=key, micro_model=micro_model, macro_model=macro_model,
        system_prompt=system_prompt,
    )
    semantic = None
    config_error = ""
    address = os.getenv("CASCADE_SC_ADDRESS", "").strip()
    taxonomy_path = os.getenv("CASCADE_SC_TAXONOMY", "").strip()
    if mode != "generative" and address:
        try:
            signal_name, revision = load_taxonomy_metadata(taxonomy_path)
            semantic = SemanticClassifierBackend(
                address=address, signal_name=signal_name,
                deadline_seconds=float(os.getenv("CASCADE_SC_DEADLINE_SECONDS", "0.05")),
                expected_taxonomy_revision=revision,
                tls_enabled=os.getenv("CASCADE_SC_TLS", "").strip().lower()
                in {"1", "true", "yes"},
                tls_ca_path=os.getenv("CASCADE_SC_TLS_CA", "").strip(),
                tls_cert_path=os.getenv("CASCADE_SC_TLS_CERT", "").strip(),
                tls_key_path=os.getenv("CASCADE_SC_TLS_KEY", "").strip(),
                server_name=os.getenv("CASCADE_SC_TLS_SERVER_NAME", "").strip(),
                allow_insecure_remote=(
                    os.getenv("CASCADE_SC_ALLOW_INSECURE_REMOTE", "").strip().lower()
                    in {"1", "true", "yes"}
                ),
            )
        except Exception as exc:
            config_error = str(exc)
            log.warning("Semantic classifier disabled: %s", exc)
    hybrid_margin = float(os.getenv("CASCADE_SC_HYBRID_MARGIN", "0.20"))
    hybrid_suppress_margin = float(
        os.getenv("CASCADE_SC_HYBRID_SUPPRESS_MARGIN", "0.40")
    )
    classifier = CascadeClassifier(
        generative, semantic, mode=mode,
        hybrid_margin=hybrid_margin,
        hybrid_suppress_margin=hybrid_suppress_margin,
        recorder=recorder,
    )
    if mode != "generative" and not address:
        classifier.config_error = "CASCADE_SC_ADDRESS is required outside generative mode"
    elif config_error:
        classifier.config_error = config_error
    return classifier
