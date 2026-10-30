from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from cascade_compression.memory_digest import FederatedMemoryDigest


def memory(memory_id="m1", formed_at="2026-09-10T12:34:56+00:00"):
    return SimpleNamespace(to_dict=lambda: {
        "memory_id": memory_id, "formed_at": formed_at,
        "source_instance": "cascade-k8s", "signal_type": "pod_failure",
        "severity": "high", "classification": "needs_attention", "strength": 0.8,
    })


def test_hourly_digest_preserves_formed_timestamp_and_clears_after_send(tmp_path):
    sent = []
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="owner@example.test",
        smtp_host="", send=lambda records: sent.extend(records),
    )
    digest.enqueue(memory())

    assert digest.pending_count() == 1
    assert digest.flush() == 1
    assert sent[0]["formed_at"] == "2026-09-10T12:34:56+00:00"
    assert sent[0]["aggregated_at"]
    assert digest.pending_count() == 0


def test_failed_delivery_keeps_pending_memories(tmp_path):
    def fail(_records):
        raise RuntimeError("smtp unavailable")

    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="owner@example.test",
        smtp_host="", send=fail,
    )
    digest.enqueue(memory())

    try:
        digest.flush()
    except RuntimeError:
        pass
    assert digest.pending_count() == 1


def test_summary_and_acknowledgement_are_stable(tmp_path):
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
    )
    digest.enqueue(memory("m1"))
    digest.enqueue(memory("m2", "2026-09-10T12:50:00+00:00"))

    summary = digest.summary()
    assert summary["count"] == 2
    assert summary["formed_from"] == "2026-09-10T12:34:56+00:00"
    assert summary["formed_to"] == "2026-09-10T12:50:00+00:00"
    assert summary["sources"] == {"cascade-k8s": 2}
    assert digest.acknowledge("wrong") is False
    assert digest.acknowledge(summary["digest_id"]) is True
    assert digest.pending_count() == 0


def test_hourly_summary_excludes_old_aggregations(tmp_path):
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
    )
    digest.enqueue(memory("fresh"))
    records = digest._read()
    records.append({
        "memory_id": "old", "formed_at": "2020-01-01T00:00:00+00:00",
        "aggregated_at": "2020-01-01T00:00:00+00:00", "source_instance": "knowledge",
        "signal_type": "runbook_decay", "severity": "medium",
    })
    digest._write(records)
    assert digest.summary(hours=1)["count"] == 1


def test_summary_filters_explicit_aggregation_window(tmp_path):
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
        retention_hours=24 * 365,
    )
    digest._write([
        {"memory_id": "before", "aggregated_at": "2026-09-09T23:59:59+00:00"},
        {"memory_id": "inside", "aggregated_at": "2026-09-10T12:00:00+00:00",
         "classification": ""},
        {"memory_id": "after", "aggregated_at": "2026-09-11T00:00:00+00:00"},
    ])
    result = digest.summary(
        start=datetime(2026, 9, 10, tzinfo=timezone.utc),
        end=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    assert result["count"] == 1
    assert result["classifications"] == {"unknown": 1}
    assert result["notable"][0]["memory_id"] == "inside"
    assert result["notable"][0]["classification"] == "unknown"


def test_formed_summary_uses_original_memory_time_not_aggregation_time(tmp_path):
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
    )
    records = [
        {"memory_id": "old", "formed_at": "2026-09-09T23:59:59+00:00",
         "aggregated_at": "2026-09-10T12:00:00+00:00"},
        {"memory_id": "inside", "formed_at": "2026-09-10T12:00:00+00:00",
         "aggregated_at": "2026-09-12T12:00:00+00:00"},
    ]

    result = digest.summarize_formed(
        records,
        start=datetime(2026, 9, 10, tzinfo=timezone.utc),
        end=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )

    assert result["count"] == 1
    assert result["notable"][0]["memory_id"] == "inside"


@pytest.mark.parametrize("hour", [0, 12])
def test_period_endpoint_reads_retained_archive_by_formed_time(tmp_path, monkeypatch, hour):
    from cascade_compression import service

    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
    )
    local_tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 10, 24, hour, 5, tzinfo=local_tz)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(service, "datetime", FixedDateTime)
    monkeypatch.setenv("CASCADE_SUMMARY_TIMEZONE", "America/Chicago")
    today = now.replace(hour=0, minute=1)
    yesterday = today - timedelta(days=1)
    records = [
        {"memory_id": "today", "formed_at": today.astimezone(timezone.utc).isoformat(),
         "aggregated_at": yesterday.astimezone(timezone.utc).isoformat()},
        {"memory_id": "yesterday",
         "formed_at": yesterday.astimezone(timezone.utc).isoformat(),
         "aggregated_at": today.astimezone(timezone.utc).isoformat()},
    ]

    class Archive:
        def export_memories(self, min_strength=0.0):
            return {"memories": records}

    monkeypatch.setattr(service, "_memory_archive", Archive())
    monkeypatch.setattr(service, "_memory_digest", digest)

    result = service.memory_period_summaries()["periods"]

    assert result["today"]["count"] == 1
    assert result["today"]["notable"][0]["memory_id"] == "today"
    assert result["yesterday"]["count"] == 1
    assert result["yesterday"]["notable"][0]["memory_id"] == "yesterday"
    assert result["today"]["time_basis"] == "formed_at"


