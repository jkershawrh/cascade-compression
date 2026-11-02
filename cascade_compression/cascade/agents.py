"""Built-in cascade agents — generic patterns that work across domains.

Domain-specific agents (K8s pod health, Kafka lag, etc.) live in domain packs.
These agents handle universal signal patterns.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import math
from copy import deepcopy
from collections import defaultdict
from typing import Dict, List, Set

from .protocol import CascadeAgent, CascadeDecision, Outcome, Signal
from .repeat_integrity import verified_repeat_key, verified_repeat_update_key


class DeduplicateAgent:
    """Drops duplicate signals within a time window.

    Compliance-labeled signals bypass dedup — each instance is individually
    reportable regardless of content similarity. Location-bearing fields
    (location, span_id, circuit_id, node, host) are included in the hash
    so that distinct-origin signals are not collapsed.
    """

    name = "deduplicate"
    stage = 1

    DEDUP_BYPASS_LABELS = frozenset({"compliance", "fraud", "sanctions"})
    VOLATILE_EVIDENCE_FIELDS = frozenset({
        "timestamp", "firstTimestamp", "lastTimestamp", "observed_at",
    })

    def __init__(self, window_seconds: float = 60.0, *, require_verified_message: bool = False,
                 update_on_growth: bool = False):
        self._window = window_seconds
        if not math.isfinite(window_seconds) or window_seconds < 0:
            raise ValueError("dedup window must be finite and non-negative")
        self._require_verified_message = require_verified_message
        self._update_on_growth = update_on_growth
        self._seen: Dict[str, float] = {}
        self._retained: Dict[str, Signal] = {}
        # Per-domain dedup windows: signals from sources that re-observe the
        # same event (K8s polled snapshots) need long windows; signals from
        # streaming sources (AAP database rows) don't repeat and gain nothing.
        # Configured via CASCADE_DEDUP_WINDOW_<DOMAIN> env vars.
        import os
        self._domain_windows: Dict[str, float] = {}
        for env_key in os.environ:
            if (env_key.startswith("CASCADE_DEDUP_WINDOW_")
                    and env_key != "CASCADE_DEDUP_WINDOW_SECONDS"):
                domain = env_key[len("CASCADE_DEDUP_WINDOW_"):].lower()
                try:
                    window = float(os.environ[env_key])
                    if not math.isfinite(window) or window < 0:
                        raise ValueError("out of range")
                    self._domain_windows[domain] = window
                except ValueError as exc:
                    raise ValueError("domain dedup window must be finite and non-negative") from exc

    def _window_for(self, signal: Signal) -> float:
        """Select the dedup window for this signal's domain (or the default)."""
        domain = (signal.labels or {}).get("domain", "")
        if domain and domain in self._domain_windows:
            return self._domain_windows[domain]
        return self._window

    def _process_update(self, s: Signal, batch_objects) -> Optional[CascadeDecision]:
        """Latest-wins verified repeat: fold count growth into the retained
        record instead of re-delivering it. Decreases and other changes
        deliver (fail-open); every fold-in is auditable in the evidence."""
        key = verified_repeat_update_key(s)
        if key is None:
            return None
        retained = self._retained.get(key)
        if retained is None:
            self._retained[key] = s
            self._seen[key] = time.monotonic()
            return None
        # Check expiry against this signal's domain-specific window
        window = self._window_for(s)
        last_seen = self._seen.get(key, 0)
        if time.monotonic() - last_seen >= window:
            # Window expired for this domain — treat as new
            self._retained[key] = s
            self._seen[key] = time.monotonic()
            return None
        old_count = retained.content.get('count')
        new_count = s.content.get('count')
        if (isinstance(old_count, int) and isinstance(new_count, int)
                and new_count > old_count):
            if id(retained) not in batch_objects:
                # Previously acknowledged deliveries are immutable. A new
                # count must reach the consumer and archive as new evidence.
                self._retained[key] = s
                self._seen[key] = time.monotonic()
                return None
            retained.content['count'] = new_count
            for field in ("timestamp", "lastTimestamp", "observed_at"):
                if isinstance(s.content.get(field), str):
                    retained.content[field] = s.content[field]
            return CascadeDecision(
                signal_id=s.signal_id, agent_name=self.name,
                outcome=Outcome.DEDUPE,
                evidence=f'verified repeat, count {old_count} -> {new_count}')
        if new_count == old_count:
            return CascadeDecision(
                signal_id=s.signal_id, agent_name=self.name,
                outcome=Outcome.DEDUPE,
                evidence=f'verified exact duplicate of {key[:8]}')
        # Non-monotonic count: suspect, deliver.
        self._retained[key] = s
        self._seen[key] = time.monotonic()
        return None

    def _process_verified(self, s: Signal, now: float) -> Optional[CascadeDecision]:
        key = verified_repeat_key(s)
        if key is None:
            return None
        if key in self._seen:
            return CascadeDecision(
                signal_id=s.signal_id, agent_name=self.name,
                outcome=Outcome.DEDUPE, evidence=f'verified exact duplicate of {key[:8]}')
        self._seen[key] = now
        return None

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        now = time.monotonic()
        max_window = max([self._window, *self._domain_windows.values()])
        self._seen = {k: v for k, v in self._seen.items() if now - v < max_window}
        if self._update_on_growth:
            self._retained = {k: s for k, s in self._retained.items() if k in self._seen}

        decisions = []
        batch_objects = {id(s) for s in signals}
        for s in signals:
            if self._update_on_growth:
                decision = self._process_update(s, batch_objects)
                if decision is not None:
                    decisions.append(decision)
                continue
            if self._require_verified_message:
                decision = self._process_verified(s, now)
                if decision is not None:
                    decisions.append(decision)
                continue
            if self._is_compliance(s):
                continue

            stable_content = {
                key: value for key, value in (s.content or {}).items()
                if key not in self.VOLATILE_EVIDENCE_FIELDS
            }
            key_data = json.dumps(
                [(s.labels or {}).get("domain", ""), s.cluster, s.signal_type,
                 s.source, s.namespace, s.severity, stable_content],
                sort_keys=True, separators=(",", ":"), default=str,
            )
            key = hashlib.sha256(key_data.encode()).hexdigest()
            if key in self._seen:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DEDUPE, evidence=f"duplicate of {key[:8]}",
                ))
            else:
                self._seen[key] = now
        if self._update_on_growth:
            # Freeze only records touched by this batch. A caller may reuse or
            # mutate its Signal after delivery, including its observation UUID.
            for key, retained in list(self._retained.items()):
                if id(retained) in batch_objects:
                    self._retained[key] = deepcopy(retained)
        return decisions

    def _is_compliance(self, s: Signal) -> bool:
        label = s.labels.get("label", "") if s.labels else ""
        return label in self.DEDUP_BYPASS_LABELS

