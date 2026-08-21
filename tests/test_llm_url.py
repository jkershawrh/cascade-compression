"""The chat-completions URL must tolerate a base with or without /v1.

Deployment docs publish the gateway form (https://host/v1) while the local
Ollama form is https://host. Appending /v1/chat/completions unconditionally
produced /v1/v1/chat/completions and a 404 for every documented deploy command.
"""

import pytest

from cascade_compression.cascade.protocol import chat_completions_url


@pytest.mark.parametrize("base,expected", [
    # Bare host — the Ollama form used by demo scripts
    ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
    # Gateway form — what the README and openshift.yaml tell operators to set
    ("https://gateway.example.com/v1", "https://gateway.example.com/v1/chat/completions"),
    # Trailing slashes must not produce a doubled separator
    ("http://localhost:11434/", "http://localhost:11434/v1/chat/completions"),
    ("https://gateway.example.com/v1/", "https://gateway.example.com/v1/chat/completions"),
    # In-cluster service DNS with a port
    ("http://litellm.ns.svc:4000", "http://litellm.ns.svc:4000/v1/chat/completions"),
    ("http://litellm.ns.svc:4000/v1", "http://litellm.ns.svc:4000/v1/chat/completions"),
])
def test_accepts_both_documented_forms(base, expected):
    assert chat_completions_url(base) == expected


def test_never_doubles_the_version_segment():
    for base in ("https://h/v1", "https://h/v1/", "https://h"):
        assert "/v1/v1/" not in chat_completions_url(base)


def test_path_prefixed_gateway_is_preserved():
    """A gateway mounted under a path keeps that path."""
    assert chat_completions_url("https://api.example.com/llm/v1") == (
        "https://api.example.com/llm/v1/chat/completions"
    )


def test_v1_inside_a_hostname_is_not_stripped():
    """Only a trailing /v1 path segment is trimmed, not a lookalike host."""
    assert chat_completions_url("https://v1.example.com") == (
        "https://v1.example.com/v1/chat/completions"
    )
