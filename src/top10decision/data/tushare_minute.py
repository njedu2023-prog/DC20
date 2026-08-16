from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests


API_URL = "https://api.tushare.pro"
MINUTE_FIELDS = ("time", "open", "close", "high", "low", "vol", "amount")
HISTORICAL_MINUTE_FIELDS = (
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
)
DAILY_CLOSE_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
    "pct_chg",
)
DAILY_LIMIT_FIELDS = (
    "trade_date",
    "ts_code",
    "up_limit",
    "down_limit",
)
DAILY_LIMIT_LIST_FIELDS = (
    "trade_date",
    "ts_code",
    "name",
    "limit_type",
    "close",
    "up_limit",
    "down_limit",
    "open_times",
    "fd_amount",
    "first_time",
    "last_time",
    "limit_times",
    "up_stat",
    "industry",
    "turnover_ratio",
    "amount",
    "float_mv",
    "total_mv",
)
AUCTION_OPEN_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "open",
    "high",
    "low",
    "vol",
    "amount",
    "vwap",
)


def _normalize_trade_date_partition(
    frame: pd.DataFrame,
    trade_date: str,
    *,
    numeric_columns: Iterable[str] = (),
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if "trade_date" not in frame.columns:
        raise RuntimeError("Tushare snapshot has no trade_date column")
    out = frame.copy()
    out["trade_date"] = (
        out["trade_date"]
        .astype(str)
        .str.replace(r"\D", "", regex=True)
        .str[:8]
    )
    source_dates = set(
        out.loc[
            out["trade_date"].str.fullmatch(r"\d{8}", na=False),
            "trade_date",
        ]
    )
    if source_dates != {trade_date}:
        raise RuntimeError(
            "Tushare snapshot trade_date mismatch: "
            f"requested={trade_date}, actual={sorted(source_dates)}"
        )
    if "ts_code" in out.columns:
        out["ts_code"] = out["ts_code"].map(normalize_code)
        out = out[out["ts_code"].ne("")]
    for column in numeric_columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    if "ts_code" in out.columns:
        out = out.drop_duplicates("ts_code", keep="last").sort_values(
            "ts_code"
        )
    return out.reset_index(drop=True)


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        left, right = text.split(".", 1)
        return f"{left.zfill(6)}.{right}"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6:
        return ""
    symbol = digits[-6:]
    suffix = "BJ" if symbol.startswith(("4", "8", "920")) else "SH" if symbol.startswith(("5", "6", "9")) else "SZ"
    return f"{symbol}.{suffix}"


def minute_output_path(root: Path, trade_date: str, ts_code: str) -> Path:
    safe_code = normalize_code(ts_code).replace(".", "_")
    return root / "data" / "market" / "minute_1m" / trade_date[:4] / trade_date / f"{safe_code}.csv"


def auction_open_output_path(root: Path, trade_date: str) -> Path:
    return (
        root
        / "data"
        / "market"
        / "raw"
        / trade_date[:4]
        / trade_date
        / "stk_auction_o.csv"
    )


def read_minute_snapshot(root: Path, trade_date: str, ts_code: str) -> pd.DataFrame:
    """Read a persisted minute snapshot without requiring a credential."""
    path = minute_output_path(root, trade_date, ts_code)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()
    if frame.empty or "time" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["time"] = out["time"].astype(str).str.strip()
    for column in ("open", "close", "high", "low", "vol", "amount"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out.sort_values("time").reset_index(drop=True)


def opening_auction_price_from_snapshot(frame: pd.DataFrame) -> float:
    """Return the first regular-session bar open, a proxy for the auction print."""
    if frame.empty or "time" not in frame.columns:
        return float("nan")
    out = frame.copy()
    clock = out["time"].astype(str).str.extract(r"(\d{2}:\d{2}:\d{2})", expand=False)
    regular = out[clock.ge("09:30:00")]
    if regular.empty:
        return float("nan")
    first = regular.iloc[0]
    for column in ("open", "close"):
        try:
            value = float(first.get(column))
        except Exception:
            continue
        if pd.notna(value) and value > 0:
            return value
    return float("nan")


def opening_auction_price(root: Path, trade_date: str, ts_code: str) -> float:
    return opening_auction_price_from_snapshot(read_minute_snapshot(root, trade_date, ts_code))


@dataclass(frozen=True)
class TushareClient:
    token: str
    timeout_seconds: int = 30

    @classmethod
    def from_env(
        cls,
        env_name: str = "TUSHARE_TOKEN",
        *,
        timeout_seconds: int = 8,
    ) -> "TushareClient":
        token = str(os.environ.get(env_name, "") or "").strip()
        if not token:
            raise RuntimeError(f"{env_name} is not configured")
        return cls(token=token, timeout_seconds=max(1, int(timeout_seconds)))

    def call(self, api_name: str, params: dict[str, Any], fields: Iterable[str]) -> pd.DataFrame:
        response = requests.post(
            API_URL,
            json={
                "api_name": api_name,
                "token": self.token,
                "params": params,
                "fields": ",".join(fields),
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        code = int(payload.get("code", -1) or 0)
        if code != 0:
            message = str(payload.get("msg") or "Tushare request failed")
            raise RuntimeError(f"Tushare {api_name} error code={code}: {message}")
        data = payload.get("data") or {}
        columns = list(data.get("fields") or [])
        rows = list(data.get("items") or [])
        return pd.DataFrame(rows, columns=columns)

    def trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        frame = self.call(
            "trade_cal",
            {"exchange": "SSE", "start_date": start_date, "end_date": end_date},
            ("exchange", "cal_date", "is_open", "pretrade_date"),
        )
        if frame.empty:
            raise RuntimeError("Tushare trade_cal returned no rows")
        frame = frame.copy()
        frame["cal_date"] = frame["cal_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
        frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).astype(int)
        return frame.sort_values("cal_date").drop_duplicates("cal_date", keep="last")

    def daily_close(self, trade_date: str) -> pd.DataFrame:
        frame = self.call(
            "daily",
            {"trade_date": str(trade_date)},
            DAILY_CLOSE_FIELDS,
        )
        return _normalize_trade_date_partition(
            frame,
            str(trade_date),
            numeric_columns=(
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "vol",
                "amount",
                "pct_chg",
            ),
        )

    def daily_limits(self, trade_date: str) -> pd.DataFrame:
        frame = self.call(
            "stk_limit",
            {"trade_date": str(trade_date)},
            DAILY_LIMIT_FIELDS,
        )
        return _normalize_trade_date_partition(
            frame,
            str(trade_date),
            numeric_columns=("up_limit", "down_limit"),
        )

    def daily_limit_list(self, trade_date: str) -> pd.DataFrame:
        frame = self.call(
            "limit_list_d",
            {"trade_date": str(trade_date)},
            DAILY_LIMIT_LIST_FIELDS,
        )
        return _normalize_trade_date_partition(
            frame,
            str(trade_date),
            numeric_columns=(
                "close",
                "up_limit",
                "down_limit",
                "open_times",
                "fd_amount",
                "limit_times",
                "turnover_ratio",
                "amount",
                "float_mv",
                "total_mv",
            ),
        )

    def current_minute(self, ts_code: str, freq: str = "1MIN") -> pd.DataFrame:
        code = normalize_code(ts_code)
        if not code:
            raise ValueError(f"Invalid A-share code: {ts_code}")
        frame = self.call("rt_min_daily", {"ts_code": code, "freq": freq}, MINUTE_FIELDS)
        if frame.empty:
            return frame
        frame = frame.copy()
        frame.insert(0, "ts_code", code)
        for column in ("open", "close", "high", "low", "vol", "amount"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["time"] = frame["time"].astype(str).str.strip()
        frame = frame.dropna(subset=["open", "close"]).drop_duplicates("time", keep="last")
        return frame.sort_values("time").reset_index(drop=True)

    def historical_minute(
        self,
        ts_code: str,
        trade_date: str,
        *,
        latest_time: str = "11:00",
    ) -> pd.DataFrame:
        """Fetch one A-share historical 1-minute session through Tushare pro_bar."""
        code = normalize_code(ts_code)
        date = "".join(ch for ch in str(trade_date or "") if ch.isdigit())[:8]
        if not code or len(date) != 8:
            raise ValueError(
                f"Invalid historical minute request: {ts_code} {trade_date}"
            )
        frame = self.call(
            "stk_mins",
            {
                "ts_code": code,
                "start_date": (
                    f"{date[:4]}-{date[4:6]}-{date[6:]} 09:15:00"
                ),
                "end_date": (
                    f"{date[:4]}-{date[4:6]}-{date[6:]} "
                    f"{latest_time}:59"
                ),
                "freq": "1min",
            },
            HISTORICAL_MINUTE_FIELDS,
        )
        if frame.empty:
            return pd.DataFrame()
        out = frame.copy()
        if "trade_time" in out.columns and "time" not in out.columns:
            out = out.rename(columns={"trade_time": "time"})
        if "time" not in out.columns:
            raise RuntimeError("Tushare historical minute response has no time column")
        if "ts_code" not in out.columns:
            out.insert(0, "ts_code", code)
        else:
            out["ts_code"] = out["ts_code"].map(normalize_code)
        out["time"] = out["time"].astype(str).str.strip()
        for column in ("open", "close", "high", "low", "vol", "amount"):
            if column in out.columns:
                out[column] = pd.to_numeric(out[column], errors="coerce")
        required = {"open", "close", "high", "low"}
        if not required.issubset(out.columns):
            raise RuntimeError(
                "Tushare historical minute response misses OHLC columns"
            )
        out = out.dropna(subset=list(required)).drop_duplicates(
            "time", keep="last"
        )
        return out.sort_values("time").reset_index(drop=True)

    def opening_auction(
        self,
        trade_date: str,
        *,
        ts_code: str = "",
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"trade_date": str(trade_date)}
        code = normalize_code(ts_code)
        if code:
            params["ts_code"] = code
        frame = self.call("stk_auction_o", params, AUCTION_OPEN_FIELDS)
        if frame.empty:
            return frame
        frame = frame.copy()
        frame["ts_code"] = frame["ts_code"].map(normalize_code)
        frame["trade_date"] = (
            frame["trade_date"]
            .astype(str)
            .str.replace(r"\D", "", regex=True)
            .str[:8]
        )
        for column in ("close", "open", "high", "low", "vol", "amount", "vwap"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return (
            frame[frame["ts_code"].ne("")]
            .drop_duplicates("ts_code", keep="last")
            .sort_values("ts_code")
            .reset_index(drop=True)
        )


def write_calendar(frame: pd.DataFrame, root: Path) -> Path:
    path = root / "data" / "market" / "trade_cal_sse.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming = frame.copy()
    incoming["cal_date"] = (
        incoming["cal_date"]
        .astype(str)
        .str.replace(r"\D", "", regex=True)
        .str[:8]
    )
    incoming["is_open"] = (
        pd.to_numeric(incoming["is_open"], errors="coerce")
        .fillna(0)
        .astype(int)
    )
    if path.exists() and path.stat().st_size > 0:
        existing = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype={"cal_date": str},
        )
        if {"cal_date", "is_open"}.issubset(existing.columns):
            incoming = pd.concat(
                [existing, incoming],
                ignore_index=True,
                sort=False,
            )
    incoming = (
        incoming[incoming["cal_date"].str.fullmatch(r"\d{8}", na=False)]
        .drop_duplicates("cal_date", keep="last")
        .sort_values("cal_date", kind="stable")
        .reset_index(drop=True)
    )
    incoming.to_csv(path, index=False, encoding="utf-8-sig")
    try:
        from top10decision.writers.io_contract import _load_exchange_calendar

        _load_exchange_calendar.cache_clear()
    except ImportError:
        pass
    return path


def write_daily_close_snapshot(
    daily: pd.DataFrame,
    limits: pd.DataFrame,
    limit_list: pd.DataFrame,
    root: Path,
    trade_date: str,
) -> tuple[dict[str, Path], Path]:
    """Persist a complete, date-verified close partition for Decision truth."""
    daily = _normalize_trade_date_partition(
        daily,
        trade_date,
        numeric_columns=(
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "vol",
            "amount",
            "pct_chg",
        ),
    )
    limits = _normalize_trade_date_partition(
        limits,
        trade_date,
        numeric_columns=("up_limit", "down_limit"),
    )
    limit_list = _normalize_trade_date_partition(
        limit_list,
        trade_date,
        numeric_columns=(
            "close",
            "up_limit",
            "down_limit",
            "open_times",
            "fd_amount",
            "limit_times",
            "turnover_ratio",
            "amount",
            "float_mv",
            "total_mv",
        ),
    )
    if len(daily) < 3000:
        raise RuntimeError(
            f"incomplete Tushare daily close: rows={len(daily)}"
        )
    if len(limits) < 3000:
        raise RuntimeError(
            f"incomplete Tushare daily limit table: rows={len(limits)}"
        )

    target = (
        root
        / "data"
        / "market"
        / "raw"
        / trade_date[:4]
        / trade_date
    )
    target.mkdir(parents=True, exist_ok=True)
    frames = {
        "daily.csv": daily,
        "stk_limit.csv": limits,
        "limit_list_d.csv": limit_list,
    }
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    for filename, frame in frames.items():
        payload = frame.to_csv(index=False, lineterminator="\n")
        path = target / filename
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(payload, encoding="utf-8-sig")
        temporary.replace(path)
        paths[filename] = path
        digests[filename] = hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()

    meta_path = target / "_tushare_close_meta.json"
    meta = {
        "schema_version": "decision_tushare_close_truth_v1",
        "source": "tushare",
        "trade_date": trade_date,
        "daily_rows": int(len(daily)),
        "limit_rows": int(len(limits)),
        "limit_list_rows": int(len(limit_list)),
        "files": {
            filename: {
                "path": str(path.relative_to(root)),
                "sha256": digests[filename],
            }
            for filename, path in paths.items()
        },
        "credential_persisted": False,
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return paths, meta_path


def write_minute_snapshot(
    frame: pd.DataFrame,
    root: Path,
    trade_date: str,
    ts_code: str,
    *,
    source: str = "tushare:rt_min_daily",
) -> tuple[Path, Path]:
    path = minute_output_path(root, trade_date, ts_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    meta_path = path.with_suffix(".meta.json")
    meta = {
        "source": source,
        "trade_date": trade_date,
        "ts_code": normalize_code(ts_code),
        "frequency": "1MIN",
        "rows": int(len(frame)),
        "fields": list(frame.columns),
        "credential_persisted": False,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, meta_path


def write_auction_open_snapshot(
    frame: pd.DataFrame,
    root: Path,
    trade_date: str,
    *,
    selected_codes: Iterable[str] = (),
) -> tuple[Path, Path]:
    """Persist a compact immutable 9:30 opening-auction truth partition."""
    out = frame.copy()
    codes = {
        normalize_code(value)
        for value in selected_codes
        if normalize_code(value)
    }
    if codes and "ts_code" in out.columns:
        out = out[out["ts_code"].map(normalize_code).isin(codes)].copy()
    if "ts_code" in out.columns:
        out["ts_code"] = out["ts_code"].map(normalize_code)
        out = out[out["ts_code"].ne("")].drop_duplicates(
            "ts_code", keep="last"
        )
    out = out.sort_values("ts_code").reset_index(drop=True)
    path = auction_open_output_path(root, trade_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = out.to_csv(index=False, lineterminator="\n")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if path.exists() and path.stat().st_size > 0:
        existing = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        existing_payload = (
            existing.sort_values("ts_code")
            .reset_index(drop=True)
            .to_csv(index=False, lineterminator="\n")
        )
        existing_digest = hashlib.sha256(
            existing_payload.encode("utf-8")
        ).hexdigest()
        if existing_digest != digest:
            raise RuntimeError(
                f"immutable auction partition conflict: {path}"
            )
    else:
        out.to_csv(path, index=False, encoding="utf-8-sig")
    meta_path = path.with_suffix(".meta.json")
    meta = {
        "schema_version": "decision_auction_truth_v1",
        "source": "tushare:stk_auction_o",
        "trade_date": trade_date,
        "rows": int(len(out)),
        "requested_code_count": int(len(codes)),
        "fields": list(out.columns),
        "sha256": digest,
        "immutable": True,
        "credential_persisted": False,
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path, meta_path


__all__ = [
    "API_URL",
    "AUCTION_OPEN_FIELDS",
    "DAILY_CLOSE_FIELDS",
    "DAILY_LIMIT_FIELDS",
    "DAILY_LIMIT_LIST_FIELDS",
    "MINUTE_FIELDS",
    "TushareClient",
    "auction_open_output_path",
    "minute_output_path",
    "normalize_code",
    "opening_auction_price",
    "opening_auction_price_from_snapshot",
    "read_minute_snapshot",
    "write_calendar",
    "write_daily_close_snapshot",
    "write_auction_open_snapshot",
    "write_minute_snapshot",
]
