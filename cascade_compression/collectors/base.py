"""Base collector interface and shared HTTP helpers."""

import json
import logging
import os
import ssl
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Iterator, Optional, Union
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectorDescriptor:
    """Stable capability metadata for collector discovery and plugin loading."""

    name: str
    api_version: str = "1.0"
    capabilities: tuple[str, ...] = ("batch",)
    signal_types: tuple[str, ...] = ()
    supports_stream: bool = False
    config_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        value["signal_types"] = list(self.signal_types)
        return value


class BaseCollector(ABC):
    name: str = "base"
    api_version: str = "1.0"
    capabilities: tuple[str, ...] = ("batch",)
    signal_types: tuple[str, ...] = ()

    @abstractmethod
    def connect(self, config: dict) -> bool:
        ...

    @abstractmethod
    def collect(self) -> list:
        ...

    @abstractmethod
    def collect_all(self) -> list:
        ...

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": False}

    @classmethod
    def descriptor(cls) -> CollectorDescriptor:
        capabilities = tuple(cls.capabilities)
        return CollectorDescriptor(
            name=cls.name,
            api_version=cls.api_version,
            capabilities=capabilities,
            signal_types=tuple(cls.signal_types),
            supports_stream="stream" in capabilities,
        )


def http_json_get(url: str, headers: Optional[dict] = None,
                  timeout: int = 20, label: str = "HTTP") -> Optional[Union[dict, list]]:
    try:
        req = Request(url, headers=headers or {"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:  # nosec B310
            return json.loads(resp.read())
    except Exception as e:
        log.debug("%s GET %s: %s", label, url[:120], str(e)[:100])
        return None


def k8s_api_get(api_url: str, path: str, token: str = "",
                timeout: int = 15, label: str = "K8s") -> Optional[Union[dict, list]]:
    url = f"{api_url}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = Request(url, headers=headers)
        ctx = ssl.create_default_context()
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        if os.path.exists(ca_path):
            ctx.load_verify_locations(ca_path)
        try:
            with urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as _ssl_err:
            if "CERTIFICATE_VERIFY_FAILED" not in str(_ssl_err):
                raise
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
    except Exception as e:
        log.debug("%s GET %s: %s", label, path, str(e)[:100])
        return None


def load_sa_token() -> str:
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as f:
            return f.read().strip()
    except Exception:
        return ""


def detect_in_cluster() -> str:
    host = os.getenv("KUBERNETES_SERVICE_HOST", "")
    port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
    if host:
        return f"https://{host}:{port}"
    return ""


class DomainCollector(BaseCollector):
    """Base for domain collectors that load events from JSON or synthetic generators."""
    name: str = "domain"
    _signal_class: type = None
    _synthetic_module: str = ""

    def __init__(self, data_path: str = "", synthetic_count: int = 0):
        self._data_path = data_path
        self._synthetic_count = synthetic_count
        self._events = []
        self._poll_index = 0

    def connect(self, config: dict) -> bool:
        self._data_path = config.get("data_path", self._data_path)
        self._synthetic_count = config.get("synthetic_count", self._synthetic_count)
        if self._data_path:
            with open(self._data_path) as f:
                self._events = json.load(f)
            return True
        if self._synthetic_count and self._synthetic_module:
            import importlib
            mod = importlib.import_module(self._synthetic_module)
            self._events = [mod.asdict(e) for e in mod.generate(self._synthetic_count)]
            return True
        return False

    def collect(self) -> list:
        if self._poll_index >= len(self._events):
            return []
        batch = self._events[self._poll_index:self._poll_index + 500]
        self._poll_index += 500
        return [self._signal_class(e) for e in batch]

    def collect_all(self) -> list:
        return [self._signal_class(e) for e in self._events]
