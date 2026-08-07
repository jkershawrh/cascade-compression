"""Cascade bridge — connects collector → pipeline → LLM.

Standalone. No dependency on deepfield or any consuming application.
The bridge initializes the cascade pipeline, processes signals from any
collector, and optionally forwards survivors to an LLM for classification.
"""

import hashlib
import json
import logging
import os
import time
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .cascade.pipeline import CascadePipeline
from .cascade.protocol import Signal
from .cascade.agents import default_agents
from .cascade.corpus_analyzer import CorpusAnalyzer
from .cascade.dynamic_agents import RepeatFloodSuppressor, DominantNoiseSuppressor
from .cascade.promotion import AgentMetrics, Baseline, PromotionEngine, RuleAgent

log = logging.getLogger(__name__)


@dataclass
class BridgeStats:
    signals_processed: int = 0
    cascade_handled: int = 0
    cascade_forwarded: int = 0
    llm_classified: int = 0
    llm_noise: int = 0
    llm_important: int = 0
    compression_ratio: float = 0.0
    started_at: str = ""
    last_updated: str = ""


def _to_cascade_signal(sig: Any) -> Signal:
    """Convert any signal object to the cascade Signal protocol."""
    return Signal(
        signal_id=getattr(sig, "signal_id", uuid4()),
        signal_type=getattr(sig, "signal_type", ""),
        severity=getattr(sig, "severity", "info"),
        source=getattr(sig, "resource_name", ""),
        namespace=getattr(sig, "namespace", ""),
        cluster=str(getattr(sig, "cluster_id", ""))[:8],
        content={
            "resource_kind": getattr(sig, "resource_kind", ""),
            "message": getattr(sig, "evidence", {}).get("message", ""),
            **{k: v for k, v in getattr(sig, "evidence", {}).items() if k != "message"},
        },
        labels=getattr(sig, "labels", {}),
    )


