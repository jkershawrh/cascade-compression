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

# Deterministic value rules for structured maintenance telemetry.
# This is the domain pack's "equipment spec sheet": rated operating ranges
# and failure signatures for milling-machine telemetry (AI4I 2020 dataset).
# The engine's ValueRuleAgent evaluates these declaratively; the pack owns
# the numbers. Rules that declare a "label" are authoritative on match —
# held-out evidence: 96.2% anomaly recall on rows 5000-9999 with thresholds
# fit on rows 0-4999, misses = random failures only (RNF has no value
# signature by construction).
#
# Threshold provenance: TWF/HDF/PWF windows follow the published dataset
# semantics. OSF strain limits are fit to the corpus (perfect separation:
# L non-OSF max 10,994 vs OSF min 11,003; M gap 11,920-12,337) rounded to
# spec-sheet values; H uses the documented 13,095 (too few H rows to fit).
# The published L value (11,952) does not match this corpus — it is the M
# boundary here.
#
# Fields expected in signal content: air_temp_k, process_temp_k,
# rotational_speed_rpm, torque_nm, tool_wear_min, machine_type (L/M/H).
#
# RNF (random failure) is deliberately NOT encoded — it has no value
# signature by construction and remains the residual for the semantic
# classifier lane.
VALUE_RULES = {
    # v4: multi-sensory extension — vibration/acoustic, force/tactile,
    # proprioception rules over sensor_fields features (the numeric-sense
    # front-end adapters). Thresholds are the synthetic cell's equipment
    # spec; real deployments substitute the actual spec sheet.
    # v4 adds `covers` (healthy-by-spec else labels): a covered signal
    # type carrying its required fields with no rule match is healthy by
    # spec — resolved deterministically, no model call. Authority: the
    # versioned spec + held-out corpus evidence (NOT LLM shadow re-checks:
    # the semantic lane's telemetry incompetence is a measured result).
    # Monitored via the else-rate; withdrawable via CASCADE_VALUE_RULES=off.
    "revision": "robotic-value-rules-v4",
    "covers": {
        # Else-label only types whose corpus shows ZERO signature-less
        # failures (100% rule recall) — the healthy side is safe to
        # resolve by spec. maintenance_telemetry (AI4I) is deliberately
        # NOT covered: its RNF residual (random failures, no signature)
        # must keep falling to the semantic lane.
        "vibration_window": {"label": "routine_noise",
                             "requires": ["vib_rms_g", "vib_band1_energy",
                                          "vib_band3_energy", "vib_band4_energy"]},
        "force_contact": {"label": "routine_noise",
                          "requires": ["force_peak_n", "force_sustained_share"]},
        "servo_state": {"label": "routine_noise",
                        "requires": ["servo_following_error", "servo_current_ratio"]},
    },
    "derived": {
        # Process-air temperature differential (K) — heat dissipation margin.
        "temp_diff_k": ["sub", ["field", "process_temp_k"], ["field", "air_temp_k"]],
        # Mechanical power (W): torque [Nm] x speed [rpm] x 2*pi/60 [rad/s].
        "power_w": ["mul", ["field", "torque_nm"], ["field", "rotational_speed_rpm"],
                    ["const", 0.10471975511965977]],
        # Tool strain (min-Nm): tool wear x torque — overstrain product.
        "strain_minnm": ["mul", ["field", "tool_wear_min"], ["field", "torque_nm"]],
    },
    "rules": [
        {   # OSF — overstrain: tool wear x torque beyond type rating.
            "name": "overstrain_l",
            "classification": "overstrain_risk",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "machine_type", "op": "eq", "value": "L"},
                {"field": "strain_minnm", "op": "gt", "value": 11000},
            ],
        },
        {   # OSF — overstrain, medium-capacity variant.
            "name": "overstrain_m",
            "classification": "overstrain_risk",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "machine_type", "op": "eq", "value": "M"},
                {"field": "strain_minnm", "op": "gt", "value": 12000},
            ],
        },
        {   # OSF — overstrain, high-capacity variant (documented threshold).
            "name": "overstrain_h",
            "classification": "overstrain_risk",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "machine_type", "op": "eq", "value": "H"},
                {"field": "strain_minnm", "op": "gt", "value": 13095},
            ],
        },
        {   # HDF — heat dissipation: low temp differential at low speed.
            "name": "heat_dissipation_risk",
            "classification": "heat_dissipation_failure_risk",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "temp_diff_k", "op": "lt", "value": 8.6},
                {"field": "rotational_speed_rpm", "op": "lt", "value": 1380},
            ],
        },
        {   # PWF — power below the rated transfer window.
            "name": "power_below_rated",
            "classification": "power_failure_risk",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "power_w", "op": "lt", "value": 3500},
            ],
        },
        {   # PWF — power above the rated transfer window.
            "name": "power_above_rated",
            "classification": "power_failure_risk",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "power_w", "op": "gt", "value": 9000},
            ],
        },
        {   # TWF — tool at end of life (replacement window 200-240 min).
            # needs_attention, not real_incident: the tool may be replaced
            # in time (678 of 723 wear>=200 rows are routine replacements);
            # the zero-FN discipline accepts the compression cost.
            "name": "tool_wear_degraded",
            "classification": "tool_wear_degraded",
            "label": "needs_attention",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [
                {"field": "tool_wear_min", "op": "ge", "value": 200},
            ],
        },
        # ---- vibration / acoustic (sensor_fields.vibration_features) ----
        # Order is priority: severe/specific rules precede mild/general
        # ones so dual-signature rows take the severe label.
        {   # Overall severity (ISO 10816-style zone boundary, in g).
            "name": "severe_vibration",
            "classification": "severe_vibration",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "vib_rms_g", "op": "gt", "value": 2.0},
            ],
        },
        {   # Very-high-band energy: spalling / advanced bearing wear.
            "name": "bearing_spalling",
            "classification": "bearing_spalling",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "vib_band4_energy", "op": "gt", "value": 0.002},
            ],
        },
        {   # Bearing defect frequencies live in the 2-8kHz band.
            "name": "bearing_wear_early",
            "classification": "bearing_degradation",
            "label": "needs_attention",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [
                {"field": "vib_band3_energy", "op": "gt", "value": 0.003},
            ],
        },
        {   # Rotational imbalance: 1x-RPM band energy plus elevated RMS.
            "name": "rotational_imbalance",
            "classification": "rotational_imbalance",
            "label": "needs_attention",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [
                {"field": "vib_band1_energy", "op": "gt", "value": 0.005},
                {"field": "vib_rms_g", "op": "gt", "value": 0.8},
            ],
        },
        # ---- force / tactile (sensor_fields.force_features) ----
        {   # Peak contact force beyond the cell's rated maximum.
            "name": "force_overload",
            "classification": "collision_or_overload",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "force_peak_n", "op": "gt", "value": 250},
            ],
        },
        {   # Grip force deviating from nominal (drop / damage risk).
            "name": "grip_anomaly",
            "classification": "grip_force_anomaly",
            "label": "needs_attention",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [
                {"field": "force_dev_from_nominal_n", "op": "gt", "value": 15},
            ],
        },
        {   # Contact torque beyond the wrist rating.
            "name": "tactile_torque_anomaly",
            "classification": "torque_anomaly",
            "label": "needs_attention",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [
                {"field": "torque_peak_nm", "op": "gt", "value": 12},
            ],
        },
        # ---- proprioception (sensor_fields.proprio_features) ----
        # Severe overcurrent precedes overcurrent (priority order).
        {   # Sustained overcurrent: imminent drive fault.
            "name": "servo_severe_overcurrent",
            "classification": "servo_overcurrent_severe",
            "label": "real_incident",
            "outcome": "escalate",
            "confidence": 0.95,
            "when": [
                {"field": "servo_current_ratio", "op": "gt", "value": 1.5},
            ],
        },
        {   # Servo following error beyond the axis tolerance.
            "name": "servo_following_error",
            "classification": "servo_following_error",
            "label": "needs_attention",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [
                {"field": "servo_following_error", "op": "gt", "value": 0.5},
            ],
        },
        {   # Motor current above rated (collision / overload precursor).
            "name": "servo_overcurrent",
            "classification": "servo_overcurrent",
            "label": "needs_attention",
            "outcome": "escalate",
            "confidence": 0.9,
            "when": [
                {"field": "servo_current_ratio", "op": "gt", "value": 1.2},
            ],
        },
    ],
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
