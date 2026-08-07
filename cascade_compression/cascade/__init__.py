"""Cascade framework — deterministic signal processing before inference.

Handles 85%+ of signals without touching an LLM by running them through
a pipeline of nanoagents (pattern matching, rules, dedup, classification).
Only the remaining signals that need inference are routed to model lanes.
"""

from .protocol import CascadeAgent, CascadeDecision, Signal
from .pipeline import CascadePipeline
from .router import CascadeRouter
from .promotion import AgentMetrics, Baseline, PromotionEngine, RuleAgent

__all__ = [
    "CascadeAgent",
    "CascadeDecision",
    "CascadePipeline",
    "CascadeRouter",
    "Signal",
    "AgentMetrics",
    "Baseline",
    "PromotionEngine",
    "RuleAgent",
]
