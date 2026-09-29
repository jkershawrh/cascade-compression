"""CLI for assembling a sanitized staging-success evidence bundle."""

import argparse
import json
from pathlib import Path

from .staging_evidence import build_staging_evidence


def _read(path: str) -> dict:
    return json.loads(Path(path).read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--classification", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--stats-before", required=True)
    parser.add_argument("--stats-after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = build_staging_evidence(
        _read(args.manifest), _read(args.classification), _read(args.runtime),
        _read(args.stats_before), _read(args.stats_after),
    )
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report["status"] == "staging_success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
