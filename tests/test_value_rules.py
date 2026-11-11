"""Tests for the ValueRuleAgent — deterministic telemetry threshold rules."""

import pytest

from cascade_compression.cascade.agents import ValueRuleAgent
from cascade_compression.cascade.protocol import Outcome, Signal


def _signal(content, **kwargs):
    kwargs.setdefault("signal_type", "maintenance_telemetry")
    kwargs.setdefault("severity", "medium")
    return Signal(source="M1", labels={"domain": "robotic"},
                  content=content, **kwargs)


SPEC = {
    "revision": "test-value-rules-v1",
    "derived": {
        "temp_diff_k": ["sub", ["field", "process_temp_k"], ["field", "air_temp_k"]],
        "power_w": ["mul", ["field", "torque_nm"], ["field", "rotational_speed_rpm"],
                    ["const", 0.10471975511965977]],
    },
    "rules": [
        {   # fires first when both hold — first match wins
            "name": "heat_dissipation_risk",
            "classification": "heat_dissipation_failure_risk",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "temp_diff_k", "op": "lt", "value": 8.6},
                {"field": "rotational_speed_rpm", "op": "lt", "value": 1380},
            ],
        },
        {
            "name": "power_above_rated",
            "classification": "power_failure_risk",
            "outcome": "classify",
            "confidence": 0.9,
            "when": [{"field": "power_w", "op": "gt", "value": 9000}],
        },
        {
            "name": "tool_wear_degraded",
            "classification": "tool_wear_degraded",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [{"field": "tool_wear_min", "op": "ge", "value": 200}],
        },
    ],
}


@pytest.fixture()
def agent():
    return ValueRuleAgent(SPEC)


class TestRuleEvaluation:
    def test_derived_sub_and_condition_match(self, agent):
        # temp diff 7.9K at 1300rpm — HDF signature.
        s = _signal({"air_temp_k": 300.0, "process_temp_k": 307.9,
                     "rotational_speed_rpm": 1300, "torque_nm": 40.0,
                     "tool_wear_min": 10})
        decisions = agent.process([s])
        assert len(decisions) == 1
        d = decisions[0]
        assert d.outcome == Outcome.ESCALATE
        assert d.classification == "heat_dissipation_failure_risk"
        assert "temp_diff_k=7.9" in d.evidence
        assert d.metadata["value_rule"] == "heat_dissipation_risk"
        assert d.metadata["spec_revision"] == "test-value-rules-v1"

    def test_derived_mul_with_constant(self, agent):
        # 42.8Nm at 1551rpm -> ~6948W (within rated window: no fire).
        s = _signal({"air_temp_k": 298.1, "process_temp_k": 308.6,
                     "rotational_speed_rpm": 1551, "torque_nm": 42.8,
                     "tool_wear_min": 0})
        assert agent.process([s]) == []
        # 60Nm at 1600rpm -> ~10052W — power above rated.
        s2 = _signal({"air_temp_k": 298.1, "process_temp_k": 308.6,
                      "rotational_speed_rpm": 1600, "torque_nm": 60.0,
                      "tool_wear_min": 0})
        decisions = agent.process([s2])
        assert len(decisions) == 1
        assert decisions[0].outcome == Outcome.CLASSIFY  # "classify" outcome
        assert decisions[0].classification == "power_failure_risk"

    def test_first_match_wins(self, agent):
        # Both heat-dissipation and tool-wear hold; only the first rule fires.
        s = _signal({"air_temp_k": 300.0, "process_temp_k": 307.0,
                     "rotational_speed_rpm": 1300, "torque_nm": 40.0,
                     "tool_wear_min": 250})
        decisions = agent.process([s])
        assert len(decisions) == 1
        assert decisions[0].metadata["value_rule"] == "heat_dissipation_risk"

    def test_and_semantics_requires_all_conditions(self, agent):
        # Low temp diff but high speed — HDF needs both; no fire.
        s = _signal({"air_temp_k": 300.0, "process_temp_k": 307.0,
                     "rotational_speed_rpm": 1600, "torque_nm": 40.0,
                     "tool_wear_min": 10})
        assert agent.process([s]) == []

    def test_missing_fields_do_not_fire(self, agent):
        # Event-type signal without numeric fields — untouched (hybrid lane).
        s = _signal({"message": "[ST-01] cycle pick part BRK-48392 pos_ok"})
        assert agent.process([s]) == []

    def test_partial_fields_do_not_fire(self, agent):
        # Temperature present but torque missing: derived power is undefined.
        # Temp diff is large so the heat rule does not apply either.
        s = _signal({"air_temp_k": 300.0, "process_temp_k": 317.9,
                     "rotational_speed_rpm": 1300, "tool_wear_min": 10})
        assert agent.process([s]) == []

    def test_non_numeric_and_bool_values_do_not_fire(self, agent):
        s = _signal({"tool_wear_min": True})  # bool is not a measurement
        assert agent.process([s]) == []
        s2 = _signal({"tool_wear_min": "high"})
        assert agent.process([s2]) == []

    def test_boundary_equality(self, agent):
        # tool_wear exactly 200 matches ge 200.
        s = _signal({"tool_wear_min": 200, "air_temp_k": 298.0,
                     "process_temp_k": 309.0, "rotational_speed_rpm": 1551,
                     "torque_nm": 42.0})
        decisions = agent.process([s])
        assert decisions and decisions[0].classification == "tool_wear_degraded"

    def test_fire_counter_tracks_rules(self, agent):
        s = _signal({"tool_wear_min": 210})
        agent.process([s])
        agent.process([s])
        agent.process([s])
        assert agent.fires["tool_wear_degraded"] == 3


