"""Validate observed outcomes before granting memory suppression evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from .cascade.memory import Memory, MemoryArchive


SCHEMA_VERSION = "cascade.outcome-evidence.v1alpha1"
POLICY_REVISION = "cascade-outcome-admission-v1"
FIELDS = frozenset({
    "schema_version", "case_id", "signal_sha256", "intervention_required",
    "service_impact", "resolution", "observation_window", "source",
    "source_record_ref", "narrative", "reviewer_ref", "recorded_at",
    "independent_of_evaluated_arms", "evidence_ref",
})


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} timestamp must include a timezone")
    return parsed


@dataclass(frozen=True)
class OutcomeEvidenceVerdict:
    outcome: str
    safe_for_suppression: bool
    evidence_ref: str
    source: str
    reviewer_ref: str
    policy_revision: str = POLICY_REVISION


def validate_outcome_evidence(record: Dict[str, Any]) -> OutcomeEvidenceVerdict:
    """Validate a private evidence receipt and derive a fail-closed outcome."""
    if not isinstance(record, dict):
        raise ValueError("outcome evidence must be an object")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("outcome evidence has an unsupported schema version")
    missing = FIELDS - set(record)
    unexpected = set(record) - FIELDS
    if missing:
        raise ValueError(
            "outcome evidence is missing required fields: "
            + ", ".join(sorted(missing))
        )
    if unexpected:
        raise ValueError(
            "outcome evidence contains undeclared fields: "
            + ", ".join(sorted(unexpected))
        )
    for field in ("case_id", "source_record_ref", "narrative", "reviewer_ref"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise ValueError(f"outcome evidence requires a non-empty {field}")
    if type(record.get("intervention_required")) is not bool:
        raise ValueError("outcome evidence requires boolean intervention_required")
    if record.get("service_impact") not in {"none", "degraded", "outage", "unknown"}:
        raise ValueError("outcome evidence has invalid service_impact")
    if record.get("resolution") not in {
        "self_resolved", "automated_recovery", "operator_resolved", "ongoing", "unknown",
    }:
        raise ValueError("outcome evidence has invalid resolution")
    if record.get("source") not in {
        "independent_human_review", "authoritative_record", "gcl_verdict",
    }:
        raise ValueError("outcome evidence has invalid source")
    if record.get("independent_of_evaluated_arms") is not True:
        raise ValueError("outcome evidence is not independent of evaluated arms")
    signal_digest = str(record.get("signal_sha256") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", signal_digest):
        raise ValueError("outcome evidence is missing a signal digest")

    window = record.get("observation_window")
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise ValueError("outcome evidence requires an exact observation window")
    started = _timestamp(window["start"], "outcome observation start")
    ended = _timestamp(window["end"], "outcome observation end")
    recorded = _timestamp(record["recorded_at"], "outcome recorded_at")
    if ended <= started:
        raise ValueError("outcome observation window must be positive")
    if recorded < ended:
        raise ValueError("outcome evidence cannot be recorded before observation ends")

    intervention = record["intervention_required"]
    resolution = record["resolution"]
    if intervention and resolution == "self_resolved":
        raise ValueError("intervention-required evidence cannot be self-resolved")
    if not intervention and resolution == "operator_resolved":
        raise ValueError("operator-resolved evidence requires intervention")

    evidence_ref = str(record.get("evidence_ref") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence_ref):
        raise ValueError("outcome evidence is missing an evidence digest")
    evidence = {key: value for key, value in record.items() if key != "evidence_ref"}
    if _digest(evidence) != evidence_ref:
        raise ValueError("outcome evidence digest does not match its contents")

    benign = (
        not intervention
        and record["service_impact"] == "none"
        and resolution == "self_resolved"
    )
    return OutcomeEvidenceVerdict(
        outcome="benign" if benign else "actionable",
        safe_for_suppression=benign,
        evidence_ref=evidence_ref,
        source=record["source"],
        reviewer_ref=record["reviewer_ref"],
    )


def verify_memory_from_outcome(
    archive: MemoryArchive, memory_id: UUID, record: Dict[str, Any], *,
    signal: Dict[str, Any], expires_at: Optional[str] = None,
) -> Memory:
    """Apply validated local outcome evidence to one exact memory record."""
    verdict = validate_outcome_evidence(record)
    if _digest(signal) != record["signal_sha256"]:
        raise ValueError("outcome evidence does not match the supplied signal")
    memory = archive.get(memory_id)
    if memory is None:
        raise KeyError(str(memory_id))
    expected = {
        "signal_type": memory.signal.signal_type,
        "severity": memory.signal.severity,
        "source": memory.signal.source,
        "namespace": memory.signal.namespace,
        "cluster": memory.signal.cluster,
        "content": memory.signal.content,
        "labels": memory.signal.labels,
    }
    mismatches = [
        key for key, value in signal.items()
        if key in expected and expected[key] != value
    ]
    if mismatches:
        raise ValueError(
            "outcome evidence signal differs from memory fields: "
            + ", ".join(sorted(mismatches))
        )
    return archive.verify_memory(
        memory_id,
        outcome=verdict.outcome,
        source=f"{verdict.source}:{verdict.reviewer_ref}",
        policy_revision=verdict.policy_revision,
        evidence_ref=verdict.evidence_ref,
        expires_at=expires_at,
    )
