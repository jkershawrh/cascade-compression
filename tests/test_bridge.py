"""Tests for the standalone CascadeBridge."""

import json
from unittest.mock import MagicMock, patch

from cascade_compression.bridge import CascadeBridge, _to_cascade_signal, _BUILTIN_AGENT_NAMES
from cascade_compression.cascade.memory import MemoryArchive


class FakeSignal:
    def __init__(self, signal_type="test_event", severity="info", namespace="default"):
        self.signal_id = id(self)
        self.cluster_id = "test-cluster"
        self.namespace = namespace
        self.resource_kind = "test"
        self.resource_name = "test-resource"
        self.signal_type = signal_type
        self.severity = severity
        self.evidence = {"message": f"{signal_type} on {namespace}"}
        self.labels = {"domain": "test"}


class TestBridgeInit:
    def test_creates_without_llm(self):
        bridge = CascadeBridge()
        assert bridge.enabled
        assert bridge.stats.signals_processed == 0

    def test_domain_is_set(self):
        bridge = CascadeBridge(domain="aap")
        assert bridge.domain == "aap"

    def test_env_defaults(self):
        bridge = CascadeBridge()
        assert bridge._llm_model == ""


class TestBridgeProcess:
    def test_processes_signals(self):
        bridge = CascadeBridge()
        signals = [FakeSignal() for _ in range(10)]
        result = bridge.process(signals)
        assert result["enabled"]
        assert result["signals"] == 10
        assert bridge.stats.signals_processed == 10

    def test_compression_ratio_updates(self):
        bridge = CascadeBridge()
        signals = [FakeSignal(severity="info") for _ in range(100)]
        bridge.process(signals)
        assert bridge.stats.compression_ratio > 0

    def test_empty_signals_returns_disabled(self):
        bridge = CascadeBridge()
        result = bridge.process([])
        assert not result["enabled"]

    def test_repeated_signals_get_deduplicated(self):
        bridge = CascadeBridge()
        signals = [FakeSignal(signal_type="same_event", severity="medium") for _ in range(50)]
        bridge.process(signals)
        assert bridge.stats.cascade_handled > 0

    def test_high_severity_passes_through(self):
        bridge = CascadeBridge()
        signals = [FakeSignal(signal_type=f"critical_{i}", severity="critical") for i in range(5)]
        bridge.process(signals)
        assert bridge.stats.cascade_forwarded > 0

    def test_stats_accumulate(self):
        bridge = CascadeBridge()
        bridge.process([FakeSignal() for _ in range(10)])
        bridge.process([FakeSignal() for _ in range(10)])
        assert bridge.stats.signals_processed == 20


class TestSignalConversion:
    def test_converts_signal(self):
        sig = FakeSignal(signal_type="pod_crash", severity="high")
        cascade_sig = _to_cascade_signal(sig)
        assert cascade_sig.signal_type == "pod_crash"
        assert cascade_sig.severity == "high"
        assert cascade_sig.content.get("message")

    def test_handles_missing_fields(self):
        class MinimalSignal:
            signal_id = 1
            signal_type = "test"
            severity = "info"
        cascade_sig = _to_cascade_signal(MinimalSignal())
        assert cascade_sig.signal_type == "test"


class TestBridgeStats:
    def test_get_stats(self):
        bridge = CascadeBridge()
        bridge.process([FakeSignal() for _ in range(10)])
        stats = bridge.get_stats()
        assert "signals_processed" in stats
        assert "compression_ratio" in stats
        assert "fn_count" in stats
        assert stats["fn_count"] is None
        assert stats["fn_status"] == "not_measured"

    def test_false_negative_feedback_is_measured(self):
        bridge = CascadeBridge()
        bridge.record_feedback("alert", was_suppressed=True, is_important=True)
        bridge.record_feedback("alert", was_suppressed=False, is_important=True)
        stats = bridge.get_stats()
        assert stats["fn_count"] == 1
        assert stats["fn_rate"] == 50.0
        assert stats["fn_status"] == "measured"

    def test_get_llm_summary_empty(self):
        bridge = CascadeBridge()
        summary = bridge.get_llm_summary()
        assert summary["classified"] == 0
        assert summary["noise"] == 0
        assert summary["important"] == 0
        assert summary["activated_types"] == []

    def test_route_ledger_partitions_processed_population(self):
        bridge = CascadeBridge()
        bridge.process([FakeSignal(severity="info") for _ in range(5)])
        bridge.process([FakeSignal(signal_type=f"critical_{i}", severity="critical")
                        for i in range(3)])
        ledger = bridge.get_route_ledger()
        assert sum(route["signal_count"] for route in ledger) == 8
        eligible = [route for route in ledger if route["ai_eligible"]]
        assert sum(route["ai_calls"] for route in eligible) == 0
        assert bridge.wait_for_llm(0.1) is False

    def test_promotion_log_empty(self):
        bridge = CascadeBridge()
        assert bridge.get_promotion_log() == []


