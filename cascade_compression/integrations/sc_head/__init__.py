"""Cascade SC head server — a fast semantic-classifier replacement.

Serves trained 4-class linear heads over a shared sentence encoder via the
llm-d-sc ClassifyRequest/RankedSignal gRPC contract. Domain-agnostic: the
server routes signal names to per-domain head artifacts (weights + contract
metadata) and contains no domain logic.
"""
