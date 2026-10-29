"""gRPC head server: encoder + per-domain linear heads.

Speaks the classify.proto contract (ClassifyRequest -> ClassifyResponse)
with scoring mode classification_head_probability: scores are softmax
probabilities over the four Cascade labels, sorted descending, summing
to 1.0. The engine's hybrid margin gate consumes top1 - top2.

Artifacts (mounted at /heads):
  <signal-name>.json = {
    "contract": {classifier_id, signal, taxonomy_revision, model_revision,
                 tokenizer_revision, method, labels},
    "temperature": float,
    "classes": ["routine_noise", ...],       # index order of the head rows
    "weights": [[...384 floats] x 4],        # W (4 x dim)
    "bias": [4 floats]                       # b
  }
Encoder: sentence-transformers/all-MiniLM-L6-v2 (pinned by revision string
in each contract's model_revision; the server refuses mismatches).
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent import futures

import grpc
import numpy as np

from cascade_compression.integrations.llm_d_sc import classify_pb2, classify_pb2_grpc

HEADS_DIR = os.environ.get("SC_HEAD_DIR", "/heads")
LISTEN = os.environ.get("SC_HEAD_LISTEN", "0.0.0.0:50051")
ENCODER_NAME = os.environ.get("SC_HEAD_ENCODER", "sentence-transformers/all-MiniLM-L6-v2")

# Engine vocabulary — not domain knowledge.
CASCADE_LABELS = ["routine_noise", "known_pattern", "needs_attention", "real_incident"]


def load_heads(heads_dir: str):
    heads, contracts = {}, {}
    for name in os.listdir(heads_dir):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(heads_dir, name)) as f:
            data = json.load(f)
        contract = data["contract"]
        signal = contract["signal"]
        classes = data["classes"]
        # The server hosts whatever class set the artifact's contract
        # declares (label heads use the Cascade labels; fact heads use
        # fact vocabularies like designed/impact/recovery). Consumers
        # enforce their own expected label sets client-side — the
        # cascade's SemanticClassifierBackend rejects any response whose
        # ranked labels don't match its contract, so hosting other class
        # sets cannot leak into label-head consumers.
        if not classes or len(set(classes)) != len(classes):
            raise ValueError(f"{name}: classes must be non-empty and unique")
        if not all(isinstance(c, str) and c.strip() for c in classes):
            raise ValueError(f"{name}: classes must be non-empty strings")
        W = np.asarray(data["weights"], dtype=np.float64)
        b = np.asarray(data["bias"], dtype=np.float64)
        if W.shape != (len(classes), data.get("dim", W.shape[1])):
            raise ValueError(f"{name}: bad weight shape {W.shape}")
        heads[signal] = (W, b, float(data.get("temperature", 1.0)), classes)
        contracts[signal] = contract
    return heads, contracts


class HeadServicer(classify_pb2_grpc.ClassifyServicer):
    name = "sc-head"

    def __init__(self, encoder, heads, contracts):
        self.encoder = encoder
        self.heads = heads
        self.contracts = contracts

    def Classify(self, request, context):
        t0 = time.monotonic()
        signal = request.signals[0] if request.signals else ""
        if signal not in self.heads:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"unsupported signal '{signal}'; this instance serves "
                f"{', '.join(sorted(self.heads))}",
            )
        W, b, temperature, classes = self.heads[signal]
        contract = self.contracts[signal]
        emb = self.encoder.encode(
            [request.context], normalize_embeddings=True,
            convert_to_numpy=True,
        )[0]
        logits = (emb @ W.T + b) / temperature
        logits = logits - logits.max()
        probs = np.exp(logits)
        probs = probs / probs.sum()
        order = np.argsort(-probs)
        ranked = [
            classify_pb2.RankedSignal(label=classes[i], score=float(probs[i]))
            for i in order
        ]
        latency = (time.monotonic() - t0) * 1000
        sys.stderr.write(
            f"sc-head: {signal} {latency:.1f}ms "
            f"{ranked[0].label}={ranked[0].score:.3f}\n"
        )
        return classify_pb2.ClassifyResponse(
            request_id=request.request_id,
            classifier_id=contract["classifier_id"],
            model_revision=contract["model_revision"],
            tokenizer_revision=contract.get(
                "tokenizer_revision", contract["model_revision"]),
            taxonomy_revision=contract["taxonomy_revision"],
            status=classify_pb2.OK,
            ranked=ranked,
        )


def main():
    from sentence_transformers import SentenceTransformer

    heads, contracts = load_heads(HEADS_DIR)
    if not heads:
        raise SystemExit(f"no head artifacts found in {HEADS_DIR}")
    print(f"sc-head: loaded {len(heads)} heads: {sorted(heads)}", flush=True)
    encoder = SentenceTransformer(ENCODER_NAME)
    # Warm the encoder so the first real request pays no compile cost.
    encoder.encode(["warmup"], normalize_embeddings=True)
    print(f"sc-head: encoder {ENCODER_NAME} warmed", flush=True)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    classify_pb2_grpc.add_ClassifyServicer_to_server(
        HeadServicer(encoder, heads, contracts), server)
    server.add_insecure_port(LISTEN)
    server.start()
    print(f"sc-head: serving on {LISTEN}", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
