"""Run all classification arms over one frozen, independently adjudicated corpus."""

from __future__ import annotations

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from .classifier import (
    CASCADE_LABELS,
    CascadeClassifier,
    ClassificationResult,
    ComparisonRecorder,
)
from .evaluation import evaluate_classifiers, run_identity_errors
from .holdout import canonical_digest


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("run timestamps must include a timezone")
    return parsed


def _validate_corpus(corpus: Iterable[dict], holdout: dict,
                     adjudication: dict) -> List[dict]:
    if holdout.get("schema_version") != "cascade.holdout-manifest.v1alpha1":
        raise ValueError("unsupported holdout manifest")
    if adjudication.get("schema_version") != "cascade.adjudication-summary.v1alpha1":
        raise ValueError("unsupported adjudication summary")
    if adjudication.get("status") != "complete" or adjudication.get("unresolved") != 0:
        raise ValueError("adjudication is not complete")
    if int(adjudication.get("independent_reviewers") or 0) < 2:
        raise ValueError("adjudication requires two independent reviewers")

    rows = list(corpus)
    if not rows:
        raise ValueError("adjudicated corpus is empty")
    seen = set()
    identity = []
    for row in rows:
        case_id = str(row.get("case_id") or "")
        signal = row.get("signal")
        declared_digest = str(row.get("signal_sha256") or "")
        truth = row.get("ground_truth") or {}
        label = truth.get("classification")
        if not case_id or case_id in seen:
            raise ValueError("corpus case identifiers must be present and unique")
        if not isinstance(signal, dict) or not signal:
            raise ValueError("corpus case is missing signal evidence")
        if canonical_digest(signal) != declared_digest:
            raise ValueError("corpus signal digest mismatch")
        if label not in CASCADE_LABELS:
            raise ValueError("corpus case is missing adjudicated classification")
        seen.add(case_id)
        identity.append({"case_id": case_id, "signal_sha256": declared_digest})
    corpus_digest = canonical_digest(sorted(identity, key=lambda item: item["case_id"]))
    if corpus_digest != holdout.get("holdout_digest"):
        raise ValueError("corpus does not match frozen holdout")
    if corpus_digest != adjudication.get("corpus_digest"):
        raise ValueError("corpus does not match adjudication summary")
    if len(rows) != int(holdout.get("selection", {}).get("selected_records") or 0):
        raise ValueError("corpus size does not match holdout manifest")
    if len(rows) != int(adjudication.get("records") or 0):
        raise ValueError("corpus size does not match adjudication summary")
    return rows


def _prediction(result: ClassificationResult, *, authoritative: bool) -> dict:
    if not result.ok:
        return {}
    prediction = {
        "label": result.label,
        "authoritative": authoritative,
    }
    if result.confidence is not None:
        prediction["confidence"] = float(result.confidence)
    return prediction


def _revision_digest(values: Iterable[Any]) -> str:
    return canonical_digest(sorted(
        (json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values)
    ))


