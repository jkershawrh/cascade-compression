"""Standalone cascade compression service.

One container. Point it at a signal stream and an LLM. It discovers
what is noise, validates it, and compresses your signal volume.

    oc new-app https://github.com/jkershawrh/cascade-compression \
      -e CASCADE_LLM_URL=https://your-llm/v1 \
      -e CASCADE_LLM_KEY=sk-...

Environment variables:
    CASCADE_LLM_URL          LLM API base URL (required for agent discovery)
    CASCADE_LLM_KEY          LLM API key
    CASCADE_LLM_MODEL        LLM model name (default: microsoft-phi-4)
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

import importlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .bridge import CascadeBridge
from .cascade.memory import MemoryArchive
from .cascade.memory_intelligence import MemoryIntelligence
from .cascade.recall import RecallEngine

log = logging.getLogger(__name__)

_bridge: CascadeBridge = None
_memory_archive: MemoryArchive = None
_memory_intel: MemoryIntelligence = None


def _load_prompt(domain: str) -> str:
    try:
        mod = importlib.import_module(f"cascade_compression.domains.{domain}")
        return getattr(mod, "SYSTEM_PROMPT", "")
    except ImportError:
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bridge, _memory_archive
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
        mod = importlib.import_module(f"cascade_compression.domains.{domain}")
        domain_config = getattr(mod, "MEMORY_CONFIG", None)
        if domain_config:
            _memory_intel.register_domain(domain, domain_config)
            log.info("Registered memory config for domain=%s", domain)
    except (ImportError, AttributeError):
        pass
    log.info("Cascade compression ready (domain=%s)", domain)
    yield


app = FastAPI(
    title="Cascade Compression",
    version="1.0.0",
    description="Self-tuning signal compression — discover what needs AI and what does not.",
    lifespan=lifespan,
)


class SignalInput(BaseModel):
    signal_type: str = ""
    severity: str = "info"
    source: str = ""
    namespace: str = ""
    content: Dict[str, Any] = Field(default_factory=dict)
    labels: Dict[str, str] = Field(default_factory=dict)


class BatchRequest(BaseModel):
    signals: List[SignalInput]


class _SignalAdapter:
    def __init__(self, s: SignalInput):
        from uuid import uuid4
        self.signal_id = uuid4()
        self.signal_type = s.signal_type
        self.severity = s.severity
        self.resource_name = s.source
        self.namespace = s.namespace
        self.cluster_id = ""
        self.resource_kind = ""
        self.evidence = s.content
        self.labels = s.labels


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "cascade-compression",
        "enabled": _bridge.enabled if _bridge else False,
    }


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


@app.post("/cascade")
def cascade(request: BatchRequest):
    if not _bridge or not _bridge.enabled:
        return {"error": "cascade not ready"}

    adapted = [_SignalAdapter(s) for s in request.signals]
    input_lookup = {a.signal_id: s for a, s in zip(adapted, request.signals)}

    from .cascade.protocol import Signal
    cascade_signals = [Signal(
        signal_id=a.signal_id,
        signal_type=a.signal_type,
        severity=a.severity,
        source=a.resource_name,
        namespace=a.namespace,
        content=a.evidence,
        labels=a.labels,
    ) for a in adapted]

    t0 = __import__("time").monotonic()
    cascade_result = _bridge.pipeline.run(cascade_signals)
    cascade_ms = (__import__("time").monotonic() - t0) * 1000

    _bridge.stats.signals_processed += len(request.signals)
    handled = len(request.signals) - len(cascade_result.remaining)
    _bridge.stats.cascade_handled += handled
    _bridge.stats.cascade_forwarded += len(cascade_result.remaining)
    if _bridge.stats.signals_processed > 0:
        _bridge.stats.compression_ratio = _bridge.stats.cascade_handled / _bridge.stats.signals_processed

    if _memory_archive is not None:
        for sig in cascade_result.remaining:
            classification = ""
            for d in cascade_result.decisions:
                if d.signal_id == sig.signal_id and d.classification:
                    classification = d.classification
                    break
            _memory_archive.store(sig, classification=classification)

    survivors = []
    for sig in cascade_result.remaining:
        original = input_lookup.get(sig.signal_id)
        if original:
            survivors.append({
                "signal_type": original.signal_type,
                "severity": original.severity,
                "source": original.source,
                "namespace": original.namespace,
                "content": original.content,
            })

    return {
        "total": len(request.signals),
        "compressed": handled,
        "survivors": len(survivors),
        "compression_ratio": round(cascade_result.compression_ratio, 3),
        "cascade_ms": round(cascade_ms, 1),
        "signals_needing_attention": survivors,
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
        source=signal.source,
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


@app.get("/analyze")
def analyze():
    archive = _bridge.memory_archive if _bridge else _memory_archive
    if not archive or not _memory_intel:
        return {"error": "memory not ready"}
    return _memory_intel.analyze(archive)


@app.post("/consolidate")
def consolidate():
    if not _memory_archive or not _bridge:
        return {"processed": 0, "evicted": 0, "compression_ratio": 0.0}
    from .cascade.agents import default_agents
    from .cascade.pipeline import CascadePipeline
    return _memory_archive.consolidate(lambda: CascadePipeline(default_agents()))


@app.get("/memories/stats")
def memory_stats():
    if not _memory_archive:
        return {"size": 0, "formed_total": 0, "evictions_total": 0}
    return _memory_archive.stats()


@app.get("/memories/export")
def memory_export(min_strength: float = 0.0):
    if not _memory_archive:
        return {"instance_id": "", "memories": []}
    return _memory_archive.export_memories(min_strength=min_strength)


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


_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

if _FRONTEND.is_dir():
    @app.get("/")
    def dashboard():
        return FileResponse(_FRONTEND / "index.html")
