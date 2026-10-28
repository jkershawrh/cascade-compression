"""Tests for the sc-head trainer: calibration and weight mapping."""
import numpy as np
import pytest

from cascade_compression.integrations.sc_head.train import (
    CASCADE_LABELS,
    fit_temperature,
    head_weights,
    reindex_probabilities,
)


class StubClassifier:
    """Mimics sklearn's classes_/coef_/intercept_ surface."""

    def __init__(self, classes, coef, intercept):
        self.classes_ = np.array(classes)
        self.coef_ = np.asarray(coef)
        self.intercept_ = np.asarray(intercept)


def test_fit_temperature_prefers_sharper_when_confident():
    # confident, correct predictions: T < 1 should win
    probs = np.array([[0.95, 0.03, 0.01, 0.01]] * 50)
    y = np.zeros(50, dtype=int)
    t = fit_temperature(probs, y)
    assert t <= 1.0


def test_fit_temperature_softens_when_overconfident():
    # overconfident wrong predictions: T > 1 should win
    probs = np.array([[0.95, 0.03, 0.01, 0.01]] * 50)
    y = np.ones(50, dtype=int)
    t = fit_temperature(probs, y)
    assert t >= 1.0


def test_reindex_probabilities_fills_missing_classes():
    raw = np.array([[0.7, 0.3]])
    classes = np.array([0, 2])  # routine_noise, needs_attention present
    probs = reindex_probabilities(raw, classes)
    assert probs.shape == (1, 4)
    assert probs[0][0] == pytest.approx(0.7)
    assert probs[0][2] == pytest.approx(0.3)
    assert probs[0][1] < 1e-6  # known_pattern absent from the fit


def test_head_weights_multiclass_rows_map_by_index():
    dim = 3
    clf = StubClassifier(
        classes=[0, 1, 3],
        coef=[[1, 1, 1], [2, 2, 2], [4, 4, 4]],
        intercept=[0.1, 0.2, 0.4],
    )
    W, b = head_weights(clf, dim)
    assert W.shape == (4, dim)
    assert np.allclose(W[0], [1, 1, 1])
    assert np.allclose(W[1], [2, 2, 2])
    assert np.allclose(W[2], 0)  # needs_attention absent
    assert np.allclose(W[3], [4, 4, 4])
    assert b[3] == pytest.approx(0.4)


def test_head_weights_binary_expands_single_row():
    dim = 2
    clf = StubClassifier(
        classes=[0, 3],
        coef=[[1.5, -0.5]],
        intercept=[0.25],
    )
    W, b = head_weights(clf, dim)
    # class 3 is the positive side of the discriminator
    assert np.allclose(W[3], [1.5, -0.5])
    assert np.allclose(W[0], [-1.5, 0.5])
    assert b[3] == pytest.approx(0.25)
    assert b[0] == pytest.approx(-0.25)
    # the middle classes stay neutral
    assert np.allclose(W[1], 0)
    assert np.allclose(W[2], 0)


def test_label_vocabulary_is_engine_vocabulary():
    assert CASCADE_LABELS == [
        "routine_noise", "known_pattern", "needs_attention", "real_incident"]
