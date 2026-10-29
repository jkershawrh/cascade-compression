"""Tests for the sc-head server: artifact validation and contract behavior.

The head servicer is tested with an injected stub encoder so the suite has
no sentence-transformers dependency. What is under test is the contract the
engine's SemanticClassifierBackend enforces: softmax scores over the four
Cascade labels, sorted descending, summing to 1.0, with revisions echoing
the artifact contract.
"""
import json
import os
import tempfile

import pytest

grpc = pytest.importorskip("grpc")

from cascade_compression.integrations.llm_d_sc import (  # noqa: E402
    classify_pb2,
)
from cascade_compression.integrations.sc_head.server import (  # noqa: E402
    CASCADE_LABELS,
    HeadServicer,
    load_heads,
)

DIM = 8


def write_artifact(directory, signal="cascade-test", weights=None, bias=None,
                   temperature=1.0, contract=None):
    weights = weights or [[0.1] * DIM for _ in CASCADE_LABELS]
    bias = bias or [0.0] * len(CASCADE_LABELS)
    contract = contract or {
        "classifier_id": "cascade-signal-classifier",
        "signal": signal,
        "taxonomy_revision": f"{signal}-head-v1",
        "model_revision": "test-encoder-v1",
        "method": "classification-head",
        "labels": CASCADE_LABELS,
    }
    artifact = {
        "contract": contract,
        "temperature": temperature,
        "classes": CASCADE_LABELS,
        "dim": DIM,
        "weights": weights,
        "bias": bias,
    }
    path = os.path.join(directory, f"{signal}.json")
    with open(path, "w") as f:
        json.dump(artifact, f)
    return path


class StubEncoder:
    """Deterministic encoder: hashes the text into a fixed unit vector."""

    def __init__(self, dim=DIM):
        self.dim = dim

    def encode(self, texts, **kwargs):
        import hashlib

        import numpy as np

        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            v = np.frombuffer(h[: self.dim], dtype=np.uint8).astype(float)
            v = v / max(1e-9, np.linalg.norm(v))
            out.append(v)
        return out


class StubContext:
    def __init__(self):
        self.aborted = None

    def abort(self, code, message):
        self.aborted = (code, message)
        raise grpc.RpcError()


@pytest.fixture
def heads_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_load_heads_valid(heads_dir):
    write_artifact(heads_dir, signal="cascade-test")
    heads, contracts = load_heads(heads_dir)
    assert "cascade-test" in heads
    assert contracts["cascade-test"]["taxonomy_revision"] == "cascade-test-head-v1"


def test_load_heads_rejects_malformed_classes(heads_dir):
    path = write_artifact(heads_dir, signal="cascade-bad")
    with open(path) as f:
        artifact = json.load(f)
    artifact["classes"] = ["", "x", "y"]  # empty class string
    with open(path, "w") as f:
        json.dump(artifact, f)
    with pytest.raises(ValueError, match="classes"):
        load_heads(heads_dir)


def test_classify_contract(heads_dir):
    # bias the head so one class wins deterministically
    weights = [[0.0] * DIM for _ in CASCADE_LABELS]
    bias = [0.0, 0.0, 3.0, 0.0]  # needs_attention wins
    write_artifact(heads_dir, signal="cascade-test",
                   weights=weights, bias=bias, temperature=0.75)
    heads, contracts = load_heads(heads_dir)
    servicer = HeadServicer(StubEncoder(), heads, contracts)

    request = classify_pb2.ClassifyRequest(
        request_id="req-1", session_id="", context="probe failed",
        signals=["cascade-test"])
    response = servicer.Classify(request, StubContext())

    assert response.request_id == "req-1"
    assert response.status == classify_pb2.OK
    assert response.classifier_id == "cascade-signal-classifier"
    assert response.taxonomy_revision == "cascade-test-head-v1"
    assert response.model_revision == "test-encoder-v1"
    # all four labels present, sorted descending
    labels = [r.label for r in response.ranked]
    assert sorted(labels) == sorted(CASCADE_LABELS)
    scores = [r.score for r in response.ranked]
    assert scores == sorted(scores, reverse=True)
    # softmax property the engine validates (sum ~ 1.0)
    assert abs(sum(scores) - 1.0) < 0.02
    assert all(0.0 <= s <= 1.0 for s in scores)
    # the biased class wins with a clear margin
    assert response.ranked[0].label == "needs_attention"
    assert scores[0] - scores[1] > 0.5


def test_classify_rejects_unknown_signal(heads_dir):
    write_artifact(heads_dir, signal="cascade-test")
    heads, contracts = load_heads(heads_dir)
    servicer = HeadServicer(StubEncoder(), heads, contracts)
    request = classify_pb2.ClassifyRequest(
        request_id="req-2", session_id="", context="x",
        signals=["cascade-unknown"])
    with pytest.raises(grpc.RpcError):
        servicer.Classify(request, StubContext())


def test_scores_are_temperature_scaled(heads_dir):
    weights = [[0.5] * DIM for _ in CASCADE_LABELS]
    bias = [0.0, 0.0, 1.0, 0.0]
    write_artifact(heads_dir, signal="cascade-hot",
                   weights=weights, bias=bias, temperature=0.1)
    write_artifact(heads_dir, signal="cascade-cold",
                   weights=weights, bias=bias, temperature=5.0)
    heads, contracts = load_heads(heads_dir)
    servicer = HeadServicer(StubEncoder(), heads, contracts)
    request = classify_pb2.ClassifyRequest(
        request_id="r", session_id="", context="same text",
        signals=["cascade-hot"])
    hot = servicer.Classify(request, StubContext())
    request.signals[:] = ["cascade-cold"]
    cold = servicer.Classify(request, StubContext())
    hot_margin = hot.ranked[0].score - hot.ranked[1].score
    cold_margin = cold.ranked[0].score - cold.ranked[1].score
    # low temperature sharpens: hot margins exceed cold margins
    assert hot_margin > cold_margin


def test_fact_head_artifacts_are_hostable(heads_dir):
    # fact heads declare their own class vocabularies (designed/impact/
    # recovery); the server hosts them, consumers enforce their own
    # expected label sets client-side
    fact_classes = ["false", "true"]
    weights = [[0.1] * DIM for _ in fact_classes]
    bias = [0.0, 1.0]
    contract = {
        "classifier_id": "cascade-signal-classifier",
        "signal": "cascade-kubernetes-designed",
        "taxonomy_revision": "cascade-kubernetes-designed-fact-v1",
        "model_revision": "all-MiniLM-L6-v2@main+fact-head-v1",
        "method": "classification-head",
        "labels": fact_classes,
    }
    artifact = {"contract": contract, "temperature": 1.0,
                "classes": fact_classes, "dim": DIM,
                "weights": weights, "bias": bias}
    import os
    with open(os.path.join(heads_dir, "cascade-kubernetes-designed.json"), "w") as f:
        json.dump(artifact, f)
    heads, contracts = load_heads(heads_dir)
    assert "cascade-kubernetes-designed" in heads
    assert contracts["cascade-kubernetes-designed"]["labels"] == fact_classes


def test_malformed_classes_rejected(heads_dir):
    path = write_artifact(heads_dir, signal="cascade-dup")
    with open(path) as f:
        artifact = json.load(f)
    artifact["classes"] = ["same", "same"]  # duplicates
    with open(path, "w") as f:
        json.dump(artifact, f)
    with pytest.raises(ValueError, match="unique"):
        load_heads(heads_dir)
