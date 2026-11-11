"""Tests for the template-cached classifier backend."""
from dataclasses import replace
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

    def cache_identity(self):
        return {'prompt_revision': self.prompt_revision}

    def classify(self, signal, text, client=None):
        self.calls += 1
        return self._result


def make_signal(severity="medium", message="probe failed on host-1",
                signal_type="event_unhealthy", namespace="ns1", source="s1"):
    return {"signal_type": signal_type, "severity": severity,
            "namespace": namespace, "source": source,
            "content": {"message": message}}


def test_exact_repeat_hits_cache():
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner)
    sig = make_signal()
    r1 = cached.classify(sig, "text")
    r2 = cached.classify(dict(sig, signal_id="next"), "text")
    assert inner.calls == 1
    assert cached.cache_hits == 1
    assert r2.label == r1.label


def test_parameter_variation_is_representation():
    assert normalize_template("retry 3 of 5 failed at 10.1.2.3") == \
        normalize_template("retry 7 of 9 failed at 192.168.0.1")
    assert normalize_template("exit code 137") != normalize_template("exit code")


@pytest.mark.parametrize("context_keys", [(), ("namespace",), ("tenant",)])
def test_namespace_always_scopes_reuse(context_keys):
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner, context_keys=context_keys)
    cached.classify(make_signal(namespace="prod"), "t")
    cached.classify(make_signal(namespace="test"), "t")
    assert inner.calls == 2


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


def test_normalization_cannot_erase_decision_evidence():
    # Legacy grouping options still accept both settings.
    inner = CountingBackend()
    cached = TemplateCachedClassifierBackend(inner, normalize_level="digits")
    cached.classify(make_signal(message="exit code 137"), "t")
    cached.classify(make_signal(message="exit code 42"), "t")
    assert inner.calls == 2
    # The verbatim setting also preserves numbers.
    inner2 = CountingBackend()
    cached2 = TemplateCachedClassifierBackend(inner2, normalize_level="none")
    cached2.classify(make_signal(message="amount=45.67"), "t")
    cached2.classify(make_signal(message="amount=99.99"), "t")
    assert inner2.calls == 2


@pytest.mark.parametrize("first,second", [
    ({"source": "a"}, {"source": "b"}),
    ({"cluster": "a"}, {"cluster": "b"}),
    ({"severity": "info"}, {"severity": "medium"}),
    ({"labels": {"scope": "a"}}, {"labels": {"scope": "b"}}),
    ({"content": {"message": "x" * 200 + "ok"}},
     {"content": {"message": "x" * 200 + "failed"}}),
    ({"content": {"message": "same", "conditions": [{"ready": True}]}},
     {"content": {"message": "same", "conditions": [{"ready": False}]}}),
    ({"tenant": {"id": 1}}, {"tenant": {"id": "1"}}),
    ({}, {"extra_evidence": {"loss": True}}),
    ({"namespace": "a\x1fb", "source": "c"}, {"namespace": "a", "source": "b\x1fc"}),
    ({}, {"tenant": ""}),
])
def test_changed_structure_with_identical_supplied_text_gets_fresh_answer(first, second):
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner, context_keys=("namespace", "source", "tenant"))
    assert cache.classify({**make_signal(), **first}, "short excerpt").label == "routine_noise"
    inner._result = replace(inner._result, label="real_incident")
    assert cache.classify({**make_signal(), **second}, "short excerpt").label == "real_incident"
    assert inner.calls == 2


def test_changed_supplied_text_gets_fresh_answer():
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    cache.classify(make_signal(), "recovered")
    inner._result = replace(inner._result, label="real_incident")
    assert cache.classify(make_signal(), "still failing").label == "real_incident"


@pytest.mark.parametrize("severity,valid", [
    ("medium", False), ("medium", 1), ("medium", "true"), ("HIGH", True),
    ("", True), (None, True), ([], True), ("high", True), ("critical", True),
])
def test_invalid_or_priority_severity_neither_reads_nor_seeds(severity, valid):
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    cache.classify(make_signal(), "t")
    sig = dict(make_signal(severity=severity), severity_input_valid=valid)
    inner._result = replace(inner._result, label="needs_attention")
    for _ in range(2):
        assert cache.classify(sig, "t").label == "needs_attention"
    assert inner.calls == 3
    assert cache.cache_bypassed == 2
    assert cache.stats()["template_cache_entries"] == 1