class TestSpecValidation:
    def test_missing_revision_raises(self):
        with pytest.raises(ValueError, match="revision"):
            ValueRuleAgent({"rules": [{"name": "x", "classification": "y",
                                       "when": [{"field": "a", "op": "gt", "value": 1}]}]})

    def test_empty_rules_raises(self):
        with pytest.raises(ValueError, match="rules"):
            ValueRuleAgent({"revision": "v1", "rules": []})

    def test_suppress_outcome_rejected(self):
        rule = {"name": "x", "classification": "y", "outcome": "suppress",
                "when": [{"field": "a", "op": "gt", "value": 1}]}
        with pytest.raises(ValueError, match="outcome"):
            ValueRuleAgent({"revision": "v1", "rules": [rule]})

    def test_unknown_op_rejected(self):
        rule = {"name": "x", "classification": "y",
                "when": [{"field": "a", "op": "near", "value": 1}]}
        with pytest.raises(ValueError, match="unknown op"):
            ValueRuleAgent({"revision": "v1", "rules": [rule]})

    def test_bad_expression_rejected(self):
        spec = {"revision": "v1",
                "derived": {"x": ["modulo", ["field", "a"], ["field", "b"]]},
                "rules": [{"name": "x", "classification": "y",
                           "when": [{"field": "x", "op": "gt", "value": 1}]}]}
        with pytest.raises(ValueError, match="unknown operator"):
            ValueRuleAgent(spec)

    def test_sub_requires_two_operands(self):
        spec = {"revision": "v1",
                "derived": {"x": ["sub", ["field", "a"], ["field", "b"],
                                   ["field", "c"]]},
                "rules": [{"name": "x", "classification": "y",
                           "when": [{"field": "x", "op": "gt", "value": 1}]}]}
        with pytest.raises(ValueError, match="exactly two"):
            ValueRuleAgent(spec)

    def test_division_by_zero_fails_condition_not_crash(self):
        spec = {"revision": "v1",
                "derived": {"ratio": ["div", ["field", "a"], ["field", "b"]]},
                "rules": [{"name": "ratio_high", "classification": "ratio",
                           "when": [{"field": "ratio", "op": "gt", "value": 1}]}]}
        agent = ValueRuleAgent(spec)
        s = _signal({"a": 10.0, "b": 0.0})
        assert agent.process([s]) == []


