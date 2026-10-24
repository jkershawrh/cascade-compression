"""Fast-forward replay harness.

Runs the full cascade (nano tier -> classification -> promotion ->
governance) against a local service with compressed governance time,
using a deterministic model stub in place of an LLM. For understanding
and pre-release soak testing — replay-class evidence only, never a
production claim.
"""
