"""Read-only, domain-agnostic presentation of serialized memory evidence.

Never bypass Memory.to_dict: production entrypoints control which fields may
leave the archive. Episode grouping is a rule, not incident adjudication.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .classifier import redact

GROUPING_RULE = "same-source-cluster-namespace-type-UTC-day-v1"
MAX_CATALOG_BYTES = 1_000_000


def timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def evidence_revision(record: dict) -> str:
    """Evidence identity excludes reinforcement/decay counters."""
    fields = ("memory_id", "content_hash", "signal_type", "formed_at",
              "classification", "severity", "source_instance", "source",
              "cluster", "namespace", "content", "labels", "analysis")
    fields = fields + ("evidence",)
    projection = {key: record[key] for key in fields if key in record}
    return hashlib.sha256(json.dumps(
        projection, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()


def memory_evidence(record: dict) -> dict:
    milestones = []
    envelope = record.get("evidence") or {}
    if timestamp(envelope.get("original_event_time")):
        milestones.append({"kind": "original_event_time", "label": "Original event",
                           "timestamp": envelope["original_event_time"]})
    if timestamp(envelope.get("classified_at")):
        milestones.append({"kind": "classified_at", "label": "Latest classification",
                           "timestamp": envelope["classified_at"]})
    for field, label in (
        ("formed_at", "Memory formed"),
        ("last_recalled_at", "Latest recall"),
        ("last_consolidated_at", "Latest consolidation"),
        ("last_modified_at", "Latest recorded modification"),
    ):
        if timestamp(record.get(field)):
            milestones.append({"kind": field, "label": label,
                               "timestamp": record[field]})
    milestones.sort(key=lambda item: timestamp(item["timestamp"]))
    return {
        "memory": record,
        "evidence_revision": evidence_revision(record),
        "milestones": milestones,
        "history_completeness": "partial",
        "unavailable": [
            *([] if timestamp(envelope.get("original_event_time")) else ["original_event_time"]),
            "federation_arrival_time",
            "complete_lifecycle_history", "per_signal_classifier_decisions",
        ],
    }


def select_records(records: list[dict], *, source: str = "", cluster: str = "",
                   namespace: str = "", since: Optional[datetime] = None,
                   until: Optional[datetime] = None) -> list[dict]:
    selected = []
    for record in records:
        if any(value and record.get(key) != value for key, value in (
            ("source_instance", source), ("cluster", cluster),
            ("namespace", namespace),
        )):
            continue
        formed = timestamp(record.get("formed_at"))
        if since and (formed is None or formed < since):
            continue
        if until and (formed is None or formed >= until):
            continue
        selected.append(record)
    return selected


def episodes(records: list[dict], *, limit: int = 25, offset: int = 0) -> dict:
    groups: dict[str, list[dict]] = {}
    for record in records:
        formed = timestamp(record.get("formed_at"))
        scope = [record.get(key) for key in
                 ("source_instance", "cluster", "namespace", "signal_type")]
        key = (json.dumps(scope + [formed.date().isoformat()])
               if all(scope) and formed else str(record["memory_id"]))
        groups.setdefault(key, []).append(record)
    values = []
    for key, members in groups.items():
        members.sort(key=lambda m: (
            timestamp(m.get("formed_at")) or datetime.min.replace(tzinfo=timezone.utc),
            m["memory_id"],
        ), reverse=True)
        dates = [m["formed_at"] for m in members if timestamp(m.get("formed_at"))]
        sampled = members[:50]
        references = [{
            **{field: m.get(field) for field in (
                "memory_id", "signal_type", "formed_at", "source_instance",
                "cluster", "namespace", "severity", "classification",
            )},
            "evidence_revision": evidence_revision(m),
        } for m in sampled]
        values.append({
            "episode_id": hashlib.sha256(key.encode()).hexdigest()[:24],
            "title": str(members[0].get("signal_type") or "Unclassified observation").replace("_", " "),
            "kind": "rule_group" if len(members) > 1 else "observation",
            "grouping_rule": GROUPING_RULE,
            "memory_count": len(members),
            "first_formed_at": dates[-1] if dates else None,
            "last_formed_at": dates[0] if dates else None,
            "outcome": "unknown",
            "classifications": dict(Counter(m.get("classification") or "unknown" for m in members)),
            "references": references,
            "references_truncated": len(members) > len(sampled),
            "summary": (
                f"{len(members)} retained observation(s) share the grouping rule "
                "where scope is complete. These records do not establish a "
                "common cause or a recovery outcome."
            ),
        })
    values.sort(key=lambda item: (
        timestamp(item["last_formed_at"]) or datetime.min.replace(tzinfo=timezone.utc),
        item["episode_id"],
    ), reverse=True)
    return {
        "scope": "currently_retained_memories", "grouping_rule": GROUPING_RULE,
        "record_count": len(records), "episode_count": len(values),
        "episodes": values[offset:offset + limit], "offset": offset,
        "limit": limit, "has_more": offset + limit < len(values),
        "missing_scope_count": sum(not all(m.get(k) for k in
            ("source_instance", "cluster", "namespace")) for m in records),
    }


class LessonCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: str = Field(min_length=1, max_length=128)
    evidence_revision: str = Field(pattern="^[a-f0-9]{64}$")


class Lesson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lesson_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    status: Literal["candidate", "reviewed", "rejected"] = "candidate"
    reviewed_by: str = Field(default="", max_length=200)
    reviewed_at: Optional[datetime] = None
    citations: list[LessonCitation] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def review_has_provenance(self):
        if len({c.memory_id for c in self.citations}) != len(self.citations):
            raise ValueError("duplicate citations")
        if self.status != "candidate":
            if not self.reviewed_by.strip() or self.reviewed_at is None:
                raise ValueError("review requires reviewer and review time")
        if self.reviewed_at is not None:
            if self.reviewed_at.tzinfo is None or self.reviewed_at > datetime.now(timezone.utc):
                raise ValueError("review time must be timezone-aware and not in the future")
        return self


class LessonCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    lessons: list[Lesson] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({lesson.lesson_id for lesson in self.lessons}) != len(self.lessons):
            raise ValueError("duplicate lesson IDs")
        return self


def lesson_catalog(path: str, records: list[dict],
                   selected_ids: set[str]) -> dict:
    """Review status is an operator declaration, rechecked against evidence."""
    if not path:
        return {"status": "not_configured", "lessons": []}
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            raise ValueError("catalog exceeds bound")
        catalog = LessonCatalog.model_validate_json(raw)
    except (OSError, ValueError):
        return {"status": "invalid", "lessons": [],
                "error": "Lesson catalog unavailable or invalid; no review claims loaded."}
    index = {m["memory_id"]: m for m in records}
    result = []
    for lesson in catalog.lessons:
        has_selected_evidence = any(c.memory_id in selected_ids for c in lesson.citations)
        entirely_missing = all(c.memory_id not in index for c in lesson.citations)
        if not has_selected_evidence and not entirely_missing:
            continue
        checks = []
        for citation in lesson.citations:
            record = index.get(citation.memory_id)
            checks.append({
                **citation.model_dump(),
                "availability": "missing" if record is None else (
                    "current" if evidence_revision(record) == citation.evidence_revision
                    else "changed"),
                "in_selected_scope": citation.memory_id in selected_ids,
            })
        complete = all(c["availability"] == "current" for c in checks)
        in_scope = all(c["in_selected_scope"] for c in checks)
        result.append(redact({
            **lesson.model_dump(mode="json"),
            "citations": checks,
            "evidence_state": "current" if complete else "needs_review",
            "scope_complete": in_scope,
            "effective_status": (
                lesson.status if complete and in_scope else "needs_review"
            ),
            "review_basis": "operator_declared_catalog",
        }))
    return {"status": "ok", "lessons": result}