class TestCategoricalAndRangeOps:
    SPEC = {
        "revision": "cats-v1",
        "rules": [
            {"name": "type_l_overstrain", "classification": "overstrain",
             "when": [
                 {"field": "machine_type", "op": "in", "value": ["L", "M"]},
                 {"field": "strain", "op": "gt", "value": 100},
             ]},
            {"name": "wear_window", "classification": "wear_window",
             "when": [{"field": "tool_wear_min", "op": "between", "value": [200, 240]}]},
            {"name": "power_outside", "classification": "power_out",
             "when": [{"field": "power_w", "op": "outside", "value": [3500, 9000]}]},
        ],
    }

    def test_membership_gate(self):
        agent = ValueRuleAgent(self.SPEC)
        s = _signal({"machine_type": "L", "strain": 150})
        assert agent.process([s]) and agent.process([s])[0].metadata["value_rule"] == "type_l_overstrain"
        s2 = _signal({"machine_type": "H", "strain": 150})
        assert agent.process([s2]) == []

    def test_between_and_outside(self):
        agent = ValueRuleAgent(self.SPEC)
        in_window = _signal({"tool_wear_min": 220, "machine_type": "H"})
        assert agent.process([in_window])[0].classification == "wear_window"
        low_power = _signal({"power_w": 3000, "machine_type": "H"})
        assert agent.process([low_power])[0].classification == "power_out"
        ok = _signal({"power_w": 5000, "machine_type": "H", "tool_wear_min": 10})
        assert agent.process([ok]) == []

    def test_eq_ne_categorical(self):
        spec = {"revision": "eq-v1",
                "rules": [
                    {"name": "is_h", "classification": "h_variant",
                     "when": [{"field": "machine_type", "op": "eq", "value": "H"}]},
                    {"name": "not_h", "classification": "other",
                     "when": [{"field": "machine_type", "op": "ne", "value": "H"}]},
                ]}
        agent = ValueRuleAgent(spec)
        h = _signal({"machine_type": "H"})
        assert agent.process([h])[0].classification == "h_variant"
        other = _signal({"machine_type": "L"})
        assert agent.process([other])[0].classification == "other"
        missing = _signal({})
        assert agent.process([missing]) == []  # missing field fails both


class TestRoboticPackSpec:
    def test_pack_declares_valid_value_rules(self):
        from cascade_compression.domain_plugins import load_domain_plugin
        plugin = load_domain_plugin("robotic")
        assert plugin.value_rules is not None
        agent = ValueRuleAgent(plugin.value_rules)  # validates the spec
        assert agent.revision == "robotic-value-rules-v4"
        # The pack rules only escalate or classify.
        for rule in plugin.value_rules["rules"]:
            assert rule["outcome"] in ("escalate", "classify")

    def test_pack_labels_are_valid_taxonomy_labels(self):
        from cascade_compression.classifier import CASCADE_LABELS
        from cascade_compression.domain_plugins import load_domain_plugin
        rules = load_domain_plugin("robotic").value_rules["rules"]
        labels = {r["label"] for r in rules if "label" in r}
        assert labels  # the pack declares authoritative labels
        assert labels <= CASCADE_LABELS

    def test_pack_rules_do_not_fire_on_event_signals(self):
        from cascade_compression.domain_plugins import load_domain_plugin
        agent = ValueRuleAgent(load_domain_plugin("robotic").value_rules)
        event = _signal({"message": "[ST-01] cycle pick part BRK-48392 pos_ok"})
        assert agent.process([event]) == []

    def test_packs_without_value_rules_are_untouched(self):
        from cascade_compression.domain_plugins import load_domain_plugin
        plugin = load_domain_plugin("kubernetes")
        assert plugin.value_rules is None


