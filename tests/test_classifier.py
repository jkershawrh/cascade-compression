"""Policy tests for optional semantic classification.

These tests use protocol-compatible stubs, so the default test suite does not
require the optional gRPC runtime or a running llm-d-sc service.
"""

from dataclasses import replace
import json

from cascade_compression.classifier import (
    CascadeClassifier,
    ClassificationResult,
    ComparisonRecorder,
    RankedLabel,
    classifier_from_environment,
    load_taxonomy_metadata,
    normalize_label,
    normalize_signal_text,
    redact,
)


class StubBackend:
    def __init__(self, name, result):
        self.name = name
        self.result = result
        self.calls = 0

    def classify(self, signal, text, client=None):
        self.calls += 1
        return replace(self.result)


def result(label, backend, margin=None, status="ok"):
    ranked = []
    if backend == "semantic" and label:
        other = "needs_attention" if label != "needs_attention" else "known_pattern"
        ranked = [
            RankedLabel(label, 0.75),
            RankedLabel(other, 0.75 - (margin or 0.0)),
            RankedLabel("real_incident", 0.10),
            RankedLabel("routine_noise", 0.05),
        ]
    return ClassificationResult(
        label=label,
        backend=backend,
        margin=margin,
        confidence=margin,
        ranked=ranked,
        status=status,
        error_code="unavailable" if status != "ok" else "",
    )


def classifier(semantic_result, generative_label="needs_attention", **kwargs):
    generative = StubBackend(
        "generative", result(generative_label, "generative")
    )
    semantic = StubBackend("semantic", semantic_result)
    instance = CascadeClassifier(
        generative,
        semantic,
        mode=kwargs.get("mode", "hybrid"),
        hybrid_margin=kwargs.get("hybrid_margin", 0.20),
        hybrid_suppress_margin=kwargs.get("hybrid_suppress_margin", 0.40),
        recorder=ComparisonRecorder(),
    )
    return instance, generative, semantic


def signal(severity="medium"):
    return {
        "signal_id": "public-test-signal",
        "signal_type": "service_health",
        "severity": severity,
        "namespace": "example",
        "content": {"message": "Unusual error rate requires review"},
    }


def test_default_mode_preserves_generative_behavior(monkeypatch):
    monkeypatch.delenv("CASCADE_CLASSIFIER_MODE", raising=False)
    monkeypatch.delenv("CASCADE_SC_ADDRESS", raising=False)
    instance = classifier_from_environment(
        url="", key="", micro_model="small", macro_model="large",
        system_prompt="Classify the signal.",
    )
    assert instance.mode == "generative"
    assert instance.semantic is None
    assert instance.config_error == ""


def test_hybrid_accepts_high_margin_semantic_result():
    instance, generative, semantic = classifier(
        result("needs_attention", "semantic", margin=0.35)
    )
    selected = instance.classify(signal())
    assert selected.label == "needs_attention"
    assert selected.authoritative_backend == "semantic"
    assert not selected.fallback_used
    assert semantic.calls == 1
    assert generative.calls == 0
    assert instance.coverage_stats()["sc_authoritative"] == 1


def test_hybrid_low_margin_falls_back_to_generative():
    instance, generative, _ = classifier(
        result("needs_attention", "semantic", margin=0.10),
        generative_label="real_incident",
    )
    selected = instance.classify(signal())
    assert selected.label == "real_incident"
    assert selected.authoritative_backend == "generative"
    assert selected.fallback_used
    assert generative.calls == 1
    stats = instance.coverage_stats()
    assert stats["margin_gated"] == 1
    assert stats["generative_authoritative"] == 1


def test_hybrid_requires_stronger_margin_for_suppression():
    instance, generative, _ = classifier(
        result("routine_noise", "semantic", margin=0.30),
        generative_label="needs_attention",
    )
    selected = instance.classify(signal())
    assert selected.label == "needs_attention"
    assert selected.fallback_used
    assert generative.calls == 1


def test_high_severity_suppression_always_falls_back():
    instance, generative, _ = classifier(
        result("routine_noise", "semantic", margin=0.90),
        generative_label="real_incident",
    )
    selected = instance.classify(signal(severity="critical"))
    assert selected.label == "real_incident"
    assert selected.fallback_used
    assert generative.calls == 1
    assert instance.coverage_stats()["severity_gated"] == 1


def test_semantic_failure_falls_back_without_dropping_signal():
    instance, generative, _ = classifier(
        result("", "semantic", status="error"),
        generative_label="needs_attention",
    )
    selected = instance.classify(signal())
    assert selected.label == "needs_attention"
    assert selected.authoritative_backend == "generative"
    assert selected.fallback_used
    assert generative.calls == 1
    assert instance.coverage_stats()["sc_failure"] == 1


def test_compare_mode_keeps_generative_authoritative():
    instance, generative, semantic = classifier(
        result("routine_noise", "semantic", margin=0.80),
        generative_label="needs_attention",
        mode="compare",
    )
    selected = instance.classify(signal())
    assert selected.label == "needs_attention"
    assert selected.authoritative_backend == "generative"
    assert not selected.fallback_used
    assert generative.calls == semantic.calls == 1
    stats = instance.coverage_stats()
    assert stats["sc_authoritative"] == 0
    assert stats["generative_authoritative"] == 1


def test_semantic_mode_still_applies_margin_policy():
    instance, generative, _ = classifier(
        result("needs_attention", "semantic", margin=0.10),
        generative_label="real_incident",
        mode="semantic",
    )
    selected = instance.classify(signal())
    assert selected.authoritative_backend == "generative"
    assert selected.fallback_used
    assert generative.calls == 1


def test_semantic_mode_cannot_suppress_critical_signal_directly():
    instance, generative, _ = classifier(
        result("routine_noise", "semantic", margin=0.90),
        generative_label="real_incident",
        mode="semantic",
    )
    selected = instance.classify(signal(severity="critical"))
    assert selected.label == "real_incident"
    assert selected.fallback_used
    assert generative.calls == 1


def test_label_and_signal_normalization_are_deterministic():
    assert normalize_label("Classification: `real_incident`.") == "real_incident"
    normalized = normalize_signal_text(
        "pod-a1b2c3 restarted at 10.1.2.3:8080 count=42"
    )
    assert "10.1.2.3" not in normalized
    assert "count=42" not in normalized


def test_public_taxonomy_matches_classifier_contract():
    signal_name, revision = load_taxonomy_metadata(
        "config/cascade-sc-taxonomy.json"
    )
    assert signal_name == "cascade_classification"
    assert revision


def test_candidate_taxonomy_cannot_be_loaded(tmp_path):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps({
        "labels": sorted([
            "routine_noise", "known_pattern", "needs_attention", "real_incident"
        ]),
        "signal": "cascade_classification",
        "taxonomy_revision": "candidate-v2",
        "lifecycle": {"status": "candidate"},
    }))
    try:
        load_taxonomy_metadata(str(path))
    except ValueError as exc:
        assert "must be approved" in str(exc)
    else:
        raise AssertionError("candidate taxonomy was accepted")


def test_evidence_redaction_covers_inline_and_url_credentials():
    value = redact({
        "message": (
            "password=hunter2 token:abc123 "
            "postgresql://operator:letmein@example.invalid/data"
        )
    })["message"]
    assert "hunter2" not in value
    assert "abc123" not in value
    assert "letmein" not in value
    assert value.count("[REDACTED]") == 3
