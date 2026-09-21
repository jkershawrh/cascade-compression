from __future__ import annotations

from argparse import Namespace

from cascade_compression.benchmarks.cascade_runtime import (
    _summary,
    benchmark_nano,
    run,
)


def test_summary_reports_tail_latency_and_per_core_throughput():
    result = _summary([1.0, 2.0, 3.0, 4.0], 100, 2.0, 2.0)

    assert result["latency_ms"]["p50"] == 2.5
    assert result["latency_ms"]["p95"] == 3.85
    assert result["throughput_signals_per_second"] == 50.0
    assert result["throughput_signals_per_second_per_core"] == 25.0
    assert result["attempted_signals_per_second"] == 50.0


def test_summary_excludes_errors_from_successful_throughput():
    result = _summary([1.0] * 10, 10, 2.0, 1.0, errors=4)

    assert result["successful_signals"] == 6
    assert result["throughput_signals_per_second"] == 3.0
    assert result["attempted_signals_per_second"] == 5.0


def test_summary_does_not_invent_per_core_throughput_without_cpu_allocation():
    result = _summary([1.0], 10, 2.0, None)
    assert result["throughput_signals_per_second"] == 5.0
    assert result["throughput_signals_per_second_per_core"] is None


def test_nano_benchmark_records_each_batch_size():
    cells = benchmark_nano(iterations=3, warmup=1, batch_sizes=[1, 4], cpu_limit=1.0)

    assert [cell["batch_size"] for cell in cells] == [1, 4]
    assert [cell["signals"] for cell in cells] == [3, 12]
    assert all(cell["errors"] == 0 for cell in cells)
    assert all(cell["throughput_signals_per_second"] > 0 for cell in cells)


def test_run_labels_measurement_semantics(monkeypatch):
    monkeypatch.setattr(
        "cascade_compression.benchmarks.cascade_runtime._cpu_limit", lambda: 1.0
    )
    args = Namespace(
        output="unused.json",
        environment_label="test",
        run_id="test-run",
        iterations=2,
        samples=2,
        warmup=1,
        batch_sizes=[1],
        concurrencies=[1],
        http_batch_size=1,
        http_url="",
        sc_address="",
        sc_signal="cascade_classification",
        sc_deadline=1.0,
    )

    result = run(args)

    assert result["schema_version"] == 1
    assert result["environment"]["label"] == "test"
    assert set(result["results"]) == {"nano"}
    assert "asynchronous" in result["semantics"]["http"]