class TestValueRuleAgentLabel:
    def test_label_rides_in_decision_metadata(self):
        spec = {"revision": "v1",
                "rules": [{"name": "hot", "classification": "overheat",
                           "label": "real_incident",
                           "when": [{"field": "temp", "op": "gt", "value": 100}]}]}
        agent = ValueRuleAgent(spec)
        s = _signal({"temp": 150.0})
        (d,) = agent.process([s])
        assert d.metadata["label"] == "real_incident"
        assert d.metadata["value_rule"] == "hot"

    def test_bad_label_rejected(self):
        spec = {"revision": "v1",
                "rules": [{"name": "hot", "classification": "overheat",
                           "label": "", "when": [{"field": "t", "op": "gt", "value": 1}]}]}
        with pytest.raises(ValueError, match="label"):
            ValueRuleAgent(spec)

    def test_no_label_is_allowed(self):
        spec = {"revision": "v1",
                "rules": [{"name": "hot", "classification": "overheat",
                           "when": [{"field": "temp", "op": "gt", "value": 100}]}]}
        agent = ValueRuleAgent(spec)
        (d,) = agent.process([_signal({"temp": 150.0})])
        assert "label" not in d.metadata


class TestBridgeValueRulePath:
    """The authoritative label path: rule matches skip the model call."""

    def _telemetry_signal(self, **overrides):
        evidence = {
            "message": "machine M1 type L: air 298.1K proc 308.6K speed 1551rpm torque 60.0Nm tool_wear 210min",
            "machine_type": "L",
            "air_temp_k": 298.1,
            "process_temp_k": 308.6,
            "rotational_speed_rpm": 1551,
            "torque_nm": 60.0,
            "tool_wear_min": 210,
        }
        evidence.update(overrides)
        sig = _FakeTelemetry()
        sig.evidence = evidence
        return sig

    def test_bridge_registers_value_rule_agent_for_robotic_domain(self):
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="robotic")
        assert bridge._value_rule_agent is not None
        assert "value_rule" in [a.name for a in bridge.pipeline._agents]

    def test_env_gate_disables_value_rules(self, monkeypatch):
        from cascade_compression.bridge import CascadeBridge
        monkeypatch.setenv("CASCADE_VALUE_RULES", "off")
        bridge = CascadeBridge(domain="robotic")
        assert bridge._value_rule_agent is None

    def test_packs_without_rules_register_nothing(self):
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="kubernetes")
        assert bridge._value_rule_agent is None
        assert "value_rule" not in [a.name for a in bridge.pipeline._agents]

    def test_matching_signal_enqueues_with_authoritative_label(self):
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="robotic")
        # strain = 210 * 60 = 12600 > 11000 (type L) -> overstrain match
        bridge.process([self._telemetry_signal()])
        entries = list(bridge._llm_buffer) + list(bridge._llm_priority_buffer)
        matched = [e for e in entries if e.get("_value_rule")]
        assert matched, "value-rule match must be enqueued with its label"
        rule = matched[0]["_value_rule"]
        assert rule["label"] == "real_incident"
        assert rule["rule"] == "overstrain_l"
        assert rule["revision"] == "robotic-value-rules-v4"

    def test_non_matching_signal_enqueues_without_rule(self):
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="robotic")
        # Everything in range: no rule fires; the entry carries no rule label.
        bridge.process([self._telemetry_signal(
            tool_wear_min=10, torque_nm=42.0, air_temp_k=298.0, process_temp_k=309.0)])
        entries = list(bridge._llm_buffer) + list(bridge._llm_priority_buffer)
        assert entries
        assert all(not e.get("_value_rule") for e in entries)

    def test_classify_entry_rule_label_skips_model(self):
        from unittest.mock import MagicMock
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="robotic")
        bridge.classifier = MagicMock()
        bridge.classifier.classify.side_effect = AssertionError(
            "the model must not be called for value-rule entries")
        entry = {"_value_rule": {"label": "real_incident", "rule": "overstrain_l",
                                 "classification": "overstrain_risk",
                                 "confidence": 0.95,
                                 "revision": "robotic-value-rules-v4"},
                 "signal_type": "maintenance_telemetry", "severity": "medium", "severity_input_valid": True,
                 "namespace": "cell_1", "content": {}, "labels": {}}
        result = bridge._classify_entry(entry, client=None)
        assert result.ok
        assert result.label == "real_incident"
        assert result.authoritative_backend == "value_rule"
        assert result.model_revision == "robotic-value-rules-v4"
        assert result.latency_ms == 0.0

    def test_classify_entry_without_rule_uses_classifier(self):
        from unittest.mock import MagicMock
        from cascade_compression.bridge import CascadeBridge
        from cascade_compression.classifier import ClassificationResult
        bridge = CascadeBridge(domain="robotic")
        bridge._qualification_bindings = lambda sig: {}  # orthogonal to this path
        bridge.classifier = MagicMock()
        bridge.classifier.classify.return_value = ClassificationResult(
            label="routine_noise", backend="test", status="ok")
        entry = {"signal_type": "maintenance_telemetry", "severity": "medium",
                 "namespace": "cell_1", "content": {}, "labels": {}}
        result = bridge._classify_entry(entry, client=None)
        assert result.label == "routine_noise"
        bridge.classifier.classify.assert_called_once()

    def test_stats_surface_value_rule_fires(self):
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="robotic")
        bridge.process([self._telemetry_signal()])
        stats = bridge.get_stats()
        assert stats["value_rule_revision"] == "robotic-value-rules-v4"
        assert stats["value_rule_fires"].get("overstrain_l") == 1


