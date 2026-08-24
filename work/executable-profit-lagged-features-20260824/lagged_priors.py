from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


SCHEMA = "dc20_executable_profit_lagged_priors_research_v1"
FEATURE_SNAPSHOT_SCHEMA = "dc20_executable_profit_lagged_prior_row_v1"
HORIZONS: tuple[tuple[str, int | None], ...] = (
    ("expanding", None),
    ("20d", 20),
    ("60d", 60),
)
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("global", ()),
    ("stage", ("stage",)),
    ("board_stage", ("board", "stage")),
    ("stock", ("ts_code",)),
)
METRICS = (
    "fill_rate",
    "profit_given_fill_rate",
    "big_loss_given_fill_rate",
    "mean_net_return",
    "executable_profit_rate",
    "mean_strategy_return",
    "fill_support_log1p",
    "return_support_log1p",
    "strategy_support_log1p",
)
GROUP_SHRINKAGE = {
    "global": 0.0,
    "stage": 40.0,
    "board_stage": 30.0,
    "stock": 12.0,
}
GLOBAL_PRIORS = {
    "fill_success": 2.0,
    "fill_total": 3.0,
    "profit_success": 1.0,
    "profit_total": 2.0,
    "big_loss_success": 1.0,
    "big_loss_total": 4.0,
    "executable_success": 1.0,
    "executable_total": 3.0,
    "return_strength": 20.0,
}


class LaggedPriorError(ValueError):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise LaggedPriorError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    # bool is an int subclass; preserve contract booleans before integer coercion.
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if value is pd.NA:
        return None
    return value


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normal_date(value: Any) -> str:
    if pd.isna(value):
        return ""
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if "." in text:
        left, right = text.split(".", 1)
        digits = "".join(character for character in left if character.isdigit())[:6]
        if len(digits) == 6 and right in {"SH", "SZ"}:
            return f"{digits}.{right}"
    digits = "".join(character for character in text if character.isdigit())[:6]
    if len(digits) != 6:
        return ""
    return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"


def _resolve_repo_file(root: Path, relative: str) -> Path:
    lowered = relative.replace("\\", "/").lower()
    _expect("/recovery/" not in f"/{lowered.strip('/')}\n", "recovery input forbidden")
    _expect("top10-decision" not in lowered, "external top10-decision input forbidden")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise LaggedPriorError(f"input escaped repo root: {relative}") from exc
    _expect(path.is_file(), f"missing input: {relative}")
    return path


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _deterministic_csv_gzip(frame: pd.DataFrame) -> bytes:
    text = io.StringIO(newline="")
    frame.to_csv(
        text,
        index=False,
        lineterminator="\n",
        na_rep="",
        float_format="%.17g",
    )
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as gz:
        gz.write(text.getvalue().encode("utf-8"))
    return output.getvalue()


def read_sse_open_dates(calendar_path: Path) -> list[str]:
    calendar = pd.read_csv(calendar_path, dtype=str, encoding="utf-8-sig")
    required = {"exchange", "cal_date", "is_open"}
    _expect(required.issubset(calendar.columns), "SSE calendar columns missing")
    calendar["cal_date"] = calendar["cal_date"].map(_normal_date)
    opened = calendar.loc[
        calendar["exchange"].fillna("").str.upper().eq("SSE")
        & calendar["is_open"].astype(str).eq("1"),
        "cal_date",
    ]
    dates = sorted(set(opened))
    _expect(bool(dates), "SSE calendar has no open dates")
    _expect(all(len(date) == 8 for date in dates), "SSE calendar has invalid dates")
    return dates


