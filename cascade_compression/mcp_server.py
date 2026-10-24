"""MCP server exposing Cascade's federated shared memory to users and agents.

Read-only by design: agents may recall, query, inspect and export memories,
never write them. Every response carries each memory's verification status
and an explicit advisory note — non-exact matches are context, not truth, and
no memory viewed through this server grants suppression authority.

Two sources:
  --state FILE    a bridge state JSON (``memory_archive`` key) or a raw
                  MemoryArchive export; the persisted federated snapshot.
  --service URL   a running cascade service; calls are proxied to its
                  existing /recall, /memories/* endpoints.

The tool logic is deliberately independent of the MCP SDK so it can be tested
without it; only ``build_server`` imports the SDK (optional extra ``mcp``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ADVISORY_NOTE = (
    "Memories are advisory context for agents. Non-exact matches carry no "
    "suppression authority. Each memory includes its verification status; "
    "unverified memories must not be treated as truth, and federation never "
    "transfers review authority."
)

DEFAULT_TOP_K = 5
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _bounded(value: int, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIMIT))


def _memory_view(memory_dict: Dict[str, Any]) -> Dict[str, Any]:
    """One memory as an agent should see it: full record plus explicit status."""
    view = dict(memory_dict)
    verification = view.get("verification") or {}
    view["verification_status"] = verification.get("status", "unverified")
    return view


def _query_signal_fields(query: str, signal_type: Optional[str],
                         namespace: Optional[str], cluster: Optional[str]) -> Dict[str, Any]:
    return {
        "signal_type": (signal_type or "").strip(),
        "severity": "info",
        "source": "mcp-query",
        "namespace": (namespace or "").strip(),
        "cluster": (cluster or "").strip(),
        "content": {"message": query},
        "labels": {},
    }


class StateFileMemorySource:
    """The persisted federated archive: bridge state file or raw export."""

    def __init__(self, path: str):
        from .cascade.memory import MemoryArchive
        from .cascade.recall import RecallEngine
        from .cascade.protocol import Signal
        data = json.loads(Path(path).read_text())
        archive_data = data.get("memory_archive", data)
        self.archive = MemoryArchive.from_dict(archive_data)
        self.engine = RecallEngine()
        self._signal = Signal

    def recall(self, query: str, signal_type: Optional[str] = None,
               namespace: Optional[str] = None, cluster: Optional[str] = None,
               top_k: int = DEFAULT_TOP_K) -> Dict[str, Any]:
        fields = _query_signal_fields(query, signal_type, namespace, cluster)
        signal = self._signal(**fields)
        results = self.engine.recall(signal, self.archive,
                                      top_k=_bounded(top_k, DEFAULT_TOP_K),
                                      min_score=0.02,
                                      namespace=namespace or None,
                                      cluster=cluster or None)
        return {
            "note": ADVISORY_NOTE,
            "matches": [{
                "score": round(r.score, 4),
                "breakdown": {k: round(v, 4) for k, v in r.breakdown.items()},
                "exact_match": bool(r.exact_match),
                "memory": _memory_view(r.memory.to_dict()),
            } for r in results],
        }

    def stats(self) -> Dict[str, Any]:
        stats = self.archive.stats()
        memories = self.archive.query(limit=MAX_LIMIT)
        by_status: Dict[str, int] = {}
        for memory in memories:
            status = getattr(memory.verification, "status", "unverified")
            by_status[status] = by_status.get(status, 0) + 1
        # The verification tally is over the queried window, not a claim
        # about the whole archive when it exceeds the bound.
        stats["verification_counts_queried_window"] = by_status
        stats["note"] = ADVISORY_NOTE
        return stats

    def query(self, signal_type: Optional[str] = None,
              namespace: Optional[str] = None, label: Optional[str] = None,
              limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        limit = _bounded(limit, DEFAULT_LIMIT)
        memories = self.archive.query(limit=MAX_LIMIT)
        selected = []
        for memory in memories:
            if signal_type and memory.signal.signal_type != signal_type:
                continue
            if namespace and memory.signal.namespace != namespace:
                continue
            if label and label not in (memory.signal.labels or {}):
                continue
            selected.append(_memory_view(memory.to_dict()))
            if len(selected) >= limit:
                break
        return {"note": ADVISORY_NOTE, "memories": selected,
                "returned": len(selected)}

    def export(self, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        limit = _bounded(limit, DEFAULT_LIMIT)
        memories = sorted(self.archive.query(limit=MAX_LIMIT),
                          key=lambda m: m.strength, reverse=True)[:limit]
        return {
            "note": ADVISORY_NOTE,
            "bounded_view": True,
            "instance_id": self.archive.stats().get("instance_id", ""),
            "memories": [_memory_view(m.to_dict()) for m in memories],
        }


class ServiceMemorySource:
    """A running cascade service; proxied read-only through its HTTP API."""

    def __init__(self, base_url: str, transport: Any = None):
        import httpx
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(transport=transport, timeout=15.0,
                                    base_url=self._base)

    def recall(self, query: str, signal_type: Optional[str] = None,
               namespace: Optional[str] = None, cluster: Optional[str] = None,
               top_k: int = DEFAULT_TOP_K) -> Dict[str, Any]:
        body = _query_signal_fields(query, signal_type, namespace, cluster)
        response = self._client.post("/recall", json=body)
        response.raise_for_status()
        result = response.json()
        # The service returns slim ranked matches (ids and scores). Hydrate
        # them with full records so agents can read content and verification
        # state; unhydrated matches keep the slim fields. Note: the service's
        # own recall semantics reinforce matched memories (strength and
        # recall_count); this proxy never writes anything itself.
        matches = result.get("matches", [])
        # Hydrate slim matches with full records so agents can read content
        # and verification state. The query pool is strongest-first, so the
        # join is scoped per matched signal_type — a global pool would miss
        # every low-strength match below the high-strength bulk.
        by_type: Dict[str, set] = {}
        for match in matches:
            if match.get("memory_id"):
                by_type.setdefault(match.get("signal_type") or "", set()).add(
                    match["memory_id"])
        by_id: Dict[str, Dict[str, Any]] = {}
        for stype, wanted in by_type.items():
            body_query = {"limit": 500}
            if stype:
                body_query["signal_type"] = stype
            full = self._client.post("/memories/query", json=body_query)
            full.raise_for_status()
            for record in full.json().get("memories", []):
                if record.get("memory_id") in wanted:
                    by_id[record["memory_id"]] = _memory_view(record)
        for match in matches:
            hydrated = by_id.get(match.get("memory_id"))
            if hydrated is not None:
                match["memory"] = hydrated
        result["note"] = ADVISORY_NOTE
        return result

    def stats(self) -> Dict[str, Any]:
        response = self._client.get("/memories/stats")
        response.raise_for_status()
        result = response.json()
        result["note"] = ADVISORY_NOTE
        return result

    def query(self, signal_type: Optional[str] = None,
              namespace: Optional[str] = None, label: Optional[str] = None,
              limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        body: Dict[str, Any] = {"limit": _bounded(limit, DEFAULT_LIMIT)}
        if signal_type:
            body["signal_type"] = signal_type
        if namespace:
            body["namespace"] = namespace
        if label:
            body["labels"] = {label: ""}
        response = self._client.post("/memories/query", json=body)
        response.raise_for_status()
        result = response.json()
        result["returned"] = len(result.get("memories", []))
        result["note"] = ADVISORY_NOTE
        return result

    def export(self, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        response = self._client.get("/memories/export")
        response.raise_for_status()
        result = response.json()
        memories = result.get("memories", [])
        result["memories"] = [_memory_view(m) for m in
                              memories[:_bounded(limit, DEFAULT_LIMIT)]]
        result["bounded_view"] = True
        result["note"] = ADVISORY_NOTE
        return result


def build_server(source: Any):
    """Wire the read-only memory tools onto an MCP server (SDK required)."""
    if sys.version_info < (3, 10):
        raise SystemExit("The optional MCP server requires Python 3.10 or newer; the core supports Python 3.9.")
    try:
        from mcp.server.mcpserver import MCPServer
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "The MCP SDK is required for the server. Install the optional "
            "extra: uv sync --extra mcp (or pip install 'cascade-compression[mcp]')"
        ) from exc

    server = MCPServer(
        name="cascade-memory",
        title="Cascade federated memory",
        description="Read-only recall, query, inspection and bounded export "
                    "of Cascade's federated shared memory.",
        instructions=ADVISORY_NOTE,
    )

    def recall(query: str, signal_type: Optional[str] = None,
               namespace: Optional[str] = None, cluster: Optional[str] = None,
               top_k: int = DEFAULT_TOP_K) -> Dict[str, Any]:
        """Recall memories similar to a natural-language query.

        Results are ranked with score breakdowns and an exact_match flag.
        Non-exact matches are advisory context only.
        """
        return source.recall(query=query, signal_type=signal_type,
                             namespace=namespace, cluster=cluster, top_k=top_k)

    def memories_stats() -> Dict[str, Any]:
        """Archive size, strengths, federated sources and verification counts."""
        return source.stats()

    def query_memories(signal_type: Optional[str] = None,
                       namespace: Optional[str] = None,
                       label: Optional[str] = None,
                       limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        """Filter memories by signal type, namespace or label."""
        return source.query(signal_type=signal_type, namespace=namespace,
                            label=label, limit=limit)

    def export_memories(limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        """A bounded, strongest-first view of the federated export pack."""
        return source.export(limit=limit)

    server.add_tool(recall, name="recall",
                    description="Recall memories similar to a query (advisory context).")
    server.add_tool(memories_stats, name="memories_stats",
                    description="Archive stats including verification counts.")
    server.add_tool(query_memories, name="query_memories",
                    description="Filter memories by type, namespace or label.")
    server.add_tool(export_memories, name="export_memories",
                    description="Bounded strongest-first federated memory pack.")

    # RAG extension: multi-perspective system understanding (service mode only)
    if hasattr(source, "_base"):
        try:
            from .mcp_rag import register_rag_tools
            import httpx as _httpx
            _client = _httpx.Client(base_url=source._base, timeout=15.0)
            register_rag_tools(server, _client, source._base)
        except (ImportError, AttributeError):
            pass  # RAG tools are optional; state-file sources skip them

    return server


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MCP server for Cascade's federated shared memory (read-only).")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--state", help="bridge state JSON or raw archive export")
    source_group.add_argument("--service", help="base URL of a running cascade service")
    args = parser.parse_args()

    if args.state:
        source = StateFileMemorySource(args.state)
    else:
        source = ServiceMemorySource(args.service)

    server = build_server(source)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
