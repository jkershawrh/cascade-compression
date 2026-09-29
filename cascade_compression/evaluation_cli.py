"""CLI for producing sanitized classification evaluation reports."""

import argparse
import json
from pathlib import Path

from .evaluation import evaluate_classifiers


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate generative, semantic, and hybrid arms on one adjudicated corpus"
    )
    parser.add_argument("input", help="Private or synthetic evaluation input JSON")
    parser.add_argument("--output", required=True, help="Sanitized aggregate report JSON")
    args = parser.parse_args()
    document = json.loads(Path(args.input).read_text())
    report = evaluate_classifiers(document)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
