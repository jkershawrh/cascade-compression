"""Replay an observations.jsonl capture through a local cascade service.

Reads a frozen capture (one JSON observation per line — the collector's
identity-envelope format: event_uid, uid, kind, name, namespace, reason,
message, message_sha256, count) and POSTs batches to the service's
``/cascade`` endpoint, preserving the collector's mapping semantics:

- ``type: event`` -> ``signal_type = event_{reason.lower()}``
- severity ``medium`` for all events (matches the k8s collector)
- the identity envelope flows in ``content`` so the verified-repeat
  dedup folds re-observed events exactly as in production

Disclosed fast-forward bias: replaying N hours of polls in minutes means
wall-clock suppression windows see proportionally more repeats per
window than production. Dedup identity semantics are unaffected. Draw
governance-mechanical conclusions (promotion, withdrawal, TTL,
suppression firing), not rate-precise ones.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import Counter
from uuid import uuid4


def to_signal(line: dict) -> dict:
    """Map one capture observation to the /cascade SignalInput schema."""
    reason = line.get("reason", "") or "unknown"
    return {
        "signal_type": f"event_{reason.lower()}",
        "severity": "medium",
        "source": line.get("name", ""),
        "namespace": line.get("namespace", ""),
        "cluster": line.get("cluster", "replay"),
        "labels": {"domain": "kubernetes"},
        "content": {
            "message": line.get("message", ""),
            "kind": line.get("kind", ""),
            "uid": line.get("uid", ""),
            "event_uid": line.get("event_uid", ""),
            "reason": reason,
            "count": line.get("count", 1),
            "message_sha256": line.get("message_sha256", ""),
        },
    }


def injection_signal(signal_type: str, source: str = "injection-probe",
                     namespace: str = "replay") -> dict:
    """A signal of an (activated) type carrying the IMPORTANT marker.

    The model stub labels anything containing SIMULATED-IMPORTANT as
    real_incident, so posting this after a type activates demonstrates
    instant authority withdrawal (negative validation).
    """
    message = f"SIMULATED-IMPORTANT probe for {signal_type}"
    return {
        "signal_type": signal_type,
        "severity": "medium",
        "source": source,
        "namespace": namespace,
        "cluster": "replay",
        "labels": {"domain": "kubernetes"},
        "content": {
            "message": message,
            "kind": "Probe",
            "uid": f"probe:{signal_type}",
            "event_uid": f"probe-{uuid4()}",
            "reason": "Probe",
            "count": 1,
            "message_sha256": "",
        },
    }


def post_batch(target: str, signals: list) -> dict:
    req = urllib.request.Request(
        target.rstrip("/") + "/cascade",
        data=json.dumps({"signals": signals}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def replay(path: str, target: str, batch_size: int = 200, limit: int = 0,
           delay: float = 0.0) -> dict:
    """Stream the capture through the service; return a summary."""
    types = Counter()
    batches = 0
    signals_sent = 0
    t0 = time.monotonic()
    batch = []
    with open(path, "r", encoding="utf-8") as handle:
        for n, line in enumerate(handle):
            if limit and n >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obs = json.loads(line)
            except json.JSONDecodeError:
                continue
            batch.append(to_signal(obs))
            types[batch[-1]["signal_type"]] += 1
            if len(batch) >= batch_size:
                post_batch(target, batch)
                batches += 1
                signals_sent += len(batch)
                batch = []
                if delay:
                    time.sleep(delay)
    if batch:
        post_batch(target, batch)
        batches += 1
        signals_sent += len(batch)
    elapsed = time.monotonic() - t0
    return {
        "signals": signals_sent,
        "batches": batches,
        "elapsed_s": round(elapsed, 1),
        "rate_per_s": round(signals_sent / max(elapsed, 0.001), 1),
        "top_types": dict(types.most_common(10)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="observations.jsonl path")
    parser.add_argument("--target", default="http://localhost:8090")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N observations (0 = all)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="seconds between batches (rate throttling)")
    args = parser.parse_args()
    summary = replay(args.capture, args.target, args.batch_size,
                     args.limit, args.delay)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
