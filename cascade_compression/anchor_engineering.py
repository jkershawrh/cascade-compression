"""Public-safe anchor engineering for the llm-d-sc taxonomy.

The module turns comparison summaries and held-out labels into a reviewable
candidate taxonomy. It deliberately has no deployment function: approval and
activation are separate operator actions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .classifier import CASCADE_LABELS

SUPPRESSIVE_LABELS = frozenset({"routine_noise", "known_pattern"})
IMPORTANT_LABELS = frozenset({"needs_attention", "real_incident"})
UNSAFE_SUPPRESSIVE_PHRASES = (
    "active incident",
    "data loss",
    "degraded service",
    "fatal error",
    "security breach",
    "service interruption",
    "service outage",
    "terminated unexpectedly",
)


def validate_taxonomy(taxonomy: Dict[str, Any]) -> None:
    """Raise ``ValueError`` when a taxonomy cannot be safely evaluated."""
    labels = taxonomy.get("labels", [])
    anchors = taxonomy.get("anchors", {})
    if set(labels) != CASCADE_LABELS or len(labels) != len(CASCADE_LABELS):
        raise ValueError("taxonomy labels must exactly match Cascade labels")
    if set(anchors) != CASCADE_LABELS:
        raise ValueError("anchor groups must exactly match taxonomy labels")

    seen: Dict[str, str] = {}
    minimum = None
    for label in labels:
        values = anchors[label]
        if not isinstance(values, list) or not values:
            raise ValueError(f"{label} must contain at least one anchor")
        minimum = len(values) if minimum is None else min(minimum, len(values))
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} contains an empty anchor")
            normalized = value.strip().casefold()
            previous = seen.get(normalized)
            if previous:
                raise ValueError(
                    f"anchor appears in both {previous} and {label}: {value}"
                )
            seen[normalized] = label

    top_k = int(taxonomy.get("top_k", 1))
    if top_k < 1 or minimum is None or top_k > minimum:
        raise ValueError("top_k must be between 1 and the smallest anchor group")


def collect_gap_summary(comparisons: Iterable[Dict[str, Any]]) -> List[dict]:
    """Aggregate fallback comparisons without retaining signal payloads.

    Agreement is useful for finding proposal opportunities, but it is not
    treated as ground-truth accuracy.
    """
    grouped: Dict[str, list] = defaultdict(list)
    for comparison in comparisons:
        if comparison.get("fallback_used"):
            grouped[str(comparison.get("signal_type") or "unknown")].append(
                comparison
            )

    gaps = []
    for signal_type, rows in sorted(grouped.items(), key=lambda item: -len(item[1])):
        semantic_labels = Counter(
            (row.get("semantic") or {}).get("label", "unknown") for row in rows
        )
        generative_labels = Counter(
            (row.get("generative") or {}).get("label", "unknown") for row in rows
        )
        margins = [
            float((row.get("semantic") or {}).get("margin") or 0.0)
            for row in rows
        ]
        agreements = sum(
            (row.get("semantic") or {}).get("label")
            == (row.get("generative") or {}).get("label")
            for row in rows
        )
        gaps.append({
            "signal_type": signal_type,
            "count": len(rows),
            "agreement_rate": round(agreements / len(rows), 4),
            "mean_margin": round(sum(margins) / len(margins), 4),
            "semantic_label_distribution": dict(semantic_labels.most_common()),
            "generative_label_distribution": dict(generative_labels.most_common()),
        })
    return gaps


def validate_anchor_proposals(
    proposals: Dict[str, List[str]],
    gaps: Iterable[Dict[str, Any]],
    *,
    current_taxonomy: Optional[Dict[str, Any]] = None,
    min_agreement: float = 0.80,
) -> Tuple[Dict[str, List[str]], List[dict]]:
    """Fail closed on unsupported, duplicate, or unsafe proposals.

    This is a proposal gate, not an activation gate. Held-out evaluation and
    explicit approval are still required.
    """
    eligible_labels = set()
    for gap in gaps:
        if float(gap.get("agreement_rate", 0.0)) < min_agreement:
            continue
        distribution = (
            gap.get("generative_label_distribution")
            or gap.get("llm_label_distribution")
            or {}
        )
        if distribution:
            label = max(distribution, key=distribution.get)
            if label in CASCADE_LABELS:
                eligible_labels.add(label)

    existing = set()
    if current_taxonomy:
        validate_taxonomy(current_taxonomy)
        existing = {
            anchor.strip().casefold()
            for values in current_taxonomy["anchors"].values()
            for anchor in values
        }

    accepted = {label: [] for label in sorted(CASCADE_LABELS)}
    rejected = []
    proposed_seen = set()
    for label, anchors in proposals.items():
        if label not in CASCADE_LABELS:
            rejected.append({"label": label, "anchor": "", "reason": "unknown_label"})
            continue
        if not isinstance(anchors, list):
            rejected.append({"label": label, "anchor": "", "reason": "anchors_must_be_a_list"})
            continue
        for raw_anchor in anchors:
            anchor = str(raw_anchor).strip()
            normalized = anchor.casefold()
            reason = ""
            if label not in eligible_labels:
                reason = "no_high_agreement_proposal_evidence"
            elif len(anchor) < 12 or len(anchor) > 240:
                reason = "anchor_length_out_of_bounds"
            elif normalized in existing or normalized in proposed_seen:
                reason = "duplicate_anchor"
            elif label in SUPPRESSIVE_LABELS and any(
                phrase in normalized for phrase in UNSAFE_SUPPRESSIVE_PHRASES
            ):
                reason = "incident_language_in_suppressive_anchor"

            if reason:
                rejected.append({"label": label, "anchor": anchor, "reason": reason})
            else:
                accepted[label].append(anchor)
                proposed_seen.add(normalized)
    return accepted, rejected


def build_candidate_taxonomy(
    current: Dict[str, Any],
    accepted: Dict[str, List[str]],
    *,
    created_at: Optional[str] = None,
    max_per_label: int = 20,
) -> dict:
    """Create a versioned candidate. The result is never active by itself."""
    validate_taxonomy(current)
    candidate = deepcopy(current)
    additions = {}
    for label in candidate["labels"]:
        room = max(0, max_per_label - len(candidate["anchors"][label]))
        values = list(accepted.get(label, []))[:room]
        candidate["anchors"][label].extend(values)
        additions[label] = values

    canonical = json.dumps(
        {"parent": current.get("taxonomy_revision", ""), "additions": additions},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    old_revision = str(current.get("taxonomy_revision", "unversioned"))
    version_match = re.search(r"-v(\d+)(?:-|$)", old_revision)
    next_version = int(version_match.group(1)) + 1 if version_match else 1
    candidate["taxonomy_revision"] = (
        f"cascade-classification-anchors-v{next_version}-candidate-{digest}"
    )
    candidate["top_k"] = min(
        int(candidate.get("top_k", 1)),
        min(len(values) for values in candidate["anchors"].values()),
    )
    candidate["lifecycle"] = {
        "status": "candidate",
        "parent_revision": old_revision,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "proposal_digest": digest,
        "additions": additions,
        "requires_held_out_evaluation": True,
        "requires_explicit_approval": True,
    }
    candidate["provenance"] = (
        f"Candidate {digest} derived from {old_revision}; not approved for activation."
    )
    validate_taxonomy(candidate)
    return candidate


def evaluate_holdout(
    rows: Iterable[Dict[str, Any]],
    *,
    normal_margin: float = 0.20,
    suppress_margin: float = 0.40,
) -> dict:
    """Evaluate predictions against adjudicated held-out labels."""
    materialized = list(rows)
    confusion = {
        expected: {predicted: 0 for predicted in sorted(CASCADE_LABELS)}
        for expected in sorted(CASCADE_LABELS)
    }
    support = Counter()
    correct = Counter()
    authoritative = 0
    false_suppressions = 0
    valid = 0
    holdout_identity = []
    seen_record_ids = set()
    for row in materialized:
        record_id = str(row.get("record_id", "")).strip()
        expected = str(row.get("expected", ""))
        predicted = str(row.get("predicted", ""))
        if not record_id:
            raise ValueError("every holdout row requires a stable record_id")
        if record_id in seen_record_ids:
            raise ValueError(f"duplicate holdout record_id: {record_id}")
        seen_record_ids.add(record_id)
        if expected not in CASCADE_LABELS or predicted not in CASCADE_LABELS:
            continue
        holdout_identity.append({"record_id": record_id, "expected": expected})
        valid += 1
        margin = float(row.get("margin") or 0.0)
        threshold = suppress_margin if predicted in SUPPRESSIVE_LABELS else normal_margin
        has_authority = margin >= threshold
        authoritative += int(has_authority)
        confusion[expected][predicted] += 1
        support[expected] += 1
        correct[expected] += int(expected == predicted)
        false_suppressions += int(
            has_authority
            and expected in IMPORTANT_LABELS
            and predicted in SUPPRESSIVE_LABELS
        )

    holdout_digest = hashlib.sha256(json.dumps(
        sorted(holdout_identity, key=lambda item: item["record_id"]),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "records": valid,
        "holdout_digest": holdout_digest,
        "accuracy": round(sum(correct.values()) / valid, 4) if valid else 0.0,
        "authority_coverage": round(authoritative / valid, 4) if valid else 0.0,
        "authoritative_false_suppressions": false_suppressions,
        "per_label_recall": {
            label: round(correct[label] / support[label], 4) if support[label] else None
            for label in sorted(CASCADE_LABELS)
        },
        "support": dict(support),
        "confusion": confusion,
        "ground_truth_required": True,
    }


def compare_evaluations(current: dict, candidate: dict) -> dict:
    """Apply conservative activation gates to two held-out evaluations."""
    if not current.get("records") or current.get("records") != candidate.get("records"):
        raise ValueError("current and candidate must evaluate the same non-empty holdout")
    if (
        not current.get("holdout_digest")
        or current.get("holdout_digest") != candidate.get("holdout_digest")
    ):
        raise ValueError("current and candidate must use the same holdout records")
    recall_regressions = []
    for label in sorted(CASCADE_LABELS):
        old = current["per_label_recall"].get(label)
        new = candidate["per_label_recall"].get(label)
        if old is not None and (new is None or new < old):
            recall_regressions.append(label)

    gates = {
        "no_accuracy_regression": candidate["accuracy"] >= current["accuracy"],
        "no_coverage_regression": (
            candidate["authority_coverage"] >= current["authority_coverage"]
        ),
        "zero_authoritative_false_suppressions": (
            candidate["authoritative_false_suppressions"] == 0
        ),
        "no_per_label_recall_regression": not recall_regressions,
    }
    return {
        "eligible_for_approval": all(gates.values()),
        "gates": gates,
        "recall_regressions": recall_regressions,
        "accuracy_delta": round(candidate["accuracy"] - current["accuracy"], 4),
        "coverage_delta": round(
            candidate["authority_coverage"] - current["authority_coverage"], 4
        ),
    }


def approve_candidate(
    candidate: Dict[str, Any],
    comparison: Dict[str, Any],
    *,
    approved_by: str,
    approved_at: Optional[str] = None,
) -> dict:
    """Mark an eligible candidate approved without deploying it."""
    validate_taxonomy(candidate)
    lifecycle = candidate.get("lifecycle", {})
    if lifecycle.get("status") != "candidate":
        raise ValueError("only a candidate taxonomy can be approved")
    if not comparison.get("eligible_for_approval"):
        raise ValueError("candidate did not pass held-out evaluation gates")
    if not approved_by.strip():
        raise ValueError("approved_by is required")

    approved = deepcopy(candidate)
    approved["taxonomy_revision"] = approved["taxonomy_revision"].replace(
        "-candidate-", "-approved-", 1
    )
    approved["lifecycle"].update({
        "status": "approved",
        "approved_by": approved_by.strip(),
        "approved_at": approved_at or datetime.now(timezone.utc).isoformat(),
        "evaluation": comparison,
        "activation_required": True,
    })
    approved["provenance"] = (
        f"Approved candidate derived from {lifecycle['parent_revision']}; "
        "activation remains a separate operator action."
    )
    validate_taxonomy(approved)
    return approved


def rollback_revision(taxonomy: Dict[str, Any]) -> str:
    """Return the immutable parent revision an operator should restore."""
    parent = str(taxonomy.get("lifecycle", {}).get("parent_revision", ""))
    if not parent:
        raise ValueError("taxonomy does not declare a parent revision")
    return parent
