from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.classifier import ClassificationResult
from cascade_compression.memory_verifier import (
    BlendedMemorySuppressionPolicy,
    VELA_QUESTION_REVISION,
    VelaNoulMemoryVerifier,
)
from tests.helpers import make_signal


class StubVela:
    def __init__(self, probability=0.99, response=None, error=None):
        self.probability = probability
        self.response = response
        self.error = error
        self.calls = 0

    def system_one(self, request, questions):
        self.calls += 1
        if self.error:
            raise self.error
        if self.response is not None:
            return self.response
        return {"answers": {"safe": {"noul": self.probability}}}


def admitted_memory():
    archive = MemoryArchive()
    memory = archive.store(make_signal(severity="critical"))
    archive.verify_memory(
        memory.memory_id,
        outcome="benign",
        source="independent-review",
        policy_revision="memory-policy-v1",
        evidence_ref="review:public-case-1",
    )
    return memory


def semantic(label="known_pattern", status="ok"):
    return ClassificationResult(
        label=label,
        status=status,
        backend="semantic",
        authoritative_backend="semantic",
        margin=0.10,
        model_revision="public-semantic-model",
        taxonomy_revision="public-taxonomy-v1",
        classifier_id="public-classifier",
        scoring_mode="anchor_cosine",
    )


def signal(severity="low"):
    return {
        "signal_id": "public-test-signal",
        "signal_type": "service_health",
        "severity": severity,
        "namespace": "example",
        "content": {"message": "Repeated probe recovered without impact"},
    }


def policy(model=None, threshold=0.95):
    model = model or StubVela()
    verifier = VelaNoulMemoryVerifier(model, model_revision="public-vela-revision")
    return BlendedMemorySuppressionPolicy(verifier, threshold=threshold), model


def test_blend_supports_only_admitted_memory_with_high_verifier_probability():
    blended, model = policy()
    assessed = blended.assess(
        signal=signal(), semantic=semantic(), memory=admitted_memory(),
        exact_match=True, evidence_text="Approved observation self-resolved without impact.",
    )
    assert assessed.supported
    assert assessed.reason == "verified_memory_support"
    assert assessed.safe_probability == 0.99
    assert assessed.semantic_model_revision == "public-semantic-model"
    assert assessed.semantic_taxonomy_revision == "public-taxonomy-v1"
    assert assessed.semantic_classifier_id == "public-classifier"
    assert assessed.semantic_scoring_mode == "anchor_cosine"
    assert assessed.memory_policy_revision == "memory-policy-v1"
    assert assessed.memory_evidence_ref == "review:public-case-1"
    assert len(assessed.evidence_digest) == 64
    assert assessed.verifier_model_revision == "public-vela-revision"
    assert assessed.question_revision == VELA_QUESTION_REVISION
    assert assessed.verifier_threshold == 0.95
    assert assessed.verifier_latency_ms >= 0.0
    assert model.calls == 1


def test_blend_rejects_unverified_or_non_exact_memory_without_calling_model():
    blended, model = policy()
    archive = MemoryArchive()
    unverified = archive.store(make_signal(severity="low"))
    assert not blended.assess(
        signal=signal(), semantic=semantic(), memory=unverified,
        exact_match=True, evidence_text="Claims benign.",
    ).supported
    assert not blended.assess(
        signal=signal(), semantic=semantic(), memory=admitted_memory(),
        exact_match=False, evidence_text="Claims benign.",
    ).supported
    assert model.calls == 0


def test_blend_rejects_high_severity_before_calling_model():
    blended, model = policy()
    assessed = blended.assess(
        signal=signal("critical"), semantic=semantic(), memory=admitted_memory(),
        exact_match=True, evidence_text="Claims benign.",
    )
    assert not assessed.supported
    assert assessed.reason == "severity_gated"
    assert model.calls == 0


def test_blend_rejects_non_suppressive_or_failed_semantic_candidate():
    blended, model = policy()
    for result in (semantic("needs_attention"), semantic("known_pattern", "error")):
        assert not blended.assess(
            signal=signal(), semantic=result, memory=admitted_memory(),
            exact_match=True, evidence_text="Claims benign.",
        ).supported
    assert model.calls == 0


def test_blend_rejects_unversioned_semantic_candidate():
    blended, model = policy()
    candidate = semantic()
    candidate.model_revision = ""
    assessed = blended.assess(
        signal=signal(), semantic=candidate, memory=admitted_memory(),
        exact_match=True, evidence_text="Claims benign.",
    )
    assert not assessed.supported
    assert assessed.reason == "semantic_unversioned"
    assert model.calls == 0


def test_blend_rejects_low_verifier_probability():
    blended, model = policy(StubVela(probability=0.949))
    assessed = blended.assess(
        signal=signal(), semantic=semantic(), memory=admitted_memory(),
        exact_match=True, evidence_text="Ambiguous operational outcome.",
    )
    assert not assessed.supported
    assert assessed.reason == "verifier_below_threshold"
    assert model.calls == 1


def test_verifier_malformed_or_failed_response_fails_closed():
    cases = [
        StubVela(response={"answers": {}}),
        StubVela(probability=float("nan")),
        StubVela(error=RuntimeError("temporarily unavailable")),
    ]
    for model in cases:
        blended, _ = policy(model)
        assessed = blended.assess(
            signal=signal(), semantic=semantic(), memory=admitted_memory(),
            exact_match=True, evidence_text="Claims benign.",
        )
        assert not assessed.supported
        assert assessed.reason.startswith("verifier_")


def test_missing_evidence_fails_before_model_call():
    blended, model = policy()
    assessed = blended.assess(
        signal=signal(), semantic=semantic(), memory=admitted_memory(),
        exact_match=True, evidence_text=" ",
    )
    assert not assessed.supported
    assert assessed.reason == "missing_evidence"
    assert model.calls == 0


def test_threshold_cannot_be_lowered_below_evaluated_floor():
    verifier = VelaNoulMemoryVerifier(
        StubVela(), model_revision="public-vela-revision",
    )
    try:
        BlendedMemorySuppressionPolicy(verifier, threshold=0.949)
    except ValueError as exc:
        assert "0.95" in str(exc)
    else:
        raise AssertionError("unsafe verifier threshold was accepted")