class TestLedgerIntegration:
    def test_no_ledger_no_error(self):
        bridge = CascadeBridge()
        signals = [FakeSignal() for _ in range(10)]
        result = bridge.process(signals)
        assert result["enabled"]

    def test_ledger_url_set(self):
        bridge = CascadeBridge(ledger_url="http://fake:28099")
        assert bridge._ledger_url == "http://fake:28099"


class TestEvidenceBundle:
    def test_evidence_bundle_with_memory_archive(self):
        bridge = CascadeBridge()
        bridge.memory_archive = MemoryArchive(max_capacity=100)
        from cascade_compression.cascade.protocol import Signal
        mem_signal = Signal(
            signal_type="pod_crashloop", severity="high",
            source="test", namespace="prod",
            content={"message": "Pod crashing in prod"}, labels={},
        )
        bridge.memory_archive.store(mem_signal, classification="important")
        sig = {
            "signal_type": "pod_crashloop", "severity": "high",
            "namespace": "prod", "content": {"message": "Pod crashing in prod"},
        }
        bundle = bridge._build_evidence_bundle(sig)
        assert "related_memories" in bundle
        assert len(bundle["related_memories"]) > 0
        assert bundle["related_memories"][0]["signal_type"] == "pod_crashloop"

    def test_evidence_bundle_without_archive(self):
        bridge = CascadeBridge()
        bridge.memory_archive = None
        bundle = bridge._build_evidence_bundle({"signal_type": "test"})
        assert bundle["related_memories"] == []


class TestGCLBuiltinFiltering:
    def test_builtin_agent_names_match_agents_py(self):
        from cascade_compression.cascade.agents import default_agents
        agents = default_agents()
        builtin_names = {a.name for a in agents}
        assert builtin_names == _BUILTIN_AGENT_NAMES

    def test_builtin_agent_fails_not_counted_as_fn(self):
        bridge = CascadeBridge()
        assert bridge._fn_count == 0
        assert bridge._gcl_builtin_fails == 0
        assert "deduplicate" in _BUILTIN_AGENT_NAMES

    def test_dynamic_agent_fails_counted_as_fn(self):
        bridge = CascadeBridge()
        bridge.record_feedback("my_dynamic_type", was_suppressed=True, is_important=True)
        assert bridge._fn_count == 1
        assert bridge._fn_evaluated == 1

    def test_gcl_builtin_fails_in_stats(self):
        bridge = CascadeBridge()
        bridge._gcl_builtin_fails = 42
        stats = bridge.get_stats()
        assert stats["gcl_builtin_fails"] == 42

    def test_gcl_builtin_fails_persisted(self):
        bridge = CascadeBridge()
        bridge._gcl_builtin_fails = 100
        bridge._fn_count = 0
        stats = bridge.get_stats()
        assert stats["gcl_builtin_fails"] == 100
        assert stats["fn_count"] is None  # no FN evaluated

    def test_written_ts_advances_watermark_and_entry_id_deduplicates(self):
        bridge = CascadeBridge()
        bridge._ledger_url = "https://ledger.example"
        entry = {
            "entry_id": "verdict-1",
            "written_ts": 1786208236746,
            "content": {
                "verdict": "FAILS",
                "original_agent": "dominant_noise_suppressor",
                "subject_type": "event_unhealthy",
                "reason": "important signal suppressed",
            },
        }
        response = MagicMock()
        response.json.return_value = {"entries": [entry]}
        client = MagicMock()
        client.get.return_value = response
        client.__enter__.return_value = client

        with patch("httpx.Client", return_value=client):
            bridge._poll_gcl_verdicts()
            bridge._poll_gcl_verdicts()

        assert bridge._verdict_watermark == 1786208236746
        assert list(bridge._verdict_seen_ids) == ["verdict-1"]
        assert bridge._fn_count == 1
        assert bridge._fn_evaluated == 1


