"""Shared test helpers."""

from cascade_compression.cascade.protocol import Signal


def make_signal(signal_type="pod_crashloop", severity="high", source="node-01",
                namespace="production", content=None, labels=None, cluster=""):
    return Signal(
        signal_type=signal_type,
        severity=severity,
        source=source,
        namespace=namespace,
        cluster=cluster,
        content=content or {"message": f"{signal_type} detected"},
        labels=labels or {},
    )