class TransientSuppressor:
    """Suppresses transient signals that resolve within a window.

    Fail-open: signals with critical keywords in content are never suppressed,
    even if the signal type and severity would normally be transient.
    """

    name = "transient_suppressor"
    stage = 1

    TRANSIENT_TYPES = {"pod_restart", "container_backoff", "probe_failure", "dns_timeout"}
    NEVER_SUPPRESS = re.compile(
        r"(fatal|critical|oomkill|segfault|panic|breach|unauthorized|data.?loss|corruption)",
        re.I,
    )

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            if s.signal_type in self.TRANSIENT_TYPES and s.severity in ("info", "low"):
                content_str = str(s.content) if s.content else ""
                if self.NEVER_SUPPRESS.search(content_str):
                    continue
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.SUPPRESS, evidence=f"transient {s.signal_type}",
                ))
        return decisions


class SeverityGate:
    """Drops info-severity signals unless they match escalation patterns.

    Compliance/fraud-labeled signals bypass the gate — regulatory events
    at any severity must reach the LLM for classification.
    """

    name = "severity_gate"
    stage = 1

    GATE_BYPASS_LABELS = frozenset({"compliance", "fraud", "sanctions"})

    ESCALATE_PATTERNS = [
        re.compile(r"(oomkill|segfault|panic|fatal|critical)", re.I),
        re.compile(r"(security|breach|unauthorized|injection)", re.I),
        re.compile(r"(data.?loss|corruption|integrity)", re.I),
    ]

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            label = s.labels.get("label", "") if s.labels else ""
            if label in self.GATE_BYPASS_LABELS:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE, evidence=f"protected {label} event",
                ))
                continue
            if s.severity not in ("info", "low"):
                continue

            content_str = str(s.content)
            escalate = any(p.search(content_str) for p in self.ESCALATE_PATTERNS)

            if escalate:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE, evidence="critical pattern in content",
                ))
            elif s.severity == "info":
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DROP, evidence="info severity, no escalation pattern",
                ))
        return decisions


