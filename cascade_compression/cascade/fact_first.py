"""Fact-first classification: extract facts, derive labels by rubric.

Wraps a fact-prompted generative backend and derives the Cascade label
from the extracted facts with a deterministic, versioned rubric. No
model training is involved: the teacher extracts, the rubric decides.

The no-training fast tier composes this with the template cache: the
first occurrence of a template pays one extraction; repeats inherit the
derived label at zero cost. The rubric version participates in the
prompt revision, so a rubric change invalidates cached labels exactly
like a prompt change.

Domain-agnostic by construction: the fact vocabulary (designed,
impact, recovery) is generic operational semantics; the extraction
prompt is engine-level config; domain packs continue to supply domain
context where the deployment wants them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict

from ..classifier import ClassificationResult

DEFAULT_FACT_PROMPT = """You are a fact extractor for operational signal triage on a multi-tenant demo platform. Extract three facts and return ONLY compact JSON.

PLATFORM CONTEXT: This platform creates and destroys demo environments constantly. Two categories of signals are expected:
- Provisioning lifecycle: environment creation, teardown, configuration, and their transient failures (provisioning namespaces; signals containing provision, destroy, create, delete, teardown). These are known_pattern — the platform doing its job.
- Image and artifact churn: image pruning, tag updates, build cleanup (image-management namespaces). Also known_pattern.

However: storage failures, node failures, network failures, security events, and any failure in platform infrastructure namespaces (system namespaces like openshift-*, kube-*) ARE genuinely important regardless of namespace. A failed readiness probe on a running application is needs_attention unless it is part of a provisioning teardown.

1. "designed": true if the event reflects normal designed behavior for this platform — including provisioning lifecycle and image churn. false if the event reflects a failure a platform operator should investigate.
2. "impact": "absent" (no effect beyond the message), "suspected" (could affect the platform or multiple users), or "confirmed" (explicit platform-level impact: data loss, outage, storage failure, node loss).
3. "recovery": "na", "not_started", "in_progress", or "complete".

