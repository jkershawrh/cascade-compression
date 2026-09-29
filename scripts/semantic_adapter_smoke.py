#!/usr/bin/env python3
"""Prove the optional llm-d-sc gRPC wire client is usable."""

import grpc
from google.protobuf.message import Message

from cascade_compression.integrations.llm_d_sc import classify_pb2, classify_pb2_grpc


def main() -> None:
    request = classify_pb2.ClassifyRequest()
    assert isinstance(request, Message)
    assert classify_pb2_grpc.GRPC_VERSION == grpc.__version__
    assert callable(classify_pb2_grpc.ClassifyStub)
    print("semantic adapter smoke: PASS")


if __name__ == "__main__":
    main()
