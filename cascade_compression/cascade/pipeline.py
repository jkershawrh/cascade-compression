"""Stage-based cascade pipeline runner.

Runs signals through registered agents in stage order. Each stage can
drop/suppress/dedupe signals so they don't reach later stages or inference.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID

from .protocol import CascadeAgent, CascadeDecision, Outcome, Signal

log = logging.getLogger(__name__)


@dataclass
class CascadeResult:
    """Outcome of running the full cascade pipeline."""
    total_signals: int = 0
    decisions: List[CascadeDecision] = field(default_factory=list)
    remaining: List[Signal] = field(default_factory=list)
    suppressed_count: int = 0
    deduped_count: int = 0
    classified_count: int = 0
    escalated_count: int = 0
    dropped_count: int = 0

    @property
    def compression_ratio(self) -> float:
        """Fraction of signals handled without inference."""
        if self.total_signals == 0:
            return 0.0
        handled = self.total_signals - len(self.remaining)
        return handled / self.total_signals

    @property
    def needs_inference_count(self) -> int:
        return len(self.remaining)


class CascadePipeline:
    """Run registered agents, with optional fail-open evaluation profiles."""

    def __init__(self, agents: Optional[List[CascadeAgent]] = None,
                 *, verified_repeat_only: bool = False, triage_only: bool = False):
        if verified_repeat_only and triage_only:
            raise ValueError("nano profiles are mutually exclusive")
        self._agents: List[CascadeAgent] = []
        self._verified_repeat_only = verified_repeat_only
        self._triage_only = triage_only
        if agents:
            for agent in sorted(agents, key=lambda a: a.stage):
                self._agents.append(agent)

    def register(self, agent: CascadeAgent) -> None:
        self._agents.append(agent)
        self._agents.sort(key=lambda a: a.stage)

    def run(self, signals: List[Signal]) -> CascadeResult:
        if self._triage_only:
            return CascadeResult(total_signals=len(signals), remaining=list(signals))
        result = CascadeResult(total_signals=len(signals))
        active = list(signals)
        removed_ids: set[UUID] = set()
        escalated_ids: set[UUID] = set()

        for agent in self._agents:
            if not active:
                break

            try:
                decisions = agent.process(active)
            except Exception:
                log.exception("Agent %s failed, skipping", agent.name)
                continue

            if not self._verified_repeat_only:
                result.decisions.extend(decisions)

            for d in decisions:
                signal = next(
                    (candidate for candidate in active
                     if candidate.signal_id == d.signal_id), None,
                )
                if self._verified_repeat_only and d.outcome in {Outcome.SUPPRESS, Outcome.DROP}:
                    continue
                if self._verified_repeat_only and d.outcome == Outcome.DEDUPE:
                    content = signal.content if signal else {}
                    labels = signal.labels if signal else {}
                    verified_repeat = bool(
                        signal and d.agent_name == "deduplicate"
                        and labels.get("domain") == "kubernetes"
                        and signal.signal_type.startswith("event_")
                        and signal.severity == "medium"
                        and signal.cluster and signal.namespace and signal.source
                        and content.get("kind") == "Pod" and content.get("uid")
                        and content.get("event_uid") and content.get("reason")
                        and type(content.get("count")) is int
                        and isinstance(content.get("message_sha256"), str)
                        and re.fullmatch(r"[0-9a-f]{64}", content["message_sha256"])
                    )
                    if not verified_repeat:
                        continue
                if d.outcome == Outcome.SUPPRESS:
                    if d.signal_id not in escalated_ids:
                        removed_ids.add(d.signal_id)
                        result.suppressed_count += 1
                elif d.outcome == Outcome.DEDUPE:
                    if d.signal_id not in escalated_ids:
                        removed_ids.add(d.signal_id)
                        result.deduped_count += 1
                elif d.outcome == Outcome.DROP:
                    if d.signal_id not in escalated_ids:
                        removed_ids.add(d.signal_id)
                    result.dropped_count += 1
                elif d.outcome == Outcome.ESCALATE:
                    escalated_ids.add(d.signal_id)
                    result.escalated_count += 1
                elif d.outcome == Outcome.CLASSIFY:
                    result.classified_count += 1
                    # Only skip inference for info-severity classified signals.
                    # Everything medium+ still goes to inference for deeper analysis.
                    # Fail-open: when in doubt, send to inference.
                    if (not self._verified_repeat_only and signal
                            and signal.severity == "info"
                            and d.signal_id not in escalated_ids):
                        removed_ids.add(d.signal_id)

                if self._verified_repeat_only and d.outcome == Outcome.DEDUPE:
                    result.decisions.append(d)

            active = [s for s in active if s.signal_id not in removed_ids]

        # Drop remaining info-severity signals that weren't explicitly escalated
        result.remaining = (
            active if self._verified_repeat_only else [
                s for s in active
                if s.severity != "info" or s.signal_id in escalated_ids
            ]
        )
        result.dropped_count += len(active) - len(result.remaining)

        return result