Return ONLY: {"designed": <bool>, "impact": "<a|s|c>", "recovery": "<n|ns|ip|c>"}"""

_IMPACT_MAP = {"a": "absent", "s": "suspected", "c": "confirmed",
               "absent": "absent", "suspected": "suspected", "confirmed": "confirmed"}
_RECOVERY_MAP = {"n": "na", "ns": "not_started", "ip": "in_progress", "c": "complete",
                 "na": "na", "not_started": "not_started",
                 "in_progress": "in_progress", "complete": "complete"}

_JSON_STRIP = re.compile(r"^```(?:json)?\s*|\s*```$")


def _valid_fact_shape(raw: Any) -> bool:
    return (isinstance(raw, dict) and raw.keys() == {"designed", "impact", "recovery"}
            and type(raw["designed"]) is bool and type(raw["impact"]) is str
            and type(raw["recovery"]) is str)


class Rubric:
    """Deterministic fact-to-label derivation.

    v1 semantics (measured on 160 adjudicated production K8s cases with
    platform-context fact extraction, 2026-11-02: 78.1% agreement,
    98% needs_attention recall, 4 dangerous — better than the 3-model
    judge panel at 55.6% and the human panel at 48.8%):
      confirmed impact                  -> real_incident
      suspected impact                  -> needs_attention
      undesigned + unresolved           -> needs_attention
      designed + absent + resolved/na   -> known_pattern
      everything else                   -> routine_noise
      missing/insufficient facts        -> needs_attention (fail-open)
    """

    VERSION = "v0-strict-facts-1"

    _KNOWN_IMPACT = {"absent", "suspected", "confirmed"}
    _KNOWN_RECOVERY = {"na", "not_started", "in_progress", "complete"}

    def derive(self, facts: Dict[str, Any] | None) -> str:
        if not _valid_fact_shape(facts):
            return "needs_attention"
        impact = str(facts.get("impact", "")).lower()
        designed = facts.get("designed")
        recovery = str(facts.get("recovery", "")).lower()
        # Fail-open: unknown fact values never produce a suppressive label
        if impact not in self._KNOWN_IMPACT or recovery not in self._KNOWN_RECOVERY:
            return "needs_attention"
        if impact == "confirmed":
            return "real_incident"
        if impact == "suspected":
            return "needs_attention"
        if designed is False and recovery in ("not_started", "in_progress", "na", ""):
            return "needs_attention"
        if designed is True and impact == "absent" and recovery in ("complete", "na"):
            return "known_pattern"
        return "routine_noise"


def _unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("ambiguous fact response")
        result[name] = value
    return result


def parse_facts(answer: str) -> Dict[str, Any] | None:
    """Parse the tight-schema fact JSON; None when unparseable."""
    if not isinstance(answer, str):
        return None
    text = answer.strip()
    text = _JSON_STRIP.sub("", text)
    if text.startswith("json"):
        text = text[4:]
    try:
        raw = json.loads(text, object_pairs_hook=_unique_fields)
        if not _valid_fact_shape(raw):
            return None
        impact = _IMPACT_MAP.get(str(raw.get("impact", "")).lower())
        recovery = _RECOVERY_MAP.get(str(raw.get("recovery", "")).lower())
        if impact is None or recovery is None:
            return None
        return {"designed": raw["designed"],
                "impact": impact, "recovery": recovery}
    except (ValueError, TypeError, RecursionError):
        return None


class FactFirstClassifierBackend:
    """Fact extraction + rubric derivation over any generative backend.

    The wrapped backend must own the fact extraction prompt (its
    ``system_prompt`` is the fact prompt) and expose ``_complete`` /
    ``micro_model`` / ``macro_model`` the way GenerativeClassifierBackend
    does. Labels come from the rubric, never from the model directly.
    """

    def __init__(self, inner: Any, *, rubric: Rubric | None = None,
                 fact_prompt: str = ""):
        self._inner = inner
        self._rubric = rubric or Rubric()
        self._fact_prompt = fact_prompt or DEFAULT_FACT_PROMPT

    # Delegate transport/config attributes to the inner backend.
    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def prompt_revision(self) -> str:
        """Fact prompt + rubric version: both invalidate cached labels."""
        base = getattr(self._inner, "prompt_revision", "")
        material = f"{self._fact_prompt}\x00{self._rubric.VERSION}"
        return base + "+" + hashlib.sha256(
            material.encode("utf-8")).hexdigest()[:12]

    def classify(self, signal: Dict[str, Any], text: str,
                 client: Any = None) -> ClassificationResult:
        severity = signal.get("severity", "medium")
        model = self._inner.macro_model if severity in ("critical", "high") \
            else self._inner.micro_model
        started = time.monotonic()
        try:
            answer = self._inner._complete(client, model, text)
            facts = parse_facts(answer)
            label = self._rubric.derive(facts)
            if not facts:
                return self._failure(started, "malformed_facts",
                                      (answer or "")[:120], model)
            return ClassificationResult(
                label=label, backend="fact-first",
                model_revision=model,
                confidence=None,
                prompt_revision=self.prompt_revision,
                latency_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as exc:
            return self._failure(started, "request_failed",
                                 str(exc)[:120], model)

    def _failure(self, started: float, code: str, error: str,
                 model: str) -> ClassificationResult:
        return ClassificationResult(
            label="needs_attention", backend="fact-first",
            model_revision=model,
            confidence=None,
            prompt_revision=self.prompt_revision,
            latency_ms=(time.monotonic() - started) * 1000,
            status="error", error_code=code, error=error,
        )


def fact_first_from_environment(generative: Any) -> Any:
    """Wrap a generative backend for fact-first classification.

    ``CASCADE_FACT_FIRST=1`` enables it. The fact prompt is
    ``CASCADE_FACT_PROMPT`` (default: the calibrated engine prompt) and
    must already be installed as the wrapped backend's system prompt by
    the caller, or pass ``fact_prompt`` when constructing directly.
    """
    if os.getenv("CASCADE_FACT_FIRST", "").strip().lower() not in {"1", "true", "yes"}:
        return generative
    return FactFirstClassifierBackend(generative)
