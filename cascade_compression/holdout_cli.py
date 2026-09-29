"""Freeze a deterministic stratified corpus for independent blind review."""

import argparse
import json
from pathlib import Path

from .holdout import freeze_stratified_holdout


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


def _quota(value: str) -> tuple[str, int]:
    try:
        name, count = value.rsplit("=", 1)
        parsed = int(count)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("quota must be STRATUM=COUNT") from exc
    if not name.strip() or parsed <= 0:
        raise argparse.ArgumentTypeError("quota must use a name and positive count")
    return name.strip(), parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--quota", action="append", type=_quota, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--dataset-revision", required=True)
    parser.add_argument("--stratification-basis", required=True)
    parser.add_argument("--source-window-start", required=True)
    parser.add_argument("--source-window-end", required=True)
    parser.add_argument("--frozen-at")
    parser.add_argument("--output-corpus", required=True)
    parser.add_argument("--output-manifest", required=True)
    args = parser.parse_args()
    paths = {
        Path(args.candidates).resolve(),
        Path(args.output_corpus).resolve(),
        Path(args.output_manifest).resolve(),
    }
    if len(paths) != 3:
        parser.error("candidate, corpus, and manifest paths must be different")
    quotas = dict(args.quota)
    if len(quotas) != len(args.quota):
        parser.error("each --quota stratum must be unique")
    corpus, manifest = freeze_stratified_holdout(
        _read_jsonl(args.candidates), quotas,
        seed=args.seed,
        dataset_name=args.dataset_name,
        dataset_revision=args.dataset_revision,
        stratification_basis=args.stratification_basis,
        source_window_start=args.source_window_start,
        source_window_end=args.source_window_end,
        frozen_at=args.frozen_at,
    )
    _write_jsonl(args.output_corpus, corpus)
    Path(args.output_manifest).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
