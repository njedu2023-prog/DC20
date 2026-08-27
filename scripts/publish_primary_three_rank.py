#!/usr/bin/env python3
"""Publish the P0 D-close 2->3/3->4 list without secondary engines.

This entry point is intentionally smaller than the normal Daily pipeline.  It
loads only exact-D candidate/market snapshots, the committed SSE calendar, the
hash-bound promotion model and its prior ledger.  It never loads or writes the
big-loss, profit, P_fill, action-plan, or shadow ledgers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from top10decision.auction_v3 import AuctionV3Config, AuctionV3Engine
from top10decision.decision.eligibility import filter_standard_limit_universe
from top10decision.decision.three_engine_models import (
    ProbabilityHeadBundle,
    ThreeEngineArtifactError,
    ThreeEngineSnapshotScore,
    load_promotion_only_artifacts,
    score_three_engine_snapshot,
)
from top10decision.decision.three_rank import (
    ThreeRankContractError,
    augment_three_engine_runtime_base,
    build_three_rank_contract,
    materialize_three_rank_artifacts,
    validate_three_rank_contract,
)
from top10decision.decision.d_close_features import D_CLOSE_MAX_HISTORY_BARS


DATE_RE = re.compile(r"^20\d{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GENERATION_MODES = ("NATURAL", "RETROSPECTIVE_RECOVERY")
REQUIRED_MARKET_TABLES = (
    "daily",
    "daily_basic",
    "limit_list_d",
    "stk_limit",
    "stock_basic",
)
RECEIPT_SCHEMA = "dc20_primary_d_receipt_v1"
HISTORY_CONTEXT_TABLES = (
    "daily",
    "daily_basic",
    "limit_list_d",
    "stk_limit",
    "stock_basic",
)
RUNTIME_IDENTITY_SCHEMA = "dc20_primary_d_runtime_identity_v1"
PROMOTION_PRIOR_SOURCE_PATHS = (
    "data/auction_v3/promotion_prior/five_year_daily_stage_board.csv",
    "data/auction_v3/promotion_prior/five_year_event_features.csv.gz",
)
PRIMARY_RUNTIME_CODE_PATHS = (
    "scripts/publish_primary_three_rank.py",
    "src/top10decision/auction_v3/__init__.py",
    "src/top10decision/auction_v3/calibration.py",
    "src/top10decision/auction_v3/config.py",
    "src/top10decision/auction_v3/engine.py",
    "src/top10decision/auction_v3/promotion_model.py",
    "src/top10decision/data/tushare_minute.py",
    "src/top10decision/decision/canonical_fingerprint.py",
    "src/top10decision/decision/contracts.py",
    "src/top10decision/decision/d_close_features.py",
    "src/top10decision/decision/eligibility.py",
    "src/top10decision/decision/exit_policy.py",
    "src/top10decision/decision/observation.py",
    "src/top10decision/decision/three_engine_models.py",
    "src/top10decision/decision/three_rank.py",
    "src/top10decision/decision/trade_selector.py",
    "src/top10decision/probability_calibration.py",
    "src/top10decision/writers/io_contract.py",
)


class PrimaryDGenerationError(ValueError):
    """Raised when the P0 list cannot prove one of its primary inputs."""


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (
            json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
            + "\n"
        ).encode("utf-8")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_file(root: Path, relative: str, *, label: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PrimaryDGenerationError(f"{label} escaped repository root") from exc
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise PrimaryDGenerationError(
            f"{label} is missing, empty, or not a regular file: {relative}"
        )
    return path


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrimaryDGenerationError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise PrimaryDGenerationError(f"{label} must be an object")
    return value


def _read_csv(path: Path, *, label: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False)
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise PrimaryDGenerationError(f"{label} is unreadable") from exc


class GitHeadInputVerifier:
    """Prove that a runtime input is the exact regular-file blob in HEAD."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        try:
            top = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.head = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise PrimaryDGenerationError(
                "P0 committed-input verification requires a Git checkout"
            ) from exc
        if Path(top).resolve() != self.root or COMMIT_RE.fullmatch(self.head) is None:
            raise PrimaryDGenerationError(
                "P0 committed-input verification is not bound to repository root/HEAD"
            )
        self._bindings: dict[str, dict[str, Any]] = {}

    def bind(self, relative: str, *, label: str) -> dict[str, Any]:
        normalized = Path(relative).as_posix()
        if normalized in self._bindings:
            return dict(self._bindings[normalized])
        path = _repository_file(self.root, normalized, label=label)
        try:
            listing = subprocess.run(
                ["git", "-C", str(self.root), "ls-tree", "-z", "HEAD", "--", normalized],
                check=True,
                capture_output=True,
            ).stdout
            worktree_blob = subprocess.run(
                ["git", "-C", str(self.root), "hash-object", "--", normalized],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise PrimaryDGenerationError(
                f"cannot verify committed P0 input: {normalized}"
            ) from exc
        if not listing:
            raise PrimaryDGenerationError(
                f"P0 historical/code input is not committed in HEAD: {normalized}"
            )
        header, separator, listed_path = listing.rstrip(b"\0").partition(b"\t")
        fields = header.decode("ascii", errors="strict").split()
        if (
            separator != b"\t"
            or listed_path.decode("utf-8") != normalized
            or len(fields) != 3
            or fields[1] != "blob"
            or fields[0] not in {"100644", "100755"}
        ):
            raise PrimaryDGenerationError(
                f"P0 committed input is not one regular blob: {normalized}"
            )
        head_blob = fields[2]
        if worktree_blob != head_blob:
            raise PrimaryDGenerationError(
                f"P0 committed input drifted from HEAD: {normalized}"
            )
        binding = {
            "path": normalized,
            "sha256": _sha256(path),
            "git_blob_sha1": head_blob,
            "git_mode": fields[0],
        }
        self._bindings[normalized] = binding
        return dict(binding)

    def bindings(self) -> list[dict[str, Any]]:
        return [dict(self._bindings[path]) for path in sorted(self._bindings)]


class PrimaryDReadOnlyEngine(AuctionV3Engine):
    """Auction feature engine restricted to an audited, finite D-close closure."""

    def __init__(
        self,
        config: AuctionV3Config,
        *,
        signal_date: str,
        context_dates: list[str],
        verifier: GitHeadInputVerifier | None,
        exact_d_inventory: Mapping[str, Mapping[str, Any]],
    ):
        super().__init__(config)
        self._primary_signal_date = signal_date
        self._primary_context_dates = tuple(context_dates)
        self._primary_verifier = verifier
        self._primary_exact_d_inventory = {
            str(name): dict(binding)
            for name, binding in exact_d_inventory.items()
        }
        self._primary_consumed: dict[str, dict[str, Any]] = {}
        self._market_dates_cache = list(context_dates)

    def market_dates(self) -> list[str]:
        return list(self._primary_context_dates)

    def _record_path(self, trade_date: str, name: str, path: Path) -> None:
        relative = path.resolve().relative_to(self.config.root.resolve()).as_posix()
        key = f"{trade_date}:{name}"
        if trade_date == self._primary_signal_date:
            expected = self._primary_exact_d_inventory.get(name)
            if not isinstance(expected, Mapping):
                raise PrimaryDGenerationError(
                    f"exact-D table consumed outside sync inventory: {name}"
                )
            if expected.get("path") != relative or expected.get("sha256") != _sha256(path):
                raise PrimaryDGenerationError(
                    f"exact-D consumed table drifted from sync inventory: {name}"
                )
            binding = dict(expected)
        else:
            if trade_date not in self._primary_context_dates:
                raise PrimaryDGenerationError(
                    f"market input escaped the fixed 21-session closure: {trade_date}/{name}"
                )
            if self._primary_verifier is None:
                binding = {"path": relative, "sha256": _sha256(path)}
            else:
                binding = self._primary_verifier.bind(
                    relative,
                    label=f"committed historical market {trade_date}/{name}",
                )
        binding.update({"trade_date": trade_date, "table": name})
        self._primary_consumed[key] = binding

    def _market_path(self, trade_date: str, name: str) -> Path | None:
        path = super()._market_path(trade_date, name)
        if path is not None:
            self._record_path(trade_date, name, path)
        return path

    def minute_table(self, trade_date: str, code: str) -> pd.DataFrame:
        # Minute files are not part of the exact-D raw sync contract.  P0 does
        # not silently consume a locally downloaded snapshot: only a file
        # already committed in HEAD may participate.
        path = self._minute_path(trade_date, code)
        if not path.exists():
            return pd.DataFrame()
        if self._primary_verifier is None:
            return super().minute_table(trade_date, code)
        relative = path.resolve().relative_to(self.config.root.resolve()).as_posix()
        binding = self._primary_verifier.bind(
            relative,
            label=f"committed P0 minute input {trade_date}/{code}",
        )
        binding.update(
            {"trade_date": trade_date, "table": "minute_1m", "ts_code": _normal_code(code)}
        )
        self._primary_consumed[f"{trade_date}:minute_1m:{_normal_code(code)}"] = binding
        return super().minute_table(trade_date, code)

    def consumed_bindings(self) -> list[dict[str, Any]]:
        return [dict(self._primary_consumed[key]) for key in sorted(self._primary_consumed)]


def _normal_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        digits, suffix = text.split(".", 1)
        digits = "".join(character for character in digits if character.isdigit())[:6]
        if len(digits) == 6 and suffix in {"SH", "SZ"}:
            return f"{digits}.{suffix}"
    digits = "".join(character for character in text if character.isdigit())[:6]
    if len(digits) != 6:
        return ""
    return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"


def _validate_exact_dates(
    frame: pd.DataFrame,
    signal_date: str,
    *,
    label: str,
    required_when_nonempty: bool = True,
) -> None:
    if frame.empty:
        return
    if "trade_date" not in frame.columns:
        if required_when_nonempty:
            raise PrimaryDGenerationError(f"{label} has no trade_date column")
        return
    dates = {_normal_date(value) for value in frame["trade_date"]}
    if dates != {signal_date}:
        raise PrimaryDGenerationError(
            f"{label} is not exact-D: expected {signal_date}, found {sorted(dates)}"
        )


def load_strict_sse_dates(
    root: Path,
    signal_date: str,
    *,
    verifier: GitHeadInputVerifier | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return T/T+1 from the committed SSE calendar, with no weekday fallback."""

    relative = "data/market/trade_cal_sse.csv"
    path = _repository_file(root, relative, label="SSE trade calendar")
    frame = _read_csv(path, label="SSE trade calendar")
    required = {"exchange", "cal_date", "is_open"}
    if not required.issubset(frame.columns):
        raise PrimaryDGenerationError("SSE trade calendar columns are incomplete")
    exchanges = frame["exchange"].fillna("").astype(str).str.upper().str.strip()
    if not exchanges.eq("SSE").all():
        raise PrimaryDGenerationError("trade calendar contains a non-SSE row")
    dates = frame["cal_date"].map(_normal_date)
    if dates.eq("").any() or dates.duplicated().any():
        raise PrimaryDGenerationError("SSE trade calendar dates are invalid or duplicated")
    is_open = pd.to_numeric(frame["is_open"], errors="coerce")
    if not is_open.isin((0, 1)).all():
        raise PrimaryDGenerationError("SSE trade calendar is_open is invalid")
    open_dates = dates[is_open.eq(1)].tolist()
    if signal_date not in open_dates:
        raise PrimaryDGenerationError(f"D={signal_date} is not an SSE trading day")
    position = open_dates.index(signal_date)
    required_history = D_CLOSE_MAX_HISTORY_BARS - 1
    history_dates = open_dates[max(0, position - required_history) : position]
    if len(history_dates) != required_history:
        raise PrimaryDGenerationError(
            "SSE calendar does not cover the fixed 20-session D-close history"
        )
    if position + 2 >= len(open_dates):
        raise PrimaryDGenerationError("SSE calendar does not cover T and T+1")
    exec_date, exit_date = open_dates[position + 1 : position + 3]
    if "pretrade_date" in frame.columns:
        pretrade = {
            date: _normal_date(previous)
            for date, previous, opened in zip(
                dates, frame["pretrade_date"], is_open, strict=True
            )
            if opened == 1
        }
        if pretrade.get(exec_date) != signal_date or pretrade.get(exit_date) != exec_date:
            raise PrimaryDGenerationError("SSE T/T+1 pretrade chain is inconsistent")
    calendar_binding = (
        verifier.bind(relative, label="committed SSE trade calendar")
        if verifier is not None
        else {"path": relative, "sha256": _sha256(path)}
    )
    calendar_binding.update({
        "path": relative,
        "sha256": _sha256(path),
        "exchange": "SSE",
        "signal_date_is_open": True,
        "historical_session_count": required_history,
        "historical_dates": history_dates,
        "runtime_context_dates": [*history_dates, signal_date],
    })
    return exec_date, exit_date, calendar_binding


def bind_committed_history_context(
    root: Path,
    signal_date: str,
    history_dates: Sequence[str],
    verifier: GitHeadInputVerifier,
) -> dict[str, Any]:
    """Preflight the exact finite history that live feature code may read."""

    expected_sessions = D_CLOSE_MAX_HISTORY_BARS - 1
    normalized_dates = [_normal_date(value) for value in history_dates]
    if (
        len(normalized_dates) != expected_sessions
        or len(set(normalized_dates)) != expected_sessions
        or any(DATE_RE.fullmatch(value) is None for value in normalized_dates)
        or normalized_dates != sorted(normalized_dates)
        or signal_date in normalized_dates
    ):
        raise PrimaryDGenerationError(
            "committed historical context is not the fixed 20-session pre-D window"
        )
    bindings: list[dict[str, Any]] = []
    for trade_date in normalized_dates:
        for table in HISTORY_CONTEXT_TABLES:
            relative = (
                f"data/market/raw/{trade_date[:4]}/{trade_date}/{table}.csv"
            )
            binding = verifier.bind(
                relative,
                label=f"committed historical market {trade_date}/{table}",
            )
            frame = _read_csv(root / relative, label=f"historical {trade_date}/{table}")
            if table in {"daily", "daily_basic", "stk_limit", "stock_basic"} and frame.empty:
                raise PrimaryDGenerationError(
                    f"required historical market table is empty: {trade_date}/{table}"
                )
            if table != "stock_basic":
                _validate_exact_dates(
                    frame,
                    trade_date,
                    label=f"historical {trade_date}/{table}",
                )
            binding.update({"trade_date": trade_date, "table": table})
            bindings.append(binding)
    return {
        "git_head": verifier.head,
        "read_only": True,
        "network_fetch_allowed": False,
        "session_count": len(normalized_dates),
        "dates": normalized_dates,
        "tables": list(HISTORY_CONTEXT_TABLES),
        "file_count": len(bindings),
        "files": bindings,
    }


def load_exact_candidate_source(
    root: Path,
    signal_date: str,
    exec_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    relative = f"data/pred/archive/pred_source_{signal_date}.csv"
    path = _repository_file(root, relative, label="exact-D candidate archive")
    actual_sha = _sha256(path)
    frame = _read_csv(path, label="exact-D candidate archive")
    _validate_exact_dates(frame, signal_date, label="candidate archive")

    meta_relative = "data/pred/_pred_source_meta.json"
    meta_path = _repository_file(root, meta_relative, label="candidate source metadata")
    meta = _read_json(meta_path, label="candidate source metadata")
    if _normal_date(meta.get("resolved_trade_date")) != signal_date:
        raise PrimaryDGenerationError("candidate metadata is not bound to D")
    for field in ("sha256", "body_sha256"):
        claimed = str(meta.get(field) or "").lower()
        if claimed != actual_sha:
            raise PrimaryDGenerationError(
                f"candidate metadata {field} does not bind the archive"
            )
    consistency = meta.get("consistency")
    if not isinstance(consistency, Mapping) or consistency.get("archive_path") != relative:
        raise PrimaryDGenerationError("candidate metadata archive path is not exact-D")
    target = _normal_date(consistency.get("target_trade_date"))
    if target and target != exec_date:
        raise PrimaryDGenerationError("candidate metadata target date is not calendar T")
    profile = meta.get("csv_profile")
    if not isinstance(profile, Mapping) or _normal_date(profile.get("trade_date")) != signal_date:
        raise PrimaryDGenerationError("candidate CSV profile is not bound to D")
    profile_target = _normal_date(profile.get("target_trade_date"))
    if profile_target and profile_target != exec_date:
        raise PrimaryDGenerationError("candidate CSV profile target is not calendar T")
    source_repository = str(meta.get("source_repository") or "")
    if source_repository == "njedu2023-prog/top10-decision":
        raise PrimaryDGenerationError("DC20 candidate runtime depends on top10-decision")
    resolved_commit = str(meta.get("resolved_commit") or "").lower()
    if resolved_commit and COMMIT_RE.fullmatch(resolved_commit) is None:
        raise PrimaryDGenerationError("candidate resolved commit is invalid")
    return frame, {
        "path": relative,
        "sha256": actual_sha,
        "row_count": int(len(frame)),
        "meta_path": meta_relative,
        "meta_sha256": _sha256(meta_path),
        "source_repository": source_repository,
        "resolved_commit": resolved_commit,
        "created_at_utc": str(meta.get("created_at_utc") or ""),
    }


def load_exact_market_package(
    root: Path,
    signal_date: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    base_relative = f"data/market/raw/{signal_date[:4]}/{signal_date}"
    meta_relative = f"{base_relative}/_sync_meta.json"
    meta_path = _repository_file(root, meta_relative, label="exact-D market metadata")
    meta = _read_json(meta_path, label="exact-D market metadata")
    if (
        _normal_date(meta.get("requested_trade_date")) != signal_date
        or _normal_date(meta.get("resolved_trade_date")) != signal_date
        or meta.get("strict_dated_source") is not True
    ):
        raise PrimaryDGenerationError("market metadata is not strict exact-D")
    source_repo = meta.get("source_repo")
    if not isinstance(source_repo, Mapping):
        raise PrimaryDGenerationError("market source repository provenance is missing")
    if (
        str(source_repo.get("owner") or "") == "njedu2023-prog"
        and str(source_repo.get("repo") or "") == "top10-decision"
    ):
        raise PrimaryDGenerationError("DC20 market runtime depends on top10-decision")
    source_commit = str(source_repo.get("resolved_commit") or "").lower()
    if COMMIT_RE.fullmatch(source_commit) is None:
        raise PrimaryDGenerationError("market resolved commit is invalid")
    records = meta.get("files")
    if not isinstance(records, list):
        raise PrimaryDGenerationError("market file inventory is missing")
    names = [
        str(record.get("name") or "")
        for record in records
        if isinstance(record, Mapping)
    ]
    if len(names) != len(set(names)) or any(
        re.fullmatch(r"[a-z0-9_]+", name) is None for name in names
    ):
        raise PrimaryDGenerationError("market file inventory names are invalid")
    by_name = {
        str(record.get("name") or ""): record
        for record in records
        if isinstance(record, Mapping)
    }
    tables: dict[str, pd.DataFrame] = {}
    bindings: dict[str, Any] = {}
    for name, record in sorted(by_name.items()):
        if record.get("success") is not True:
            continue
        relative = f"{base_relative}/{name}.csv"
        if record.get("dated_path") != relative:
            raise PrimaryDGenerationError(f"market {name} path is not exact-D")
        claimed_sha = str(record.get("sha256") or "").lower()
        if SHA256_RE.fullmatch(claimed_sha) is None:
            raise PrimaryDGenerationError(f"market {name} SHA-256 is invalid")
        path = _repository_file(root, relative, label=f"exact-D market {name}")
        actual_sha = _sha256(path)
        if actual_sha != claimed_sha:
            raise PrimaryDGenerationError(f"market {name} hash mismatch")
        table = _read_csv(path, label=f"exact-D market {name}")
        date_scoped = record.get("date_scoped") is True
        if date_scoped:
            if _normal_date(record.get("source_trade_date")) != signal_date:
                raise PrimaryDGenerationError(f"market {name} provenance is not D")
            _validate_exact_dates(table, signal_date, label=f"market {name}")
        if name in {"daily", "daily_basic", "stk_limit", "stock_basic"} and table.empty:
            raise PrimaryDGenerationError(f"required market table is empty: {name}")
        if name in REQUIRED_MARKET_TABLES:
            tables[name] = table
        bindings[name] = {
            "path": relative,
            "sha256": actual_sha,
            "row_count": int(len(table)),
            "date_scoped": date_scoped,
        }
    for name in REQUIRED_MARKET_TABLES:
        record = by_name.get(name)
        if (
            not isinstance(record, Mapping)
            or record.get("success") is not True
            or name not in tables
            or name not in bindings
        ):
            raise PrimaryDGenerationError(f"required exact-D market table failed: {name}")
    return tables, {
        "meta_path": meta_relative,
        "meta_sha256": _sha256(meta_path),
        "source_repository": f"{source_repo.get('owner')}/{source_repo.get('repo')}",
        "resolved_commit": source_commit,
        "tables": bindings,
    }


def build_exact_primary_pool(
    source: pd.DataFrame,
    market: Mapping[str, pd.DataFrame],
    signal_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build only the hard 2->3/3->4 pool from the exact-D limit-up table."""

    authoritative = market["limit_list_d"].copy()
    empty_columns = [
        "signal_date",
        "ts_code",
        "name",
        "industry",
        "limit_times",
        "stage",
    ]
    if authoritative.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in empty_columns}), {
            "universe_rule": "exact_d_limit_list_hard_stage_only",
            "authoritative_limit_rows": 0,
            "eligible_standard_limit_rows": 0,
            "hard_stage_rows": 0,
        }
    required = {"ts_code", "limit_times"}
    if not required.issubset(authoritative.columns):
        raise PrimaryDGenerationError("limit_list_d lacks ts_code/limit_times")
    if "limit_type" in authoritative.columns:
        limit_type = authoritative["limit_type"].fillna("").astype(str).str.upper().str.strip()
        authoritative = authoritative[
            limit_type.isin({"U", "UP", "涨停"}) | limit_type.eq("")
        ].copy()
    authoritative["ts_code"] = authoritative["ts_code"].map(_normal_code)
    if authoritative["ts_code"].eq("").any():
        raise PrimaryDGenerationError("limit_list_d contains an invalid code")
    if authoritative["ts_code"].duplicated().any():
        raise PrimaryDGenerationError("limit_list_d contains duplicate codes")

    def enrich(frame: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
        if other.empty:
            return frame
        other = other.copy()
        if "ts_code" not in other.columns:
            return frame
        other["ts_code"] = other["ts_code"].map(_normal_code)
        other = other[other["ts_code"].ne("")].drop_duplicates("ts_code", keep="first")
        left = frame.set_index("ts_code", drop=False)
        right = other.set_index("ts_code", drop=False)
        for column in right.columns:
            if column in {"ts_code", "trade_date"}:
                continue
            if column in left.columns:
                left[column] = left[column].combine_first(right[column])
            else:
                left[column] = right[column]
        return left.reset_index(drop=True)

    authoritative = enrich(authoritative, source)
    authoritative = enrich(authoritative, market["stock_basic"])
    authoritative["signal_date"] = signal_date
    eligible, eligibility_audit = filter_standard_limit_universe(
        authoritative,
        code_col="ts_code",
        name_col="name",
    )
    stage = pd.to_numeric(eligible["limit_times"], errors="coerce").round()
    pool = eligible.loc[stage.isin((2.0, 3.0))].copy()
    pool["limit_times"] = stage.loc[pool.index]
    pool["stage"] = stage.loc[pool.index]
    pool["signal_date"] = signal_date
    pool = pool.sort_values("ts_code", kind="stable").reset_index(drop=True)
    return pool, {
        "universe_rule": "exact_d_limit_list_hard_stage_only",
        "authoritative_limit_rows": int(len(authoritative)),
        "eligible_standard_limit_rows": int(len(eligible)),
        "hard_stage_rows": int(len(pool)),
        "eligibility": eligibility_audit,
    }


def _ensure_empty_promotion_schema(
    frame: pd.DataFrame,
    loaded: Any,
) -> pd.DataFrame:
    if not frame.empty:
        return frame
    output = frame.copy()
    bundle = loaded.payloads["promotion"].get("bundle")
    if not isinstance(bundle, ProbabilityHeadBundle):
        raise PrimaryDGenerationError("READY promotion bundle is unavailable")
    for column in bundle.feature_builder.numeric_columns:
        if column not in output.columns:
            output[column] = pd.Series(dtype=float)
    return output


def bind_committed_static_inputs(
    root: Path,
    verifier: GitHeadInputVerifier,
    *,
    validation_relative: str,
    loaded: Any,
) -> dict[str, Any]:
    """Bind every non-D file used by the isolated primary runtime to HEAD."""

    promotion_meta = loaded.metadata.get("promotion", {})
    promotion_path = Path(str(promotion_meta.get("path") or "")).resolve()
    try:
        promotion_relative = promotion_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise PrimaryDGenerationError(
            "promotion artifact escaped repository root"
        ) from exc
    if loaded.runtime_ledger_path is None:
        raise PrimaryDGenerationError("promotion runtime prior ledger path is missing")
    try:
        ledger_relative = loaded.runtime_ledger_path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise PrimaryDGenerationError(
            "promotion runtime prior ledger escaped repository root"
        ) from exc
    source = loaded.validation.get("source")
    if not isinstance(source, Mapping):
        raise PrimaryDGenerationError("promotion validation source binding is missing")
    manifest_relative = Path(str(source.get("ledger_manifest_path") or "")).as_posix()
    categories = {
        "runtime_code": list(PRIMARY_RUNTIME_CODE_PATHS),
        "promotion_prior_sources": list(PROMOTION_PRIOR_SOURCE_PATHS),
        "model": [
            validation_relative,
            promotion_relative,
            ledger_relative,
            manifest_relative,
        ],
    }
    output: dict[str, Any] = {"git_head": verifier.head}
    for category, paths in categories.items():
        if any(not path or Path(path).is_absolute() for path in paths):
            raise PrimaryDGenerationError(
                f"committed {category} path inventory is invalid"
            )
        output[category] = [
            verifier.bind(path, label=f"committed primary {category} input")
            for path in paths
        ]
    if str(promotion_meta.get("artifact_sha256") or "") != _sha256(
        root / promotion_relative
    ):
        raise PrimaryDGenerationError("promotion artifact binding drifted after load")
    if loaded.runtime_ledger_sha256 != _sha256(root / ledger_relative):
        raise PrimaryDGenerationError("promotion prior ledger binding drifted after load")
    return output


def _finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def _current_base_loss_reason(
    engine: PrimaryDReadOnlyEngine,
    signal_date: str,
    candidate: pd.Series,
) -> str:
    code = str(candidate.get("ts_code") or "")
    daily = engine._row(engine.market_table(signal_date, "daily"), code)  # noqa: SLF001
    if daily is None:
        return "MISSING_EXACT_D_DAILY_ROW"
    close = _finite_number(daily.get("close"))
    if not math.isfinite(close) or close <= 0:
        return "INVALID_EXACT_D_CLOSE"
    limit = engine._row(engine.market_table(signal_date, "stk_limit"), code)  # noqa: SLF001
    if limit is None:
        return "MISSING_EXACT_D_STK_LIMIT_ROW"
    up_limit = _finite_number(limit.get("up_limit"))
    if not math.isfinite(up_limit) or up_limit <= 0:
        return "INVALID_EXACT_D_UP_LIMIT"
    if abs(close - up_limit) > max(0.01, abs(up_limit) * 0.0025):
        return "EXACT_D_CLOSE_NOT_AT_UP_LIMIT"
    mechanism = _finite_number(candidate.get("decision_limit_pct"))
    if not math.isfinite(mechanism):
        mechanism = 100.0 * engine._limit_ratio(daily, limit)  # noqa: SLF001
    if mechanism > engine.config.max_mechanism_limit_pct + 1e-9:
        return "MECHANISM_LIMIT_ABOVE_PRIMARY_CAP"
    return "UNEXPLAINED_CURRENT_BASE_EXCLUSION"


def audit_complete_hard_pool(
    pool: pd.DataFrame,
    primary_base: pd.DataFrame,
    inference: pd.DataFrame,
    *,
    engine: PrimaryDReadOnlyEngine,
    signal_date: str,
) -> dict[str, Any]:
    """Require a one-to-one hard-stage -> inference identity mapping."""

    pool_codes = pool.get("ts_code", pd.Series(dtype=str)).astype(str)
    if pool_codes.duplicated().any():
        raise PrimaryDGenerationError("hard-stage pool contains duplicate identities")
    base_by_code = (
        primary_base.set_index("ts_code", drop=False)
        if not primary_base.empty
        else pd.DataFrame()
    )
    inference_by_code = (
        inference.set_index("ts_code", drop=False)
        if not inference.empty
        else pd.DataFrame()
    )
    rows: list[dict[str, Any]] = []
    losses: list[dict[str, Any]] = []
    for _, candidate in pool.sort_values("ts_code", kind="stable").iterrows():
        code = str(candidate["ts_code"])
        declared_value = _finite_number(candidate.get("limit_times"))
        declared_stage = int(round(declared_value)) if math.isfinite(declared_value) else 0
        reason = ""
        computed_stage: int | None = None
        if base_by_code.empty or code not in base_by_code.index:
            reason = _current_base_loss_reason(engine, signal_date, candidate)
        else:
            base_row = base_by_code.loc[code]
            if isinstance(base_row, pd.DataFrame):
                reason = "DUPLICATE_CURRENT_BASE_IDENTITY"
            else:
                computed_value = _finite_number(base_row.get("limit_times"))
                if math.isfinite(computed_value):
                    computed_stage = int(round(computed_value))
                if computed_stage != declared_stage:
                    reason = "RECOMPUTED_STAGE_MISMATCH"
                elif inference_by_code.empty or code not in inference_by_code.index:
                    reason = "RUNTIME_AUGMENT_DROPPED_HARD_IDENTITY"
        row = {
            "ts_code": code,
            "declared_stage": declared_stage,
            "computed_stage": computed_stage,
            "status": "INCLUDED" if not reason else "DROPPED",
            "reason": reason,
        }
        rows.append(row)
        if reason:
            losses.append(row)
    inference_codes = set(
        inference.get("ts_code", pd.Series(dtype=str)).astype(str)
    )
    extras = sorted(inference_codes - set(pool_codes))
    if extras:
        raise PrimaryDGenerationError(
            f"runtime inference invented non-hard identities: {extras}"
        )
    if losses:
        detail = _canonical_json_bytes(losses).decode("utf-8")
        raise PrimaryDGenerationError(
            f"hard-stage identities were not preserved into inference: {detail}"
        )
    if len(inference) != len(pool):
        raise PrimaryDGenerationError(
            "hard-stage/inference row count drifted without an attributable identity"
        )
    return {
        "hard_stage_row_count": int(len(pool)),
        "current_base_row_count": int(len(primary_base)),
        "inference_row_count": int(len(inference)),
        "loss_count": 0,
        "all_hard_identities_preserved": True,
        "rows": rows,
    }


def _runtime_identity_sha256(frame: pd.DataFrame, signal_date: str) -> str:
    rows = []
    if not frame.empty:
        ordered = frame.sort_values("ts_code", kind="stable")
        for _, row in ordered.iterrows():
            rank_value = _finite_number(row.get("promotion_rank"))
            selected_value = _finite_number(row.get("top10_selected"))
            rows.append(
                {
                    "identity": str(row.get("identity") or ""),
                    "ts_code": str(row.get("ts_code") or ""),
                    "stage_transition": str(row.get("stage_transition") or ""),
                    "top10_selected": int(selected_value),
                    "promotion_rank": int(rank_value),
                }
            )
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "schema": RUNTIME_IDENTITY_SCHEMA,
                "signal_date": signal_date,
                "rows": rows,
            }
        )
    ).hexdigest()


