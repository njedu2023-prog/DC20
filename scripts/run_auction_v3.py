#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3 import AuctionV3Config, AuctionV3Engine  # noqa: E402
from top10decision.decision.model_freeze import (  # noqa: E402
    apply_frozen_history_cutoff,
    capture_frozen_history_snapshot,
    load_model_freeze,
    load_frozen_history_snapshot,
    load_verified_frozen_history_snapshot,
    model_freeze_active,
    validate_pinned_files,
    validate_runtime_artifacts,
)
from top10decision.decision.three_rank import (  # noqa: E402
    ThreeEngineRuntimeMixin,
)


LEGACY_PROFIT_PRIVATE_RUNTIME_ENV = (
    "DC20_PERSIST_LEGACY_PROFIT_PRIVATE_RUNTIME"
)
LEGACY_PROFIT_PRIVATE_RUNTIME_ROOT = Path(
    ".dc20-private/legacy_profit_relative_research"
)


def _normal_signal_date(value: object) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"20\d{6}", text) is not None else ""


def _persist_private_legacy_profit_runtime_features(
    root: Path,
    signal_date: str,
    frame: pd.DataFrame,
) -> Path:
    """Write research-only D features outside every publish allowlist."""

    date = _normal_signal_date(signal_date)
    if not date:
        raise RuntimeError("private legacy-profit runtime date is invalid")
    root = root.resolve(strict=True)
    private_root = root / LEGACY_PROFIT_PRIVATE_RUNTIME_ROOT
    current = root
    for part in LEGACY_PROFIT_PRIVATE_RUNTIME_ROOT.parts:
        current = current / part
        if current.exists():
            if not current.is_dir() or current.is_symlink():
                raise RuntimeError(
                    "private legacy-profit runtime root is unsafe"
                )
        else:
            current.mkdir()
    if current.resolve() != private_root.resolve():
        raise RuntimeError(
            "private legacy-profit runtime root escaped repository"
        )
    output = frame.copy()
    if "signal_date" not in output.columns:
        output["signal_date"] = pd.Series(
            date,
            index=output.index,
            dtype="object",
        )
    if "ts_code" not in output.columns and output.empty:
        output["ts_code"] = pd.Series(index=output.index, dtype="object")
    row_dates = output["signal_date"].map(_normal_signal_date)
    codes = output.get(
        "ts_code",
        pd.Series("", index=output.index),
    ).map(lambda value: str(value or "").strip().upper())
    if (
        not row_dates.eq(date).all()
        or codes.eq("").any()
        or codes.duplicated().any()
    ):
        raise RuntimeError(
            "private legacy-profit runtime pool identity is invalid"
        )
    output["signal_date"] = date
    output["ts_code"] = codes
    output = output.sort_values("ts_code", kind="stable").reset_index(
        drop=True
    )
    if any(
        re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value) is None
        for value in codes
    ):
        raise RuntimeError(
            "private legacy-profit runtime pool code is invalid"
        )
    path = private_root / f"runtime_features_{date}.csv"
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise RuntimeError(
            "private legacy-profit runtime feature path is unsafe"
        )
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        output.to_csv(handle, index=False, lineterminator="\n")
        handle.flush()
        os.fsync(handle.fileno())
    payload = temporary.read_bytes()
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RuntimeError(
                    "private legacy-profit runtime feature conflict"
                )
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _capture_private_legacy_profit_runtime_features(
    engine: AuctionV3Engine,
    signal_date: str,
) -> pd.DataFrame:
    date = _normal_signal_date(signal_date)
    if not date:
        raise RuntimeError(
            "private legacy-profit runtime result date is invalid"
        )
    snapshots = engine.candidate_snapshots()
    source = snapshots.get(date)
    if source is None:
        raise RuntimeError(
            "dated pred_source candidate snapshot is missing "
            "for private legacy-profit runtime capture"
        )
    candidates = engine.load_candidates(date, source)
    builder = getattr(engine, "build_three_engine_inference_pool", None)
    if not callable(builder):
        raise RuntimeError(
            "independent three-engine inference adapter is unavailable"
        )
    return builder(date, candidates)


class ThreeEngineRuntimeAuctionV3Engine(
    ThreeEngineRuntimeMixin,
    AuctionV3Engine,
):
    pass


