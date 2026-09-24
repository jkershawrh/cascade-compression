"""Advisory exact-repeat grouping with no signal suppression authority."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from typing import Callable, Iterable

from .protocol import Signal


class ExactRepeatTriage:
    """Bounded in-process grouping for fully identified Kubernetes Pod Events."""

    _VOLATILE = frozenset({
        "timestamp", "first_timestamp", "firstTimestamp", "lastTimestamp",
        "observed_at",
    })

    def __init__(self, *, window_seconds: float = 60, max_groups: int = 50_000,
                 clock: Callable[[], float] = time.monotonic):
        if window_seconds <= 0 or max_groups < 1:
            raise ValueError("invalid triage bounds")
        self.window_seconds = window_seconds
        self.max_groups = max_groups
        self.clock = clock
        self._groups: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _eligible(signal: Signal) -> bool:
        content = signal.content or {}
        labels = signal.labels or {}
        return bool(
            labels.get("domain") == "kubernetes"
            and labels.get("label") not in {"compliance", "fraud", "sanctions"}
            and signal.signal_type.startswith("event_")
            and signal.severity == "medium"
            and signal.cluster and signal.namespace and signal.source
            and content.get("kind") == "Pod" and content.get("uid")
            and content.get("event_uid") and content.get("reason")
            and type(content.get("count")) is int
            and isinstance(content.get("message_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", content["message_sha256"])
        )

    def assess(self, signals: Iterable[Signal]) -> dict[str, dict]:
        """Return advisory tags keyed by signal ID; inputs remain untouched."""
        with self._lock:
            now = self.clock()
            while self._groups:
                first_key, (first_seen, _) = next(iter(self._groups.items()))
                if now - first_seen < self.window_seconds:
                    break
                self._groups.pop(first_key)

            tags: dict[str, dict] = {}
            for signal in signals:
                if not self._eligible(signal):
                    continue
                stable_content = {
                    key: value for key, value in (signal.content or {}).items()
                    if key not in self._VOLATILE
                }
                identity = [
                    "kubernetes", signal.cluster, signal.signal_type,
                    signal.source, signal.namespace, signal.severity,
                    stable_content,
                ]
                digest = hashlib.sha256(json.dumps(
                    identity, sort_keys=True, separators=(",", ":"), default=str,
                ).encode()).hexdigest()
                prior = self._groups.get(digest)
                if prior is None:
                    while len(self._groups) >= self.max_groups:
                        self._groups.popitem(last=False)
                    self._groups[digest] = (now, 1)
                    occurrence = 1
                else:
                    first_seen, count = prior
                    occurrence = count + 1
                    self._groups[digest] = (first_seen, occurrence)
                tags[str(signal.signal_id)] = {
                    "advisory": True,
                    "kind": "exact_repeat_group",
                    "group_id": digest,
                    "occurrence": occurrence,
                    "exact_repeat": occurrence > 1,
                }
            return tags