class SafetyEscalator(SeverityGate):
    """Apply protected-label and critical-content checks before any removal.

    Reuse the severity gate's policy, but emit escalation decisions only. The
    ordinary gate can drop unprotected info traffic later, after numeric checks.
    """

    name = "safety_escalator"
    stage = 0

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        return [decision for decision in super().process(signals)
                if decision.outcome == Outcome.ESCALATE]


class PatternClassifier:
    """Classifies signals by regex patterns — handles known failure signatures."""

    name = "pattern_classifier"
    stage = 2

    PATTERNS = {
        "oom": (re.compile(r"(oomkill|out.of.memory|memory.limit)", re.I), "memory_exhaustion"),
        "disk": (re.compile(r"(disk.full|no.space|filesystem.full|disk.pressure)", re.I), "disk_pressure"),
        "cpu": (re.compile(r"(cpu.throttl|cpu.pressure|cpu.limit)", re.I), "cpu_pressure"),
        "network": (re.compile(r"(connection.refused|timeout|unreachable|dns.fail)", re.I), "network_failure"),
        "crash": (re.compile(r"(crashloop|segfault|panic|core.dump)", re.I), "application_crash"),
        "auth": (re.compile(r"(unauthorized|forbidden|403|401|auth.fail)", re.I), "auth_failure"),
        "scaling": (re.compile(r"(hpa.max|replica.limit|insufficient.cpu|pending.pod)", re.I), "scaling_limit"),
    }

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            content_str = str(s.content)
            for pattern_name, (pattern, classification) in self.PATTERNS.items():
                if pattern.search(content_str):
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY,
                        classification=classification,
                        evidence=f"matched {pattern_name} pattern",
                        confidence=0.85,
                    ))
                    break
        return decisions


class ThresholdClassifier:
    """Check every critical measurement before warnings or suppression."""

    name = "threshold_classifier"
    stage = 0

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for signal in signals:
            measurements = []
            for name in ("cpu", "memory", "disk"):
                value = signal.content.get(f"{name}_percent")
                if value is None:
                    value = signal.content.get(f"{name}_usage")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    measurements.append((name, value))

            # A CPU warning must not mask critical memory or disk pressure.
            for threshold, outcome, suffix, confidence in (
                (95, Outcome.ESCALATE, "critical", 0.95),
                (80, Outcome.CLASSIFY, "warning", 0.90),
            ):
                matched = next(((name, value) for name, value in measurements
                                if value > threshold), None)
                if matched is not None:
                    name, value = matched
                    decisions.append(CascadeDecision(
                        signal_id=signal.signal_id, agent_name=self.name,
                        outcome=outcome, classification=f"{name}_{suffix}",
                        evidence=f"{name}={value}%", confidence=confidence,
                    ))
                    break
        return decisions


