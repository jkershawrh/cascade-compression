import json

from fastapi.testclient import TestClient
import pytest
import jsonschema
from pathlib import Path

from cascade_compression import service
from cascade_compression.cascade.memory import MemoryArchive, Memory
from cascade_compression.cascade.protocol import Signal
from cascade_compression.memory_workspace import (
    LessonCatalog, episodes, evidence_revision, lesson_catalog,
)


@pytest.fixture
def archive(monkeypatch):
    archive = MemoryArchive(instance_id="synthetic-operations")
    for i in range(3):
        memory = archive.store(Signal(
            signal_type="provisioning_failure", cluster="example-cluster",
            namespace="training" if i < 2 else "other-scope",
            content={"message": f"Synthetic observation {i}"},
        ), classification="needs_attention")
        memory.formed_at = f"2026-10-12T14:0{i}:00Z"
    monkeypatch.setattr(service, "_memory_archive", archive)
    monkeypatch.delenv("CASCADE_MEMORY_LESSONS_FILE", raising=False)
    return archive


def records(archive):
    return [m.to_dict() for m in archive.query(limit=archive.size)]


def catalog(record, **overrides):
    lesson = {
        "lesson_id": "synthetic-lesson",
        "title": "Synthetic reviewed annotation",
        "summary": "Synthetic observations merit a troubleshooting review.",
        "status": "reviewed",
        "reviewed_by": "Example reviewer",
        "reviewed_at": "2026-01-01T00:00:00Z",
        "citations": [{"memory_id": record["memory_id"],
                       "evidence_revision": evidence_revision(record)}],
        **overrides,
    }
    return {"schema_version": 1, "lessons": [lesson]}


