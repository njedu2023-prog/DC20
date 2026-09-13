"""Read-only, SHA-bound P0 sources -> unverified research D projection.

No network, writes, fitting, scoring, model loading or source authority. Four
external expected hashes must come from the caller's independent P0 evidence.
Receipt hashes prove byte identity, NOT Git membership, availability or a new
prediction freeze. Only this D's registered source paths may be mapped.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import stat


SCHEMA = "dc20_bound_p0_candidate_d_source_projection_20260913_v1"
CODE_ROOT = Path(__file__).absolute().parents[2]
CODE_PINS = {
    "scripts/publish_primary_three_rank.py": "5996a8f9e0b55f34c22e4e10e78924616cf8a8ebfb08be789ed169a9b3bb8d98",
    "work/profit_1000_upgrade/candidate_d_feature_projection.py": "aabe73a52482626ab71b042cf4a7c4a1548e6131b7b231707d2d30d29348188f",
    "work/profit_1000_upgrade/candidate_live_feature_math.py": "922686579ade03fb3818dfa27f015bf5888f61199bfce08e35d5fd6dbceebc68",
    "src/top10decision/decision/d_close_features.py": "c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e",
    "src/top10decision/decision/three_rank.py": "f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a",
}
HISTORY_PATH = "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
HISTORY_SHA = "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46"
HISTORY_ROWS = 12322
CALENDAR_PATH = "data/market/trade_cal_sse.csv"
MAX_BYTES = 64 * 1024 * 1024
RUNTIME_ID = ("signal_date", "ts_code", "stage_transition", "identity", "feature_snapshot_sha256",
    "promotion_model_as_of_date", "promotion_model_artifact_sha256")
RUNTIME_NUMERIC = ("stage", "promotion_rank", "top10_selected", "five_year_board_stage_delta",
    "five_year_streak_runup", "five_year_pre_streak_1d_return", "five_year_recent_20d_rate",
    "five_year_recent_60d_rate", "focus_pool_size", "stage2_pool_size", "stage3_pool_size", "stage_pool_share")


class DSourceBlocked(ValueError):
    """Fail closed; no usable projection or scoring authority is returned."""


def require(ok, reason):
    if not ok:
        raise DSourceBlocked(reason)


def _sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "EXACT_SHA256_REQUIRED")
    return value


def _path(value, *, directory=False):
    require(type(value) in (str, Path) or type(value) is type(Path()), "EXACT_PATH_REQUIRED")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts
        and not any(p.is_symlink() for p in (path, *path.parents)), "ABSOLUTE_UNALIASED_PATH_REQUIRED")
    require(path.is_dir() if directory else path.is_file(), "MISSING_REGISTERED_SOURCE_OR_ROOT")
    if not directory:
        require(stat.S_ISREG(path.stat().st_mode) and path.stat().st_nlink == 1, "REGULAR_SINGLE_LINK_SOURCE_REQUIRED")
    return path


def _fingerprint(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _read(path):
    path = _path(path)
    before = _fingerprint(path)
    require(before[4] <= MAX_BYTES, "SOURCE_SIZE_LIMIT")
    with path.open("rb") as stream:
        body = stream.read(MAX_BYTES + 1)
    require(len(body) <= MAX_BYTES and len(body) == before[4]
        and _fingerprint(_path(path)) == before, "SOURCE_CHANGED_DURING_READ")
    return body, before


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _git_blob(body):
    return hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()


def _code_snapshot():
    paths = {CODE_ROOT / name: expected for name, expected in CODE_PINS.items()}
    paths[Path(__file__).absolute()] = SELF_SHA
    result = []
    for path, expected in paths.items():
        body, identity = _read(path)
        require(_digest(body) == expected, "FROZEN_CODE_SHA_CHANGED")
        result.append((str(path), expected, identity))
    return tuple(result)


SELF_SHA = _digest(_read(Path(__file__).absolute())[0])
_code_snapshot()  # Verify fixed dependency bytes before importing them.
from scripts import publish_primary_three_rank as publisher
from work.profit_1000_upgrade import candidate_d_feature_projection as projection
from top10decision.decision import three_rank


def _code_guard():
    modules = (publisher, projection, projection.feature_math, projection.feature_math.historical, three_rank)
    for module, relative in zip(modules, CODE_PINS):
        require(Path(module.__file__).absolute() == CODE_ROOT / relative, "DEPENDENCY_IMPORT_ORIGIN_CHANGED")
    validator = publisher.validate_three_rank_contract
    require(validator is three_rank.validate_three_rank_contract
        and getattr(validator, "__module__", None) == "top10decision.decision.three_rank"
        and getattr(getattr(validator, "__code__", None), "co_filename", None)
            == str(CODE_ROOT / "src/top10decision/decision/three_rank.py"), "ACTUAL_THREE_RANK_VALIDATOR_ORIGIN_CHANGED")
    return _code_snapshot()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(body):
    value = json.loads(body, object_pairs_hook=_object,
        parse_constant=lambda _: require(False, "NONFINITE_JSON"))
    require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    return value


def _csv(body, required):
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig"), newline=""))
    header = next(reader, None)
    require(header is not None and len(header) == len(set(header)) and set(required) <= set(header),
        "UNIQUE_REQUIRED_CSV_COLUMNS")
    indices = {key: header.index(key) for key in required}
    result = []
    for row in reader:
        require(len(row) == len(header), "EXACT_CSV_ROW_WIDTH")
        # Unknown columns are not retained or interpreted as feature/outcome values.
        result.append({key: row[index] for key, index in indices.items()})
    return result


def _number(value, *, nullable=False, integer=False):
    require(type(value) is str, "NATIVE_CSV_STRING_REQUIRED")
    if nullable and value == "":
        return None
    if integer:
        require(re.fullmatch(r"0|[1-9][0-9]*", value), "EXACT_INTEGER_CSV_ENCODING_REQUIRED")
        return int(value)
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise DSourceBlocked("FINITE_NUMERIC_CSV_REQUIRED") from None
    require(math.isfinite(number), "FINITE_NUMERIC_CSV_REQUIRED")
    return number


def _p0_paths(day):
    return {"receipt": f"outputs/decision/primary_d_receipt_{day}.json",
        "runtime_features": f"outputs/decision/primary_d_runtime_features_{day}.csv",
        "three_rank_json": f"outputs/decision/three_rank_top10_{day}.json",
        "three_rank_csv": f"outputs/decision/three_rank_top10_{day}.csv"}


def _arguments(source_root, signal_date, expected, mapping):
    root, day = _path(source_root, directory=True), projection._date(signal_date)
    require(type(expected) is dict and set(expected) == set(_p0_paths(day)), "EXACT_FOUR_EXTERNAL_P0_HASHES_REQUIRED")
    hashes = {key: _sha(expected[key]) for key in _p0_paths(day)}
    require(mapping is None or type(mapping) is dict, "EXPLICIT_SOURCE_MAP_REQUIRED")
    paths = {}
    for key, value in (mapping or {}).items():
        require(type(key) is str and not Path(key).is_absolute() and ".." not in Path(key).parts
            and Path(key).as_posix() == key, "EXACT_REGISTERED_LOGICAL_PATH_REQUIRED")
        paths[key] = str(_path(value))
    return str(root), day, hashes, paths


def _registration(receipt, contract, day):
    require(receipt.get("signal_date") == contract.get("signal_date") == day, "EXACT_D_NOT_REDATED_P0_REQUIRED")
    inputs = receipt["inputs"]
    calendar, market, history = inputs["calendar"], inputs["market"], inputs["runtime_prior_ledger"]
    require(calendar["path"] == CALENDAR_PATH and history == {"path": HISTORY_PATH, "sha256": HISTORY_SHA},
        "FIXED_COMPLETE_HISTORY_AND_CALENDAR_REQUIRED")
    dates = calendar["runtime_context_dates"]
    require(type(dates) is list and len(dates) == 21 and dates == sorted(set(dates)) and dates[-1] == day
        and all(projection._date(d) <= day for d in dates), "EXACT_21_D_ONLY_REGISTERED_DATES_REQUIRED")
    require(calendar["historical_dates"] == dates[:-1] and calendar["historical_session_count"] == 20
        and type(calendar["historical_session_count"]) is int and calendar["signal_date_is_open"] is True,
        "REGISTERED_CALENDAR_CONTEXT_MISMATCH")
    declarations = inputs["runtime_consumed_market_files"]
    require(type(declarations) is list and all(type(x) is dict for x in declarations), "REGISTERED_MARKET_LIST_REQUIRED")
    # Validate the dates of all declared market sources before opening any values.
    for item in declarations:
        require(projection._date(item["trade_date"]) <= day, "FUTURE_REGISTERED_MARKET_SOURCE")
    daily = [item for item in declarations if item.get("table") == "daily"]
    require(len(daily) == 21 and sorted(item["trade_date"] for item in daily) == dates, "EXACT_REGISTERED_DAILY_SET_REQUIRED")
    committed = inputs["committed_history_context"]
    historical = [item for item in committed["files"] if item.get("table") == "daily"]
    require(committed["dates"] == dates[:-1] and len(historical) == 20
        and sorted(item["trade_date"] for item in historical) == dates[:-1], "EXACT_COMMITTED_HISTORY_DAILY_SET_REQUIRED")
    historical_by_date = {item["trade_date"]: item for item in historical}
    registered = {HISTORY_PATH: HISTORY_SHA, CALENDAR_PATH: _sha(calendar["sha256"])}
    for item in daily:
        d = item["trade_date"]
        require(item["path"] == f"data/market/raw/{d[:4]}/{d}/daily.csv", "STRICT_DAILY_REGISTRATION_REQUIRED")
        common = {"path", "sha256", "trade_date", "table"}
        if d == day:
            require(set(item) == common | {"row_count", "date_scoped"} and item["date_scoped"] is True
                and type(item["row_count"]) is int and item["row_count"] >= 0, "STRICT_D_DAILY_REGISTRATION_REQUIRED")
        else:
            # Actual committed historical records do not declare row_count or
            # date_scoped. They instead bind exact Git blobs; do not invent
            # those absent fields or silently accept a third record shape.
            require(set(item) == common | {"git_blob_sha1", "git_mode"} and item["git_mode"] == "100644"
                and type(item["git_blob_sha1"]) is str and re.fullmatch(r"[0-9a-f]{40}", item["git_blob_sha1"])
                and item == historical_by_date[d], "STRICT_COMMITTED_DAILY_BLOB_REGISTRATION_REQUIRED")
        registered[item["path"]] = _sha(item["sha256"])
    meta_path = f"data/market/raw/{day[:4]}/{day}/_sync_meta.json"
    require(market["meta_path"] == meta_path, "EXACT_D_META_PATH_REQUIRED")
    registered[meta_path] = _sha(market["meta_sha256"])
    require(len(registered) == 24, "EXACT_REGISTERED_SOURCE_SET_REQUIRED")
    return registered, daily, dates, market


def _identities(runtime, history, daily_rows, day):
    seen = set()
    for row in runtime:
        require(projection._date(row["signal_date"]) == day, "RUNTIME_OUTSIDE_D")
        code = projection._code(row["ts_code"])
        require(code not in seen, "DUPLICATE_RUNTIME_CODE")
        seen.add(code)
        require(projection._date(row["promotion_model_as_of_date"]) < day, "PROMOTION_TRAIN_END_NOT_BEFORE_D")
    seen = set()
    for row in history:
        d, code = projection._date(row["signal_date"]), projection._code(row["ts_code"])
        require(d < day and d <= "20260814" and d < "20260914", "HISTORY_OUTSIDE_FIXED_SCOPE")
        require((d, code) not in seen, "DUPLICATE_HISTORY_IDENTITY")
        seen.add((d, code))
    require(len(history) == HISTORY_ROWS and max((x["signal_date"] for x in history), default=None) == "20260814",
        "COMPLETE_FIXED_HISTORY_ROWS_REQUIRED")
    for d, rows in daily_rows:
        seen = set()
        for row in rows:
            require(projection._date(row["trade_date"]) == d and d <= day, "DAILY_OUTSIDE_REGISTERED_DATE")
            # Registered full-market daily files also contain Beijing stocks.
            # They are identity-checked here, never admitted to the SH/SZ P0
            # candidate pool or passed to candidate feature math.
            code = row["ts_code"]
            require(type(code) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code), "EXACT_MARKET_DAILY_CODE_REQUIRED")
            require(code not in seen, "DUPLICATE_DAILY_CODE")
            seen.add(code)


def _native(runtime, history, daily_rows, contract):
    rows = [{**{key: row[key] for key in RUNTIME_ID},
        **{key: _number(row[key], integer=key in ("promotion_rank", "top10_selected"),
            nullable=key in projection.FIVE_YEAR_RETAINED) for key in RUNTIME_NUMERIC}} for row in runtime]
    prior = [{"signal_date": row["signal_date"], "ts_code": row["ts_code"],
        "promotion_hit": _number(row["promotion_hit"], nullable=True)} for row in history]
    bars = {row["ts_code"]: [] for row in contract["rows"]}
    for d, values in daily_rows:
        for row in values:
            if row["ts_code"] in bars:
                bars[row["ts_code"]].append({"trade_date": d, "ts_code": row["ts_code"],
                    "close": _number(row["close"]), "volume": _number(row["vol"], nullable=True)})
    return rows, prior, bars


def project_bound_p0_d(source_root, *, signal_date, expected_p0_sha256, source_path_map=None):
    """Return immutable projection plus byte bindings; never issue authority.

    The four expected hash keys are receipt/runtime_features/three_rank_json/
    three_rank_csv. Mapping keys are exact receipt source paths, not P0 paths.
    Missing/invalid evidence raises DSourceBlocked (some frozen validators raise
    ValueError); fewer than 21 observed bars returns explicit BLOCKED status.
    No fallback to latest, extra dates or replacement prices is permitted.
    """
    code_before = _code_guard()
    args = _arguments(source_root, signal_date, expected_p0_sha256, source_path_map)
    root_text, day, hashes, mapping = args
    root = Path(root_text)
    bindings = {}
    def read_bound(logical, expected, origin=None):
        path = Path(origin) if origin else root / logical
        body, identity = _read(path)
        require(_digest(body) == expected, "REGISTERED_OR_EXTERNAL_SOURCE_SHA_MISMATCH:" + logical)
        bindings[logical] = (str(path), expected, identity)
        return body
    p0 = {key: read_bound(path, hashes[key]) for key, path in _p0_paths(day).items()}
    receipt, contract = _json(p0["receipt"]), _json(p0["three_rank_json"])
    registered, daily, dates, market = _registration(receipt, contract, day)
    require(set(mapping) <= set(registered), "SOURCE_MAP_CANNOT_EXPAND_REGISTERED_SCOPE")
    sources = {path: read_bound(path, sha, mapping.get(path)) for path, sha in registered.items()}
    require(len({record[0] for record in bindings.values()}) == len(bindings), "SOURCE_ORIGIN_REUSED_FOR_DIFFERENT_PATHS")
    meta = _json(sources[market["meta_path"]])
    require(meta["trade_date"] == day and meta["source_repo"]["resolved_commit"] == market["resolved_commit"]
        and re.fullmatch(r"[0-9a-f]{40}", market["resolved_commit"])
        and meta["source_repo"]["owner"] + "/" + meta["source_repo"]["repo"] == market["source_repository"],
        "META_D_AND_UPSTREAM_BINDING_MISMATCH")
    latest = [x for x in daily if x["trade_date"] == day][0]
    market_daily = market["tables"]["daily"]
    require(all(type(market_daily.get(k)) is type(latest[k]) and market_daily[k] == latest[k]
        for k in ("path", "sha256", "row_count", "date_scoped")), "RECEIPT_D_DAILY_BINDINGS_DISAGREE")
    meta_daily = [x for x in meta["files"] if x.get("name") == "daily"]
    require(len(meta_daily) == 1 and meta_daily[0]["sha256"] == latest["sha256"]
        and meta_daily[0]["dated_path"] == latest["path"] and type(meta_daily[0]["bytes"]) is int
        and meta_daily[0]["bytes"] == len(sources[latest["path"]]), "META_DAILY_BINDING_MISMATCH")
    calendar = _csv(sources[CALENDAR_PATH], ("exchange", "cal_date", "is_open"))
    open_dates, seen = [], set()
    for row in calendar:
        d = projection._date(row["cal_date"])
        require(row["exchange"] == "SSE" and row["is_open"] in ("0", "1") and d not in seen,
            "EXACT_UNIQUE_SSE_CALENDAR_REQUIRED")
        seen.add(d)
        if row["is_open"] == "1": open_dates.append(d)
    open_dates.sort()
    require([d for d in open_dates if d <= day][-21:] == dates
        and [d for d in open_dates if d > day][:2] == [contract["exec_date"], contract["exit_date"]],
        "CALENDAR_D_CONTEXT_OR_T_TPLUS1_MISMATCH")
    runtime = _csv(p0["runtime_features"], RUNTIME_ID + RUNTIME_NUMERIC)
    with gzip.GzipFile(fileobj=io.BytesIO(sources[HISTORY_PATH])) as stream:
        history_body = stream.read(MAX_BYTES + 1)
    require(len(history_body) <= MAX_BYTES, "DECOMPRESSED_HISTORY_SIZE_LIMIT")
    history = _csv(history_body, ("signal_date", "ts_code", "promotion_hit"))
    daily_rows = []
    for item in daily:
        values = _csv(sources[item["path"]], ("trade_date", "ts_code", "close", "vol"))
        if item["trade_date"] == day:
            require(len(values) == item["row_count"], "REGISTERED_DAILY_ROW_COUNT_MISMATCH")
        else:
            require(_git_blob(sources[item["path"]]) == item["git_blob_sha1"], "COMMITTED_DAILY_GIT_BLOB_MISMATCH")
        daily_rows.append((item["trade_date"], values))
    _identities(runtime, history, daily_rows, day)  # Whole population before numeric/truth conversion.
    paths = {key: root / path for key, path in _p0_paths(day).items()}
    def index_now():
        return publisher.build_primary_d_runtime_index(root, receipt_path=paths["receipt"],
            runtime_path=paths["runtime_features"], three_rank_json_path=paths["three_rank_json"],
            three_rank_csv_path=paths["three_rank_csv"])
    index = index_now()
    native, prior, bars = _native(runtime, history, daily_rows, contract)
    train_end = receipt["inputs"]["promotion_model"]["as_of_date"]
    result = projection.project_candidate_d_features(native, contract, index, prior, bars, signal_date=day,
        promotion_train_end=train_end, promotion_train_end_kind=projection.TRAIN_END_KIND)
    require(index_now() == index, "P0_INDEX_CHANGED_DURING_PROJECTION")
    require(_arguments(source_root, signal_date, expected_p0_sha256, source_path_map) == args,
        "CALLER_SOURCE_ARGUMENTS_CHANGED")
    for logical, (path, expected, original_identity) in bindings.items():
        body, identity = _read(Path(path))
        require(_digest(body) == expected and identity == original_identity, "SOURCE_BYTES_OR_ORIGIN_CHANGED:" + logical)
    require(_code_guard() == code_before, "CODE_OR_ORIGIN_CHANGED_DURING_PROJECTION")
    require(_arguments(source_root, signal_date, expected_p0_sha256, source_path_map) == args,
        "CALLER_SOURCE_ARGUMENTS_CHANGED")
    return projection._freeze({"schema_version": SCHEMA, "signal_date": day, "status": result["status"],
        "projection": result, "four_file_bytes_verified": True, "registered_source_bytes_verified": True,
        "registered_daily_file_count": len(daily), "complete_history_row_count": len(history),
        "source_file_bindings": [{"receipt_path": key, "origin_path": record[0], "sha256": record[1],
            "bytes": record[2][4], "explicit_original_blob_mapping": key in mapping} for key, record in sorted(bindings.items())],
        "code_sha256": SELF_SHA, "dependency_sha256": dict(CODE_PINS),
        "source_independently_verified": False, "source_authority_issued": False,
        "git_membership_verified": False, "point_in_time_availability_verified": False,
        "natural_freeze_verified": False, "production_activation_allowed": False,
        "model_artifact_loaded_or_verified": False, "model_training_performed": False,
        "model_predictions_computed": False, "scoring_authorized": False, "research_only": True,
        "future_outcomes_read": False, "files_written": 0, "network_calls_performed": 0})


__all__ = ["project_bound_p0_d", "DSourceBlocked"]
