"""AI4I telemetry field adapter — the numeric-sense front-end exemplar.

Sensors → front-end adapter → signals → cascade (unchanged). This module is
the front-end adapter for numeric telemetry: it parses machine-readable
values out of raw telemetry text and adds them as structured content fields
so the engine's deterministic value rules (domain pack VALUE_RULES) can read
them. The semantic message is preserved unchanged for the classifier lanes.

The parse is deterministic on the fixed AI4I corpus message format:

    machine M14860 type M: air 298.1K proc 308.6K speed 1551rpm
    torque 42.8Nm tool_wear 0min[ failure_modes=TWF]

Fields added to content: machine_type, air_temp_k, process_temp_k,
rotational_speed_rpm, torque_nm, tool_wear_min.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_MESSAGE_RE = re.compile(
    r"^machine (?P<machine>\S+) type (?P<type>\w+): "
    r"air (?P<air>\d+(?:\.\d+)?)K proc (?P<proc>\d+(?:\.\d+)?)K "
    r"speed (?P<speed>\d+)rpm torque (?P<torque>\d+(?:\.\d+)?)Nm "
    r"tool_wear (?P<wear>\d+)min"
    r"(?: failure_modes=(?P<modes>[\w,+]+))?$"
)


class TelemetryFieldError(ValueError):
    """Raised when a telemetry message does not match the expected format."""


def parse_telemetry_message(message: str) -> Dict[str, Any]:
    """Parse an AI4I telemetry message into structured numeric fields.

    Raises TelemetryFieldError for messages outside the fixed format —
    an adapter must fail loudly rather than silently dropping values.
    """
    match = _MESSAGE_RE.match(message or "")
    if not match:
        raise TelemetryFieldError(f"unparseable telemetry message: {message!r}")
    g = match.groupdict()
    return {
        "machine_id": g["machine"],
        "machine_type": g["type"],
        "air_temp_k": float(g["air"]),
        "process_temp_k": float(g["proc"]),
        "rotational_speed_rpm": int(g["speed"]),
        "torque_nm": float(g["torque"]),
        "tool_wear_min": int(g["wear"]),
    }


def add_telemetry_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a corpus row with structured numeric fields added to content.

    The message and every other field are preserved unchanged; only the
    machine-readable fields are added. Rows are not modified in place.
    """
    content = dict(row.get("content") or {})
    fields = parse_telemetry_message(content.get("message", ""))
    if fields["machine_id"] != content.get("uid"):
        raise TelemetryFieldError(
            f"machine id {fields['machine_id']!r} does not match uid "
            f"{content.get('uid')!r}")
    content.update({
        "machine_type": fields["machine_type"],
        "air_temp_k": fields["air_temp_k"],
        "process_temp_k": fields["process_temp_k"],
        "rotational_speed_rpm": fields["rotational_speed_rpm"],
        "torque_nm": fields["torque_nm"],
        "tool_wear_min": fields["tool_wear_min"],
    })
    return {**row, "content": content}


def convert_corpus(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert a corpus of signal rows to structured-field rows."""
    return [add_telemetry_fields(row) for row in rows]


def telemetry_row_to_signal(row: Dict[str, Any]):
    """Build a cascade Signal from a (converted) corpus row."""
    from ..cascade.protocol import Signal

    return Signal(
        signal_type=row.get("signal_type", "maintenance_telemetry"),
        severity=row.get("severity", "medium"),
        source=row.get("source", ""),
        namespace=row.get("namespace", ""),
        cluster=row.get("cluster", ""),
        labels=dict(row.get("labels") or {}),
        content=dict(row.get("content") or {}),
    )


def load_corpus(path: str) -> List[Dict[str, Any]]:
    """Load a JSONL corpus."""
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_corpus(rows: List[Dict[str, Any]], path: str) -> None:
    """Write a JSONL corpus."""
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
