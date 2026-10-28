"""Tests for the template-cached classifier backend."""
import time

import pytest

from cascade_compression.classifier import ClassificationResult
from cascade_compression.cascade.template_cache import (
    TemplateCachedClassifierBackend,
    normalize_template,
)

LABELS = dict(label="routine_noise", backend="stub", model_revision="m",
              confidence=None, prompt_revision="p1", latency_ms=1.0)


class CountingBackend:
    """Stub backend that counts calls and returns fixed results."""

    name = "stub"

    def __init__(self, result=None, prompt_revision="p1"):
        self.calls = 0
        self.prompt_revision = prompt_revision
        self._result = result or ClassificationResult(**LABELS)

    def classify(self, signal, text, client=None):
        self.calls += 1
        return self._result


def make_signal(severity="medium", message="probe failed on host-1",
                signal_type="event_unhealthy", namespace="ns1", source="s1"):
    return {"signal_type": signal_type, "severity": severity,
            "namespace": namespace, "source": source,
            "content": {"message": message}}


def test_repeat_template_hits_cache():
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner)
    sig = make_signal()
    r1 = cached.classify(sig, "text")
    r2 = cached.classify(make_signal(message="probe failed on host-2"), "text2")
    assert inner.calls == 1
    assert cached.cache_hits == 1
    assert r2.label == r1.label


def test_parameter_variation_is_representation():
    assert normalize_template("retry 3 of 5 failed at 10.1.2.3") == \
        normalize_template("retry 7 of 9 failed at 192.168.0.1")
    assert normalize_template("exit code 137") != normalize_template("exit code")


def test_context_keys_opt_in_separation():
    # context scoping is opt-in: facts are template-intrinsic by default
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner)  # default: no context keys
    cached.classify(make_signal(namespace="prod"), "t")
    cached.classify(make_signal(namespace="test"), "t")
    assert inner.calls == 1  # same template: shared fact across contexts

    inner2 = CountingBackend()
    cached2 = TemplateCachedClassifierBackend(inner2, context_keys=("namespace",))
    cached2.classify(make_signal(namespace="prod"), "t")
    cached2.classify(make_signal(namespace="test"), "t")
    assert inner2.calls == 2  # opted-in context scoping separates entries


def test_high_severity_bypasses_cache():
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner)
    cached.classify(make_signal(severity="critical"), "t")
    cached.classify(make_signal(severity="critical"), "t")
    assert inner.calls == 2
    assert cached.cache_bypassed == 2


def test_prompt_revision_invalidates():
    inner = CountingBackend(prompt_revision="p1")
    cached = TemplateCachedClassifierBackend(inner)
    cached.classify(make_signal(), "t")
    inner.prompt_revision = "p2"  # a few-shot/prompt update
    cached.classify(make_signal(), "t")
    assert inner.calls == 2
    assert cached.cache_stale_prompt >= 0  # counter surface exists


def test_ttl_expiry_re_extracts():
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner, ttl_seconds=0.05)
    cached.classify(make_signal(), "t")
    time.sleep(0.08)
    cached.classify(make_signal(), "t")
    assert inner.calls == 2


def test_failed_results_not_cached():
    class FailingBackend(CountingBackend):
        def classify(self, signal, text, client=None):
            super().classify(signal, text, client)
            return ClassificationResult(label="", backend="stub",
                                        model_revision="m", confidence=None,
                                        prompt_revision="p1", latency_ms=1.0,
                                        error_code="request_failed", error="x")

    inner = FailingBackend()
    cached = TemplateCachedClassifierBackend(inner)
    cached.classify(make_signal(), "t")
    cached.classify(make_signal(), "t")
    assert inner.calls == 2  # failures never served from cache


def test_stats_surface():
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner)
    cached.classify(make_signal(), "t")
    cached.classify(make_signal(), "t")
    stats = cached.stats()
    assert stats["template_cache_hits"] == 1
    assert stats["template_cache_entries"] == 1


def test_factory_opt_in(monkeypatch):
    """CASCADE_TEMPLATE_CACHE=1 wraps the generative backend."""
    import os
    monkeypatch.setenv("CASCADE_LLM_URL", "http://localhost:1")
    monkeypatch.setenv("CASCADE_LLM_KEY", "k")
    monkeypatch.setenv("CASCADE_LLM_MODEL", "m")
    monkeypatch.setenv("CASCADE_TEMPLATE_CACHE", "1")
    from cascade_compression.classifier import classifier_from_environment
    clf = classifier_from_environment(
        url="http://localhost:1", key="k", micro_model="m", macro_model="m",
        system_prompt="s")
    from cascade_compression.cascade.template_cache import (
        TemplateCachedClassifierBackend)
    assert isinstance(clf.generative, TemplateCachedClassifierBackend)
    monkeypatch.setenv("CASCADE_TEMPLATE_CACHE", "")
    clf2 = classifier_from_environment(
        url="http://localhost:1", key="k", micro_model="m", macro_model="m",
        system_prompt="s")
    assert not isinstance(clf2.generative, TemplateCachedClassifierBackend)


def test_normalization_level_is_deployment_choice():
    # digits: numbers are parameters (templated streams)
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner, normalize_level="digits")
    cached.classify(make_signal(message="exit code 137"), "t")
    cached.classify(make_signal(message="exit code 42"), "t")
    assert inner.calls == 1
    # none: numbers are semantics (amounts, measurements)
    inner2 = CountingBackend()
    cached2 = TemplateCachedClassifierBackend(inner2, normalize_level="none")
    cached2.classify(make_signal(message="amount=45.67"), "t")
    cached2.classify(make_signal(message="amount=99.99"), "t")
    assert inner2.calls == 2