class FreezeAwareAuctionV3Engine(
    ThreeEngineRuntimeMixin,
    AuctionV3Engine,
):
    def __init__(
        self,
        config: AuctionV3Config,
        manifest: dict[str, object],
        *,
        force_inactive: bool = False,
    ):
        super().__init__(config)
        self.model_freeze_manifest = manifest
        self.force_inactive = bool(force_inactive)
        self.model_freeze_history_audit: dict[str, object] = {}

    def build_history(self):
        if self.force_inactive:
            frozen, audit = load_verified_frozen_history_snapshot(
                self.config.root,
                self.model_freeze_manifest,
            )
        else:
            frozen, audit = load_frozen_history_snapshot(
                self.config.root,
                self.model_freeze_manifest,
            )
        if frozen is not None:
            self.model_freeze_history_audit = audit
            return frozen
        history = super().build_history()
        history, audit = apply_frozen_history_cutoff(
            history,
            self.model_freeze_manifest,
        )
        history, audit = capture_frozen_history_snapshot(
            self.config.root,
            self.model_freeze_manifest,
            history,
        )
        self.model_freeze_history_audit = audit
        return history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Decision V12 observation Top10 plus independent trade "
            "selector with fixed T+1 09:30 exit and auction truth"
        )
    )
    parser.add_argument(
        "--signal-date",
        default="",
        help="D-day signal date, YYYYMMDD; default latest frozen pred source",
    )
    parser.add_argument("--root", default=str(ROOT), help="Repository root")
    parser.add_argument(
        "--force-prediction",
        action="store_true",
        help="Replace a dated prediction snapshot; disabled by default",
    )
    parser.add_argument(
        "--order-amount",
        type=float,
        default=100_000.0,
        help="Reference amount used only by auction-capacity simulation; no order is sent",
    )
    parser.add_argument(
        "--round-trip-cost-bps",
        type=float,
        default=35.0,
        help="Commission, taxes and fees excluding modeled slippage",
    )
    parser.add_argument("--slippage-bps-each-side", type=float, default=5.0)
    parser.add_argument(
        "--force-inactive",
        action="store_true",
        help="Enforce the complete inactive V2 freeze in a disposable dry-run checkout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    manifest = load_model_freeze(root, required=True)
    active = model_freeze_active(manifest)
    force_inactive = bool(args.force_inactive and not active)
    validate_pinned_files(
        root,
        manifest,
        force_enforcement=force_inactive,
    )
    config = AuctionV3Config(
        root=root,
        order_amount_cny=max(0.0, args.order_amount),
        round_trip_cost_bps=max(0.0, args.round_trip_cost_bps),
        slippage_bps_each_side=max(0.0, args.slippage_bps_each_side),
    )
    engine: AuctionV3Engine
    if active or force_inactive:
        engine = FreezeAwareAuctionV3Engine(
            config,
            manifest,
            force_inactive=force_inactive,
        )
    else:
        engine = ThreeEngineRuntimeAuctionV3Engine(config)
    private_runtime_mode = os.environ.get(
        LEGACY_PROFIT_PRIVATE_RUNTIME_ENV
    )
    if private_runtime_mode not in (None, "1"):
        raise RuntimeError(
            f"{LEGACY_PROFIT_PRIVATE_RUNTIME_ENV} must be exactly 1"
        )
    result = engine.run(
        args.signal_date,
        force_prediction=args.force_prediction,
    )
    runtime_audit = validate_runtime_artifacts(
        root,
        manifest,
        check_action_plan=False,
        force_enforcement=force_inactive,
    )
    if private_runtime_mode == "1":
        private_signal_date = _normal_signal_date(result.signal_date)
        private_runtime_frame = (
            _capture_private_legacy_profit_runtime_features(
                engine,
                private_signal_date,
            )
        )
        _persist_private_legacy_profit_runtime_features(
            root,
            private_signal_date,
            private_runtime_frame,
        )
    print(
        json.dumps(
            {
                "result": asdict(result),
                "model_freeze": {
                    **runtime_audit,
                    "history": getattr(
                        engine,
                        "model_freeze_history_audit",
                        {},
                    ),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
