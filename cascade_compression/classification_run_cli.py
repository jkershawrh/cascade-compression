"""Run and score all classifier arms on an adjudicated frozen corpus."""

import argparse
import json
import os
from pathlib import Path

import httpx

from .classification_run import run_and_evaluate
from .classifier import classifier_from_environment
from .domain_plugins import load_domain_plugin


def _read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_jsonl(path: str) -> list:
    return [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write(path: str, value: dict) -> None:
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    temporary.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--holdout-manifest", required=True)
    parser.add_argument("--adjudication-summary", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--config-digest", required=True)
    parser.add_argument("--taxonomy-revision", required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--domain", default="kubernetes")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output-private", required=True)
    parser.add_argument("--output-report", required=True)
    args = parser.parse_args()
    all_paths = [
        args.corpus, args.holdout_manifest, args.adjudication_summary,
        args.output_private, args.output_report,
    ]
    if len({Path(path).resolve() for path in all_paths}) != len(all_paths):
        parser.error("input and output paths must all be different")

    url = os.getenv("CASCADE_LLM_URL", os.getenv("LITELLM_API_BASE", ""))
    key = os.getenv("CASCADE_LLM_KEY", os.getenv("LITELLM_API_KEY", ""))
    model = os.getenv("CASCADE_LLM_MODEL", "")
    micro = os.getenv("CASCADE_MICRO_MODEL", "") or model
    macro = os.getenv("CASCADE_MACRO_MODEL", "") or model
    if not url or not micro or not macro:
        parser.error("CASCADE_LLM_URL and micro/macro model names are required")
    prototype = classifier_from_environment(
        url=url, key=key, micro_model=micro, macro_model=macro,
        system_prompt=load_domain_plugin(args.domain).system_prompt,
        mode_override="hybrid",
    )
    if prototype.config_error or prototype.semantic is None:
        parser.error(prototype.config_error or "semantic backend is unavailable")
    run = {
        "commit": args.commit,
        "image_digest": args.image_digest,
        "config_digest": args.config_digest,
        "taxonomy_revision": args.taxonomy_revision,
        "window_start": args.window_start,
        "window_end": args.window_end,
    }
    with httpx.Client(timeout=60) as client:
        private_input, report = run_and_evaluate(
            _read_jsonl(args.corpus), _read(args.holdout_manifest),
            _read(args.adjudication_summary),
            generative=prototype.generative, semantic=prototype.semantic,
            run=run, hybrid_margin=prototype.hybrid_margin,
            hybrid_suppress_margin=prototype.hybrid_suppress_margin,
            workers=args.workers, client=client,
        )
    _write(args.output_private, private_input)
    _write(args.output_report, report)
    print(json.dumps({
        "status": report["evidence"]["status"],
        "records": report["dataset"]["records"],
        "dataset_digest": report["dataset"]["digest"],
        "output_report": args.output_report,
    }, indent=2, sort_keys=True))
    return 0 if report["evidence"]["status"] == "decision_grade" else 2


if __name__ == "__main__":
    raise SystemExit(main())