def _normalise_history(frame: pd.DataFrame, *, source_kind: str) -> pd.DataFrame:
    if source_kind == "full":
        rename = {
            "target_exit_date": "availability_date",
            "market_fill": "fill_label",
            "profit_hit": "profit_label",
            "big_loss_hit": "big_loss_label",
            "net_return": "net_return_label",
        }
    elif source_kind == "top10":
        rename = {
            "scheduled_exit_date": "availability_date",
            "public_market_buyable_proxy": "fill_label",
            "conditional_profit_hit": "profit_label",
            "conditional_big_loss_hit": "big_loss_label",
            "conditional_net_return_after_cost": "net_return_label",
        }
    else:
        raise LaggedPriorError(f"unknown source kind: {source_kind}")
    required = {"signal_date", "ts_code", "stage", "board", *rename}
    _expect(required.issubset(frame.columns), f"{source_kind} history columns missing")
    output = frame[["signal_date", "ts_code", "stage", "board", *rename]].rename(columns=rename).copy()
    output["signal_date"] = output["signal_date"].map(_normal_date)
    output["availability_date"] = output["availability_date"].map(_normal_date)
    output["ts_code"] = output["ts_code"].map(_normal_code)
    output["board"] = output["board"].fillna("").astype(str).str.strip().str.upper()
    output["stage"] = pd.to_numeric(output["stage"], errors="coerce").round()
    _expect(output["signal_date"].str.fullmatch(r"20\d{6}").all(), "history signal date invalid")
    _expect(output["availability_date"].str.fullmatch(r"20\d{6}").all(), "history availability date invalid")
    _expect(output["ts_code"].str.fullmatch(r"\d{6}\.(SH|SZ)").all(), "history code invalid")
    _expect(output["stage"].isin((2.0, 3.0)).all(), "history escaped stage 2/3")
    _expect(output["board"].isin(("SH_MAIN", "SZ_MAIN")).all(), "history board invalid")
    _expect(not output.duplicated(["signal_date", "ts_code"]).any(), "history keys duplicated")
    for column in ("fill_label", "profit_label", "big_loss_label", "net_return_label"):
        output[column] = pd.to_numeric(output[column], errors="coerce")
    for column in ("fill_label", "profit_label", "big_loss_label"):
        invalid = output[column].notna() & ~output[column].isin((0.0, 1.0))
        _expect(not invalid.any(), f"{column} non-binary")
    nonfill = output["fill_label"].eq(0)
    _expect(
        output.loc[nonfill, ["profit_label", "big_loss_label", "net_return_label"]].isna().all().all(),
        "nonfill rows carry conditional outcome truth",
    )
    matured = output["fill_label"].eq(1) & output["net_return_label"].notna()
    _expect(output.loc[matured, "profit_label"].notna().all(), "matured profit truth missing")
    _expect(output.loc[matured, "big_loss_label"].notna().all(), "matured loss truth missing")
    _expect(
        np.isclose(
            output.loc[matured, "profit_label"],
            output.loc[matured, "net_return_label"].gt(0).astype(float),
            rtol=0,
            atol=0,
        ).all(),
        "profit truth conflicts with net return",
    )
    _expect(
        np.isclose(
            output.loc[matured, "big_loss_label"],
            output.loc[matured, "net_return_label"].le(-0.03).astype(float),
            rtol=0,
            atol=0,
        ).all(),
        "big-loss truth conflicts with net return",
    )
    return output.sort_values(["availability_date", "signal_date", "ts_code"], kind="stable").reset_index(drop=True)


def normalise_targets(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"signal_date", "ts_code", "stage", "board", "promotion_rank"}
    _expect(required.issubset(frame.columns), "target Top10 columns missing")
    output = frame.copy()
    output["signal_date"] = output["signal_date"].map(_normal_date)
    output["ts_code"] = output["ts_code"].map(_normal_code)
    output["board"] = output["board"].fillna("").astype(str).str.strip().str.upper()
    output["stage"] = pd.to_numeric(output["stage"], errors="coerce").round().astype(int)
    output["promotion_rank"] = pd.to_numeric(output["promotion_rank"], errors="coerce").round().astype(int)
    _expect(not output.duplicated(["signal_date", "ts_code"]).any(), "target keys duplicated")
    _expect(output["stage"].isin((2, 3)).all(), "target escaped stage 2/3")
    return output.sort_values(["signal_date", "promotion_rank", "ts_code"], kind="stable").reset_index(drop=True)


def validate_calendar_binding(
    history: pd.DataFrame,
    targets: pd.DataFrame,
    open_dates: Sequence[str],
    *,
    source_kind: str,
) -> dict[str, int]:
    positions = {date: index for index, date in enumerate(open_dates)}
    for date in targets["signal_date"].unique():
        _expect(date in positions, f"target D absent from SSE calendar: {date}")
    for row in history[["signal_date", "availability_date"]].drop_duplicates().itertuples(index=False):
        d_index = positions.get(str(row.signal_date))
        exit_index = positions.get(str(row.availability_date))
        _expect(d_index is not None and exit_index is not None, "history date absent from SSE calendar")
        _expect(exit_index == d_index + 2, f"{source_kind} D/T/T+1 calendar binding drifted")
    return positions


