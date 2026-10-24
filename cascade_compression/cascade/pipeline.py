"""Stage-based cascade pipeline runner.

Runs signals through registered agents in stage order. Each stage can
drop/suppress/dedupe signals so they don't reach later stages or inference.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID

from .protocol import CascadeAgent, CascadeDecision, Outcome, Signal
from .agents import DeduplicateAgent
from .repeat_integrity import verified_repeat_key

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
                 *, verified_repeat_only: bool = False, triage_only: bool = False,
                 verified_repeat_update: bool = False, qualified_suppression=None):
        if sum(map(bool, (verified_repeat_only, triage_only, verified_repeat_update))) > 1:
            raise ValueError("nano profiles are mutually exclusive")
        self._agents: List[CascadeAgent] = []
        self._verified_repeat_only = verified_repeat_only
        self._verified_repeat_update = verified_repeat_update
        self._triage_only = triage_only
        self._qualified_suppression = qualified_suppression
        self._verified_deduplicator = None
        if agents:
            for agent in sorted(agents, key=lambda a: a.stage):
                self.register(agent)

    def register(self, agent: CascadeAgent) -> None:
        if type(agent) is DeduplicateAgent and (self._verified_repeat_only
                                                 or self._verified_repeat_update):
            if self._verified_deduplicator is not None:
                raise ValueError('Only one verified deduplicator may be registered')
            agent = DeduplicateAgent(window_seconds=agent._window,
                                     require_verified_message=True,
                                     update_on_growth=self._verified_repeat_update)
            self._verified_deduplicator = agent
        self._agents.append(agent)
        self._agents.sort(key=lambda a: a.stage)

    def run(self, signals: List[Signal]) -> CascadeResult:
        # Ambiguous IDs make a decision for one input affect another. Reject the
        # whole batch before invoking stateful agents, so the caller can retry.
        ids = [signal.signal_id for signal in signals]
        if any(value is None or value == "" for value in ids):
            raise ValueError("signal IDs must be non-empty and unique within a batch")
        if len(set(ids)) != len(ids) or len({str(value) for value in ids}) != len(ids):
            raise ValueError("signal IDs must be unique within a batch")
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

            active_by_id = {signal.signal_id: signal for signal in active}
            for d in decisions:
                signal = active_by_id.get(d.signal_id)
                if signal is None:
                    continue
                if (d.signal_id in escalated_ids and d.outcome in {
                    Outcome.SUPPRESS, Outcome.DROP, Outcome.DEDUPE,
                }):
                    # Ignored removal attempts must not appear as suppression
                    # evidence or inflate the archive and compression counters.
                    continue
                if (self._verified_repeat_only or self._verified_repeat_update) \
                        and d.outcome in {Outcome.SUPPRESS, Outcome.DROP}:
                    allowed = False
                    if (self._verified_repeat_update and d.outcome == Outcome.SUPPRESS
                            and self._qualified_suppression is not None):
                        try:
                            allowed = self._qualified_suppression(agent, signal, d) is True
                        except Exception:
                            log.exception("Qualified suppression check failed; retaining signal")
                    if not allowed:
                        continue
                if self._verified_repeat_only and d.outcome == Outcome.DEDUPE:
                    verified_repeat = bool(signal and agent is self._verified_deduplicator
                                           and verified_repeat_key(signal) is not None)
                    if not verified_repeat:
                        continue
                if self._verified_repeat_update and d.outcome == Outcome.DEDUPE:
                    from .repeat_integrity import verified_repeat_update_key
                    verified_update = bool(signal and agent is self._verified_deduplicator
                                           and verified_repeat_update_key(signal) is not None)
                    if not verified_update:
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
                    if d.signal_id not in escalated_ids:
                        result.escalated_count += 1
                    escalated_ids.add(d.signal_id)
                elif d.outcome == Outcome.CLASSIFY:
                    result.classified_count += 1
                    # Only skip inference for info-severity classified signals.
                    # Everything medium+ still goes to inference for deeper analysis.
                    # Fail-open: when in doubt, send to inference.
                    if (not (self._verified_repeat_only or self._verified_repeat_update) and signal
                            and signal.severity == "info"
                            and d.signal_id not in escalated_ids):
                        removed_ids.add(d.signal_id)

                if not self._verified_repeat_only or d.outcome == Outcome.DEDUPE:
                    result.decisions.append(d)

            active = [s for s in active if s.signal_id not in removed_ids]

        # Only explicit, accepted agent decisions may remove a signal.
        result.remaining = active

        return result