def test_record_count_pruning(tmp_path):
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
        max_records=3,
    )
    for i in range(5):
        digest.enqueue(SimpleNamespace(to_dict=lambda i=i: {
            "memory_id": f"m{i}", "formed_at": "2026-09-10T12:34:56+00:00",
            "source_instance": "cascade-k8s", "signal_type": "pod_failure",
            "severity": "high", "classification": "needs_attention", "strength": 0.8,
            "aggregated_at": datetime.now(timezone.utc).isoformat(),
        }))
    assert digest.pending_count() == 3


def test_age_pruning(tmp_path):
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
        retention_hours=1,
    )
    now = datetime.now(timezone.utc)
    recent = now.isoformat()
    old = (now - timedelta(hours=2)).isoformat()
    digest._write([
        {"memory_id": "recent", "aggregated_at": recent},
        {"memory_id": "old", "aggregated_at": old},
    ])
    assert digest.pending_count() == 1


def test_temp_file_cleanup_on_write_failure(tmp_path, monkeypatch):
    digest = FederatedMemoryDigest(
        spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
    )
    digest.enqueue(memory("existing"))
    original = digest.spool_path.read_text()

    def fail_replace(_source, _target):
        raise OSError("injected replace failure")

    monkeypatch.setattr(type(digest.spool_path), "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        digest.enqueue(memory("new"))

    assert digest.spool_path.read_text() == original
    assert not digest.spool_path.with_suffix(".json.tmp").exists()


def test_federated_import_persists_aggregate_state(monkeypatch):
    from cascade_compression import service

    class Archive:
        def import_memories(self, _data):
            return 2

    class Bridge:
        saved = False

        def save_state(self):
            self.saved = True

    bridge = Bridge()
    monkeypatch.setattr(service, "_memory_archive", Archive())
    monkeypatch.setattr(service, "_bridge", bridge)

    assert service.memory_import({"instance_id": "source", "memories": []}) == {"imported": 2}
    assert bridge.saved is True


# ---------------------------------------------------------------------------
# Service surface (OSS port)
# ---------------------------------------------------------------------------

class TestDigestEndpoints:
    @pytest.fixture
    def client(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient
        from cascade_compression import service

        digest = FederatedMemoryDigest(
            spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
        )
        monkeypatch.setattr(service, "_memory_digest", digest)
        monkeypatch.setattr(service, "_memory_archive", None)
        monkeypatch.setattr(service, "_bridge", None)
        monkeypatch.delenv("CASCADE_MEMORY_DIGEST_TOKEN", raising=False)
        return TestClient(service.app), digest

    def test_status_endpoint_reports_spool_state(self, client):
        client, digest = client
        digest.enqueue(memory("spooled"))
        body = client.get("/memories/digest/status").json()
        assert body == {
            "enabled": False,
            "interval_seconds": 3600,
            "pending": 1,
        }

    def test_summary_endpoint_requires_configured_token(self, client, monkeypatch):
        client, digest = client
        digest.enqueue(memory("secret-memory"))
        # No token configured: every request is rejected.
        assert client.get("/memories/digest/summary").status_code == 401
        assert client.get(
            "/memories/digest/summary",
            headers={"x-cascade-digest-token": "anything"},
        ).status_code == 401

        monkeypatch.setenv("CASCADE_MEMORY_DIGEST_TOKEN", "expected-token")
        assert client.get("/memories/digest/summary").status_code == 401
        assert client.get(
            "/memories/digest/summary",
            headers={"x-cascade-digest-token": "wrong"},
        ).status_code == 401
        response = client.get(
            "/memories/digest/summary",
            headers={"x-cascade-digest-token": "expected-token"},
        )
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_ack_endpoint_clears_only_the_current_digest_id(self, client, monkeypatch):
        client, digest = client
        monkeypatch.setenv("CASCADE_MEMORY_DIGEST_TOKEN", "expected-token")
        digest.enqueue(memory("m1"))
        summary = digest.summary()
        headers = {"x-cascade-digest-token": "expected-token"}

        wrong = client.post("/memories/digest/ack", headers=headers,
                            json={"digest_id": "not-the-id"})
        assert wrong.json() == {"acknowledged": False}
        assert digest.pending_count() == 1

        right = client.post("/memories/digest/ack", headers=headers,
                            json={"digest_id": summary["digest_id"]})
        assert right.json() == {"acknowledged": True}
        assert digest.pending_count() == 0

    def test_hourly_summary_is_content_free(self, client):
        client, digest = client
        digest.enqueue(memory("recent"))
        body = client.get("/memories/hourly-summary").json()
        assert body["count"] == 1
        # The public dashboard aggregate never carries the ack identity.
        assert "digest_id" not in body


class TestFederatedDigestWiring:
    def test_federated_import_feeds_the_digest_spool(self, monkeypatch, tmp_path):
        """Memories accepted via federation are enqueued for the digest."""
        from cascade_compression import service
        from cascade_compression.cascade.memory import MemoryArchive
        from cascade_compression.cascade.protocol import Signal

        digest = FederatedMemoryDigest(
            spool_path=str(tmp_path / "spool.json"), recipient="", smtp_host="",
        )
        archive = MemoryArchive()
        archive.add_federated_listener(digest.enqueue)
        monkeypatch.setattr(service, "_memory_archive", archive)
        monkeypatch.setattr(service, "_bridge", None)

        payload = {
            "instance_id": "cascade-k8s",
            "memories": [{
                "signal_type": "pod_failure",
                "severity": "high",
                "formed_at": "2026-10-20T12:00:00+00:00",
                "classification": "needs_attention",
                "content": {"message": "crashloop"},
            }],
        }
        assert service.memory_import(payload) == {"imported": 1}
        assert digest.pending_count() == 1
        assert digest.summary()["sources"] == {"cascade-k8s": 1}