class TestLLMBackpressure:
    def test_queue_preserves_high_severity_and_counts_drops(self):
        bridge = CascadeBridge()
        bridge._llm_max_buffer = 1
        bridge._llm_max_buffer_bytes = 1_000_000
        bridge._llm_buffer = [
            {"signal_type": "old-low", "severity": "low"},
            {"signal_type": "critical", "severity": "critical"},
            {"signal_type": "old-medium", "severity": "medium"},
            {"signal_type": "new-medium", "severity": "medium"},
            {"signal_type": "high", "severity": "high"},
        ]

        bridge._enforce_llm_buffer_limit()

        assert [s["signal_type"] for s in bridge._llm_priority_buffer] == [
            "critical", "high",
        ]
        assert [s["signal_type"] for s in bridge._llm_buffer] == ["new-medium"]
        assert bridge._llm_dropped == 2
        assert bridge._llm_dropped_by_severity == {"low": 1, "medium": 1}
        assert bridge.get_stats()["llm_dropped"] == 2

    def test_queue_is_bounded_by_bytes_and_preserves_priority(self):
        bridge = CascadeBridge()
        bridge._llm_max_buffer = 10
        bridge._llm_max_buffer_bytes = 500
        bridge._llm_buffer = [
            {"signal_type": "old-medium", "severity": "medium",
             "_queue_bytes": 400},
            {"signal_type": "critical", "severity": "critical",
             "_queue_bytes": 400},
            {"signal_type": "new-medium", "severity": "medium",
             "_queue_bytes": 400},
        ]

        bridge._enforce_llm_buffer_limit()

        assert [s["signal_type"] for s in bridge._llm_priority_buffer] == [
            "critical",
        ]
        assert [s["signal_type"] for s in bridge._llm_buffer] == ["new-medium"]
        assert bridge._llm_buffer_bytes == 400
        assert bridge._llm_dropped_by_severity == {"medium": 1}

    def test_concurrent_enqueue_keeps_queue_accounting_consistent(self):
        import threading

        bridge = CascadeBridge()
        bridge._llm_max_buffer = 1000
        bridge._llm_priority_max_buffer = 1000
        normal = [_to_cascade_signal(FakeSignal(f"normal-{i}", "medium"))
                  for i in range(100)]
        priority = [_to_cascade_signal(FakeSignal(f"priority-{i}", "high"))
                    for i in range(100)]

        threads = [
            threading.Thread(target=bridge._enqueue_llm_signals, args=(normal,)),
            threading.Thread(target=bridge._enqueue_llm_signals, args=(priority,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(bridge._llm_buffer) == 100
        assert len(bridge._llm_priority_buffer) == 100
        assert bridge._llm_buffer_bytes == sum(
            bridge._entry_queue_bytes(sig) for sig in bridge._llm_buffer
        )
        assert bridge._llm_priority_buffer_bytes == sum(
            bridge._entry_queue_bytes(sig) for sig in bridge._llm_priority_buffer
        )

    def test_llm_content_is_compacted_before_queueing(self):
        bridge = CascadeBridge()
        bridge._llm_max_content_bytes = 1024
        content, truncated = bridge._compact_llm_content({
            "message": "failed task",
            "stdout": "x" * 100_000,
            "nested": {"values": ["y" * 1000] * 100},
        })

        assert truncated is True
        assert content["message"] == "failed task"
        assert len(json.dumps(content).encode("utf-8")) < 2048

    def test_context_maps_are_bounded_live(self):
        bridge = CascadeBridge()
        bridge._CONTEXT_MAP_MAX = 3
        bridge._CONTEXT_MAP_PRUNE_AT = 4
        for index in range(6):
            bridge._increment_context_count(
                bridge._llm_context_noise, f"type:namespace-{index}",
            )
        assert len(bridge._llm_context_noise) <= 4

    def test_pressure_meta_events_are_rate_limited(self):
        bridge = CascadeBridge()
        with patch("cascade_compression.bridge.time.monotonic",
                   side_effect=[1.0, 2.0, 62.0]):
            bridge._emit_meta("meta_llm_starvation", "high", "full")
            bridge._emit_meta("meta_llm_starvation", "high", "still full")
            bridge._emit_meta("meta_llm_starvation", "high", "still full")
        assert len(bridge._meta_signals) == 2

    def test_ledger_executor_drops_work_when_pending_limit_is_reached(self):
        bridge = CascadeBridge()
        for _ in range(bridge._ledger_max_pending):
            assert bridge._ledger_slots.acquire(blocking=False)

        submitted = bridge._submit_ledger_write(lambda: None)

        assert submitted is False
        assert bridge._ledger_writes_dropped == 1
        assert bridge.get_stats()["ledger_writes_dropped"] == 1
        for _ in range(bridge._ledger_max_pending):
            bridge._ledger_slots.release()

    def test_memory_ledger_batch_is_retained_for_retry(self):
        bridge = CascadeBridge(ledger_url="https://ledger.example")
        bridge._ledger_memory_pending = [{"memory_id": "m1"}, {"memory_id": "m2"}]
        bridge._ledger_memory_flush_running = True

        with patch(
            "cascade_compression.integrations.ledger.write_memory_events",
            return_value=False,
        ) as write:
            bridge._drain_memory_ledger_queue()

        assert bridge._ledger_memory_pending == [
            {"memory_id": "m1"}, {"memory_id": "m2"},
        ]
        assert bridge._ledger_memory_flush_running is False
        write.assert_called_once()

        bridge._ledger_memory_flush_running = True
        with patch(
            "cascade_compression.integrations.ledger.write_memory_events",
            return_value=True,
        ):
            bridge._drain_memory_ledger_queue()

        assert bridge._ledger_memory_pending == []
        assert bridge._ledger_memory_batches_written == 1
