#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.research_context import (  # noqa: E402
    publish_vendored_research_context,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-time import of an immutable legacy action-plan blob as a "
            "sanitized, non-action DC20 research context"
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--report-input", required=True)
    parser.add_argument("--evaluation-input", required=True)
    parser.add_argument("--output-root", default=str(ROOT))
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--source-commit-sha", required=True)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--source-blob-sha", required=True)
    parser.add_argument("--source-raw-sha256", required=True)
    parser.add_argument("--report-source-path", required=True)
    parser.add_argument("--report-source-blob-sha", required=True)
    parser.add_argument("--report-source-raw-sha256", required=True)
    parser.add_argument("--evaluation-source-path", required=True)
    parser.add_argument("--evaluation-source-blob-sha", required=True)
    parser.add_argument("--evaluation-source-raw-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path, context = publish_vendored_research_context(
        Path(args.input),
        Path(args.output_root),
        repository=args.source_repository,
        commit_sha=args.source_commit_sha,
        source_path=args.source_path,
        blob_sha=args.source_blob_sha,
        raw_sha256=args.source_raw_sha256,
        report_input_path=Path(args.report_input),
        report_source_path=args.report_source_path,
        report_blob_sha=args.report_source_blob_sha,
        report_raw_sha256=args.report_source_raw_sha256,
        evaluation_input_path=Path(args.evaluation_input),
        evaluation_source_path=args.evaluation_source_path,
        evaluation_blob_sha=args.evaluation_source_blob_sha,
        evaluation_raw_sha256=args.evaluation_source_raw_sha256,
    )
    print(
        json.dumps(
            {
                "path": str(path),
                "report_date": context["report_date"],
                "source_binding": context["source_binding"],
                "action_authorized": context["action_authorized"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
