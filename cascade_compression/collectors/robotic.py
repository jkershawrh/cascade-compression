"""Robotic cell collectors — industrial signal sources.

Collectors for robot controllers (OPC UA), PLCs (Modbus/symbolic),
vision systems, safety systems, and predictive maintenance sensors.
Each collector produces signals with the identity envelope required
for verified-repeat suppression.
"""
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import List

log = logging.getLogger(__name__)


class RobotControllerCollector:
    """Polls robot controller state via OPC UA or REST.

    Produces signals for cycle completions, fault codes, state changes,
    and axis telemetry. Each signal carries the identity envelope.
    """

    def __init__(self, cell_id: str, robot_id: str, controller_url: str = ""):
        self.cell_id = cell_id
        self.robot_id = robot_id
        self.controller_url = controller_url or os.getenv("ROBOT_CONTROLLER_URL", "")
        self._last_sequence = 0

    def collect(self) -> List[dict]:
        """Return signals from the robot controller."""
        signals = []
        for event in self._poll_controller():
            message = event.get("message", "")
            signals.append({
                "signal_type": f"robot_{event.get('type', 'unknown')}",
                "severity": event.get("severity", "info"),
                "source": f"robot:{self.robot_id}",
                "namespace": self.cell_id,
                "cluster": os.getenv("CASCADE_CLUSTER_ID", "robotic-fleet"),
                "content": {
                    "message": message,
                    "kind": "Robot",
                    "uid": f"{self.cell_id}:{self.robot_id}",
                    "event_uid": f"robot-{self.robot_id}-{event.get('sequence', self._next_seq())}",
                    "reason": event.get("type", "unknown"),
                    "count": event.get("count", 1),
                    "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                    "axis": event.get("axis", ""),
                    "fault_code": event.get("fault_code", ""),
                    "cycle_time_ms": event.get("cycle_time_ms"),
                },
                "labels": {
                    "domain": "robotic",
                    "cell": self.cell_id,
                    "robot": self.robot_id,
                    "axis": event.get("axis", ""),
                },
            })
        return signals

    def _poll_controller(self):
        """Poll the controller — override with actual OPC UA/REST client."""
        return []

    def _next_seq(self):
        self._last_sequence += 1
        return self._last_sequence


class PLCTagCollector:
    """Polls PLC tag values via Modbus or symbolic access.

    Produces signals for tag changes that exceed thresholds.
    Repetitive normal-state tags are suppressed by the nano tier.
    """

    def __init__(self, cell_id: str, plc_id: str, tags: dict = None):
        self.cell_id = cell_id
        self.plc_id = plc_id
        self.tags = tags or {}
        self._previous_values = {}

    def collect(self) -> List[dict]:
        signals = []
        for tag_name, value in self._read_tags().items():
            if not self._should_emit(tag_name, value):
                continue
            message = f"{tag_name}={value}"
            signals.append({
                "signal_type": f"plc_{tag_name}",
                "severity": self._tag_severity(tag_name, value),
                "source": f"plc:{self.plc_id}",
                "namespace": self.cell_id,
                "cluster": os.getenv("CASCADE_CLUSTER_ID", "robotic-fleet"),
                "content": {
                    "message": message,
                    "kind": "PLC",
                    "uid": f"{self.cell_id}:{self.plc_id}:{tag_name}",
                    "event_uid": f"plc-{self.plc_id}-{tag_name}-{value}",
                    "reason": tag_name,
                    "count": 1,
                    "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                    "tag": tag_name,
                    "value": value,
                    "previous_value": self._previous_values.get(tag_name),
                },
                "labels": {
                    "domain": "plc",
                    "cell": self.cell_id,
                    "tag": tag_name,
                },
            })
            self._previous_values[tag_name] = value
        return signals

    def _read_tags(self):
        """Read PLC tags — override with actual Modbus/symbolic client."""
        return {}

    def _should_emit(self, tag_name, value):
        """Only emit when value changes or crosses a threshold."""
        prev = self._previous_values.get(tag_name)
        if prev is None or prev != value:
            return True
        # Same value — let the nano tier suppress it
        return False

    def _tag_severity(self, tag_name, value):
        """Map tag values to severity."""
        thresholds = self.tags.get(tag_name, {})
        if value >= thresholds.get("critical", float("inf")):
            return "critical"
        if value >= thresholds.get("high", float("inf")):
            return "high"
        if value >= thresholds.get("medium", float("inf")):
            return "medium"
        return "info"


class VisionSystemCollector:
    """Collects vision inspection results.

    Pass/fail results for each inspected part. Identical pass results
    are suppressed by the nano tier; failures and anomalies reach the
    classifier.
    """

    def __init__(self, cell_id: str, camera_id: str):
        self.cell_id = cell_id
        self.camera_id = camera_id
        self._inspection_count = 0

    def collect(self) -> List[dict]:
        signals = []
        for result in self._get_results():
            self._inspection_count += 1
            message = f"Part #{self._inspection_count}: {result.get('outcome', 'unknown')}"
            signals.append({
                "signal_type": f"vision_{result.get('outcome', 'unknown')}",
                "severity": "low" if result.get("outcome") == "pass" else "medium",
                "source": f"vision:{self.camera_id}",
                "namespace": self.cell_id,
                "cluster": os.getenv("CASCADE_CLUSTER_ID", "robotic-fleet"),
                "content": {
                    "message": message,
                    "kind": "Vision",
                    "uid": f"{self.cell_id}:{self.camera_id}",
                    "event_uid": f"vision-{self.camera_id}-{self._inspection_count}",
                    "reason": result.get("outcome", "unknown"),
                    "count": 1,
                    "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                    "defect_type": result.get("defect_type", ""),
                    "measurement": result.get("measurement"),
                    "tolerance": result.get("tolerance"),
                },
                "labels": {
                    "domain": "vision",
                    "cell": self.cell_id,
                    "camera": self.camera_id,
                },
            })
        return signals

    def _get_results(self):
        """Get inspection results — override with actual vision system client."""
        return []


class SafetySystemCollector:
    """Collects safety events — NEVER suppressed.

    Uses triage_only profile internally: every safety event is
    delivered, classified, and audited. No deduplication.
    """

    def __init__(self, cell_id: str):
        self.cell_id = cell_id

    def collect(self) -> List[dict]:
        signals = []
        for event in self._get_safety_events():
            message = f"Safety: {event.get('type', 'unknown')} — {event.get('detail', '')}"
            signals.append({
                "signal_type": f"safety_{event.get('type', 'unknown')}",
                "severity": event.get("severity", "critical"),
                "source": f"safety:{self.cell_id}",
                "namespace": self.cell_id,
                "cluster": os.getenv("CASCADE_CLUSTER_ID", "robotic-fleet"),
                "content": {
                    "message": message,
                    "kind": "Safety",
                    "uid": f"{self.cell_id}:safety",
                    "event_uid": f"safety-{self.cell_id}-{event.get('timestamp', datetime.now(timezone.utc).isoformat())}",
                    "reason": event.get("type", "unknown"),
                    "count": 1,
                    "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                    "zone": event.get("zone", ""),
                    "action_taken": event.get("action_taken", ""),
                },
                "labels": {
                    "domain": "safety",
                    "cell": self.cell_id,
                },
            })
        return signals

    def _get_safety_events(self):
        """Get safety events — override with actual safety system client."""
        return []
