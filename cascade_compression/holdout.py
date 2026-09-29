"""Freeze a deterministic, stratified, model-blind classification holdout."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .classifier import CASCADE_LABELS

FORBIDDEN_REVIEW_KEY = re.compile(
    r"ground.?truth|classif|predict|model|decision|outcome|llm|review|route|verdict",
    re.IGNORECASE,
)


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _selection_score(seed: str, case_id: str, signal_digest: str) -> str:
    material = f"{seed}\0{case_id}\0{signal_digest}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _opaque_case_id(seed: str, case_id: str, signal_digest: str) -> str:
    material = f"opaque\0{seed}\0{case_id}\0{signal_digest}".encode("utf-8")
    return "case-" + hashlib.sha256(material).hexdigest()[:24]


def _forbidden_signal_keys(value: Any) -> set:
    found = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized == "expected" or FORBIDDEN_REVIEW_KEY.search(normalized):
                found.add(normalized)
            found.update(_forbidden_signal_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_forbidden_signal_keys(nested))
    return found


def _aware_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def freeze_stratified_holdout(
    candidates: Iterable[dict],
    quotas: Mapping[str, int],
    *,
    seed: str,
    dataset_name: str,
    dataset_revision: str,
    stratification_basis: str,
    source_window_start: str,
    source_window_end: str,
    frozen_at: Optional[str] = None,
    evaluation_purpose: str = "representative_prevalence",
    minimum_label_candidates: Optional[Mapping[str, int]] = None,
) -> Tuple[List[dict], dict]:
    """Select exact per-stratum quotas and return blinded records plus a manifest.

    ``sampling_stratum`` is used only for selection. It and every other candidate
    field except ``signal`` are absent from the reviewer corpus.
    """
    if not seed:
        raise ValueError("holdout seed must not be empty")
    if evaluation_purpose not in {
        "representative_prevalence", "label_coverage_challenge",
    }:
        raise ValueError("unsupported evaluation purpose")
    requested_label_candidates = {
        str(label): int(count)
        for label, count in (minimum_label_candidates or {}).items()
    }
    if (
        evaluation_purpose == "representative_prevalence"
        and requested_label_candidates
    ):
        raise ValueError("representative holdouts cannot declare label targets")
    if evaluation_purpose == "label_coverage_challenge":
        if set(requested_label_candidates) != set(CASCADE_LABELS):
            raise ValueError(
                "challenge holdouts require candidate targets for every label"
            )
        if any(count <= 0 for count in requested_label_candidates.values()):
            raise ValueError("challenge label candidate targets must be positive")
    if not dataset_name or not dataset_revision or not stratification_basis:
        raise ValueError("dataset identity and stratification basis are required")
    source_start = _aware_timestamp(source_window_start, "source_window_start")
    source_end = _aware_timestamp(source_window_end, "source_window_end")
    if source_end <= source_start:
        raise ValueError("source observation window must be positive")
    normalized_quotas = {str(key): int(value) for key, value in quotas.items()}
    if not normalized_quotas or any(value <= 0 for value in normalized_quotas.values()):
        raise ValueError("every holdout quota must be a positive integer")

    grouped: Dict[str, List[dict]] = defaultdict(list)
    seen_ids = set()
    challenge_signal_digests = set()
    candidate_identity = []
    for candidate in candidates:
        case_id = str(candidate.get("case_id") or "").strip()
        stratum = str(candidate.get("sampling_stratum") or "").strip()
        signal = candidate.get("signal")
        if not case_id or case_id in seen_ids:
            raise ValueError("candidate case_id values must be present and unique")
        if not stratum:
            raise ValueError(f"candidate {case_id} has no sampling_stratum")
        if not isinstance(signal, dict) or not signal:
            raise ValueError(f"candidate {case_id} has no signal object")
        forbidden = _forbidden_signal_keys(signal)
        if forbidden:
            raise ValueError(
                f"candidate {case_id} signal contains evaluation fields: "
                f"{sorted(forbidden)}"
            )
        seen_ids.add(case_id)
        signal_digest = canonical_digest(signal)
        coverage_target = candidate.get("coverage_target")
        source_record_ref = candidate.get("source_record_ref")
        if evaluation_purpose == "label_coverage_challenge":
            if coverage_target not in CASCADE_LABELS:
                raise ValueError(
                    f"challenge candidate {case_id} has no valid coverage_target"
                )
            if coverage_target == "known_pattern" and not (
                isinstance(source_record_ref, str) and source_record_ref.strip()
            ):
                raise ValueError(
                    f"known_pattern challenge candidate {case_id} requires "
                    "an authoritative source_record_ref"
                )
            if signal_digest in challenge_signal_digests:
                raise ValueError("challenge candidates must contain unique signals")
            challenge_signal_digests.add(signal_digest)
        item = {
            "source_case_id": case_id,
            "stratum": stratum,
            "signal": signal,
            "signal_sha256": signal_digest,
            "score": _selection_score(seed, case_id, signal_digest),
            "coverage_target": coverage_target,
            "source_record_prequalified": bool(
                isinstance(source_record_ref, str) and source_record_ref.strip()
            ),
            "source_record_ref": (
                source_record_ref.strip()
                if isinstance(source_record_ref, str) and source_record_ref.strip()
                else None
            ),
        }
        grouped[stratum].append(item)
        candidate_identity.append({
            "case_id": case_id,
            "sampling_stratum": stratum,
            "signal_sha256": signal_digest,
            "coverage_target": coverage_target,
            "source_record_ref_sha256": (
                canonical_digest(source_record_ref)
                if isinstance(source_record_ref, str) and source_record_ref.strip()
                else None
            ),
        })

    if not candidate_identity:
        raise ValueError("candidate corpus is empty")
    if set(grouped) != set(normalized_quotas):
        missing = sorted(set(grouped) - set(normalized_quotas))
        unknown = sorted(set(normalized_quotas) - set(grouped))
        raise ValueError(
            f"quotas must cover the exact candidate strata; missing={missing}, unknown={unknown}"
        )

    selected = []
    strata = {}
    for stratum_index, stratum in enumerate(sorted(grouped), start=1):
        available = grouped[stratum]
        requested = normalized_quotas[stratum]
        if len(available) < requested:
            raise ValueError(
                f"stratum {stratum!r} has {len(available)} candidates, needs {requested}"
            )
        chosen = sorted(
            available,
            key=lambda item: (item["score"], item["source_case_id"]),
        )[:requested]
        selected.extend(chosen)
        strata[f"stratum-{stratum_index:03d}"] = {
            "available": len(available),
            "requested": requested,
            "selected": len(chosen),
        }

    blinded = []
    for item in sorted(selected, key=lambda value: value["score"]):
        blinded_case = {
            "schema_version": "cascade.system-evaluation-case.v1alpha1",
            "case_id": _opaque_case_id(
                seed, item["source_case_id"], item["signal_sha256"],
            ),
            "signal": item["signal"],
            "signal_sha256": item["signal_sha256"],
        }
        if item["source_record_ref"]:
            blinded_case["evidence_refs"] = [item["source_record_ref"]]
        blinded.append(blinded_case)
    opaque_ids = [item["case_id"] for item in blinded]
    if len(opaque_ids) != len(set(opaque_ids)):
        raise ValueError("opaque case identifier collision")

    timestamp = frozen_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    frozen_time = _aware_timestamp(timestamp, "frozen_at")
    if frozen_time < source_end:
        raise ValueError("frozen_at cannot predate the source observation window end")
    selected_identity = sorted(
        ({"case_id": row["case_id"], "signal_sha256": row["signal_sha256"]}
         for row in blinded),
        key=lambda value: value["case_id"],
    )
    selected_candidate_targets = defaultdict(int)
    for item in selected:
        if item["coverage_target"] in CASCADE_LABELS:
            selected_candidate_targets[item["coverage_target"]] += 1
    if evaluation_purpose == "label_coverage_challenge":
        shortages = {
            label: {
                "selected": selected_candidate_targets[label],
                "required": required,
            }
            for label, required in requested_label_candidates.items()
            if selected_candidate_targets[label] < required
        }
        if shortages:
            raise ValueError(
                "selected challenge corpus misses candidate label targets: "
                + json.dumps(shortages, sort_keys=True)
            )
    manifest = {
        "schema_version": "cascade.holdout-manifest.v1alpha2",
        "dataset": {"name": dataset_name, "revision": dataset_revision},
        "source_window": {
            "start": source_window_start,
            "end": source_window_end,
        },
        "frozen_at": timestamp,
        "selection": {
            "algorithm": "sha256-rank-v1",
            "seed_digest": canonical_digest(seed),
            "stratification_basis": stratification_basis,
            "candidate_records": len(candidate_identity),
            "selected_records": len(blinded),
            "strata": strata,
        },
        "candidate_digest": canonical_digest(sorted(
            candidate_identity,
            key=lambda value: (value["case_id"], value["sampling_stratum"]),
        )),
        "holdout_digest": canonical_digest(selected_identity),
        "evaluation_design": {
            "purpose": evaluation_purpose,
            "prevalence_claim_permitted": (
                evaluation_purpose == "representative_prevalence"
            ),
            "candidate_targets_are_ground_truth": False,
            "minimum_label_candidates": dict(sorted(
                requested_label_candidates.items()
            )),
            "selected_candidate_targets": dict(sorted(
                selected_candidate_targets.items()
            )),
            "known_pattern_authority_prequalified": (
                evaluation_purpose == "label_coverage_challenge"
                and all(
                    item["source_record_prequalified"]
                    for item in selected
                    if item["coverage_target"] == "known_pattern"
                )
            ),
        },
        "blinding": {
            "evaluated_predictions_removed": True,
            "ground_truth_removed": True,
            "sampling_strata_removed": True,
            "candidate_case_ids_replaced": True,
        },
        "contains_raw_records": False,
    }
    return blinded, manifest
