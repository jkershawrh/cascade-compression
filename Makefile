.PHONY: test test-cascade test-routing test-infra test-tco test-all up

PYTHON ?= python3

## ── Memory tests ───────────────────────────────────────────────────
test-memory:
	$(PYTHON) -m pytest tests/test_memory.py tests/test_memory_contracts.py tests/test_recall.py tests/test_consolidation.py tests/test_priming.py tests/test_federation.py -v

## ── Cascade engine tests ────────────────────────────────────────────
test-cascade:
	$(PYTHON) -m pytest tests/test_cascade.py tests/test_cascade_safety.py tests/test_promotion.py -v

## ── Routing tests ───────────────────────────────────────────────────
test-routing:
	$(PYTHON) -m pytest tests/test_corpora.py tests/test_strategy_router.py tests/test_bootstrapper.py tests/test_task_mapping.py tests/test_synthetic_routing.py -v

## ── Infrastructure tests ────────────────────────────────────────────
test-infra:
	$(PYTHON) -m pytest tests/test_scaler.py tests/test_fleet_manager.py -v

## ── TCO calculator tests ────────────────────────────────────────────
test-contracts:
	$(PYTHON) -m pytest tests/test_contracts.py -v

test-calculations:
	$(PYTHON) -m pytest tests/test_calculations.py -v

test-scenarios:
	$(PYTHON) -m pytest tests/test_scenarios.py -v

test-api:
	$(PYTHON) -m pytest tests/test_api.py -v

test-tco:
	$(PYTHON) -m pytest tests/test_contracts.py tests/test_calculations.py tests/test_scenarios.py tests/test_api.py -v

## ── All tests ───────────────────────────────────────────────────────
test-all:
	$(PYTHON) -m pytest tests/ -v

## ── Run the app ─────────────────────────────────────────────────────
up:
	uvicorn cascade_compression.tco.api:app --host 0.0.0.0 --port 8090 --reload

## ── Benchmark targets ───────────────────────────────────────────────
## Set BENCH_REGISTRY and BENCH_NAMESPACE for your own cluster, e.g.
##   make bench-push BENCH_REGISTRY=$$(oc registry info) BENCH_NAMESPACE=my-ns
BENCH_REGISTRY ?= $(shell oc registry info 2>/dev/null)
BENCH_NAMESPACE ?= cascade-benchmarks
BENCH_IMAGE ?= $(BENCH_REGISTRY)/$(BENCH_NAMESPACE)/cascade-benchmark-harness:latest

bench-build:
	podman build -f benchmarks/Containerfile -t $(BENCH_IMAGE) --platform linux/amd64 .

bench-push:
	podman login $(BENCH_REGISTRY) -u $$(oc whoami) -p $$(oc whoami -t)
	podman push $(BENCH_IMAGE)

bench-setup:
	oc apply -f benchmarks/k8s/results-pvc.yaml

bench-run-all:
	@for f in benchmarks/k8s/job-*.yaml; do \
		name=$$(grep "name:" "$$f" | head -1 | awk '{print $$2}'); \
		oc delete job "$$name" -n $(BENCH_NAMESPACE) --ignore-not-found; \
		oc apply -f "$$f"; \
	done
	@echo "All jobs submitted. Watch with: make bench-status"

bench-status:
	oc get jobs -n $(BENCH_NAMESPACE) -l app=benchmark-harness

bench-results:
	mkdir -p benchmarks/results
	oc rsync $$(oc get pod -n $(BENCH_NAMESPACE) -l app=benchmark-harness --field-selector=status.phase=Succeeded -o jsonpath='{.items[-1].metadata.name}'):/results/ benchmarks/results/ -n $(BENCH_NAMESPACE)

bench-report:
	@for f in benchmarks/results/benchmark-*.json; do \
		python3 -m cascade_compression.benchmarks.rubric "$$f"; \
	done
