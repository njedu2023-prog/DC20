"""Private research admission for exactly evidenced full nontrading sessions.

Neither an old S event, a missing market row, nor an empty response is enough.
Admission combines the pinned diagnostic, the externally verified daily-gap
collection, the original label report and an exact candidate/minute inventory.
No HTTP, source writes, fabricated OHLC, labels or production activation.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import stat
from types import MappingProxyType
import weakref

from work.profit_1000_upgrade import suspension_truth as old
from work.profit_1000_upgrade import minute_truth
from work.profit_1000_upgrade import candidate_scope_verify as candidate_verify
from work.profit_1000_upgrade import minute_gap_verify as minute_verify
from top10decision.decision import shadow_exit_minute_truth as legacy_minutes

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
SOURCE_POLICY_ID = "dc20_research_exact_nontrading_session_20260913_v1"
AS_OF_DATE = "20260911"
PRIOR_LABEL_SHA = "2cca1e928b92a6cd46dabf7090714cfbc5e88682f2d93ecbc481c01ecd910609"
DAILY_FIELDS = ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg")
# No interval inference: every allowed date requires its own explicit S row.
GROUPS = (
    ("20240425", "20240426", "600234.SH", ("20240429",), 1, 2),
    ("20250606", "20250609", "603226.SH", ("20250610", "20250611", "20250612"), 3, 4),
    ("20251113", "20251114", "603122.SH", ("20251117", "20251118", "20251119"), 5, 6),
    ("20260703", "20260706", "603580.SH", ("20260707", "20260708", "20260709", "20260710", "20260713"), 7, 8),
    ("20260721", "20260722", "002036.SZ", ("20260723", "20260724", "20260727", "20260728", "20260729"), 9, 10),
    ("20240425", "20240426", "600083.SH", ("20240430",), None, None),
)
ALLOWED_PAIRS = tuple(sorted((day, code) for _, _, code, dates, _, _ in GROUPS for day in dates))
PINNED = {
    "work/profit_1000_upgrade/suspension_truth.py": "6c3a7b97104bab7093f590d9e08e01f1a7994375bde774769ef86bfbf24a2c34",
    "work/profit_1000_upgrade/minute_truth.py": "69f2eb9fe2ceb1579a59d5247a3f1ddc72b80d251abbed9fe69e06e0d83df268",
    "work/profit_1000_upgrade/candidate_scope_verify.py": "fa8d8ad5956680435693cd9000954566ae8ffb1273454706450042a4e777c905",
    "work/profit_1000_upgrade/minute_gap_verify.py": "4028d78eedf343f1b261d5be562751e3464bf5c7a0ffa218223cebcb463a2b92",
    "work/profit_1000_upgrade/daily_gap_verify.py": "a79938138467b465ab31d8650d35d99d7c43a0a7196109dde33bdba9d1982591",
    "src/top10decision/decision/shadow_exit_minute_truth.py": "0cdd36ed69e734ab8c59bb3b44a5bf27cc702a9ce67879a225f4a94d1d14ee65",
}
_KEY = object()
_ISSUED = weakref.WeakSet()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _root(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts and path.is_dir()
            and not any(p.is_symlink() for p in (path, *path.parents)), "UNALIASED_MARKET_ROOT_REQUIRED")
    return path.resolve(strict=True)


def _read(path, maximum):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts
            and not any(p.is_symlink() for p in (path, *path.parents)), "UNALIASED_SOURCE_REQUIRED")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= maximum,
            "BOUNDED_REGULAR_SOURCE_REQUIRED")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    require(len(raw) == info.st_size, "SOURCE_CHANGED_DURING_READ")
    return raw


def _sha(path, maximum=128 * 1024**2):
    return hashlib.sha256(_read(path, maximum)).hexdigest()


_SELF_SHA = _sha(Path(__file__))


def _guard():
    require(_sha(Path(__file__)) == _SELF_SHA, "NONTRADING_CODE_CHANGED")
    for name, digest in PINNED.items():
        require(_sha(CHECKOUT / name) == digest, "PINNED_NONTRADING_DEPENDENCY_CHANGED")


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if type(value) in (tuple, list):
        return tuple(_freeze(v) for v in value)
    return value


def _plain(value):
    if isinstance(value, MappingProxyType):
        return {k: _plain(v) for k, v in value.items()}
    if type(value) is tuple:
        return [_plain(v) for v in value]
    return value


def _inventory(root):
    root = _root(root)
    files, names, dirs, inodes, total, pending = {}, set(), set(), set(), 0, [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            children = list(entries)
        for entry in children:
            path = Path(entry.path)
            name = path.relative_to(root).as_posix()
            require(name.casefold() not in names and "\\" not in name and ":" not in name,
                    "ALIASED_MARKET_PATH")
            names.add(name.casefold())
            require(len(names) <= 40_000, "MARKET_ENTRY_LIMIT")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                dirs.add(name); pending.append(path); continue
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not path.is_symlink(),
                    "NONREGULAR_OR_HARDLINK_MARKET_SOURCE")
            require((info.st_dev, info.st_ino) not in inodes, "REUSED_MARKET_INODE")
            inodes.add((info.st_dev, info.st_ino))
            total += info.st_size
            require(len(files) < 20_000 and total <= 8 * 1024**3, "MARKET_SIZE_LIMIT")
            files[name] = _sha(path)
    expected_dirs = {p.as_posix() for name in files for p in Path(name).parents if p.as_posix() != "."}
    require(dirs == expected_dirs, "UNREGISTERED_EMPTY_MARKET_DIRECTORY")
    return files


def _bindings(items):
    result = {}
    for item in items:
        name = item["path"]
        require(type(name) is str and not name.startswith("/") and "\\" not in name
                and all(p not in ("", ".", "..") for p in name.split("/")) and name not in result,
                "INVALID_OR_DUPLICATE_SOURCE_BINDING")
        old._sha_value(item["sha256"])
        result[name] = item["sha256"]
    return result


def _complete(table, fields):
    table = _plain(table)
    require(type(table) is dict and set(table) == {"fields", "items", "count", "has_more"}
            and table["fields"] == list(fields) and type(table["items"]) is list
            and type(table["count"]) is int and table["count"] in (0, len(table["items"]))
            and table["has_more"] is False, "NONTRADING_REQUIRES_EXPLICIT_COMPLETE_TABLE")
    return table


def _old_evidence(archive_path):
    raw = _read(archive_path, old.MAX_BYTES)
    files, receipt = old._archive(raw)
    daily, events, bindings = {}, {}, []
    requests = {r["query_number"]: r for r in receipt["requests"]}
    require(set(requests) == set(range(1, 11)), "OLD_DIAGNOSTIC_REQUEST_SET_CHANGED")
    for signal, _, code, days, daily_id, event_id in GROUPS[:-1]:
        req = requests[daily_id]
        params = {"trade_date": days[0], "ts_code": code}
        require(req["endpoint"] == "daily" and req["params"] == params and req["signal_date"] == signal
                and req["fields"] == list(DAILY_FIELDS) and req["network_request_performed"] is True
                and req["status"] == "DAILY_ABSENT" and type(req["row_count"]) is int and req["row_count"] == 0,
                "OLD_DAILY_QUERY_IDENTITY_CHANGED")
        require(old._parse(files[f"receipts/{daily_id:02d}.json"]) == req, "OLD_DAILY_RECEIPT_MISMATCH")
        pairs = req["source_files"]
        require(len(pairs) == 2, "OLD_DAILY_PAIR_INVALID")
        data_raw, meta_raw = (files[p["path"]] for p in pairs)
        table = _complete(old._parse(data_raw), DAILY_FIELDS)
        require(table["items"] == [], "OLD_DAILY_NOT_EMPTY")
        meta = old._parse(meta_raw)
        require(meta == {"credential_persisted": False, "data_sha256": old._sha(data_raw),
            "data_values_modified": False, "diagnostic_only": True,
            "encoding": "ORIGINAL_DATA_TABLE_CANONICAL_JSON_NO_ENVELOPE_MESSAGES",
            "fetched_at_utc": req["fetched_at_utc"], "fields": list(DAILY_FIELDS),
            "http_response_sha256": req["http_response_sha256"], "immutable": True,
            "params": params, "row_count": 0, "schema_version": "dc20_diagnostic_data_table_v1",
            "source": "tushare:daily"}, "OLD_DAILY_METADATA_CHANGED")
        old._fetched(req["fetched_at_utc"], days[0])
        daily[(days[0], code)] = tuple({"origin": "old_suspension_diagnostic", **p} for p in pairs)
        event = requests[event_id]
        require(old._parse(files[f"receipts/{event_id:02d}.json"]) == event, "OLD_EVENT_RECEIPT_MISMATCH")
        old._diagnostic_pair(files, event, days[0], code)  # All old identity/metadata checks.
        data = _complete(old._parse(files[event["source_files"][0]["path"]]), old.FIELDS)
        rows = old._table(data, days[0], code, event["params"])
        for day in days:
            row = rows.get(day)
            if row is not None and row["suspend_type"] == "S" and row["suspend_timing"] in (None, ""):
                events[(day, code)] = tuple({"origin": "old_suspension_diagnostic", **p} for p in event["source_files"])
        bindings.extend({"origin": "old_suspension_diagnostic", **p} for p in pairs + event["source_files"])
    return daily, events, tuple(bindings)


def _no_existing_row(root, day, code):
    path = root / "data/market/raw" / day[:4] / day / "daily.csv"
    if path.exists():
        raw = _read(path, 16_000_000)
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
            require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames))
                    and {"ts_code", "trade_date"} <= set(reader.fieldnames), "INVALID_EXISTING_DAILY_TABLE")
            seen = set()
            for row in reader:
                require(None not in row and all(v is not None for v in row.values())
                        and row["trade_date"] == day and row["ts_code"] not in seen,
                        "INVALID_EXISTING_DAILY_TABLE")
                seen.add(row["ts_code"])
                if row["ts_code"] == code:
                    return False  # Even zero/invalid OHLC is a row, NOT an empty response.
        except (UnicodeError, csv.Error):
            raise ValueError("INVALID_EXISTING_DAILY_TABLE") from None
    # Any registered or orphan minute pair contradicts/blocks absent-session proof.
    # We deliberately do not reinterpret all-zero/invalid bars as no trading.
    paths = (*minute_truth.paths(root, day, code), *legacy_minutes.minute_paths(root, day, code))
    return not any(p.exists() or p.is_symlink() for p in paths)


@dataclass(frozen=True, slots=True)
class _Context:
    candidate: object
    daily: object
    minutes: tuple
    root: Path
    expected_files: object
    archive_path: Path
    prior_path: Path
    code_bindings: tuple

    def assert_unchanged(self):
        _guard()
        self.candidate.assert_unchanged(); self.daily.assert_unchanged()
        for scope in self.minutes:
            scope.assert_unchanged()
        require(_sha(self.archive_path, old.MAX_BYTES) == old.DIAGNOSTIC_ZIP_SHA,
                "NONTRADING_DIAGNOSTIC_CHANGED")
        require(_sha(self.prior_path) == PRIOR_LABEL_SHA, "NONTRADING_PRIOR_LABEL_CHANGED")
        require(_inventory(self.root) == dict(self.expected_files), "NONTRADING_MARKET_INVENTORY_CHANGED")
        for path, digest in self.code_bindings:
            require(_sha(path) == digest, "NONTRADING_VERIFIER_CODE_CHANGED")


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False, init=False)
class VerifiedNoTradingSession:
    trade_date: str
    ts_code: str
    as_of_date: str
    source_policy_id: str
    source_files: tuple
    daily_collection_receipt_sha256: str
    registered_plan_sha256: str
    prior_label_report_sha256: str
    diagnostic_archive_sha256: str
    market_absence_verified: bool
    can_advance_holding_day: bool
    research_only: bool
    production_activation_allowed: bool
    actual_execution_claimed: bool
    actual_capacity_verified: bool
    settlement_allowed: bool
    synthetic_ohlc_created: bool
    provider_timestamp_semantics_confirmed: bool
    _context: _Context

    def __init__(self, day, code, bindings, context, *, _key=None):
        require(_key is _KEY and type(context) is _Context, "NONTRADING_PROOF_PRIVATE_CONSTRUCTION")
        values = dict(trade_date=day, ts_code=code, as_of_date=AS_OF_DATE, source_policy_id=SOURCE_POLICY_ID,
            source_files=_freeze(bindings), daily_collection_receipt_sha256=context.daily.receipt_sha256,
            registered_plan_sha256=context.daily.plan_sha256, prior_label_report_sha256=PRIOR_LABEL_SHA,
            diagnostic_archive_sha256=old.DIAGNOSTIC_ZIP_SHA, market_absence_verified=True,
            can_advance_holding_day=True, research_only=True, production_activation_allowed=False,
            actual_execution_claimed=False, provider_timestamp_semantics_confirmed=False, _context=context)
        values.update(actual_capacity_verified=False, settlement_allowed=False, synthetic_ohlc_created=False)
        for key, value in values.items():
            object.__setattr__(self, key, value)
        _ISSUED.add(self)

    def assert_unchanged(self):
        require(type(self) is VerifiedNoTradingSession and self in _ISSUED, "UNISSUED_NONTRADING_PROOF")
        self._context.assert_unchanged()

    def evidence(self):
        require(type(self) is VerifiedNoTradingSession and self in _ISSUED, "UNISSUED_NONTRADING_PROOF")
        return {name: _plain(getattr(self, name)) for name in self.__slots__
                if name not in {"_context", "__weakref__"}}


def verify_nontrading_sessions(*, verified_daily_scope, verified_candidate_scope, market_root,
                              diagnostic_archive_path, prior_label_report_path,
                              verified_minute_scopes=()):
    """Return only qualified fixed sessions; missing/conflicting evidence stays absent.

    The exact typed daily verifier must already have external run/commit and
    registered-plan/prior-label SHA checks. Ordinary dicts/bools never qualify.
    Zero or one independently verified minute overlay is supported, not chains.
    """
    from work.profit_1000_upgrade import daily_gap_verify as daily_verify
    _guard()
    require(type(verified_candidate_scope) is candidate_verify.VerifiedCandidateCollection,
            "EXACT_CANDIDATE_AUTHORITY_REQUIRED")
    require(type(verified_daily_scope) is daily_verify.VerifiedDailyGapCollection,
            "EXACT_DAILY_GAP_AUTHORITY_REQUIRED")
    require(type(verified_minute_scopes) is tuple and len(verified_minute_scopes) <= 1
            and all(type(v) is minute_verify.VerifiedMinuteCollection for v in verified_minute_scopes),
            "ZERO_OR_ONE_EXACT_MINUTE_AUTHORITY_REQUIRED")
    candidate, daily = verified_candidate_scope, verified_daily_scope
    require(candidate.as_of_date == daily.as_of_date == AS_OF_DATE
            and daily.prior_label_report_sha256 == PRIOR_LABEL_SHA
            and daily.diagnostic_archive_sha256 == old.DIAGNOSTIC_ZIP_SHA,
            "NONTRADING_AUTHORITY_IDENTITY_CONFLICT")
    candidate.assert_unchanged(); daily.assert_unchanged()
    root = _root(market_root)
    roots = [root, _root(candidate.root), _root(daily.root)]
    expected = _bindings(candidate.base_file_bindings)
    for scope in verified_minute_scopes:
        scope.assert_unchanged()
        roots.append(_root(scope.root))
        require(scope.as_of_date == AS_OF_DATE and scope.label_report_sha256 == PRIOR_LABEL_SHA,
                "MINUTE_AUTHORITY_WRONG_PRIOR_LABEL")
        extra = _bindings(scope.source_bindings)
        require(not set(expected).intersection(extra), "MINUTE_OVERLAY_CANNOT_OVERWRITE_BASE")
        loaded_bindings = {}
        for pair in scope.successful_pairs:
            payload = minute_truth.load(scope.root, *pair)
            require(payload is not None, "REGISTERED_MINUTE_SOURCE_MISSING")
            loaded_bindings.update(_bindings(payload["source_files"]))
        require(loaded_bindings == extra, "MINUTE_AUTHORITY_BINDING_UNION_CHANGED")
        expected.update(extra)
    require(all(a != b and a not in b.parents and b not in a.parents
                for i, a in enumerate(roots) for b in roots[i + 1:]), "SOURCE_ROOTS_ALIAS_OR_OVERLAP")
    require(_inventory(root) == expected, "MARKET_ROOT_NOT_EXACT_VERIFIED_UNION")
    archive_path, prior_path = Path(diagnostic_archive_path), Path(prior_label_report_path)
    old_daily, events, _ = _old_evidence(archive_path)
    prior_raw = _read(prior_path, 128 * 1024**2)
    require(old._sha(prior_raw) == PRIOR_LABEL_SHA, "REGISTERED_PRIOR_LABEL_SHA_CHANGED")
    prior = old._parse(prior_raw)
    require(prior.get("as_of_date") == AS_OF_DATE
            and prior.get("candidate_collection_receipt_sha256") == candidate.receipt_sha256
            and prior.get("candidate_manifest_sha256") == candidate.frozen_manifest_sha256
            and prior.get("base_archive_sha256") == candidate.base_archive_sha256,
            "PRIOR_LABEL_CANDIDATE_AUTHORITY_MISMATCH")
    rows = prior.get("rows")
    require(type(rows) is list and len(rows) == 6753, "FROZEN_LABEL_POPULATION_CHANGED")
    if verified_minute_scopes:
        missing_minutes = []
        for row in rows:
            if row.get("label_status") != "PENDING_EXIT_MISSING_MINUTES":
                continue
            require(row.get("missing_evidence_kind") == "research_exit_1000_1m_0931"
                    and row.get("missing_evidence_code") == row.get("ts_code"),
                    "PRIOR_MINUTE_GAP_IDENTITY_CHANGED")
            missing_minutes.append((row["missing_evidence_date"], row["ts_code"]))
        require(tuple(sorted(set(missing_minutes))) == verified_minute_scopes[0].gap_pairs,
                "MINUTE_SCOPE_NOT_EXACT_PRIOR_MISSING_SET")
    for signal, execution, code, days, _, _ in GROUPS:
        selected = [r for r in rows if (r.get("signal_date"), r.get("exec_date"), r.get("ts_code")) == (signal, execution, code)]
        require(len(selected) == 1 and selected[0].get("label_status") == "PENDING_EXIT_MISSING_DAILY"
                and selected[0].get("missing_evidence_kind") == "daily"
                and selected[0].get("missing_evidence_date") == days[0]
                and selected[0].get("missing_evidence_code") == code, "FROZEN_MISSING_DAILY_CASE_CHANGED")
    context = _Context(candidate, daily, verified_minute_scopes, root, _freeze(expected),
                       archive_path, prior_path, ((Path(daily_verify.__file__), _sha(Path(daily_verify.__file__))),))
    proven = []
    for day, code in ALLOWED_PAIRS:
        daily_bindings = old_daily.get((day, code))
        fresh = daily.records.get(("daily", day, code))
        if fresh is not None:
            table = _complete(fresh["table"], DAILY_FIELDS)
            if table["items"]:
                continue
            require(fresh["status"] == "DAILY_EMPTY_SOURCE_WRITTEN", "DAILY_EMPTY_STATUS_CONFLICT")
            daily_bindings = tuple({"origin": "daily_gap", **dict(b)} for b in fresh["source_files"])
        suspension_bindings = events.get((day, code))
        fresh = daily.records.get(("suspend_d", day, code))
        if fresh is not None:
            table = _complete(fresh["table"], old.FIELDS)
            selected = [dict(zip(table["fields"], r)) for r in table["items"]]
            if len(selected) != 1 or selected[0] != {"ts_code": code, "trade_date": day,
                    "suspend_type": "S", "suspend_timing": selected[0].get("suspend_timing")}:
                continue
            if selected[0]["suspend_timing"] not in (None, ""):
                continue
            require(fresh["status"] == "SUSPEND_EVENTS_SOURCE_WRITTEN", "SUSPEND_EVENT_STATUS_CONFLICT")
            suspension_bindings = tuple({"origin": "daily_gap", **dict(b)} for b in fresh["source_files"])
        if daily_bindings is not None and suspension_bindings is not None and _no_existing_row(root, day, code):
            proven.append(VerifiedNoTradingSession(day, code, daily_bindings + suspension_bindings, context, _key=_KEY))
    context.assert_unchanged()
    return tuple(proven)


def verify_nontrading_session(trade_date, code, **kwargs):
    pair = old._date(trade_date), old._code(code)
    require(pair in ALLOWED_PAIRS, "NONTRADING_SESSION_OUTSIDE_FIXED_SCOPE")
    return next((proof for proof in verify_nontrading_sessions(**kwargs)
                 if (proof.trade_date, proof.ts_code) == pair), None)


__all__ = ["VerifiedNoTradingSession", "verify_nontrading_session", "verify_nontrading_sessions", "SOURCE_POLICY_ID"]
