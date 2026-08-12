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
from typing import Any, Dict, List

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .bridge import CascadeBridge

log = logging.getLogger(__name__)

_bridge: CascadeBridge = None


def _load_prompt(domain: str) -> str:
    try:
        mod = importlib.import_module(f"cascade_compression.domains.{domain}")
        return getattr(mod, "SYSTEM_PROMPT", "")
    except ImportError:
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bridge
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
    result = _bridge.process(adapted)
    return result


@app.get("/agents")
def agents():
    if not _bridge:
        return {"agents": []}
    return {
        "activated": list(_bridge._activated_types),
        "discovered": _bridge.get_discovered_agents(),
        "promotion_log": _bridge.get_promotion_log(50),
    }