class _FakeTelemetry:
    """Minimal signal object shaped for bridge._to_cascade_signal."""

    def __init__(self):
        self.signal_id = None
        self.cluster_id = "cell_1"
        self.namespace = "cell_1"
        self.resource_kind = "Machine"
        self.resource_name = "M1"
        self.signal_type = "maintenance_telemetry"
        self.severity = "medium"
        self.evidence = {}
        self.labels = {"domain": "robotic"}


class TestElseLabel:
    """Healthy-by-spec else labels for covered signal types."""

    SPEC = {
        "revision": "else-v1",
        "covers": {
            "vibration_window": {"label": "routine_noise",
                                 "requires": ["vib_rms_g"]},
            "servo_state": "routine_noise",  # shorthand: all referenced fields
        },
        "rules": [
            {"name": "severe_vibration", "classification": "severe_vibration",
             "label": "real_incident",
             "when": [{"field": "vib_rms_g", "op": "gt", "value": 2.0}]},
            {"name": "servo_overcurrent", "classification": "servo_overcurrent",
             "when": [{"field": "servo_current_ratio", "op": "gt", "value": 1.2}]},
        ],
    }

    def test_covered_unmatched_takes_else_label(self):
        agent = ValueRuleAgent(self.SPEC)
        sig = _signal({"vib_rms_g": 0.4}, signal_type="vibration_window")
        assert agent.evaluate(sig) is None  # no rule matched
        entry = agent.else_label(sig)
        assert entry == {"label": "routine_noise", "rule": "else:vibration_window",
                         "classification": "spec_healthy", "confidence": 0.9,
                         "revision": "else-v1"}

    def test_severity_gate_wins(self):
        agent = ValueRuleAgent(self.SPEC)
        for severity in ("critical", "high"):
            sig = _signal({"vib_rms_g": 0.4}, signal_type="vibration_window",
                          severity=severity)
            assert agent.else_label(sig) is None

    def test_missing_fields_not_else_labeled(self):
        agent = ValueRuleAgent(self.SPEC)
        sig = _signal({"message": "no numeric fields"},
                      signal_type="vibration_window")
        assert agent.else_label(sig) is None

    def test_partial_fields_not_else_labeled(self):
        agent = ValueRuleAgent(self.SPEC)
        sig = _signal({"vib_rms_g": 0.4}, signal_type="servo_state")
        # servo_state shorthand requires all referenced fields
        # (servo_current_ratio is referenced by the overcurrent rule)
        assert agent.else_label(sig) is None

    def test_uncovered_type_not_else_labeled(self):
        agent = ValueRuleAgent(self.SPEC)
        sig = _signal({"message": "an event"}, signal_type="robot_cycle")
        assert agent.else_label(sig) is None

    def test_bad_covers_rejected(self):
        for bad in ({"vibration_window": None}, {"vibration_window": ""},
                    {"vibration_window": {"label": "x", "requires": []}},
                    {"vibration_window": {"requires": ["f"]}},
                    {"vibration_window": {"label": "x", "requires": [1]}}):
            spec = {"revision": "v", "covers": bad,
                    "rules": self.SPEC["rules"]}
            with pytest.raises(ValueError, match="covers"):
                ValueRuleAgent(spec)

    def test_covers_shorthand_and_full_forms(self):
        agent = ValueRuleAgent(self.SPEC)
        assert agent.covers == {"vibration_window": "routine_noise",
                                "servo_state": "routine_noise"}
        sig = _signal({"servo_current_ratio": 0.5, "vib_rms_g": 0.1},
                      signal_type="servo_state")
        entry = agent.else_label(sig)
        assert entry and entry["label"] == "routine_noise"


