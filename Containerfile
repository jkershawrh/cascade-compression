FROM registry.access.redhat.com/ubi9/python-311@sha256:a0bdb55576fc5b8d6704279307817828ef027e1065533ceba133fe9516003a6c AS builder

WORKDIR /opt/app-root/src

COPY requirements-build.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-build.lock

COPY pyproject.toml README.md LICENSE ./
COPY cascade_compression/ cascade_compression/
COPY config/ config/
COPY data/ data/
COPY frontend/ frontend/
COPY contracts/ contracts/

RUN pip wheel --no-cache-dir --no-deps --no-build-isolation \
    --wheel-dir /tmp/wheels .

FROM registry.access.redhat.com/ubi9/python-311@sha256:a0bdb55576fc5b8d6704279307817828ef027e1065533ceba133fe9516003a6c

WORKDIR /opt/app-root/src

COPY requirements-container.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-container.lock

COPY --chown=1001:0 --from=builder /tmp/wheels/*.whl /tmp/wheels/
COPY --chown=1001:0 scripts/semantic_adapter_smoke.py scripts/semantic_adapter_smoke.py
RUN pip install --no-cache-dir --no-deps /tmp/wheels/*.whl
RUN python scripts/semantic_adapter_smoke.py

EXPOSE 8090

USER 1001

CMD ["uvicorn", "cascade_compression.service:app", "--host", "0.0.0.0", "--port", "8090"]
