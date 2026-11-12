"""Cascade bridge — connects collector → pipeline → LLM.

Standalone. No dependency on deepfield or any consuming application.
The bridge initializes the cascade pipeline, processes signals from any
collector, and optionally forwards survivors to an LLM for classification.
"""

import json
import hashlib
import logging
import math
import os
import random
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import UUID, uuid4

from . import classification_completion as completion
from .cascade.agents import default_agents, DeduplicateAgent
from .cascade.corpus_analyzer import CorpusAnalyzer
from .cascade.dynamic_agents import ContextualNoiseSuppressor, DominantNoiseSuppressor, RepeatFloodSuppressor
from .cascade.inverse import SuppressionArchive
from .cascade.memory import MemoryArchive, MemoryEventBackpressure
from .cascade.pipeline import CascadePipeline
from .cascade.promotion import AgentMetrics, Baseline, PromotionEngine, RuleAgent
from .cascade.protocol import Signal, llm_complete, severity_input_is_valid
from .cascade.protocol import Outcome
from .cascade.template_cache import AdmittedQueueEntry, TemplateCachedClassifierBackend
from .cascade.triage import ExactRepeatTriage
from .classifier import SERIALIZER_REVISION, classifier_from_environment, serialize_signal
from .durable_queue import (
    DURABLE_QUEUE_OVERFLOW_POLICY, DurableLedgerQueue, DurableQueue,
    sanitize_payload,
)
from .lifecycle import LifecycleControls, REVISION, atomic_snapshot, valid_time

log = logging.getLogger(__name__)

_PROMOTION_MIN_SAMPLES = 200
_PROMOTION_ALLOWED_IMPORTANT = 0

_BUILTIN_AGENT_NAMES = frozenset({
    "deduplicate", "transient_suppressor", "severity_gate",
    "pattern_classifier", "threshold_classifier", "trend_detector",
    "safety_escalator",
})


def suppression_tier_for(samples: int) -> tuple:
    """Deeper qualification evidence earns deeper suppression authority.

    Standard (promotion bar .. tier-2 bar): first-3 per source per 5 min
    (~36 deliveries/hour/source). Tier 2: first-1 per 5 min (~12/hr).
    Tier 3: first-3 per hour (~3/hr). Tiers only deepen suppression
    inside an already-qualified type; withdrawal, shadow validation,
    and the per-decision qualification gate are unchanged.
    """
    tier2 = int(os.getenv("CASCADE_SUPPRESSION_TIER2_SAMPLES", "5000"))
    tier3 = int(os.getenv("CASCADE_SUPPRESSION_TIER3_SAMPLES", "50000"))
    if samples >= tier3:
        return (3, 3600.0)
    if samples >= tier2:
        return (1, 300.0)
    return (3, 300.0)


def _is_noise_classification(classification: str) -> bool:
    return "routine_noise" in classification or "known_pattern" in classification


def _value_rule_classification(rule: dict):
    """Deterministic ClassificationResult for a matched value rule.

    The rule's taxonomy label is authoritative: no model call, no fallback.
    Provenance (spec revision, rule name) rides on model_revision/backend so
    every downstream record shows which spec produced the verdict.
    """
    from .classifier import ClassificationResult, RankedLabel

    label = rule.get("label", "needs_attention")
    confidence = float(rule.get("confidence", 0.95))
    return ClassificationResult(
        label=label,
        ranked=[RankedLabel(label=label, score=confidence)],
        confidence=confidence,
        margin=confidence,
        latency_ms=0.0,
        backend="value_rule",
        authoritative_backend="value_rule",
        model_revision=rule.get("revision", ""),
        status="ok",
    )


@dataclass
class BridgeStats:
    signals_processed: int = 0
    cascade_handled: int = 0
    cascade_forwarded: int = 0
    llm_classified: int = 0
    llm_noise: int = 0
    llm_important: int = 0
    value_rule_classified: int = 0
    value_rule_else_classified: int = 0
    compression_ratio: float = 0.0
    started_at: str = ""
    last_updated: str = ""


def _to_cascade_signal(sig: Any) -> Signal:
    """Convert any signal object to the cascade Signal protocol."""
    return Signal(
        signal_id=(
            uuid4() if getattr(sig, "signal_id", None) in (None, "")
            else sig.signal_id
        ),
        signal_type=getattr(sig, "signal_type", ""),
        severity=getattr(sig, "severity", "info"),
        severity_input_valid=getattr(sig, "severity_input_valid", True),
        source=getattr(sig, "resource_name", ""),
        namespace=getattr(sig, "namespace", ""),
        cluster=str(getattr(sig, "cluster_id", "")),
        content={
            "resource_kind": getattr(sig, "resource_kind", ""),
            "message": getattr(sig, "evidence", {}).get("message", ""),
            **{k: v for k, v in getattr(sig, "evidence", {}).items() if k != "message"},
        },
        labels=getattr(sig, "labels", {}),
    )


