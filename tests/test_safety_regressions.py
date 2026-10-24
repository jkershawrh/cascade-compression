"""Regression cases from the blind review of the default suppression path."""

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cascade_compression.bridge import CascadeBridge, _to_cascade_signal
from cascade_compression.cascade.agents import default_agents
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.protocol import Outcome, Signal
from cascade_compression.classifier import (
    SERIALIZER_REVISION, CascadeClassifier, ClassificationResult,
    ComparisonRecorder, serialize_signal,
)
from cascade_compression.collectors.finance import FinanceSignal
from cascade_compression.collectors.healthcare import HealthcareSignal
from cascade_compression.collectors.insurance import InsuranceSignal
from cascade_compression.collectors.kubernetes import KubernetesSignal
from cascade_compression.collectors.retail import RetailSignal
from cascade_compression.collectors.telecom import TelecomSignal


@pytest.mark.parametrize("signal_class", [
    FinanceSignal, HealthcareSignal, InsuranceSignal, RetailSignal, TelecomSignal,
])
@pytest.mark.parametrize("record", [{}, {"id": None}, {"id": ""}])
def test_collectors_assign_unique_ids_when_missing(signal_class, record):
    first, second = signal_class(record), signal_class(record)
    assert first.signal_id is not None
    assert first.signal_id != ""
    assert first.signal_id != second.signal_id


@pytest.mark.parametrize("signal_class", [
    FinanceSignal, HealthcareSignal, InsuranceSignal, RetailSignal, TelecomSignal,
])
@pytest.mark.parametrize("supplied_id", [0, "source-record-1"])
def test_collectors_preserve_supplied_correlation_ids(signal_class, supplied_id):
    assert signal_class({"id": supplied_id}).signal_id == supplied_id


def test_kubernetes_observations_have_unique_ids_across_namespaces():
    records = [
        {"type": "pod", "name": "web", "namespace": namespace}
        for namespace in ("prod", "test", "prod")
    ]
    assert len({KubernetesSignal(record).signal_id for record in records}) == 3


def test_routine_healthcare_record_does_not_remove_unrelated_critical_record():
    records = [
        HealthcareSignal({"event_type": "temperature", "value": 37}),
        HealthcareSignal({
            "event_type": "spo2", "value": 70, "severity_flag": "critical",
        }),
    ]
    signals = [_to_cascade_signal(record) for record in records]
    result = CascadePipeline(default_agents()).run(signals)
    assert signals[1] in result.remaining


@pytest.mark.parametrize("as_text", [False, True])
def test_duplicate_ids_are_rejected_before_any_agent_mutates_state(as_text):
    pipeline = CascadePipeline(default_agents())
    shared_id = uuid4()
    first = Signal(signal_id=shared_id, severity="info")
    critical = Signal(signal_id=str(shared_id) if as_text else shared_id, severity="critical")
    with pytest.raises(ValueError, match="unique"):
        pipeline.run([first, critical])
    # Rejection must not prime the dedup cache for a subsequent valid request.
    critical.severity = "medium"
    assert pipeline.run([critical]).remaining == [critical]


def test_api_rejects_duplicate_ids_as_invalid_input():
    from fastapi.testclient import TestClient
    from cascade_compression.cascade.service import app

    shared_id = str(uuid4())
    response = TestClient(app).post("/cascade", json={
        "signals": [
            {"signal_id": shared_id, "severity": "info"},
            {"signal_id": shared_id, "severity": "critical"},
        ],
        "forward_to_inference": False,
    })
    assert response.status_code == 422
    assert "unique" in response.text


@pytest.mark.parametrize("label", ["compliance", "fraud", "sanctions"])
@pytest.mark.parametrize("severity", ["info", "low", "medium"])
def test_protected_events_survive_builtin_and_learned_suppression(label, severity):
    from cascade_compression.cascade.dynamic_agents import DominantNoiseSuppressor

    suppressor = DominantNoiseSuppressor()
    suppressor.add_noise_type("dns_timeout")
    pipeline = CascadePipeline([*default_agents(), suppressor])
    signals = [Signal(
        signal_type="dns_timeout", severity=severity,
        content={"message": "reportable event"}, labels={"label": label},
    ) for _ in range(2)]
    result = pipeline.run(signals)
    assert result.remaining == signals
    assert result.compression_ratio == 0
    assert not any(decision.outcome in {Outcome.DROP, Outcome.SUPPRESS, Outcome.DEDUPE}
                   for decision in result.decisions)


