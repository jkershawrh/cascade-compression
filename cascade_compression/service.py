"""Standalone cascade compression service.

One container. Point it at a signal stream and an LLM. It discovers
what is noise, validates it, and compresses your signal volume.

    oc new-app https://github.com/jkershawrh/cascade-compression \
      -e CASCADE_LLM_URL=https://your-llm/v1 \
      -e CASCADE_LLM_KEY=sk-...

Environment variables:
    CASCADE_LLM_URL          LLM API base URL (required for agent discovery)
    CASCADE_LLM_KEY          LLM API key
    CASCADE_LLM_MODEL        LLM model name (deployment-specific, no default)
    CASCADE_MICRO_MODEL      Model for micro tier / medium severity (default: CASCADE_LLM_MODEL)
    CASCADE_MACRO_MODEL      Model for macro tier / high+critical severity (default: CASCADE_LLM_MODEL)
    CASCADE_DOMAIN           Domain pack (default: kubernetes)
    CASCADE_LEDGER_URL       Immutable ledger URL (optional, for governance)
    CASCADE_LEDGER_TOKEN     Ledger bearer token
    CASCADE_SHADOW_RATE      Shadow validation sample rate (default: 0.05)
    CASCADE_ACTIVATION_TTL_HOURS  Agent TTL in hours (default: 72)
    CASCADE_HUMAN_GATE       Set to 1 to require human approval before activation
    CASCADE_STATE_FILE       Path to persist agent state across restarts
"""

import logging
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, model_validator
from .cascade.protocol import severity_input_is_valid

from .biography import generate_biography, _compute_health_score
from .bridge import CascadeBridge
from .cascade.memory import MemoryArchive
from .cascade.inverse import SuppressionArchive, inverse_analysis, export_learned_agents
from .cascade.memory_intelligence import MemoryIntelligence
from .cascade.recall import RecallEngine
from .memory_search import (
    MemorySearchEngine, _available as _search_available,
    _unavailable_reason as _search_unavailable_reason,
)
from .domain_plugins import DomainPluginError, load_domain_plugin
from .resources import resource_dir

log = logging.getLogger(__name__)

_bridge: CascadeBridge = None
_memory_archive: MemoryArchive = None
_memory_intel: MemoryIntelligence = None
_search_engine: MemorySearchEngine = None


def _load_prompt(domain: str) -> str:
    try:
        return load_domain_plugin(domain).system_prompt
    except DomainPluginError:
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bridge, _memory_archive, _memory_intel
    domain = os.getenv("CASCADE_DOMAIN", "kubernetes")
    _bridge = CascadeBridge(
        llm_url=os.getenv("CASCADE_LLM_URL", ""),
        llm_key=os.getenv("CASCADE_LLM_KEY", ""),
        llm_model=os.getenv("CASCADE_LLM_MODEL", ""),
        system_prompt=_load_prompt(domain),
        domain=domain,
        ledger_url=os.getenv("CASCADE_LEDGER_URL", ""),
        ledger_token=os.getenv("CASCADE_LEDGER_TOKEN", ""),
    )
    _memory_archive = _bridge.memory_archive
    _memory_intel = MemoryIntelligence()
    try:
        domain_config = load_domain_plugin(domain).memory_config
        if domain_config:
            _memory_intel.register_domain(domain, domain_config)
            if _memory_archive:
                _memory_archive.set_decay_config(_memory_intel.decay_config)
            log.info("Registered memory config for domain=%s", domain)
    except DomainPluginError:
        pass
    # Optional: connect pgvector search engine
    global _search_engine
    db_url = os.getenv("CASCADE_SEARCH_DB_URL", "")
    if db_url and _search_available():
        try:
            _search_engine = MemorySearchEngine()
            _search_engine.connect(db_url)
            _search_engine.ensure_schema()
            log.info("pgvector search engine connected")
        except Exception as e:
            log.warning("pgvector search engine failed to connect: %s", e)
            _search_engine = None

    log.info("Cascade compression ready (domain=%s)", domain)
    yield
    if _bridge:
        _bridge.close()
    if _search_engine:
        _search_engine.close()