def test_server_filters_before_pagination_and_preserves_scope(archive):
    client = TestClient(service.app)
    response = client.get("/memories/episodes", params={
        "namespace": "training", "limit": 1,
        "since": "2026-10-12T00:00:00Z", "until": "2026-10-13T00:00:00Z",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["record_count"] == 2
    assert data["episode_count"] == 1
    assert data["episodes"][0]["memory_count"] == 2
    assert data["episodes"][0]["outcome"] == "unknown"
    assert data["lessons"]["status"] == "not_configured"
    assert all(r["namespace"] == "training" for r in data["episodes"][0]["references"])
    assert client.get("/memories/episodes?limit=1&offset=1").json()["episodes"]
    assert client.get("/memories/episodes?offset=99").json()["episodes"] == []


@pytest.mark.parametrize("params", [
    {"since": "2026-10-12T00:00:00"},
    {"since": "2026-10-13T00:00:00Z", "until": "2026-10-12T00:00:00Z"},
    {"limit": 51}, {"offset": -1},
])
def test_invalid_filters_fail_explicitly(archive, params):
    assert TestClient(service.app).get("/memories/episodes", params=params).status_code == 422


def test_evidence_reads_never_reinforce_or_drain_events(archive):
    memory = archive.query(limit=1)[0]
    before = (memory.strength, memory.recall_count, len(archive._events))
    data = TestClient(service.app).get(f"/memories/evidence/{memory.memory_id}").json()
    assert data["history_completeness"] == "partial"
    assert data["evidence_revision"] == evidence_revision(memory.to_dict())
    assert "per_signal_classifier_decisions" in data["unavailable"]
    assert before == (memory.strength, memory.recall_count, len(archive._events))


def test_evidence_404_for_invalid_or_evicted_identity(archive):
    client = TestClient(service.app)
    assert client.get("/memories/evidence/invalid").status_code == 404
    assert client.get("/memories/evidence/00000000-0000-0000-0000-000000000000").status_code == 404


def test_derived_views_respect_serializer_privacy_boundary(archive, monkeypatch):
    original = Memory.to_dict
    def aggregate(memory):
        record = original(memory)
        return {key: value for key, value in record.items()
                if key not in {"content", "source", "namespace", "cluster", "analysis", "labels", "evidence"}}
    monkeypatch.setattr(Memory, "to_dict", aggregate)
    client = TestClient(service.app)
    data = client.get("/memories/episodes").json()
    assert data["episode_count"] == 3
    assert data["missing_scope_count"] == 3
    assert "example-cluster" not in json.dumps(data)
    memory = archive.query(limit=1)[0]
    evidence = client.get(f"/memories/evidence/{memory.memory_id}").json()
    assert "content" not in evidence["memory"]
    assert "example-cluster" not in json.dumps(evidence)


def test_catalog_review_invalidates_changed_or_missing_evidence(archive, tmp_path):
    all_records = records(archive)
    record = all_records[0]
    path = tmp_path / "lessons.json"
    path.write_text(json.dumps(catalog(record)))
    selected = {r["memory_id"] for r in all_records}
    assert lesson_catalog(str(path), all_records, selected)["lessons"][0]["effective_status"] == "reviewed"
    modified = [{**r, "classification": "real_incident"} for r in all_records]
    lesson = lesson_catalog(str(path), modified, selected)["lessons"][0]
    assert lesson["effective_status"] == "needs_review"
    assert lesson["citations"][0]["availability"] == "changed"
    # Another citation keeps the lesson visible when one reference was evicted.
    extra = all_records[1]
    doc = catalog(record)
    doc["lessons"][0]["citations"].append({
        "memory_id": extra["memory_id"], "evidence_revision": evidence_revision(extra),
    })
    path.write_text(json.dumps(doc))
    remaining = [r for r in all_records if r["memory_id"] != record["memory_id"]]
    lesson = lesson_catalog(str(path), remaining, {r["memory_id"] for r in remaining})["lessons"][0]
    assert lesson["effective_status"] == "needs_review"
    assert lesson["citations"][0]["availability"] == "missing"


def test_filtering_cannot_present_partial_lesson_as_reviewed(archive, tmp_path):
    all_records = records(archive)
    doc = catalog(all_records[0])
    doc["lessons"][0]["citations"].append({
        "memory_id": all_records[1]["memory_id"],
        "evidence_revision": evidence_revision(all_records[1]),
    })
    path = tmp_path / "lessons.json"
    path.write_text(json.dumps(doc))
    result = lesson_catalog(str(path), all_records, {all_records[0]["memory_id"]})
    assert result["lessons"][0]["scope_complete"] is False
    assert result["lessons"][0]["effective_status"] == "needs_review"


def test_retention_updates_do_not_invalidate_review(archive):
    record = records(archive)[0]
    assert evidence_revision(record) == evidence_revision({
        **record, "strength": .1, "recall_count": 999, "last_modified_at": "2026-10-13T12:00:00Z",
    })
    assert evidence_revision(record) != evidence_revision({**record, "content": {"message": "different"}})


def test_catalog_errors_fail_closed_without_exposing_path(archive, tmp_path):
    path = tmp_path / "lessons.json"
    path.write_text(json.dumps(catalog(records(archive)[0], reviewed_by="")))
    result = lesson_catalog(str(path), records(archive), set())
    assert result["status"] == "invalid"
    assert result["lessons"] == []
    assert str(path) not in json.dumps(result)
    path.write_bytes(b" " * 1_000_001)
    assert lesson_catalog(str(path), [], set())["status"] == "invalid"


def test_entirely_evicted_lesson_remains_visible_as_needing_review(archive, tmp_path):
    path = tmp_path / "lessons.json"
    path.write_text(json.dumps(catalog(records(archive)[0])))
    result = lesson_catalog(str(path), [], set())
    assert result["lessons"][0]["effective_status"] == "needs_review"
    assert result["lessons"][0]["scope_complete"] is False
    assert result["lessons"][0]["citations"][0]["availability"] == "missing"


def test_archive_unavailable_is_not_reported_as_zero_observations(monkeypatch):
    monkeypatch.setattr(service, "_memory_archive", None)
    assert TestClient(service.app).get("/memories/episodes").status_code == 503


def test_review_contract_rejects_duplicate_ids_and_future_reviews(archive):
    doc = catalog(records(archive)[0])
    doc["lessons"].append(doc["lessons"][0])
    with pytest.raises(ValueError):
        LessonCatalog.model_validate(doc)
    with pytest.raises(ValueError):
        LessonCatalog.model_validate(catalog(records(archive)[0], reviewed_at="2999-01-01T00:00:00Z"))


def test_episode_ids_stable_when_retention_strength_changes(archive):
    before = episodes(records(archive))
    for memory in archive.query(limit=archive.size):
        memory.strength = .99
    after = episodes(records(archive))
    assert [e["episode_id"] for e in before["episodes"]] == [e["episode_id"] for e in after["episodes"]]


def test_episode_references_are_bounded_and_offsets_are_normalized_to_utc():
    base = {"memory_id": "a", "source_instance": "synthetic",
            "cluster": "example", "namespace": "example", "signal_type": "test"}
    data = episodes([
        {**base, "memory_id": str(i), "formed_at": "2026-10-12T23:30:00-05:00"}
        for i in range(60)
    ] + [{**base, "memory_id": "utc", "formed_at": "2026-10-13T04:45:00Z"}])
    assert data["episode_count"] == 1
    assert data["episodes"][0]["memory_count"] == 61
    assert len(data["episodes"][0]["references"]) == 50
    assert data["episodes"][0]["references_truncated"] is True


def test_catalog_schema_and_api_review_status(archive, tmp_path, monkeypatch):
    record = records(archive)[0]
    document = catalog(record)
    schema = json.loads((Path(__file__).parent.parent / "contracts/schemas/memory-lesson-catalog.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(document, schema, format_checker=jsonschema.FormatChecker())
    invalid = catalog(record, reviewed_by="")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, schema)
    path = tmp_path / "lessons.json"
    path.write_text(json.dumps(document))
    monkeypatch.setenv("CASCADE_MEMORY_LESSONS_FILE", str(path))
    client = TestClient(service.app)
    lesson = client.get("/memories/episodes").json()["lessons"]["lessons"][0]
    assert lesson["effective_status"] == "reviewed"
    assert lesson["review_basis"] == "operator_declared_catalog"
    memory = archive.query(limit=archive.size)[0]
    memory.classification = "real_incident"
    assert client.get("/memories/episodes").json()["lessons"]["lessons"][0]["effective_status"] == "needs_review"
