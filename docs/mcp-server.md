# MCP server for the federated shared memory

`cascade-mcp` exposes Cascade's federated shared memory to users and agents
over the Model Context Protocol. It is **read-only by design**: agents may
recall, query, inspect and export memories, never write them.

Every response includes each memory's verification status and an explicit
advisory note: non-exact matches are context, not truth, and no memory viewed
through this server grants suppression authority. Unverified memories must
not be treated as facts, and federation never transfers review authority.

## Install

The server needs the optional `mcp` extra:

```sh
uv sync --extra mcp        # or: pip install 'cascade-compression[mcp]'
```

## Sources

- `--state FILE` — a bridge state JSON (the `memory_archive` section) or a raw
  archive export. This is the persisted snapshot of a federated instance; the
  server loads it offline with no network access.
- `--service URL` — a running cascade service; calls are proxied to its
  existing `/recall` and `/memories/*` endpoints for a live view.

## Tools

| Tool | Purpose |
|------|---------|
| `recall` | Ranked memories for a natural-language query, with score breakdowns and an `exact_match` flag |
| `memories_stats` | Archive size, strengths, federated sources, verification counts |
| `query_memories` | Filter by signal type, namespace or label |
| `export_memories` | Bounded, strongest-first view of the federated export pack |

Results are bounded (at most 100 per call).

**Recall tip:** pass a `signal_type` hint when you know the domain
(`recall("hotfix revert", signal_type="hotfix_pattern")`). A running service
applies a similarity floor; type-less natural-language queries can score just
under it while type-anchored queries clear it easily and hydrate full records.

## Claude Desktop / Claude Code configuration

```json
{
  "mcpServers": {
    "cascade-memory": {
      "command": "cascade-mcp",
      "args": ["--state", "/path/to/cascade-state.json"]
    }
  }
}
```

Or against a running service:

```json
{
  "mcpServers": {
    "cascade-memory": {
      "command": "cascade-mcp",
      "args": ["--service", "http://cascade-memory:8090"]
    }
  }
}
```

## Quickstart for consumers

Try it in 30 seconds against the labeled synthetic demo state:

```sh
uv tool install --from "git+https://github.com/jkershawrh/cascade-compression" "cascade-compression[mcp]"
cascade-mcp --state tests/fixtures/mcp-demo-state.json   # from a repo clone
# interactive exploration:
npx @modelcontextprotocol/inspector cascade-mcp --state tests/fixtures/mcp-demo-state.json
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.cascade-memory]
command = "cascade-mcp"
args = ["--state", "/path/to/cascade-state.json"]
```

Claude Code (`.mcp.json` in the project):

```json
{"mcpServers": {"cascade-memory": {
  "command": "cascade-mcp",
  "args": ["--state", "/path/to/cascade-state.json"]}}}
```

## Claim boundary

The server surfaces memory content and verification state; it makes no
effectiveness, truth or safety claim. A memory marked `approved` under a
governed review is still advisory context here — suppression authority is
evaluated inside Cascade's engine under its own contracts, never by an agent
reading through this interface.