class ValueRuleAgent:
    """Deterministic threshold rules over structured numeric content fields.

    Domain packs declare VALUE_RULES: a versioned, declarative spec of
    derived quantities (expression trees over content fields) and threshold
    rules (ANDed conditions, first match wins — rule order is priority).
    The agent evaluates whatever spec it is given; it carries no domain
    knowledge of its own.

    Safety: rules may only ESCALATE or CLASSIFY, never suppress — the same
    fail-open guarantee as TrendDetector and SafetyEscalator. Signals that
    lack the referenced fields are untouched, so event-type signals flowing
    through the same cascade are unaffected (the hybrid-lane pattern:
    telemetry is classified by spec, events by semantics).

    Cost: zero model calls — a nano-tier agent (stage 0).

    Spec format (validated at construction; malformed specs fail fast)::

        {
          "revision": "pack-value-rules-v1",
          "derived": {                      # optional
            "temp_diff_k": ["sub", ["field", "process_temp_k"],
                                     ["field", "air_temp_k"]],
            "power_w": ["mul", ["field", "torque_nm"],
                               ["field", "rotational_speed_rpm"],
                               ["const", 0.10471975511965977]],
          },
          "rules": [
            {"name": "heat_dissipation_risk",
             "classification": "heat_dissipation_failure_risk",
             "outcome": "escalate",          # or "classify"
             "confidence": 0.95,
             "when": [                       # ANDed; first match wins
               {"field": "temp_diff_k", "op": "lt", "value": 8.6},
               {"field": "rotational_speed_rpm", "op": "lt", "value": 1380},
             ]},
          ],
        }

    Ops: lt/le/gt/ge (numeric), between/outside (``value: [lo, hi]``),
    in/not_in (membership), eq/ne (categorical or numeric). Numeric ops on
    a missing or non-numeric field simply fail to match.

    Rules may declare a ``label`` — the cascade taxonomy label
    (routine_noise/known_pattern/needs_attention/real_incident) the bridge
    applies authoritatively when the rule matches, skipping the model call
    (the same authority discipline as the qualified sc-head: deterministic,
    versioned, and backed by held-out evidence). Rules without a label
    escalate only and leave the final label to the classifier lane.

    The spec may also declare ``covers``: {signal_type: else_label}. A
    signal of a covered type that carries the referenced numeric fields
    (the spec actually applied) but matches NO rule takes the else label
    authoritatively — the spec-sheet semantics: within spec is healthy.
    The else path never applies to critical/high severity (the severity
    gate wins) and requires field presence, so malformed or event-like
    signals of a covered type still fall to the semantic lane.

    Backstop honesty: the semantic lane CANNOT validate numeric telemetry
    (measured: 0% clean-input recall on AI4I; 100% over-escalation on
    healthy sensor windows) — an LLM shadow re-check would disagree with
    correct else-labels and is therefore NOT used as a demotion trigger.
    The else path's authority is the versioned spec plus its held-out
    corpus evidence, monitored through the else-rate (a collapse means
    new signatures outside the spec) and withdrawable via
    CASCADE_VALUE_RULES=off.
    """

    name = "value_rule"
    stage = 0

    _NUMERIC_OPS = frozenset({"lt", "le", "gt", "ge"})
    _RANGE_OPS = frozenset({"between", "outside"})
    _MEMBERSHIP_OPS = frozenset({"in", "not_in"})
    _EXPRESSION_OPS = frozenset({"add", "sub", "mul", "div"})
    _ALLOWED_OUTCOMES = ("escalate", "classify")

    def __init__(self, spec: dict):
        revision = spec.get("revision")
        if not isinstance(revision, str) or not revision:
            raise ValueError("value-rule spec must declare a non-empty revision")
        rules = spec.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("value-rule spec must declare a non-empty rules list")
        derived = spec.get("derived", {})
        if not isinstance(derived, dict):
            raise ValueError("value-rule spec derived must be a mapping")
        covers = spec.get("covers", {})
        if not isinstance(covers, dict):
            raise ValueError("value-rule spec covers must be a mapping")
        self._covers: Dict[str, dict] = {}
        for type_name, value in covers.items():
            if not isinstance(type_name, str) or not type_name:
                raise ValueError("value-rule covers keys must be non-empty strings")
            if isinstance(value, str):
                # Shorthand: label only; the spec must evaluate every
                # referenced field (single-type specs).
                if not value:
                    raise ValueError(
                        f"value-rule covers[{type_name!r}] label must be non-empty")
                self._covers[type_name] = {"label": value,
                                           "requires": None}
            elif isinstance(value, dict):
                label = value.get("label")
                requires = value.get("requires")
                if not isinstance(label, str) or not label:
                    raise ValueError(
                        f"value-rule covers[{type_name!r}] must declare a label")
                if not isinstance(requires, list) or not requires or not all(
                        isinstance(f, str) and f for f in requires):
                    raise ValueError(
                        f"value-rule covers[{type_name!r}] requires must be a "
                        "non-empty list of field names")
                self._covers[type_name] = {"label": label,
                                           "requires": list(requires)}
            else:
                raise ValueError(
                    f"value-rule covers[{type_name!r}] must be a label string "
                    "or {{label, requires}}")
        self.revision = revision
        self.covers = {k: v["label"] for k, v in self._covers.items()}
        self._derived = {name: self._validate_expression(name, expr)
                         for name, expr in derived.items()}
        self._rules = [self._validate_rule(rule) for rule in rules]
        self.fires: Dict[str, int] = defaultdict(int)
        self._referenced_fields: Set[str] = set()
        for _, (_, operands) in self._derived.items():
            for kind, value in operands:
                if kind == "field":
                    self._referenced_fields.add(value)
        for rule in self._rules:
            for cond in rule["conditions"]:
                self._referenced_fields.add(cond["field"])

    # ------------------------------------------------------------------
    # Spec validation — fail fast at construction, not per signal.
    # ------------------------------------------------------------------
    @classmethod
    def _validate_expression(cls, name: str, expr):
        if not isinstance(expr, (list, tuple)) or len(expr) < 3:
            raise ValueError(
                f"derived {name!r} must be [op, operand, operand, ...]")
        op = expr[0]
        if op not in cls._EXPRESSION_OPS:
            raise ValueError(f"derived {name!r} uses unknown operator {op!r}")
        operands = []
        for operand in expr[1:]:
            if not isinstance(operand, (list, tuple)) or len(operand) != 2:
                raise ValueError(
                    f"derived {name!r} operands must be ['field', name] or ['const', value]")
            kind, value = operand
            if kind == "field":
                if not isinstance(value, str) or not value:
                    raise ValueError(
                        f"derived {name!r} field reference must be a non-empty string")
                operands.append(("field", value))
            elif kind == "const":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"derived {name!r} constant must be numeric")
                operands.append(("const", float(value)))
            else:
                raise ValueError(
                    f"derived {name!r} operand kind must be 'field' or 'const', got {kind!r}")
        if op in ("sub", "div") and len(operands) != 2:
            raise ValueError(f"derived {name!r} {op} requires exactly two operands")
        if len(operands) < 2:
            raise ValueError(f"derived {name!r} requires at least two operands")
        return op, operands

    @classmethod
    def _validate_rule(cls, rule) -> dict:
        if not isinstance(rule, dict):
            raise ValueError("value rules must be mappings")
        name = rule.get("name")
        classification = rule.get("classification")
        outcome = rule.get("outcome", "escalate")
        confidence = rule.get("confidence", 0.9)
        when = rule.get("when")
        label = rule.get("label")
        if label is not None and (not isinstance(label, str) or not label):
            raise ValueError(f"value rule {name!r} label must be a non-empty string")
        if not isinstance(name, str) or not name:
            raise ValueError("value rule must declare a non-empty name")
        if not isinstance(classification, str) or not classification:
            raise ValueError(f"value rule {name!r} must declare a classification")
        if outcome not in cls._ALLOWED_OUTCOMES:
            raise ValueError(
                f"value rule {name!r} outcome must be one of {cls._ALLOWED_OUTCOMES}")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not 0.0 <= float(confidence) <= 1.0):
            raise ValueError(f"value rule {name!r} confidence must be within [0, 1]")
        if not isinstance(when, list) or not when:
            raise ValueError(f"value rule {name!r} must declare a non-empty 'when' list")
        return {
            "name": name,
            "classification": classification,
            "outcome": outcome,
            "confidence": float(confidence),
            "label": label,
            "conditions": [cls._validate_condition(name, c) for c in when],
        }

    @classmethod
    def _validate_condition(cls, rule_name: str, cond) -> dict:
        if not isinstance(cond, dict):
            raise ValueError(f"value rule {rule_name!r} conditions must be mappings")
        field = cond.get("field")
        op = cond.get("op")
        value = cond.get("value")
        if not isinstance(field, str) or not field:
            raise ValueError(f"value rule {rule_name!r} condition must reference a field")
        if op in cls._NUMERIC_OPS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"value rule {rule_name!r} op {op!r} needs a numeric value")
            return {"field": field, "op": op, "value": float(value)}
        if op in cls._RANGE_OPS:
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(
                    f"value rule {rule_name!r} op {op!r} needs a two-element [lo, hi] value")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
                raise ValueError(f"value rule {rule_name!r} range bounds must be numeric")
            return {"field": field, "op": op,
                    "value": (float(value[0]), float(value[1]))}
        if op in cls._MEMBERSHIP_OPS:
            if not isinstance(value, (list, tuple)) or not value:
                raise ValueError(f"value rule {rule_name!r} op {op!r} needs a non-empty list")
            return {"field": field, "op": op, "value": tuple(value)}
        if op in ("eq", "ne"):
            if value is None:
                raise ValueError(f"value rule {rule_name!r} op {op!r} needs a value")
            return {"field": field, "op": op, "value": value}
        raise ValueError(f"value rule {rule_name!r} uses unknown op {op!r}")

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def _resolve_numeric_fields(self, signal: Signal) -> Dict[str, float]:
        content = signal.content or {}
        resolved: Dict[str, float] = {}
        for field in self._referenced_fields:
            value = content.get(field)
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value)):
                resolved[field] = float(value)
        return resolved

    def _evaluate_expression(self, expr, fields: Dict[str, float]):
        op, operands = expr
        values = []
        for kind, value in operands:
            if kind == "const":
                values.append(value)
            elif value in fields:
                values.append(fields[value])
            else:
                return None  # a referenced input is missing or non-numeric
        if op == "add":
            return float(sum(values))
        if op == "sub":
            return values[0] - values[1]
        if op == "mul":
            result = 1.0
            for v in values:
                result *= v
            return result
        if values[1] == 0:  # div
            return None
        return values[0] / values[1]

    @staticmethod
    def _describe(cond: dict, context: Dict[str, float], raw: dict) -> str:
        field, op, value = cond["field"], cond["op"], cond["value"]
        if op in ("eq", "ne", "in", "not_in"):
            return f"{field}={raw.get(field)!r} {op} {value!r}"
        actual = context[field]
        if op in ("lt", "le", "gt", "ge"):
            return f"{field}={actual:.4g} {op} {value:g}"
        return f"{field}={actual:.4g} {op} [{value[0]:g}, {value[1]:g}]"

    def _condition_holds(self, cond: dict, context: Dict[str, float], raw: dict) -> bool:
        field, op, value = cond["field"], cond["op"], cond["value"]
        if op in ("eq", "ne"):
            actual = raw.get(field)
            if actual is None:
                return False
            return (actual == value) if op == "eq" else (actual != value)
        if op in self._MEMBERSHIP_OPS:
            actual = raw.get(field)
            if actual is None:
                return False
            return (actual in value) if op == "in" else (actual not in value)
        if field not in context:
            return False
        actual = context[field]
        if op in ("lt", "le", "gt", "ge"):
            return {"lt": actual < value, "le": actual <= value,
                    "gt": actual > value, "ge": actual >= value}[op]
        lo, hi = value
        if op == "between":
            return lo <= actual <= hi
        return actual < lo or actual > hi

    def evaluate(self, signal: Signal):
        """Return the first matching rule (dict) for a signal, or None."""
        raw = signal.content or {}
        context = self._resolve_numeric_fields(signal)
        for name, expr in self._derived.items():
            result = self._evaluate_expression(expr, context)
            if result is not None and math.isfinite(result):
                context[name] = result
        for rule in self._rules:
            if all(self._condition_holds(c, context, raw) for c in rule["conditions"]):
                return rule, context
        return None

    def spec_applied(self, signal: Signal) -> bool:
        """Did this signal carry the fields the spec evaluates for its type?

        A covered signal type without its required numeric fields was
        never actually evaluated by the spec — it must not take an else
        label and falls to the semantic lane instead.
        """
        entry = self._covers.get(signal.signal_type)
        if entry is None:
            return False
        required = entry["requires"]
        if required is None:
            required = sorted(self._referenced_fields)
            if not required:
                return False
        content = signal.content or {}
        for field in required:
            value = content.get(field)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value)):
                return False
        return True

    def else_label(self, signal: Signal):
        """The healthy-by-spec label for a covered, unmatched signal.

        Returns the else entry (label/rule/classification/revision) when
        the signal's type is covered, the spec's fields were present,
        and no severity gate protects it; otherwise None. Callers must
        have already established that no rule matched (evaluate -> None).
        """
        if signal.signal_type not in self._covers:
            return None
        if signal.severity in ("critical", "high"):
            return None  # the severity gate wins over suppressive labels
        if not self.spec_applied(signal):
            return None
        return {"label": self._covers[signal.signal_type]["label"],
                "rule": f"else:{signal.signal_type}",
                "classification": "spec_healthy",
                "confidence": 0.9,
                "revision": self.revision}

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for signal in signals:
            match = self.evaluate(signal)
            if match is None:
                continue
            rule, context = match
            raw = signal.content or {}
            evidence = "; ".join(
                self._describe(c, context, raw) for c in rule["conditions"])
            self.fires[rule["name"]] += 1
            decisions.append(CascadeDecision(
                signal_id=signal.signal_id,
                agent_name=self.name,
                outcome=Outcome.ESCALATE if rule["outcome"] == "escalate"
                else Outcome.CLASSIFY,
                classification=rule["classification"],
                evidence=f"{rule['name']}: {evidence} [spec {self.revision}]",
                confidence=rule["confidence"],
                metadata={"value_rule": rule["name"], "spec_revision": self.revision,
                          **({"label": rule["label"]} if rule["label"] else {})},
            ))
        return decisions


