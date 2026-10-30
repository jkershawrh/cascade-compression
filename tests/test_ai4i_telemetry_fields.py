"""Tests for the AI4I telemetry field adapter (numeric-sense front end)."""

import pytest

from cascade_compression.harness.ai4i_telemetry_fields import (
    TelemetryFieldError,
    add_telemetry_fields,
    convert_corpus,
    parse_telemetry_message,
    telemetry_row_to_signal,
)


ROW = {
    "signal_type": "maintenance_telemetry",
    "severity": "medium",
    "source": "M14860",
    "namespace": "cell_1",
    "cluster": "ai4i",
    "labels": {"domain": "robotic", "label": "normal"},
    "content": {
        "message": "machine M14860 type M: air 298.1K proc 308.6K "
                   "speed 1551rpm torque 42.8Nm tool_wear 0min",
        "kind": "SystemLog",
        "uid": "M14860",
        "event_uid": "ai4i_telemetry",
        "reason": "TELEMETRY",
        "count": 1,
        "message_sha256": "86c09e0b4a2c4fe0775a4538bbae19bf5964d78d78dd0e98fcca10042110cb59",
        "failure_modes": [],
    },
}


class TestParse:
    def test_parses_all_fields(self):
        f = parse_telemetry_message(ROW["content"]["message"])
        assert f == {
            "machine_id": "M14860", "machine_type": "M",
            "air_temp_k": 298.1, "process_temp_k": 308.6,
            "rotational_speed_rpm": 1551, "torque_nm": 42.8,
            "tool_wear_min": 0,
        }

    def test_parses_failure_mode_suffix(self):
        f = parse_telemetry_message(
            "machine L47230 type L: air 298.9K proc 309.1K speed 2861rpm "
            "torque 4.6Nm tool_wear 143min failure_modes=PWF")
        assert f["machine_id"] == "L47230"
        assert f["tool_wear_min"] == 143

    @pytest.mark.parametrize("bad", [
        "", "not a telemetry message", "machine M1 type M: garbage",
        "machine M1: air 298K", None,
    ])
    def test_rejects_malformed(self, bad):
        with pytest.raises(TelemetryFieldError):
            parse_telemetry_message(bad)


class TestAddFields:
    def test_adds_fields_preserving_message(self):
        out = add_telemetry_fields(ROW)
        assert out["content"]["message"] == ROW["content"]["message"]
        assert out["content"]["machine_type"] == "M"
        assert out["content"]["torque_nm"] == 42.8
        assert out["labels"] == ROW["labels"]
        # the original row is not mutated
        assert "machine_type" not in ROW["content"]

    def test_uid_mismatch_raises(self):
        row = {**ROW, "content": {**ROW["content"], "uid": "DIFFERENT"}}
        with pytest.raises(TelemetryFieldError, match="does not match uid"):
            add_telemetry_fields(row)


class TestCorpusAndSignal:
    def test_convert_corpus(self):
        rows = [ROW, {**ROW, "content": {**ROW["content"],
                    "message": "machine L1 type L: air 298.0K proc 308.0K "
                               "speed 1400rpm torque 46.0Nm tool_wear 3min",
                    "uid": "L1"}}]
        out = convert_corpus(rows)
        assert out[1]["content"]["tool_wear_min"] == 3
        assert out[1]["content"]["rotational_speed_rpm"] == 1400

    def test_row_to_signal(self):
        sig = telemetry_row_to_signal(add_telemetry_fields(ROW))
        assert sig.signal_type == "maintenance_telemetry"
        assert sig.content["tool_wear_min"] == 0
        assert sig.labels["domain"] == "robotic"
