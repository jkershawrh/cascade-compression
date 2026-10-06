from copy import deepcopy

import pytest

from cascade_compression.benchmarks.compare_semantic_runtime import compare_runtime


def artifact(source_revision="runtime-old"):
    return {
        "schema_version": 1,
        "semantic_contract": {
            "runtime_source_revision": source_revision,
            "observed": {
                "classifier_id": "cascade-classification",
                "model_revision": "model-sha",
                "tokenizer_revision": "tokenizer-sha",
                "taxonomy_revision": "taxonomy-v1",
                "scoring_mode": "anchor_cosine",
            },
        },
        "results": {"semantic": [
            {
                "workload": "normalized_cache_hit",
                "concurrency": 1,
                "errors": 0,
                "success_rate": 1.0,
                "latency_ms": {"p95": 10.0},
                "throughput_signals_per_second": 100.0,
            },
            {
                "workload": "unique_cache_miss",
                "concurrency": 8,
                "errors": 0,
                "success_rate": 1.0,
                "latency_ms": {"p95": 100.0},
                "throughput_signals_per_second": 80.0,
            },
        ]},
    }


def test_runtime_comparison_passes_materially_equivalent_candidate():
    baseline = artifact()
    candidate = artifact("runtime-v02")
    candidate["results"]["semantic"][0]["latency_ms"]["p95"] = 10.5
    candidate["results"]["semantic"][0]["throughput_signals_per_second"] = 98.0

    report = compare_runtime(baseline, candidate)

    assert report["status"] == "pass"
    assert not report["failures"]


def test_runtime_comparison_rejects_regressions():
    baseline = artifact()
    candidate = artifact("runtime-v02")
    cell = candidate["results"]["semantic"][1]
    cell["latency_ms"]["p95"] = 125.0
    cell["throughput_signals_per_second"] = 65.0
    cell["success_rate"] = 0.99
    cell["errors"] = 1

    report = compare_runtime(baseline, candidate)

    assert report["status"] == "fail"
    assert len(report["failures"]) == 4


def test_runtime_comparison_rejects_model_or_taxonomy_change():
    baseline = artifact()
    candidate = artifact("runtime-v02")
    candidate["semantic_contract"]["observed"]["model_revision"] = "other-model"

    with pytest.raises(ValueError, match="artifact identity"):
        compare_runtime(baseline, candidate)


def test_runtime_comparison_requires_matching_cells():
    baseline = artifact()
    candidate = deepcopy(artifact("runtime-v02"))
    candidate["results"]["semantic"].pop()

    with pytest.raises(ValueError, match="cells do not match"):
        compare_runtime(baseline, candidate)