def test_missing_severity_bypasses_cache():
    sig = make_signal()
    del sig["severity"]
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    for _ in range(2):
        cache.classify(sig, "same")
    assert inner.calls == 2
    assert cache.stats()["template_cache_entries"] == 0


@pytest.mark.parametrize("content", [{"value": {1: "x"}}, {"value": ("x",)},
                                     {"value": float("nan")}, {"_truncated": True}])
def test_unrepresentable_or_truncated_evidence_bypasses(content):
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    sig = dict(make_signal(), content=content)
    for _ in range(2):
        cache.classify(sig, "same")
    assert inner.calls == 2
    assert cache.cache_bypassed == 2


def test_projected_queue_entries_cannot_share_a_benign_answer():
    # The queue can normalize severity and compact away a changed message tail.
    from cascade_compression.bridge import CascadeBridge

    bridge = CascadeBridge()
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    try:
        for i, tail in enumerate(("ok", "outage")):
            content, truncated = bridge._compact_llm_content({"message": "x" * 3000 + tail})
            assert truncated
            sig = dict(make_signal(), _work_id=str(i), content=content)
            result = cache.classify(sig, "same retained excerpt")
            assert result.label == inner._result.label
            inner._result = replace(inner._result, label="real_incident")
        assert cache.cache_bypassed == 2
    finally:
        bridge.close()


def test_hit_provenance_and_return_values_are_isolated():
    inner = CountingBackend()
    inner._result.alternatives["other"] = ClassificationResult(label="real_incident")
    cache = TemplateCachedClassifierBackend(inner)
    first = cache.classify(make_signal(), "t")
    first.alternatives["other"].label = "known_pattern"
    hit = cache.classify(make_signal(), "t")
    assert hit.alternatives["other"].label == "real_incident"
    assert not first.cache_hit and hit.cache_hit
    hit.label = "known_pattern"
    assert cache.classify(make_signal(), "t").label == "routine_noise"


def test_missing_backend_identity_bypasses():
    inner = CountingBackend()
    inner.cache_identity = None
    cache = TemplateCachedClassifierBackend(inner)
    for _ in range(2):
        cache.classify(make_signal(), "t")
    assert inner.calls == 2


def test_model_change_invalidates_even_with_same_prompt(monkeypatch):
    from cascade_compression.classifier import GenerativeClassifierBackend

    backend = GenerativeClassifierBackend(url="unused", key="", system_prompt="p",
                                          micro_model="old", macro_model="large")
    monkeypatch.setattr(backend, "_complete", lambda client, model, text:
                        "routine_noise" if model == "old" else "real_incident")
    cache = TemplateCachedClassifierBackend(backend)
    assert cache.classify(make_signal(), "t").label == "routine_noise"
    backend.micro_model = "new"
    assert cache.classify(make_signal(), "t").label == "real_incident"


@pytest.mark.parametrize("mode", ["generative", "compare", "hybrid"])
def test_cache_hit_cannot_earn_promotion(mode):
    from cascade_compression.bridge import CascadeBridge

    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    bridge = CascadeBridge()
    try:
        bridge.classifier.mode = mode
        bridge.classifier.generative = cache
        bridge.classifier.semantic = CountingBackend(result=ClassificationResult(status="error"))
        for signal_id in ("first", "second"):
            bridge._llm_active_threads = 1
            bridge._run_llm([dict(make_signal(), signal_id=signal_id, severity_input_valid=True)])
        assert bridge._llm_accounting_succeeded == 2
        assert inner.calls == 1
        assert bridge._llm_noise_counts["event_unhealthy"] == 1
        assert bridge._qualification_evidence["context:event_unhealthy:ns1"]["noise"] == 1
        assert sum(bridge._llm_calls_by_tier.values()) == 1
        assert [r["cache_hit"] for r in bridge._llm_results] == [False, True]
    finally:
        bridge.close()


