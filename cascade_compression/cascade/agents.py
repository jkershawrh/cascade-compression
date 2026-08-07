"""Built-in cascade agents — generic patterns that work across domains.

Domain-specific agents (K8s pod health, Kafka lag, etc.) live in domain packs.
These agents handle universal signal patterns.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from typing import Dict, List, Set

from .protocol import CascadeAgent, CascadeDecision, Outcome, Signal


class DeduplicateAgent:
    """Drops duplicate signals within a time window."""

    name = "deduplicate"
    stage = 1

    def __init__(self, window_seconds: float = 60.0):
        self._window = window_seconds
        self._seen: Dict[str, float] = {}

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        now = time.monotonic()
        # Expire old entries
        self._seen = {k: v for k, v in self._seen.items() if now - v < self._window}

        decisions = []
        for s in signals:
            content_key = s.content.get("message", "") if s.content else ""
            key = hashlib.sha256(
                f"{s.signal_type}:{s.source}:{s.namespace}:{s.severity}:{content_key}".encode()
            ).hexdigest()
            if key in self._seen:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DEDUPE, evidence=f"duplicate of {key[:8]}",
                ))
            else:
                self._seen[key] = now
        return decisions


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
    """Drops info-severity signals unless they match escalation patterns."""

    name = "severity_gate"
    stage = 1

    ESCALATE_PATTERNS = [
        re.compile(r"(oomkill|segfault|panic|fatal|critical)", re.I),
        re.compile(r"(security|breach|unauthorized|injection)", re.I),
        re.compile(r"(data.?loss|corruption|integrity)", re.I),
    ]

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            if s.severity != "info":
                continue

            content_str = str(s.content)
            escalate = any(p.search(content_str) for p in self.ESCALATE_PATTERNS)

            if escalate:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE, evidence="info with critical pattern",
                ))
            else:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DROP, evidence="info severity, no escalation pattern",
                ))
        return decisions


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
    """Classifies signals based on numeric thresholds in content."""

    name = "threshold_classifier"
    stage = 2

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            c = s.content

            # CPU usage
            cpu = c.get("cpu_percent") or c.get("cpu_usage")
            if cpu is not None and isinstance(cpu, (int, float)):
                if cpu > 95:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.ESCALATE, classification="cpu_critical",
                        evidence=f"cpu={cpu}%", confidence=0.95,
                    ))
                    continue
                elif cpu > 80:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY, classification="cpu_warning",
                        evidence=f"cpu={cpu}%", confidence=0.90,
                    ))
                    continue

            # Memory usage
            mem = c.get("memory_percent") or c.get("memory_usage")
            if mem is not None and isinstance(mem, (int, float)):
                if mem > 95:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.ESCALATE, classification="memory_critical",
                        evidence=f"memory={mem}%", confidence=0.95,
                    ))
                    continue
                elif mem > 80:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY, classification="memory_warning",
                        evidence=f"memory={mem}%", confidence=0.90,
                    ))
                    continue

            # Disk usage
            disk = c.get("disk_percent") or c.get("disk_usage")
            if disk is not None and isinstance(disk, (int, float)):
                if disk > 95:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.ESCALATE, classification="disk_critical",
                        evidence=f"disk={disk}%", confidence=0.95,
                    ))
                elif disk > 80:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY, classification="disk_warning",
                        evidence=f"disk={disk}%", confidence=0.90,
                    ))

        return decisions


def default_agents() -> list:
    """Return the standard built-in cascade agents."""
    return [
        DeduplicateAgent(),
        TransientSuppressor(),
        SeverityGate(),
        PatternClassifier(),
        ThresholdClassifier(),
    ]
