"""Sensor front-end adapters — raw sense data to structured signal fields.

The multi-sensory pattern: sensors → front-end adapter (this module) →
signals with structured numeric fields → cascade (unchanged). The
adapters are deterministic feature extractors; the domain pack's
VALUE_RULES declare the equipment-spec thresholds over the extracted
fields, and the engine's ValueRuleAgent evaluates them with zero model
calls.

Three numeric senses (vision is thin — vision systems emit text results
that flow through the event lane directly):

- Vibration/acoustic: window of acceleration samples → radix-2 FFT →
  band energies, RMS, crest factor, spectral centroid.
- Force/tactile: per-axis force/torque readings at contact → peak,
  sustained, deviation-from-nominal features.
- Proprioception: commanded vs actual joint state → following error,
  velocity, current-draw features.

The FFT is pure Python (radix-2, power-of-two windows) so the module
carries no numpy dependency; live collectors with higher sample rates
can substitute an optimized implementation — the output contract is
the feature dict.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence


# ---------------------------------------------------------------------------
# Pure-Python radix-2 FFT (iterative, in-place bit-reversal)
# ---------------------------------------------------------------------------

def _bit_reverse_indices(n: int) -> List[int]:
    bits = n.bit_length() - 1
    return [int(format(i, "0{}b".format(bits))[::-1], 2) for i in range(n)]


def fft_radix2(samples: Sequence[float]) -> List[complex]:
    """Iterative radix-2 FFT. ``len(samples)`` must be a power of two."""
    n = len(samples)
    if n == 0:
        return []
    if n & (n - 1):
        raise ValueError("FFT window length must be a power of two")
    order = _bit_reverse_indices(n)
    data = [complex(samples[i], 0.0) for i in order]
    size = 2
    while size <= n:
        half = size // 2
        twiddle_step = complex(math.cos(-2 * math.pi / size),
                               math.sin(-2 * math.pi / size))
        for start in range(0, n, size):
            w = 1.0 + 0j
            for offset in range(half):
                a = data[start + offset]
                b = data[start + offset + half] * w
                data[start + offset] = a + b
                data[start + offset + half] = a - b
                w *= twiddle_step
        size *= 2
    return data


# ---------------------------------------------------------------------------
# Vibration / acoustic
# ---------------------------------------------------------------------------

def vibration_features(samples: Sequence[float], sample_rate_hz: float,
                       bands: Sequence[Sequence[float]] = (
                           (0.0, 500.0), (500.0, 2000.0),
                           (2000.0, 8000.0), (8000.0, 20000.0))) -> Dict[str, float]:
    """Window of acceleration samples (g) → vibration feature fields.

    Bands default to condition-monitoring conventions: low (imbalance /
    1x RPM), low-mid (misalignment harmonics), high (bearing defect
    frequencies), very high (spalling / early wear).
    """
    n = len(samples)
    if n == 0:
        return {}
    rms = math.sqrt(sum(s * s for s in samples) / n)
    peak = max(abs(s) for s in samples)
    crest = peak / rms if rms > 0 else 0.0

    spectrum = fft_radix2(samples)
    half = n // 2
    mags = [abs(spectrum[i]) / n for i in range(1, half)]
    freqs = [i * sample_rate_hz / n for i in range(1, half)]
    total_power = sum(m * m for m in mags) or 1e-12
    centroid = (sum(f * m * m for f, m in zip(freqs, mags)) / total_power)

    features: Dict[str, float] = {"vib_rms_g": round(rms, 4),
                                  "vib_peak_g": round(peak, 4),
                                  "vib_crest_factor": round(crest, 2),
                                  "vib_centroid_hz": round(centroid, 1)}
    for i, (lo, hi) in enumerate(bands, start=1):
        energy = sum(m * m for f, m in zip(freqs, mags) if lo <= f < hi)
        features[f"vib_band{i}_energy"] = round(energy, 6)
        features[f"vib_band{i}_share"] = round(energy / total_power, 4)
    return features


def acoustic_features(samples: Sequence[float], sample_rate_hz: float) -> Dict[str, float]:
    """Acoustic window (Pa) → the same feature family, `ac_` prefixed."""
    out = vibration_features(samples, sample_rate_hz)
    return {k.replace("vib_", "ac_"): v for k, v in out.items()}


# ---------------------------------------------------------------------------
# Force / tactile
# ---------------------------------------------------------------------------

def force_features(readings: Sequence[Dict[str, float]],
                   nominal_grip_n: float = 0.0,
                   max_force_n: float = 0.0) -> Dict[str, float]:
    """Contact-moment F/T readings → force/tactile feature fields.

    ``readings`` are dicts with per-axis keys (fx, fy, fz) and optional
    torques (tx, ty, tz), sampled through a contact event.
    """
    if not readings:
        return {}
    magnitudes = [math.sqrt(r.get("fx", 0.0) ** 2 + r.get("fy", 0.0) ** 2
                            + r.get("fz", 0.0) ** 2) for r in readings]
    peak = max(magnitudes)
    mean = sum(magnitudes) / len(magnitudes)
    features: Dict[str, float] = {
        "force_peak_n": round(peak, 2),
        "force_mean_n": round(mean, 2),
        "force_samples": float(len(readings)),
    }
    torque_peak = 0.0
    for r in readings:
        t = math.sqrt(r.get("tx", 0.0) ** 2 + r.get("ty", 0.0) ** 2
                      + r.get("tz", 0.0) ** 2)
        torque_peak = max(torque_peak, t)
    if torque_peak > 0:
        features["torque_peak_nm"] = round(torque_peak, 3)
    if nominal_grip_n > 0:
        deviations = [abs(m - nominal_grip_n) for m in magnitudes]
        features["force_dev_from_nominal_n"] = round(max(deviations), 2)
    if max_force_n > 0:
        features["force_overrated_ratio"] = round(peak / max_force_n, 3)
    # sustained contact: fraction of samples above 80% of peak
    threshold = 0.8 * peak if peak > 0 else 0.0
    features["force_sustained_share"] = round(
        sum(1 for m in magnitudes if m >= threshold) / len(magnitudes), 3)
    return features


# ---------------------------------------------------------------------------
# Proprioception
# ---------------------------------------------------------------------------

def proprio_features(commanded: float, actual: float,
                     velocity: float = 0.0, current_a: float = 0.0,
                     rated_current_a: float = 0.0) -> Dict[str, float]:
    """Servo axis state → proprioceptive feature fields."""
    features: Dict[str, float] = {
        "servo_following_error": round(abs(commanded - actual), 4),
        "servo_velocity": round(velocity, 3),
    }
    if current_a:
        features["servo_current_a"] = round(current_a, 3)
    if rated_current_a > 0:
        features["servo_current_ratio"] = round(current_a / rated_current_a, 3)
    return features