class TrendDetector:
    """Detects monotonic trends across sequential signals from the same entity.

    Runs at stage 0 — before severity gate and dedup. When a trend is detected
    (e.g. 4 declining vital signs), the latest signal is ESCALATED so it
    survives the severity gate even at info severity.

    Domain-agnostic: entity key and trend fields are discovered from the signal's
    labels and content. Any numeric field that moves monotonically over
    min_readings consecutive signals from the same entity triggers escalation.

    Safety: escalate-only, never suppresses. Same fail-open guarantee as
    PrimingEscalator.
    """

    name = "trend_detector"
    stage = 0

    ENTITY_KEYS = ("patient_id", "account_id", "device_id", "node",
                   "host", "circuit_id", "instance_id", "instance")
    IGNORE_FIELDS = frozenset({
        "message", "raw", "description", "name", "type",
        "reading_number", "sequence", "burst_id", "total",
    })
    TREND_FIELDS = re.compile(
        r"(heart_rate|bp_|spo2|temp|respiratory|pulse|"
        r"cpu_|memory_|disk_|latency|error_|fail_|"
        r"velocity_|restart|queue_|saturation|utilization|"
        r"packet_loss|jitter|signal_strength|consecutive_|"
        r"load_|iops|throughput|response_time)",
        re.I,
    )

    def __init__(self, min_readings: int = 3, window_seconds: float = 3600):
        self._min_readings = min_readings
        self._window = window_seconds
        self._history: Dict[str, List[dict]] = defaultdict(list)
        self._last_cleanup = 0.0

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        now = time.monotonic()
        if now - self._last_cleanup > 300:
            self._cleanup(now)
            self._last_cleanup = now

        decisions = []
        for s in signals:
            entity = self._entity_key(s)
            if not entity:
                continue

            numerics = self._extract_numerics(s)
            if not numerics:
                continue

            self._history[entity].append({"ts": now, "values": numerics, "signal_id": s.signal_id})

            history = self._history[entity]
            if len(history) < self._min_readings:
                continue

            recent = history[-self._min_readings:]
            osc_window = history[-max(self._min_readings + 1, 4):]
            trends = self._detect_trends(recent, osc_window)
            if trends:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE,
                    evidence=f"trend detected: {', '.join(trends)}",
                    confidence=0.85,
                ))
        return decisions

    def _entity_key(self, s: Signal) -> str:
        if s.labels:
            for key in self.ENTITY_KEYS:
                val = s.labels.get(key)
                if val:
                    return f"{key}={val}"
        if s.content:
            for key in self.ENTITY_KEYS:
                val = s.content.get(key)
                if val:
                    return f"{key}={val}"
        return ""

    def _extract_numerics(self, s: Signal) -> Dict[str, float]:
        if not s.content:
            return {}
        result = {}
        for k, v in s.content.items():
            if k in self.IGNORE_FIELDS:
                continue
            if not self.TREND_FIELDS.search(k):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                result[k] = float(v)
        return result

    def _detect_trends(self, readings: List[dict],
                       osc_readings: List[dict] = None) -> List[str]:
        if len(readings) < self._min_readings:
            return []

        all_fields: Set[str] = set()
        for r in readings:
            all_fields.update(r["values"].keys())

        trends = []
        for field in all_fields:
            values = [r["values"].get(field) for r in readings]
            if any(v is None for v in values):
                continue

            increasing = all(values[i] < values[i + 1] for i in range(len(values) - 1))
            decreasing = all(values[i] > values[i + 1] for i in range(len(values) - 1))

            if increasing:
                delta = values[-1] - values[0]
                pct = abs(delta / values[0]) * 100 if values[0] != 0 else 100
                if pct >= 5:
                    trends.append(f"{field} rising ({values[0]:.1f}→{values[-1]:.1f})")
            elif decreasing:
                delta = values[0] - values[-1]
                pct = abs(delta / values[0]) * 100 if values[0] != 0 else 100
                if pct >= 5:
                    trends.append(f"{field} falling ({values[0]:.1f}→{values[-1]:.1f})")

        if not trends and osc_readings and len(osc_readings) >= 4:
            osc_fields: Set[str] = set()
            for r in osc_readings:
                osc_fields.update(r["values"].keys())
            for field in osc_fields:
                values = [r["values"].get(field) for r in osc_readings]
                if any(v is None for v in values):
                    continue
                osc = self._detect_oscillation(field, values)
                if osc:
                    trends.append(osc)
        return trends

    def _detect_oscillation(self, field: str, values: List[float]) -> str:
        """Detect flapping — direction reverses on most consecutive readings."""
        if len(values) < 4:
            return ""
        reversals = 0
        for i in range(1, len(values) - 1):
            prev_dir = values[i] - values[i - 1]
            next_dir = values[i + 1] - values[i]
            if prev_dir * next_dir < 0:
                reversals += 1
        if reversals >= len(values) - 2:
            mn, mx = min(values), max(values)
            if mx - mn > 0:
                return f"{field} oscillating ({mn:.0f}↔{mx:.0f}, {reversals} reversals)"
        return ""

    def _cleanup(self, now: float):
        expired = []
        for entity, readings in self._history.items():
            self._history[entity] = [r for r in readings if now - r["ts"] < self._window]
            if not self._history[entity]:
                expired.append(entity)
        for e in expired:
            del self._history[e]


def default_agents() -> list:
    """Return the standard built-in cascade agents."""
    return [
        TrendDetector(),
        SafetyEscalator(),
        ThresholdClassifier(),
        DeduplicateAgent(),
        TransientSuppressor(),
        SeverityGate(),
        PatternClassifier(),
    ]
