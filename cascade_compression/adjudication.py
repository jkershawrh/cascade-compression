"""Validate and merge independent blind-review receipts without publishing records."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .classifier import CASCADE_LABELS

IMPORTANT = frozenset({"needs_attention", "real_incident"})
REVIEW_FIELDS = ("actionability", "classification", "expected_memory")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _index(records: Iterable[dict], label: str) -> Dict[str, dict]:
    indexed = {}
    for record in records:
        case_id = str(record.get("case_id") or "")
        if not case_id:
            raise ValueError(f"{label} contains a record without case_id")
        if case_id in indexed:
            raise ValueError(f"{label} contains duplicate case_id")
        indexed[case_id] = record
    return indexed


def _validate_receipt(receipt: dict) -> None:
    classification = receipt.get("classification")
    actionability = receipt.get("actionability")
    if classification not in CASCADE_LABELS:
        raise ValueError("review receipt contains an invalid classification")
    if actionability not in {"actionable", "suppressible"}:
        raise ValueError("review receipt contains invalid actionability")
    if (classification in IMPORTANT) != (actionability == "actionable"):
        raise ValueError("review classification conflicts with actionability")
    if type(receipt.get("expected_memory")) is not bool:
        raise ValueError("review receipt requires boolean expected_memory")
    if receipt.get("independent_of_evaluated_arms") is not True:
        raise ValueError("review receipt is not independent of evaluated arms")
    if not receipt.get("reviewer_ref") or not receipt.get("reviewed_at"):
        raise ValueError("review receipt is missing reviewer or timestamp")
    try:
        datetime.fromisoformat(str(receipt["reviewed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("review receipt has an invalid timestamp") from exc
    if receipt.get("source") not in {
        "independent_human_review", "authoritative_record",
    }:
        raise ValueError("review receipt has an invalid truth source")
    if receipt.get("source") == "authoritative_record" and not receipt.get(
        "source_record_ref"
    ):
        raise ValueError("authoritative review requires a source record reference")
    if not str(receipt.get("rationale") or "").strip():
        raise ValueError("review receipt requires a rationale")
    signal_digest = str(receipt.get("signal_sha256") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", signal_digest):
        raise ValueError("review receipt is missing a signal digest")
    if not re.fullmatch(
        r"sha256:[0-9a-f]{64}", str(receipt.get("evidence_ref") or "")
    ):
        raise ValueError("review receipt is missing an evidence digest")


def merge_independent_reviews(
    corpus: List[dict],
    first_reviews: List[dict],
    second_reviews: List[dict],
    resolution_reviews: Optional[List[dict]] = None,
) -> Tuple[List[dict], dict, List[dict]]:
    """Return adjudicated corpus, sanitized summary, and unresolved private cases."""
    cases = _index(corpus, "corpus")
    first = _index(first_reviews, "first review")
    second = _index(second_reviews, "second review")
    if not cases:
        raise ValueError("corpus is empty")
    if set(first) != set(cases) or set(second) != set(cases):
        raise ValueError("each independent review must cover the exact corpus")

    for receipt in [*first.values(), *second.values()]:
        _validate_receipt(receipt)
    first_reviewers = {item["reviewer_ref"] for item in first.values()}
    second_reviewers = {item["reviewer_ref"] for item in second.values()}
    if len(first_reviewers) != 1 or len(second_reviewers) != 1:
        raise ValueError("each review file must represent exactly one reviewer")
    if first_reviewers == second_reviewers:
        raise ValueError("independent review files must use different reviewers")

    resolutions = _index(resolution_reviews or [], "resolution review")
    if not set(resolutions) <= set(cases):
        raise ValueError("resolution review contains a case outside the corpus")
    for receipt in resolutions.values():
        _validate_receipt(receipt)
    resolution_reviewers = {
        item["reviewer_ref"] for item in resolutions.values()
    }
    if resolution_reviewers & (first_reviewers | second_reviewers):
        raise ValueError("resolution reviewer must be independent of both reviewers")

    merged = []
    unresolved = []
    disagreements = 0
    label_counts = Counter()
    for case_id, case in cases.items():
        left, right = first[case_id], second[case_id]
        signal = case.get("signal")
        if not isinstance(signal, dict) or not signal:
            raise ValueError("corpus case is missing its signal evidence")
        corpus_signal_digest = _digest(signal)
        declared_digest = case.get("signal_sha256")
        if declared_digest and declared_digest != corpus_signal_digest:
            raise ValueError("corpus signal digest does not match its signal evidence")
        if left["signal_sha256"] != corpus_signal_digest:
            raise ValueError("review receipt does not match the frozen signal evidence")
        if left["signal_sha256"] != right["signal_sha256"]:
            raise ValueError("review files refer to different signal evidence")
        agreed = all(left[field] == right[field] for field in REVIEW_FIELDS)
        if agreed:
            chosen = left
            method = "double_review_consensus"
            evidence_refs = [left.get("evidence_ref"), right.get("evidence_ref")]
        else:
            disagreements += 1
            chosen = resolutions.get(case_id)
            if chosen is None:
                unresolved.append(case)
                continue
            if chosen["signal_sha256"] != left["signal_sha256"]:
                raise ValueError("resolution refers to different signal evidence")
            method = "independent_resolution"
            evidence_refs = [
                left.get("evidence_ref"), right.get("evidence_ref"),
                chosen.get("evidence_ref"),
            ]
        label = chosen["classification"]
        label_counts[label] += 1
        merged.append({
            **case,
            "ground_truth": {
                "actionability": chosen["actionability"],
                "classification": label,
                "expected_memory": chosen["expected_memory"],
                "provenance": {
                    "method": method,
                    "independent": True,
                    "reviewers": 2 if agreed else 3,
                    "evidence_refs": [ref for ref in evidence_refs if ref],
                },
            },
        })

    summary = {
        "schema_version": "cascade.adjudication-summary.v1alpha1",
        "status": "complete" if not unresolved else "incomplete",
        "corpus_digest": _digest([
            {"case_id": case_id, "signal_sha256": first[case_id]["signal_sha256"]}
            for case_id in sorted(cases)
        ]),
        "records": len(cases),
        "adjudicated": len(merged),
        "disagreements": disagreements,
        "unresolved": len(unresolved),
        "labels": dict(sorted(label_counts.items())),
        "independent_reviewers": 2,
        "resolution_reviewers": len(resolution_reviewers),
        "contains_raw_records": False,
    }
    return merged, summary, unresolved
