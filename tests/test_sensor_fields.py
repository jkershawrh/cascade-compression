"""Tests for the sensor front-end adapters (numeric senses)."""

import math

import pytest

from cascade_compression.harness.sensor_fields import (
    acoustic_features,
    fft_radix2,
    force_features,
    proprio_features,
    vibration_features,
)


def _sine(freq, n, fs, amp):
    return [amp * math.sin(2 * math.pi * freq * i / fs) for i in range(n)]


class TestFFT:
    def test_pure_sine_peaks_at_frequency(self):
        fs, n = 12800.0, 1024
        samples = _sine(3000, n, fs, 0.5)
        spec = fft_radix2(samples)
        half = n // 2
        mags = [(abs(spec[i]) / n, i * fs / n) for i in range(1, half)]
        peak_mag, peak_freq = max(mags)
        assert peak_freq == pytest.approx(3000, abs=fs / n)
        assert peak_mag == pytest.approx(0.25, abs=0.01)

    def test_dc_signal(self):
        spec = fft_radix2([1.0] * 8)
        assert abs(spec[0]) == pytest.approx(8.0)
        assert all(abs(spec[i]) < 1e-9 for i in range(1, 8))

    def test_rejects_non_power_of_two(self):
        with pytest.raises(ValueError, match="power of two"):
            fft_radix2([0.0] * 12)

    def test_empty(self):
        assert fft_radix2([]) == []


class TestVibrationFeatures:
    FS = 12800.0
    N = 1024

    def test_healthy_broadband_noise(self):
        import random
        random.seed(1)
        samples = [random.gauss(0, 0.05) for _ in range(self.N)]
        f = vibration_features(samples, self.FS)
        assert f["vib_rms_g"] == pytest.approx(0.05, abs=0.01)
        assert f["vib_crest_factor"] > 2.0  # gaussian noise crest
        assert sum(f["vib_band{}_share".format(i)] for i in range(1, 5)) == pytest.approx(1.0, abs=0.01)
        assert f["vib_band3_energy"] < 0.001  # healthy high band

    def test_bearing_signature_in_band3(self):
        samples = [a + b for a, b in zip(
            _sine(3000, self.N, self.FS, 0.3), _sine(4200, self.N, self.FS, 0.2))]
        f = vibration_features(samples, self.FS)
        assert f["vib_band3_share"] > 0.9
        assert f["vib_band3_energy"] > 0.003  # crosses the pack threshold

    def test_spalling_signature_in_band4(self):
        # Band 4 (8-20kHz) requires a sample rate above 40kHz (Nyquist).
        fs, n = 51200.0, 2048
        samples = _sine(11000, n, fs, 0.4)
        f = vibration_features(samples, fs)
        assert f["vib_band4_energy"] > 0.002

    def test_imbalance_signature_band1_plus_rms(self):
        samples = _sine(60, self.N, self.FS, 1.2)
        f = vibration_features(samples, self.FS)
        assert f["vib_band1_energy"] > 0.005
        assert f["vib_rms_g"] > 0.8
        assert f["vib_rms_g"] == pytest.approx(1.2 / math.sqrt(2), abs=0.02)

    def test_empty_window(self):
        assert vibration_features([], self.FS) == {}

    def test_custom_bands(self):
        samples = _sine(100, self.N, self.FS, 0.5)
        f = vibration_features(samples, self.FS, bands=((0, 200), (200, 1000)))
        assert set(f) == {"vib_rms_g", "vib_peak_g", "vib_crest_factor",
                          "vib_centroid_hz", "vib_band1_energy", "vib_band1_share",
                          "vib_band2_energy", "vib_band2_share"}


class TestAcousticFeatures:
    def test_prefix_swap(self):
        samples = _sine(2000, 512, 6400.0, 0.3)
        f = acoustic_features(samples, 6400.0)
        assert "ac_rms_g" in f and "ac_band2_energy" in f
        assert "vib_rms_g" not in f


class TestForceFeatures:
    def _contact(self, fx, n=50, tz=0.0):
        return [{"fx": fx, "fy": 0.0, "fz": 1.0, "tz": tz} for _ in range(n)]

    def test_nominal_grip(self):
        f = force_features(self._contact(80.0), nominal_grip_n=80.0, max_force_n=250)
        assert f["force_peak_n"] == pytest.approx(math.sqrt(80.0**2 + 1.0), abs=0.1)
        assert f["force_dev_from_nominal_n"] < 1.0
        assert f["force_overrated_ratio"] < 0.5

    def test_overload(self):
        f = force_features(self._contact(300.0), nominal_grip_n=80.0, max_force_n=250)
        assert f["force_peak_n"] > 250
        assert f["force_overrated_ratio"] > 1.0

    def test_grip_deviation(self):
        f = force_features(self._contact(50.0), nominal_grip_n=80.0)
        assert f["force_dev_from_nominal_n"] > 15

    def test_torque(self):
        f = force_features(self._contact(80.0, tz=15.0))
        assert f["torque_peak_nm"] == pytest.approx(15.0)

    def test_sustained_share(self):
        # constant force: every sample is >= 80% of peak
        f = force_features(self._contact(80.0))
        assert f["force_sustained_share"] == pytest.approx(1.0)

    def test_empty(self):
        assert force_features([]) == {}


class TestProprioFeatures:
    def test_healthy(self):
        f = proprio_features(commanded=100.0, actual=100.02, velocity=250.0,
                             current_a=4.0, rated_current_a=8.0)
        assert f["servo_following_error"] < 0.5
        assert f["servo_current_ratio"] == pytest.approx(0.5)

    def test_following_error(self):
        f = proprio_features(commanded=100.0, actual=99.2, velocity=250.0)
        assert f["servo_following_error"] == pytest.approx(0.8)
        assert f["servo_following_error"] > 0.5

    def test_overcurrent(self):
        f = proprio_features(100.0, 100.0, 0.0, current_a=11.0, rated_current_a=8.0)
        assert f["servo_current_ratio"] > 1.2
        assert f["servo_current_ratio"] < 1.5

    def test_severe_overcurrent(self):
        f = proprio_features(100.0, 100.0, 0.0, current_a=13.0, rated_current_a=8.0)
        assert f["servo_current_ratio"] > 1.5


class TestPackCoversSenses:
    def test_pack_rules_reference_sensor_fields(self):
        from cascade_compression.domain_plugins import load_domain_plugin
        rules = load_domain_plugin("robotic").value_rules
        names = {r["name"] for r in rules["rules"]}
        expected = {"bearing_wear_early", "bearing_spalling", "rotational_imbalance",
                    "severe_vibration", "force_overload", "grip_anomaly",
                    "tactile_torque_anomaly", "servo_following_error",
                    "servo_overcurrent", "servo_severe_overcurrent"}
        assert expected <= names
        assert rules["revision"] == "robotic-value-rules-v3"

    def test_spec_validates_in_agent(self):
        from cascade_compression.cascade.agents import ValueRuleAgent
        from cascade_compression.domain_plugins import load_domain_plugin
        agent = ValueRuleAgent(load_domain_plugin("robotic").value_rules)
        assert len(agent._rules) == 17
