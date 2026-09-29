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
RECEIPT_FIELDS = frozenset({
    "schema_version", "case_id", "signal_sha256", "actionability",
    "classification", "expected_memory", "source", "source_record_ref",
    "rationale", "reviewer_ref", "reviewed_at",
    "independent_of_evaluated_arms", "evidence_ref",
})


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


def _parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} timestamp must include a timezone")
    return parsed


def _validate_receipt(receipt: dict) -> datetime:
    if not isinstance(receipt, dict):
        raise ValueError("review receipt must be an object")
    if receipt.get("schema_version") != "cascade.review-receipt.v1alpha1":
        raise ValueError("review receipt has an unsupported schema version")
    missing = RECEIPT_FIELDS - set(receipt)
    unexpected = set(receipt) - RECEIPT_FIELDS
    if missing:
        raise ValueError(
            "review receipt is missing required fields: "
            + ", ".join(sorted(missing))
        )
    if unexpected:
        raise ValueError(
            "review receipt contains undeclared fields: "
            + ", ".join(sorted(unexpected))
        )
    if not isinstance(receipt["case_id"], str) or not receipt["case_id"].strip():
        raise ValueError("review receipt requires a string case_id")
    if (
        not isinstance(receipt["reviewer_ref"], str)
        or not receipt["reviewer_ref"].strip()
    ):
        raise ValueError("review receipt requires a string reviewer_ref")
    if not isinstance(receipt["reviewed_at"], str):
        raise ValueError("review receipt requires a string reviewed_at")
    if not isinstance(receipt["rationale"], str):
        raise ValueError("review receipt requires a string rationale")
    if receipt["source_record_ref"] is not None and not isinstance(
        receipt["source_record_ref"], str
    ):
        raise ValueError("review receipt source_record_ref must be a string or null")
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
    reviewed_at = _parse_time(receipt["reviewed_at"], "review receipt")
    if receipt.get("source") not in {
        "independent_human_review", "authoritative_record",
    }:
        raise ValueError("review receipt has an invalid truth source")
    if receipt.get("source") == "authoritative_record" and not receipt.get(
        "source_record_ref"
    ):
        raise ValueError("authoritative review requires a source record reference")
    if classification == "known_pattern" and receipt.get(
        "source"
    ) != "authoritative_record":
        raise ValueError("known_pattern requires an authoritative source record")
    if not receipt["rationale"].strip():
        raise ValueError("review receipt requires a rationale")
    signal_digest = str(receipt.get("signal_sha256") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", signal_digest):
        raise ValueError("review receipt is missing a signal digest")
    evidence_ref = str(receipt.get("evidence_ref") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence_ref):
        raise ValueError("review receipt is missing an evidence digest")
    evidence = {
        key: value for key, value in receipt.items() if key != "evidence_ref"
    }
    if _digest(evidence) != evidence_ref:
        raise ValueError("review receipt evidence digest does not match its contents")
    return reviewed_at


def merge_independent_reviews(
    corpus: List[dict],
    first_reviews: List[dict],
    second_reviews: List[dict],
    resolution_reviews: Optional[List[dict]] = None,
    *,
    holdout_manifest: dict,
) -> Tuple[List[dict], dict, List[dict]]:
    """Return adjudicated corpus, sanitized summary, and unresolved private cases."""
    if holdout_manifest.get("schema_version") != "cascade.holdout-manifest.v1alpha1":
        raise ValueError("unsupported holdout manifest")
    frozen_at = _parse_time(holdout_manifest.get("frozen_at"), "holdout frozen_at")
    cases = _index(corpus, "corpus")
    review_times = [
        _validate_receipt(receipt)
        for receipt in [*first_reviews, *second_reviews]
    ]
    first = _index(first_reviews, "first review")
    second = _index(second_reviews, "second review")
    if not cases:
        raise ValueError("corpus is empty")
    if set(first) != set(cases) or set(second) != set(cases):
        raise ValueError("each independent review must cover the exact corpus")

    first_reviewers = {item["reviewer_ref"] for item in first.values()}
    second_reviewers = {item["reviewer_ref"] for item in second.values()}
    if len(first_reviewers) != 1 or len(second_reviewers) != 1:
        raise ValueError("each review file must represent exactly one reviewer")
    if first_reviewers == second_reviewers:
        raise ValueError("independent review files must use different reviewers")

    raw_resolutions = resolution_reviews or []
    review_times.extend(_validate_receipt(receipt) for receipt in raw_resolutions)
    resolutions = _index(raw_resolutions, "resolution review")
    if not set(resolutions) <= set(cases):
        raise ValueError("resolution review contains a case outside the corpus")
    if any(reviewed_at < frozen_at for reviewed_at in review_times):
        raise ValueError("review receipt predates the frozen holdout")
    resolution_reviewers = {
        item["reviewer_ref"] for item in resolutions.values()
    }
    if len(resolution_reviewers) > 1:
        raise ValueError("all disagreement resolutions must use one third reviewer")
    if resolution_reviewers & (first_reviewers | second_reviewers):
        raise ValueError("resolution reviewer must be independent of both reviewers")
    disagreement_ids = {
        case_id for case_id in cases
        if any(
            first[case_id][field] != second[case_id][field]
            for field in REVIEW_FIELDS
        )
    }
    if not set(resolutions) <= disagreement_ids:
        raise ValueError("resolution review may cover only disputed cases")

    merged = []
    unresolved = []
    disagreements = 0
    label_counts = Counter()
    review_evidence = []
    for case_id, case in cases.items():
        left, right = first[case_id], second[case_id]
        review_evidence.append({
            "case_id": case_id,
            "first": left["evidence_ref"],
            "second": right["evidence_ref"],
            "resolution": (
                resolutions[case_id]["evidence_ref"]
                if case_id in resolutions else None
            ),
        })
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

    corpus_digest = _digest([
        {"case_id": case_id, "signal_sha256": first[case_id]["signal_sha256"]}
        for case_id in sorted(cases)
    ])
    if corpus_digest != holdout_manifest.get("holdout_digest"):
        raise ValueError("adjudication corpus does not match the holdout manifest")
    if len(cases) != int(
        (holdout_manifest.get("selection") or {}).get("selected_records") or 0
    ):
        raise ValueError("adjudication corpus size does not match the holdout manifest")
    summary = {
        "schema_version": "cascade.adjudication-summary.v1alpha3",
        "status": "complete" if not unresolved else "incomplete",
        "corpus_digest": corpus_digest,
        "holdout_manifest_digest": _digest(holdout_manifest),
        "review_evidence_digest": _digest(sorted(
            review_evidence, key=lambda item: item["case_id"],
        )),
        "review_window": {
            "started_at": min(review_times).isoformat(),
            "completed_at": max(review_times).isoformat(),
        },
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
