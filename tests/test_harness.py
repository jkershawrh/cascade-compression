"""Fast-forward harness: model stub and replay mapping."""

import json

from cascade_compression.harness.model_stub import (
    IMPORTANT_MARKER, completion_body, label_for_payload,
)
from cascade_compression.harness.replay import injection_signal, to_signal

CAPTURE_LINE = {
    "count": 4,
    "event_uid": "c0e5eb33-9986-4b88-803b-3d61a8f61126",
    "kind": "Pod",
    "message": "Failed to pull image",
    "message_sha256": "8dcc4272...",
    "name": "automation-job-284189-ds955",
    "namespace": "aap2-prod0",
    "poll_index": 1,
    "reason": "Failed",
    "timestamp": "2026-10-21T13:50:33Z",
    "type": "event",
    "uid": "1e7bb191-00c7-4292-94ce-515125ca663d",
}


def test_stub_labels_marker_as_real_incident():
    payload = {"messages": [
        {"role": "user", "content": f"classify: {IMPORTANT_MARKER} probe"},
    ]}
    assert label_for_payload(payload) == "real_incident"


def test_stub_defaults_to_routine_noise():
    payload = {"messages": [
        {"role": "user", "content": "classify: Failed to pull image"},
    ]}
    assert label_for_payload(payload) == "routine_noise"


def test_stub_response_shape_matches_llm_complete_contract():
    """The engine reads choices[0].message.content — the stub must serve it."""
    payload = {"model": "stub", "messages": []}
    body = completion_body(payload, label_for_payload(payload))
    assert body["choices"][0]["message"]["content"] == "routine_noise"


def test_replay_mapping_matches_collector_semantics():
    """event records map exactly like the k8s collector: event_{reason},
    severity medium, identity envelope preserved in content."""
    signal = to_signal(CAPTURE_LINE)
    assert signal["signal_type"] == "event_failed"
    assert signal["severity"] == "medium"
    assert signal["source"] == "automation-job-284189-ds955"
    assert signal["namespace"] == "aap2-prod0"
    content = signal["content"]
    assert content["event_uid"] == CAPTURE_LINE["event_uid"]
    assert content["uid"] == CAPTURE_LINE["uid"]
    assert content["count"] == 4
    assert content["message_sha256"] == CAPTURE_LINE["message_sha256"]
    assert content["kind"] == "Pod"
    assert content["reason"] == "Failed"


def test_injection_signal_carries_marker():
    probe = injection_signal("event_failedscheduling")
    assert IMPORTANT_MARKER in probe["content"]["message"]
    assert probe["signal_type"] == "event_failedscheduling"
    # Unique event_uid per probe so it is never deduped away.
    again = injection_signal("event_failedscheduling")
    assert probe["content"]["event_uid"] != again["content"]["event_uid"]