class TestRoboticPackElse:
    def test_pack_v4_covers_sensor_types(self):
        from cascade_compression.domain_plugins import load_domain_plugin
        rules = load_domain_plugin("robotic").value_rules
        assert rules["revision"] == "robotic-value-rules-v4"
        covers = rules["covers"]
        assert set(covers) == {"vibration_window", "force_contact", "servo_state"}
        # The AI4I lane is deliberately NOT covered (RNF residual).
        assert "maintenance_telemetry" not in covers
        agent = ValueRuleAgent(rules)
        assert agent.covers["vibration_window"] == "routine_noise"

    def test_bridge_else_label_skips_model(self):
        from unittest.mock import MagicMock
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="robotic")
        bridge._qualification_bindings = lambda sig: {}
        bridge.classifier = MagicMock()
        bridge.classifier.classify.side_effect = AssertionError(
            "the model must not be called for else-labeled signals")
        entry = {"_value_rule": {"label": "routine_noise",
                                 "rule": "else:vibration_window",
                                 "classification": "spec_healthy",
                                 "confidence": 0.9,
                                 "revision": "robotic-value-rules-v4"},
                 "signal_type": "vibration_window", "severity": "medium", "severity_input_valid": True,
                 "namespace": "cell_1", "content": {}, "labels": {}}
        result = bridge._classify_entry(entry, client=None)
        assert result.ok
        assert result.label == "routine_noise"
        assert result.authoritative_backend == "value_rule"

    def test_bridge_process_else_labels_covered_healthy(self):
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge(domain="robotic")
        healthy = _FakeTelemetry()
        healthy.evidence = {
            "message": "machine M type L: air 298.1K proc 308.6K speed 1551rpm torque 42.0Nm tool_wear 10min",
            "machine_type": "L", "air_temp_k": 298.1, "process_temp_k": 308.6,
            "rotational_speed_rpm": 1551, "torque_nm": 42.0, "tool_wear_min": 10,
        }
        healthy.signal_type = "vibration_window"
        healthy.evidence.update({"vib_rms_g": 0.4, "vib_band1_energy": 0.0001,
                                 "vib_band3_energy": 0.0001, "vib_band4_energy": 0.0})
        bridge.process([healthy])
        entries = list(bridge._llm_buffer) + list(bridge._llm_priority_buffer)
        matched = [e for e in entries if e.get("_value_rule")]
        assert matched, "healthy covered signal must be else-labeled"
        assert matched[0]["_value_rule"]["rule"] == "else:vibration_window"
        assert matched[0]["_value_rule"]["label"] == "routine_noise"
        stats = bridge.get_stats()
        assert stats.get("value_rule_else_classified") == 1
