"""Wire and scoring-contract tests for the optional llm-d-sc adapter."""

import math

import pytest

pytest.importorskip("grpc")
pytest.importorskip("google.protobuf")

from cascade_compression.classifier import SemanticClassifierBackend
from cascade_compression.integrations.llm_d_sc import classify_pb2


class Stub:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def Classify(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


def response(revision="taxonomy-v1", *, model_revision="model-v1",
             tokenizer_revision="tokenizer-v1",
             classifier_id="public-test-classifier",
             request_id="request-1", scores=(0.72, 0.32, 0.20, 0.10)):
    return classify_pb2.ClassifyResponse(
        request_id=request_id,
        classifier_id=classifier_id,
        model_revision=model_revision,
        tokenizer_revision=tokenizer_revision,
        taxonomy_revision=revision,
        status=classify_pb2.OK,
        ranked=[
            classify_pb2.RankedSignal(label="needs_attention", score=scores[0]),
            classify_pb2.RankedSignal(label="known_pattern", score=scores[1]),
            classify_pb2.RankedSignal(label="routine_noise", score=scores[2]),
            classify_pb2.RankedSignal(label="real_incident", score=scores[3]),
        ],
    )


def test_adapter_sends_versioned_request_and_returns_ranked_margin():
    stub = Stub(response())
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        expected_taxonomy_revision="taxonomy-v1",
        expected_model_revision="model-v1",
        expected_tokenizer_revision="tokenizer-v1",
        expected_classifier_id="public-test-classifier",
        stub=stub,
    )

    result = backend.classify(
        {"signal_id": "request-1"}, "service_health medium: unusual rate"
    )

    assert result.ok
    assert result.label == "needs_attention"
    assert result.margin == pytest.approx(0.40)
    assert result.taxonomy_revision == "taxonomy-v1"
    assert result.model_revision == "model-v1"
    assert result.scoring_mode == "anchor_cosine"
    assert stub.request.signals == ["cascade_classification"]
    assert stub.timeout == 0.05


def test_adapter_rejects_taxonomy_revision_mismatch():
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        expected_taxonomy_revision="taxonomy-v2",
        stub=Stub(response("taxonomy-v1")),
    )

    result = backend.classify({"signal_id": "request-1"}, "example")

    assert not result.ok
    assert result.error_code == "revision_mismatch"


def test_adapter_rejects_model_revision_mismatch():
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        expected_model_revision="model-v2",
        stub=Stub(response(model_revision="model-v1")),
    )

    result = backend.classify({"signal_id": "request-1"}, "example")

    assert not result.ok
    assert result.error_code == "model_revision_mismatch"


def test_adapter_rejects_response_for_another_request():
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        stub=Stub(response(request_id="different-request")),
    )

    result = backend.classify({"signal_id": "request-1"}, "example")

    assert not result.ok
    assert result.error_code == "request_id_mismatch"


@pytest.mark.parametrize(("kwargs", "error_code"), [
    ({"tokenizer_revision": "other-tokenizer"}, "tokenizer_revision_mismatch"),
    ({"classifier_id": "other-classifier"}, "classifier_id_mismatch"),
])
def test_adapter_rejects_unexpected_runtime_identity(kwargs, error_code):
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        expected_tokenizer_revision="tokenizer-v1",
        expected_classifier_id="public-test-classifier",
        stub=Stub(response(**kwargs)),
    )

    result = backend.classify({"signal_id": "request-1"}, "example")

    assert not result.ok
    assert result.error_code == error_code


@pytest.mark.parametrize("scores", [
    (0.72, 0.32, math.nan, 0.10),
    (0.72, 0.20, 0.32, 0.10),
    (1.20, 0.32, 0.20, 0.10),
])
def test_anchor_mode_rejects_invalid_scores(scores):
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        stub=Stub(response(scores=scores)),
    )

    result = backend.classify({"signal_id": "request-1"}, "example")

    assert not result.ok
    assert result.error_code in {"malformed_scores", "scoring_mode_mismatch"}


def test_probability_mode_accepts_softmax_scores():
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        scoring_mode="classification_head_probability",
        stub=Stub(response(scores=(0.60, 0.25, 0.10, 0.05))),
    )

    result = backend.classify({"signal_id": "request-1"}, "example")

    assert result.ok
    assert result.scoring_mode == "classification_head_probability"
    assert result.margin == pytest.approx(0.35)


def test_probability_mode_rejects_cosine_shaped_scores():
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        scoring_mode="classification_head_probability",
        stub=Stub(response()),
    )

    result = backend.classify({"signal_id": "request-1"}, "example")

    assert not result.ok
    assert result.error_code == "scoring_mode_mismatch"


def test_remote_plaintext_channel_requires_explicit_override():
    with pytest.raises(ValueError, match="requires TLS"):
        SemanticClassifierBackend(
            address="classifier.example:50051",
            signal_name="cascade_classification",
            deadline_seconds=0.05,
        )