def test_medium_repeat_flood_is_suppressed_after_threshold():
    """The last-mile fix: promoted suppressors must act on medium signals.

    Found live in production: the K8s collector maps Warning events to
    medium (the identity-admission convention), so every promoted
    suppressor skipped exactly the signals it was promoted to suppress.
    AAP compression sat at 3.2% with three activated suppressors.
    """
    from cascade_compression.cascade.dynamic_agents import RepeatFloodSuppressor

    suppressor = RepeatFloodSuppressor(max_repeats=3, window_seconds=300)
    suppressor.add_signal_type("task_runner_item_on_ok")
    signals = [Signal(signal_type="task_runner_item_on_ok", severity="medium",
                      source="job-1", namespace="ns") for _ in range(6)]
    decisions = suppressor.process(signals)
    assert len(decisions) == 3
    assert all(d.outcome == Outcome.SUPPRESS for d in decisions)


def test_medium_dominant_noise_is_suppressed():
    from cascade_compression.cascade.dynamic_agents import DominantNoiseSuppressor

    suppressor = DominantNoiseSuppressor()
    suppressor.add_noise_type("event_failedscheduling")
    signals = [Signal(signal_type="event_failedscheduling", severity="medium",
                      source="pod-1", namespace="ns") for _ in range(3)]
    decisions = suppressor.process(signals)
    assert len(decisions) == 2
    assert all(d.outcome == Outcome.SUPPRESS for d in decisions)


def test_medium_contextual_noise_is_suppressed():
    from cascade_compression.cascade.dynamic_agents import ContextualNoiseSuppressor

    suppressor = ContextualNoiseSuppressor()
    suppressor.add_noise_context("task_runner_retry", "aap-general")
    signals = [Signal(signal_type="task_runner_retry", severity="medium",
                      source="job-1", namespace="aap-general") for _ in range(3)]
    decisions = suppressor.process(signals)
    assert len(decisions) == 2
    assert all(d.outcome == Outcome.SUPPRESS for d in decisions)


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_dynamic_suppressors_never_touch_high_or_critical(severity):
    """Fail-open is preserved: no dynamic suppressor may act above medium."""
    from cascade_compression.cascade.dynamic_agents import (
        ContextualNoiseSuppressor, DominantNoiseSuppressor, RepeatFloodSuppressor,
    )

    repeat = RepeatFloodSuppressor()
    repeat.add_signal_type("t")
    dominant = DominantNoiseSuppressor()
    dominant.add_noise_type("t")
    contextual = ContextualNoiseSuppressor()
    contextual.add_noise_context("t", "ns")
    signals = [Signal(signal_type="t", severity=severity, source="s",
                      namespace="ns") for _ in range(10)]
    assert repeat.process(signals) == []
    assert dominant.process(signals) == []
    assert contextual.process(signals) == []


def test_anchor_pipeline_routes_exist():
    """The anchor pipeline's gate read 404s as zero signals and stayed
    permanently closed while exiting status=0. The routes must exist and
    return the consumed contract shapes."""
    from fastapi.testclient import TestClient

    from cascade_compression.service import app

    client = TestClient(app)
    stats = client.get("/classifier/stats")
    assert stats.status_code == 200
    assert "coverage" in stats.json()
    comparisons = client.get("/classifier/comparisons?limit=5")
    assert comparisons.status_code == 200
    assert "comparisons" in comparisons.json()


def test_service_adapter_exposes_source_for_dynamic_suppressors():
    """Dynamic suppressors key on `s.source`; the service adapter must
    carry it. Found live: the adapter mapped source to resource_name
    only, every suppressor raised AttributeError on every batch, and
    the pipeline's exception handler skipped them silently — zero
    suppress decisions ever reached the ledger despite activated
    agents on both services."""
    from cascade_compression.service import SignalInput, _SignalAdapter

    adapter = _SignalAdapter(SignalInput(
        signal_type="event_failedscheduling", severity="medium",
        source="kube-scheduler", namespace="ns", content={}, labels={},
    ))
    assert adapter.source == "kube-scheduler"
    from cascade_compression.cascade.dynamic_agents import RepeatFloodSuppressor

    suppressor = RepeatFloodSuppressor(max_repeats=1, window_seconds=300)
    suppressor.add_signal_type("event_failedscheduling")
    adapted = [_SignalAdapter(SignalInput(
        signal_type="event_failedscheduling", severity="medium",
        source="kube-scheduler", namespace="ns", content={}, labels={},
    )) for _ in range(3)]
    decisions = suppressor.process(adapted)
    assert len(decisions) == 2
    assert all(d.outcome == Outcome.SUPPRESS for d in decisions)