class CascadeBridge(LifecycleControls):
    """Standalone cascade bridge.

    Usage::

        bridge = CascadeBridge(
            llm_url="https://llm.example.com/v1",
            llm_key="sk-...",
            llm_model="your-model-name",
            system_prompt="You are classifying...",
        )
        signals = collector.collect()
        result = bridge.process(signals)
    """

    def __init__(
        self,
        llm_url: str = "",
        llm_key: str = "",
        llm_model: str = "",
        system_prompt: str = "",
        domain: str = "default",
        ledger_url: str = "",
        ledger_token: str = "",
    ):
        self.domain = domain
        self._closed = False
        self._llm_url = llm_url or os.getenv("CASCADE_LLM_URL", os.getenv("LITELLM_API_BASE", ""))
        self._llm_key = llm_key or os.getenv("CASCADE_LLM_KEY", os.getenv("LITELLM_API_KEY", ""))
        self._llm_model = llm_model or os.getenv("CASCADE_LLM_MODEL", "")
        self._micro_model = os.getenv("CASCADE_MICRO_MODEL", "") or self._llm_model
        self._macro_model = os.getenv("CASCADE_MACRO_MODEL", "") or self._llm_model
        self._gpu_url = os.getenv("CASCADE_GPU_URL", "")
        self._gpu_key = os.getenv("CASCADE_GPU_KEY", "") or self._llm_key
        self._gpu_model = os.getenv("CASCADE_GPU_MODEL", "")
        self._gpu_analyses: list = []
        self._gpu_analyses_file = os.getenv("CASCADE_GPU_ANALYSES_FILE", "")
        self._system_prompt = system_prompt
        # Truth-informed few-shot exemplars from the domain plugin, if present.
        # These teach the generative model the platform's operating rhythm
        # (the noise/pattern/attention boundaries that generic prompts miss).
        from .domain_plugins import load_domain_plugin
        try:
            plugin = load_domain_plugin(self.domain)
            self._few_shot = plugin.few_shot_exemplars
            self._value_rules = plugin.value_rules
        except Exception:
            self._few_shot = None
            self._value_rules = None
        self.classifier = classifier_from_environment(
            url=self._llm_url,
            key=self._llm_key,
            micro_model=self._micro_model,
            macro_model=self._macro_model,
            system_prompt=self._system_prompt,
            few_shot_exemplars=self._few_shot,
        )
        self._ledger_url = ledger_url or os.getenv("CASCADE_LEDGER_URL", "")
        self._ledger_token = ledger_token or os.getenv("CASCADE_LEDGER_TOKEN", "")
        self._state_file = os.getenv("CASCADE_STATE_FILE", "")
        self._ledger_max_pending = max(
            1, int(os.getenv("CASCADE_LEDGER_MAX_PENDING", "8"))
        )
        self._ledger_slots = threading.BoundedSemaphore(self._ledger_max_pending)
        self._ledger_executor = ThreadPoolExecutor(
            max_workers=max(1, int(os.getenv("CASCADE_LEDGER_THREADS", "2"))),
            thread_name_prefix="cascade-ledger",
        )
        self._ledger_writes_dropped = 0
        self._ledger_receipt_pending_max = max(
            100, int(os.getenv("CASCADE_LEDGER_RECEIPT_PENDING", "100000"))
        )
        self._ledger_receipt_bytes_max = max(
            1024 * 1024,
            int(os.getenv(
                "CASCADE_LEDGER_RECEIPT_BYTES", str(512 * 1024 * 1024),
            )),
        )
        receipt_spool = os.getenv("CASCADE_LEDGER_RECEIPT_SPOOL_FILE", "")
        if not receipt_spool and self._state_file:
            receipt_spool = self._state_file + ".ledger-receipts.sqlite3"
        self._ledger_receipt_store = (
            DurableLedgerQueue(
                receipt_spool,
                max_pending=self._ledger_receipt_pending_max,
                max_bytes=self._ledger_receipt_bytes_max,
            )
            if receipt_spool else None
        )
        self._ledger_receipt_lock = threading.Lock()
        self._ledger_receipt_flush_running = False
        self._ledger_receipt_retry_timer = None
        self._ledger_receipt_failures = 0
        self._ledger_receipt_consecutive_failures = 0
        self._ledger_receipts_written = 0
        self._ledger_receipt_last_success = ""
        self._ledger_memory_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cascade-memory-ledger",
        )
        self._ledger_memory_pending: list = []
        self._ledger_memory_batch_count = 0
        self._ledger_memory_pending_max = max(
            100, int(os.getenv("CASCADE_LEDGER_MEMORY_PENDING", "50000"))
        )
        self._ledger_memory_bytes_max = max(
            1024 * 1024,
            int(os.getenv(
                "CASCADE_LEDGER_MEMORY_BYTES", str(512 * 1024 * 1024),
            )),
        )
        self._ledger_memory_batch_size = max(
            1, int(os.getenv("CASCADE_LEDGER_MEMORY_BATCH", "100"))
        )
        self._ledger_memory_lock = threading.Lock()
        self._ledger_memory_flush_running = False
        self._ledger_memory_events_dropped = 0
        self._ledger_memory_batches_written = 0
        self._ledger_memory_failures = 0
        self._ledger_memory_consecutive_failures = 0
        self._ledger_memory_last_success = ""
        self._ledger_memory_retry_initial = max(
            0.1, float(os.getenv("CASCADE_LEDGER_RETRY_INITIAL_SECONDS", "1"))
        )
        self._ledger_memory_retry_max = max(
            self._ledger_memory_retry_initial,
            float(os.getenv("CASCADE_LEDGER_RETRY_MAX_SECONDS", "60")),
        )
        self._ledger_memory_retry_timer = None
        spool_file = os.getenv("CASCADE_LEDGER_MEMORY_SPOOL_FILE", "")
        if not spool_file and self._state_file:
            spool_file = self._state_file + ".ledger-memory.sqlite3"
        self._ledger_memory_store = (
            DurableLedgerQueue(
                spool_file,
                max_pending=self._ledger_memory_pending_max,
                max_bytes=self._ledger_memory_bytes_max,
            )
            if spool_file else None
        )

        self.enabled = True
        nano_profile = os.getenv("CASCADE_NANO_PROFILE", "legacy").strip().lower()
        if nano_profile not in {"legacy", "verified_repeat_only", "triage_only",
                                 "verified_repeat_update", "qualified_repeat_update"}:
            raise ValueError(f"unknown CASCADE_NANO_PROFILE: {nano_profile}")
        self.nano_profile = nano_profile
        # The dedup window is the retention horizon for repeat suppression.
        # The default (60s) matches the collection cadence; deployments with
        # recurring events should set this to their event-recurrence horizon.
        dedup_window = float(os.getenv("CASCADE_DEDUP_WINDOW_SECONDS", "60"))
        agents = default_agents()
        for i, agent in enumerate(agents):
            if type(agent) is DeduplicateAgent:
                agents[i] = DeduplicateAgent(window_seconds=dedup_window)
        self.pipeline = CascadePipeline(
            agents,
            verified_repeat_only=nano_profile == "verified_repeat_only",
            triage_only=nano_profile == "triage_only",
            verified_repeat_update=nano_profile in {"verified_repeat_update", "qualified_repeat_update"},
            qualified_suppression=(self._qualified_suppression_allowed
                                   if nano_profile == "qualified_repeat_update" else None),
        )
        # Domain-pack value rules (deterministic telemetry thresholds).
        # Escalate/classify only — never suppress. Gated off with
        # CASCADE_VALUE_RULES=off; packs without VALUE_RULES are unaffected.
        self._value_rule_agent = None
        if (self._value_rules
                and os.getenv("CASCADE_VALUE_RULES", "on").strip().lower()
                not in {"off", "0", "false", "no"}):
            from .cascade.agents import ValueRuleAgent
            self._value_rule_agent = ValueRuleAgent(self._value_rules)
            self.pipeline.register(self._value_rule_agent)
        self._triage_advisor = ExactRepeatTriage() if nano_profile == "triage_only" else None
        self._human_gate = bool(os.getenv("CASCADE_HUMAN_GATE", ""))
        self.promotion = PromotionEngine(human_gate_enabled=self._human_gate,
                                        approval_ttl_hours=float(os.getenv('CASCADE_APPROVAL_TTL_HOURS','24')))
        self.stats = BridgeStats(started_at=datetime.now(timezone.utc).isoformat())

        self._repeat_suppressor = RepeatFloodSuppressor(max_repeats=3, window_seconds=300)
        self._noise_suppressor = DominantNoiseSuppressor(window_seconds=300)
        # Multi-key contextual suppression: each key gets its own suppressor
        # and discovery path. CASCADE_CONTEXT_KEYS is a comma-separated list
        # (default: "namespace"). E.g., "namespace,source" decomposes mixed
        # types by both namespace and source (task name / playbook name).
        context_keys = [
            k.strip() for k in os.getenv(
                "CASCADE_CONTEXT_KEYS", os.getenv("CASCADE_CONTEXT_KEY", "namespace")
            ).split(",") if k.strip()
        ]
        self._contextual_suppressors: list = []
        for _ck in context_keys:
            _suppressor = ContextualNoiseSuppressor(
                context_key=_ck, window_seconds=300,
                agent_name=f"contextual_noise_suppressor_{_ck}",
            )
            self._contextual_suppressors.append(_suppressor)
            self.pipeline.register(_suppressor)
        # Primary suppressor for backward compatibility (first key)
        self._contextual_suppressor = self._contextual_suppressors[0]
        # Make the context keys available to the lifecycle for recording
        self._context_keys = tuple(context_keys)
        self.pipeline.register(self._repeat_suppressor)
        self.pipeline.register(self._noise_suppressor)
        self._activated_types: set = set()
        self._activated_patterns: Dict[str, str] = {}
        self._activated_contexts: Dict[str, set] = defaultdict(set)
        self._context_activation_timestamps: Dict[str, str] = {}

        self.corpus_analyzer = CorpusAnalyzer(
            min_frequency=0.05, min_repeat_count=10,
            repeat_window_seconds=300,
            analysis_interval=float(
                os.getenv("CASCADE_ANALYSIS_INTERVAL_SECONDS", "300")),
        )

        self._llm_buffer: list = []
        self._llm_max_buffer = max(1, int(os.getenv("CASCADE_LLM_BUFFER", "10000")))
        self._llm_max_buffer_bytes = max(
            1024, int(os.getenv("CASCADE_LLM_BUFFER_BYTES", str(128 * 1024 * 1024)))
        )
        self._llm_max_content_bytes = max(
            256, int(os.getenv("CASCADE_LLM_CONTENT_BYTES", str(16 * 1024)))
        )
        self._llm_buffer_bytes = 0
        self._llm_coalescing_enabled = os.getenv(
            "CASCADE_LLM_COALESCE", "1",
        ).lower() not in {"0", "false", "no"}
        self._llm_coalesce_index: Dict[bytes, dict] = {}
        self._llm_coalesced = 0
        self._llm_coalesced_by_type: Dict[str, int] = defaultdict(int)
        self._llm_priority_buffer: list = []
        self._llm_priority_max_buffer = max(
            1, int(os.getenv("CASCADE_LLM_PRIORITY_BUFFER", "2000"))
        )
        self._llm_priority_max_bytes = max(
            1024, int(os.getenv(
                "CASCADE_LLM_PRIORITY_BUFFER_BYTES", str(32 * 1024 * 1024),
            ))
        )
        self._llm_priority_buffer_bytes = 0
        self._llm_priority_dropped = 0
        self._llm_payloads_truncated = 0
        self._llm_batch_size = int(os.getenv("CASCADE_LLM_BATCH", "500"))
        self._llm_max_concurrent = int(os.getenv("CASCADE_LLM_THREADS", "3"))
        self._llm_max_inflight = max(
            1, int(os.getenv("CASCADE_LLM_MAX_INFLIGHT", "1")),
        )
        self._llm_slots = threading.BoundedSemaphore(self._llm_max_inflight)
        self._llm_inflight = 0
        self._llm_peak_inflight = 0
        self._llm_inflight_lock = threading.Lock()
        self._llm_results: list = []
        self._llm_active_threads = 0
        self._llm_lock = threading.Lock()
        self._llm_queue_lock = threading.RLock()
        self._llm_noise_counts: Dict[str, int] = defaultdict(int)
        self._llm_important_counts: Dict[str, int] = defaultdict(int)
        self._llm_failure_counts: Dict[str, int] = defaultdict(int)
        self._llm_accounting_started_at = datetime.now(timezone.utc).isoformat()
        self._llm_accounting_enqueued = 0
        self._llm_accounting_dispatched = 0
        self._llm_accounting_succeeded = 0
        self._llm_accounting_failed = 0
        self._llm_accounting_dropped = 0
        self._llm_context_noise: Dict[str, int] = defaultdict(int)
        self._llm_context_important: Dict[str, int] = defaultdict(int)

        self._agent_metrics: Dict[str, AgentMetrics] = {}
        self._agent_rules: Dict[str, RuleAgent] = {}
        self._promotion_log: list = []
        self._last_promotion_time: float = 0
        self._promotion_interval: float = float(
            os.getenv("CASCADE_PROMOTION_INTERVAL_SECONDS", "300"))

        self._fn_count = 0
        self._fn_evaluated = 0
        self._fn_types: Dict[str, int] = defaultdict(int)
        self._gcl_builtin_fails = 0
        # Ledger entries use an integer ``written_ts`` watermark.  Keep a
        # bounded ID set as well because the ledger's ``from_ts`` filter is
        # inclusive, so entries at the watermark can legitimately reappear.
        self._verdict_watermark: Any = 0
        self._verdict_seen_ids: Dict[str, None] = {}
        self._verdict_poll_running = False
        self._verdict_poll_lock = threading.Lock()
        self._defer_verdict_feedback_save = False
        self._verdict_mode = os.getenv("CASCADE_VERDICT_MODE", "strict").strip().lower()
        if self._verdict_mode not in {"strict", "legacy"}:
            raise ValueError("unknown CASCADE_VERDICT_MODE")
        self._verdict_cursor = None
        self._verdict_scope = None
        self._verdict_store_id = None
        self._verdict_catchup_complete = False
        self._verdict_checked_through = 0
        self._verdict_catchup_error = ""

        self._llm_dropped = 0
        self._llm_dropped_by_severity: Dict[str, int] = defaultdict(int)
        self._route_counts: Dict[str, int] = defaultdict(int)
        self._llm_calls_by_tier: Dict[str, int] = defaultdict(int)
        self._llm_seconds_by_tier: Dict[str, float] = defaultdict(float)

        self._shadow_sample_rate = float(os.getenv("CASCADE_SHADOW_RATE", "0.05"))
        self._shadow_buffer: list = []
        self._shadow_max_buffer = 100
        self._shadow_running = False
        self._shadow_checks = 0
        self._shadow_demotions = 0

        self._activation_ttl_hours = float(os.getenv("CASCADE_ACTIVATION_TTL_HOURS", "72"))
        self._qualification_ttl_hours = float(os.getenv('CASCADE_QUALIFICATION_TTL_HOURS','72'))
        if not math.isfinite(self._qualification_ttl_hours) or self._qualification_ttl_hours <= 0:
            raise ValueError('CASCADE_QUALIFICATION_TTL_HOURS must be finite and positive')
        self._activation_timestamps: Dict[str, str] = {}
        self._serializer_revision = SERIALIZER_REVISION
        self._lifecycle_lock = threading.RLock()
        self._snapshot_lock = threading.RLock()
        self._qualification_evidence = {}
        self._classifier_inflight = {}
        self._executing_work_ids = set()
        self._completion_pending = {}
        self._completion_ack = set()
        self._classifier_recovery_error = ""
        self._ledger_memory_handoff_failures = 0
        self._ledger_memory_handoff_healthy = True
        self._classifier_terminal_receipts = []
        self._classifier_terminal_evicted = 0
        self._classifier_terminal_max = max(1, int(os.getenv('CASCADE_CLASSIFIER_TERMINAL_RECEIPTS_MAX', '10000')))
        self._state_healthy = True
        self._learning_fence_reason = ""

        self.baseline = Baseline(normal_ranges={})
        self._observed_ranges: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))

        self._memory_enabled = bool(os.getenv("CASCADE_MEMORY_ENABLED", "1"))
        self._memory_max = int(os.getenv("CASCADE_MEMORY_MAX", "10000"))
        self.memory_archive = (MemoryArchive(max_capacity=self._memory_max, audit_backpressure=True)
                               if self._memory_enabled else None)
        self.suppression_archive = SuppressionArchive(max_capacity=self._memory_max) if self._memory_enabled else None

        # Meta-cascade: self-monitoring signals
        self._meta_signals: list = []
        self._meta_max_buffer = 500
        self._meta_target = os.getenv("CASCADE_META_TARGET", "")
        self._meta_last_emitted: Dict[str, float] = {}
        self._context_lock = threading.Lock()
        self._verdict_lock = threading.Lock()
        # Context activation/demotion state is mutated from the verdict
        # poll thread, the promotion cycle, and classifier completion,
        # while _save_state iterates it — structural mutation and
        # iteration must be mutually exclusive.
        self._contexts_lock = threading.RLock()
        self._pending_memory_ids: Dict[str, Any] = {}
        self._pending_memory_lock = threading.Lock()
        # Durable classification-work spool (opt-in via CASCADE_LLM_QUEUE_FILE).
        # Attributes exist even when disabled so queue paths stay uniform.
        self._durable_queue = None
        self._durable_dirty: Dict[str, Any] = {}
        self._durable_persisted_ids: set = set()
        self._durable_staged_entries: list = []
        self._durable_recovered_ids: set = set()
        self._durable_recovered_accounted: set = set()
        self._accounting_restored_from_snapshot = False
        self._durable_memory_checkpoint_healthy = True
        self._durable_memory_checkpoint_failures = 0

        # The spool must be read before the state snapshot so recovered
        # work IDs are known when interrupted classifier work is settled:
        # durably spooled work is replayed, everything else stays terminal.
        queue_file = os.getenv("CASCADE_LLM_QUEUE_FILE", "")
        if queue_file:
            self._durable_queue = DurableQueue(
                queue_file,
                max_entries=int(
                    os.getenv("CASCADE_LLM_QUEUE_MAX_ENTRIES", "15000")
                ),
                max_bytes=int(
                    os.getenv(
                        "CASCADE_LLM_QUEUE_MAX_BYTES", str(256 * 1024 * 1024),
                    )
                ),
                ttl_seconds=int(
                    os.getenv("CASCADE_LLM_QUEUE_TTL_SECONDS", "86400")
                ),
            )
            try:
                for entry in self._durable_queue.restore():
                    self._durable_persisted_ids.add(entry["_work_id"])
                    self._durable_recovered_ids.add(entry["_work_id"])
                    self._durable_staged_entries.append(entry)
            except Exception:
                self._durable_queue.close()
                raise

        self._restore_state()
        if self._ledger_url and self._ledger_receipt_pending_count():
            self._start_ledger_receipt_drain()
        self._start_memory_ledger_drain()

    def _qualified_suppression_allowed(self, agent, signal, decision):
        """Lifecycle authority, never a payload-provided approval flag.

        Called while process holds the lifecycle lock. Only the explicit
        qualified profile uses this; the bounded repeat profile is unchanged.
        """
        if (not self._verdict_authority_ready()
                or decision.outcome != Outcome.SUPPRESS or decision.agent_name != agent.name
                or signal.severity_input_valid is not True
                or signal.severity not in {"info", "low", "medium"}
                or signal.labels.get("label") in {"compliance", "fraud", "sanctions"}
                or not all(isinstance(v, str) and v.strip() for v in
                           (signal.source, signal.namespace, signal.cluster))
                or (self._state_file and (not self._state_healthy
                                         or getattr(self, "_save_failures", 0) > 0))):
            return False
        kind = signal.signal_type
        if agent is self._noise_suppressor or agent is self._repeat_suppressor:
            pattern = "dominant_type" if agent is self._noise_suppressor else "repeat_flood"
            return (kind in self._activated_types
                    and self._activated_patterns.get(kind) == pattern
                    and self._restored_authority_valid(kind, pattern))
        if agent in self._contextual_suppressors and not self._human_gate:
            context = getattr(signal, agent._context_key, "")
            identity = f"{kind}:{context}"
            record = self._qualified_record("context:" + identity, self._context_binding(identity))
            return bool(context in self._activated_contexts.get(kind, set())
                        and record and record["noise"] >= _PROMOTION_MIN_SAMPLES
                        and record["important"] == 0
                        and valid_time(self._context_activation_timestamps.get(identity), self._activation_ttl_hours)
                        and any(m.tier == "nano" and not m.deactivated
                                and m.config.get("pattern_type") == "contextual_noise"
                                and m.config.get("signal_type") == kind
                                and m.config.get("context_value") == context
                                for m in self._agent_metrics.values()))
        return False

    def process(self, signals: list) -> Dict:
        if not self.enabled or not signals:
            return {"enabled": False}

        if self._classifier_recovery_error:
            return {"enabled": False, "accepted": False, "durable_acceptance": False,
                    "backpressure": True, "status": "classification_recovery_required"}
        if self.memory_archive is not None and self._durable_queue is not None and not self._state_file:
            return {"enabled": False, "accepted": False, "durable_acceptance": False,
                    "backpressure": True, "status": "memory_snapshot_required"}
        if (self._completion_pending or self._completion_ack) and not completion.resume(self):
            self._flush_memory_events()
            return {"enabled": False, "accepted": False, "durable_acceptance": False,
                    "backpressure": True, "status": "classification_completion_pending"}
        cascade_signals = [_to_cascade_signal(s) for s in signals]

        t0 = time.monotonic()
        with self._lifecycle_lock:
            self._check_activation_ttl()
            # The legacy nano profile also runs learned agents. Withhold just
            # those agents during recovery; the deterministic stages still run.
            withheld = () if self._verdict_authority_ready() else (
                self._repeat_suppressor, self._noise_suppressor, *self._contextual_suppressors,
            )
            cascade_result = self.pipeline.run(cascade_signals, withheld_agents=withheld)
        cascade_ms = (time.monotonic() - t0) * 1000

        triage_tags = (
            self._triage_advisor.assess(cascade_signals)
            if self._triage_advisor is not None else {}
        )

        self._last_remaining = list(cascade_result.remaining)
        remaining_count = len(cascade_result.remaining)
        handled_count = len(signals) - remaining_count
        self.stats.signals_processed += len(signals)
        self.stats.cascade_handled += handled_count
        self.stats.cascade_forwarded += remaining_count
        if self.stats.signals_processed > 0:
            self.stats.compression_ratio = self.stats.cascade_handled / self.stats.signals_processed
        self.stats.last_updated = datetime.now(timezone.utc).isoformat()
        self._record_terminal_routes(cascade_signals, cascade_result)

        self.corpus_analyzer.observe(cascade_signals)

        has_suppressors = self._activated_types or self._activated_contexts
        if has_suppressors and self._shadow_sample_rate > 0 and self._llm_url:
            self._queue_shadow_samples(cascade_result, cascade_signals)

        if self.suppression_archive is not None:
            self.suppression_archive.record_batch(
                cascade_result.decisions, cascade_signals)

        memory_targets = {}
        if self.memory_archive is not None:
            for sig in cascade_result.remaining:
                classification = "needs_attention"
                for d in cascade_result.decisions:
                    if d.signal_id == sig.signal_id and d.classification:
                        classification = d.classification
                        break
                try:
                    memory = self.memory_archive.store(sig, classification=classification)
                except MemoryEventBackpressure:
                    self._flush_memory_events()
                    return {"enabled": False, "accepted": False, "durable_acceptance": False,
                            "backpressure": True, "status": "memory_audit_backpressure",
                            "recovery_required": True}
                memory_targets[id(sig)] = memory.memory_id
                if self._classification_dispatch_enabled() or self._durable_queue is not None:
                    with self._pending_memory_lock:
                        self._pending_memory_ids[str(sig.signal_id)] = memory.memory_id

        # Meta: detect memory pressure (above 80% capacity)
        if self.memory_archive is not None:
            mem_usage = self.memory_archive.size / max(1, self.memory_archive._max_capacity)
            if mem_usage > 0.8:
                self._emit_meta(
                    "meta_memory_pressure", "medium",
                    f"Memory archive at {mem_usage:.0%} "
                    f"({self.memory_archive.size}/{self.memory_archive._max_capacity})",
                    usage=round(mem_usage, 3),
                    size=self.memory_archive.size,
                    capacity=self.memory_archive._max_capacity,
                )

        # Checkpoint classification memories before queue admission so a
        # durable spool entry can never reference an unpersisted memory.
        if not self._checkpoint_classification_memories():
            return {"enabled": False, "accepted": False, "durable_acceptance": False,
                    "backpressure": True, "status": "memory_checkpoint_failed",
                    "recovery_required": True}
        # Value-rule matches carry their taxonomy label authoritatively
        # (deterministic, versioned spec — the qualified-head discipline).
        # Covered types with no rule match take the healthy-by-spec else
        # label on the same authoritative path.
        value_rule_labels = {}
        for d in cascade_result.decisions:
            if d.agent_name == "value_rule" and d.metadata.get("label"):
                value_rule_labels[str(d.signal_id)] = {
                    "label": d.metadata["label"],
                    "rule": d.metadata["value_rule"],
                    "classification": d.classification,
                    "confidence": d.confidence,
                    "revision": d.metadata["spec_revision"],
                }
        if (self._value_rule_agent is not None
                and self._value_rule_agent._covers):
            remaining_ids = {str(s.signal_id) for s in cascade_result.remaining}
            for sig in cascade_result.remaining:
                if str(sig.signal_id) in value_rule_labels:
                    continue
                match = self._value_rule_agent.evaluate(sig)
                if match is not None:
                    continue  # a rule matched but declared no label
                else_entry = self._value_rule_agent.else_label(sig)
                if else_entry is not None:
                    value_rule_labels[str(sig.signal_id)] = else_entry
                    self.stats.value_rule_else_classified += 1
        if not self._enqueue_llm_signals(cascade_result.remaining,
                                         value_rule_labels, memory_targets=memory_targets):
            return {
                'enabled': False, 'accepted': False, 'durable_acceptance': False,
                'backpressure': True, 'status': 'classifier_checkpoint_failed',
                'recovery_required': True, 'signals': len(signals),
                'action': 'retry checkpoint or reconcile original signal IDs before replay',
                '_remaining_signal_ids': [str(sig.signal_id) for sig in cascade_result.remaining],
                'limitation': 'Processing may have changed in-memory state; classifier work is not durably accepted.',
            }

        # Meta: detect LLM starvation (buffer above 80% and all threads busy)
        buffer_pct = len(self._llm_buffer) / max(1, self._llm_max_buffer)
        if (buffer_pct > 0.8
                and self._llm_active_threads >= self._llm_max_concurrent
                and self._llm_url):
            self._emit_meta(
                "meta_llm_starvation", "high",
                f"LLM buffer at {buffer_pct:.0%} ({len(self._llm_buffer)}/{self._llm_max_buffer}) "
                f"with {self._llm_active_threads}/{self._llm_max_concurrent} threads busy",
                buffer_size=len(self._llm_buffer),
                buffer_max=self._llm_max_buffer,
                active_threads=self._llm_active_threads,
                max_threads=self._llm_max_concurrent,
            )

        self._dispatch_llm_batches()

        if self._shadow_buffer and not self._shadow_running and self._llm_url:
            batch = list(self._shadow_buffer[:20])
            self._shadow_buffer = self._shadow_buffer[20:]
            threading.Thread(target=self._run_shadow_llm, args=(batch,), daemon=True).start()

        now = time.monotonic()
        if now - self._last_promotion_time > self._promotion_interval:
            self._last_promotion_time = now
            self._discover_and_promote()
            self._flush_memory_events()
            self._save_state()
            if self._ledger_url and not self._verdict_poll_running:
                threading.Thread(
                    target=self._poll_gcl_verdicts, daemon=True,
                ).start()

        if self._ledger_url:
            self._enqueue_decisions_to_ledger(cascade_result, cascade_signals)

        return {
            "enabled": True,
            'accepted': True, 'backpressure': False,
            'durable_acceptance': bool(self._state_file) and self._state_healthy,
            'queue_persistence': 'snapshot' if self._state_file else 'memory_only',
            "signals": len(signals),
            "cascade_ms": round(cascade_ms, 1),
            "compression": round(cascade_result.compression_ratio, 3),
            "triaged_repeats": sum(
                bool(tag["exact_repeat"]) for tag in triage_tags.values()
            ),
            "triage_eligible": len(triage_tags),
            "triage_unqualified": len(signals) - len(triage_tags),
            "_remaining_signal_ids": [
                str(sig.signal_id) for sig in cascade_result.remaining
            ],
            "_remaining_signal_content": {
                str(sig.signal_id): deepcopy(sig.content) for sig in cascade_result.remaining
            },
            "_triage": triage_tags,
        }

    def _record_terminal_routes(self, signals: list, result: Any) -> None:
        """Account for every input exactly once without equating compression with savings."""
        remaining = {signal.signal_id for signal in result.remaining}
        outcomes: Dict[Any, set] = defaultdict(set)
        for decision in result.decisions:
            outcomes[decision.signal_id].add(decision.outcome)
        for signal in signals:
            if signal.signal_id in remaining:
                tier = "macro_eligible" if signal.severity in {"critical", "high"} else "micro_eligible"
            elif Outcome.DEDUPE in outcomes[signal.signal_id]:
                tier = "deduplicated"
            elif Outcome.SUPPRESS in outcomes[signal.signal_id]:
                tier = "suppressed"
            elif Outcome.CLASSIFY in outcomes[signal.signal_id]:
                tier = "classified_without_ai"
            else:
                tier = "excluded"
            self._route_counts[tier] += 1

    def _enqueue_llm_signals(self, signals: list, value_rule_labels: dict = None, *, memory_targets=None) -> bool:
        value_rule_labels = value_rule_labels or {}
        with self._llm_queue_lock:
            for sig in signals:
                self._llm_accounting_enqueued += 1
                entry = AdmittedQueueEntry(sig)
                content, truncated = self._compact_llm_content(sig.content)
                if self._durable_queue is not None:
                    content = sanitize_payload(content)
                now = datetime.now(timezone.utc).isoformat()
                signal_id = str(sig.signal_id)
                if memory_targets is None:
                    with self._pending_memory_lock:
                        memory_id = self._pending_memory_ids.get(signal_id)
                else:
                    memory_id = memory_targets.get(id(sig))
                entry.update({
                    "_memory_ids": {signal_id: str(memory_id)} if memory_id else {},
                    "_work_id": str(uuid4()),
                    "signal_id": signal_id,
                    "labels": dict(sig.labels or {}),
                    "content": content,
                    "_signal_ids": [signal_id],
                    "_signal_ids_bytes": len(signal_id.encode("utf-8")) + 16,
                    "_enqueued_at": now,
                })
                # Discard the fingerprint on any lossy projection. Keep a stable
                # index key for live entries; each later reuse rechecks the bytes.
                entry.admission_key = entry.verified_key()
                key = entry.admission_key
                rule = value_rule_labels.get(signal_id)
                if rule is not None and entry["severity_input_valid"]:
                    # Authoritative deterministic label: no model call, and
                    # no coalescing (each match is accounted individually).
                    entry["_value_rule"] = rule
                entry["_queue_bytes"] = self._estimate_llm_entry_bytes(entry)
                if sig.severity in {"critical", "high"}:
                    self._llm_priority_buffer.append(entry)
                    self._llm_priority_buffer_bytes += entry["_queue_bytes"]
                else:
                    if key is not None and self._can_coalesce(entry):
                        existing = self._llm_coalesce_index.get(key)
                        if (existing is not None and existing.verified_key() == key
                                and (signal_id not in existing["_signal_ids"]
                                     or existing["_memory_ids"].get(signal_id) == entry["_memory_ids"].get(signal_id))):
                            # Same semantic signal is already queued for
                            # classification: one LLM call can answer for
                            # every occurrence, so fold this signal into the
                            # existing entry instead of enqueueing a new one.
                            old_size = self._entry_queue_bytes(existing)
                            aggregation = existing.setdefault(
                                "_aggregation", {
                                    "occurrence_count": 1,
                                    "first_seen": existing.get("_enqueued_at", now),
                                },
                            )
                            aggregation["occurrence_count"] += 1
                            aggregation["last_seen"] = now
                            existing["_memory_ids"].update(entry["_memory_ids"])
                            existing["_signal_ids"].append(signal_id)
                            existing["_signal_ids_bytes"] = existing.get(
                                "_signal_ids_bytes", 0,
                            ) + len(signal_id.encode("utf-8")) + 16
                            existing.pop("_queue_bytes", None)
                            new_size = self._entry_queue_bytes(existing)
                            self._llm_buffer_bytes += new_size - old_size
                            self._llm_coalesced += 1
                            self._llm_coalesced_by_type[sig.signal_type] += 1
                            if truncated:
                                self._llm_payloads_truncated += 1
                            self._mark_durable_locked(existing)
                            continue
                        self._llm_coalesce_index[key] = entry
                    self._llm_buffer.append(entry)
                    self._llm_buffer_bytes += entry["_queue_bytes"]
                if truncated:
                    self._llm_payloads_truncated += 1
                self._mark_durable_locked(entry)
            self._enforce_llm_buffer_limit_locked()
            self._enforce_llm_priority_limit_locked()
            spooled = self._flush_durable_locked()
        if self._state_file:
            accepted = self._save_state()
            if not accepted:
                with self._lifecycle_lock:
                    self._fence_learning('enqueue checkpoint failed')
            return accepted and spooled
        return spooled

    def _dispatch_llm_batches(self) -> None:
        if (self._closed or not self._durable_memory_checkpoint_healthy
                or not completion.resume(self) or not self._classification_dispatch_enabled()):
            return
        while True:
            with self._llm_queue_lock:
                if self._completion_pending or self._completion_ack or self._classifier_recovery_error:
                    return
                try:
                    # Reclaim expired rows before retrying a capacity-blocked write.
                    self._expire_durable_locked()
                except Exception:
                    if self._durable_queue is not None:
                        self._durable_queue.healthy = False
                        self._durable_queue.failures += 1
                    log.error("Durable classification queue expiry failed; dispatch paused")
                    return
                if not self._flush_durable_locked():
                    return
                # Newly committed RAM work may itself have aged past TTL while
                # storage was unavailable. Do not dispatch it as fresh work.
                try:
                    self._expire_durable_locked()
                except Exception:
                    if self._durable_queue is not None:
                        self._durable_queue.healthy = False
                        self._durable_queue.failures += 1
                    log.error("Durable classification queue expiry failed; dispatch paused")
                    return
                if not self._llm_priority_buffer and not self._llm_buffer:
                    return
                with self._llm_lock:
                    if self._llm_active_threads >= self._llm_max_concurrent:
                        return
                    self._llm_active_threads += 1

                priority_count = min(
                    len(self._llm_priority_buffer), self._llm_batch_size,
                )
                batch = list(self._llm_priority_buffer[:priority_count])
                self._llm_priority_buffer = self._llm_priority_buffer[priority_count:]
                self._llm_priority_buffer_bytes = max(
                    0, self._llm_priority_buffer_bytes
                    - sum(self._entry_queue_bytes(sig) for sig in batch),
                )
                normal_count = self._llm_batch_size - len(batch)
                if normal_count:
                    normal = list(self._llm_buffer[:normal_count])
                    self._llm_buffer = self._llm_buffer[normal_count:]
                    for sig in normal:
                        key = getattr(sig, "admission_key", None)
                        if key and self._llm_coalesce_index.get(key) is sig:
                            self._llm_coalesce_index.pop(key, None)
                    self._llm_buffer_bytes = max(
                        0, self._llm_buffer_bytes
                        - sum(self._entry_queue_bytes(sig) for sig in normal),
                    )
                    batch.extend(normal)
                dispatched_signals = sum(
                    len(sig.get("_signal_ids") or [sig.get("signal_id", "")])
                    for sig in batch
                )
                self._llm_accounting_dispatched += dispatched_signals
                for entry in batch:
                    self._classifier_inflight[entry['_work_id']] = entry
            if self._state_file and not self._save_state():
                with self._llm_queue_lock:
                    for entry in batch:
                        self._classifier_inflight.pop(entry['_work_id'], None)
                    self._llm_priority_buffer[:0] = [e for e in batch if e.get('severity') in {'critical', 'high'}]
                    self._llm_buffer[:0] = [e for e in batch if e.get('severity') not in {'critical', 'high'}]
                    self._llm_priority_buffer_bytes = sum(self._entry_queue_bytes(e) for e in self._llm_priority_buffer)
                    self._llm_buffer_bytes = sum(self._entry_queue_bytes(e) for e in self._llm_buffer)
                    self._llm_accounting_dispatched -= dispatched_signals
                    # Re-queued entries must become coalesce targets again;
                    # their index entries were dropped at dispatch.
                    self._rebuild_llm_coalesce_index_locked()
                with self._llm_lock:
                    self._llm_active_threads -= 1
                return
            try:
                threading.Thread(
                    target=self._run_llm, args=(batch,), daemon=True,
                ).start()
            except Exception as exc:
                # The batch has already left both queues. Treat failure to
                # create its worker as a terminal classifier failure so the
                # original-signal accounting cannot remain falsely in-flight.
                # Terminal-failed work is deleted from the durable spool:
                # cross-restart accounting must not replay counted outcomes.
                with self._llm_queue_lock:
                    self._llm_accounting_failed += dispatched_signals
                    for entry in batch:
                        self._classifier_inflight.pop(entry['_work_id'], None)
                    self._discard_pending_memory_ids(batch, status="failed")
                with self._llm_lock:
                    self._llm_active_threads -= 1
                log.error("Could not start classifier worker: %s", str(exc)[:80])
                self._save_state()

    def _classification_dispatch_enabled(self) -> bool:
        """Return whether queued survivors have an available classifier path."""
        if self._llm_url:
            return True
        return self.classifier.mode == "semantic" and self.classifier.semantic is not None

    def _forget_pending_memory(self, sig: dict) -> None:
        with self._pending_memory_lock:
            self._pending_memory_ids.pop(str(sig.get("signal_id", "")), None)

    def _set_entry_classification_status(self, entry, status):
        """Mirror a queue outcome onto the linked memory evidence."""
        if self.memory_archive is None:
            return
        with self._pending_memory_lock:
            memory_ids = {
                self._pending_memory_ids.get(str(identifier))
                for identifier in entry.get("_signal_ids") or [entry.get("signal_id", "")]
            }
        with self.memory_archive._lock:
            for memory_id in memory_ids - {None}:
                memory = self.memory_archive.get(memory_id)
                if memory:
                    memory.evidence["classification_status"] = status
                    memory.last_modified_at = datetime.now(timezone.utc).isoformat()

    def _discard_pending_memory_ids(self, entries: list, *, delete_work=True,
                                    status="dropped") -> None:
        """Retire queue entries and their memory linkage consistently.

        Memory status is recorded, durable work is optionally deleted, and
        pending signal→memory links are removed so a later classification
        cannot write back to an already-discarded observation.
        """
        for entry in entries:
            self._set_entry_classification_status(entry, status)
            if delete_work:
                self._mark_durable_locked(entry, delete=True)
            with self._pending_memory_lock:
                for signal_id in entry.get("_signal_ids") or [entry.get("signal_id", "")]:
                    self._pending_memory_ids.pop(str(signal_id), None)

    def _classifier_work_snapshot(self):
        with self._llm_queue_lock:
            work = super()._classifier_work_snapshot()
            entries = {e["_work_id"]: e for e in self._llm_buffer + self._llm_priority_buffer
                       if "_work_id" in e}
            entries.update(self._classifier_inflight)
            for item in work["outstanding"]:
                entry = entries[item["work_id"]]
                item["signal_ids"] = list(entry.get("_signal_ids") or [entry["signal_id"]])
            return work

    def _checkpoint_classification_memories(self):
        """Checkpoint before queue admission/ack, outside the queue lock.

        Opt-in only: batching avoids one full archive write per classified signal.
        A failed checkpoint leaves durable work available for restart recovery.
        """
        if self._durable_queue is None or not self._state_file:
            return True
        healthy = self._save_state()
        self._durable_memory_checkpoint_healthy = bool(healthy)
        if not healthy:
            self._durable_memory_checkpoint_failures += 1
        return bool(healthy)

    def _mark_durable_locked(self, entry, *, delete=False):
        """Stage a queue-entry upsert or deletion for the durable spool."""
        if self._durable_queue is None:
            return
        identifier = entry.get("_work_id")
        if not identifier:
            return
        if delete and identifier not in self._durable_persisted_ids:
            self._durable_dirty.pop(identifier, None)
            return
        self._durable_dirty[identifier] = None if delete else entry

    def _flush_durable_locked(self):
        """Commit staged spool changes; failure stops dispatch, never RAM fallback."""
        if self._durable_queue is None or not self._durable_dirty:
            return True
        if self._durable_queue.apply(self._durable_dirty):
            for identifier, entry in self._durable_dirty.items():
                if entry is None:
                    self._durable_persisted_ids.discard(identifier)
                else:
                    self._durable_persisted_ids.add(identifier)
            self._durable_dirty.clear()
            return True
        return False

    def _expire_durable_locked(self):
        """Drop TTL-expired spool rows and their queued counterparts."""
        if self._durable_queue is None:
            return
        # Failure must stop dispatch; it must not silently fall back to RAM.
        expired = self._durable_queue.expire(protected=self._classifier_inflight)
        self._durable_persisted_ids.difference_update(expired)
        self._durable_queue.healthy = True
        for name, byte_name in (("_llm_buffer", "_llm_buffer_bytes"),
                                ("_llm_priority_buffer", "_llm_priority_buffer_bytes")):
            entries = getattr(self, name)
            dropped = [e for e in entries if e.get("_work_id") in expired]
            kept = [e for e in entries if e.get("_work_id") not in expired]
            setattr(self, name, kept)
            setattr(self, byte_name, sum(self._entry_queue_bytes(e) for e in kept))
            self._discard_pending_memory_ids(dropped, status="expired")
            self._llm_accounting_dropped += sum(
                len(e.get("_signal_ids") or [e.get("signal_id", "")]) for e in dropped
            )
            self._llm_dropped += len(dropped)
        self._rebuild_llm_coalesce_index_locked()

    def _complete_durable_restore(self):
        """Recover spooled work without increasing inference concurrency.

        Restored entries re-enter the RAM queues at their original priority.
        Entries the state snapshot already counted keep their accounting;
        entries the snapshot never saw (spool ahead of a failed snapshot)
        are counted as fresh admissions so per-signal accounting reconciles.
        """
        for entry in self._durable_staged_entries:
            signal_ids = entry.get("_signal_ids") or [entry.get("signal_id", "")]
            if (not self._accounting_restored_from_snapshot
                    or entry["_work_id"] not in self._durable_recovered_accounted):
                self._llm_accounting_enqueued += len(signal_ids)
            with self._pending_memory_lock:
                for signal_id, memory_id in (entry.get("_memory_ids") or {}).items():
                    try:
                        memory_id = UUID(memory_id)
                    except (ValueError, TypeError, AttributeError):
                        continue
                    self._pending_memory_ids[str(signal_id)] = memory_id
            entry.pop("_queue_bytes", None)
            if entry.get("severity") in {"high", "critical"}:
                self._llm_priority_buffer.append(entry)
                self._llm_priority_buffer_bytes += self._entry_queue_bytes(entry)
            else:
                self._llm_buffer.append(entry)
                self._llm_buffer_bytes += self._entry_queue_bytes(entry)
        self._durable_staged_entries = []
        with self._llm_queue_lock:
            self._rebuild_llm_coalesce_index_locked()
            self._enforce_llm_buffer_limit_locked()
            self._enforce_llm_priority_limit_locked()
            self._flush_durable_locked()


    def _estimate_llm_entry_bytes(self, entry: dict) -> int:
        """Estimate retained queue bytes, including container overhead."""
        serializable = {
            k: v for k, v in entry.items()
            if k not in {
                "_queue_bytes", "_coalesce_key", "_signal_ids", "_enqueued_at",
                "_signal_ids_bytes",
            }
        }
        try:
            encoded = json.dumps(
                serializable, separators=(",", ":"), ensure_ascii=False,
                default=str,
            ).encode("utf-8")
            internal_bytes = entry.get("_signal_ids_bytes")
            if not isinstance(internal_bytes, int) or internal_bytes < 0:
                internal_bytes = sum(
                    len(str(signal_id).encode("utf-8")) + 16
                    for signal_id in entry.get("_signal_ids", [])
                )
            return len(encoded) + internal_bytes + 320
        except (TypeError, ValueError):
            return len(str(serializable).encode("utf-8")) + 256

    def _entry_queue_bytes(self, entry: dict) -> int:
        size = entry.get("_queue_bytes")
        if not isinstance(size, int) or size < 0:
            size = self._estimate_llm_entry_bytes(entry)
            entry["_queue_bytes"] = size
        return size

    def _can_coalesce(self, entry: dict) -> bool:
        # A deployment may explicitly make a delivery attribute (e.g. signal_id)
        # part of classifier context. A fold would discard that per-member scope.
        backend = self.classifier.generative
        return (self._llm_coalescing_enabled and "_value_rule" not in entry
                and not (isinstance(backend, TemplateCachedClassifierBackend)
                         and backend.queue_context_keys))

    def _rebuild_llm_coalesce_index_locked(self) -> None:
        self._llm_coalesce_index = {
            entry.admission_key: entry for entry in self._llm_buffer
            if isinstance(entry, AdmittedQueueEntry) and entry.admission_key is not None
            and self._can_coalesce(entry)
        }

    def _llm_oldest_queue_age_seconds(self) -> float:
        with self._llm_queue_lock:
            timestamps = [
                entry.get("_enqueued_at")
                for entry in self._llm_priority_buffer + self._llm_buffer
                if entry.get("_enqueued_at")
            ]
        if not timestamps:
            return 0.0
        try:
            oldest = min(datetime.fromisoformat(value) for value in timestamps)
            return max(
                0.0,
                (datetime.now(timezone.utc) - oldest).total_seconds(),
            )
        except (TypeError, ValueError):
            return 0.0

    def _compact_llm_content(self, content: Any) -> tuple:
        """Retain only bounded classification evidence in the async queue."""
        if not isinstance(content, dict):
            content = {"message": str(content)}
        truncated = False

        def shrink(value: Any, depth: int = 0) -> Any:
            nonlocal truncated
            if depth >= 4:
                truncated = True
                return str(value)[:512]
            if isinstance(value, str):
                if len(value.encode("utf-8")) > 2048:
                    truncated = True
                    return value.encode("utf-8")[:2048].decode("utf-8", "ignore")
                return value
            if isinstance(value, dict):
                items = list(value.items())
                if len(items) > 50:
                    truncated = True
                    items = items[:50]
                return {str(k): shrink(v, depth + 1) for k, v in items}
            if isinstance(value, (list, tuple)):
                if len(value) > 20:
                    truncated = True
                return [shrink(v, depth + 1) for v in list(value)[:20]]
            if value is None or isinstance(value, (bool, int, float)):
                return value
            truncated = True
            return str(value)[:512]

        compact = shrink(content)
        encoded = json.dumps(
            compact, separators=(",", ":"), ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        if len(encoded) <= self._llm_max_content_bytes:
            return compact, truncated

        priority_keys = (
            "message", "event", "job_status", "failed", "elapsed",
            "launch_type", "host", "play", "task", "role", "playbook",
            "job_id", "current_state", "run_status", "reason",
        )
        bounded = {}
        for key in priority_keys:
            if key not in compact:
                continue
            candidate = {**bounded, key: compact[key], "_truncated": True}
            if len(json.dumps(candidate, default=str).encode("utf-8")) <= self._llm_max_content_bytes:
                bounded[key] = compact[key]
        bounded["_truncated"] = True
        if not bounded.get("message") and "message" in compact:
            budget = max(32, self._llm_max_content_bytes - 64)
            bounded["message"] = str(compact["message"]).encode("utf-8")[:budget].decode(
                "utf-8", "ignore",
            )
        return bounded, True

    def _enforce_llm_buffer_limit(self) -> None:
        """Public/test entry point for synchronized queue enforcement."""
        with self._llm_queue_lock:
            priority = [
                s for s in self._llm_buffer
                if s.get("severity") in {"critical", "high"}
            ]
            if priority:
                self._llm_buffer = [
                    s for s in self._llm_buffer
                    if s.get("severity") not in {"critical", "high"}
                ]
                self._llm_priority_buffer.extend(priority)
                self._llm_priority_buffer_bytes += sum(
                    self._entry_queue_bytes(sig) for sig in priority
                )
            self._enforce_llm_buffer_limit_locked()
            self._enforce_llm_priority_limit_locked()

    def _enforce_llm_buffer_limit_locked(self) -> None:
        self._llm_buffer_bytes = sum(
            self._entry_queue_bytes(sig) for sig in self._llm_buffer
        )
        if (len(self._llm_buffer) <= self._llm_max_buffer
                and self._llm_buffer_bytes <= self._llm_max_buffer_bytes):
            return

        kept = []
        kept_bytes = 0
        for sig in reversed(self._llm_buffer):
            size = self._entry_queue_bytes(sig)
            if (len(kept) < self._llm_max_buffer
                    and kept_bytes + size <= self._llm_max_buffer_bytes):
                kept.append(sig)
                kept_bytes += size
        kept.reverse()

        kept_ids = {id(s) for s in kept}
        dropped = [s for s in self._llm_buffer if id(s) not in kept_ids]
        self._llm_buffer = kept
        self._llm_buffer_bytes = kept_bytes
        self._rebuild_llm_coalesce_index_locked()
        self._discard_pending_memory_ids(dropped)
        self._llm_dropped += len(dropped)
        for sig in dropped:
            self._llm_dropped_by_severity[sig.get("severity", "unknown")] += 1
            self._llm_accounting_dropped += len(
                sig.get("_signal_ids") or [sig.get("signal_id", "")]
            )

        self._emit_meta(
            "meta_llm_queue_drop", "critical",
            f"LLM queue dropped {len(dropped)} signals under backpressure",
            dropped=len(dropped),
            dropped_total=self._llm_dropped,
            dropped_by_severity=dict(self._llm_dropped_by_severity),
            buffer_max=self._llm_max_buffer,
            buffer_max_bytes=self._llm_max_buffer_bytes,
            buffer_bytes=self._llm_buffer_bytes,
        )

    def _enforce_llm_priority_limit_locked(self) -> None:
        self._llm_priority_buffer_bytes = sum(
            self._entry_queue_bytes(sig) for sig in self._llm_priority_buffer
        )
        if (len(self._llm_priority_buffer) <= self._llm_priority_max_buffer
                and self._llm_priority_buffer_bytes <= self._llm_priority_max_bytes):
            return

        kept = []
        kept_bytes = 0
        ordered = (
            [s for s in self._llm_priority_buffer if s.get("severity") == "critical"]
            + [s for s in self._llm_priority_buffer if s.get("severity") == "high"]
        )
        for sig in ordered:
            size = self._entry_queue_bytes(sig)
            if (len(kept) < self._llm_priority_max_buffer
                    and kept_bytes + size <= self._llm_priority_max_bytes):
                kept.append(sig)
                kept_bytes += size

        kept_ids = {id(sig) for sig in kept}
        dropped = [
            sig for sig in self._llm_priority_buffer if id(sig) not in kept_ids
        ]
        self._llm_priority_buffer = kept
        self._llm_priority_buffer_bytes = kept_bytes
        self._discard_pending_memory_ids(dropped)
        self._llm_priority_dropped += len(dropped)
        self._llm_dropped += len(dropped)
        for sig in dropped:
            self._llm_dropped_by_severity[sig.get("severity", "unknown")] += 1
            self._llm_accounting_dropped += len(
                sig.get("_signal_ids") or [sig.get("signal_id", "")]
            )
        self._emit_meta(
            "meta_llm_priority_overflow", "critical",
            f"Protected LLM queue overflowed by {len(dropped)} signals",
            dropped=len(dropped),
            dropped_total=self._llm_priority_dropped,
            buffer_max=self._llm_priority_max_buffer,
            buffer_max_bytes=self._llm_priority_max_bytes,
        )

    def _discover_and_promote(self):
        with self._lifecycle_lock:
            return self._discover_and_promote_locked()

    def _discover_and_promote_locked(self):
        self._check_activation_ttl()
        if self._state_file and not self._state_healthy:
            return
        new_agents = self.corpus_analyzer.maybe_analyze()
        for agent_metrics, rule in new_agents:
            sig_type = agent_metrics.config.get("signal_type", "")
            pattern_type = agent_metrics.config.get("pattern_type", "")
            if not sig_type or pattern_type not in {"repeat_flood", "dominant_type"}:
                continue

            self._agent_metrics[agent_metrics.name] = agent_metrics
            self._agent_rules[agent_metrics.name] = rule
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "discovered",
                "agent": agent_metrics.name,
                "tier": "draft",
                "status": "awaiting_llm_validation",
                "config": agent_metrics.config,
            })

        for name, agent_metrics in self._agent_metrics.items():
            sig_type = agent_metrics.config.get("signal_type", "")
            pattern_type = agent_metrics.config.get("pattern_type", "")
            if (
                not sig_type
                or pattern_type not in {"repeat_flood", "dominant_type"}
                or sig_type in self._activated_types
            ):
                continue

            rule = self._agent_rules.get(name)
            record = (self._qualified_record('agent:' + name, self._rule_binding(agent_metrics, rule))
                      if rule else None)
            if record is None:
                agent_metrics.tier = 'draft'
                agent_metrics.human_approved = False
                agent_metrics.human_approved_by = None
                agent_metrics.human_approved_at = None
                continue
            noise = record['noise']
            important = record['important']
            total = noise + important
            if total == 0:
                continue

            unsafe_rate = important / total
            agent_metrics.samples_tested = total
            agent_metrics.accuracy = noise / total
            agent_metrics.false_positive_rate = unsafe_rate
            agent_metrics.false_negative_rate = unsafe_rate
            agent_metrics.coverage = 1.0
            agent_metrics.rubric_status = (
                "green"
                if total >= _PROMOTION_MIN_SAMPLES
                and important <= _PROMOTION_ALLOWED_IMPORTANT
                else "red"
            )

            previous_tier = agent_metrics.tier
            while agent_metrics.tier in {"draft", "candidate", "pending_approval"}:
                current_tier = agent_metrics.tier
                self.promotion.check_promotion(agent_metrics)
                if agent_metrics.tier == current_tier:
                    break
            if agent_metrics.tier != previous_tier:
                self._promotion_log.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "promoted",
                    "agent": name,
                    "tier": agent_metrics.tier,
                    "status": agent_metrics.rubric_status,
                    "samples": total,
                    "important": important,
                })

            if (
                agent_metrics.tier != "nano"
                or total < _PROMOTION_MIN_SAMPLES
                or important > _PROMOTION_ALLOWED_IMPORTANT
            ):
                continue

            pattern_type = agent_metrics.config["pattern_type"]
            if pattern_type == "repeat_flood":
                self._repeat_suppressor.add_signal_type(sig_type)
                self._repeat_suppressor.set_tier(
                    sig_type, *suppression_tier_for(noise))
            else:
                self._noise_suppressor.add_noise_type(sig_type)
                self._noise_suppressor.set_tier(
                    sig_type, suppression_tier_for(noise)[1])
            self._activated_types.add(sig_type)
            self._activated_patterns[sig_type] = pattern_type
            self._activation_timestamps[sig_type] = datetime.now(timezone.utc).isoformat()
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "activated",
                "agent": name,
                "tier": "nano",
                "status": "validated",
                "samples": total,
                "important": important,
                "config": agent_metrics.config,
            })
            log.info("ACTIVATED: %s (%d noise, %d important)",
                     sig_type, noise, important)
            self._emit_meta(
                "meta_agent_activated", "info",
                f"Agent activated for {sig_type} ({pattern_type})",
                agent=name, pattern_type=pattern_type,
                samples=total, noise=noise, important=important,
            )

        self._discover_contextual_noise()
        self._check_activation_ttl()
        self._refresh_suppression_tiers()
        self._flush_promotion_events()

    def _refresh_suppression_tiers(self):
        """Deepen graduated suppression as qualification evidence grows.

        Runs each promotion cycle: a type whose classified-noise record
        has crossed a tier boundary since activation suppresses more
        deeply. Withdrawal paths are unchanged — a tier never outlives
        the authority it deepens.
        """
        for kind in list(self._activated_types):
            noise = self._llm_noise_counts.get(kind)
            if type(noise) is not int:
                continue
            pattern = self._activated_patterns.get(kind, "repeat_flood")
            if pattern == "repeat_flood":
                self._repeat_suppressor.set_tier(
                    kind, *suppression_tier_for(noise))
            else:
                self._noise_suppressor.set_tier(
                    kind, suppression_tier_for(noise)[1])

    def _discover_contextual_noise(self):
        """Promote (signal_type, context) pairs as contextual noise.

        When a type has mixed importance overall (can't be promoted),
        check if specific contexts within it are pure noise. Discovery
        iterates all configured context keys; each pure (type, value)
        pair is added to the suppressor whose context key matches the
        signal attribute that produced it.
        """
        _CTX_MIN_SAMPLES = _PROMOTION_MIN_SAMPLES
        _CTX_MAX_IMPORTANT = 0

        # Classification workers update these maps concurrently.  Iterate over
        # a snapshot so bounded-map pruning cannot invalidate the iterator.
        with self._context_lock:
            context_noise = list(self._llm_context_noise.items())
            context_important = dict(self._llm_context_important)

        # Map from context value to the suppressor that handles it.
        # Each suppressor checks one signal attribute; we try each key
        # to find which one this context value belongs to.
        suppressor_by_key = {s._context_key: s for s in self._contextual_suppressors}

        for ctx_key, noise_count in context_noise:
            sig_type, ctx_value = ctx_key.split(":", 1)
            if not ctx_value:
                continue
            if sig_type in self._activated_types:
                continue
            if ctx_value in self._activated_contexts.get(sig_type, set()):
                continue

            record = self._qualified_record('context:' + ctx_key, self._context_binding(ctx_key))
            if record is None:
                continue
            noise_count, important = record['noise'], record['important']
            total = noise_count + important
            if total < _CTX_MIN_SAMPLES:
                continue
            important_rate = important / total
            if important > _CTX_MAX_IMPORTANT:
                continue
            if self._human_gate:
                # Contextual activation does not yet have a separately scoped
                # approval object. A configured human gate therefore keeps it
                # inactive rather than silently bypassing approval.
                continue

            # Find which suppressor handles this context value: try each
            # registered context key and check if the value matches the
            # signal attribute for that key. Default to the primary.
            target_suppressor = self._contextual_suppressor
            for s in self._contextual_suppressors:
                # The context value came from this suppressor's key if it
                # differs from the namespace (the default) or if this is
                # the only suppressor. For multi-key, the identity format
                # from the lifecycle is always "{type}:{value}" — we need
                # to check which key produced this value.
                if s._context_key == "namespace" and ctx_value != "":
                    target_suppressor = s
                    break
                # For non-namespace keys, we can't distinguish from the
                # identity alone; use the first suppressor that isn't
                # already handling this type.
                if s._context_key != "namespace":
                    target_suppressor = s
                    break

            identity = f"{sig_type}:{ctx_value}"
            agent_name = (
                "contextual_noise_"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
            )
            metrics = self._agent_metrics.get(agent_name)
            if metrics is None:
                metrics = AgentMetrics(
                    name=agent_name,
                    config={
                        "signal_type": sig_type,
                        "pattern_type": "contextual_noise",
                        "context_key": self._contextual_suppressor._context_key,
                        "context_value": ctx_value,
                    },
                )
                self._agent_metrics[agent_name] = metrics
                self._agent_rules[agent_name] = RuleAgent({'name': agent_name, 'signal_types': [sig_type]})
            metrics.samples_tested = total
            metrics.accuracy = noise_count / total
            metrics.false_positive_rate = important_rate
            metrics.false_negative_rate = important_rate
            metrics.coverage = 1.0
            metrics.rubric_status = "green"
            while metrics.tier in {"draft", "candidate"}:
                previous = metrics.tier
                self.promotion.check_promotion(metrics)
                if metrics.tier == previous:
                    break
            if metrics.tier != "nano":
                continue

            with self._contexts_lock:
                target_suppressor.add_noise_context(sig_type, ctx_value)
                self._activated_contexts[sig_type].add(ctx_value)
            self._context_activation_timestamps[identity] = datetime.now(
                timezone.utc
            ).isoformat()
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "contextual_activated",
                "agent": f"ctx_{sig_type}_{ctx_value}",
                "tier": "nano",
                "status": "validated",
                "samples": total,
                "important": important,
                "important_rate": round(important_rate, 4),
                "context_key": target_suppressor._context_key,
                "context_value": ctx_value,
            })
            log.info("CONTEXTUAL ACTIVATION: %s in %s=%s (%d samples, %.1f%% important)",
                     sig_type, target_suppressor._context_key,
                     ctx_value, total, important_rate * 100)

    def _check_activation_ttl(self):
        """Suspend agents whose activation has expired."""
        with self._lifecycle_lock:
            return self._check_activation_ttl_locked()

    def _check_activation_ttl_locked(self):
        if self._state_file and not self._state_healthy:
            self._fence_learning('unhealthy state must not suppress adaptively')
            return
        for kind in list(self._activated_types):
            if not self._restored_authority_valid(kind, self._activated_patterns.get(kind)):
                self._deactivate_agent(kind)
                for name, metrics in self._agent_metrics.items():
                    if metrics.config.get('signal_type') == kind:
                        metrics.last_batch_fn_count = 0
                        self.promotion.demote(metrics, reason='activation evidence, configuration or TTL no longer valid')
                        self.promotion.reactivate(metrics)
                        self._qualification_evidence.pop('agent:' + name, None)
                self._llm_noise_counts[kind] = 0
                self._llm_important_counts[kind] = 0
        for kind, contexts in list(self._activated_contexts.items()):
            for context in list(contexts):
                identity = f'{kind}:{context}'
                record = self._qualified_record('context:' + identity, self._context_binding(identity))
                if (record is None or record['noise'] < 200 or record['important'] != 0
                        or not valid_time(self._context_activation_timestamps.get(identity), self._activation_ttl_hours)):
                    contexts.discard(context)
                    for s in self._contextual_suppressors: s.remove_noise_context(kind, context)
                    self._context_activation_timestamps.pop(identity, None)
                    self._qualification_evidence.pop('context:' + identity, None)
                    self._llm_context_noise[identity] = 0
                    self._llm_context_important[identity] = 0
                    for metrics in self._agent_metrics.values():
                        if (metrics.config.get('pattern_type') == 'contextual_noise'
                                and metrics.config.get('signal_type') == kind
                                and metrics.config.get('context_value') == context):
                            metrics.last_batch_fn_count = 0
                            self.promotion.demote(metrics, reason='context evidence, configuration or TTL no longer valid')
                            self.promotion.reactivate(metrics)
        if (
            not self._activation_timestamps
            and not self._context_activation_timestamps
        ) or self._activation_ttl_hours <= 0:
            return

        now = datetime.now(timezone.utc)
        expired = {}
        for sig_type, ts_str in list(self._activation_timestamps.items()):
            try:
                activated_at = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                )
                if activated_at.tzinfo is None:
                    raise ValueError("activation timestamp lacks timezone")
                age_hours = (now - activated_at).total_seconds() / 3600
                if age_hours < 0 or age_hours >= self._activation_ttl_hours:
                    expired[sig_type] = "ttl_expired"
            except (AttributeError, ValueError, TypeError):
                expired[sig_type] = "invalid_activation_timestamp"

        for sig_type, expiry_reason in expired.items():
            for name, metrics in self._agent_metrics.items():
                if (
                    metrics.config.get("signal_type") == sig_type
                    and metrics.config.get("pattern_type")
                    in {"repeat_flood", "dominant_type"}
                    and metrics.tier in {"nano", "micro", "macro"}
                ):
                    self.promotion.demote(
                        metrics,
                        reason=(
                            f"activation TTL expired ({self._activation_ttl_hours}h)"
                            if expiry_reason == "ttl_expired"
                            else "activation timestamp was invalid"
                        ),
                    )
                    self.promotion.reactivate(metrics)
                    metrics.samples_tested = 0
                    log.warning("Agent '%s' TTL expired for %s — must re-qualify",
                                name, sig_type)
                    self._emit_meta(
                        "meta_agent_demoted", "high",
                        f"Agent '{name}' TTL expired for {sig_type}",
                        agent=name, related_type=sig_type,
                        reason=expiry_reason,
                    )
                    break
            self._deactivate_agent(sig_type)
            self._activation_timestamps.pop(sig_type, None)
            self._llm_noise_counts[sig_type] = 0
            self._llm_important_counts[sig_type] = 0
            for name, metrics in self._agent_metrics.items():
                if metrics.config.get('signal_type') == sig_type:
                    self._qualification_evidence.pop('agent:' + name, None)

        expired_contexts = {}
        for identity, ts_str in list(self._context_activation_timestamps.items()):
            try:
                activated_at = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                )
                if activated_at.tzinfo is None:
                    raise ValueError("activation timestamp lacks timezone")
                age_hours = (now - activated_at).total_seconds() / 3600
                if age_hours < 0 or age_hours >= self._activation_ttl_hours:
                    expired_contexts[identity] = "ttl_expired"
            except (AttributeError, ValueError, TypeError):
                expired_contexts[identity] = "invalid_activation_timestamp"

        with self._contexts_lock:
            for identity, expiry_reason in expired_contexts.items():
                sig_type, ctx_value = identity.split(":", 1)
                for s in self._contextual_suppressors: s.remove_noise_context(sig_type, ctx_value)
                self._activated_contexts[sig_type].discard(ctx_value)
                self._context_activation_timestamps.pop(identity, None)
                self._llm_context_noise[identity] = 0
                self._llm_context_important[identity] = 0
                self._qualification_evidence.pop('context:' + identity, None)
                for metrics in self._agent_metrics.values():
                    if (
                        metrics.config.get("pattern_type") == "contextual_noise"
                        and metrics.config.get("signal_type") == sig_type
                        and metrics.config.get("context_value") == ctx_value
                        and metrics.tier in {"nano", "micro", "macro"}
                    ):
                        self.promotion.demote(
                            metrics,
                            reason=(
                                f"context activation TTL expired ({self._activation_ttl_hours}h)"
                                if expiry_reason == "ttl_expired"
                                else "context activation timestamp was invalid"
                            ),
                        )
                        self.promotion.reactivate(metrics)
                        break
                self._emit_meta(
                    "meta_agent_demoted", "high",
                    f"Contextual agent expired for {sig_type}",
                    related_type=sig_type, reason=expiry_reason,
                )

    def _classify_one(self, sig, client):
        with self._lifecycle_lock:
            sig['_qualification_bindings'] = self._qualification_bindings(sig)
        return self.classifier.classify(sig, client)

    def _classify_entry(self, sig, client):
        """Classify one queue entry: value-rule labels are authoritative;
        everything else goes through the semantic admission budget."""
        rule = sig.get("_value_rule")
        # A normalized severity alone cannot authenticate a restored rule label.
        if not severity_input_is_valid(sig.get("severity"), sig.get("severity_input_valid", rule is None)):
            from .classifier import ClassificationResult
            return ClassificationResult(label="needs_attention", backend="input_validation",
                                        status="error", error_code="invalid_severity",
                                        error="raw severity is invalid or unverified")
        if rule is not None:
            return _value_rule_classification(rule)
        return self._classify_one_limited(sig, client)

    def _classify_one_limited(self, sig, client):
        """Classify within the bridge-wide semantic admission budget."""
        with self._llm_slots:
            with self._llm_inflight_lock:
                self._llm_inflight += 1
                self._llm_peak_inflight = max(
                    self._llm_peak_inflight, self._llm_inflight,
                )
            try:
                return self._classify_one(sig, client)
            finally:
                with self._llm_inflight_lock:
                    self._llm_inflight -= 1

    def _build_evidence_bundle(self, sig: dict) -> dict:
        bundle = {"signal": sig, "related_memories": [], "causal_chain": [], "entity_context": []}
        if not self.memory_archive:
            return bundle
        try:
            from .cascade.recall import RecallEngine
            from .cascade.protocol import Signal
            query_signal = Signal(
                signal_type=sig.get("signal_type", ""),
                severity=sig.get("severity", "info"),
                severity_input_valid=sig.get("severity_input_valid", False) is True,
                source="", namespace=sig.get("namespace", ""),
                content=sig.get("content", {}), labels={},
            )
            engine = RecallEngine()
            results = engine.recall(query_signal, archive=self.memory_archive, top_k=5, reinforce=False)
            bundle["related_memories"] = [
                {"signal_type": r.memory.signal.signal_type,
                 "strength": round(r.memory.strength, 3),
                 "message": r.memory.signal.content.get("message", "")[:150],
                 "score": round(r.score, 3)}
                for r in results
            ]
        except Exception as e:
            log.debug("Evidence bundle recall failed: %s", str(e)[:60])
        return bundle

    def _escalate_to_gpu(self, sig: dict, classification: str, client):
        if not self._gpu_url:
            return None
        try:
            bundle = self._build_evidence_bundle(sig)
            memories_text = "\n".join(
                f"- [{m['signal_type']}] (str={m['strength']}) {m['message']}"
                for m in bundle.get("related_memories", [])
            ) or "No related memories found."

            prompt = (
                f"Analyze this infrastructure signal with full context.\n\n"
                f"Signal: {sig['signal_type']} {sig['severity']}\n"
                f"Message: {sig.get('content', {}).get('message', '')}\n"
                f"Namespace: {sig.get('namespace', '')}\n"
                f"CPU classification: {classification}\n\n"
                f"Related memories (what the platform remembers):\n{memories_text}\n\n"
                f"Respond in JSON:\n"
                f'{{"root_cause": "...", "impact": "...", '
                f'"remediation": ["step1", "step2"], "confidence": 0.0-1.0}}'
            )

            t0 = time.monotonic()
            raw = llm_complete(
                client, self._gpu_url, self._gpu_key, self._gpu_model,
                [{"role": "system", "content": "You are an infrastructure analyst. Analyze signals and provide structured root cause analysis in JSON."},
                 {"role": "user", "content": prompt}],
                max_tokens=500,
            )
            ms = (time.monotonic() - t0) * 1000

            try:
                analysis = json.loads(raw)
            except json.JSONDecodeError:
                analysis = {"raw_response": raw}

            analysis["model"] = self._gpu_model
            analysis["latency_ms"] = round(ms)
            analysis["signal_type"] = sig["signal_type"]
            analysis["severity"] = sig.get("severity", "")
            analysis["namespace"] = sig.get("namespace", "")
            analysis["classification"] = classification
            analysis["timestamp"] = datetime.now(timezone.utc).isoformat()
            analysis["evidence_bundle_size"] = len(bundle.get("related_memories", []))

            self._gpu_analyses.append(analysis)
            if len(self._gpu_analyses) > 500:
                self._gpu_analyses = self._gpu_analyses[-500:]

            if self._gpu_analyses_file:
                try:
                    self._append_gpu_analysis(analysis)
                except Exception as e:
                    log.debug("GPU analysis file write failed: %s", str(e)[:60])

            log.info("GPU analysis: %s → %s (%.0fms, model=%s)",
                     sig["signal_type"], analysis.get("root_cause", "?")[:50], ms, self._gpu_model)

            return analysis
        except Exception as e:
            log.debug("GPU escalation failed: %s", str(e)[:60])
            return None

    _GPU_MAX_LINES = 10000
    _CONTEXT_MAP_MAX = 5000
    _CONTEXT_MAP_PRUNE_AT = 5500

    def _prune_context_map(self, ctx_map: dict) -> dict:
        if len(ctx_map) <= self._CONTEXT_MAP_MAX:
            return ctx_map
        sorted_keys = sorted(ctx_map, key=ctx_map.get, reverse=True)
        return {k: ctx_map[k] for k in sorted_keys[:self._CONTEXT_MAP_MAX]}

    def _increment_context_count(self, ctx_map: dict, key: str) -> None:
        """Increment and periodically prune a live classification context map."""
        with self._context_lock:
            ctx_map[key] += 1
            if len(ctx_map) > self._CONTEXT_MAP_PRUNE_AT:
                pruned = self._prune_context_map(ctx_map)
                ctx_map.clear()
                ctx_map.update(pruned)

    def _append_gpu_analysis(self, analysis: dict):
        import fcntl
        path = self._gpu_analyses_file
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(analysis) + "\n")
        try:
            with open(path) as f:
                lines = f.readlines()
            if len(lines) > self._GPU_MAX_LINES:
                keep = lines[-self._GPU_MAX_LINES:]
                with open(path, "w") as f:
                    f.writelines(keep)
                log.info("GPU analyses rotated: %d → %d lines", len(lines), len(keep))
        except Exception:
            pass
            fcntl.flock(f, fcntl.LOCK_UN)

    def _observe_classification(self, sig, classification):
        """Record the inference once, including results waiting for audit capacity."""
        ans = classification.label
        ms = classification.latency_ms
        if not isinstance(ms, (int, float)) or not math.isfinite(ms) or ms < 0:
            ms = None
        model = classification.model_revision
        sev = sig.get("severity", "medium")
        if sig.get("_value_rule"):
            tier = "nano"
            self.stats.value_rule_classified += 1
        else:
            self.stats.llm_classified += 1
            tier = "macro" if sev in ("critical", "high") else "micro"
            if not classification.cache_hit:
                self._llm_calls_by_tier[tier] += 1
                if ms is not None:
                    self._llm_seconds_by_tier[tier] += ms / 1000.0

        self._llm_results.append({
            "signal_type": sig["signal_type"],
            "classification": ans,
            "latency_ms": round(ms) if ms is not None else None,
            "model": model,
            "tier": tier,
            "classifier_backend": classification.authoritative_backend,
            "cache_hit": classification.cache_hit,
            "classifier_mode": self.classifier.mode,
            "occurrence_count": len(
                sig.get("_signal_ids") or [sig.get("signal_id", "")]
            ),
            "semantic_margin": (
                classification.alternatives.get("semantic").margin
                if classification.alternatives.get("semantic") else None
            ),
        })
        if len(self._llm_results) > 1000:
            self._llm_results = self._llm_results[-1000:]

    def _store_gpu_analysis(self, sig, label, client):
        analysis = self._escalate_to_gpu(sig, label, client)
        if analysis and self.memory_archive:
            signal = Signal(
                signal_type=sig["signal_type"], severity=sig.get("severity", "info"),
                severity_input_valid=sig.get("severity_input_valid", False) is True,
                source="", namespace=sig.get("namespace", ""),
                content=sig.get("content", {}), labels={},
            )
            self.memory_archive.store(signal, classification=label, metadata={"analysis": analysis})

    def _run_llm(self, signals):
        # The spool owns eligibility. A stale worker invocation after a lost
        # local ack must not issue another inference or consume a second result.
        owned = set()
        claimed = []
        with self._lifecycle_lock, self._llm_queue_lock:
            for entry in signals:
                work_id = entry.get("_work_id")
                if work_id:
                    if (work_id in self._executing_work_ids or work_id in self._completion_pending
                            or work_id in self._completion_ack
                            or (self._durable_queue is not None and work_id not in self._durable_persisted_ids)):
                        continue
                    owned.add(work_id)
                    self._executing_work_ids.add(work_id)
                claimed.append(entry)
        remaining = {id(entry): entry for entry in claimed}

        def failed(entry, error):
            with self._llm_queue_lock:
                self._llm_accounting_failed += len(entry.get("_signal_ids") or [entry.get("signal_id")])
                self._set_entry_classification_status(entry, "failed")
                self._classifier_inflight.pop(entry.get("_work_id"), None)
            kind = entry.get("signal_type", "unknown")
            self._llm_failure_counts[kind] += 1
            log.warning("Classification failed for %s: %s", kind, str(error)[:80])

        try:
            import httpx
            from concurrent.futures import as_completed

            llm_workers = int(os.getenv("CASCADE_LLM_WORKERS", "8"))
            with httpx.Client(timeout=60) as client, ThreadPoolExecutor(max_workers=llm_workers) as pool:
                futures = {pool.submit(self._classify_entry, entry, client): entry for entry in claimed}
                for future in as_completed(futures):
                    sig = futures[future]
                    remaining.pop(id(sig))
                    try:
                        classification = future.result()
                        if not classification.ok:
                            raise ValueError(classification.error_code or classification.error)
                    except Exception as exc:
                        failed(sig, exc)
                        continue
                    try:
                        self._observe_classification(sig, classification)
                    except (TypeError, ValueError, AttributeError) as exc:
                        log.warning("Could not record classifier telemetry: %s", exc)
                    if not completion.retain(self, sig, classification):
                        # Retained or fenced. No failure receipt, ack or inference retry here.
                        continue
                    ans = classification.label
                    if self._gpu_url and not _is_noise_classification(ans):
                        try:
                            self._store_gpu_analysis(sig, ans, client)
                        except Exception as exc:
                            log.warning("Could not attach optional GPU analysis: %s", exc)


        except Exception as exc:
            log.warning("Classifier worker error: %s", exc)
            for entry in remaining.values():
                failed(entry, exc)
        finally:
            try:
                self._save_state()
                self._flush_memory_events()
            finally:
                with self._llm_queue_lock:
                    self._executing_work_ids.difference_update(owned)
                with self._llm_lock:
                    self._llm_active_threads -= 1
            self._dispatch_llm_batches()

    def _queue_shadow_samples(self, cascade_result, cascade_signals):
        """Sample suppressed signals from activated agents for LLM re-check.

        Covers both type-level and contextual suppressions.
        """
        signal_map = {s.signal_id: s for s in cascade_signals}
        candidates = []
        for d in cascade_result.decisions:
            if d.outcome.value not in ("suppress", "drop"):
                continue
            sig = signal_map.get(d.signal_id)
            if not sig:
                continue
            if sig.signal_type in self._activated_types:
                candidates.append(sig)
            elif sig.signal_type in self._activated_contexts:
                ctx = " ".join(str(getattr(sig, s._context_key, "")) for s in self._contextual_suppressors if getattr(sig, s._context_key, ""))
                if ctx in self._activated_contexts[sig.signal_type]:
                    candidates.append(sig)

        if not candidates or self._shadow_sample_rate <= 0:
            return

        sample_size = max(1, int(len(candidates) * self._shadow_sample_rate))
        sample = random.sample(candidates, min(sample_size, len(candidates)))
        for sig in sample:
            self._shadow_buffer.append({
                "signal_type": sig.signal_type,
                "severity": sig.severity,
                "namespace": sig.namespace,
                "source": sig.source,
                "cluster": sig.cluster,
                "labels": dict(sig.labels or {}),
                "content": sig.content,
            })
        if len(self._shadow_buffer) > self._shadow_max_buffer:
            self._shadow_buffer = self._shadow_buffer[-self._shadow_max_buffer:]

    def _run_shadow_llm(self, signals):
        """Re-classify suppressed signals via independent model.

        Uses the GPU model (different from micro/macro) when available
        to break circular validation. Falls back to macro model.
        """
        self._shadow_running = True
        try:
            import httpx
            shadow_url = self._gpu_url or self._llm_url
            shadow_key = self._gpu_key or self._llm_key
            shadow_model = self._gpu_model if self._gpu_url else self._macro_model
            with httpx.Client(timeout=60) as client:
                for sig in signals:
                    text = serialize_signal(sig)
                    try:
                        ans = llm_complete(
                            client, shadow_url, shadow_key, shadow_model,
                            [{"role": "system", "content": self._system_prompt},
                             {"role": "user", "content": text}],
                        ).lower()
                        self._shadow_checks += 1

                        if not _is_noise_classification(ans):
                            self._shadow_demotions += 1
                            ns = sig.get("namespace", "")
                            ctx_key = self._contextual_suppressors[0]._context_key

                            if (sig["signal_type"] in self._activated_contexts
                                    and ns in self._activated_contexts.get(sig["signal_type"], set())):
                                with self._contexts_lock:
                                    for s in self._contextual_suppressors: s.remove_noise_context(sig["signal_type"], ns)
                                    self._activated_contexts[sig["signal_type"]].discard(ns)
                                identity = f"{sig['signal_type']}:{ns}"
                                self._context_activation_timestamps.pop(identity, None)
                                self._llm_context_noise[identity] = 0
                                self._llm_context_important[identity] = 0
                                for metrics in self._agent_metrics.values():
                                    if (
                                        metrics.config.get("pattern_type")
                                        == "contextual_noise"
                                        and metrics.config.get("signal_type")
                                        == sig["signal_type"]
                                        and metrics.config.get("context_value") == ns
                                        and metrics.tier in {
                                            "nano", "micro", "macro",
                                        }
                                    ):
                                        metrics.last_batch_fn_count = 1
                                        self.promotion.demote(
                                            metrics,
                                            reason="FN caught by shadow validation",
                                        )
                                        self._flush_promotion_events()
                                        break
                                log.warning(
                                    "SHADOW: contextual FN — %s in %s=%s is important "
                                    "(classified '%s'), context demoted",
                                    sig["signal_type"], ctx_key, ns, ans,
                                )
                            else:
                                self.record_feedback(
                                    sig["signal_type"],
                                    was_suppressed=True,
                                    is_important=True,
                                )
                                log.warning(
                                    "SHADOW: type-level FN — %s is important "
                                    "(classified '%s'), agent demoted",
                                    sig["signal_type"], ans,
                                )
                            self._emit_meta(
                                "meta_shadow_demotion", "critical",
                                f"Shadow caught FN: {sig['signal_type']} "
                                f"in {ctx_key}={ns} classified '{ans}'",
                                related_type=sig["signal_type"],
                                context_value=ns,
                                llm_classification=ans,
                            )

                    except Exception as e:
                        log.debug("Shadow LLM call failed: %s", str(e)[:60])
        except Exception as e:
            log.warning("Shadow LLM error: %s", e)
        finally:
            self._shadow_running = False

    def _submit_ledger_write(self, func, *args) -> bool:
        """Submit ledger work without allowing an unbounded thread backlog."""
        if not self._ledger_slots.acquire(blocking=False):
            self._ledger_writes_dropped += 1
            self._emit_meta(
                "meta_ledger_backpressure", "medium",
                "Ledger write dropped because the bounded executor is full",
                dropped_total=self._ledger_writes_dropped,
                max_pending=self._ledger_max_pending,
            )
            return False

        def run():
            try:
                func(*args)
            finally:
                self._ledger_slots.release()

        try:
            self._ledger_executor.submit(run)
            return True
        except RuntimeError:
            self._ledger_slots.release()
            self._ledger_writes_dropped += 1
            return False

    def _write_to_ledger(self, cascade_result, cascade_signals):
        try:
            from .integrations.ledger import write_decisions
            write_decisions(
                self._ledger_url, self._ledger_token,
                cascade_result, cascade_signals, self.domain,
            )
        except Exception as e:
            log.debug("Ledger write failed: %s", str(e)[:60])

    def _enqueue_decisions_to_ledger(self, cascade_result, cascade_signals):
        from .integrations.ledger import build_decision_entry
        entry = build_decision_entry(
            cascade_result, cascade_signals, self.domain,
        )
        if entry:
            self._enqueue_ledger_entry(entry)

    def _enqueue_ledger_entry(self, entry: dict) -> bool:
        """Persist a receipt before delivery when a spool is configured."""
        if not self._ledger_receipt_store:
            from .integrations.ledger import post_entry
            return self._submit_ledger_write(
                post_entry, self._ledger_url, self._ledger_token, entry,
            )
        rejected = self._ledger_receipt_store.enqueue([entry])
        if rejected:
            self._ledger_writes_dropped += rejected
            self._emit_meta(
                "meta_ledger_backpressure", "high",
                "Durable ledger receipt spool rejected new work at its configured bound; queued undelivered records were preserved",
                rejected=rejected,
                dropped_total=self._ledger_writes_dropped,
                pending_max=self._ledger_receipt_pending_max,
            )
        self._start_ledger_receipt_drain()
        return rejected == 0

    def _ledger_receipt_pending_count(self) -> int:
        return self._ledger_receipt_store.count() if self._ledger_receipt_store else 0

    def _start_ledger_receipt_drain(self):
        if self._closed or not self._ledger_receipt_store:
            return
        with self._ledger_receipt_lock:
            if (
                self._closed
                or self._ledger_receipt_flush_running
                or not self._ledger_receipt_pending_count()
            ):
                return
            self._ledger_receipt_flush_running = True
        try:
            self._ledger_executor.submit(self._drain_ledger_receipt_queue)
        except RuntimeError:
            with self._ledger_receipt_lock:
                self._ledger_receipt_flush_running = False

    def _schedule_ledger_receipt_retry(self):
        with self._ledger_receipt_lock:
            existing = self._ledger_receipt_retry_timer
            if (
                self._closed
                or not self._ledger_receipt_store
                or not self._ledger_receipt_pending_count()
                or (existing is not None and existing.is_alive())
            ):
                return
            delay = min(
                self._ledger_memory_retry_max,
                self._ledger_memory_retry_initial
                * (2 ** max(0, self._ledger_receipt_consecutive_failures - 1)),
            )
            timer = threading.Timer(delay, self._retry_ledger_receipts)
            timer.daemon = True
            self._ledger_receipt_retry_timer = timer
        timer.start()

    def _retry_ledger_receipts(self):
        with self._ledger_receipt_lock:
            self._ledger_receipt_retry_timer = None
        self._start_ledger_receipt_drain()

    def _drain_ledger_receipt_queue(self):
        try:
            from .integrations.ledger import post_entry
            while True:
                with self._ledger_receipt_lock:
                    queued = self._ledger_receipt_store.peek(1)
                    if not queued:
                        self._ledger_receipt_flush_running = False
                        return
                    item = queued[0]
                if not post_entry(
                    self._ledger_url, self._ledger_token, item.payload,
                ):
                    with self._ledger_receipt_lock:
                        self._ledger_receipt_flush_running = False
                        self._ledger_receipt_failures += 1
                        self._ledger_receipt_consecutive_failures += 1
                    self._schedule_ledger_receipt_retry()
                    return
                with self._ledger_receipt_lock:
                    self._ledger_receipt_store.acknowledge([item.sequence])
                    self._ledger_receipts_written += 1
                    self._ledger_receipt_consecutive_failures = 0
                    self._ledger_receipt_last_success = datetime.now(
                        timezone.utc
                    ).isoformat()
        except Exception as e:
            log.debug("Ledger receipt delivery failed: %s", str(e)[:60])
            with self._ledger_receipt_lock:
                self._ledger_receipt_flush_running = False
                self._ledger_receipt_failures += 1
                self._ledger_receipt_consecutive_failures += 1
            self._schedule_ledger_receipt_retry()

    def _flush_promotion_events(self):
        events = self.promotion.drain_events()
        if not events:
            return
        for event in events:
            self._promotion_log.append({
                "timestamp": event.timestamp,
                "event": event.event_type,
                "agent": event.agent_name,
                "from_tier": event.from_tier,
                "to_tier": event.to_tier,
                "reason": event.reason,
                "evidence": event.to_dict()["evidence"],
            })
            if not self._ledger_url:
                continue
            if self._ledger_receipt_store:
                from .integrations.ledger import build_promotion_entry
                self._enqueue_ledger_entry(
                    build_promotion_entry(event.to_dict(), self.domain)
                )
            else:
                self._submit_ledger_write(
                    self._write_promotion_to_ledger, event.to_dict(),
                )

    def _write_promotion_to_ledger(self, event_dict):
        try:
            from .integrations.ledger import write_promotion_event
            write_promotion_event(
                self._ledger_url, self._ledger_token,
                event_dict, self.domain,
            )
        except Exception as e:
            log.debug("Promotion ledger write failed: %s", str(e)[:60])

    def _flush_memory_events(self):
        healthy = self._stage_memory_events()
        self._start_memory_ledger_drain()
        return healthy

    def _memory_source_pending(self):
        return self.memory_archive.pending_event_count() if self.memory_archive else 0

    def _stage_memory_events(self):
        if not self.memory_archive or not self._ledger_url:
            return True
        # Keep the same lock order as process/snapshot. The source checkpoint
        # precedes removal; crashes can replay a prefix, never silently lose it.
        with self._lifecycle_lock, self._ledger_memory_lock, self._llm_queue_lock, self.memory_archive._lock:
            if not self._memory_source_pending():
                return True
            try:
                if self._state_file and not self._save_state():
                    raise RuntimeError("memory event checkpoint failed")
                def accept(events):
                    if self._ledger_memory_store:
                        return self._ledger_memory_store.accept_memory_prefix(
                            events, self.memory_archive._handoff_id, self.memory_archive._handoff_offset,
                        )
                    capacity = max(0, self._ledger_memory_pending_max - len(self._ledger_memory_pending))
                    used = sum(len(json.dumps(e).encode()) for e in self._ledger_memory_pending)
                    count = 0
                    for event in events[:capacity]:
                        used += len(json.dumps(event).encode())
                        if used > self._ledger_memory_bytes_max:
                            break
                        count += 1
                    self._ledger_memory_pending.extend(events[:count])
                    return count
                moved = self.memory_archive.transfer_events(accept, limit=self._ledger_memory_batch_size)
                if moved and self._state_file and not self._save_state():
                    raise OSError("memory handoff checkpoint failed")
                self._ledger_memory_handoff_healthy = True
                return True
            except Exception:
                self._ledger_memory_handoff_failures += 1
                self._ledger_memory_handoff_healthy = False
                self._emit_meta("meta_ledger_memory_overflow", "high",
                                "Memory event handoff failed; source retained for retry")
                return False

    def _ledger_memory_pending_count(self) -> int:
        if self._ledger_memory_store:
            return self._ledger_memory_store.count()
        return len(self._ledger_memory_pending)

    def _start_memory_ledger_drain(self):
        with self._ledger_memory_lock:
            if (
                self._closed
                or not self._ledger_url
                or self._ledger_memory_flush_running
                or not (self._ledger_memory_pending_count() or self._memory_source_pending())
            ):
                return
            self._ledger_memory_flush_running = True
        try:
            self._ledger_memory_executor.submit(self._drain_memory_ledger_queue)
        except RuntimeError:
            with self._ledger_memory_lock:
                self._ledger_memory_flush_running = False

    def _schedule_memory_ledger_retry(self):
        with self._ledger_memory_lock:
            existing = self._ledger_memory_retry_timer
            if (
                self._closed
                or not self._ledger_memory_store
                or not (self._ledger_memory_pending_count() or self._memory_source_pending())
                or (existing is not None and existing.is_alive())
            ):
                return
            delay = min(
                self._ledger_memory_retry_max,
                self._ledger_memory_retry_initial
                * (2 ** min(60, max(0, self._ledger_memory_consecutive_failures - 1))),
            )
            timer = threading.Timer(delay, self._retry_memory_ledger)
            timer.daemon = True
            self._ledger_memory_retry_timer = timer
        timer.start()

    def _retry_memory_ledger(self):
        with self._ledger_memory_lock:
            self._ledger_memory_retry_timer = None
        self._start_memory_ledger_drain()

    def _drain_memory_ledger_queue(self):
        try:
            from .integrations.ledger import write_memory_events
            while True:
                self._stage_memory_events()
                completion.resume(self)
                empty = False
                with self._ledger_memory_lock:
                    if not self._ledger_memory_pending_count():
                        self._ledger_memory_flush_running = False
                        empty = True
                    elif self._ledger_memory_store:
                        queued = self._ledger_memory_store.memory_delivery_batch(
                            self._ledger_memory_batch_size
                        )
                        batch = [item.payload for item in queued]
                    else:
                        queued = []
                        if not self._ledger_memory_batch_count:
                            self._ledger_memory_batch_count = min(
                                len(self._ledger_memory_pending), self._ledger_memory_batch_size)
                        batch = self._ledger_memory_pending[:self._ledger_memory_batch_count]
                if empty:
                    self._schedule_memory_ledger_retry()
                    return
                if not self._ledger_url or not write_memory_events(
                    self._ledger_url, self._ledger_token, batch, self.domain,
                ):
                    with self._ledger_memory_lock:
                        self._ledger_memory_flush_running = False
                        self._ledger_memory_failures += 1
                        self._ledger_memory_consecutive_failures += 1
                    self._schedule_memory_ledger_retry()
                    return
                with self._ledger_memory_lock:
                    if self._ledger_memory_store:
                        deleted = self._ledger_memory_store.acknowledge(item.sequence for item in queued)
                        if deleted != len(queued):
                            raise OSError("memory ledger acknowledgment count mismatch")
                    else:
                        del self._ledger_memory_pending[:len(batch)]
                        self._ledger_memory_batch_count = 0
                    self._ledger_memory_batches_written += 1
                    self._ledger_memory_consecutive_failures = 0
                    self._ledger_memory_last_success = datetime.now(
                        timezone.utc
                    ).isoformat()
        except Exception as e:
            log.debug("Memory ledger batch failed: %s", str(e)[:60])
            with self._ledger_memory_lock:
                self._ledger_memory_flush_running = False
                self._ledger_memory_failures += 1
                self._ledger_memory_consecutive_failures += 1
            self._schedule_memory_ledger_retry()

    def _deactivate_agent(self, signal_type: str):
        pattern_type = self._activated_patterns.pop(signal_type, None)
        self._activated_types.discard(signal_type)
        self._repeat_suppressor.remove_signal_type(signal_type)
        self._noise_suppressor.remove_noise_type(signal_type)
        self._activation_timestamps.pop(signal_type, None)

    def _apply_gcl_failure(self, kind, decision_ref):
        # The type may also have qualified contexts or a pending candidate.
        # Remove their evidence before record_feedback checkpoints the withdrawal,
        # so rediscovery cannot immediately re-grant authority after restart.
        for key in list(self._qualification_evidence):
            if key.startswith(f"context:{kind}:"):
                self._qualification_evidence.pop(key)
        for name, metrics in self._agent_metrics.items():
            if metrics.config.get("signal_type") == kind:
                self._qualification_evidence.pop("agent:" + name, None)
        for context in self._activated_contexts.get(kind, ()):
            for suppressor in self._contextual_suppressors:
                suppressor.remove_noise_context(kind, context)
        # The lifecycle lock excludes unrelated feedback until the consumer
        # checkpoints all page effects before publishing any receipt.
        self._defer_verdict_feedback_save = True
        try:
            self.record_feedback(kind, was_suppressed=True, is_important=True)
        finally:
            self._defer_verdict_feedback_save = False
        self.classifier.record_gcl_verdict(kind, "FAILS", decision_ref)

    def _verdict_authority_ready(self):
        from .verdict_catchup import MAX_AGE_MS

        if not self._ledger_url:
            return True
        return (self._verdict_catchup_complete
                and 0 <= int(time.time() * 1000) - self._verdict_checked_through <= MAX_AGE_MS)

    def get_verdict_receipt(self, entry_id: str):
        """Return a durable same-domain live consumption receipt, or None if absent."""
        from .verdict_catchup import _scope
        from .verdict_receipts import lookup, open_store

        with self._lifecycle_lock, open_store(self, _scope(self)) as db:
            return lookup(db, entry_id)

    def _poll_gcl_verdicts(self):
        from .verdict_catchup import poll

        if self._closed or not self._ledger_url or not self._verdict_poll_lock.acquire(blocking=False):
            return False
        try:
            return poll(self)
        finally:
            self._verdict_poll_lock.release()

    def _save_state(self):
        if not self._state_file:
            return True
        with self._lifecycle_lock, self._snapshot_lock:
            if self._classifier_recovery_error:
                return False
            return self._save_state_locked()

    def _save_state_locked(self):
        try:
            with self._verdict_lock:
                _verdict_snapshot = (
                    self._verdict_watermark,
                    list(self._verdict_seen_ids),
                    deepcopy(self._verdict_cursor),
                    self._verdict_scope,
                    self._verdict_store_id,
                )
            with self._contexts_lock:
                _contexts_snapshot = (
                    dict(self._activated_patterns),
                    {key: sorted(values)
                     for key, values in list(self._activated_contexts.items())},
                    dict(self._context_activation_timestamps),
                )
            state = {
                "version": 5,
                "qualification_revision": REVISION,
                "classifier_identity": self._classifier_identity(),
                "qualification_evidence": {
                    key: dict(value) if isinstance(value, dict) else value
                    for key, value in list(self._qualification_evidence.items())
                },
                "classifier_work": self._classifier_work_snapshot(),
                "classification_completion": completion.snapshot(self),
                "serializer_revision": SERIALIZER_REVISION,
                "activated_agents": _contexts_snapshot[0],
                "activated_contexts": _contexts_snapshot[1],
                "context_activation_timestamps": _contexts_snapshot[2],
                "llm_noise_counts": dict(self._llm_noise_counts),
                "llm_important_counts": dict(self._llm_important_counts),
                "llm_context_noise": dict(self._prune_context_map(self._llm_context_noise)),
                "llm_context_important": dict(self._prune_context_map(self._llm_context_important)),
                "known_patterns": list(self.corpus_analyzer._known_patterns),
                "candidate_agents": [{
                    "name": metrics.name,
                    "tier": metrics.tier,
                    "config": metrics.config,
                    "human_approved": metrics.human_approved,
                    "human_approved_by": metrics.human_approved_by,
                    "human_approved_at": metrics.human_approved_at,
                    "deactivated": metrics.deactivated,
                    "rule": {
                        "name": rule.name,
                        "signal_types": rule.signal_types,
                        "condition": rule.condition,
                        "classification": rule.classification,
                    },
                } for name, metrics in self._agent_metrics.items()
                  if (rule := self._agent_rules.get(name)) is not None],
                "verdict_watermark": _verdict_snapshot[0],
                "verdict_seen_ids": _verdict_snapshot[1],
                "verdict_cursor": _verdict_snapshot[2],
                "verdict_scope": _verdict_snapshot[3],
                "verdict_store_id": _verdict_snapshot[4],
                "gcl_builtin_fails": self._gcl_builtin_fails,
                "llm_dropped": self._llm_dropped,
                "llm_dropped_by_severity": dict(self._llm_dropped_by_severity),
                "llm_payloads_truncated": self._llm_payloads_truncated,
                "llm_priority_dropped": self._llm_priority_dropped,
                "ledger_writes_dropped": self._ledger_writes_dropped,
                "ledger_receipt_failures": self._ledger_receipt_failures,
                "ledger_receipts_written": self._ledger_receipts_written,
                "ledger_receipt_last_success": self._ledger_receipt_last_success,
                "ledger_memory_events_dropped": self._ledger_memory_events_dropped,
                "ledger_memory_batches_written": self._ledger_memory_batches_written,
                "ledger_memory_failures": self._ledger_memory_failures,
                "ledger_memory_last_success": self._ledger_memory_last_success,
                "activation_timestamps": dict(self._activation_timestamps),
                "stats_snapshot": {
                    "signals_processed": self.stats.signals_processed,
                    "llm_classified": self.stats.llm_classified,
                },
                "memory_archive": self.memory_archive.to_dict() if self.memory_archive else None,
            }
            atomic_snapshot(self._state_file, state)
            self._state_healthy = True
            self._save_failures = 0
            return True
        except Exception as e:
            # A transient serialization race (e.g. a dict mutated during
            # iteration) must not wipe the entire learning state. Retry on
            # the next save; only fence after repeated failures.
            self._save_failures = getattr(self, '_save_failures', 0) + 1
            if self._save_failures >= 5:
                self._fence_learning('repeated snapshot write failures', state=True)
                log.warning("Failed to save state %d times, fencing: %s",
                            self._save_failures, e)
            else:
                log.warning("Transient state save failure (%d/5): %s",
                            self._save_failures, e)
            return False

    def _restore_state(self):
        if not self._state_file or not os.path.exists(self._state_file):
            # No snapshot to reconcile against: spooled work counts as fresh
            # admissions for this process (production replay semantics).
            if self._durable_queue is not None:
                self._complete_durable_restore()
            return
        try:
            with open(self._state_file) as f:
                state = json.load(f)
            uncertain_completion = bool(state.get("classification_completion", {}).get("pending"))
            completion.restore(self, state)
            self._restore_classifier_work(state.get('classifier_work'))
            uncertain_validation = any(entry.get('phase')=='inflight'
                                       for entry in state.get('classifier_work',{}).get('outstanding',[]))
            evidence = state if state.get("serializer_revision") == SERIALIZER_REVISION else {}
            if not evidence:
                log.warning("Classifier input revision changed; learned rules require fresh qualification")
            self._llm_noise_counts = defaultdict(int, evidence.get("llm_noise_counts", {}))
            self._llm_important_counts = defaultdict(
                int, evidence.get("llm_important_counts", {})
            )

            self._verdict_watermark = state.get("verdict_watermark", "")
            self._verdict_cursor = state.get("verdict_cursor")
            self._verdict_scope = state.get("verdict_scope")
            self._verdict_store_id = state.get("verdict_store_id")
            self._verdict_seen_ids = {
                str(entry_id): None
                for entry_id in state.get("verdict_seen_ids", [])[-2000:]
            }
            self._gcl_builtin_fails = state.get("gcl_builtin_fails", 0)
            self._llm_dropped = state.get("llm_dropped", 0)
            self._llm_dropped_by_severity = defaultdict(
                int, state.get("llm_dropped_by_severity", {}),
            )
            self._llm_payloads_truncated = state.get("llm_payloads_truncated", 0)
            self._llm_priority_dropped = state.get("llm_priority_dropped", 0)
            self._ledger_writes_dropped = state.get("ledger_writes_dropped", 0)
            self._ledger_receipt_failures = state.get(
                "ledger_receipt_failures", 0,
            )
            self._ledger_receipts_written = state.get(
                "ledger_receipts_written", 0,
            )
            self._ledger_receipt_last_success = state.get(
                "ledger_receipt_last_success", "",
            )
            self._ledger_memory_events_dropped = state.get(
                "ledger_memory_events_dropped", 0,
            )
            self._ledger_memory_batches_written = state.get(
                "ledger_memory_batches_written", 0,
            )
            self._ledger_memory_failures = state.get(
                "ledger_memory_failures", 0,
            )
            self._ledger_memory_last_success = state.get(
                "ledger_memory_last_success", "",
            )
            self._activation_timestamps = evidence.get("activation_timestamps", {})
            bound = (not uncertain_completion and not uncertain_validation and evidence.get('version') == 5 and evidence.get('qualification_revision') == REVISION
                     and evidence.get('classifier_identity') == self._classifier_identity())
            self._qualification_evidence = evidence.get('qualification_evidence', {}) if bound else {}
            if evidence.get("version") in (2, 3, 4, 5):
                self.corpus_analyzer._known_patterns = set(evidence.get("known_patterns", []))
                for candidate in evidence.get("candidate_agents", []):
                    metrics = AgentMetrics(
                        name=candidate["name"],
                        tier=candidate.get("tier", "draft"),
                        config=candidate.get("config", {}),
                        human_approved=candidate.get('human_approved', False) if bound else False,
                        human_approved_by=candidate.get('human_approved_by') if bound else None,
                        human_approved_at=candidate.get('human_approved_at') if bound else None,
                        deactivated=candidate.get('deactivated', False),
                    )
                    self._agent_metrics[metrics.name] = metrics
                    self._agent_rules[metrics.name] = RuleAgent(candidate["rule"])

                self._activated_patterns = {
                    kind: pattern for kind, pattern in evidence.get('activated_agents', {}).items()
                    if bound and self._restored_authority_valid(kind, pattern)
                }
                self._activated_types = set(self._activated_patterns)
                for kind in set(evidence.get('activated_agents', {})) - self._activated_types:
                    self._activation_timestamps.pop(kind, None)
                    self._llm_noise_counts[kind] = 0
                    self._llm_important_counts[kind] = 0
                    for name, metrics in self._agent_metrics.items():
                        if metrics.config.get('signal_type') == kind:
                            self._qualification_evidence.pop('agent:' + name, None)
                            metrics.tier = 'draft'
                            metrics.human_approved = False
                            metrics.human_approved_by = None
                            metrics.human_approved_at = None
                for sig_type, pattern_type in self._activated_patterns.items():
                    if pattern_type == "repeat_flood":
                        self._repeat_suppressor.add_signal_type(sig_type)
                    elif pattern_type == "dominant_type":
                        self._noise_suppressor.add_noise_type(sig_type)

                self._llm_context_noise = defaultdict(int, evidence.get("llm_context_noise", {}))
                self._llm_context_important = defaultdict(int, evidence.get("llm_context_important", {}))
                for kind, contexts in evidence.get('activated_contexts', {}).items():
                    for context in contexts:
                        identity = f'{kind}:{context}'
                        timestamp = evidence.get('context_activation_timestamps', {}).get(identity)
                        record = self._qualified_record('context:' + identity, self._context_binding(identity))
                        if (bound and not self._human_gate and record and record['noise'] >= 200
                                and record['important'] == 0 and valid_time(timestamp, self._activation_ttl_hours)):
                            self._activated_contexts[kind].add(context)
                            for s in self._contextual_suppressors: s.add_noise_context(kind, context)
                            self._context_activation_timestamps[identity] = timestamp
                        else:
                            self._qualification_evidence.pop('context:' + identity, None)
                            self._llm_context_noise[identity] = 0
                            self._llm_context_important[identity] = 0
                self._validate_restored_tiers()
            else:
                # Legacy state did not preserve the suppressor kind. Revalidate it
                # instead of restoring a type with broader suppression semantics.
                self.corpus_analyzer._known_patterns = set()
                log.warning("Legacy cascade state requires safe rediscovery")
            memory_data = state.get("memory_archive")
            if memory_data and self.memory_archive is not None:
                self.memory_archive = MemoryArchive.from_dict(memory_data, audit_backpressure=True)

            self._promote_orphaned_noise_types()
            # Complete the spool restore before persisting interrupted-work
            # receipts: the snapshot's outstanding list must reflect that the
            # spool — not the snapshot — is the authority for recovered work.
            if self._durable_queue is not None:
                self._complete_durable_restore()
            # A persisted journal whose not-yet-applied members reference
            # memories the same snapshot does not contain is unrecoverable by
            # the snapshot's own testimony: the destination is durably gone
            # (capacity eviction between checkpoint and completion). Discard
            # those records as dropped instead of fencing the restore; live
            # application keeps the stricter uncertainty fence.
            if self._completion_pending and self.memory_archive is not None:
                for work_id in list(self._completion_pending):
                    record = self._completion_pending[work_id]
                    unapplied = record["members"][record["next_member"]:]
                    missing = [target for _, target in unapplied
                               if target and self.memory_archive.get(UUID(target)) is None]
                    if missing:
                        log.warning(
                            "Discarding retained classification %s: %d/%d unapplied "
                            "member target(s) absent from the restored archive "
                            "(evicted before completion)",
                            work_id, len(missing), len(unapplied))
                        del self._completion_pending[work_id]
                        with self._llm_queue_lock:
                            self._classifier_inflight.pop(work_id, None)
                        work = state.get("classifier_work")
                        if isinstance(work, dict) and isinstance(work.get("accounting"), dict):
                            work["accounting"]["dropped"] = (
                                work["accounting"].get("dropped", 0) + len(unapplied))
            completion.resume(self)
            if state.get('classifier_work', {}).get('outstanding'):
                if not self._save_state():
                    raise OSError('could not persist interrupted classifier-work receipts')

            log.info("Restored: %d activated, %d LLM counts, %d patterns",
                     len(self._activated_types), len(self._llm_noise_counts),
                     len(self.corpus_analyzer._known_patterns))
        except Exception as e:
            self._state_healthy = False
            for kind in list(self._activated_types):
                self._deactivate_agent(kind)
            for kind, contexts in self._activated_contexts.items():
                for context in list(contexts):
                    for s in self._contextual_suppressors: s.remove_noise_context(kind, context)
                contexts.clear()
            self._qualification_evidence.clear()
            # Preserve the original snapshot and spool. A partial restore cannot
            # distinguish a fresh row from an already committed classification.
            completion.fence(self, "classification state restore failed")
            log.warning("Failed to restore state: %s", e)

    def _promote_orphaned_noise_types(self):
        """Keep historical-only evidence inactive until normal re-qualification.

        Restored counts are useful discovery evidence, but they do not carry the
        complete promotion provenance, approval state, or current holdout safety
        needed to activate a suppressor.
        """
        for sig_type, noise_count in self._llm_noise_counts.items():
            if sig_type in self._activated_types:
                continue
            important = self._llm_important_counts.get(sig_type, 0)
            total = noise_count + important
            if total < _PROMOTION_MIN_SAMPLES:
                continue
            self._promotion_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "historical_evidence_restored",
                "agent": f"restored_{sig_type}",
                "tier": "draft",
                "status": "awaiting_fresh_qualification",
                "samples": total,
                "important": important,
            })
            log.info(
                "RESTORED EVIDENCE: %s remains inactive (%d noise, %d important)",
                sig_type, noise_count, important,
            )

    def close(self) -> None:
        """Stop retry timers and executors without discarding durable work."""
        if self._closed:
            return
        self._closed = True
        self.enabled = False
        for lock, timer_name in (
            (self._ledger_receipt_lock, "_ledger_receipt_retry_timer"),
            (self._ledger_memory_lock, "_ledger_memory_retry_timer"),
        ):
            with lock:
                timer = getattr(self, timer_name)
                if timer is not None:
                    timer.cancel()
                    setattr(self, timer_name, None)
        self._ledger_executor.shutdown(wait=False, cancel_futures=True)
        self._ledger_memory_executor.shutdown(wait=False, cancel_futures=True)
        if self._durable_queue is not None:
            self._durable_queue.close()

    def save_state(self):
        """Persist current bridge state after out-of-band mutations such as federation."""
        self._save_state()

    def get_stats(self) -> Dict:
        stats = asdict(self.stats)
        measured = self._fn_evaluated > 0
        stats["fn_count"] = self._fn_count if measured else None
        stats["fn_rate"] = (
            round(self._fn_count / self._fn_evaluated * 100, 3)
            if measured else None
        )
        stats["fn_evaluated"] = self._fn_evaluated
        stats["fn_status"] = "measured" if measured else "not_measured"
        stats["fn_types"] = dict(sorted(self._fn_types.items(), key=lambda x: -x[1])[:10])
        stats["shadow_checks"] = self._shadow_checks
        stats["shadow_demotions"] = self._shadow_demotions
        stats["shadow_sample_rate"] = self._shadow_sample_rate
        stats["activation_ttl_hours"] = self._activation_ttl_hours
        stats["gcl_builtin_fails"] = self._gcl_builtin_fails
        stats["llm_dropped"] = self._llm_dropped
        stats["llm_dropped_by_severity"] = dict(self._llm_dropped_by_severity)
        stats["llm_buffer_bytes"] = self._llm_buffer_bytes
        stats["llm_buffer_max_bytes"] = self._llm_max_buffer_bytes
        stats["llm_priority_buffer_bytes"] = self._llm_priority_buffer_bytes
        stats["llm_priority_buffer_max_bytes"] = self._llm_priority_max_bytes
        stats["llm_priority_dropped"] = self._llm_priority_dropped
        stats["llm_payloads_truncated"] = self._llm_payloads_truncated
        stats["llm_coalesced"] = self._llm_coalesced
        stats["llm_coalesced_by_type"] = dict(
            sorted(
                self._llm_coalesced_by_type.items(),
                key=lambda item: -item[1],
            )[:20]
        )
        stats["llm_oldest_queue_age_seconds"] = round(
            self._llm_oldest_queue_age_seconds(), 3,
        )
        stats["llm_inflight"] = self._llm_inflight
        stats["llm_peak_inflight"] = self._llm_peak_inflight
        stats["llm_max_inflight"] = self._llm_max_inflight
        stats["classification_completion_pending"] = len(self._completion_pending)
        stats["classification_ack_pending"] = len(self._completion_ack)
        stats["classification_recovery_error"] = self._classifier_recovery_error
        stats["memory_audit_source_pending"] = self._memory_source_pending()
        stats["memory_audit_handoff_healthy"] = self._ledger_memory_handoff_healthy
        stats["memory_audit_handoff_failures"] = self._ledger_memory_handoff_failures
        stats['verdict_catchup_complete'] = self._verdict_catchup_complete and self._verdict_authority_ready()
        stats['verdict_catchup_error'] = (
            'stale_coverage' if self._verdict_catchup_complete and not stats['verdict_catchup_complete']
            else self._verdict_catchup_error
        )
        stats['verdict_checked_through'] = self._verdict_checked_through
        with self._verdict_lock:
            cursor = self._verdict_cursor
            stats['verdict_watermark'] = self._verdict_watermark
            stats['verdict_next_offset'] = cursor.get('offset') if isinstance(cursor, dict) else None
            stats['verdict_window_total'] = cursor.get('total') if isinstance(cursor, dict) else None
        stats['verdict_mode'] = self._verdict_mode
        stats['state_snapshot_healthy'] = self._state_healthy
        stats['learning_fence_reason'] = self._learning_fence_reason
        if self._value_rule_agent is not None:
            stats['value_rule_revision'] = self._value_rule_agent.revision
            stats['value_rule_fires'] = dict(
                sorted(self._value_rule_agent.fires.items(), key=lambda x: -x[1]))
            stats['value_rule_else_classified'] = (
                self.stats.value_rule_else_classified)
        stats['classifier_work_recovery'] = (
            'sqlite_replay' if self._durable_queue is not None
            else 'terminal_receipts_on_restart' if self._state_file
            else 'memory_only'
        )
        stats['classifier_restart_terminal_receipts'] = len(self._classifier_terminal_receipts)
        stats['classifier_restart_terminal_receipts_evicted'] = self._classifier_terminal_evicted
        with self._llm_queue_lock:
            queued = sum(
                len(entry.get("_signal_ids") or [entry.get("signal_id", "")])
                for entry in self._llm_buffer + self._llm_priority_buffer
            )
            in_flight = (
                self._llm_accounting_dispatched
                - self._llm_accounting_succeeded
                - self._llm_accounting_failed
            )
            accounted = (
                queued + in_flight + self._llm_accounting_succeeded
                + self._llm_accounting_failed + self._llm_accounting_dropped
            )
            stats["llm_signal_outcomes_since_start"] = {
                "started_at": self._llm_accounting_started_at,
                "enqueued": self._llm_accounting_enqueued,
                "queued": queued,
                "dispatched": self._llm_accounting_dispatched,
                "in_flight": in_flight,
                "classified": self._llm_accounting_succeeded,
                "failed": self._llm_accounting_failed,
                "dropped": self._llm_accounting_dropped,
                "reconciled": (
                    in_flight >= 0 and accounted == self._llm_accounting_enqueued
                ),
                "terminal_complete": (
                    not self._classifier_recovery_error and in_flight == 0 and queued == 0
                    and self._llm_accounting_failed == 0
                    and self._llm_accounting_dropped == 0
                    and accounted == self._llm_accounting_enqueued
                ),
            }
            stats["llm_durable_queue"] = (
                self._durable_queue.stats() if self._durable_queue is not None
                else {"enabled": False}
            )
            stats["llm_durable_queue"]["uncommitted_changes"] = len(self._durable_dirty)
            stats["llm_durable_queue"]["memory_checkpoint_healthy"] = (
                self._durable_memory_checkpoint_healthy
            )
            stats["llm_durable_queue"]["memory_checkpoint_failures"] = (
                self._durable_memory_checkpoint_failures
            )
            if self._durable_queue is not None:
                stats["llm_signal_outcomes_since_start"]["terminal_complete"] &= (
                    self._durable_queue.healthy and not self._durable_dirty
                    and stats["llm_durable_queue"]["pending_entries"] == 0
                )
        stats["ledger_writes_dropped"] = self._ledger_writes_dropped
        stats["ledger_max_pending"] = self._ledger_max_pending
        receipt_pending = self._ledger_receipt_pending_count()
        stats["ledger_receipt_pending"] = receipt_pending
        stats["ledger_receipt_pending_max"] = self._ledger_receipt_pending_max
        stats["ledger_receipt_utilization"] = round(
            receipt_pending / self._ledger_receipt_pending_max, 4
        )
        stats["ledger_receipt_queue_durability"] = (
            "sqlite" if self._ledger_receipt_store else "memory_only"
        )
        stats["ledger_receipt_overflow_policy"] = (
            DURABLE_QUEUE_OVERFLOW_POLICY
            if self._ledger_receipt_store else "bounded_executor_reject_new"
        )
        stats["ledger_receipt_oldest_age_seconds"] = round(
            self._ledger_receipt_store.oldest_age_seconds(), 3
        ) if self._ledger_receipt_store else None
        stats["ledger_receipt_spool_bytes"] = (
            self._ledger_receipt_store.storage_bytes()
            if self._ledger_receipt_store else 0
        )
        receipt_filesystem = (
            self._ledger_receipt_store.filesystem_capacity()
            if self._ledger_receipt_store else {}
        )
        stats["ledger_receipt_filesystem_total_bytes"] = (
            receipt_filesystem.get("total_bytes")
        )
        stats["ledger_receipt_filesystem_free_bytes"] = (
            receipt_filesystem.get("free_bytes")
        )
        stats["ledger_receipt_filesystem_used_fraction"] = (
            receipt_filesystem.get("used_fraction")
        )
        receipt_payload_bytes = (
            self._ledger_receipt_store.payload_bytes()
            if self._ledger_receipt_store else 0
        )
        stats["ledger_receipt_payload_bytes"] = receipt_payload_bytes
        stats["ledger_receipt_bytes_max"] = self._ledger_receipt_bytes_max
        stats["ledger_receipt_byte_utilization"] = round(
            receipt_payload_bytes / self._ledger_receipt_bytes_max, 4
        )
        stats["ledger_receipt_rejected_total"] = (
            self._ledger_receipt_store.rejected_count()
            if self._ledger_receipt_store else self._ledger_writes_dropped
        )
        stats["ledger_receipt_failures"] = self._ledger_receipt_failures
        stats["ledger_receipt_consecutive_failures"] = (
            self._ledger_receipt_consecutive_failures
        )
        stats["ledger_receipts_written"] = self._ledger_receipts_written
        stats["ledger_receipt_last_success"] = self._ledger_receipt_last_success
        pending = self._ledger_memory_pending_count()
        stats["ledger_memory_pending"] = pending
        stats["ledger_memory_pending_max"] = self._ledger_memory_pending_max
        stats["ledger_memory_utilization"] = round(
            pending / self._ledger_memory_pending_max, 4
        )
        stats["ledger_memory_queue_durability"] = (
            "sqlite" if self._ledger_memory_store else "memory_only"
        )
        stats["ledger_memory_overflow_policy"] = (
            DURABLE_QUEUE_OVERFLOW_POLICY
            if self._ledger_memory_store else "drop_oldest_memory_only"
        )
        stats["ledger_memory_oldest_age_seconds"] = round(
            self._ledger_memory_store.oldest_age_seconds(), 3
        ) if self._ledger_memory_store else None
        stats["ledger_memory_spool_bytes"] = (
            self._ledger_memory_store.storage_bytes()
            if self._ledger_memory_store else 0
        )
        memory_filesystem = (
            self._ledger_memory_store.filesystem_capacity()
            if self._ledger_memory_store else {}
        )
        stats["ledger_memory_filesystem_total_bytes"] = (
            memory_filesystem.get("total_bytes")
        )
        stats["ledger_memory_filesystem_free_bytes"] = (
            memory_filesystem.get("free_bytes")
        )
        stats["ledger_memory_filesystem_used_fraction"] = (
            memory_filesystem.get("used_fraction")
        )
        stats["ledger_spools_share_filesystem"] = bool(
            receipt_filesystem.get("filesystem_id")
            and receipt_filesystem.get("filesystem_id")
            == memory_filesystem.get("filesystem_id")
        )
        memory_payload_bytes = (
            self._ledger_memory_store.payload_bytes()
            if self._ledger_memory_store else 0
        )
        stats["ledger_memory_payload_bytes"] = memory_payload_bytes
        stats["ledger_memory_bytes_max"] = self._ledger_memory_bytes_max
        stats["ledger_memory_byte_utilization"] = round(
            memory_payload_bytes / self._ledger_memory_bytes_max, 4
        )
        stats["ledger_memory_rejected_total"] = (
            self._ledger_memory_store.rejected_count()
            if self._ledger_memory_store else self._ledger_memory_events_dropped
        )
        stats["ledger_memory_events_dropped"] = self._ledger_memory_events_dropped
        stats["ledger_memory_batches_written"] = self._ledger_memory_batches_written
        stats["ledger_memory_failures"] = self._ledger_memory_failures
        stats["ledger_memory_consecutive_failures"] = (
            self._ledger_memory_consecutive_failures
        )
        stats["ledger_memory_last_success"] = self._ledger_memory_last_success
        stats["classifier"] = {
            "mode": self.classifier.mode,
            "config_error": self.classifier.config_error,
            "serializer_revision": SERIALIZER_REVISION,
            "coverage": self.classifier.coverage_stats(),
            **self.classifier.recorder.stats(),
        }
        if isinstance(self.classifier.generative, TemplateCachedClassifierBackend):
            stats["classifier"]["template_cache"] = (
                self.classifier.generative.stats())
        return stats

    def wait_for_llm(self, timeout_seconds: float = 60.0) -> bool:
        """Wait until replay AI work drains; return False when evidence is incomplete."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            with self._llm_queue_lock:
                queued = len(self._llm_buffer) + len(self._llm_priority_buffer)
            with self._llm_lock:
                active = self._llm_active_threads
            if active == 0 and queued and (self._completion_pending or self._completion_ack):
                if not completion.resume(self):
                    return False
                self._dispatch_llm_batches()
            if queued == 0 and active == 0:
                if not completion.resume(self):
                    return False
                if self._durable_queue is None:
                    return True
                # Fail closed: uncommitted spool changes, surviving rows, or an
                # unhealthy spool mean replay evidence is incomplete.
                with self._llm_queue_lock:
                    return (
                        not self._durable_dirty
                        and self._durable_queue.stats()["pending_entries"] == 0
                        and self._durable_queue.healthy
                    )
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def get_route_ledger(self) -> list[dict]:
        """Return privacy-safe aggregate routes for a replay evidence artifact."""
        routes = []
        for name in ("deduplicated", "suppressed", "classified_without_ai", "excluded"):
            count = self._route_counts[name]
            if count:
                routes.append({"route": name, "signal_count": count, "ai_eligible": False,
                               "ai_calls": 0})
        for tier in ("micro", "macro"):
            eligible = self._route_counts[f"{tier}_eligible"]
            if eligible:
                routes.append({
                    "route": tier,
                    "signal_count": eligible,
                    "ai_eligible": True,
                    "ai_calls": self._llm_calls_by_tier[tier],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "inference_seconds": round(self._llm_seconds_by_tier[tier], 6),
                    "inference_cost_usd": None,
                })
        return routes

    # -- Meta-cascade: self-monitoring -----------------------------------------

    _META_COOLDOWNS = {
        "meta_memory_pressure": 60.0,
        "meta_llm_starvation": 60.0,
        "meta_llm_queue_drop": 60.0,
        "meta_ledger_backpressure": 60.0,
        "meta_llm_priority_overflow": 60.0,
        "meta_ledger_memory_overflow": 60.0,
    }

    def _emit_meta(self, signal_type: str, severity: str, message: str,
                   **kwargs) -> None:
        """Append a meta signal describing an internal cascade event."""
        cooldown = self._META_COOLDOWNS.get(signal_type, 0.0)
        if cooldown:
            now = time.monotonic()
            last = self._meta_last_emitted.get(signal_type)
            if last is not None and now - last < cooldown:
                return
            self._meta_last_emitted[signal_type] = now

        meta = {
            "signal_type": signal_type,
            "severity": severity,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "domain": self.domain,
            **kwargs,
        }
        self._meta_signals.append(meta)
        if len(self._meta_signals) > self._meta_max_buffer:
            self._meta_signals = self._meta_signals[-self._meta_max_buffer:]
        log.debug("META: %s [%s] %s", signal_type, severity, message)
        if self._meta_target:
            threading.Thread(
                target=self._post_meta_signal, args=(meta,), daemon=True,
            ).start()

    def _post_meta_signal(self, meta: dict) -> None:
        """POST a meta signal to an external meta-cascade instance."""
        try:
            import httpx
            with httpx.Client(timeout=10) as client:
                client.post(
                    self._meta_target.rstrip("/") + "/cascade",
                    json={"signals": [{
                        "signal_type": meta["signal_type"],
                        "severity": meta["severity"],
                        "content": {"message": meta["message"], **{
                            k: v for k, v in meta.items()
                            if k not in ("signal_type", "severity", "message",
                                         "timestamp", "domain")
                        }},
                    }]},
                )
        except Exception as e:
            log.debug("Meta POST failed: %s", str(e)[:60])

    def get_meta_signals(self, limit: int = 50) -> list:
        """Return the most recent meta signals."""
        return self._meta_signals[-limit:]

    def check_consolidation_meta(self, result: dict) -> None:
        """Emit meta signal if consolidation evicted a large fraction."""
        processed = result.get("processed", 0)
        evicted = result.get("evicted", 0)
        if processed > 0 and evicted / processed > 0.10:
            self._emit_meta(
                "meta_consolidation_eviction_spike", "high",
                f"Consolidation evicted {evicted}/{processed} memories "
                f"({evicted / processed:.0%})",
                evicted=evicted,
                processed=processed,
                eviction_rate=round(evicted / processed, 3),
            )

    def record_feedback(
        self, signal_type: str, *, was_suppressed: bool, is_important: bool
    ) -> None:
        with self._lifecycle_lock:
            return self._record_feedback_locked(signal_type, was_suppressed=was_suppressed, is_important=is_important)

    def _record_feedback_locked(self, signal_type, *, was_suppressed, is_important):
        """Record externally verified ground truth for FN telemetry.

        If a suppressed signal was important (false negative) and the
        responsible agent is activated, that agent is instantly demoted.
        """
        self._fn_evaluated += 1
        if was_suppressed and is_important:
            self._fn_count += 1
            self._fn_types[signal_type] += 1
            self._emit_meta(
                "meta_fn_detected", "critical",
                f"False negative confirmed: {signal_type} was suppressed but important",
                related_type=signal_type,
                fn_total=self._fn_count,
                fn_evaluated=self._fn_evaluated,
            )

            if signal_type in self._activated_types:
                for name, metrics in self._agent_metrics.items():
                    sig = metrics.config.get("signal_type", "")
                    if (
                        sig == signal_type
                        and metrics.config.get("pattern_type")
                        in {"repeat_flood", "dominant_type"}
                        and metrics.tier in {"nano", "micro", "macro"}
                    ):
                        metrics.last_batch_fn_count = 1
                        self.promotion.demote(
                            metrics,
                            reason=f"FN confirmed by external feedback: {signal_type}",
                        )
                        self._deactivate_agent(signal_type)
                        self._llm_noise_counts[signal_type] = 0
                        self._llm_important_counts[signal_type] = 0
                        self._flush_promotion_events()
                        log.warning("Agent '%s' demoted via feedback: %s was important",
                                    name, signal_type)
                        self._emit_meta(
                            "meta_agent_demoted", "high",
                            f"Agent '{name}' demoted: FN confirmed for {signal_type}",
                            agent=name, related_type=signal_type,
                            reason="fn_confirmed",
                        )
                        break

                # Restored/orphaned authority must be withdrawn even when no
                # matching metrics survived. Negative feedback cannot depend
                # on the continued existence of its original agent object.
                self._deactivate_agent(signal_type)
                self._llm_noise_counts[signal_type] = 0
                self._llm_important_counts[signal_type] = 0
                for name, metrics in self._agent_metrics.items():
                    if metrics.config.get('signal_type') == signal_type:
                        self._qualification_evidence.pop('agent:' + name, None)

            active_contexts = set(self._activated_contexts.get(signal_type, set()))
            if active_contexts:
                for ctx_value in active_contexts:
                    self._contextual_suppressor.remove_noise_context(
                        signal_type, ctx_value,
                    )
                    self._context_activation_timestamps.pop(
                        f"{signal_type}:{ctx_value}", None,
                    )
                    self._llm_context_noise[f"{signal_type}:{ctx_value}"] = 0
                    self._llm_context_important[f"{signal_type}:{ctx_value}"] = 0
                    self._qualification_evidence.pop('context:' + f'{signal_type}:{ctx_value}', None)
                self._activated_contexts[signal_type].clear()
                for metrics in self._agent_metrics.values():
                    if (
                        metrics.config.get("pattern_type") == "contextual_noise"
                        and metrics.config.get("signal_type") == signal_type
                        and metrics.tier in {"nano", "micro", "macro"}
                    ):
                        metrics.last_batch_fn_count = 1
                        self.promotion.demote(
                            metrics,
                            reason=(
                                "FN confirmed by external feedback for contextual "
                                f"suppression: {signal_type}"
                            ),
                        )
                self._flush_promotion_events()
                self._emit_meta(
                    "meta_agent_demoted", "high",
                    f"Contextual suppressors demoted: FN confirmed for {signal_type}",
                    related_type=signal_type,
                    reason="fn_confirmed",
                )
            if not self._defer_verdict_feedback_save:
                self._save_state()

    def get_llm_results(self, limit: int = 20) -> list:
        return self._llm_results[-limit:]

    def get_llm_summary(self) -> Dict:
        top_noise = sorted(self._llm_noise_counts.items(), key=lambda x: -x[1])[:10]
        return {
            "classified": self.stats.llm_classified,
            "noise": self.stats.llm_noise,
            "important": self.stats.llm_important,
            "noise_pct": round(self.stats.llm_noise / max(1, self.stats.llm_classified) * 100, 1),
            "top_noise_types": [{
                "signal_type": k,
                "noise_count": v,
                "activated": k in self._activated_types,
            } for k, v in top_noise],
            "activated_types": list(self._activated_types),
            "activated_contexts": {k: sorted(v) for k, v in self._activated_contexts.items()},
            "contextual_suppressor_count": sum(len(v) for v in self._activated_contexts.values()),
            "context_noise_keys": len(self._llm_context_noise),
            "context_important_keys": len(self._llm_context_important),
            "buffer_size": len(self._llm_buffer) + len(self._llm_priority_buffer),
            "normal_buffer_size": len(self._llm_buffer),
            "priority_buffer_size": len(self._llm_priority_buffer),
            "coalesced": self._llm_coalesced,
            "oldest_queue_age_seconds": round(
                self._llm_oldest_queue_age_seconds(), 3,
            ),
            "running": self._llm_active_threads > 0,
        }

    def get_gpu_analyses(self, limit: int = 20) -> list:
        return self._gpu_analyses[-limit:]

    def get_promotion_log(self, limit: int = 30) -> list:
        return self._promotion_log[-limit:]

    def get_discovered_agents(self) -> list:
        if hasattr(self, "corpus_analyzer"):
            return self.corpus_analyzer.get_proposed_agents()
        return []