STAT_FIELDS = (
    "fill_n",
    "fill_sum",
    "return_n",
    "profit_sum",
    "big_loss_sum",
    "net_sum",
    "strategy_n",
    "executable_sum",
    "strategy_sum",
)


@dataclass(frozen=True)
class StatSeries:
    exit_index: np.ndarray
    cumulative: Mapping[str, np.ndarray]

    def query(self, current_index: int, window: int | None) -> dict[str, float]:
        high = int(np.searchsorted(self.exit_index, current_index, side="left"))
        low_index = -1 if window is None else current_index - window
        low = 0 if window is None else int(np.searchsorted(self.exit_index, low_index, side="left"))
        result: dict[str, float] = {}
        for name in STAT_FIELDS:
            values = self.cumulative[name]
            upper = float(values[high - 1]) if high > 0 else 0.0
            lower = float(values[low - 1]) if low > 0 else 0.0
            result[name] = upper - lower
        return result

    def max_available_before(self, current_index: int) -> int | None:
        high = int(np.searchsorted(self.exit_index, current_index, side="left"))
        return int(self.exit_index[high - 1]) if high > 0 else None


def _stat_rows(frame: pd.DataFrame, positions: Mapping[str, int]) -> pd.DataFrame:
    output = frame.copy()
    output["exit_index"] = output["availability_date"].map(positions)
    _expect(output["exit_index"].notna().all(), "availability date missing from calendar")
    fill = output["fill_label"]
    return_known = fill.eq(1) & output["net_return_label"].notna()
    strategy_known = fill.eq(0) | return_known
    output["fill_n"] = fill.notna().astype(float)
    output["fill_sum"] = fill.fillna(0.0)
    output["return_n"] = return_known.astype(float)
    output["profit_sum"] = output["profit_label"].where(return_known, 0.0).fillna(0.0)
    output["big_loss_sum"] = output["big_loss_label"].where(return_known, 0.0).fillna(0.0)
    output["net_sum"] = output["net_return_label"].where(return_known, 0.0).fillna(0.0)
    output["strategy_n"] = strategy_known.astype(float)
    output["executable_sum"] = output["profit_label"].where(return_known, 0.0).fillna(0.0)
    output["strategy_sum"] = output["net_return_label"].where(return_known, 0.0).fillna(0.0)
    return output


class HistoryIndex:
    def __init__(self, frame: pd.DataFrame, positions: Mapping[str, int]):
        values = _stat_rows(frame, positions)
        self._series: dict[tuple[str, tuple[Any, ...]], StatSeries] = {}
        for group_name, columns in GROUPS:
            if columns:
                iterator: Iterable[tuple[Any, pd.DataFrame]] = values.groupby(list(columns), sort=False, dropna=False)
            else:
                iterator = [((), values)]
            for raw_key, group in iterator:
                if columns:
                    key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
                else:
                    key = ()
                aggregated = group.groupby("exit_index", sort=True)[list(STAT_FIELDS)].sum().reset_index()
                cumulative = {
                    name: aggregated[name].to_numpy(dtype=float).cumsum()
                    for name in STAT_FIELDS
                }
                self._series[(group_name, tuple(key))] = StatSeries(
                    exit_index=aggregated["exit_index"].to_numpy(dtype=int),
                    cumulative=cumulative,
                )

    def query(
        self,
        group_name: str,
        key: tuple[Any, ...],
        current_index: int,
        window: int | None,
    ) -> dict[str, float]:
        series = self._series.get((group_name, key))
        if series is None:
            return {name: 0.0 for name in STAT_FIELDS}
        return series.query(current_index, window)

    def max_global_available_before(self, current_index: int) -> int | None:
        return self._series[("global", ())].max_available_before(current_index)


def _global_metrics(stats: Mapping[str, float]) -> dict[str, float]:
    fill_rate = (stats["fill_sum"] + GLOBAL_PRIORS["fill_success"]) / (
        stats["fill_n"] + GLOBAL_PRIORS["fill_total"]
    )
    profit_rate = (stats["profit_sum"] + GLOBAL_PRIORS["profit_success"]) / (
        stats["return_n"] + GLOBAL_PRIORS["profit_total"]
    )
    loss_rate = (stats["big_loss_sum"] + GLOBAL_PRIORS["big_loss_success"]) / (
        stats["return_n"] + GLOBAL_PRIORS["big_loss_total"]
    )
    executable_rate = (stats["executable_sum"] + GLOBAL_PRIORS["executable_success"]) / (
        stats["strategy_n"] + GLOBAL_PRIORS["executable_total"]
    )
    mean_net = stats["net_sum"] / (stats["return_n"] + GLOBAL_PRIORS["return_strength"])
    strategy_mean = stats["strategy_sum"] / (
        stats["strategy_n"] + GLOBAL_PRIORS["return_strength"]
    )
    return {
        "fill_rate": fill_rate,
        "profit_given_fill_rate": profit_rate,
        "big_loss_given_fill_rate": loss_rate,
        "mean_net_return": mean_net,
        "executable_profit_rate": executable_rate,
        "mean_strategy_return": strategy_mean,
        "fill_support_log1p": math.log1p(stats["fill_n"]),
        "return_support_log1p": math.log1p(stats["return_n"]),
        "strategy_support_log1p": math.log1p(stats["strategy_n"]),
    }


