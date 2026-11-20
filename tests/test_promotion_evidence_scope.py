"""Promotion evidence must be readable without guessing what a field counts.

The live trail on 2026-11-19 carried a promotion with
``false_negative_count: 0`` next to ``false_negative_rate: 0.0175`` and
``accuracy: 0.9825``. All three were honest: the rate and accuracy were
lifetime aggregates over 57 samples, while the count was the false negatives
seen in the latest validation batch. Nothing in the record said so, which made
the promotion look arithmetically impossible.

These tests pin the scopes: ``false_negative_count`` divides by
``samples_tested`` to reproduce ``false_negative_rate``, the batch counter is
carried separately, and the per-agent ``promoted`` summary names the gate its
``status`` belongs to.
"""

from datetime import datetime, timezone

import pytest

from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.promotion import (
    AgentMetrics,
    PromotionEngine,
    RuleAgent,
)
from cascade_compression.lifecycle import REVISION


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_STATE_FILE", str(tmp_path / "state.json"))
    b = CascadeBridge()
    b.corpus_analyzer.maybe_analyze = lambda: []
    b.promotion.human_gate_enabled = False
    b._human_gate = False
    metrics = AgentMetrics(
        name="synthetic_rule",
        config={"signal_type": "routine", "pattern_type": "dominant_type"},
    )
    b._agent_metrics[metrics.name] = metrics
    b._agent_rules[metrics.name] = RuleAgent({
        "name": metrics.name,
        "signal_types": ["routine"],
        "condition": {"field": "value", "operator": "lt", "value": 10},
    })
    yield b
    b.close()


def qualify(bridge, noise, important):
    """Install the qualification record a sweep would have earned."""
    metrics = bridge._agent_metrics["synthetic_rule"]
    rule = bridge._agent_rules["synthetic_rule"]
    now = datetime.now(timezone.utc).isoformat()
    bridge._qualification_evidence["agent:synthetic_rule"] = {
        "binding": bridge._rule_binding(metrics, rule),
        "revision": REVISION,
        "noise": noise,
        "important": important,
        "first_observed_at": now,
        "last_observed_at": now,
    }


def test_event_count_reproduces_the_rate():
    engine = PromotionEngine()
    agent = AgentMetrics(
        name="synthetic_rule",
        tier="draft",
        samples_tested=57,
        accuracy=56 / 57,
        false_positive_rate=1 / 57,
        false_negative_rate=1 / 57,
        false_negative_count=1,
        last_batch_fn_count=0,
    )

    engine._do_promote(agent, "draft", "candidate", reason="test")
    evidence = engine.drain_events()[0].to_dict()["evidence"]

    assert evidence["false_negative_count"] == 1
    assert evidence["false_negative_count_last_batch"] == 0
    assert (evidence["false_negative_count"] / evidence["samples_tested"]
            == pytest.approx(evidence["false_negative_rate"], abs=1e-4))
    assert evidence["accuracy"] == pytest.approx(
        1 - evidence["false_negative_rate"], abs=1e-4)


def test_bridge_promotion_records_a_count_that_matches_its_rate(bridge):
    qualify(bridge, noise=56, important=1)

    bridge._discover_and_promote(new_agents=[])

    promotions = [e for e in bridge.get_promotion_log(50)
                  if e.get("event") == "promotion"
                  and e.get("agent") == "synthetic_rule"]
    assert promotions, "a promotion event should follow the promoted summary"
    evidence = promotions[-1]["evidence"]

    assert evidence["samples_tested"] == 57
    assert evidence["false_negative_count"] == 1
    assert evidence["false_negative_count_last_batch"] == 0
    assert (evidence["false_negative_count"] / evidence["samples_tested"]
            == pytest.approx(evidence["false_negative_rate"], abs=1e-4))


def test_promoted_summary_names_its_gate_and_origin(bridge):
    qualify(bridge, noise=57, important=0)

    bridge._discover_and_promote(new_agents=[])

    summaries = [e for e in bridge.get_promotion_log(50)
                 if e.get("event") == "promoted"
                 and e.get("agent") == "synthetic_rule"]
    assert len(summaries) == 1
    summary = summaries[0]

    assert summary["from_tier"] == "draft"
    assert summary["tier"] == "candidate"
    assert summary["transitions"] == 1
    assert summary["mirror_of"] == "promotion"
    # 57 samples cannot clear the 200-sample nano gate, so "red" is correct
    # here and the scope says which gate it is red against.
    assert summary["status"] == "red"
    assert summary["status_scope"] == "nano_activation_gate"
