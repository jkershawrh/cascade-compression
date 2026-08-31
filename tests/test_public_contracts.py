"""Compatibility tests for the public OSS contract seam."""

from dataclasses import asdict
import json
import re

import jsonschema

from cascade_compression.cascade.protocol import CascadeDecision, Outcome, Signal
from cascade_compression.collectors.base import BaseCollector
from cascade_compression.contracts import contract_manifest, contract_schema
from cascade_compression.value_evidence import (
    CustomerEconomics,
    ReplayArm,
    ReplayEvidence,
    build_vef_claim,
)


SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


class ExampleCollector(BaseCollector):
    name = "example"
    capabilities = ("batch", "stream")
    signal_types = ("example.event",)

    def connect(self, config: dict) -> bool:
        return True

    def collect(self) -> list:
        return []

    def collect_all(self) -> list:
        return []


def load_schema(contract_id: str) -> dict:
    with contract_schema(contract_id).open(encoding="utf-8") as handle:
        return json.load(handle)


def test_manifest_has_unique_versioned_contracts():
    manifest = contract_manifest()
    ids = [item["id"] for item in manifest["contracts"]]
    assert len(ids) == len(set(ids))
    assert all(SEMVER.fullmatch(item["version"]) for item in manifest["contracts"])
    assert {item["stability"] for item in manifest["contracts"]} <= {"stable", "alpha"}


def test_every_manifest_schema_is_valid_json_schema():
    for item in contract_manifest()["contracts"]:
        jsonschema.Draft202012Validator.check_schema(load_schema(item["id"]))


def test_signal_and_decision_match_wire_contracts():
    signal = Signal(signal_type="example.event", source="test", content={"value": 1})
    signal_payload = asdict(signal)
    signal_payload["signal_id"] = str(signal.signal_id)
    jsonschema.validate(signal_payload, load_schema("cascade.signal"))

    decision = CascadeDecision(
        signal_id=signal.signal_id,
        agent_name="example-agent",
        outcome=Outcome.KEEP,
        confidence=0.9,
    )
    decision_payload = asdict(decision)
    decision_payload["signal_id"] = str(decision.signal_id)
    decision_payload["outcome"] = decision.outcome.value
    jsonschema.validate(decision_payload, load_schema("cascade.decision"))


def test_collector_descriptor_matches_plugin_contract():
    descriptor = ExampleCollector.descriptor().to_dict()
    assert descriptor["supports_stream"] is True
    jsonschema.validate(descriptor, load_schema("cascade.collector-plugin"))


def test_value_evidence_export_matches_public_contract():
    evidence = ReplayEvidence(
        baseline=ReplayArm("sha256:contract", 10, 10, 0),
        cascade=ReplayArm("sha256:contract", 10, 2, 0),
        shadow_sampled_suppressions=8,
        total_suppressions=8,
    )
    claim = build_vef_claim(
        evidence, CustomerEconomics(0.05, 0), period="contract-test"
    )
    jsonschema.validate(claim, load_schema("cascade.value-evidence"))
