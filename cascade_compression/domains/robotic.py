"""Robotic cell domain pack — industrial automation signals.

Signal types from robotic workcells: robot controller events, PLC tag
changes, vision inspection results, safety system events, cycle-time
telemetry, and predictive maintenance sensors.

The nano tier is ideal here: industrial processes are massively
repetitive (the same cycle thousands of times per shift), so identity-
based suppression with count-update folding eliminates ~96% of signal
volume. Only anomalies, faults, and deviations reach the classifier.
"""

DOMAIN = "robotic"
SYSTEM_PROMPT = """You are classifying industrial robotic cell signals from a manufacturing workcell. Signal sources include robot controllers, PLCs, vision systems, safety systems, and environmental sensors.

Context: A robotic workcell in a production line performing repetitive operations (welding, assembly, pick-and-place, inspection). Normal operation produces highly repetitive signals. Anomalies indicate equipment degradation, quality issues, or process drift.

Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""

MEMORY_CONFIG = {
    "decay": {
        "routine_noise": {"half_life_hours": 12, "min_strength": 0.05},
        "known_pattern": {"half_life_hours": 48, "min_strength": 0.05},
        "needs_attention": {"half_life_hours": 168, "min_strength": 0.1},
        "real_incident": {"half_life_hours": 720, "min_strength": 0.2},
    },
    "consolidation_interval_hours": 4,
    "eviction_threshold": 0.05,
}

# Per-domain dedup windows for this pack
DEDUP_WINDOWS = {
    "robotic": 3600,       # 1h — cycles repeat within a shift
    "plc": 300,             # 5min — tag values stabilize quickly
    "vision": 3600,         # 1h — pass/fail patterns repeat
    "safety": 0,            # never — safety events always alert
    "maintenance": 1800,    # 30min — sensor readings repeat
}


def _robot_entity_extractor(sig):
    """Entity key: cell:robot:axis or cell:plc:tag."""
    cell = sig.labels.get("cell", "")
    robot = sig.labels.get("robot", "")
    axis = sig.labels.get("axis", "")
    if robot:
        return f"{cell}:{robot}:{axis}" if axis else f"{cell}:{robot}"
    tag = sig.labels.get("tag", "")
    if tag:
        return f"{cell}:plc:{tag}"
    return f"{cell}:general" if cell else None
