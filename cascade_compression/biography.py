"""Platform biography — structured autobiography from cascade state.

Pure data aggregation — no LLM calls, no file I/O. Tells the story
of what the platform has experienced through its memory and inverse
cascade data.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List


def generate_biography(bridge, memory_archive, memory_intel) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    # ── 1. Timeline ──────────────────────────────────────────────────
    timeline = {"generated_at": now}
    if bridge:
        timeline["started_at"] = bridge.stats.started_at
        timeline["signals_processed"] = bridge.stats.signals_processed
        timeline["domain"] = bridge.domain

        milestones = []
        for entry in bridge.get_promotion_log(1000):
            event = entry.get("event", "")
            if event in ("activated", "promoted", "discovered"):
                milestones.append({
                    "timestamp": entry.get("timestamp", ""),
                    "event": event,
                    "agent": entry.get("agent", ""),
                    "tier": entry.get("tier", ""),
                })
        activations = [m for m in milestones if m["event"] == "activated"]
        if activations:
            timeline["first_activation"] = activations[0]
            timeline["latest_activation"] = activations[-1]
            timeline["total_activations"] = len(activations)

        demotions = [e for e in bridge.get_promotion_log(1000)
                     if e.get("event") == "demotion"
                     or e.get("event_type") == "demotion"]
        if demotions:
            timeline["first_demotion"] = {
                "timestamp": demotions[0].get("timestamp", ""),
                "agent": demotions[0].get("agent",
                                          demotions[0].get("agent_name", "")),
                "reason": demotions[0].get("reason", ""),
            }
            timeline["total_demotions"] = len(demotions)

        timeline["milestone_count"] = len(milestones)

    # ── 2. Top Memories ──────────────────────────────────────────────
    top_memories = []
    if memory_archive and memory_archive.size > 0:
        strongest = memory_archive.query(min_strength=0.0, limit=20)
        for m in strongest:
            entry = {
                "signal_type": m.signal.signal_type,
                "severity": m.signal.severity,
                "strength": round(m.strength, 4),
                "formed_at": m.formed_at,
                "recall_count": m.recall_count,
                "consolidation_count": m.consolidation_count,
                "classification": m.classification,
                "message": m.signal.content.get("message", "")[:200],
            }
            if m.analysis:
                entry["has_analysis"] = True
                entry["root_cause"] = m.analysis.get("root_cause", "")[:200]
            top_memories.append(entry)

    # ── 3. Patterns Learned ──────────────────────────────────────────
    patterns_learned = []
    if bridge:
        for sig_type in sorted(bridge._activated_types):
            pattern_type = bridge._activated_patterns.get(sig_type, "unknown")
            noise_count = bridge._llm_noise_counts.get(sig_type, 0)
            important_count = bridge._llm_important_counts.get(sig_type, 0)
            activated_at = bridge._activation_timestamps.get(sig_type, "")
            patterns_learned.append({
                "signal_type": sig_type,
                "pattern_type": pattern_type,
                "noise_count": noise_count,
                "important_count": important_count,
                "activated_at": activated_at,
                "action": (
                    "repeat flood suppression"
                    if pattern_type == "repeat_flood"
                    else "dominant noise suppression"
                ),
            })

    # ── 4. Noise Profile ─────────────────────────────────────────────
    noise_profile: Dict[str, Any] = {"baseline_types": 0, "top_noise": []}
    suppression = bridge.suppression_archive if bridge else None
    if suppression and suppression.size > 0:
        from .cascade.inverse import generate_baseline
        baseline = generate_baseline(suppression)
        noise_profile["baseline_types"] = len(baseline.signal_types)
        noise_profile["total_suppression_decisions"] = suppression._total_decisions
        top_noise = sorted(
            [{"signal_type": k, "frequency": v.get("frequency", 0),
              "strength": round(v.get("strength", 0), 3),
              "agents": v.get("agents", [])}
             for k, v in baseline.signal_types.items()],
            key=lambda x: x["frequency"],
            reverse=True,
        )[:15]
        noise_profile["top_noise"] = top_noise
        noise_profile["interpretation"] = (
            f"The platform considers {len(baseline.signal_types)} signal types "
            f"to be normal background noise, learned from "
            f"{suppression._total_decisions} suppression decisions."
        )

    # ── 5. Causal Chains ─────────────────────────────────────────────
    causal_chains: List[Dict[str, str]] = []
    if memory_intel:
        graph = memory_intel.causal_graph
        for cause, effects in graph._forward.items():
            for effect in effects:
                causal_chains.append({"cause": cause, "effect": effect})

    # ── 6. Gaps ──────────────────────────────────────────────────────
    causal_gaps: List[Dict[str, Any]] = []
    if memory_archive and memory_intel:
        from .cascade.inverse import find_all_gaps
        causal_gaps = find_all_gaps(memory_archive, memory_intel.causal_graph)

    # ── 7. Absences ──────────────────────────────────────────────────
    absences: List[Dict[str, Any]] = []
    if memory_intel:
        detector = memory_intel.absence_detector
        if detector.expectations:
            absences = detector.check_missing(now)

    # ── 8. GPU Analyses ──────────────────────────────────────────────
    gpu_summary: Dict[str, Any] = {"count": 0, "analyses": []}
    if bridge and bridge._gpu_analyses:
        analyses = bridge._gpu_analyses
        gpu_summary["count"] = len(analyses)
        by_type: Dict[str, list] = defaultdict(list)
        for a in analyses:
            by_type[a.get("signal_type", "unknown")].append(a)
        gpu_summary["by_signal_type"] = {
            sig_type: {
                "count": len(items),
                "root_causes": list({
                    a.get("root_cause", "")[:150]
                    for a in items if a.get("root_cause")
                })[:5],
                "avg_confidence": round(
                    sum(a.get("confidence", 0) for a in items) / len(items), 3
                ) if items else 0,
            }
            for sig_type, items in sorted(
                by_type.items(), key=lambda kv: len(kv[1]), reverse=True
            )[:10]
        }
        gpu_summary["recent"] = [
            {
                "signal_type": a.get("signal_type", ""),
                "severity": a.get("severity", ""),
                "root_cause": a.get("root_cause", "")[:200],
                "confidence": a.get("confidence", 0),
                "model": a.get("model", ""),
                "timestamp": a.get("timestamp", ""),
            }
            for a in analyses[-5:]
        ]

    # ── 9. Health Score ──────────────────────────────────────────────
    health = _compute_health_score(
        memory_archive=memory_archive,
        causal_gaps=causal_gaps,
        causal_chains=causal_chains,
        absences=absences,
    )

    narrative = _generate_narrative(
        timeline, top_memories, patterns_learned, noise_profile,
        causal_gaps[:20], absences, gpu_summary, health,
    )

    return {
        "biography": {
            "timeline": timeline,
            "top_memories": top_memories,
            "patterns_learned": patterns_learned,
            "noise_profile": noise_profile,
            "causal_chains": causal_chains,
            "causal_gaps": causal_gaps[:20],
            "absences": absences,
            "gpu_analyses": gpu_summary,
            "health": health,
            "narrative": narrative,
        },
    }


def _compute_health_score(
    memory_archive,
    causal_gaps: List[Dict],
    causal_chains: List[Dict],
    absences: List[Dict],
) -> Dict[str, Any]:
    total_rules = len(causal_chains) if causal_chains else 0
    gap_count = len(causal_gaps)
    gap_ratio = gap_count / max(1, total_rules)

    absence_count = len(absences)

    avg_strength = 0.0
    strength_std = 0.0
    memory_count = 0
    if memory_archive and memory_archive.size > 0:
        stats = memory_archive.stats()
        avg_strength = stats.get("avg_strength", 0.0)
        memory_count = stats.get("size", 0)
        memories = memory_archive.query(limit=memory_archive.size or 1)
        if memories:
            strengths = [m.strength for m in memories]
            mean = sum(strengths) / len(strengths)
            variance = sum((s - mean) ** 2 for s in strengths) / len(strengths)
            strength_std = variance ** 0.5

    score = 100.0
    score -= min(30.0, gap_ratio * 30.0)
    score -= min(20.0, absence_count * 5.0)
    if memory_count > 0:
        strength_penalty = max(0.0, (0.3 - avg_strength)) * 66.7
        score -= min(20.0, strength_penalty)
    if memory_count == 0:
        score -= 10.0
    score -= min(20.0, strength_std * 20.0)
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "grade": (
            "excellent" if score >= 90 else
            "good" if score >= 75 else
            "fair" if score >= 50 else
            "poor"
        ),
        "factors": {
            "gap_ratio": round(gap_ratio, 3),
            "gap_count": gap_count,
            "total_causal_rules": total_rules,
            "absence_count": absence_count,
            "avg_memory_strength": round(avg_strength, 4),
            "strength_std": round(strength_std, 4),
            "memory_count": memory_count,
        },
    }


def _generate_narrative(timeline, top_memories, patterns_learned, noise_profile,
                        causal_gaps, absences, gpu_summary, health) -> Dict[str, Any]:
    chapters = []
    signals = timeline.get("signals_processed", 0)
    domain = timeline.get("domain", "unknown")
    agents = timeline.get("total_activations", 0)
    grade = health.get("grade", "unknown")
    score = health.get("score", 0)
    mem_count = health.get("factors", {}).get("memory_count", 0)

    opening = f"This {domain} cascade has processed {signals:,} signals"
    started = timeline.get("started_at", "")
    if started:
        opening += f" since {started[:10]}"
    opening += f". It discovered {agents} suppression agents and formed {mem_count} memories."
    opening += f" Platform health: {grade} ({score:.0f}/100)."

    ch1_lines = []
    if patterns_learned:
        ch1_lines.append(f"The cascade taught itself {len(patterns_learned)} suppression rules by observing the signal stream.")
        for p in patterns_learned[:5]:
            noise = p.get("noise_count", 0)
            imp = p.get("important_count", 0)
            ch1_lines.append(
                f"  {p['signal_type']}: {noise:,} noise / {imp:,} important — {p.get('action', 'suppressed')}."
            )
        if len(patterns_learned) > 5:
            ch1_lines.append(f"  ...and {len(patterns_learned) - 5} more patterns.")
    else:
        ch1_lines.append("No suppression patterns discovered yet. The cascade is still learning.")
    chapters.append({"title": "What It Learned", "text": "\n".join(ch1_lines)})

    ch2_lines = []
    if top_memories:
        all_max = all(m.get("strength", 0) >= 0.99 for m in top_memories[:10])
        if all_max:
            ch2_lines.append(f"Every top memory is at maximum strength. These are chronic conditions, not transient events.")
        else:
            ch2_lines.append(f"The strongest memories tell the story of what keeps happening.")

        type_counts = Counter(m["signal_type"] for m in top_memories)
        for sig_type, count in type_counts.most_common(5):
            sample = next(m for m in top_memories if m["signal_type"] == sig_type)
            strength = sample.get("strength", 0)
            msg = sample.get("message", "")
            root = sample.get("root_cause", "")
            line = f"  {sig_type}: {count} memories at strength {strength:.2f}"
            if msg:
                line += f" — \"{msg[:80]}\""
            if root:
                line += f" Root cause: {root[:80]}"
            ch2_lines.append(line)
    else:
        ch2_lines.append("No memories formed yet. The cascade needs more time to identify what matters.")
    chapters.append({"title": "What It Remembers", "text": "\n".join(ch2_lines)})

    ch3_lines = []
    baseline_count = noise_profile.get("baseline_types", 0)
    total_supp = noise_profile.get("total_suppression_decisions", 0)
    if total_supp > 0:
        ch3_lines.append(f"{total_supp:,} suppression decisions across {baseline_count} signal types. This is what the platform considers normal.")
        for n in noise_profile.get("top_noise", [])[:5]:
            ch3_lines.append(f"  {n['signal_type']}: {n.get('frequency', 0):,} occurrences (strength {n.get('strength', 0):.2f})")
        interp = noise_profile.get("interpretation", "")
        if interp:
            ch3_lines.append(interp)
    else:
        ch3_lines.append("No suppression baseline yet. The cascade hasn't processed enough signals to define normal.")
    chapters.append({"title": "What It Ignores", "text": "\n".join(ch3_lines)})

    ch4_lines = []
    if causal_gaps:
        ch4_lines.append(f"{len(causal_gaps)} causal gaps detected — effects observed without their expected upstream causes.")
        for g in causal_gaps[:5]:
            ch4_lines.append(f"  Expected {g.get('expected_cause', '?')} before {g.get('effect', '?')}")
            interp = g.get("interpretation", "")
            if interp:
                ch4_lines.append(f"    {interp}")
    if absences:
        ch4_lines.append(f"{len(absences)} expected signals are missing — monitoring blind spots.")
        for a in absences[:3]:
            overdue = a.get("hours_overdue")
            line = f"  {a['signal_type']}: expected every {a.get('expected_interval_hours', '?')}h"
            if overdue:
                line += f", overdue by {overdue:.1f}h"
            ch4_lines.append(line)
    if not causal_gaps and not absences:
        ch4_lines.append("No causal gaps or missing signals detected. Full observability coverage.")
    chapters.append({"title": "What's Missing", "text": "\n".join(ch4_lines)})

    ch5_lines = []
    gpu_count = gpu_summary.get("count", 0)
    if gpu_count > 0:
        ch5_lines.append(f"{gpu_count} deep analyses produced by the GPU macro tier.")
        for a in gpu_summary.get("recent", [])[:3]:
            rc = a.get("root_cause", "")
            conf = a.get("confidence", 0)
            ch5_lines.append(f"  {a.get('signal_type', '?')} ({a.get('severity', '?')}): {rc[:100]} [confidence: {conf:.0%}]")
    else:
        ch5_lines.append("No GPU analyses yet. Critical signals have not reached the macro tier.")
    chapters.append({"title": "What It Analyzed", "text": "\n".join(ch5_lines)})

    return {"opening": opening, "chapters": chapters}
