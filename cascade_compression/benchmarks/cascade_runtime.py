"""Runtime benchmark for Cascade's synchronous path and llm-d-sc overhead.

The benchmark deliberately reports three independent measurements:

* ``nano``: the in-process deterministic pipeline, with no model work.
* ``http``: the externally visible ``POST /cascade`` request path.
* ``semantic``: direct synchronous llm-d-sc gRPC classification.

Keeping these measurements separate avoids implying that llm-d-sc latency is
part of Cascade's HTTP response.  The current service queues survivor
classification asynchronously after the deterministic request path returns.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from cascade_compression.cascade.agents import default_agents
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.protocol import Signal
from cascade_compression.classifier import serialize_signal


SCHEMA_VERSION = 1


def percentile(data: list[float], pct: float) -> float:
    """Interpolate a percentile without the production-proof benchmark package."""
    if not data:
        return 0.0
    values = sorted(data)
    position = (len(values) - 1) * pct / 100
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (position - lower) * (values[upper] - values[lower])


def _cpu_limit() -> float | None:
    """Return a measured cgroup CPU quota, never an unverified host core count."""
    cpu_max = Path("/sys/fs/cgroup/cpu.max")
    try:
        quota, period = cpu_max.read_text(encoding="utf-8").strip().split()
        if quota != "max":
            return max(float(quota) / float(period), 0.001)
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return None


def _summary(latencies_ms: list[float], signals: int, elapsed_seconds: float,
             cpu_limit: float | None, errors: int = 0,
             failed_signals: int | None = None) -> dict[str, Any]:
    failed = errors if failed_signals is None else failed_signals
    successful = max(0, signals - failed)
    attempted_throughput = signals / elapsed_seconds if elapsed_seconds > 0 else 0.0
    throughput = successful / elapsed_seconds if elapsed_seconds > 0 else 0.0
    return {
        "samples": len(latencies_ms),
        "signals": signals,
        "successful_signals": successful,
        "errors": errors,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "latency_ms": {
            "p50": round(percentile(latencies_ms, 50), 6),
            "p95": round(percentile(latencies_ms, 95), 6),
            "p99": round(percentile(latencies_ms, 99), 6),
            "mean": round(statistics.fmean(latencies_ms), 6) if latencies_ms else 0.0,
            "min": round(min(latencies_ms), 6) if latencies_ms else 0.0,
            "max": round(max(latencies_ms), 6) if latencies_ms else 0.0,
        },
        "throughput_signals_per_second": round(throughput, 3),
        "throughput_signals_per_second_per_core": (
            round(throughput / cpu_limit, 3) if cpu_limit else None
        ),
        "attempted_signals_per_second": round(attempted_throughput, 3),
    }


def _signal(index: int, *, severity: str = "info", unique: bool = True) -> Signal:
    suffix = index if unique else 0
    return Signal(
        signal_type="scheduled_reconciliation",
        severity=severity,
        source=f"benchmark-{suffix}",
        namespace="benchmark",
        content={"message": f"Scheduled reconciliation {suffix} completed successfully"},
        labels={"benchmark": "cascade-runtime"},
    )


def benchmark_nano(*, iterations: int, warmup: int,
                   batch_sizes: list[int], cpu_limit: float | None) -> list[dict[str, Any]]:
    cells = []
    for batch_size in batch_sizes:
        pipeline = CascadePipeline(default_agents())
        sequence = 0

        def run_once() -> None:
            nonlocal sequence
            batch = [_signal(sequence + offset) for offset in range(batch_size)]
            sequence += batch_size
            result = pipeline.run(batch)
            if result.remaining:
                raise RuntimeError("nano benchmark input unexpectedly survived")

        for _ in range(warmup):
            run_once()
        latencies = []
        started = time.perf_counter()
        for _ in range(iterations):
            call_started = time.perf_counter_ns()
            run_once()
            latencies.append((time.perf_counter_ns() - call_started) / 1_000_000)
        elapsed = time.perf_counter() - started
        cell = _summary(latencies, iterations * batch_size, elapsed, cpu_limit)
        cell.update({
            "batch_size": batch_size,
            "per_signal_latency_us_p50": round(
                percentile(latencies, 50) * 1000 / batch_size, 3
            ),
        })
        cells.append(cell)
    return cells


def _parallel_samples(call: Callable[[int], tuple[float, bool]], *, samples: int,
                      concurrency: int) -> tuple[list[float], int, float]:
    latencies: list[float] = []
    errors = 0
    started = time.perf_counter()
    if concurrency == 1:
        results = (call(index) for index in range(samples))
        for latency, ok in results:
            latencies.append(latency)
            errors += int(not ok)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(call, index) for index in range(samples)]
            for future in as_completed(futures):
                latency, ok = future.result()
                latencies.append(latency)
                errors += int(not ok)
    return latencies, errors, time.perf_counter() - started


def benchmark_http(*, base_url: str, samples: int, warmup: int,
                   concurrencies: list[int], batch_size: int,
                   cpu_limit: float | None) -> list[dict[str, Any]]:
    import httpx

    url = f"{base_url.rstrip('/')}/cascade"
    payload_counter = 0

    def payload() -> dict[str, Any]:
        nonlocal payload_counter
        start = payload_counter
        payload_counter += batch_size
        return {"signals": [
            {
                "signal_type": "scheduled_reconciliation",
                "severity": "info",
                "source": f"benchmark-{start + offset}",
                "namespace": "benchmark",
                "content": {
                    "message": f"Scheduled reconciliation {start + offset} completed successfully"
                },
                "labels": {"benchmark": "cascade-runtime"},
            }
            for offset in range(batch_size)
        ]}

    cells = []
    # httpx.Client is thread-safe. Reusing it measures the steady-state request
    # path instead of repeatedly charging TLS/TCP connection setup to Cascade.
    with httpx.Client(timeout=30.0) as client:
        for _ in range(warmup):
            response = client.post(url, json=payload())
            response.raise_for_status()

        for concurrency in concurrencies:
            def call(_: int) -> tuple[float, bool]:
                started = time.perf_counter_ns()
                try:
                    response = client.post(url, json=payload())
                    ok = response.status_code == 200 and "error" not in response.json()
                except (httpx.HTTPError, ValueError):
                    ok = False
                return (time.perf_counter_ns() - started) / 1_000_000, ok

            latencies, errors, elapsed = _parallel_samples(
                call, samples=samples, concurrency=concurrency,
            )
            cell = _summary(
                latencies, samples * batch_size, elapsed, cpu_limit, errors,
                failed_signals=errors * batch_size,
            )
            cell.update({"concurrency": concurrency, "batch_size": batch_size})
            cells.append(cell)
    return cells


def benchmark_semantic(*, address: str, signal_name: str, run_id: str, samples: int,
                       warmup: int, concurrencies: list[int],
                       cpu_limit: float | None, deadline_seconds: float) -> list[dict[str, Any]]:
    import grpc

    from cascade_compression.integrations.llm_d_sc import classify_pb2, classify_pb2_grpc

    channel = grpc.insecure_channel(address)
    stub = classify_pb2_grpc.ClassifyStub(channel)

    def classify(index: int, normalize: bool) -> tuple[float, bool]:
        signal = {
            "signal_id": str(uuid4()),
            "signal_type": "scheduled_reconciliation",
            "severity": "info",
            "namespace": "benchmark",
            "content": {
                "message": (
                    f"Benchmark run {run_id} scheduled reconciliation {index} "
                    "completed successfully"
                )
            },
        }
        text = serialize_signal(signal, normalize=normalize)
        started = time.perf_counter_ns()
        try:
            response = stub.Classify(
                classify_pb2.ClassifyRequest(
                    request_id=signal["signal_id"],
                    session_id="",
                    context=text,
                    signals=[signal_name],
                    context_completeness=classify_pb2.FULL,
                ),
                timeout=deadline_seconds,
            )
            ok = response.status == classify_pb2.OK and bool(response.ranked)
        except grpc.RpcError:
            ok = False
        return (time.perf_counter_ns() - started) / 1_000_000, ok

    for index in range(warmup):
        _, ok = classify(index, True)
        if not ok:
            raise RuntimeError("llm-d-sc warmup classification failed")

    cells = []
    for workload_index, (workload, normalize) in enumerate(
        # Measure the fast path before deliberately saturating the classifier;
        # otherwise timed-out cache-miss work can remain queued and pollute the
        # following cache-hit latency distribution.
        (("normalized_cache_hit", True), ("unique_cache_miss", False))
    ):
        for concurrency_index, concurrency in enumerate(concurrencies):
            # Never reuse raw cache-miss inputs between concurrency cells. A
            # previous version reused the same range, causing the serial cell
            # to warm the service cache for later cells and mislabeling their
            # results. Normalized inputs intentionally collapse to one key.
            offset = 1_000_000 + (
                workload_index * len(concurrencies) + concurrency_index
            ) * samples
            latencies, errors, elapsed = _parallel_samples(
                lambda index, base=offset: classify(index + base, normalize),
                samples=samples,
                concurrency=concurrency,
            )
            cell = _summary(latencies, samples, elapsed, cpu_limit, errors)
            cell.update({"workload": workload, "concurrency": concurrency})
            cells.append(cell)
    return cells


def run(args: argparse.Namespace) -> dict[str, Any]:
    cpu_limit = getattr(args, "cpu_cores", None) or _cpu_limit()
    run_id = args.run_id or uuid4().hex
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.perf_counter()
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "cascade-runtime",
        "semantics": {
            "nano": "In-process deterministic CascadePipeline only.",
            "http": "External POST /cascade latency; survivor model work is asynchronous.",
            "semantic": "Direct synchronous llm-d-sc gRPC round trip.",
        },
        "environment": {
            "label": args.environment_label,
            "cpu_limit": cpu_limit,
            "cpu_allocation_verified": cpu_limit is not None,
            "cpu_limit_source": (
                "explicit" if getattr(args, "cpu_cores", None) else
                "cgroup_quota" if cpu_limit is not None else "unverified"
            ),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "git_revision": os.getenv("BENCHMARK_GIT_REVISION", "unknown"),
        },
        "config": {
            "iterations": args.iterations,
            "samples": args.samples,
            "warmup": args.warmup,
            "batch_sizes": args.batch_sizes,
            "concurrencies": args.concurrencies,
            "http_batch_size": args.http_batch_size,
            "run_id": run_id,
        },
        "results": {
            "nano": benchmark_nano(
                iterations=args.iterations,
                warmup=args.warmup,
                batch_sizes=args.batch_sizes,
                cpu_limit=cpu_limit,
            ),
        },
    }
    if args.http_url:
        result["results"]["http"] = benchmark_http(
            base_url=args.http_url,
            samples=args.samples,
            warmup=args.warmup,
            concurrencies=args.concurrencies,
            batch_size=args.http_batch_size,
            cpu_limit=cpu_limit,
        )
    if args.sc_address:
        result["results"]["semantic"] = benchmark_semantic(
            address=args.sc_address,
            signal_name=args.sc_signal,
            run_id=run_id,
            samples=args.samples,
            warmup=args.warmup,
            concurrencies=args.concurrencies,
            cpu_limit=cpu_limit,
            deadline_seconds=args.sc_deadline,
        )
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    result["run"] = {
        "wall_seconds": round(time.perf_counter() - started, 6),
        "user_cpu_seconds": round(usage_after.ru_utime - usage_before.ru_utime, 6),
        "system_cpu_seconds": round(usage_after.ru_stime - usage_before.ru_stime, 6),
        "max_rss_kib": usage_after.ru_maxrss,
    }
    return result


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--environment-label", default="unspecified")
    parser.add_argument("--cpu-cores", type=_positive_float,
                        help="Verified CPU allocation when no cgroup quota exists")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--iterations", type=_positive, default=1000)
    parser.add_argument("--samples", type=_positive, default=1000)
    parser.add_argument("--warmup", type=_positive, default=100)
    parser.add_argument("--batch-sizes", type=_positive, nargs="+", default=[1, 16, 128, 1024])
    parser.add_argument("--concurrencies", type=_positive, nargs="+", default=[1, 8, 32])
    parser.add_argument("--http-batch-size", type=_positive, default=1)
    parser.add_argument("--http-url", default="")
    parser.add_argument("--sc-address", default="")
    parser.add_argument("--sc-signal", default="cascade_classification")
    parser.add_argument("--sc-deadline", type=float, default=5.0)
    args = parser.parse_args()
    result = run(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
