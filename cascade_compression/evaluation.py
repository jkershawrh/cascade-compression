"""Aggregate, public-safe evaluation of adjudicated classification results.

The input may remain private.  The returned report contains metrics and a
digest of record identities plus truth labels, never signal payloads or record
identifiers.  Agreement between classifier arms is reported separately from
accuracy against adjudicated ground truth.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from itertools import combinations
from typing import Any, Dict, Iterable, List

from .anchor_engineering import IMPORTANT_LABELS, SUPPRESSIVE_LABELS
from .classifier import CASCADE_LABELS

ABSTAIN = "__abstain__"
REQUIRED_RUN_FIELDS = (
    "commit",
    "image_digest",
    "config_digest",
    "taxonomy_revision",
    "window_start",
    "window_end",
)


def _round(value: float) -> float:
    return round(value, 6)


def _dataset_digest(rows: Iterable[Dict[str, Any]]) -> str:
    identity = sorted(
        (
            {"record_id": row["record_id"], "expected": row["expected"]}
            for row in rows
        ),
        key=lambda item: (item["record_id"], item["expected"]),
    )
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _calibration(observations: List[tuple], classified: int,
                 bins: int = 10) -> dict:
    if not observations:
        return {
            "status": "not_measured", "samples": 0,
            "eligible_predictions": classified, "coverage": 0.0,
        }
    buckets = [[] for _ in range(bins)]
    for confidence, correct in observations:
        index = min(int(confidence * bins), bins - 1)
        buckets[index].append((confidence, correct))
    populated = []
    ece = 0.0
    total = len(observations)
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_confidence = sum(item[0] for item in bucket) / len(bucket)
        empirical_accuracy = sum(item[1] for item in bucket) / len(bucket)
        ece += len(bucket) / total * abs(mean_confidence - empirical_accuracy)
        populated.append({
            "lower": _round(index / bins),
            "upper": _round((index + 1) / bins),
            "samples": len(bucket),
            "mean_confidence": _round(mean_confidence),
            "empirical_accuracy": _round(empirical_accuracy),
        })
    brier = sum((confidence - correct) ** 2 for confidence, correct in observations)
    return {
        "status": "measured",
        "samples": total,
        "eligible_predictions": classified,
        "coverage": _round(total / classified) if classified else 0.0,
        "top_label_brier": _round(brier / total),
        "expected_calibration_error": _round(ece),
        "bins": populated,
    }


def _evaluate_arm(rows: List[dict], arm: str) -> dict:
    predicted_labels = sorted(CASCADE_LABELS) + [ABSTAIN]
    confusion = {
        truth: {prediction: 0 for prediction in predicted_labels}
        for truth in sorted(CASCADE_LABELS)
    }
    support = Counter()
    predicted_support = Counter()
    correct = Counter()
    calibration = []
    classified = 0
    dangerous_misses = 0
    raw_false_suppressions = 0
    authoritative_suppressions = 0
    correct_authoritative_suppressions = 0

    for row in rows:
        truth = row["expected"]
        support[truth] += 1
        prediction = row["predictions"].get(arm)
        if prediction is None or prediction.get("label") in (None, ""):
            confusion[truth][ABSTAIN] += 1
            continue
        label = str(prediction["label"])
        if label not in CASCADE_LABELS:
            raise ValueError(f"unknown prediction label for {arm}: {label}")
        confidence = prediction.get("confidence")
        if confidence is not None:
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(f"confidence for {arm} must be between 0 and 1")
            calibration.append((confidence, int(label == truth)))

        authoritative = bool(prediction.get("authoritative", False))
        classified += 1
        predicted_support[label] += 1
        confusion[truth][label] += 1
        correct[label] += int(label == truth)
        false_suppression = truth in IMPORTANT_LABELS and label in SUPPRESSIVE_LABELS
        raw_false_suppressions += int(false_suppression)
        dangerous_misses += int(authoritative and false_suppression)
        if authoritative and label in SUPPRESSIVE_LABELS:
            authoritative_suppressions += 1
            correct_authoritative_suppressions += int(truth in SUPPRESSIVE_LABELS)

    per_label = {}
    recalls = []
    f1_values = []
    for label in sorted(CASCADE_LABELS):
        true_positive = correct[label]
        recall = true_positive / support[label] if support[label] else None
        precision = (
            true_positive / predicted_support[label]
            if predicted_support[label]
            else None
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else 0.0 if precision is not None and recall is not None else None
        )
        if recall is not None:
            recalls.append(recall)
        if f1 is not None:
            f1_values.append(f1)
        per_label[label] = {
            "support": support[label],
            "predicted": predicted_support[label],
            "precision": _round(precision) if precision is not None else None,
            "recall": _round(recall) if recall is not None else None,
            "f1": _round(f1) if f1 is not None else None,
        }

    total = len(rows)
    important_support = sum(support[label] for label in IMPORTANT_LABELS)
    total_correct = sum(correct.values())
    return {
        "records": total,
        "classified": classified,
        "abstentions": total - classified,
        "coverage": _round(classified / total) if total else 0.0,
        "overall_accuracy": _round(total_correct / total) if total else 0.0,
        "classified_accuracy": (
            _round(total_correct / classified) if classified else None
        ),
        "balanced_accuracy": _round(sum(recalls) / len(recalls)) if recalls else None,
        "macro_f1": _round(sum(f1_values) / len(f1_values)) if f1_values else None,
        "important_support": important_support,
        "raw_false_suppressions": raw_false_suppressions,
        "authoritative_dangerous_misses": dangerous_misses,
        "authoritative_dangerous_miss_rate": (
            _round(dangerous_misses / important_support)
            if important_support else None
        ),
        "authoritative_suppressions": authoritative_suppressions,
        "authoritative_suppression_precision": (
            _round(correct_authoritative_suppressions / authoritative_suppressions)
            if authoritative_suppressions else None
        ),
        "per_label": per_label,
        "confusion": confusion,
        "calibration": _calibration(calibration, classified),
    }


def evaluate_classifiers(document: Dict[str, Any]) -> dict:
    """Evaluate all classifier arms on one frozen, adjudicated corpus."""
    if document.get("schema_version") != "cascade.classification-input.v1alpha1":
        raise ValueError("unsupported classification evaluation input version")
    rows = list(document.get("records") or [])
    if not rows:
        raise ValueError("classification evaluation requires records")

    seen = set()
    arms = set()
    for row in rows:
        record_id = str(row.get("record_id", "")).strip()
        truth = str(row.get("expected", ""))
        if not record_id:
            raise ValueError("every record requires a stable record_id")
        if record_id in seen:
            raise ValueError(f"duplicate record_id: {record_id}")
        if truth not in CASCADE_LABELS:
            raise ValueError(f"unknown expected label: {truth}")
        if not isinstance(row.get("predictions", {}), dict):
            raise ValueError("predictions must be an object keyed by arm")
        row["record_id"] = record_id
        row["expected"] = truth
        seen.add(record_id)
        arms.update(str(arm) for arm in row.get("predictions", {}))
    if not arms:
        raise ValueError("classification evaluation requires at least one arm")

    metrics = {arm: _evaluate_arm(rows, arm) for arm in sorted(arms)}
    agreements = {}
    for left, right in combinations(sorted(arms), 2):
        compared = 0
        agreed = 0
        for row in rows:
            left_prediction = row["predictions"].get(left, {}).get("label")
            right_prediction = row["predictions"].get(right, {}).get("label")
            if left_prediction in CASCADE_LABELS and right_prediction in CASCADE_LABELS:
                compared += 1
                agreed += int(left_prediction == right_prediction)
        agreements[f"{left}__{right}"] = {
            "compared": compared,
            "agreements": agreed,
            "agreement_rate": _round(agreed / compared) if compared else None,
            "is_accuracy": False,
        }

    dataset = document.get("dataset") or {}
    adjudication = dataset.get("adjudication") or {}
    run = document.get("run") or {}
    missing_run_fields = [field for field in REQUIRED_RUN_FIELDS if not run.get(field)]
    decision_grade = (
        adjudication.get("status") == "complete"
        and adjudication.get("independent") is True
        and int(adjudication.get("reviewers") or 0) >= 2
        and not missing_run_fields
    )
    return {
        "schema_version": "cascade.classification-evaluation.v1alpha1",
        "dataset": {
            "name": str(dataset.get("name") or "unnamed"),
            "revision": str(dataset.get("revision") or "unversioned"),
            "digest": _dataset_digest(rows),
            "records": len(rows),
            "adjudication": {
                "status": str(adjudication.get("status") or "unknown"),
                "method": str(adjudication.get("method") or "unspecified"),
                "independent": adjudication.get("independent") is True,
                "reviewers": int(adjudication.get("reviewers") or 0),
            },
        },
        "run": {field: run.get(field) for field in REQUIRED_RUN_FIELDS},
        "model_revisions": dict(run.get("model_revisions") or {}),
        "arms": metrics,
        "pairwise_agreement": agreements,
        "evidence": {
            "status": "decision_grade" if decision_grade else "mechanics_only",
            "ground_truth_required": True,
            "same_corpus": True,
            "missing_run_fields": missing_run_fields,
            "contains_raw_records": False,
            "limitations": [] if decision_grade else [
                "Decision-grade status requires complete independent adjudication by at least two reviewers and frozen run metadata."
            ],
        },
    }
