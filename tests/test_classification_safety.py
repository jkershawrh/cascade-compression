"""Malformed evidence cannot grant a benign label at either authority boundary."""
import json
from unittest.mock import Mock

import pytest

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.agents import ValueRuleAgent
from cascade_compression.cascade.fact_first import FactFirstClassifierBackend, Rubric, parse_facts
from cascade_compression.cascade.protocol import Signal
from cascade_compression.domains.robotic import VALUE_RULES


@pytest.mark.parametrize("designed", ["false", "true", 0, 1, None, [], {"value": False}])
def test_fact_bool_is_never_coerced(designed):
    raw = dict(designed=designed, impact="absent", recovery="na")
    assert parse_facts(json.dumps(raw)) is None
    assert Rubric().derive(raw) == "needs_attention"
    inner = Mock(micro_model="local", prompt_revision="fixture")
    inner._complete.return_value = json.dumps(raw)
    result = FactFirstClassifierBackend(inner).classify({}, "event")
    assert (result.ok, result.label, result.error_code) == (False, "needs_attention", "malformed_facts")


@pytest.mark.parametrize("raw", [
    '{"impact":"a","recovery":"n"}',
    '{"designed":false,"designed":true,"impact":"a","recovery":"n"}',
    '{"designed":true,"impact":"c","impact":"a","recovery":"n"}',
    '{"designed":true,"impact":"a","recovery":"n","contradictory":true}',
    '{"designed":true,"impact":["a"],"recovery":"n"}',
])
def test_missing_and_ambiguous_facts_abstain(raw):
    assert parse_facts(raw) is None


@pytest.mark.parametrize("severity,prior", [("malformed", True), (None, True), ("medium", False)])
def test_bad_severity_cannot_be_healthy_or_reused_in_queue(severity, prior):
    agent = ValueRuleAgent(VALUE_RULES)
    content = {"force_peak_n": 0.1, "force_sustained_share": 0.01}
    bad = Signal(signal_type="force_contact", severity=severity, severity_input_valid=prior, content=content)
    good = Signal(signal_type="force_contact", severity="medium", content=content)
    assert bad.severity_input_valid is False and agent.evaluate(bad) is None
    assert agent.else_label(bad) is None
    label = agent.else_label(good)
    assert label["label"] == "routine_noise"
    bridge = CascadeBridge(domain="robotic")
    try:
        bridge._enqueue_llm_signals([good, bad])
        assert [e["severity_input_valid"] for e in bridge._llm_buffer] == [True, False]
        result = bridge._classify_entry(bridge._llm_buffer[1], client=None)
        assert not result.ok and result.label == "needs_attention"
    finally:
        bridge.close()


@pytest.mark.parametrize("status", [{}, {"severity_input_valid": False}, {"severity_input_valid": 1}])
def test_recovered_rule_needs_positive_provenance(status):
    bridge = CascadeBridge(domain="robotic")
    try:
        entry = dict(severity="medium", _value_rule={"label":"routine_noise"}, **status)
        result = bridge._classify_entry(entry, client=None)
        assert not result.ok and result.label == "needs_attention"
    finally:
        bridge.close()


def test_valid_rule_completes_in_worker_without_counting_a_model_call():
    b = CascadeBridge(domain="robotic")
    try:
        sig = Signal(signal_type="force_contact", severity="medium",
                     content={"force_peak_n": 0.1, "force_sustained_share": 0.01})
        rule = b._value_rule_agent.else_label(sig)
        b._enqueue_llm_signals([sig], {str(sig.signal_id): rule})
        entry = b._llm_buffer.pop()
        b._llm_active_threads = 1
        b._run_llm([entry])
        assert b._llm_accounting_succeeded == 1 and b._llm_accounting_failed == 0
        assert b.stats.value_rule_classified == 1 and sum(b._llm_calls_by_tier.values()) == 0
        assert b._llm_results[-1]["tier"] == "nano"
    finally:
        b.close()