def test_cached_important_result_withdraws_existing_authority():
    from cascade_compression.bridge import CascadeBridge

    inner = CountingBackend(result=ClassificationResult(label="real_incident", backend="stub"))
    bridge = CascadeBridge()
    bridge.classifier.generative = TemplateCachedClassifierBackend(inner)
    try:
        sig = dict(make_signal(), severity_input_valid=True)
        # Seed the cache before the bridge gains authority for this signal type.
        assert bridge.classifier.classify(sig).label == "real_incident"
        bridge._activated_types.add(sig["signal_type"])
        bridge._llm_active_threads = 1
        bridge._run_llm([dict(sig, signal_id="recheck")])
        assert inner.calls == 1
        assert bridge._llm_results[-1]["cache_hit"]
        assert sig["signal_type"] not in bridge._activated_types
    finally:
        bridge.close()


def test_unavailable_backend_identity_falls_through_to_fresh_classification():
    class UnversionedBackend(CountingBackend):
        def cache_identity(self):
            raise RuntimeError("revision unavailable")

    inner = UnversionedBackend()
    cache = TemplateCachedClassifierBackend(inner)
    for _ in range(2):
        assert cache.classify(make_signal(), "t").ok
    assert inner.calls == cache.cache_bypassed == 2


@pytest.mark.parametrize("durable", [False, True])
def test_lossless_queued_repeats_share_one_fresh_qualification(durable, tmp_path, monkeypatch):
    from cascade_compression.bridge import CascadeBridge
    from cascade_compression.cascade.protocol import Signal

    if durable:
        monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", str(tmp_path / "work.sqlite"))
    bridge = CascadeBridge(llm_url="synthetic")
    inner = CountingBackend()
    bridge.classifier.generative = TemplateCachedClassifierBackend(inner)
    try:
        for count in (2, 1):
            bridge._enqueue_llm_signals([Signal(**make_signal()) for _ in range(count)])
            assert len(bridge._llm_buffer) == 1
            bridge._dispatch_llm_batches()
            assert bridge.wait_for_llm(2)
        assert inner.calls == 1
        assert [r["cache_hit"] for r in bridge._llm_results] == [False, True]
        assert [r["occurrence_count"] for r in bridge._llm_results] == [2, 1]
        assert bridge._qualification_evidence["context:event_unhealthy:ns1"]["noise"] == 1
        assert bridge._llm_accounting_succeeded == 3
    finally:
        bridge.close()


@pytest.mark.parametrize("field", ["long_tail", "redacted", "timestamp", "context"])
def test_enqueue_never_shares_changed_evidence(field, tmp_path, monkeypatch):
    from cascade_compression.bridge import CascadeBridge
    from cascade_compression.cascade.protocol import Signal

    monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", str(tmp_path / "work.sqlite"))
    bridge = CascadeBridge()
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    bridge.classifier.generative = cache
    try:
        signals = []
        for value in ("first-example", "second-example"):
            sig = Signal(**make_signal())
            if field == "long_tail":
                sig.content["message"] = "x" * 2500 + value
            elif field == "redacted":
                sig.content["api_token"] = value
            elif field == "timestamp":
                sig.content["condition"] = {"lastTransitionTime": value}
            else:
                sig.namespace = value
            signals.append(sig)
        bridge._enqueue_llm_signals(signals)
        assert len(bridge._llm_buffer) == 2
        first, second = bridge._llm_buffer
        if field in ("long_tail", "redacted"):
            assert first["content"] == second["content"]
            assert b"first-example" not in (tmp_path / "work.sqlite").read_bytes()
        assert bridge._classify_entry(first, None).label == "routine_noise"
        inner._result = replace(inner._result, label="real_incident")
        assert bridge._classify_entry(second, None).label == "real_incident"
        assert inner.calls == 2
        if field in ("long_tail", "redacted"):
            assert cache.cache_bypassed == 2
    finally:
        bridge.close()