def _smoothed_metrics(
    stats: Mapping[str, float],
    global_values: Mapping[str, float],
    strength: float,
) -> dict[str, float]:
    if strength <= 0:
        return dict(global_values)
    return {
        "fill_rate": (stats["fill_sum"] + strength * global_values["fill_rate"]) / (stats["fill_n"] + strength),
        "profit_given_fill_rate": (
            stats["profit_sum"] + strength * global_values["profit_given_fill_rate"]
        ) / (stats["return_n"] + strength),
        "big_loss_given_fill_rate": (
            stats["big_loss_sum"] + strength * global_values["big_loss_given_fill_rate"]
        ) / (stats["return_n"] + strength),
        "mean_net_return": (
            stats["net_sum"] + strength * global_values["mean_net_return"]
        ) / (stats["return_n"] + strength),
        "executable_profit_rate": (
            stats["executable_sum"] + strength * global_values["executable_profit_rate"]
        ) / (stats["strategy_n"] + strength),
        "mean_strategy_return": (
            stats["strategy_sum"] + strength * global_values["mean_strategy_return"]
        ) / (stats["strategy_n"] + strength),
        "fill_support_log1p": math.log1p(stats["fill_n"]),
        "return_support_log1p": math.log1p(stats["return_n"]),
        "strategy_support_log1p": math.log1p(stats["strategy_n"]),
    }


def feature_columns(prefix: str) -> list[str]:
    return [
        f"{prefix}_{group}_{horizon}_{metric}"
        for group, _ in GROUPS
        for horizon, _ in HORIZONS
        for metric in METRICS
    ]


def build_lagged_features(
    *,
    history: pd.DataFrame,
    targets: pd.DataFrame,
    open_dates: Sequence[str],
    source_kind: str,
    prefix: str,
) -> pd.DataFrame:
    history = _normalise_history(history, source_kind=source_kind)
    targets = normalise_targets(targets)
    positions = validate_calendar_binding(history, targets, open_dates, source_kind=source_kind)
    index = HistoryIndex(history, positions)
    rows: list[dict[str, Any]] = []
    for target in targets.itertuples(index=False):
        current_index = positions[str(target.signal_date)]
        values: dict[str, Any] = {
            "signal_date": str(target.signal_date),
            "ts_code": str(target.ts_code),
            "promotion_rank": int(target.promotion_rank),
        }
        max_used_index = index.max_global_available_before(current_index)
        values["lagged_prior_max_history_exit_date"] = (
            str(open_dates[max_used_index]) if max_used_index is not None else ""
        )
        for horizon_name, horizon_days in HORIZONS:
            global_stats = index.query("global", (), current_index, horizon_days)
            global_values = _global_metrics(global_stats)
            for group_name, columns in GROUPS:
                key = tuple(getattr(target, column) for column in columns)
                stats = index.query(group_name, key, current_index, horizon_days)
                metrics = _smoothed_metrics(
                    stats,
                    global_values,
                    GROUP_SHRINKAGE[group_name],
                )
                for metric, metric_value in metrics.items():
                    values[f"{prefix}_{group_name}_{horizon_name}_{metric}"] = metric_value
        rows.append(values)
    output = pd.DataFrame(rows)
    ordered_features = feature_columns(prefix)
    _expect(set(ordered_features).issubset(output.columns), "lagged feature columns missing")
    _expect(np.isfinite(output[ordered_features].to_numpy(dtype=float)).all(), "lagged features nonfinite")
    _expect(
        (
            output["lagged_prior_max_history_exit_date"].eq("")
            | output["lagged_prior_max_history_exit_date"].lt(output["signal_date"])
        ).all(),
        "lagged prior audit found non-strict outcome availability",
    )
    output = output[
        [
            "signal_date",
            "ts_code",
            "promotion_rank",
            "lagged_prior_max_history_exit_date",
            *ordered_features,
        ]
    ]
    output["lagged_prior_snapshot_sha256"] = output.apply(
        lambda row: _canonical_sha256(
            {
                "schema": FEATURE_SNAPSHOT_SCHEMA,
                "source_kind": source_kind,
                "signal_date": row["signal_date"],
                "ts_code": row["ts_code"],
                "values": {
                    column: format(float(row[column]), ".12g")
                    for column in ordered_features
                },
            }
        ),
        axis=1,
    )
    return output.sort_values(["signal_date", "promotion_rank", "ts_code"], kind="stable").reset_index(drop=True)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(value, dict), f"JSON object required: {path}")
    return value


