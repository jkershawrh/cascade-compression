"""Truth-informed few-shot exemplars: prompt augmentation and wiring."""

import pytest

from cascade_compression.classifier import GenerativeClassifierBackend, augment_with_exemplars
from cascade_compression.domain_plugins import load_domain_plugin


EXEMPLARS = {
    "known_pattern": ["A pool reached its minimum under load"],
    "needs_attention": ["A volume is bound to a non-existent node"],
    "routine_noise": ["Health check reports healthy"],
}


def test_augment_adds_exemplars():
    base = "Classify signals."
    augmented = augment_with_exemplars(base, EXEMPLARS)
    assert "Classify signals." in augmented
    assert "known_pattern:" in augmented
    assert "A pool reached its minimum" in augmented
    assert "prefer known_pattern or needs_attention" in augmented


def test_augment_empty_is_identity():
    assert augment_with_exemplars("prompt", {}) == "prompt"
    assert augment_with_exemplars("prompt", None) == "prompt"


def test_augment_changes_prompt_hash():
    """The prompt revision must change when exemplars change — measurements
    stay bound to their prompt."""
    base = GenerativeClassifierBackend(
        url="http://x", key="k", micro_model="m", macro_model="m",
        system_prompt="Classify signals.")
    augmented = GenerativeClassifierBackend(
        url="http://x", key="k", micro_model="m", macro_model="m",
        system_prompt="Classify signals.", few_shot_exemplars=EXEMPLARS)
    assert base.prompt_revision != augmented.prompt_revision


def test_kubernetes_plugin_carries_exemplars():
    plugin = load_domain_plugin("kubernetes")
    exemplars = plugin.few_shot_exemplars
    assert exemplars is not None
    for label in ("routine_noise", "known_pattern", "needs_attention", "real_incident"):
        assert label in exemplars
        assert len(exemplars[label]) >= 2
    # The prompts carry concrete platform semantics, not generic phrases
    all_text = " ".join(e for exs in exemplars.values() for e in exs)
    assert "volume" in all_text or "pool" in all_text or "sandbox" in all_text


def test_augmented_prompt_preserves_label_definitions():
    """The base prompt's label definitions must survive augmentation."""
    base = "Classify: routine_noise means X. known_pattern means Y."
    augmented = augment_with_exemplars(base, EXEMPLARS)
    assert "routine_noise means X" in augmented
    assert "known_pattern means Y" in augmented
