"""Tests for continuous validation: shadow sampling and time-bounded activation.

Validates:
- Shadow validation: suppressed signals sampled and sent to LLM for re-check
- Time-bounded activation: agents expire after TTL and must re-qualify
- Integration: shadow + TTL + GCL demotion all produce provenance events
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from cascade_compression.bridge import CascadeBridge, _is_noise_classification
from cascade_compression.cascade.pipeline import CascadeResult
from cascade_compression.cascade.promotion import AgentMetrics, PromotionEngine
from cascade_compression.cascade.protocol import CascadeDecision, Outcome, Signal

from tests.helpers import make_signal as _make_signal


@pytest.fixture
def bridge():
    b = CascadeBridge(domain="test")
    b.enabled = True
    b._llm_url = ""
    b._shadow_sample_rate = 1.0  # 100% for deterministic tests
    return b


class TestShadowSampling:
    def test_queue_shadow_samples_from_activated_agents(self, bridge):
        sig = _make_signal("event_noise")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="dominant_noise_suppressor",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            )],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 1
        assert bridge._shadow_buffer[0]["signal_type"] == "event_noise"

    def test_no_shadow_for_non_activated_types(self, bridge):
        sig = _make_signal("unknown_type")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="some_agent",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            )],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 0

    def test_no_shadow_for_keep_decisions(self, bridge):
        sig = _make_signal("event_noise")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="test",
                outcome=Outcome.KEEP,
                confidence=0.9,
            )],
            remaining=[sig],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 0

    def test_shadow_buffer_capped(self, bridge):
        bridge._shadow_max_buffer = 5
        bridge._activated_types = {"event_noise"}
        signals = [_make_signal("event_noise") for _ in range(10)]
        result = CascadeResult(
            total_signals=10,
            decisions=[CascadeDecision(
                signal_id=s.signal_id,
                agent_name="test",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            ) for s in signals],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, signals)
        assert len(bridge._shadow_buffer) <= 5

    def test_shadow_sample_rate_zero_skips(self, bridge):
        bridge._shadow_sample_rate = 0.0
        sig = _make_signal("event_noise")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="test",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            )],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 0

    def test_shadow_demotion_on_important_classification(self, bridge):
        """When shadow LLM says a suppressed signal is important, demotion fires."""
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge.record_feedback("event_noise", was_suppressed=True, is_important=True)

        assert metrics.tier == "draft"
        assert metrics.deactivated is True
        assert "event_noise" not in bridge._activated_types

    def test_feedback_demotes_type_agent_not_context_agent(self, bridge):
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        context = AgentMetrics(
            name="context", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "contextual_noise",
                "context_value": "namespace-a",
            },
        )
        type_agent = AgentMetrics(
            name="type", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics = {"context": context, "type": type_agent}

        bridge.record_feedback(
            "event_noise", was_suppressed=True, is_important=True,
        )

        assert type_agent.tier == "draft"
        assert context.tier == "nano"
        assert "event_noise" not in bridge._activated_types

    def test_noise_classification_helper(self):
        assert _is_noise_classification("routine_noise") is True
        assert _is_noise_classification("known_pattern noise") is True
        assert _is_noise_classification("needs_attention") is False
        assert _is_noise_classification("real_incident") is False


class TestTimeBoundedActivation:
    def test_fresh_agent_not_expired(self, bridge):
        bridge._activation_ttl_hours = 72
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        bridge._activation_timestamps = {
            "event_noise": datetime.now(timezone.utc).isoformat()
        }
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "nano"
        assert "event_noise" in bridge._activated_types

    def test_expired_agent_suspended(self, bridge):
        bridge._activation_ttl_hours = 72
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
        bridge._activation_timestamps = {"event_noise": expired_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "draft"
        assert metrics.deactivated is False  # reactivated, ready to re-climb
        assert metrics.samples_tested == 0
        assert "event_noise" not in bridge._activated_types
        assert "event_noise" not in bridge._activation_timestamps

    def test_expired_agent_emits_demotion_event(self, bridge):
        bridge._activation_ttl_hours = 1
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        bridge._activation_timestamps = {"event_noise": expired_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        events = bridge.promotion.drain_events()
        assert len(events) == 1
        assert events[0].event_type == "demotion"
        assert "TTL expired" in events[0].reason

    def test_ttl_zero_disables_expiry(self, bridge):
        bridge._activation_ttl_hours = 0
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        old_time = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        bridge._activation_timestamps = {"event_noise": old_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "nano"
        assert "event_noise" in bridge._activated_types

    def test_malformed_activation_timestamp_fails_closed(self, bridge):
        bridge._activation_ttl_hours = 72
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        bridge._activation_timestamps = {"event_noise": "not-a-timestamp"}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "draft"
        assert "event_noise" not in bridge._activated_types
        assert "event_noise" not in bridge._activation_timestamps

    def test_expired_agent_can_requalify(self, bridge):
        """After TTL expiry, agent is at draft + reactivated — can climb again."""
        bridge._activation_ttl_hours = 1
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        bridge._activation_timestamps = {"event_noise": expired_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics["noise_agent"] = metrics
        bridge._llm_noise_counts["event_noise"] = 500

        bridge._check_activation_ttl()

        assert metrics.tier == "draft"
        assert metrics.deactivated is False
        assert metrics.samples_tested == 0
        assert bridge._llm_noise_counts["event_noise"] == 0
        # Agent is ready to climb again via normal promotion path
        engine = bridge.promotion
        result = engine.check_promotion(metrics)
        assert result.tier == "draft"  # no samples yet, stays at draft
        bridge._discover_and_promote()
        assert result.tier == "draft"  # historical counts cannot re-promote it

    def test_type_ttl_demotes_type_agent_not_context_agent(self, bridge):
        bridge._activation_ttl_hours = 1
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        bridge._activation_timestamps = {
            "event_noise": (
                datetime.now(timezone.utc) - timedelta(hours=2)
            ).isoformat(),
        }
        context = AgentMetrics(
            name="context", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "contextual_noise",
                "context_value": "namespace-a",
            },
        )
        type_agent = AgentMetrics(
            name="type", tier="nano",
            config={
                "signal_type": "event_noise",
                "pattern_type": "dominant_type",
            },
        )
        bridge._agent_metrics = {"context": context, "type": type_agent}

        bridge._check_activation_ttl()

        assert type_agent.tier == "draft"
        assert context.tier == "nano"


class TestStatsExposure:
    def test_stats_include_shadow_and_ttl(self, bridge):
        stats = bridge.get_stats()
        assert "shadow_checks" in stats
        assert "shadow_demotions" in stats
        assert "shadow_sample_rate" in stats
        assert "activation_ttl_hours" in stats
        assert stats["shadow_sample_rate"] == 1.0
        assert stats["activation_ttl_hours"] == 72

    def test_promotion_events_remain_local_without_external_ledger(self, bridge):
        agent = AgentMetrics(
            name="local-agent", tier="draft", samples_tested=60,
            accuracy=1.0, false_positive_rate=0.0,
            false_negative_rate=0.0,
        )
        bridge.promotion.check_promotion(agent)

        bridge._flush_promotion_events()

        assert bridge.get_promotion_log()[-1]["event"] == "promotion"
        assert bridge.get_promotion_log()[-1]["agent"] == "local-agent"


class TestRestoredAndContextualSafety:
    def test_historical_counts_do_not_auto_activate(self, bridge):
        bridge._llm_noise_counts["historical_noise"] = 1000
        bridge._llm_important_counts["historical_noise"] = 0

        bridge._promote_orphaned_noise_types()

        assert "historical_noise" not in bridge._activated_types
        assert bridge.get_promotion_log()[-1]["status"] == (
            "awaiting_fresh_qualification"
        )

    def test_context_with_known_important_signal_does_not_activate(self, bridge):
        bridge._llm_context_noise["mixed_type:namespace-a"] = 199
        bridge._llm_context_important["mixed_type:namespace-a"] = 1

        bridge._discover_contextual_noise()

        assert "namespace-a" not in bridge._activated_contexts["mixed_type"]

    def test_human_gate_blocks_automatic_context_activation(self, bridge):
        bridge._human_gate = True
        bridge._llm_context_noise["noise_type:namespace-a"] = 200
        bridge._llm_context_important["noise_type:namespace-a"] = 0

        bridge._discover_contextual_noise()

        assert "namespace-a" not in bridge._activated_contexts["noise_type"]

    def test_context_activates_through_promotion_engine(self, bridge):
        bridge._llm_context_noise["noise_type:namespace-a"] = 200
        bridge._llm_context_important["noise_type:namespace-a"] = 0

        bridge._discover_contextual_noise()

        assert "namespace-a" in bridge._activated_contexts["noise_type"]
        metrics = next(
            item for item in bridge._agent_metrics.values()
            if item.config.get("pattern_type") == "contextual_noise"
        )
        assert metrics.tier == "nano"
        assert metrics.false_negative_rate == 0
        assert "noise_type:namespace-a" in bridge._context_activation_timestamps
        transitions = [
            (event.from_tier, event.to_tier)
            for event in bridge.promotion.events
        ]
        assert ("draft", "candidate") in transitions
        assert ("candidate", "nano") in transitions

    def test_contextual_activation_expires(self, bridge):
        bridge._activation_ttl_hours = 1
        bridge._llm_context_noise["noise_type:namespace-a"] = 200
        bridge._discover_contextual_noise()
        bridge._context_activation_timestamps["noise_type:namespace-a"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()

        bridge._check_activation_ttl()

        assert "namespace-a" not in bridge._activated_contexts["noise_type"]
        metrics = next(
            item for item in bridge._agent_metrics.values()
            if item.config.get("pattern_type") == "contextual_noise"
        )
        assert metrics.tier == "draft"
        assert bridge._llm_context_noise["noise_type:namespace-a"] == 0

    def test_external_feedback_demotes_contextual_suppressors(self, bridge):
        bridge._llm_context_noise["noise_type:namespace-a"] = 200
        bridge._discover_contextual_noise()

        bridge.record_feedback(
            "noise_type", was_suppressed=True, is_important=True,
        )

        assert not bridge._activated_contexts["noise_type"]
        metrics = next(
            item for item in bridge._agent_metrics.values()
            if item.config.get("pattern_type") == "contextual_noise"
        )
        assert metrics.tier == "draft"


class TestStateRoundTrip:
    def test_activation_timestamps_persisted(self, bridge, tmp_path):
        state_file = str(tmp_path / "cascade_state.json")
        bridge._state_file = state_file
        bridge._activation_timestamps = {
            "event_noise": "2026-08-10T00:00:00+00:00",
        }
        bridge._verdict_watermark = "2026-08-10T01:00:00+00:00"
        bridge._verdict_seen_ids = {"verdict-1": None}
        bridge._llm_dropped = 7
        bridge._llm_dropped_by_severity["medium"] = 7
        bridge._llm_payloads_truncated = 3
        bridge._ledger_writes_dropped = 2

        bridge._save_state()

        with open(state_file) as f:
            state = json.load(f)
        assert state["activation_timestamps"]["event_noise"] == "2026-08-10T00:00:00+00:00"
        assert state["verdict_watermark"] == "2026-08-10T01:00:00+00:00"
        assert state["verdict_seen_ids"] == ["verdict-1"]

        bridge2 = CascadeBridge(domain="test")
        bridge2._state_file = state_file
        bridge2._restore_state()
        assert bridge2._activation_timestamps["event_noise"] == "2026-08-10T00:00:00+00:00"
        assert bridge2._verdict_watermark == "2026-08-10T01:00:00+00:00"
        assert list(bridge2._verdict_seen_ids) == ["verdict-1"]
        assert bridge2._llm_dropped == 7
        assert bridge2._llm_dropped_by_severity["medium"] == 7
        assert bridge2._llm_payloads_truncated == 3
        assert bridge2._ledger_writes_dropped == 2