def test_normalized_invalid_severity_cannot_fold_seed_or_hit():
    from cascade_compression.bridge import CascadeBridge
    from cascade_compression.cascade.protocol import Signal

    bridge = CascadeBridge()
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    bridge.classifier.generative = cache
    try:
        bridge._enqueue_llm_signals([Signal(**make_signal(severity=s))
                                     for s in ("medium", "INVALID", "INVALID")])
        assert len(bridge._llm_buffer) == 3
        good, *invalid = bridge._llm_buffer
        assert bridge._classify_entry(good, None).ok
        for entry in invalid:
            result = bridge._classify_entry(entry, None)
            assert not result.ok and result.label == "needs_attention"
        assert inner.calls == 1 and cache.cache_hits == 0
    finally:
        bridge.close()


def test_recovered_queue_metadata_cannot_authorize_folding_or_cache(tmp_path, monkeypatch):
    from cascade_compression.bridge import CascadeBridge
    from cascade_compression.cascade.protocol import Signal

    monkeypatch.setenv("CASCADE_LLM_QUEUE_FILE", str(tmp_path / "work.sqlite"))
    bridge = CascadeBridge()
    try:
        bridge._enqueue_llm_signals([Signal(**make_signal())])
        old = dict(bridge._llm_buffer[0])
        old.pop("severity_input_valid")
        old.setdefault("_coalesce_key", "f" * 64)
        assert bridge._durable_queue.apply({old["_work_id"]: old})
    finally:
        bridge.close()
    restored = CascadeBridge()
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    restored.classifier.generative = cache
    try:
        restored._enqueue_llm_signals([Signal(**make_signal()), Signal(**make_signal())])
        assert [len(e["_signal_ids"]) for e in restored._llm_buffer] == [1, 2]
        old, live = restored._llm_buffer
        assert list(restored._llm_coalesce_index.values()) == [live]
        assert not restored._classify_entry(live, None).cache_hit
        assert restored._classify_entry(live, None).cache_hit
        # Even a byte-for-byte copy of a live row lacks process-owned provenance.
        for projection in (old, dict(live)):
            inner._result = replace(inner._result, label="real_incident")
            assert restored.classifier.classify(projection).label == "real_incident"
        assert cache.cache_bypassed == 2 and inner.calls == 3
    finally:
        restored.close()


def test_changed_live_queue_target_is_rechecked_before_folding_and_lookup():
    from cascade_compression.bridge import CascadeBridge
    from cascade_compression.cascade.protocol import Signal

    bridge = CascadeBridge()
    inner = CountingBackend()
    cache = TemplateCachedClassifierBackend(inner)
    bridge.classifier.generative = cache
    try:
        bridge._enqueue_llm_signals([Signal(**make_signal())])
        changed = bridge._llm_buffer[0]
        assert bridge._classify_entry(changed, None).label == "routine_noise"
        changed["content"]["impact"] = "outage"
        # A stale index entry must not take another member even if a caller also
        # supplies the former key as data on the changed projection.
        changed["_coalesce_key"] = "caller-supplied"
        bridge._enqueue_llm_signals([Signal(**make_signal())])
        assert [len(e["_signal_ids"]) for e in bridge._llm_buffer] == [1, 1]
        inner._result = replace(inner._result, label="real_incident")
        assert bridge._classify_entry(changed, None).label == "real_incident"
        assert cache.cache_bypassed == 1
        assert bridge._classify_entry(bridge._llm_buffer[1], None).cache_hit
    finally:
        bridge.close()


def test_explicit_per_delivery_context_disables_cross_member_reuse():
    from cascade_compression.bridge import CascadeBridge
    from cascade_compression.cascade.protocol import Signal

    bridge = CascadeBridge()
    inner = CountingBackend()
    bridge.classifier.generative = TemplateCachedClassifierBackend(inner, context_keys=("signal_id",))
    try:
        bridge._enqueue_llm_signals([Signal(**make_signal()), Signal(**make_signal())])
        assert len(bridge._llm_buffer) == 2
        for entry in bridge._llm_buffer:
            assert not bridge._classify_entry(entry, None).cache_hit
        assert inner.calls == 2
    finally:
        bridge.close()