def materialize(repo_root: Path, output_dir: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    manifest_path = _resolve_repo_file(
        root,
        "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json",
    )
    manifest = _read_json(manifest_path)
    full_path = _resolve_repo_file(root, manifest["inputs"]["five_year_source_ledger"]["path"])
    top10_path = _resolve_repo_file(root, manifest["output"]["path"])
    calendar_path = _resolve_repo_file(root, manifest["inputs"]["strict_sse_calendar"]["path"])
    _expect(_sha256(full_path) == manifest["inputs"]["five_year_source_ledger"]["sha256"], "full ledger SHA drifted")
    _expect(_sha256(top10_path) == manifest["output"]["sha256"], "Top10 ledger SHA drifted")
    _expect(_sha256(calendar_path) == manifest["inputs"]["strict_sse_calendar"]["sha256"], "SSE calendar SHA drifted")
    open_dates = read_sse_open_dates(calendar_path)
    full = pd.read_csv(full_path, low_memory=False, dtype={"signal_date": str, "target_exit_date": str, "ts_code": str})
    top10 = pd.read_csv(top10_path, low_memory=False, dtype={"signal_date": str, "scheduled_exit_date": str, "ts_code": str})
    targets = normalise_targets(top10)
    outputs: dict[str, Any] = {}
    for source_kind, source, prefix in (
        ("full", full, "fullhist"),
        ("top10", top10, "top10hist"),
    ):
        frame = build_lagged_features(
            history=source,
            targets=targets,
            open_dates=open_dates,
            source_kind=source_kind,
            prefix=prefix,
        )
        payload = _deterministic_csv_gzip(frame)
        output_path = output_dir / f"{source_kind}_lagged_priors.csv.gz"
        _atomic_bytes(output_path, payload)
        info = {
            "path": output_path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "rows": int(len(frame)),
            "dates": int(frame["signal_date"].nunique()),
            "feature_columns": feature_columns(prefix),
            "feature_columns_sha256": _canonical_sha256(feature_columns(prefix)),
            "sort": ["signal_date", "promotion_rank", "ts_code"],
            "compression": "gzip_mtime_0",
        }
        outputs[source_kind] = info
    research_manifest = {
        "schema_version": SCHEMA,
        "status": "RESEARCH_ONLY_NOT_A_MODEL_NOT_RELEASED",
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
        "runtime_dependency_on_recovery": False,
        "availability_rule": "history scheduled/target exit date must be strictly earlier than current signal D",
        "calendar": {
            "path": manifest["inputs"]["strict_sse_calendar"]["path"],
            "sha256": _sha256(calendar_path),
            "exchange": "SSE",
            "strict": True,
            "rolling_windows": "previous 20/60 SSE open-session indices by outcome availability date",
        },
        "inputs": {
            "full_history": {"path": str(full_path.relative_to(root)), "sha256": _sha256(full_path), "rows": int(len(full))},
            "top10_history_and_targets": {"path": str(top10_path.relative_to(root)), "sha256": _sha256(top10_path), "rows": int(len(top10))},
        },
        "smoothing": {"group_strength": GROUP_SHRINKAGE, "global_priors": GLOBAL_PRIORS},
        "groups": [name for name, _ in GROUPS],
        "horizons": {name: days for name, days in HORIZONS},
        "metrics": list(METRICS),
        "outputs": outputs,
        "official_trade_action_allowed": False,
        "model_trained": False,
    }
    manifest_bytes = (
        json.dumps(research_manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    _atomic_bytes(output_dir / "lagged_priors_manifest.json", manifest_bytes)
    research_manifest["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    return research_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = materialize(args.repo_root, args.output_dir.resolve())
    except (LaggedPriorError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
