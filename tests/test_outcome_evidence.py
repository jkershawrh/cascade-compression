import copy
import json
from pathlib import Path

import jsonschema
import pytest

from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.outcome_evidence import (
    _digest,
    validate_outcome_evidence,
    verify_memory_from_outcome,
)
from tests.helpers import make_signal


SCHEMA = Path(__file__).parent.parent / "contracts/schemas/outcome-evidence.json"


def frozen_signal(message="public test signal"):
    return {
        "signal_type": "pod_crashloop",
        "severity": "critical",
        "source": "node-01",
        "namespace": "production",
        "cluster": "",
        "content": {"message": message},
        "labels": {},
    }


def evidence(signal=None, **overrides):
    signal = signal or frozen_signal()
    record = {
        "schema_version": "cascade.outcome-evidence.v1alpha1",
        "case_id": "public-case-1",
        "signal_sha256": _digest(signal),
        "intervention_required": False,
        "service_impact": "none",
        "resolution": "self_resolved",
        "observation_window": {
            "start": "2026-10-07T10:00:00Z",
            "end": "2026-10-07T11:00:00Z",
        },
        "source": "authoritative_record",
        "source_record_ref": "incident:public-case-1",
        "narrative": "The condition cleared without intervention or impact.",
        "reviewer_ref": "reviewer-public-a",
        "recorded_at": "2026-10-07T12:00:00Z",
        "independent_of_evaluated_arms": True,
    }
    record.update(overrides)
    record["evidence_ref"] = _digest(record)
    return record


def test_contract_accepts_valid_benign_evidence():
    schema = json.loads(SCHEMA.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(evidence(), schema)


def test_strict_benign_outcome_is_safe_for_suppression():
    verdict = validate_outcome_evidence(evidence())
    assert verdict.outcome == "benign"
    assert verdict.safe_for_suppression


@pytest.mark.parametrize("changes", [
    {"intervention_required": True, "resolution": "operator_resolved"},
    {"service_impact": "degraded"},
    {"resolution": "automated_recovery"},
    {"resolution": "ongoing"},
    {"service_impact": "unknown"},
])
def test_uncertain_or_harmful_outcomes_are_actionable(changes):
    verdict = validate_outcome_evidence(evidence(**changes))
    assert verdict.outcome == "actionable"
    assert not verdict.safe_for_suppression


def test_inconsistent_resolution_is_rejected():
    with pytest.raises(ValueError, match="cannot be self-resolved"):
        validate_outcome_evidence(evidence(intervention_required=True))


def test_observation_window_and_recording_order_are_enforced():
    invalid = evidence(observation_window={
        "start": "2026-10-07T11:00:00Z",
        "end": "2026-10-07T10:00:00Z",
    })
    with pytest.raises(ValueError, match="window must be positive"):
        validate_outcome_evidence(invalid)

    premature = evidence(recorded_at="2026-10-07T10:30:00Z")
    with pytest.raises(ValueError, match="before observation ends"):
        validate_outcome_evidence(premature)


def test_tampered_evidence_digest_is_rejected():
    record = evidence()
    record["narrative"] = "Changed after signing."
    with pytest.raises(ValueError, match="digest does not match"):
        validate_outcome_evidence(record)


def test_validated_outcome_can_verify_exact_memory():
    archive = MemoryArchive()
    signal_data = frozen_signal()
    memory = archive.store(make_signal(
        severity="critical", content=signal_data["content"],
    ))
    verified = verify_memory_from_outcome(
        archive, memory.memory_id, evidence(signal_data), signal=signal_data,
    )
    assert verified.verification.status == "approved"
    assert verified.verification.outcome == "benign"
    assert verified.verification.policy_revision == "cascade-outcome-admission-v1"
    assert verified.suppression_evidence_admissible(exact_match=True)


def test_outcome_evidence_cannot_be_applied_to_a_different_memory():
    archive = MemoryArchive()
    signal_data = frozen_signal()
    memory = archive.store(make_signal(
        severity="critical", content={"message": "different signal"},
    ))
    with pytest.raises(ValueError, match="differs from memory fields"):
        verify_memory_from_outcome(
            archive, memory.memory_id, evidence(signal_data), signal=signal_data,
        )


def test_outcome_evidence_requires_the_supplied_signal_digest():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    with pytest.raises(ValueError, match="does not match the supplied signal"):
        verify_memory_from_outcome(
            archive, memory.memory_id, evidence(frozen_signal()),
            signal=frozen_signal("tampered signal"),
        )


def test_contract_rejects_extra_private_fields():
    schema = json.loads(SCHEMA.read_text())
    record = copy.deepcopy(evidence())
    record["internal_url"] = "https://example.invalid/private"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, schema)
