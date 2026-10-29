"""Tests for fact-first classification: rubric, parsing, backend, cache composition."""
import json

import pytest

from cascade_compression.classifier import ClassificationResult
from cascade_compression.cascade.fact_first import (
    FactFirstClassifierBackend,
    Rubric,
    parse_facts,
)


class StubFactBackend:
    """Mimics GenerativeClassifierBackend's _complete surface."""

    name = "stub"
    micro_model = "micro"
    macro_model = "macro"

    def __init__(self, answer=None, fail=False):
        self._answer = answer
        self._fail = fail
        self.calls = 0
        self.system_prompt = "fact prompt"

    def _complete(self, client, model, text):
        self.calls += 1
        if self._fail:
            raise RuntimeError("boom")
        return self._answer


# ---- rubric v0 semantics ----

@pytest.mark.parametrize("facts,expected", [
    ({"designed": False, "impact": "confirmed", "recovery": "not_started"}, "real_incident"),
    ({"designed": True, "impact": "confirmed", "recovery": "na"}, "real_incident"),
    ({"designed": False, "impact": "suspected", "recovery": "na"}, "needs_attention"),
    ({"designed": False, "impact": "absent", "recovery": "not_started"}, "needs_attention"),
    ({"designed": False, "impact": "absent", "recovery": "in_progress"}, "needs_attention"),
    ({"designed": True, "impact": "absent", "recovery": "complete"}, "known_pattern"),
    ({"designed": True, "impact": "absent", "recovery": "na"}, "known_pattern"),
    ({"designed": True, "impact": "absent", "recovery": "not_started"}, "routine_noise"),
    (None, "needs_attention"),  # fail-open
    ({}, "needs_attention"),  # fail-open
])
def test_rubric_v0(facts, expected):
    assert Rubric().derive(facts) == expected


def test_rubric_fail_open_is_the_safe_direction():
    # missing facts never produce a suppressive label
    for missing in (None, {}, {"designed": True}):
        assert Rubric().derive(missing) in ("needs_attention",)


# ---- fact parsing ----

@pytest.mark.parametrize("answer,facts", [
    ('{"designed": false, "impact": "c", "recovery": "n"}',
     {"designed": False, "impact": "confirmed", "recovery": "na"}),
    ('```json\n{"designed": true, "impact": "a", "recovery": "c"}\n```',
     {"designed": True, "impact": "absent", "recovery": "complete"}),
    ('{"designed": false, "impact": "suspected", "recovery": "ip"}',
     {"designed": False, "impact": "suspected", "recovery": "in_progress"}),
    ('garbage', None),
    ('{"designed": false, "impact": "x", "recovery": "n"}', None),  # bad impact
])
def test_parse_facts(answer, facts):
    assert parse_facts(answer) == facts


# ---- backend wrapper ----

def test_backend_derives_label_from_facts():
    inner = StubFactBackend(answer='{"designed": false, "impact": "s", "recovery": "n"}')
    ff = FactFirstClassifierBackend(inner)
    r = ff.classify({"severity": "medium"}, "text")
    assert r.ok and r.label == "needs_attention"
    assert r.backend == "fact-first"
    assert inner.calls == 1


def test_backend_confirmed_becomes_real_incident():
    inner = StubFactBackend(answer='{"designed": false, "impact": "c", "recovery": "ns"}')
    r = FactFirstClassifierBackend(inner).classify({"severity": "medium"}, "t")
    assert r.label == "real_incident"


def test_backend_malformed_facts_fail_open():
    inner = StubFactBackend(answer="not json at all")
    r = FactFirstClassifierBackend(inner).classify({"severity": "medium"}, "t")
    assert not r.ok
    assert r.label == "needs_attention"  # fail-open, never suppressive
    assert r.error_code == "malformed_facts"


def test_backend_transport_failure_fails_open():
    inner = StubFactBackend(fail=True)
    r = FactFirstClassifierBackend(inner).classify({"severity": "medium"}, "t")
    assert not r.ok and r.label == "needs_attention"
    assert r.error_code == "request_failed"


def test_prompt_revision_binds_rubric_version():
    inner = StubFactBackend()
    ff = FactFirstClassifierBackend(inner)
    class OtherRubric(Rubric):
        VERSION = "v1"
    ff2 = FactFirstClassifierBackend(inner, rubric=OtherRubric())
    assert ff.prompt_revision != ff2.prompt_revision


# ---- cache composition: fact-first + template cache ----

