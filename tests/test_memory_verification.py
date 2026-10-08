from datetime import datetime, timedelta, timezone

from cascade_compression.cascade.memory import MemoryArchive
from tests.helpers import make_signal


def approve(archive, memory, **overrides):
    values = {
        "outcome": "benign",
        "source": "human-review",
        "policy_revision": "memory-policy-v1",
        "evidence_ref": "review:case-1",
    }
    values.update(overrides)
    return archive.verify_memory(memory.memory_id, **values)


def test_new_memory_is_unverified_and_cannot_authorize_suppression():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"), classification="routine_noise")
    assert memory.verification.status == "unverified"
    assert not memory.suppression_evidence_admissible(exact_match=True)


def test_approved_exact_benign_memory_is_admissible():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    approve(archive, memory)
    assert memory.suppression_evidence_admissible(exact_match=True)


def test_admission_fails_closed_for_non_exact_or_actionable_evidence():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    approve(archive, memory, outcome="actionable")
    assert not memory.suppression_evidence_admissible(exact_match=True)
    approve(archive, memory, outcome="benign")
    assert not memory.suppression_evidence_admissible(exact_match=False)


def test_admission_rejects_weak_conflicting_or_expired_memory():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    memory.strength = 0.79
    approve(archive, memory)
    assert not memory.suppression_evidence_admissible(exact_match=True)

    memory.strength = 1.0
    approve(archive, memory, contradictory=True)
    assert not memory.suppression_evidence_admissible(exact_match=True)

    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    approve(archive, memory, contradictory=False, expires_at=expired)
    assert not memory.suppression_evidence_admissible(exact_match=True)


def test_admission_fails_closed_for_malformed_expiration():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    approve(archive, memory, expires_at="not-a-timestamp")
    assert not memory.suppression_evidence_admissible(exact_match=True)


def test_verification_roundtrips_through_archive_state():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    approve(archive, memory)
    restored = MemoryArchive.from_dict(archive.to_dict())
    restored_memory = restored.get(memory.memory_id)
    assert restored_memory.verification.status == "approved"
    assert restored_memory.verification.outcome == "benign"
    assert restored_memory.suppression_evidence_admissible(exact_match=True)


def test_federated_import_does_not_trust_remote_approval():
    source = MemoryArchive(instance_id="source")
    memory = source.store(make_signal(severity="critical"))
    approve(source, memory)

    target = MemoryArchive(instance_id="target")
    assert target.import_memories(source.export_memories()) == 1
    imported = target.query()[0]
    assert imported.verification.status == "unverified"
    assert not imported.suppression_evidence_admissible(exact_match=True)


def test_verification_emits_an_audit_event():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    archive.drain_events()
    approve(archive, memory)
    event = archive.drain_events()[0]
    assert event.event_type == "verified"
    assert event.details["outcome"] == "benign"
    assert event.details["policy_revision"] == "memory-policy-v1"
