#!/usr/bin/env python3
"""Publish the hash-pinned legacy profit head as a relative research sidecar.

This command never changes the official profit fields, the promotion TopN,
Action, or execution. D=20260821 is rebuilt from the sealed recovery package;
future Daily callers must provide a repository-owned complete runtime feature
CSV produced before the official three-engine scorer discarded input features.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.legacy_profit_relative_research import (  # noqa: E402
    LegacyProfitRelativeResearchError,
    SEALED_VALIDATION_PATH,
    build_projection,
    materialize_projection,
)
from top10decision.decision.three_engine_models import (  # noqa: E402
    load_research_only_legacy_three_engine_snapshot,
)


DATE_RE = re.compile(r"20\d{6}")
RECOVERY_SIGNAL_DATE = "20260821"
RECOVERY_MANIFEST = Path(
    "data/decision_three_engines/recovery/20260821/manifest.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_repository_file(root: Path, value: Path, *, label: str) -> Path:
    root = root.resolve(strict=True)
    candidate = value if value.is_absolute() else root / value
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise LegacyProfitRelativeResearchError(f"{label} escaped repository") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise LegacyProfitRelativeResearchError(f"{label} has a symlink ancestor")
    if not current.is_file() or current.stat().st_size <= 0:
        raise LegacyProfitRelativeResearchError(f"{label} is missing or empty")
    return current.resolve(strict=True)


def _runtime_input(
    root: Path,
    signal_date: str,
    runtime_features_csv: Path | None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    if runtime_features_csv is not None:
        path = _safe_repository_file(
            root,
            runtime_features_csv,
            label="canonical runtime feature CSV",
        )
        try:
            frame = pd.read_csv(path, low_memory=False)
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            raise LegacyProfitRelativeResearchError(
                "canonical runtime feature CSV is invalid"
            ) from exc
        if frame.empty and signal_date != RECOVERY_SIGNAL_DATE:
            # A true zero-candidate day is represented by an empty CSV with a
            # valid header. It is accepted below by the core contract.
            pass
        return frame, {
            "source_kind": "canonical_runtime_feature_csv",
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }

    if signal_date != RECOVERY_SIGNAL_DATE:
        raise LegacyProfitRelativeResearchError(
            "future signal dates require --runtime-features-csv"
        )
    manifest = _safe_repository_file(
        root,
        RECOVERY_MANIFEST,
        label="sealed D=20260821 recovery manifest",
    )
    try:
        from scripts.build_decision_three_rank_snapshot import (
            build_runtime_candidate_frame,
            load_recovery_inputs,
        )

        pool, bars_by_code, _recovery = load_recovery_inputs(root)
        frame = build_runtime_candidate_frame(root, pool, bars_by_code)
    except Exception as exc:
        raise LegacyProfitRelativeResearchError(
            "sealed D=20260821 runtime feature rebuild failed"
        ) from exc
    return frame, {
        "source_kind": "sealed_20260821_recovery",
        "path": manifest.relative_to(root).as_posix(),
        "sha256": _sha256(manifest),
    }


def project(
    root: Path,
    *,
    signal_date: str,
    runtime_features_csv: Path | None = None,
) -> tuple[Path, Path, Path, dict]:
    root = root.resolve(strict=True)
    if DATE_RE.fullmatch(signal_date) is None:
        raise LegacyProfitRelativeResearchError("signal date is invalid")
    frame, source = _runtime_input(root, signal_date, runtime_features_csv)
    loaded = load_research_only_legacy_three_engine_snapshot(
        root / SEALED_VALIDATION_PATH,
        root=root,
    )
    projection = build_projection(
        root,
        signal_date=signal_date,
        runtime_candidates=frame,
        loaded=loaded,
        runtime_source=source,
    )
    return materialize_projection(root, projection)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument(
        "--runtime-features-csv",
        type=Path,
        default=None,
        help=(
            "repository-owned complete pre-overlay runtime feature CSV; "
            "required after D=20260821"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        json_path, csv_path, index_path, payload = project(
            args.root,
            signal_date=args.signal_date,
            runtime_features_csv=args.runtime_features_csv,
        )
    except (LegacyProfitRelativeResearchError, OSError, ValueError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(
        "legacy profit relative research projected: "
        f"D={payload['signal_date']} N={payload['candidate_count']} "
        f"json={json_path} csv={csv_path} index={index_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
