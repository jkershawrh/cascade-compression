"""Public collector interface and generic collector smoke tests."""

import json

from cascade_compression.collectors.base import BaseCollector, DomainCollector
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
