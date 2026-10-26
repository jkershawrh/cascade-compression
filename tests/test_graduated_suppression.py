"""Graduated suppression authority: deeper evidence earns deeper suppression.

The standard promotion bar (200+ noise, 0 important) earns first-3-per-
source-per-5-min. As the classified-noise record grows, the tier deepens:
fewer deliveries per window, then longer windows. Tiers never outlive the
authority they deepen — withdrawal clears them with the type.
"""

import pytest

from cascade_compression.bridge import suppression_tier_for
from cascade_compression.cascade.dynamic_agents import (
    DominantNoiseSuppressor, RepeatFloodSuppressor,
)
from cascade_compression.cascade.protocol import Outcome, Signal


def _signals(n, signal_type="t", source="s", severity="medium"):
    return [Signal(signal_type=signal_type, severity=severity,
                   source=source, namespace="ns") for _ in range(n)]


def test_tier_boundaries_default():
    """Standard bar -> (3, 300s); tier 2 -> (1, 300s); tier 3 -> (3, 3600s)."""
    assert suppression_tier_for(200) == (3, 300.0)
    assert suppression_tier_for(4999) == (3, 300.0)
    assert suppression_tier_for(5000) == (1, 300.0)
    assert suppression_tier_for(49999) == (1, 300.0)
    assert suppression_tier_for(50000) == (3, 3600.0)


def test_tier_boundaries_configurable(monkeypatch):
    """Fast-time knobs: the boundaries scale for compressed-time runs."""
    monkeypatch.setenv("CASCADE_SUPPRESSION_TIER2_SAMPLES", "300")
    monkeypatch.setenv("CASCADE_SUPPRESSION_TIER3_SAMPLES", "1500")
    assert suppression_tier_for(299) == (3, 300.0)
    assert suppression_tier_for(300) == (1, 300.0)
    assert suppression_tier_for(1500) == (3, 3600.0)


def test_repeat_flood_tier_deepens_suppression():
    """Tier 2 (first-1-per-window) suppresses repeats 2+ where the
    standard tier (first-3) would deliver them."""
    suppressor = RepeatFloodSuppressor(max_repeats=3, window_seconds=300)
    suppressor.add_signal_type("t")
    standard = suppressor.process(_signals(6))
    assert len(standard) == 3  # first 3 pass

    suppressor.set_tier("t", 1, 300)
    assert suppressor.tier_for("t") == (1, 300.0)
    # Fresh source: the tier semantics measured on a clean window.
    deep = suppressor.process(_signals(6, source="s2"))
    assert len(deep) == 5  # only the first passes


def test_repeat_flood_tier_is_per_type():
    """A tiered type does not change behavior for other registered types."""
    suppressor = RepeatFloodSuppressor(max_repeats=3, window_seconds=300)
    suppressor.add_signal_type("t")
    suppressor.add_signal_type("u")
    suppressor.set_tier("t", 1, 300)
    t_decisions = suppressor.process(_signals(4, signal_type="t"))
    u_decisions = suppressor.process(_signals(4, signal_type="u"))
    assert len(t_decisions) == 3
    assert len(u_decisions) == 1  # standard first-3


def test_withdrawal_clears_tier():
    """Removing a type resets its graduated tier: re-adding starts standard."""
    suppressor = RepeatFloodSuppressor()
    suppressor.add_signal_type("t")
    suppressor.set_tier("t", 1, 300)
    suppressor.remove_signal_type("t")
    assert "t" not in suppressor._tiers
    assert suppressor.tier_for("t") == (3, 300.0)


def test_dominant_noise_tier_widens_window():
    """The dominant-noise tier deepens by window: within the standard
    window a tiered type suppresses repeats the default would expire."""
    suppressor = DominantNoiseSuppressor(window_seconds=300)
    suppressor.add_noise_type("t")
    suppressor.set_tier("t", 3600.0)
    first = suppressor.process(_signals(1))
    assert first == []
    second = suppressor.process(_signals(1))
    assert len(second) == 1
    assert second[0].outcome == Outcome.SUPPRESS


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_graduated_tiers_never_touch_high_or_critical(severity):
    """Deeper authority never breaches the fail-open band."""
    repeat = RepeatFloodSuppressor()
    repeat.add_signal_type("t")
    repeat.set_tier("t", 0, 3600)
    dominant = DominantNoiseSuppressor()
    dominant.add_noise_type("t")
    dominant.set_tier("t", 3600.0)
    signals = _signals(10, severity=severity)
    assert repeat.process(signals) == []
    assert dominant.process(signals) == []


def test_tier_zero_is_documented_boundary_not_emitted_by_graduation():
    """max_repeats=0 would suppress every occurrence in the window —
    including the first — leaving shadow validation as the only check.
    The graduation helper never emits it (its floor is first-1); this
    test pins that floor and documents the boundary semantics."""
    assert min(suppression_tier_for(n)[0] for n in (200, 5000, 50000)) == 1
    suppressor = RepeatFloodSuppressor()
    suppressor.add_signal_type("t")
    suppressor.set_tier("t", 0, 300)
    decisions = suppressor.process(_signals(5))
    assert len(decisions) == 5  # everything suppressed: why 0 is out of design