app = FastAPI(
    title="Cascade Compression",
    version="1.0.0",
    description="Self-tuning signal compression — discover what needs AI and what does not.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SignalInput(BaseModel):
    signal_type: str = ""
    severity: str = "info"
    severity_input_valid: StrictBool = True
    source: str = ""
    cluster: str = ""
    namespace: str = ""
    content: Dict[str, Any] = Field(default_factory=dict)
    labels: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def preserve_severity_validity(self):
        self.severity_input_valid = severity_input_is_valid(self.severity, self.severity_input_valid)
        return self


class BatchRequest(BaseModel):
    signals: List[SignalInput]


class _SignalAdapter:
    def __init__(self, s: SignalInput):
        from uuid import uuid4
        self.signal_id = uuid4()
        self.signal_type = s.signal_type
        self.severity = s.severity
        self.severity_input_valid = s.severity_input_valid
        self.resource_name = s.source
        # Dynamic suppressors key on `source` (protocol.Signal shape).
        # Without this attribute they raise AttributeError on every
        # batch and the pipeline silently skips them — found live as
        # the reason zero suppress decisions ever reached the ledger.
        self.source = s.source
        self.namespace = s.namespace
        self.cluster_id = s.cluster
        self.resource_kind = ""
        self.evidence = s.content
        self.labels = s.labels


@app.get("/health")
def health():
    snapshot_healthy = getattr(_bridge, "_state_healthy", None) if _bridge else None
    enabled = bool(_bridge and _bridge.enabled)
    if _bridge is None:
        status, code = "not_ready", 503
    elif snapshot_healthy is False:
        status, code = "degraded", 503
    elif snapshot_healthy is not True:
        status, code = "unknown", 503
    else:
        status, code = ("ok" if enabled else "paused"), 200
    body = {
        "status": status,
        "service": "cascade-compression",
        "enabled": enabled,
        "ready": status == "ok",
        "state_snapshot_healthy": snapshot_healthy,
        "nano_profile": _bridge.nano_profile if _bridge else "",
    }
    return body if code == 200 else JSONResponse(status_code=code, content=body)


@app.get("/stats")
def stats():
    if not _bridge or not _bridge.enabled:
        return {"status": "not_ready"}
    return {
        "stats": _bridge.get_stats(),
        "llm": _bridge.get_llm_summary(),
        "activated_agents": list(_bridge._activated_types),
        "promotion_log": _bridge.get_promotion_log(20),
    }


@app.get("/classifier/stats")
def classifier_stats():
    """Classifier coverage stats for the anchor pipeline.

    Contract consumed by ``cascade_compression.anchor_pipeline.check_gate``:
    the ``coverage`` dict (total, margin_gated, gcl_fails_sc,
    gcl_fail_types). Found live in production: the anchor pipeline queried
    these routes, got 404s, and its gate silently read zero signals —
    permanently closed while exiting status=0.
    """
    if not _bridge or not _bridge.enabled:
        return {"coverage": {}}
    return {
        "mode": _bridge.classifier.mode,
        "coverage": _bridge.classifier.coverage_stats(),
        **_bridge.classifier.recorder.stats(),
    }


@app.get("/classifier/comparisons")
def classifier_comparisons(limit: int = 200):
    """Recent SC/generative comparison events for the anchor pipeline.

    Contract consumed by ``collect_gaps``: per-comparison signal_type,
    fallback_used, and the semantic/generative label/margin pairs —
    exactly the shape ``recorder.public_events`` produces.
    """
    if not _bridge or not _bridge.enabled:
        return {"comparisons": []}
    bounded = min(max(1, limit), 1000)
    return {"comparisons": _bridge.classifier.recorder.public_events(bounded)}


def _retained_response(request, *, status, code, processing_started=False,
                       backpressure=False, adapted=None, cascade_ms=0.0):
    """Retain the validated original batch; never imply rollback or safe replay."""
    originals = [signal.model_dump(mode="json") for signal in request.signals]
    body = {
        "schema_version": "cascade.service-admission.v1",
        "status": status, "enabled": False, "accepted": False,
        "durable_acceptance": False, "backpressure": backpressure,
        "processing_started": processing_started,
        "recovery_required": processing_started,
        "disposition": "retained_for_reconciliation" if processing_started else "not_processed",
        "total": len(originals), "compressed": 0, "survivors": len(originals),
        "retained": len(originals), "compression_ratio": 0.0,
        "cascade_ms": round(cascade_ms, 1), "signals_needing_attention": originals,
        "retry_safe": None,
        "retry_semantics": "reconcile_before_replay" if processing_started else "not_processed",
        "reconciliation": {
            "required": processing_started,
            "observation_ids_by_input_index": [str(item.signal_id) for item in (adapted or [])],
            "id_origin": "generated_for_this_request" if adapted else "not_generated",
            "idempotency_guaranteed": False,
            "processing_may_have_changed_state": processing_started,
            "prior_inference_execution": "unknown" if processing_started else "not_dispatched",
        },
        "action": (
            "Retain original inputs; reconcile checkpoint and generated observation IDs before replay. "
            "Automatic replay may duplicate work already processed or queued."
            if processing_started else "Inputs were not processed; retain them until service admission resumes."
        ),
    }
    return body if code == 200 else JSONResponse(status_code=code, content=body)


def _accepted_bridge_result(result, adapted):
    """Only the explicit v5 acceptance shape may authorize survivor accounting."""
    if not isinstance(result, dict):
        return False
    if (result.get("enabled") is not True or result.get("accepted") is not True
            or result.get("backpressure") is not False
            or type(result.get("durable_acceptance")) is not bool):
        return False
    remaining = result.get("_remaining_signal_ids")
    if (type(remaining) is not list or any(type(value) is not str or not value for value in remaining)
            or len(set(remaining)) != len(remaining)
            or not set(remaining).issubset({str(item.signal_id) for item in adapted})):
        return False
    persistence = result.get("queue_persistence")
    if type(persistence) is not str or persistence not in {"snapshot", "memory_only"}:
        return False
    if result["durable_acceptance"] and persistence != "snapshot":
        return False
    if type(result.get("signals")) is not int or result["signals"] != len(adapted):
        return False
    compression = result.get("compression")
    if (type(compression) not in (int, float) or not 0 <= compression <= 1
            or not math.isfinite(compression)):
        return False
    if type(result.get("_triage")) is not dict:
        return False
    if "recovery_required" in result and result["recovery_required"] is not False:
        return False
    if "status" in result and (type(result["status"]) is not str
                              or result["status"] not in {"ok", "accepted"}):
        return False
    return True


@app.post("/cascade")
def cascade(request: BatchRequest):
    if _bridge is None:
        return _retained_response(request, status="not_ready", code=503, backpressure=True)
    if not _bridge.enabled:
        return _retained_response(request, status="paused", code=200)
    if not request.signals:
        body = _retained_response(request, status="empty", code=200)
        body.update(enabled=True, accepted=True)
        return body

    adapted = [_SignalAdapter(s) for s in request.signals]

    t0 = __import__("time").monotonic()
    try:
        bridge_result = _bridge.process(adapted)
    except Exception as exc:
        log.warning("Bridge processing did not establish admission (%s)", type(exc).__name__)
        return _retained_response(request, status="bridge_processing_failed", code=503,
            processing_started=True, backpressure=True, adapted=adapted,
            cascade_ms=(__import__("time").monotonic() - t0) * 1000)
    cascade_ms = (__import__("time").monotonic() - t0) * 1000
    if not _accepted_bridge_result(bridge_result, adapted):
        status = "bridge_admission_invalid"
        if isinstance(bridge_result, dict) and bridge_result.get("accepted") is False:
            reason = bridge_result.get("status")
            if type(reason) is str and reason.strip():
                status = reason
        return _retained_response(request, status=status, code=503,
            processing_started=True, backpressure=True, adapted=adapted, cascade_ms=cascade_ms)

    survivors = []
    if bridge_result.get("enabled"):
        remaining_ids = set(bridge_result.get("_remaining_signal_ids", []))
        triage_tags = bridge_result.get("_triage", {})
        for adapter, original in zip(adapted, request.signals):
            if str(adapter.signal_id) in remaining_ids:
                item = {
                    "signal_type": original.signal_type,
                    "severity": original.severity,
                    "severity_input_valid": adapter.severity_input_valid,
                    "source": original.source,
                    "cluster": original.cluster,
                    "namespace": original.namespace,
                    "content": original.content,
                    "labels": original.labels,
                }
                tag = triage_tags.get(str(adapter.signal_id))
                if tag:
                    item["triage"] = tag
                survivors.append(item)

    return {
        "enabled": True,
        "accepted": bridge_result["accepted"],
        "durable_acceptance": bridge_result["durable_acceptance"],
        "backpressure": False,
        "queue_persistence": bridge_result.get("queue_persistence"),
        "total": len(request.signals),
        "compressed": len(request.signals) - len(survivors),
        "survivors": len(survivors),
        "compression_ratio": bridge_result.get("compression", 0),
        "cascade_ms": round(cascade_ms, 1),
        "signals_needing_attention": survivors,
        "triaged_repeats": bridge_result.get("triaged_repeats", 0),
        "triage_eligible": bridge_result.get("triage_eligible", 0),
        "triage_unqualified": bridge_result.get("triage_unqualified", 0),
    }


@app.get("/agents")
def agents():
    if not _bridge:
        return {"agents": []}
    return {
        "activated": list(_bridge._activated_types),
        "discovered": _bridge.get_discovered_agents(),
        "promotion_log": _bridge.get_promotion_log(50),
    }


@app.get("/meta")
def meta(limit: int = 50):
    """Meta-cascade: signals about the cascade's own operations."""
    if not _bridge:
        return {"meta_signals": [], "count": 0}
    signals = _bridge.get_meta_signals(limit)
    return {
        "meta_signals": signals,
        "count": len(signals),
        "meta_target": bool(_bridge._meta_target),
    }


class MemoryQueryRequest(BaseModel):
    signal_type: str = None
    labels: Dict[str, str] = None
    min_strength: float = 0.0
    limit: int = 100


@app.post("/recall")
def recall(signal: SignalInput):
    if not _memory_archive:
        return {"matches": [], "query_ms": 0}
    from .cascade.protocol import Signal as CascadeSignal
    query = CascadeSignal(
        signal_type=signal.signal_type,
        severity=signal.severity,
        severity_input_valid=signal.severity_input_valid,
        source=signal.source,
        cluster=signal.cluster,
        namespace=signal.namespace,
        content=signal.content,
        labels=signal.labels,
    )
    t0 = __import__("time").monotonic()
    engine = RecallEngine()
    results = engine.recall(query, _memory_archive, reinforce=True)
    query_ms = (__import__("time").monotonic() - t0) * 1000
    return {
        "matches": [{
            "memory_id": str(r.memory.memory_id),
            "score": round(r.score, 4),
            "breakdown": {k: round(v, 4) for k, v in r.breakdown.items()},
            "signal_type": r.memory.signal.signal_type,
            "classification": r.memory.classification,
            "strength": round(r.memory.strength, 4),
        } for r in results],
        "query_ms": round(query_ms, 2),
    }


@app.get("/inverse")
def inverse():
    archive = _bridge.memory_archive if _bridge else _memory_archive
    suppression = _bridge.suppression_archive if _bridge else None
    if not archive or not suppression:
        return {"error": "not ready"}
    return inverse_analysis(suppression, archive, _memory_intel)


@app.get("/agents/export")
def agents_export():
    if not _bridge:
        return {"agents": []}
    return export_learned_agents(_bridge)


@app.get("/analyze")
def analyze():
    archive = _bridge.memory_archive if _bridge else _memory_archive
    if not archive or not _memory_intel:
        return {"error": "memory not ready"}
    return _memory_intel.analyze(archive)


@app.post("/consolidate")
def consolidate(batch_size: int = 1000):
    if not _memory_archive or not _bridge:
        return {"processed": 0, "evicted": 0, "compression_ratio": 0.0}
    from .cascade.agents import default_agents
    from .cascade.pipeline import CascadePipeline
    result = _memory_archive.consolidate(
        lambda: CascadePipeline(default_agents()),
        batch_size=batch_size,
    )
    _bridge.check_consolidation_meta(result)
    return result


@app.get("/memories/stats")
def memory_stats():
    if not _memory_archive:
        return {"size": 0, "formed_total": 0, "evictions_total": 0}
    return _memory_archive.stats()


@app.get("/memories/export")
def memory_export(min_strength: float = 0.0, since: str = None):
    if not _memory_archive:
        return {"instance_id": "", "memories": []}
    return _memory_archive.export_memories(min_strength=min_strength, since=since)


@app.post("/memories/import")
def memory_import(data: Dict[str, Any]):
    if not _memory_archive:
        return {"imported": 0}
    count = _memory_archive.import_memories(data)
    return {"imported": count}


@app.post("/memories/query")
def memory_query(request: MemoryQueryRequest = None):
    if not _memory_archive:
        return {"memories": []}
    if request is None:
        request = MemoryQueryRequest()
    results = _memory_archive.query(
        signal_type=request.signal_type,
        labels=request.labels,
        min_strength=request.min_strength,
        limit=request.limit,
    )
    return {"memories": [m.to_dict() for m in results]}


# ---------------------------------------------------------------------------
# Semantic search endpoints (optional — requires pgvector)
# ---------------------------------------------------------------------------

@app.get("/memories/search")
def memory_search(q: str = "", top_k: int = 10, min_similarity: float = 0.5,
                  signal_type: str = None, severity: str = None,
                  namespace: str = None):
    """Semantic search over indexed memories and GPU analyses.

    Query params:
        q:              Search text (required)
        top_k:          Max results (default 10)
        min_similarity: Minimum cosine similarity threshold (default 0.5)
        signal_type:    Filter by signal type (optional)
        severity:       Filter by severity (optional)
        namespace:      Filter by namespace (optional)
    """
    if not _search_available():
        return {
            "error": _search_unavailable_reason(),
            "results": [],
        }
    if not _search_engine or not _search_engine.connected:
        return {
            "error": (
                "Semantic search is not connected. Set CASCADE_SEARCH_DB_URL "
                "to a PostgreSQL connection string with pgvector enabled."
            ),
            "results": [],
        }
    if not q:
        return {"error": "Query parameter 'q' is required", "results": []}

    filters = {}
    if signal_type:
        filters["signal_type"] = signal_type
    if severity:
        filters["severity"] = severity
    if namespace:
        filters["namespace"] = namespace

    try:
        if filters:
            results = _search_engine.search_memories(
                q, top_k=top_k, filters=filters, min_similarity=min_similarity,
            )
        else:
            results = _search_engine.search(
                q, top_k=top_k, min_similarity=min_similarity,
            )
        return {
            "query": q,
            "results": [r.to_dict() for r in results],
            "count": len(results),
        }
    except Exception as e:
        log.error("Search failed: %s", e)
        return {"error": str(e), "results": []}


@app.get("/memories/search/stats")
def memory_search_stats():
    """Return pgvector index statistics."""
    if not _search_available():
        return {"error": _search_unavailable_reason()}
    if not _search_engine:
        return {"error": "Search engine not initialized"}
    return _search_engine.stats()


@app.post("/memories/search/index")
def memory_search_reindex():
    """Trigger a full reindex of current memories into pgvector."""
    if not _search_available():
        return {"error": _search_unavailable_reason()}
    if not _search_engine or not _search_engine.connected:
        return {"error": "Search engine not connected"}
    if not _memory_archive:
        return {"indexed": 0}

    memories = _memory_archive.query(limit=_memory_archive._max_capacity)
    count = _search_engine.index_memories_bulk(memories)

    # Also index GPU analyses if the file exists
    gpu_path = "/state/gpu-analyses.jsonl"
    ana_count = _search_engine.index_analyses_from_jsonl(gpu_path)

    return {
        "memories_indexed": count,
        "analyses_indexed": ana_count,
    }


# ---------------------------------------------------------------------------
# Biography — structured platform autobiography from memory + inverse data
# ---------------------------------------------------------------------------

@app.get("/biography")
def biography():
    """Auto-generated structured biography from cascade memory and inverse data."""
    return generate_biography(_bridge, _memory_archive, _memory_intel)


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

_FRONTEND = resource_dir("frontend")

if _FRONTEND is not None:
    @app.get("/")
    def dashboard():
        return FileResponse(_FRONTEND / "index.html")
