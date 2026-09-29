"""Merge two independent blind reviews and optional disagreement resolution."""

import argparse
import json
from pathlib import Path

from .adjudication import merge_independent_reviews


def _read_jsonl(path: str) -> list:
    return [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: str, records: list) -> None:
    Path(path).write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--holdout-manifest", required=True)
    parser.add_argument("--review", action="append", required=True)
    parser.add_argument("--resolution")
    parser.add_argument("--output-corpus", required=True)
    parser.add_argument("--output-summary", required=True)
    parser.add_argument("--output-disagreements", required=True)
    args = parser.parse_args()
    if len(args.review) != 2:
        parser.error("provide exactly two --review files")
    merged, summary, unresolved = merge_independent_reviews(
        _read_jsonl(args.corpus),
        _read_jsonl(args.review[0]),
        _read_jsonl(args.review[1]),
        _read_jsonl(args.resolution) if args.resolution else None,
        holdout_manifest=json.loads(
            Path(args.holdout_manifest).read_text(encoding="utf-8")
        ),
    )
    _write_jsonl(args.output_corpus, merged)
    _write_jsonl(args.output_disagreements, unresolved)
    Path(args.output_summary).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
