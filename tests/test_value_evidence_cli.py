import json
from pathlib import Path

from cascade_compression.value_evidence_cli import export_claim, main


FIXTURES = Path(__file__).parent / "fixtures" / "value_evidence"


def test_fixture_exports_route_and_effort_ledgers():
    replay = json.loads((FIXTURES / "replay.json").read_text())
    economics = json.loads((FIXTURES / "economics.json").read_text())
    claim = export_claim(replay, economics, period="pilot")
    assert claim["measurement"]["baseline_route_ledger"]["ai_eligibility_rate"] == 0.6
    assert claim["measurement"]["raw_calls_avoided"] == 470
    assert claim["financial_model"]["gross_value"] == 46
    assert claim["realization_cost"] == 35


def test_cli_writes_reproducible_claim(monkeypatch, tmp_path):
    output = tmp_path / "claim.json"
    monkeypatch.setattr(
        "sys.argv",
        ["cascade-value-export", "--replay-result", str(FIXTURES / "replay.json"),
         "--economics", str(FIXTURES / "economics.json"), "--period", "pilot",
         "--output", str(output)],
    )
    assert main() == 0
    claim = json.loads(output.read_text())
    assert claim["evidence"]["provenance"]["input_digest"]


def test_cli_combines_separate_observed_arms(monkeypatch, tmp_path):
    combined = json.loads((FIXTURES / "replay.json").read_text())
    baseline = tmp_path / "baseline.json"
    cascade = tmp_path / "cascade.json"
    economics = FIXTURES / "economics.json"
    output = tmp_path / "claim.json"
    baseline.write_text(json.dumps(combined["baseline"]))
    cascade.write_text(json.dumps({
        **combined["cascade"],
        "dangerous_misses_measured": True,
        "ai_work_complete": True,
        "shadow_sampled_suppressions": combined["shadow_sampled_suppressions"],
        "total_suppressions": combined["total_suppressions"],
    }))
    monkeypatch.setattr(
        "sys.argv",
        ["cascade-value-export", "--baseline-result", str(baseline),
         "--cascade-result", str(cascade), "--economics", str(economics),
         "--period", "pilot", "--output", str(output)],
    )
    assert main() == 0
    assert json.loads(output.read_text())["measurement"]["observed"] == 470