def build_runtime_feature_snapshot(
    snapshot: ThreeEngineSnapshotScore,
    loaded: Any,
    *,
    signal_date: str,
    generated_at_utc: str,
    hard_pool_size: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Seal the scorer's full hard pool, including raw inputs and selection."""

    generated = str(generated_at_utc or "").strip()
    if not generated:
        raise PrimaryDGenerationError("runtime snapshot generated_at_utc is missing")
    frame = snapshot.rows.copy()
    if len(frame) != hard_pool_size or snapshot.promotion_pool_size != hard_pool_size:
        raise PrimaryDGenerationError(
            "runtime snapshot is not the complete exact-D hard-stage pool"
        )
    required = {
        "signal_date",
        "ts_code",
        "name",
        "industry",
        "stage",
        "stage_transition",
        "board",
        "feature_snapshot_sha256",
        "top10_selected",
        "promotion_rank",
        "predicted_promotion_probability",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise PrimaryDGenerationError(
            f"runtime snapshot omitted required columns: {missing}"
        )
    frame["generated_at_utc"] = generated
    frame["signal_date"] = frame["signal_date"].map(_normal_date)
    frame["ts_code"] = frame["ts_code"].map(_normal_code)
    frame["stage_transition"] = frame["stage_transition"].fillna("").astype(str)
    frame["identity"] = (
        frame["signal_date"].astype(str)
        + "|"
        + frame["ts_code"].astype(str)
        + "|"
        + frame["stage_transition"]
    )
    if (
        not frame["signal_date"].eq(signal_date).all()
        or frame["ts_code"].eq("").any()
        or frame["ts_code"].duplicated().any()
        or frame["identity"].duplicated().any()
        or not frame["stage_transition"].isin(("2→3", "3→4")).all()
    ):
        raise PrimaryDGenerationError("runtime snapshot identity is invalid")
    if not frame.empty:
        ranks = pd.to_numeric(frame["promotion_rank"], errors="coerce")
        selected = pd.to_numeric(frame["top10_selected"], errors="coerce")
        probability = pd.to_numeric(
            frame["predicted_promotion_probability"], errors="coerce"
        )
        expected_ranks = list(range(1, len(frame) + 1))
        if not ranks.notna().all() or sorted(ranks.astype(int).tolist()) != expected_ranks:
            raise PrimaryDGenerationError("runtime promotion ranks are not complete")
        expected_selected = ranks.le(min(10, len(frame))).astype(int)
        if (
            not probability.map(math.isfinite).all()
            or not probability.between(0.0, 1.0).all()
            or not selected.isin((0, 1)).all()
            or not selected.astype(int).equals(expected_selected)
        ):
            raise PrimaryDGenerationError(
                "runtime promotion probability/selection is not a true scored TopN"
            )
        frame["promotion_rank"] = ranks.astype("Int64")
        frame["top10_selected"] = selected.astype("Int64")
    bundle = loaded.payloads["promotion"].get("bundle")
    if not isinstance(bundle, ProbabilityHeadBundle):
        raise PrimaryDGenerationError("READY promotion bundle is unavailable")
    raw_columns = list(bundle.feature_builder.numeric_columns)
    missing_raw = sorted(set(raw_columns) - set(frame.columns))
    if missing_raw:
        raise PrimaryDGenerationError(
            f"runtime snapshot omitted promotion raw features: {missing_raw}"
        )
    if not frame.empty and not frame["feature_snapshot_sha256"].eq(
        snapshot.feature_snapshot_sha256
    ).all():
        raise PrimaryDGenerationError("runtime feature snapshot hash column drifted")
    leading = [
        "signal_date",
        "ts_code",
        "name",
        "industry",
        "stage",
        "stage_transition",
        "board",
        "generated_at_utc",
        "identity",
        "feature_snapshot_sha256",
        "top10_selected",
        "promotion_rank",
        "predicted_promotion_probability",
    ]
    frame = frame[
        leading + [column for column in frame.columns if column not in leading]
    ]
    selected_count = int(
        pd.to_numeric(frame["top10_selected"], errors="coerce").fillna(0).sum()
    )
    return frame, {
        "runtime_identity_schema": RUNTIME_IDENTITY_SCHEMA,
        "runtime_identity_sha256": _runtime_identity_sha256(frame, signal_date),
        "runtime_feature_row_count": int(len(frame)),
        "runtime_selected_count": selected_count,
        "runtime_raw_feature_columns": raw_columns,
        "runtime_raw_feature_columns_sha256": hashlib.sha256(
            _canonical_json_bytes(raw_columns)
        ).hexdigest(),
    }


def build_primary_contract(
    snapshot: ThreeEngineSnapshotScore,
    *,
    signal_date: str,
    exec_date: str,
    exit_date: str,
    generated_at_utc: str = "",
) -> dict[str, Any]:
    promotion = snapshot.model_metadata.get("promotion", {})
    if promotion.get("status") != "READY":
        raise PrimaryDGenerationError(
            f"promotion is not READY: {promotion.get('status')}"
        )
    if snapshot.diagnostics.get("runtime_feature_gate_passed") is not True:
        raise PrimaryDGenerationError("promotion runtime feature gate failed")
    if snapshot.diagnostics.get("runtime_promotion_priors_attached") is not True:
        raise PrimaryDGenerationError("promotion runtime prior ledger is unavailable")
    rows = snapshot.rows.where(pd.notna(snapshot.rows), None).to_dict("records")
    explicit_models = {
        head: dict(snapshot.model_metadata.get(head, {}))
        for head in ("promotion", "big_loss", "profit")
    }
    for head in ("big_loss", "profit"):
        explicit_models[head] = {
            "status": "NOT_READY_PRIMARY_ONLY",
            "version": "",
            "as_of_date": "",
            "artifact_sha256": "",
            "validation_gate_pass_count": None,
            "validation_gate_total_count": None,
            "validation_gate_score_pct": None,
        }
    contract = build_three_rank_contract(
        {
            "signal_date": signal_date,
            "exec_date": exec_date,
            "exit_date": exit_date,
            "generated_at_utc": generated_at_utc,
            "feature_snapshot_sha256": snapshot.feature_snapshot_sha256,
            "top10_members_sha256": snapshot.top10_members_sha256,
            "candidates": rows,
            "model": {"three_rank_models": explicit_models},
        }
    )
    validate_three_rank_contract(contract)
    if (
        contract["models"]["promotion"]["status"] != "READY"
        or contract["models"]["big_loss"]["status"] != "NOT_READY_PRIMARY_ONLY"
        or contract["models"]["profit"]["status"] != "NOT_READY_PRIMARY_ONLY"
    ):
        raise PrimaryDGenerationError("primary-only contract scope drifted")
    if any(
        row["big_loss_safety_rank"] is not None
        or row["predicted_big_loss_probability"] is not None
        or row["profit_rank"] is not None
        or row["predicted_profit_probability"] is not None
        or row["p_fill_shadow_rank"] is not None
        or row["p_fill_shadow_probability"] is not None
        for row in contract["rows"]
    ):
        raise PrimaryDGenerationError("secondary output leaked into primary contract")
    return contract


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = _canonical_json_bytes(value, pretty=True)
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
            raise PrimaryDGenerationError(f"immutable receipt drifted: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_immutable_csv(path: Path, frame: pd.DataFrame) -> None:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
            raise PrimaryDGenerationError(f"immutable runtime snapshot drifted: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_primary_three_rank(
    root: Path,
    signal_date: str,
    *,
    generation_mode: str = "NATURAL",
) -> dict[str, Any]:
    root = root.resolve()
    signal_date = _normal_date(signal_date)
    mode = str(generation_mode or "").strip().upper()
    if DATE_RE.fullmatch(signal_date) is None:
        raise PrimaryDGenerationError("signal_date must be YYYYMMDD")
    if mode not in GENERATION_MODES:
        raise PrimaryDGenerationError(f"generation_mode must be one of {GENERATION_MODES}")

    verifier = GitHeadInputVerifier(root)
    exec_date, exit_date, calendar_binding = load_strict_sse_dates(
        root,
        signal_date,
        verifier=verifier,
    )
    history_binding = bind_committed_history_context(
        root,
        signal_date,
        calendar_binding["historical_dates"],
        verifier,
    )
    source, candidate_binding = load_exact_candidate_source(
        root, signal_date, exec_date
    )
    market, market_binding = load_exact_market_package(root, signal_date)
    pool, pool_audit = build_exact_primary_pool(source, market, signal_date)

    validation_relative = "models/decision_three_engines/validation_latest.json"
    validation_path = _repository_file(
        root, validation_relative, label="promotion validation manifest"
    )
    loaded = load_promotion_only_artifacts(validation_path, root=root)
    static_binding = bind_committed_static_inputs(
        root,
        verifier,
        validation_relative=validation_relative,
        loaded=loaded,
    )
    engine = PrimaryDReadOnlyEngine(
        AuctionV3Config(root=root),
        signal_date=signal_date,
        context_dates=list(calendar_binding["runtime_context_dates"]),
        verifier=verifier,
        exact_d_inventory=market_binding["tables"],
    )
    # The persisted promotion bundle was trained on the canonical current-base
    # surface (mechanism limit, D-only streak context, stock priors, and pool
    # context).  Starting from the raw limit-list rows would silently omit
    # those primary features.  For a truthful zero-candidate day there is no
    # row to enrich, so retain the explicit empty hard-range schema instead.
    primary_base = (
        engine._current_base(signal_date, pool)  # noqa: SLF001 - canonical P0 surface
        if not pool.empty
        else pool
    )
    inference = augment_three_engine_runtime_base(
        engine,
        signal_date,
        primary_base,
    )
    pool_audit["hard_to_inference"] = audit_complete_hard_pool(
        pool,
        primary_base,
        inference,
        engine=engine,
        signal_date=signal_date,
    )
    inference = _ensure_empty_promotion_schema(inference, loaded)
    snapshot = score_three_engine_snapshot(
        inference,
        loaded,
        signal_date=signal_date,
        top_n=10,
    )
    if snapshot.promotion_pool_size != len(inference):
        raise PrimaryDGenerationError("promotion pool size changed during scoring")
    if set(snapshot.rows.get("ts_code", pd.Series(dtype=str)).astype(str)) != set(
        inference.get("ts_code", pd.Series(dtype=str)).astype(str)
    ):
        raise PrimaryDGenerationError("promotion scorer changed hard-pool identities")
    selected = int(pd.to_numeric(snapshot.rows.get("top10_selected"), errors="coerce").fillna(0).sum())
    if selected != min(10, len(inference)):
        raise PrimaryDGenerationError("promotion did not select the exact real TopN")
    contract = build_primary_contract(
        snapshot,
        signal_date=signal_date,
        exec_date=exec_date,
        exit_date=exit_date,
        generated_at_utc=candidate_binding["created_at_utc"],
    )
    json_path, csv_path, materialized = materialize_three_rank_artifacts(root, contract)
    runtime_frame, runtime_binding = build_runtime_feature_snapshot(
        snapshot,
        loaded,
        signal_date=signal_date,
        generated_at_utc=candidate_binding["created_at_utc"],
        hard_pool_size=len(pool),
    )
    runtime_path = (
        root
        / "outputs"
        / "decision"
        / f"primary_d_runtime_features_{signal_date}.csv"
    )
    _write_immutable_csv(runtime_path, runtime_frame)
    runtime_binding.update(
        {
            "runtime_features_path": runtime_path.relative_to(root).as_posix(),
            "runtime_features_sha256": _sha256(runtime_path),
        }
    )
    validation_sha = _sha256(validation_path)
    promotion_meta = materialized["models"]["promotion"]
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "artifact_kind": "p0_promotion_only_d_list_receipt",
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
        "generation_mode": mode,
        "prospective": mode == "NATURAL",
        "forward_eligible": mode == "NATURAL",
        "not_forward_generated": mode != "NATURAL",
        "recovered_at_utc": (
            candidate_binding["created_at_utc"]
            if mode == "RETROSPECTIVE_RECOVERY"
            else ""
        ),
        "nominal_source_cutoff_bj": (
            f"{signal_date[:4]}-{signal_date[4:6]}-{signal_date[6:]}T21:15:00+08:00"
        ),
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "primary_status": "READY",
        "action_authorized": False,
        "action_input_consumed": False,
        "formal_trade_count": 0,
        "shadow_forward_ledger_eligible": False,
        "future_market_data_consumed": False,
        "latest_fallback_used": False,
        "secondary_outputs_generated": {
            "action_plan": False,
            "big_loss": False,
            "profit": False,
            "p_fill_shadow": False,
            "executable_profit": False,
        },
        "inputs": {
            "calendar": calendar_binding,
            "committed_history_context": history_binding,
            "committed_static_inputs": static_binding,
            "git_head": verifier.head,
            "candidate": candidate_binding,
            "market": market_binding,
            "model_validation": {
                "path": validation_relative,
                "sha256": validation_sha,
            },
            "promotion_model": {
                "version": promotion_meta["version"],
                "as_of_date": promotion_meta["model_as_of_date"],
                "artifact_sha256": promotion_meta["artifact_sha256"],
            },
            "runtime_prior_ledger": {
                "path": (
                    loaded.runtime_ledger_path.relative_to(root).as_posix()
                    if loaded.runtime_ledger_path is not None
                    else ""
                ),
                "sha256": loaded.runtime_ledger_sha256,
            },
            "runtime_consumed_market_files": engine.consumed_bindings(),
        },
        "pool_audit": pool_audit,
        "outputs": {
            "json_path": json_path.relative_to(root).as_posix(),
            "json_sha256": _sha256(json_path),
            "csv_path": csv_path.relative_to(root).as_posix(),
            "csv_sha256": _sha256(csv_path),
            "index_path": "outputs/decision/three_rank_index.json",
            "bundle_sha256": materialized["bundle_sha256"],
            "feature_snapshot_sha256": materialized["feature_snapshot_sha256"],
            "top10_members_sha256": materialized["top10_members_sha256"],
            "promotion_pool_size": materialized["promotion_pool_size"],
            "top10_count": materialized["top10_count"],
            **runtime_binding,
        },
        "invariants": {
            "strict_sse_d_t_tplus1": True,
            "exact_d_candidate_archive": True,
            "exact_d_market_package": True,
            "exact_d_stk_limit_required": True,
            "fixed_committed_20_session_history": True,
            "runtime_market_reads_hash_bound": True,
            "promotion_only_artifacts_loaded": True,
            "complete_hard_pool_runtime_snapshot": True,
            "hard_pool_identity_loss_forbidden": True,
            "real_n_up_to_10_without_padding": True,
            "zero_candidates_allowed": True,
            "immutable_d_bundle": True,
        },
    }
    receipt_path = root / "outputs" / "decision" / f"primary_d_receipt_{signal_date}.json"
    _write_immutable_json(receipt_path, receipt)
    return {
        "contract": materialized,
        "receipt": receipt,
        "paths": {
            "json": json_path,
            "csv": csv_path,
            "runtime_features": runtime_path,
            "receipt": receipt_path,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--signal-date", required=True, help="D in YYYYMMDD")
    parser.add_argument(
        "--generation-mode",
        choices=GENERATION_MODES,
        default="NATURAL",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = publish_primary_three_rank(
            Path(args.root),
            args.signal_date,
            generation_mode=args.generation_mode,
        )
    except (PrimaryDGenerationError, ThreeEngineArtifactError, ThreeRankContractError) as exc:
        print(f"[P0 BLOCK] {exc}", file=sys.stderr)
        return 2
    contract = result["contract"]
    print(
        json.dumps(
            {
                "status": "READY",
                "signal_date": contract["signal_date"],
                "exec_date": contract["exec_date"],
                "exit_date": contract["exit_date"],
                "promotion_pool_size": contract["promotion_pool_size"],
                "top10_count": contract["top10_count"],
                "bundle_sha256": contract["bundle_sha256"],
                "receipt": result["paths"]["receipt"].as_posix(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