@pytest.mark.parametrize("content", [
    {"cpu_percent": 99},
    {"memory_percent": 99},
    {"disk_percent": 99},
    {"cpu_percent": 85, "memory_percent": 99},
    {"message": "security injection detected"},
])
@pytest.mark.parametrize("signal_type", ["metric", "dns_timeout"])
def test_critical_evidence_is_checked_before_suppression(content, signal_type):
    signal = Signal(signal_type=signal_type, severity="info", content=content)
    result = CascadePipeline(default_agents()).run([signal])
    assert result.remaining == [signal]
    assert result.escalated_count == 1
    assert result.dropped_count == result.suppressed_count == 0


def test_undecided_info_signal_survives_without_severity_gate():
    signal = Signal(severity="info")
    result = CascadePipeline().run([signal])
    assert result.remaining == [signal]
    assert result.decisions == []
    assert result.dropped_count == 0


@pytest.mark.parametrize("normalize", [False, True])
def test_classifier_preserves_numeric_and_structured_evidence(normalize):
    def reading(value):
        return {
            "signal_type": "metric", "severity": "low", "namespace": "prod",
            "source": "node-a", "cluster": "cluster-a",
            "content": {
                "message": f"CPU utilization {value}", "cpu_percent": value,
                "details": {"limit": 95, "healthy": False},
            },
            "labels": {"domain": "test"},
        }

    safe, dangerous = reading(42), reading(99)
    safe_text = serialize_signal(safe, normalize=normalize)
    dangerous_text = serialize_signal(dangerous, normalize=normalize)
    assert safe_text != dangerous_text
    assert json.loads(dangerous_text) == dangerous


def test_classifier_preserves_evidence_even_without_a_message():
    first = {"content": {"value": 42, "unit": "percent"}}
    second = {"content": {"value": 99, "unit": "percent"}}
    assert serialize_signal(first) != serialize_signal(second)


def test_classifier_canonicalizes_key_order_and_omits_correlation_id_only():
    signal = {"signal_id": "first", "content": {"value": 42, "unit": "%"}}
    equivalent = {"content": {"unit": "%", "value": 42}, "signal_id": "second"}
    assert serialize_signal(signal) == serialize_signal(equivalent)


@pytest.mark.parametrize("mode", ["generative", "compare", "semantic", "hybrid"])
def test_authoritative_backends_receive_preserved_evidence(mode):
    class Backend:
        def __init__(self):
            self.inputs = []

        def classify(self, signal, text, client=None):
            self.inputs.append(json.loads(text))
            return ClassificationResult(label="needs_attention", margin=0.9)

    generative, semantic = Backend(), Backend()
    classifier = CascadeClassifier(
        generative, semantic, mode=mode, hybrid_margin=0.2,
        recorder=ComparisonRecorder(),
    )
    signals = [{"severity": "low", "content": {"value": value}}
               for value in (42, 99)]
    for signal in signals:
        assert classifier.classify(signal).ok
    for backend in (generative, semantic):
        if backend.inputs:
            assert backend.inputs == signals


def test_bridge_preserves_evidence_for_async_and_shadow_classification(monkeypatch):
    bridge = CascadeBridge()
    signal = Signal(
        signal_type="metric", severity="low", source="node-a",
        cluster="cluster-a", namespace="prod",
        content={"value": 99, "unit": "%"}, labels={"domain": "test"},
    )
    captured = []

    def complete(client, url, key, model, messages):
        captured.append(json.loads(messages[-1]["content"]))
        return "routine_noise"

    monkeypatch.setattr("cascade_compression.bridge.llm_complete", complete)
    try:
        bridge._enqueue_llm_signals([signal])
        expected = json.loads(serialize_signal(bridge._llm_buffer[0]))
        assert expected["content"] == signal.content
        assert expected["source"] == signal.source
        assert expected["cluster"] == signal.cluster
        assert expected["labels"] == signal.labels

        from cascade_compression.cascade.pipeline import CascadeResult
        from cascade_compression.cascade.protocol import CascadeDecision

        bridge._activated_types.add(signal.signal_type)
        bridge._shadow_sample_rate = 1
        bridge._queue_shadow_samples(CascadeResult(decisions=[CascadeDecision(
            signal_id=signal.signal_id, outcome=Outcome.SUPPRESS,
        )]), [signal])
        bridge._run_shadow_llm(bridge._shadow_buffer)
        assert captured == [expected]
    finally:
        bridge.close()


