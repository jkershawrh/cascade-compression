"""Wire-contract tests for the optional llm-d-sc adapter."""

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


def response(revision="taxonomy-v1"):
    return classify_pb2.ClassifyResponse(
        request_id="request-1",
        classifier_id="public-test-classifier",
        model_revision="model-v1",
        tokenizer_revision="tokenizer-v1",
        taxonomy_revision=revision,
        status=classify_pb2.OK,
        ranked=[
            classify_pb2.RankedSignal(label="needs_attention", score=0.72),
            classify_pb2.RankedSignal(label="known_pattern", score=0.32),
            classify_pb2.RankedSignal(label="routine_noise", score=0.20),
            classify_pb2.RankedSignal(label="real_incident", score=0.10),
        ],
    )


def test_adapter_sends_versioned_request_and_returns_ranked_margin():
    stub = Stub(response())
    backend = SemanticClassifierBackend(
        address="unused:50051",
        signal_name="cascade_classification",
        deadline_seconds=0.05,
        expected_taxonomy_revision="taxonomy-v1",
        stub=stub,
    )

    result = backend.classify(
        {"signal_id": "request-1"}, "service_health medium: unusual rate"
    )

    assert result.ok
    assert result.label == "needs_attention"
    assert result.margin == pytest.approx(0.40)
    assert result.taxonomy_revision == "taxonomy-v1"
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


def test_remote_plaintext_channel_requires_explicit_override():
    with pytest.raises(ValueError, match="requires TLS"):
        SemanticClassifierBackend(
            address="classifier.example:50051",
            signal_name="cascade_classification",
            deadline_seconds=0.05,
        )
