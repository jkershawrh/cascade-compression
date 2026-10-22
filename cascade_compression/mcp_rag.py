"""RAG extension for cascade-mcp: multi-perspective system understanding.

An agent connects via MCP and can ask the system to explain itself from
different angles — operator, analyst, auditor, user, debug — or search
across its knowledge sources. The tools retrieve structured context from
the live cascade service; the agent's LLM synthesizes the natural-language
answer for the end user.

Read-only. Extends the existing cascade-mcp server with four new tools:
  system_perspective  — curated view from a named perspective
  explain_signal      — trace how a signal type flows through the system
  search_knowledge    — semantic search across memories + system state
  system_narrative    — documentation + live state about a topic
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PERSPECTIVES = {
    "operator": {
        "description": "What's happening right now? Compression, active agents, queue health.",
        "sources": ["stats", "agents", "health", "meta"],
    },
    "analyst": {
        "description": "Why is the system making these decisions? Classification patterns, noise/important ratios.",
        "sources": ["stats", "classifier", "noise_types", "important_types"],
    },
    "auditor": {
        "description": "What has the system learned? Promotion/demotion history, GCL verdicts, safety events.",
        "sources": ["promotion_log", "gcl", "demotions", "shadow"],
    },
    "user": {
        "description": "What do I need to know? Important signals, memory highlights, actionable context.",
        "sources": ["important_types", "memories_stats", "recent_memories"],
    },
    "debug": {
        "description": "Why isn't something working? Configuration, classifier state, queue depths, errors.",
        "sources": ["health", "classifier", "queue", "errors"],
    },
}

NARRATIVES = {
    "compression": {
        "title": "How Cascade compresses signals",
        "explanation": (
            "Cascade uses a three-tier pipeline. The nano tier (deterministic rules) "
            "suppresses the bulk of signals for free — exact repeats, transient warnings, "
            "below-threshold patterns. Survivors go to the micro tier (llm-d-sc semantic "
            "classification on CPU). Only signals the semantic classifier can't handle "
            "confidently fall through to the macro tier (granite LLM). The compression "
            "ratio measures what fraction never needs a model call."
        ),
        "live_sources": ["compression_ratio", "cascade_handled", "llm_classified",
                          "sc_authoritative"],
    },
    "learning": {
        "title": "How Cascade learns what to suppress",
        "explanation": (
            "Classification outcomes feed a promotion engine. When a signal type "
            "accumulates 200+ noise classifications with zero important, an agent "
            "is promoted to suppress that type deterministically. Any confirmed "
            "false negative (an important signal that was suppressed) instantly "
            "demotes the agent. All authority has a 72-hour TTL and must re-qualify. "
            "The GCL verdict system audits decisions and FAILS verdicts trigger demotion."
        ),
        "live_sources": ["activated_types", "noise_counts", "important_counts",
                          "promotion_log", "gcl_builtin_fails"],
    },
    "safety": {
        "title": "How Cascade avoids suppressing important signals",
        "explanation": (
            "Multiple safety layers: (1) high/critical severity signals are never "
            "suppressed — fail-open. (2) The verified-repeat profile requires complete "
            "event identity (UID, namespace, message digest match) before suppressing. "
            "(3) Count decreases are treated as suspect and delivered. (4) Shadow "
            "validation re-checks 5% of suppressed signals. (5) GCL verdict FAILS "
            "trigger instant demotion. (6) All learned authority expires after 72 hours."
        ),
        "live_sources": ["shadow_checks", "shadow_demotions", "fn_evaluated",
                          "gcl_builtin_fails", "activated_types"],
    },
    "memory": {
        "title": "How Cascade builds institutional memory",
        "explanation": (
            "Signals that survive the pipeline and are classified form memories — "
            "strength-weighted records with content, verification status, and "
            "provenance. Memories decay over time but are reinforced by recall and "
            "consolidation. Federation allows instances to share memories: k8s, AAP, "
            "and knowledge sources all feed a central aggregator. Memories can only "
            "support suppression if they carry a verified benign outcome bound to "
            "the current policy — unverified memories are advisory context only."
        ),
        "live_sources": ["memories_stats", "federated_sources", "verification_counts"],
    },
    "governance": {
        "title": "How Cascade governs itself",
        "explanation": (
            "The GCL (Ground Control Ledger) records every decision. An audit "
            "CronJob reviews decisions and writes verdicts (PASS/FAIL) to a ledger. "
            "The bridge polls the ledger for FAILS verdicts and demotes the "
            "responsible agents. The verdict poll uses a bounded window (20 min) "
            "to avoid querying stale backlogs. External feedback (confirmed false "
            "negatives) also triggers demotion. All authority changes are logged "
            "with provenance."
        ),
        "live_sources": ["gcl_builtin_fails", "fn_evaluated", "promotion_log"],
    },
    "classification": {
        "title": "How Cascade classifies signals",
        "explanation": (
            "The hybrid classifier uses llm-d-sc (semantic, CPU-optimized) as the "
            "primary path. Signals are matched against a taxonomy of anchors using "
            "cosine similarity. When confidence is low (margin below threshold), "
            "the signal falls back to a granite LLM for generative classification. "
            "The taxonomy can be refined by the anchor engineering pipeline, which "
            "proposes new anchors for patterns the classifier struggles with. "
            "Classification outcomes feed the promotion engine and memory formation."
        ),
        "live_sources": ["classifier_mode", "sc_authoritative", "llm_fallback",
                          "noise_types", "important_types"],
    },
}


def gather_perspective(client, base_url: str, perspective: str) -> Dict[str, Any]:
    """Retrieve the relevant system state for a named perspective."""
    config = PERSPECTIVES.get(perspective)
    if not config:
        return {"error": f"unknown perspective: {perspective}",
                "available": sorted(PERSPECTIVES.keys())}

    result = {"perspective": perspective,
              "description": config["description"],
              "data": {}}

    for source in config["sources"]:
        try:
            if source == "stats":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    stats = r.json().get("stats", {})
                    result["data"]["compression"] = stats.get("compression_ratio")
                    result["data"]["signals_handled"] = stats.get("cascade_handled")
                    result["data"]["signals_forwarded"] = stats.get("cascade_forwarded")
                    result["data"]["noise"] = stats.get("llm_noise")
                    result["data"]["important"] = stats.get("llm_important")
                    result["data"]["activated_agents"] = list(
                        stats.get("activated_agents", []))
                    cov = stats.get("classifier", {}).get("coverage", {})
                    result["data"]["semantic_classified"] = cov.get("sc_authoritative")
                    result["data"]["llm_fallback"] = cov.get("llm_fallback")

            elif source == "agents":
                r = client.get(f"{base_url}/agents")
                if r.status_code == 200:
                    result["data"]["agents"] = r.json()

            elif source == "health":
                r = client.get(f"{base_url}/health")
                if r.status_code == 200:
                    result["data"]["health"] = r.json()

            elif source == "meta":
                r = client.get(f"{base_url}/meta")
                if r.status_code == 200:
                    meta = r.json()
                    result["data"]["recent_events"] = [
                        {"type": m.get("type"), "severity": m.get("severity"),
                         "message": m.get("message", "")[:100]}
                        for m in (meta if isinstance(meta, list) else meta.get("meta", []))[:5]]

            elif source == "classifier":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    c = r.json().get("stats", {}).get("classifier", {})
                    result["data"]["classifier_mode"] = c.get("mode")
                    result["data"]["config_error"] = c.get("config_error", "")
                    cov = c.get("coverage", {})
                    result["data"]["coverage"] = {k: cov.get(k) for k in
                        ("sc_authoritative", "generative_authoritative",
                         "llm_fallback", "sc_failure")}

            elif source == "noise_types":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    s = r.json().get("stats", {})
                    result["data"]["noise_by_type"] = s.get("llm_noise_counts", {})

            elif source == "important_types":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    s = r.json().get("stats", {})
                    result["data"]["important_by_type"] = s.get("llm_important_counts", {})

            elif source == "promotion_log":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    s = r.json().get("stats", {})
                    result["data"]["gcl_fails"] = s.get("gcl_builtin_fails")
                    result["data"]["fn_evaluated"] = s.get("fn_evaluated")
                    result["data"]["fn_count"] = s.get("fn_count")

            elif source == "shadow":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    s = r.json().get("stats", {})
                    result["data"]["shadow_checks"] = s.get("shadow_checks")
                    result["data"]["shadow_demotions"] = s.get("shadow_demotions")

            elif source == "memories_stats":
                r = client.get(f"{base_url}/memories/stats")
                if r.status_code == 200:
                    result["data"]["memory"] = r.json()

            elif source == "recent_memories":
                r = client.post(f"{base_url}/memories/query",
                                json={"limit": 5})
                if r.status_code == 200:
                    mems = r.json().get("memories", [])
                    result["data"]["recent_memories"] = [
                        {"type": m.get("signal_type"), "classification": m.get("classification"),
                         "strength": m.get("strength"),
                         "message": (m.get("content") or {}).get("message", "")[:80]}
                        for m in mems]

            elif source == "queue":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    s = r.json().get("stats", {})
                    o = s.get("llm_signal_outcomes_since_start", {})
                    result["data"]["queue"] = o

            elif source == "errors":
                r = client.get(f"{base_url}/stats")
                if r.status_code == 200:
                    s = r.json().get("stats", {})
                    result["data"]["llm_dropped"] = s.get("llm_dropped")
                    result["data"]["write_failures"] = s.get("classifier", {}).get("write_failures")

        except Exception as exc:
            result["data"][source] = {"error": str(exc)[:100]}

    return result


def explain_signal_flow(client, base_url: str, signal_type: str,
                        namespace: Optional[str] = None) -> Dict[str, Any]:
    """Trace how a signal type flows through the cascade."""
    result = {"signal_type": signal_type, "namespace": namespace,
              "flow": [], "context": {}}

    # What the system knows about this type
    r = client.get(f"{base_url}/stats")
    if r.status_code == 200:
        stats = r.json().get("stats", {})
        noise = stats.get("llm_noise_counts", {}).get(signal_type, 0)
        important = stats.get("llm_important_counts", {}).get(signal_type, 0)
        activated = signal_type in (stats.get("activated_agents") or
                                    stats.get("llm_noise_counts", {}))
        result["context"]["noise_classifications"] = noise
        result["context"]["important_classifications"] = important
        result["context"]["total_classified"] = noise + important

        # Build the flow narrative
        if noise + important == 0:
            result["flow"].append({
                "stage": "classification",
                "status": "unclassified",
                "detail": "No classification outcomes for this type yet"})
        else:
            if important > 0:
                result["flow"].append({
                    "stage": "classification",
                    "status": "mixed",
                    "detail": f"{noise} noise, {important} important — system is learning this type"})
            else:
                result["flow"].append({
                    "stage": "classification",
                    "status": "noise_only",
                    "detail": f"{noise} noise, 0 important — eligible for promotion at 200+"})

            if noise >= 200 and important == 0:
                result["flow"].append({
                    "stage": "promotion",
                    "status": "eligible",
                    "detail": "Meets promotion threshold (200+ noise, 0 important)"})
            elif noise < 200:
                result["flow"].append({
                    "stage": "promotion",
                    "status": "accumulating",
                    "detail": f"{noise}/200 noise samples needed"})

    # Memory context
    r = client.post(f"{base_url}/memories/query",
                    json={"signal_type": signal_type, "limit": 3})
    if r.status_code == 200:
        mems = r.json().get("memories", [])
        if mems:
            result["context"]["memories"] = [
                {"classification": m.get("classification"),
                 "strength": round(m.get("strength", 0), 2),
                 "message": (m.get("content") or {}).get("message", "")[:80],
                 "verified": (m.get("verification") or {}).get("status", "unverified")}
                for m in mems]

    # Recall similar memories
    r = client.post(f"{base_url}/recall",
                    json={"signal_type": signal_type,
                          "severity": "medium",
                          "content": {"message": signal_type},
                          "namespace": namespace or ""})
    if r.status_code == 200:
        matches = r.json().get("matches", [])
        if matches:
            result["context"]["similar_memories"] = len(matches)

    return result


def build_narrative(client, base_url: str, topic: str) -> Dict[str, Any]:
    """Return documentation + live state for a system topic."""
    config = NARRATIVES.get(topic)
    if not config:
        return {"error": f"unknown topic: {topic}",
                "available": sorted(NARRATIVES.keys())}

    result = {
        "topic": topic,
        "title": config["title"],
        "explanation": config["explanation"],
        "live_state": {},
    }

    r = client.get(f"{base_url}/stats")
    if r.status_code == 200:
        stats = r.json().get("stats", {})
        for key in config["live_sources"]:
            if key in stats:
                result["live_state"][key] = stats[key]
            elif key == "activated_types":
                result["live_state"][key] = list(stats.get("llm_noise_counts", {}).keys())
            elif key == "noise_counts":
                result["live_state"][key] = stats.get("llm_noise_counts", {})
            elif key == "important_counts":
                result["live_state"][key] = stats.get("llm_important_counts", {})
            elif key == "classifier_mode":
                result["live_state"][key] = stats.get("classifier", {}).get("mode")
            elif key == "sc_authoritative":
                cov = stats.get("classifier", {}).get("coverage", {})
                result["live_state"][key] = cov.get("sc_authoritative")
            elif key == "llm_fallback":
                cov = stats.get("classifier", {}).get("coverage", {})
                result["live_state"][key] = cov.get("llm_fallback")
            elif key == "federated_sources":
                pass  # from memories/stats
            elif key == "verification_counts":
                pass  # from memories/stats

    # Memory stats for memory-related topics
    if topic in ("memory", "governance"):
        r = client.get(f"{base_url}/memories/stats")
        if r.status_code == 200:
            ms = r.json()
            result["live_state"]["memory_size"] = ms.get("size")
            result["live_state"]["federated_sources"] = ms.get("federated_sources")

    return result


def register_rag_tools(server, client, base_url: str):
    """Register the RAG tools on an existing MCP server."""

    def system_perspective(perspective: str) -> Dict[str, Any]:
        """Get the cascade system's state from a named perspective.

        Perspectives: operator (what's happening now), analyst (why decisions
        are being made), auditor (what has been learned, governance events),
        user (what matters to me), debug (why isn't something working).

        Returns structured data the agent synthesizes into an explanation.
        """
        return gather_perspective(client, base_url, perspective)

    def explain_signal(signal_type: str,
                       namespace: Optional[str] = None) -> Dict[str, Any]:
        """Explain how a signal type flows through the cascade system.

        Traces the classification outcomes, promotion status, memory context,
        and suppression eligibility for the given signal type. Use this to
        answer 'why is the system handling X this way?'
        """
        return explain_signal_flow(client, base_url, signal_type, namespace)

    def search_knowledge(query: str,
                         signal_type: Optional[str] = None,
                         limit: int = 10) -> Dict[str, Any]:
        """Search the cascade system's knowledge — memories and state.

        Combines memory recall (semantic similarity) with system state
        (classification stats, active agents) to answer questions about
        what the system knows and how it's behaving.
        """
        results = {"query": query, "memories": [], "system_context": {}}

        # Semantic recall
        r = client.post(f"{base_url}/recall", json={
            "signal_type": signal_type or "",
            "severity": "medium",
            "content": {"message": query},
            "namespace": "",
        })
        if r.status_code == 200:
            for m in r.json().get("matches", [])[:limit]:
                mem = m.get("memory", m)
                results["memories"].append({
                    "signal_type": mem.get("signal_type") or m.get("signal_type"),
                    "message": (mem.get("content") or {}).get("message", "")[:100],
                    "strength": mem.get("strength"),
                    "score": m.get("score"),
                    "classification": mem.get("classification"),
                })

        # System context
        r = client.get(f"{base_url}/stats")
        if r.status_code == 200:
            stats = r.json().get("stats", {})
            results["system_context"] = {
                "compression": stats.get("compression_ratio"),
                "active_agents": list(stats.get("llm_noise_counts", {}).keys())[:5],
                "important_types": list(stats.get("llm_important_counts", {}).keys())[:5],
            }

        return results

    def system_narrative(topic: str) -> Dict[str, Any]:
        """Get a narrative explanation of a cascade system topic.

        Topics: compression, learning, safety, memory, governance,
        classification. Returns documentation plus live state — the agent
        synthesizes these into a user-friendly explanation.
        """
        return build_narrative(client, base_url, topic)

    server.add_tool(system_perspective, name="system_perspective",
                    description="Get the system state from a named perspective "
                                "(operator/analyst/auditor/user/debug).")
    server.add_tool(explain_signal, name="explain_signal",
                    description="Trace how a signal type flows through the cascade.")
    server.add_tool(search_knowledge, name="search_knowledge",
                    description="Search memories and system state by query.")
    server.add_tool(system_narrative, name="system_narrative",
                    description="Get documentation + live state about a system topic.")
    return server