@pytest.mark.parametrize("revision", [None, "cascade-classifier-text-v2", SERIALIZER_REVISION])
def test_restored_rules_require_the_current_classifier_input_revision(tmp_path, revision):
    bridge = CascadeBridge()
    bridge._state_file = str(tmp_path / "state.json")
    bridge._activated_patterns["custom"] = "dominant_type"
    bridge._activated_types.add("custom")
    bridge._activation_timestamps["custom"] = datetime.now(timezone.utc).isoformat()
    bridge._llm_noise_counts["custom"] = 500
    bridge.memory_archive.store(Signal(severity="critical"))
    bridge._ledger_receipts_written = 7
    try:
        bridge._save_state()
    finally:
        bridge.close()
    with open(bridge._state_file) as source:
        state = json.load(source)
    if revision is None:
        state.pop("serializer_revision")
    else:
        state["serializer_revision"] = revision
    with open(bridge._state_file, "w") as destination:
        json.dump(state, destination)

    # Preserve the original 500 in a raw input snapshot, never as current authority.
    assert state["llm_noise_counts"]["custom"] == 500
    historical = tmp_path / "historical-unqualified-input.json"
    historical.write_text(json.dumps(state, sort_keys=True))
    import hashlib
    (tmp_path / "historical-unqualified-input.sha256").write_text(
        hashlib.sha256(historical.read_bytes()).hexdigest()
    )
    restored = CascadeBridge()
    restored._state_file = bridge._state_file
    try:
        restored._restore_state()
        assert restored.memory_archive.size == 1
        assert restored._ledger_receipts_written == 7
        # A current serializer never grants authority without qualified evidence.
        assert not restored._activated_types
        # Rejected qualification maps may clear or zero; no untrusted count survives.
        assert not any(restored._llm_noise_counts.values())
        assert not restored._qualification_evidence
        assert not restored._activation_timestamps
        restored.corpus_analyzer.maybe_analyze = lambda: []
        for _ in range(2):
            restored._discover_and_promote()
            assert not restored._activated_types
            assert not restored._activation_timestamps
            assert restored.memory_archive.size == 1
            assert restored._ledger_receipts_written == 7
    finally:
        restored.close()


@pytest.mark.parametrize("contextual", [False, True])
@pytest.mark.parametrize("maintenance_due", [False, True])
def test_expired_rules_cannot_suppress_the_first_batch_after_expiry(
    contextual, maintenance_due,
):
    bridge = CascadeBridge()
    bridge._last_promotion_time = 0 if maintenance_due else time.monotonic()
    bridge._activation_ttl_hours = 72
    expired = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    signal = Signal(signal_type="custom", severity="low", namespace="prod")
    if contextual:
        bridge._contextual_suppressor.add_noise_context("custom", "prod")
        bridge._activated_contexts["custom"].add("prod")
        bridge._context_activation_timestamps["custom:prod"] = expired
        bridge._contextual_suppressor.process([signal])
    else:
        bridge._noise_suppressor.add_noise_type("custom")
        bridge._activated_types.add("custom")
        bridge._activated_patterns["custom"] = "dominant_type"
        bridge._activation_timestamps["custom"] = expired
        bridge._noise_suppressor.process([signal])
    incoming = SimpleNamespace(
        signal_id=signal.signal_id, signal_type="custom", severity="low",
        namespace="prod", evidence={"message": "distinct new event"},
    )
    try:
        result = bridge.process([incoming])
        assert result["_remaining_signal_ids"] == [str(signal.signal_id)]
        assert "custom" not in bridge._activated_types
        assert "prod" not in bridge._activated_contexts["custom"]
    finally:
        bridge.close()
