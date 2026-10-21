"""Tests for the MCP memory server: source logic, safety framing, wiring.

The core tool logic is SDK-independent; only the final wiring test needs the
optional ``mcp`` extra and skips without it. No network, models or writes.
"""
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.cascade.protocol import Signal
from cascade_compression.mcp_server import (
    ADVISORY_NOTE, ServiceMemorySource, StateFileMemorySource, _bounded,
    build_server,
)


def make_archive(tmp_path: Path) -> Path:
    archive = MemoryArchive(instance_id="federated-test")
    identified = {
        "message": "payment gateway connection pool exhausted",
        "event_uid": "10000000-0000-4000-8000-000000000001",
    }
    memory = None
    for _ in range(3):
        memory = archive.store(Signal(
            signal_type="event_failedmount", severity="medium",
            source="collector", namespace="payments", cluster="prod",
            labels={"domain": "kubernetes"}, content=dict(identified)))
    archive.verify_memory(memory.memory_id, outcome="benign",
                          source="independent-review",
                          policy_revision="memory-policy-v1",
                          evidence_ref="review:case-1")
    archive.store(Signal(
        signal_type="event_backoff", severity="medium", source="collector",
        namespace="payments", cluster="prod", labels={"domain": "kubernetes"},
        content={"message": "crash loop backoff restarting",
                 "event_uid": "10000000-0000-4000-8000-000000000002"}))
    archive.store(Signal(
        signal_type="event_failedsched", severity="medium", source="collector",
        namespace="edge", cluster="prod", labels={"domain": "kubernetes"},
        content={"message": "scheduler node pressure pending",
                 "event_uid": "10000000-0000-4000-8000-000000000003"}))
    # Bridge-state wrapper format.
    state = {"memory_archive": archive.to_dict(), "stats": {}}
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    return path


class TestStateFileSource:
    def test_loads_bridge_state_and_raw_export(self, tmp_path):
        path = make_archive(tmp_path)
        source = StateFileMemorySource(str(path))
        assert source.archive.size == 3
        raw = tmp_path / "raw.json"
        raw.write_text(json.dumps(json.loads(path.read_text())["memory_archive"]))
        assert StateFileMemorySource(str(raw)).archive.size == 3

    def test_recall_ranks_with_scores_and_verification(self, tmp_path):
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        result = source.recall("connection pool exhaustion on payment gateway")
        assert result["note"] == ADVISORY_NOTE
        assert result["matches"]
        top = result["matches"][0]
        assert top["score"] > 0
        assert "breakdown" in top and "exact_match" in top
        assert top["memory"]["signal_type"] == "event_failedmount"
        assert top["memory"]["verification_status"] == "approved"

    def test_recall_unverified_memory_reports_status(self, tmp_path):
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        result = source.recall("scheduler node pressure pending")
        matches = [m for m in result["matches"]
                   if m["memory"]["signal_type"] == "event_failedsched"]
        assert matches
        assert matches[0]["memory"]["verification_status"] == "unverified"

    def test_recall_namespace_filter_scopes(self, tmp_path):
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        result = source.recall("node pressure", namespace="edge")
        assert result["matches"]
        assert all(m["memory"]["namespace"] == "edge" for m in result["matches"])

    def test_query_filters(self, tmp_path):
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        result = source.query(signal_type="event_backoff")
        assert result["returned"] == 1
        assert result["memories"][0]["namespace"] == "payments"
        assert source.query(namespace="nowhere")["returned"] == 0

    def test_export_is_bounded_and_strongest_first(self, tmp_path):
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        result = source.export(limit=2)
        assert result["bounded_view"] is True
        assert len(result["memories"]) == 2
        strengths = [m["strength"] for m in result["memories"]]
        assert strengths == sorted(strengths, reverse=True)

    def test_stats_includes_verification_counts(self, tmp_path):
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        stats = source.stats()
        assert stats["size"] == 3
        assert stats["verification_counts_queried_window"]["approved"] == 1
        assert stats["verification_counts_queried_window"]["unverified"] == 2

    def test_bounded_helper(self):
        assert _bounded(0, 5) == 1
        assert _bounded(1000, 5) == 100
        assert _bounded("not-a-number", 5) == 5


class TestServiceSource:
    def _transport(self, tmp_path):
        import httpx
        archive_source = StateFileMemorySource(str(make_archive(tmp_path)))
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/recall":
                body = json.loads(request.content)
                return httpx.Response(200, json=archive_source.recall(
                    query=body["content"]["message"],
                    namespace=body.get("namespace") or None))
            if request.url.path == "/memories/stats":
                return httpx.Response(200, json=archive_source.stats())
            if request.url.path == "/memories/query":
                body = json.loads(request.content)
                return httpx.Response(200, json=archive_source.query(
                    signal_type=body.get("signal_type")))
            if request.url.path == "/memories/export":
                return httpx.Response(200, json=archive_source.export(limit=100))
            return httpx.Response(404)

        return httpx.MockTransport(handler), requests

    def test_service_proxy_recall_and_stats(self, tmp_path):
        transport, requests = self._transport(tmp_path)
        source = ServiceMemorySource("http://cascade-memory:8090",
                                      transport=transport)
        result = source.recall("payment gateway connection pool")
        assert result["note"] == ADVISORY_NOTE
        assert result["matches"]
        stats = source.stats()
        assert stats["size"] == 3
        assert all(r.url.host == "cascade-memory" for r in requests)

    def test_service_proxy_query_and_export(self, tmp_path):
        transport, _ = self._transport(tmp_path)
        source = ServiceMemorySource("http://svc", transport=transport)
        assert source.query(signal_type="event_backoff")["returned"] == 1
        exported = source.export(limit=1)
        assert len(exported["memories"]) == 1


class TestServerWiring:
    def test_tools_registered_and_callable(self, tmp_path):
        pytest.importorskip("mcp")
        import asyncio
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        server = build_server(source)
        tools = {t.name for t in asyncio.run(server.list_tools())}
        assert tools == {"recall", "memories_stats", "query_memories",
                         "export_memories"}
        recall_tool = next(t for t in asyncio.run(server.list_tools())
                           if t.name == "recall")
        assert "advisory" in (recall_tool.description or "").lower()

    def test_missing_sdk_exits_with_install_hint(self, tmp_path):
        source = StateFileMemorySource(str(make_archive(tmp_path)))
        with patch.dict("sys.modules", {"mcp": None, "mcp.server": None,
                                        "mcp.server.mcpserver": None}):
            with pytest.raises(SystemExit, match="optional"):
                build_server(source)
