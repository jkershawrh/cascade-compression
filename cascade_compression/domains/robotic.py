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

FEW_SHOT_EXEMPLARS = {
    "routine_noise": [
        "[ST-01] cycle pick part BRK-48392 cycle_time 4.52s pos_ok",
        "[ST-02] plc air_pressure = 6.0bar",
        "[ST-01] vision inspect part PLT-22318 RESULT: PASS",
        "[ST-04] telemetry cycle_time 4.61s trend nominal",
        "[ST-03] maintenance spindle_temp = 48C",
        "[ST-02] cycle weld part WLD-77381 cycle_time 4.35s pos_ok"
    ],
    "known_pattern": [
        "[ST-03] vision inspect part INS-81932 RESULT: MINOR_DEFECT dim 0.153mm",
        "[ST-01] SAFETY light_curtain beam break during maintenance window authorized",
        "[ST-02] plc feed_rate setpoint changed 1200 to 1180mm/min by recipe"
    ],
    "needs_attention": [
        "[ST-02] telemetry cycle_time 6.84s trend DEGRADING",
        "[ST-01] maintenance vibration ALERT: vibration 14.2mm/s threshold 8.0 exceeded",
        "[ST-03] vision inspect part INS-40001 RESULT: FAIL new defect class: surface_crack",
        "[ST-02] SAFETY interlock fault light_curtain channel 2 unresponsive"
    ],
    "real_incident": [
        "[ST-01] spindle overload trip current 187% rated \u2014 spindle stopped",
        "[ST-03] controller fault axis 3 position error 4.2mm exceeding tolerance \u2014 controller reset required",
        "[ST-04] communication loss to servo drive node 12 timeout 5000ms \u2014 drive offline",
        "[ST-01] SAFETY e-stop TRIGGERED operator zone intrusion"
    ]
}
