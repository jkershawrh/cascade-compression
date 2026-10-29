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

DEFAULT_FACT_PROMPT = """You are a fact extractor for operational signal triage. Extract three facts and return ONLY compact JSON.

1. "designed": true if the event reflects normal, DESIGNED system behavior (heartbeats, scheduled operations, informational updates, successful operations). false if it reflects a failure, error, or deviation from designed behavior - EVEN IF such failures are common. Common is not designed.
2. "impact": "absent" (no effect beyond the message itself), "suspected" (a failure occurred that COULD affect a workload, service, or user), or "confirmed" (the message EXPLICITLY STATES impact: data loss, outage, eviction of RUNNING workloads, dropped traffic, unavailable service). A failure merely occurring is NOT confirmed impact - if the message does not state the consequence, use "suspected".
3. "recovery": "na" (nothing to recover from), "not_started", "in_progress", or "complete" (the message states the condition resolved).

Return ONLY: {"designed": <bool>, "impact": "<a|s|c>", "recovery": "<n|ns|ip|c>"}"""

_IMPACT_MAP = {"a": "absent", "s": "suspected", "c": "confirmed",
               "absent": "absent", "suspected": "suspected", "confirmed": "confirmed"}
_RECOVERY_MAP = {"n": "na", "ns": "not_started", "ip": "in_progress", "c": "complete",
                 "na": "na", "not_started": "not_started",
                 "in_progress": "in_progress", "complete": "complete"}

_JSON_STRIP = re.compile(r"^```(?:json)?\s*|\s*```$")


class Rubric:
    """Deterministic fact-to-label derivation.

    v0 semantics (measured on 160 adjudicated production K8s cases:
    47.5% agreement, zero dangerous errors — all error mass in the
    over-attention direction):
      confirmed impact                  -> real_incident
      suspected impact                  -> needs_attention
      undesigned + unresolved           -> needs_attention
      designed + absent + resolved/na   -> known_pattern
      everything else                   -> routine_noise
      missing/insufficient facts        -> needs_attention (fail-open)
    """

    VERSION = "v0"

    _KNOWN_IMPACT = {"absent", "suspected", "confirmed"}
    _KNOWN_RECOVERY = {"na", "not_started", "in_progress", "complete"}

    def derive(self, facts: Dict[str, Any] | None) -> str:
        if not facts:
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


def parse_facts(answer: str) -> Dict[str, Any] | None:
    """Parse the tight-schema fact JSON; None when unparseable."""
    text = (answer or "").strip()
    text = _JSON_STRIP.sub("", text)
    if text.startswith("json"):
        text = text[4:]
    try:
        raw = json.loads(text)
        impact = _IMPACT_MAP.get(str(raw.get("impact", "")).lower())
        recovery = _RECOVERY_MAP.get(str(raw.get("recovery", "")).lower())
        if impact is None or recovery is None:
            return None
        return {"designed": bool(raw.get("designed", False)),
                "impact": impact, "recovery": recovery}
    except (json.JSONDecodeError, AttributeError, TypeError):
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
