"""Read-only upstream acquisition into a NEW, repository-external input bundle.

No models, legacy DC20 outputs, latest data, production directories or ledgers
are read or written. HTTPS URLs contain an allowlisted repository and immutable
commit. Original bytes (including BOM) are preserved; missing cells are not zero.
Input acceptance is not inference, proof of point-in-time availability, or a
forward release. The metadata has row counts, not per-file cryptographic hashes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from .schedule import dates_for, read_calendar

CALENDAR = "data/market/trade_cal_sse.csv"
CALENDAR_SHA256 = "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
PRED_REPO = "njedu2023-prog/a-top10"
MARKET_REPO = "njedu2023-prog/a-share-top3-data"
REQUIRED_TABLES = ("daily", "daily_basic", "limit_list_d", "stk_limit", "stock_basic")
OPTIONAL_TABLES = ("limit_up_tags", "hot_boards", "intraday_features")
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_REQUESTS = 190
MAX_COLLECTION_SECONDS = 12 * 60
CODE_RE = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)")
COMMIT_RE = re.compile(r"[a-f0-9]{40}")
URL_RE = re.compile(r"https://raw\.githubusercontent\.com/njedu2023-prog/(a-top10|a-share-top3-data)/[a-f0-9]{40}/[A-Za-z0-9_./-]+")
FIELDS = {
    "candidate": {"trade_date", "verify_date", "ts_code", "name", "generated_at_utc"},
    "daily": {"trade_date", "ts_code", "open", "high", "low", "close", "pre_close", "vol", "amount"},
    "daily_basic": {"trade_date", "ts_code", "turnover_rate", "volume_ratio", "total_mv", "float_mv"},
    "limit_list_d": {"trade_date", "ts_code", "name", "limit_times"},
    "stk_limit": {"trade_date", "ts_code", "up_limit", "down_limit"},
    "stock_basic": {"ts_code", "name", "industry", "list_date"},
    "limit_up_tags": {"trade_date", "ts_code"},
    "hot_boards": {"trade_date"},
    "intraday_features": {"trade_date", "ts_code"},
}
NUMERIC_FIELDS = {
    "daily": {"open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg"},
    "daily_basic": {"turnover_rate", "turnover_rate_f", "volume_ratio", "total_mv", "float_mv"},
    "stk_limit": {"up_limit", "down_limit"},
    "limit_list_d": {"limit_times", "open_times", "close", "up_limit", "down_limit", "fd_amount", "amount"},
}


class InputError(ValueError):
    """Independent input evidence is unavailable or inconsistent."""


class FetchError(InputError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()


def _aware(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InputError("invalid aware timestamp") from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InputError("an aware collection timestamp is required")
    return value.astimezone(timezone.utc)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"20\d{6}", value):
        raise InputError("date must be YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise InputError("invalid calendar date") from exc
    return value


def _validate_url(url):
    if not isinstance(url, str) or not URL_RE.fullmatch(url) or any(p in {"", ".", ".."} for p in url.split("/")[3:]):
        raise InputError("only allowlisted immutable raw GitHub URLs are permitted")


def default_fetch(url):
    """No credentials, redirects, retry or unbounded response downloads."""
    _validate_url(url)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise FetchError("redirected source rejected", status=code)
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "DC20-independent-input-audit/1"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise FetchError("unexpected source response", status=response.status)
            body = response.read(MAX_FILE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"pinned source HTTP {exc.code}", status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FetchError("pinned source transport failed") from None
    if len(body) > MAX_FILE_BYTES:
        raise FetchError("source exceeds per-file size limit")
    return body


def _object(body):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise InputError("duplicate metadata key")
            out[key] = value
        return out
    try:
        obj = json.loads(body, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(InputError("nonfinite metadata")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("invalid source metadata JSON") from exc
    if not isinstance(obj, dict):
        raise InputError("metadata must be an object")
    return obj


def _csv_profile(body, table, date, *, exec_date=None, now=None):
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig"), newline=""))
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)) or not FIELDS[table].issubset(columns):
            raise InputError(f"{table}: incomplete or duplicate CSV columns")
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise InputError(f"{table}: invalid CSV") from exc
    if not rows and table not in {"candidate", "limit_list_d", *OPTIONAL_TABLES}:
        raise InputError(f"{table}: required table is empty")
    codes, missing, generated = set(), {}, set()
    for row in rows:
        if None in row or None in row.values():
            raise InputError(f"{table}: ragged CSV row")
        if "ts_code" in columns:
            code = row["ts_code"]
            if not CODE_RE.fullmatch(code) or code in codes:
                raise InputError(f"{table}: invalid or duplicate stock code")
            codes.add(code)
        if table != "stock_basic" and row.get("trade_date") != date:
            raise InputError(f"{table}: row not exact-date {date}")
        if table == "stock_basic" and "trade_date" in columns and row["trade_date"] != date:
            raise InputError("stock_basic: supplied row date disagrees with its snapshot")
        if table == "candidate":
            if row["verify_date"] != exec_date or not row["name"].strip():
                raise InputError("candidate: target T or name is invalid")
            timestamp = _aware(row["generated_at_utc"])
            close = datetime.strptime(date + "150000", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            if timestamp < close:
                raise InputError("candidate: generation precedes D close")
            if timestamp > now:
                raise InputError("candidate: generation postdates collection")
            generated.add(timestamp.isoformat())
        for field in NUMERIC_FIELDS.get(table, set()).intersection(columns):
            raw = row[field].strip()
            if raw.lower() in {"", "nan", "none", "null"}:
                missing[field] = missing.get(field, 0) + 1
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation as exc:
                raise InputError(f"{table}: nonnumeric {field}") from exc
            if not value.is_finite() or (field != "pct_chg" and value < 0):
                raise InputError(f"{table}: invalid {field}")
    if len(generated) > 1:
        raise InputError("candidate: mixed generation timestamps")
    return dict(row_count=len(rows), columns=columns, unique_stock_count=len(codes),
        date_scoped=table != "stock_basic", snapshot_date=date,
        missing_numeric_cells=missing, source_generated_at_utc=next(iter(generated), None))


def _meta_contract(body, date, now):
    meta = _object(body)
    if meta.get("resolved_trade_date") != date or meta.get("requested_trade_date") not in {None, date}:
        raise InputError("upstream metadata is not exact-date")
    try:
        generated = datetime.strptime(meta["generated_at_bj"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    except (ValueError, KeyError, TypeError) as exc:
        raise InputError("upstream metadata lacks a valid Beijing generation timestamp") from exc
    if generated > now:
        raise InputError("metadata generation postdates collection")
    close = datetime.strptime(date + "150000", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    if generated < close:
        raise InputError("metadata generation precedes snapshot D close")
    records = meta.get("jobs")
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise InputError("metadata job inventory missing")
    jobs = {r.get("key"): r for r in records}
    if len(jobs) != len(records):
        raise InputError("duplicate metadata job")
    expected_counts = {}
    for table in REQUIRED_TABLES:
        job = jobs.get(table)
        if not isinstance(job, dict) or job.get("status") not in {"ok", "ok_empty"} or job.get("error") not in {None, ""}:
            raise InputError(f"required upstream job unsuccessful: {table}")
        if type(job.get("rows")) is not int or job["rows"] < 0:
            raise InputError(f"invalid upstream count: {table}")
        if table != "stock_basic" and job.get("kwargs", {}).get("trade_date") != date:
            raise InputError(f"upstream job date mismatch: {table}")
        count = job["rows"]
        if table == "limit_list_d":
            policy = meta.get("derived", {}).get("limit_list_d_policy", {})
            if policy:
                if policy.get("trade_date") != date or policy.get("policy") != "CLOSE_EQ_UP_LIMIT" or type(policy.get("output_rows")) is not int or not 0 <= policy["output_rows"] <= count:
                    raise InputError("invalid derived close-limit row count")
                count = policy["output_rows"]
        if count == 0 and table != "limit_list_d":
            raise InputError(f"required upstream table reports no rows: {table}")
        expected_counts[table] = count
    tags = meta.get("derived", {}).get("hot_board_tags", {})
    intraday = meta.get("intraday_features", {})
    optional = {"limit_up_tags": tags.get("tagged"), "hot_boards": tags.get("hot_boards"),
        "intraday_features": intraday.get("rows") if intraday.get("ok") is True else None}
    optional = {key: value if type(value) is int and value >= 0 else None for key, value in optional.items()}
    return expected_counts, optional, generated.astimezone(timezone.utc).isoformat()


def _calendar(root):
    path = root
    for part in CALENDAR.split("/"):
        path /= part
        if path.is_symlink():
            raise InputError("calendar symlink rejected")
    dates = read_calendar(path, CALENDAR_SHA256)
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, check=True, text=True).stdout.strip()
        tree = subprocess.run(["git", "-C", str(root), "ls-tree", "HEAD", "--", CALENDAR], capture_output=True, check=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InputError("calendar requires an existing committed checkout") from exc
    body = path.read_bytes()
    blob = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
    if not COMMIT_RE.fullmatch(head) or tree != f"100644 blob {blob}\t{CALENDAR}":
        raise InputError("calendar does not match its committed regular blob")
    return dates, body, head, blob


def _output_path(root, output):
    path = Path(output)
    if not path.is_absolute() or path != path.resolve(strict=False) or not path.parent.is_dir():
        raise InputError("output must be a physical absolute path with an existing parent")
    if path.is_relative_to(root) or root.is_relative_to(path):
        raise InputError("input output must be outside the repository and its ancestors")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise InputError("output symlink rejected")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise InputError("output must be brand new or an empty directory")
    return path


def _write_bundle(root, output, bodies, manifest):
    output = _output_path(root, output)
    if not output.exists():
        output.mkdir(mode=0o700)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(output, flags)
    try:
        for relative, body in [*bodies.items(), ("manifest.json", _json_bytes(manifest))]:
            parts = relative.split("/")
            if any(part in {"", ".", ".."} or "\\" in part for part in parts):
                raise InputError("unsafe output member")
            current = os.dup(root_fd)
            try:
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                    child = os.open(part, flags, dir_fd=current)
                    os.close(current)
                    current = child
                descriptor = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=current)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(current)
    finally:
        os.close(root_fd)


def collect_inputs(root, output, signal_date, pred_commit, market_commit, *, fetch=None, now_utc=None):
    """Collect an immutable independent input audit; never invoke model/ledger.

    ``fetch`` accepts one pinned HTTPS URL and returns unmodified bytes.
    The result is written to ``output/manifest.json`` only after every required
    input validates. A failed attempt never publishes a success manifest.
    """
    root = Path(root).resolve(strict=True)
    output = _output_path(root, output)
    collector_sha = _sha(Path(__file__).read_bytes())
    date = _date(signal_date)
    if not all(isinstance(commit, str) and COMMIT_RE.fullmatch(commit) for commit in (pred_commit, market_commit)):
        raise InputError("upstream inputs require two immutable 40-hex commits")
    now = _aware(now_utc if now_utc is not None else datetime.now(timezone.utc))
    opened, calendar_bytes, head, calendar_blob = _calendar(root)
    d, t, t1 = dates_for(date, opened)
    position = opened.index(date)
    if position < 20:
        raise InputError("calendar does not cover preceding twenty sessions")
    if datetime.strptime(date + "150000", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai")) > now:
        raise InputError("target D has not closed at collection time")
    sessions = opened[position - 20:position + 1]
    get = default_fetch if fetch is None else fetch
    deadline = time.monotonic() + MAX_COLLECTION_SECONDS
    files, bodies, request_count, total_bytes = {}, {}, 0, 0

    def receive(repo, commit, remote, local, *, table, snapshot_date):
        nonlocal request_count, total_bytes
        url = f"https://raw.githubusercontent.com/{repo}/{commit}/{remote}"
        _validate_url(url)
        if time.monotonic() >= deadline:
            raise InputError("collection exceeded its twelve-minute deadline")
        request_count += 1
        if request_count > MAX_REQUESTS:
            raise InputError("request budget exceeded")
        try:
            body = get(url)
        except FetchError as exc:
            # Only allowlisted provenance and numeric HTTP status escape; a
            # supplied fetcher's exception text may include sensitive headers.
            status = exc.status if type(exc.status) is int and 100 <= exc.status <= 599 else None
            raise FetchError(f"pinned source unavailable: {repo}@{commit}/{remote} "
                             f"(HTTP {status if status is not None else 'unavailable'})",
                             status=status) from None
        if time.monotonic() >= deadline:
            raise InputError("collection exceeded its twelve-minute deadline")
        if not isinstance(body, bytes) or not body or len(body) > MAX_FILE_BYTES:
            raise InputError("fetch must return bounded nonempty original bytes")
        total_bytes += len(body)
        if total_bytes > MAX_TOTAL_BYTES:
            raise InputError("input bundle exceeds total size budget")
        bodies[local] = body
        files[local] = dict(sha256=_sha(body), bytes=len(body), source_url=url,
            source_repository=repo, source_commit=commit, source_path=remote,
            table=table, snapshot_date=snapshot_date)
        return body

    candidate_path = f"candidate/pred_decisio_{date}.csv"
    candidate = receive(PRED_REPO, pred_commit, f"outputs/decisio/pred_decisio_{date}.csv", candidate_path,
        table="candidate", snapshot_date=date)
    files[candidate_path].update(_csv_profile(candidate, "candidate", date, exec_date=t, now=now))
    market, optional_gaps = {}, []
    for session in sessions:
        remote_base, local_base = f"data/raw/{session[:4]}/{session}", f"market/{session[:4]}/{session}"
        meta_path = f"{local_base}/_meta.json"
        meta = receive(MARKET_REPO, market_commit, f"{remote_base}/_meta.json", meta_path,
            table="_meta", snapshot_date=session)
        try:
            expected, optional, generated = _meta_contract(meta, session, now)
        except InputError as exc:
            raise InputError(f"{session}/_meta.json: {exc}") from None
        files[meta_path]["source_generated_at_utc"] = generated
        market[session] = dict(metadata_path=meta_path, required_tables={}, optional_tables={})
        for table in REQUIRED_TABLES:
            local = f"{local_base}/{table}.csv"
            body = receive(MARKET_REPO, market_commit, f"{remote_base}/{table}.csv", local,
                table=table, snapshot_date=session)
            profile = _csv_profile(body, table, session)
            if profile["row_count"] != expected[table]:
                raise InputError(f"{session}/{table}: metadata row count mismatch")
            files[local].update(profile)
            market[session]["required_tables"][table] = local
        for table in OPTIONAL_TABLES:
            if optional[table] is None:
                market[session]["optional_tables"][table] = {"status": "NOT_DECLARED_AVAILABLE"}
                optional_gaps.append(dict(signal_date=session, table=table, reason="NOT_DECLARED_AVAILABLE"))
                continue
            local = f"{local_base}/{table}.csv"
            try:
                body = receive(MARKET_REPO, market_commit, f"{remote_base}/{table}.csv", local,
                    table=table, snapshot_date=session)
            except FetchError as exc:
                market[session]["optional_tables"][table] = {"status": "DECLARED_BUT_UNAVAILABLE", "http_status": exc.status}
                optional_gaps.append(dict(signal_date=session, table=table, reason="DECLARED_BUT_UNAVAILABLE"))
                continue
            profile = _csv_profile(body, table, session)
            if profile["row_count"] != optional[table]:
                raise InputError(f"{session}/{table}: optional metadata count mismatch")
            files[local].update(profile)
            market[session]["optional_tables"][table] = dict(status="PRESENT", path=local)
    calendar_path = "calendar/trade_cal_sse.csv"
    bodies[calendar_path] = calendar_bytes
    files[calendar_path] = dict(sha256=_sha(calendar_bytes), bytes=len(calendar_bytes), table="calendar",
        source_repository="njedu2023-prog/DC20", source_commit=head, source_path=CALENDAR, git_blob_sha1=calendar_blob)
    manifest = dict(schema_version="dc20_forward_input_bundle_v1", manifest_path="manifest.json",
        status="REQUIRED_INPUTS_VERIFIED", signal_date=d, exec_date=t, exit_date=t1,
        collected_at_utc=now.isoformat(), source_repository="njedu2023-prog/DC20", source_commit=head,
        pred_commit=pred_commit, market_commit=market_commit, candidate_path=candidate_path,
        calendar_path=calendar_path, history_sessions=sessions[:-1], market_sessions=sessions,
        required_tables=list(REQUIRED_TABLES), optional_tables=list(OPTIONAL_TABLES),
        market=market, files=files, request_count=request_count, downloaded_bytes=total_bytes,
        optional_gaps=optional_gaps, optional_tables_complete=not optional_gaps,
        scope="independent_read_only_input_audit_not_full_P0_feature_equivalence",
        hash_basis="observed_original_bytes_at_immutable_HTTPS_commit_URL; upstream_metadata_has_no_file_checksums",
        source_generation_is_not_collection_time=True, historical_point_in_time_acquisition_claimed=False,
        model_inference_performed=False, training_performed=False, ledger_written=False,
        production_enabled=False, forward_ledger_eligible=False,
        collector_sha256=collector_sha)
    manifest["bundle_sha256"] = _sha(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode())
    if time.monotonic() >= deadline:
        raise InputError("collection exceeded its twelve-minute deadline")
    if _sha(Path(__file__).read_bytes()) != collector_sha:
        raise InputError("collector source changed during input acquisition")
    _write_bundle(root, output, bodies, manifest)
    return manifest