class CascadeBridge:
    """Standalone cascade bridge.

    Usage::

        bridge = CascadeBridge(
            llm_url="https://maas.example.com/v1",
            llm_key="sk-...",
            llm_model="granite-3-2-8b-instruct-cpu",
            system_prompt="You are classifying...",
        )
        signals = collector.collect()
        result = bridge.process(signals)
    """

    def __init__(
        self,
        llm_url: str = "",
        llm_key: str = "",
        llm_model: str = "microsoft-phi-4",
        system_prompt: str = "",
        domain: str = "default",
        ledger_url: str = "",
        ledger_token: str = "",
    ):
        self.domain = domain
        self._llm_url = llm_url or os.getenv("CASCADE_LLM_URL", os.getenv("LITELLM_API_BASE", ""))
        self._llm_key = llm_key or os.getenv("CASCADE_LLM_KEY", os.getenv("LITELLM_API_KEY", ""))
        self._llm_model = llm_model or os.getenv("CASCADE_LLM_MODEL", "microsoft-phi-4")
        self._system_prompt = system_prompt
        self._ledger_url = ledger_url or os.getenv("CASCADE_LEDGER_URL", "")
        self._ledger_token = ledger_token or os.getenv("CASCADE_LEDGER_TOKEN", "")

        self.enabled = True
        self.pipeline = CascadePipeline(default_agents())
        self.promotion = PromotionEngine()
        self.stats = BridgeStats(started_at=datetime.now(timezone.utc).isoformat())

        self._repeat_suppressor = RepeatFloodSuppressor(max_repeats=3, window_seconds=300)
        self._noise_suppressor = DominantNoiseSuppressor(window_seconds=300)
        self.pipeline.register(self._repeat_suppressor)
        self.pipeline.register(self._noise_suppressor)
        self._activated_types: set = set()

        self.corpus_analyzer = CorpusAnalyzer(
            min_frequency=0.05, min_repeat_count=10,
            repeat_window_seconds=300, analysis_interval=300,
        )

        self._llm_buffer: list = []
        self._llm_max_buffer = 500
        self._llm_results: list = []
        self._llm_running = False
        self._llm_noise_counts: Dict[str, int] = defaultdict(int)

        self._agent_metrics: Dict[str, AgentMetrics] = {}
        self._agent_rules: Dict[str, RuleAgent] = {}
        self._promotion_log: list = []
        self._last_promotion_time: float = 0
        self._promotion_interval: float = 300

        self._fn_count = 0
        self._fn_types: Dict[str, int] = defaultdict(int)

        self.baseline = Baseline(normal_ranges={})
        self._observed_ranges: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))

        self._state_file = os.getenv("CASCADE_STATE_FILE", "")

        self._restore_state()

    def process(self, signals: list) -> Dict:
        if not self.enabled or not signals:
            return {"enabled": False}

        cascade_signals = [_to_cascade_signal(s) for s in signals]

        t0 = time.monotonic()
        cascade_result = self.pipeline.run(cascade_signals)
        cascade_ms = (time.monotonic() - t0) * 1000

        remaining_count = len(cascade_result.remaining)
        handled_count = len(signals) - remaining_count
        self.stats.signals_processed += len(signals)
        self.stats.cascade_handled += handled_count
        self.stats.cascade_forwarded += remaining_count
        if self.stats.signals_processed > 0:
            self.stats.compression_ratio = self.stats.cascade_handled / self.stats.signals_processed
        self.stats.last_updated = datetime.now(timezone.utc).isoformat()

        self.corpus_analyzer.observe(cascade_signals)

        for sig in cascade_result.remaining:
            self._llm_buffer.append({
                "signal_type": sig.signal_type,
                "severity": sig.severity,
                "namespace": sig.namespace,
                "content": sig.content,
            })
        if len(self._llm_buffer) > self._llm_max_buffer:
            self._llm_buffer = self._llm_buffer[-self._llm_max_buffer:]

        if self._llm_buffer and not self._llm_running and self._llm_url:
            batch = list(self._llm_buffer[:50])
            self._llm_buffer = self._llm_buffer[50:]
            threading.Thread(target=self._run_llm, args=(batch,), daemon=True).start()

        now = time.monotonic()
        if now - self._last_promotion_time > self._promotion_interval:
            self._last_promotion_time = now
            self._discover_and_promote()
            self._save_state()

        if self._ledger_url:
            threading.Thread(
                target=self._write_to_ledger,
                args=(cascade_result, cascade_signals),
                daemon=True,
            ).start()

        return {
            "enabled": True,
            "signals": len(signals),
            "cascade_ms": round(cascade_ms, 1),
            "compression": round(cascade_result.compression_ratio, 3),
        }

    def _discover_and_promote(self):
        new_agents = self.corpus_analyzer.maybe_analyze()
        for agent_metrics, rule in new_agents:
            sig_type = agent_metrics.config.get("signal_type", "")
            pattern_type = agent_metrics.config.get("pattern_type", "")
            if sig_type and sig_type not in self._activated_types:
                if self._llm_noise_counts.get(sig_type, 0) >= 3:
                    if pattern_type == "repeat_flood":
                        self._repeat_suppressor.add_signal_type(sig_type)
                    else:
                        self._noise_suppressor.add_noise_type(sig_type)
                    self._activated_types.add(sig_type)
                    self._promotion_log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event": "activated",
                        "agent": f"suppress_{sig_type}",
                        "tier": "nano",
                        "status": "llm_confirmed",
                        "config": agent_metrics.config,
                    })
                    log.info("ACTIVATED: %s (LLM confirmed %d noise)",
                             sig_type, self._llm_noise_counts[sig_type])
                else:
                    self._promotion_log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event": "discovered",
                        "agent": agent_metrics.config.get("signal_type", ""),
                        "tier": "draft",
                        "status": "awaiting_llm_validation",
                        "config": agent_metrics.config,
                    })

    def _run_llm(self, signals):
        self._llm_running = True
        try:
            import httpx
            client = httpx.Client(timeout=60, verify=False)

            for sig in signals:
                text = (
                    f"{sig['signal_type']} {sig['severity']}: "
                    f"{sig.get('content', {}).get('message', '')} "
                    f"namespace={sig.get('namespace', '')}"
                )
                try:
                    t0 = time.monotonic()
                    r = client.post(
                        f"{self._llm_url}/v1/chat/completions",
                        json={
                            "model": self._llm_model,
                            "messages": [
                                {"role": "system", "content": self._system_prompt},
                                {"role": "user", "content": text},
                            ],
                            "max_tokens": 5,
                            "temperature": 0,
                        },
                        headers={"Authorization": f"Bearer {self._llm_key}"},
                    )
                    ms = (time.monotonic() - t0) * 1000
                    ans = r.json()["choices"][0]["message"]["content"].strip().lower()
                    self.stats.llm_classified += 1

                    self._llm_results.append({
                        "signal_type": sig["signal_type"],
                        "classification": ans,
                        "latency_ms": round(ms),
                    })
                    if len(self._llm_results) > 1000:
                        self._llm_results = self._llm_results[-1000:]

                    if "routine_noise" in ans or "known_pattern" in ans:
                        self.stats.llm_noise += 1
                        self._llm_noise_counts[sig["signal_type"]] += 1
                    else:
                        self.stats.llm_important += 1

                except Exception as e:
                    log.debug("LLM call failed: %s", str(e)[:60])

            client.close()
        except Exception as e:
            log.warning("LLM error: %s", e)
        finally:
            self._llm_running = False

    def _write_to_ledger(self, cascade_result, cascade_signals):
        try:
            from .integrations.ledger import write_decisions
            write_decisions(
                self._ledger_url, self._ledger_token,
                cascade_result, cascade_signals, self.domain,
            )
        except Exception as e:
            log.debug("Ledger write failed: %s", str(e)[:60])

    def _save_state(self):
        if not self._state_file:
            return
        try:
            state = {
                "activated_types": list(self._activated_types),
                "llm_noise_counts": dict(self._llm_noise_counts),
                "known_patterns": list(self.corpus_analyzer._known_patterns),
                "stats_snapshot": {
                    "signals_processed": self.stats.signals_processed,
                    "llm_classified": self.stats.llm_classified,
                },
            }
            with open(self._state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            log.warning("Failed to save state: %s", e)

    def _restore_state(self):
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file) as f:
                state = json.load(f)
            self._activated_types = set(state.get("activated_types", []))
            self._llm_noise_counts = defaultdict(int, state.get("llm_noise_counts", {}))
            self.corpus_analyzer._known_patterns = set(state.get("known_patterns", []))
            for sig_type in self._activated_types:
                self._repeat_suppressor.add_signal_type(sig_type)
                self._noise_suppressor.add_noise_type(sig_type)
            log.info("Restored: %d activated, %d LLM counts, %d patterns",
                     len(self._activated_types), len(self._llm_noise_counts),
                     len(self.corpus_analyzer._known_patterns))
        except Exception as e:
            log.warning("Failed to restore state: %s", e)

    def get_stats(self) -> Dict:
        stats = asdict(self.stats)
        stats["fn_count"] = self._fn_count
        stats["fn_rate"] = round(self._fn_count / max(1, self.stats.signals_processed) * 100, 3)
        stats["fn_types"] = dict(sorted(self._fn_types.items(), key=lambda x: -x[1])[:10])
        return stats

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
            "buffer_size": len(self._llm_buffer),
            "running": self._llm_running,
        }

    def get_promotion_log(self, limit: int = 30) -> list:
        return self._promotion_log[-limit:]

    def get_discovered_agents(self) -> list:
        if hasattr(self, "corpus_analyzer"):
            return self.corpus_analyzer.get_proposed_agents()
        return []
