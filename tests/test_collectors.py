"""Public collector interface and generic collector smoke tests."""

import json

from cascade_compression.collectors.base import (
    BaseCollector,
    DomainCollector,
    k8s_api_get,
)
from cascade_compression.collectors.finance import FinanceCollector
from cascade_compression.collectors.healthcare import HealthcareCollector
from cascade_compression.collectors.insurance import InsuranceCollector
from cascade_compression.collectors.kubernetes import KubernetesCollector
from cascade_compression.collectors.prometheus import PrometheusCollector
from cascade_compression.collectors.retail import RetailCollector
from cascade_compression.collectors.telecom import TelecomCollector


GENERIC_COLLECTORS = (
    FinanceCollector,
    HealthcareCollector,
    InsuranceCollector,
    KubernetesCollector,
    PrometheusCollector,
    RetailCollector,
    TelecomCollector,
)


def test_generic_collectors_implement_public_interface():
    for collector_type in GENERIC_COLLECTORS:
        assert issubclass(collector_type, BaseCollector)
        assert collector_type.name
        assert collector_type.descriptor().api_version == "1.0"


def test_domain_collector_reads_public_json_fixture(tmp_path):
    fixture = tmp_path / "events.json"
    fixture.write_text(json.dumps([{"event": "synthetic"}]), encoding="utf-8")
    collector = DomainCollector(data_path=str(fixture))
    assert collector.connect({}) is True
    assert collector._events == [{"event": "synthetic"}]


def test_public_domain_collectors_include_their_synthetic_generators():
    for collector_type in (
        FinanceCollector,
        HealthcareCollector,
        InsuranceCollector,
        RetailCollector,
        TelecomCollector,
    ):
        collector = collector_type(synthetic_count=3)
        assert collector.connect({}) is True
        assert collector.collect_all()


def test_k8s_api_never_retries_with_tls_verification_disabled(monkeypatch):
    calls = []

    def certificate_failure(*args, **kwargs):
        calls.append(kwargs["context"])
        raise OSError("CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr(
        "cascade_compression.collectors.base.urlopen", certificate_failure,
    )
    assert k8s_api_get("https://api.example", "/health") is None
    assert len(calls) == 1
    assert calls[0].check_hostname is True