def run_classification_experiment(
    corpus: Iterable[dict],
    holdout: dict,
    adjudication: dict,
    *,
    generative: Any,
    semantic: Any,
    run: Dict[str, Any],
    hybrid_margin: float,
    hybrid_suppress_margin: float,
    workers: int = 1,
    client: Any = None,
) -> Dict[str, Any]:
    """Return private evaluator input with no signal payloads."""
    rows = _validate_corpus(corpus, holdout, adjudication)
    if semantic is None:
        raise ValueError("semantic backend is required for the three-arm experiment")
    if not getattr(generative, "structured_confidence", False):
        raise ValueError("structured generative confidence is required")
    if run_identity_errors(run):
        raise ValueError("run identity is incomplete or malformed")
    window_start = _parse_time(run["window_start"])
    window_end = _parse_time(run["window_end"])
    started_at = datetime.now(timezone.utc)
    if not window_start <= started_at <= window_end:
        raise ValueError("experiment start is outside the declared run window")
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")

    compare = CascadeClassifier(
        generative, semantic, mode="compare", hybrid_margin=hybrid_margin,
        hybrid_suppress_margin=hybrid_suppress_margin,
        recorder=ComparisonRecorder(),
    )
    hybrid = CascadeClassifier(
        generative, semantic, mode="hybrid", hybrid_margin=hybrid_margin,
        hybrid_suppress_margin=hybrid_suppress_margin,
        recorder=ComparisonRecorder(),
    )
    revisions = {"generative": set(), "semantic": set()}

    def observe(result: Any, observed: Dict[str, set]) -> None:
        if result is None:
            return
        if (result.backend == "generative" and result.model_revision
                and result.prompt_revision):
            observed["generative"].add((
                result.model_revision, result.prompt_revision,
            ))
        if (result.backend == "semantic" and result.classifier_id
                and result.model_revision and result.taxonomy_revision):
            observed["semantic"].add((
                result.classifier_id, result.model_revision,
                result.tokenizer_revision, result.taxonomy_revision,
            ))

    def classify(row: dict) -> tuple[dict, dict, float]:
        call_started = time.perf_counter()
        signal = row["signal"]
        compared = compare.classify(signal, client)
        hybrid_result = hybrid.classify(signal, client)
        gen = compared.alternatives.get("generative")
        sem = compared.alternatives.get("semantic")
        observed = {"generative": set(), "semantic": set()}
        for result in (
            gen, sem, hybrid_result, *hybrid_result.alternatives.values(),
        ):
            observe(result, observed)
        output = {
            "record_id": row["case_id"],
            "signal_sha256": row["signal_sha256"],
            "expected": row["ground_truth"]["classification"],
            "predictions": {
                "generative": _prediction(gen or ClassificationResult(), authoritative=True),
                "semantic": _prediction(sem or ClassificationResult(), authoritative=False),
                "hybrid": _prediction(hybrid_result, authoritative=True),
            },
        }
        return output, observed, (time.perf_counter() - call_started) * 1000

    classified = []
    latencies = []
    experiment_started = time.perf_counter()
    if workers == 1:
        for row in rows:
            output, observed, latency = classify(row)
            classified.append(output)
            latencies.append(latency)
            for arm, values in observed.items():
                revisions[arm].update(values)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(classify, row): row["case_id"] for row in rows}
            for future in as_completed(futures):
                output, observed, latency = future.result()
                classified.append(output)
                latencies.append(latency)
                for arm, values in observed.items():
                    revisions[arm].update(values)
    completed_at = datetime.now(timezone.utc)
    if completed_at > window_end:
        raise ValueError("experiment completed outside the declared run window")
    classified.sort(key=lambda item: item["record_id"])
    if any(not revisions[arm] for arm in ("generative", "semantic")):
        raise ValueError("revision metadata is missing for one or more classifier arms")
    observed_taxonomies = {item[3] for item in revisions["semantic"]}
    if observed_taxonomies != {run["taxonomy_revision"]}:
        raise ValueError("observed semantic taxonomy does not match declared revision")
    generative_revision = _revision_digest(revisions["generative"])
    semantic_revision = _revision_digest(revisions["semantic"])
    model_revisions = {
        "generative": generative_revision,
        "semantic": semantic_revision,
        "hybrid": canonical_digest({
            "policy": "cascade-hybrid-v1",
            "hybrid_margin": hybrid_margin,
            "hybrid_suppress_margin": hybrid_suppress_margin,
            "generative_revision": generative_revision,
            "semantic_revision": semantic_revision,
        }),
    }
    output_run = {**run, "model_revisions": model_revisions}
    return {
        "schema_version": "cascade.classification-input.v1alpha1",
        "dataset": {
            "name": holdout["dataset"]["name"],
            "revision": holdout["dataset"]["revision"],
            "holdout_digest": holdout["holdout_digest"],
            "adjudication": {
                "status": adjudication["status"],
                "method": (
                    "independent-double-review-with-resolution"
                    if int(adjudication.get("resolution_reviewers") or 0) > 0
                    else "independent-double-review"
                ),
                "independent": True,
                "reviewers": int(adjudication["independent_reviewers"]),
                "corpus_digest": adjudication["corpus_digest"],
            },
        },
        "run": output_run,
        "execution": {
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "workers": workers,
            "records": len(classified),
            "wall_seconds": round(time.perf_counter() - experiment_started, 6),
            "median_case_ms": round(statistics.median(latencies), 6) if latencies else None,
            "contains_raw_records": False,
        },
        "records": classified,
    }


def run_and_evaluate(*args: Any, **kwargs: Any) -> tuple[dict, dict]:
    private_input = run_classification_experiment(*args, **kwargs)
    return private_input, evaluate_classifiers(private_input)