def test_fact_first_composes_with_template_cache():
    from cascade_compression.cascade.template_cache import (
        TemplateCachedClassifierBackend,
    )
    inner = StubFactBackend(answer='{"designed": false, "impact": "s", "recovery": "n"}')
    ff = FactFirstClassifierBackend(inner)
    cached = TemplateCachedClassifierBackend(ff)
    sig = {"signal_type": "event_x", "severity": "medium",
           "content": {"message": "probe failed on host-1"}}
    r1 = cached.classify(sig, "t1")
    r2 = cached.classify(sig, "t2")  # same template: cache hit
    assert inner.calls == 1
    assert r1.label == r2.label == "needs_attention"
    assert cached.cache_hits == 1


def test_rubric_change_invalidates_cache():
    from cascade_compression.cascade.template_cache import (
        TemplateCachedClassifierBackend,
    )
    inner = StubFactBackend(answer='{"designed": false, "impact": "s", "recovery": "n"}')
    ff = FactFirstClassifierBackend(inner)
    cached = TemplateCachedClassifierBackend(ff)
    sig = {"signal_type": "event_x", "severity": "medium",
           "content": {"message": "probe failed on host-1"}}
    cached.classify(sig, "t")

    class NewRubric(Rubric):
        VERSION = "v1-experimental"
    ff._rubric = NewRubric()
    cached.classify(sig, "t")
    assert inner.calls == 2  # rubric version changed: cache invalidated


def test_factory_fact_first_opt_in(monkeypatch):
    import os
    monkeypatch.setenv("CASCADE_LLM_URL", "http://localhost:1")
    monkeypatch.setenv("CASCADE_LLM_KEY", "k")
    monkeypatch.setenv("CASCADE_LLM_MODEL", "m")
    monkeypatch.setenv("CASCADE_FACT_FIRST", "1")
    from cascade_compression.classifier import classifier_from_environment
    from cascade_compression.cascade.fact_first import FactFirstClassifierBackend
    clf = classifier_from_environment(
        url="http://localhost:1", key="k", micro_model="m", macro_model="m",
        system_prompt="domain pack prompt")
    assert isinstance(clf.generative, FactFirstClassifierBackend)
    # the wrapped backend carries the FACT prompt, not the domain prompt
    inner = clf.generative._inner
    assert "fact extractor" in inner.system_prompt.lower()
    monkeypatch.setenv("CASCADE_FACT_FIRST", "")
    clf2 = classifier_from_environment(
        url="http://localhost:1", key="k", micro_model="m", macro_model="m",
        system_prompt="domain pack prompt")
    assert not isinstance(clf2.generative, FactFirstClassifierBackend)


def test_factory_fact_first_composes_with_cache(monkeypatch):
    import os
    monkeypatch.setenv("CASCADE_LLM_URL", "http://localhost:1")
    monkeypatch.setenv("CASCADE_LLM_KEY", "k")
    monkeypatch.setenv("CASCADE_LLM_MODEL", "m")
    monkeypatch.setenv("CASCADE_FACT_FIRST", "1")
    monkeypatch.setenv("CASCADE_TEMPLATE_CACHE", "1")
    from cascade_compression.classifier import classifier_from_environment
    from cascade_compression.cascade.fact_first import FactFirstClassifierBackend
    from cascade_compression.cascade.template_cache import (
        TemplateCachedClassifierBackend,
    )
    clf = classifier_from_environment(
        url="http://localhost:1", key="k", micro_model="m", macro_model="m",
        system_prompt="s")
    # cache wraps fact-first: cache -> fact-first -> generative
    assert isinstance(clf.generative, TemplateCachedClassifierBackend)
    assert isinstance(clf.generative._inner, FactFirstClassifierBackend)


def test_factory_fact_first_gets_fact_token_budget(monkeypatch):
    """The 5-token label default truncates fact JSON mid-object.

    Found live: malformed_facts with the answer cut at '{"designed": false'.
    """
    import os
    monkeypatch.setenv("CASCADE_LLM_URL", "http://localhost:1")
    monkeypatch.setenv("CASCADE_LLM_KEY", "k")
    monkeypatch.setenv("CASCADE_LLM_MODEL", "m")
    monkeypatch.setenv("CASCADE_FACT_FIRST", "1")
    from cascade_compression.classifier import classifier_from_environment
    clf = classifier_from_environment(
        url="http://localhost:1", key="k", micro_model="m", macro_model="m",
        system_prompt="s")
    # without the cache: generative IS the fact-first wrapper;
    # its _inner is the fact-prompted GenerativeClassifierBackend
    inner = clf.generative._inner
    assert inner.completion_max_tokens is not None and inner.completion_max_tokens >= 30
