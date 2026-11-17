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


class TestProjectedVolumeExhaustion:
    """A percentage threshold cannot tell fast-growing from parked-full.

    The anchor-proposals volume sat at 73% while gaining ~2.6 MiB/day and
    would have failed permanently in about nine days; the 85% rule would not
    have fired in time. These rules project days-to-full from 6h of history
    and split the urgency bands so one volume yields exactly one severity.
    """

    def _rules(self):
        from cascade_compression.collectors.prometheus import HEALTH_QUERIES
        return {
            name: rule for name, rule in HEALTH_QUERIES.items()
            if rule["signal_type"] == "metric_pvc_projected_full"
        }

    def test_rules_exist_with_exclusive_bands(self):
        rules = self._rules()
        assert set(rules) == {"pvc_projected_full_7d", "pvc_projected_full_30d"}
        assert rules["pvc_projected_full_7d"]["severity"] == "high"
        assert rules["pvc_projected_full_30d"]["severity"] == "medium"
        for rule in rules.values():
            assert "deriv(kubelet_volume_stats_used_bytes[24h]) > 1024" in rule["query"]
            # Growth in the previous window too: a one-off write is not a trend.
            assert "deriv(kubelet_volume_stats_used_bytes[24h] offset 1d) > 1024" in rule["query"]
            assert "kubelet_volume_stats_available_bytes" in rule["query"]
            assert "kubelet_volume_stats_capacity_bytes) >= 0.5" in rule["query"]
        assert "< 7" in rules["pvc_projected_full_7d"]["query"]
        assert ">= 7" in rules["pvc_projected_full_30d"]["query"]

    def test_signal_carries_volume_identity(self, monkeypatch):
        collector = PrometheusCollector()
        collector._thanos_url = "https://thanos.invalid"
        collector._clusters = {}
        collector._token = ""

        def fake_query(url, query, token=""):
            if "deriv(kubelet_volume_stats_used_bytes" not in query:
                return None
            return [{
                "metric": {
                    "persistentvolumeclaim": "build-artifacts",
                    "namespace": "cascade-compression",
                },
                "value": [0, "4.2"],
            }]

        monkeypatch.setattr(collector, "_query", fake_query)
        signals = [s for s in collector._collect_metrics()
                   if s.signal_type == "metric_pvc_projected_full"]

        assert {s.severity for s in signals} == {"high", "medium"}
        assert all(s.namespace == "cascade-compression" for s in signals)
        assert all("build-artifacts" in s.evidence["message"] for s in signals)

    def test_volume_series_identify_the_claim_not_the_node(self, monkeypatch):
        collector = PrometheusCollector()
        collector._thanos_url = "https://thanos.invalid"
        collector._clusters = {}
        collector._token = ""

        def fake_query(url, query, token=""):
            if "deriv(kubelet_volume_stats_used_bytes" not in query:
                return None
            return [{
                "metric": {
                    "persistentvolumeclaim": "postgres-data",
                    "namespace": "production",
                    # Kubelet volume series carry all three labels; the node
                    # label must not outrank the claim.
                    "node": "worker-00",
                    "instance": "10.0.0.1:10250",
                },
                "value": [0, "5.0"],
            }]

        monkeypatch.setattr(collector, "_query", fake_query)
        signals = [s for s in collector._collect_metrics()
                   if s.signal_type == "metric_pvc_projected_full"]
        assert signals
        assert all("postgres-data" in s.evidence["message"] for s in signals)
        assert all("worker-00" not in s.evidence["message"] for s in signals)
