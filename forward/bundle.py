"""Offline, fail-closed access to a separately pinned immutable input bundle.

The caller must supply the manifest's SHA256 from an independent trusted
receipt. A self hash or a status flag is not that trust anchor. Validation reads
the committed SSE calendar and this bundle only; it never queries the network,
old DC20 market/prediction files, rankings, statistics, or any ledger. All input
bytes are frozen in memory after verification, so consumers do not reopen an
untrusted path. Historical acquisition remains REPLAY, not natural-run identity.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import inputs
from .schedule import dates_for

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_FILES = 192  # 190 remote responses + calendar + manifest.


class BundleError(inputs.InputError):
    """An independently pinned bundle failed offline integrity/contract checks."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _same(actual, expected, label):
    # JSON comparison also distinguishes bools from numeric 0/1.
    if _canonical(actual) != _canonical(expected):
        raise BundleError(f"{label}: contract mismatch")


def _sha256(value, label):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise BundleError(f"{label}: external lowercase SHA256 required")
    return value


def _regular_bytes(root_fd, relative, limit):
    parts = relative.split("/")
    if not parts or any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise BundleError("unsafe bundle member")
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
                raise BundleError(f"{relative}: bounded, single-link regular file required")
            body = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
            if len(body) != before.st_size or len(body) > limit or (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                    before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                                            after.st_mtime_ns, after.st_ctime_ns):
                raise BundleError(f"{relative}: file changed during verification")
            return body
    finally:
        os.close(current)


def _open_physical_directory(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve(strict=True):
        raise BundleError("bundle must be an existing physical absolute directory")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _inventory(root_fd, expected):
    """Inspect names/types only, never open undeclared file contents."""
    directories = {"/".join(path.split("/")[:i])
                   for path in expected for i in range(1, len(path.split("/")))}
    found = set()

    def walk(descriptor, prefix=""):
        with os.scandir(descriptor) as entries:
            for entry in entries:
                relative = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if relative not in directories:
                        raise BundleError(f"undeclared bundle directory: {relative}")
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    try:
                        walk(child, relative + "/")
                    finally:
                        os.close(child)
                else:
                    if relative not in expected or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise BundleError(f"undeclared or unsafe bundle file: {relative}")
                    found.add(relative)
                    if len(found) > MAX_BUNDLE_FILES:
                        raise BundleError("bundle file budget exceeded")
    walk(root_fd)
    _same(sorted(found), sorted(expected), "on-disk bundle inventory")


class VerifiedInputBundle:
    """Validate once and serve immutable bytes; never infer or admit a ledger.

    ``root`` supplies only the already committed, hash-pinned SSE calendar.
    ``source_commit`` is the collector's recorded DC20 revision; it may precede
    the consuming inference revision. The consumer must record its own HEAD.
    Optional absence is the pinned collector's report, not a new network check.
    """

    def __init__(self, root, bundle_dir, expected_manifest_sha256):
        expected_hash = _sha256(expected_manifest_sha256, "manifest")
        self._files = {}
        self._metadata = {}
        self._manifest = {}
        self._manifest_bytes = b""
        descriptor = None
        try:
            root = Path(root).resolve(strict=True)
            bundle_dir = Path(bundle_dir)
            if bundle_dir.is_relative_to(root) or root.is_relative_to(bundle_dir):
                raise BundleError("independent bundle must be outside the repository and its ancestors")
            descriptor = _open_physical_directory(bundle_dir)
            manifest_bytes = _regular_bytes(descriptor, "manifest.json", MAX_MANIFEST_BYTES)
            if inputs._sha(manifest_bytes) != expected_hash:
                raise BundleError("manifest SHA256 disagrees with external receipt")
            manifest = inputs._object(manifest_bytes)
            self_hash = _sha256(manifest.get("bundle_sha256"), "bundle self hash")
            unhashed = {key: value for key, value in manifest.items() if key != "bundle_sha256"}
            if inputs._sha(_canonical(unhashed)) != self_hash:
                raise BundleError("bundle self hash mismatch")
            self._validate(root, descriptor, manifest)
            self._manifest = copy.deepcopy(manifest)
            self._manifest_bytes = manifest_bytes
            self._manifest_sha256 = expected_hash
        except BundleError:
            raise
        except (inputs.InputError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            raise BundleError(f"input bundle rejected: {exc}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _validate(self, root, descriptor, manifest):
        opened, calendar_bytes, _, calendar_blob = inputs._calendar(root)
        date = inputs._date(manifest.get("signal_date"))
        d, t, t1 = dates_for(date, opened)
        position = opened.index(d)
        if position < 20:
            raise BundleError("calendar lacks twenty preceding sessions")
        sessions = opened[position - 20:position + 1]
        now = inputs._aware(manifest.get("collected_at_utc"))
        close = datetime.strptime(d + "150000", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        if now < close:
            raise BundleError("input collection predates target D close")
        for key in ("pred_commit", "market_commit", "source_commit"):
            if not isinstance(manifest.get(key), str) or not inputs.COMMIT_RE.fullmatch(manifest[key]):
                raise BundleError(f"{key}: immutable commit required")
        _sha256(manifest.get("collector_sha256"), "collector provenance")
        _same(manifest.get("exec_date"), t, "strict T")
        _same(manifest.get("exit_date"), t1, "strict T+1")
        _same(manifest.get("history_sessions"), sessions[:-1], "preceding sessions")
        _same(manifest.get("market_sessions"), sessions, "market sessions")
        market = manifest.get("market")
        if not isinstance(market, dict):
            raise BundleError("market inventory missing")
        _same(sorted(market), sessions, "market inventory dates")

        # Derive the complete allowed file set before reading any member bytes.
        candidate_path = f"candidate/pred_decisio_{d}.csv"
        calendar_path = "calendar/trade_cal_sse.csv"
        expected_paths = {candidate_path, calendar_path}
        for session in sessions:
            record = market[session]
            _same(sorted(record), ["metadata_path", "optional_tables", "required_tables"], "market record keys")
            base = f"market/{session[:4]}/{session}"
            _same(record["metadata_path"], f"{base}/_meta.json", "metadata path")
            expected_paths.add(f"{base}/_meta.json")
            _same(record["required_tables"], {table: f"{base}/{table}.csv" for table in inputs.REQUIRED_TABLES}, "required paths")
            expected_paths.update(record["required_tables"].values())
            optional = record["optional_tables"]
            _same(sorted(optional), sorted(inputs.OPTIONAL_TABLES), "optional table keys")
            for table in inputs.OPTIONAL_TABLES:
                entry = optional[table]
                status = entry.get("status")
                if status == "PRESENT":
                    _same(entry, {"status": status, "path": f"{base}/{table}.csv"}, "optional present path")
                    expected_paths.add(f"{base}/{table}.csv")
                elif status == "NOT_DECLARED_AVAILABLE":
                    _same(entry, {"status": status}, "optional absence")
                elif status == "DECLARED_BUT_UNAVAILABLE":
                    http = entry.get("http_status")
                    if http is not None and (type(http) is not int or not 100 <= http <= 599):
                        raise BundleError("invalid optional HTTP status")
                    _same(entry, {"status": status, "http_status": http}, "optional unavailability")
                else:
                    raise BundleError("unknown optional availability status")
        records = manifest.get("files")
        if not isinstance(records, dict):
            raise BundleError("file records missing")
        _same(sorted(records), sorted(expected_paths), "declared file set")
        _inventory(descriptor, expected_paths | {"manifest.json"})
        total = 0
        for relative in sorted(expected_paths):
            record = records[relative]
            if not isinstance(record, dict) or type(record.get("bytes")) is not int:
                raise BundleError(f"{relative}: invalid file size contract")
            _sha256(record.get("sha256"), relative)
            body = _regular_bytes(descriptor, relative, inputs.MAX_FILE_BYTES)
            if len(body) != record["bytes"] or inputs._sha(body) != record["sha256"]:
                raise BundleError(f"{relative}: original bytes/hash mismatch")
            self._files[relative] = body
            total += len(body)
            if total > inputs.MAX_TOTAL_BYTES + len(calendar_bytes):
                raise BundleError("bundle byte budget exceeded")

        def source(relative, repo, commit, remote, table, session):
            body = self._files[relative]
            url = f"https://raw.githubusercontent.com/{repo}/{commit}/{remote}"
            inputs._validate_url(url)
            return dict(sha256=inputs._sha(body), bytes=len(body), source_url=url,
                        source_repository=repo, source_commit=commit, source_path=remote,
                        table=table, snapshot_date=session)

        candidate = source(candidate_path, inputs.PRED_REPO, manifest["pred_commit"],
                           f"outputs/decisio/pred_decisio_{d}.csv", "candidate", d)
        candidate.update(inputs._csv_profile(self._files[candidate_path], "candidate", d, exec_date=t, now=now))
        expected_records = {candidate_path: candidate}
        expected_market, optional_gaps = {}, []
        request_count = 127  # candidate + 21 * (metadata + five required tables)
        for session in sessions:
            base, remote_base = f"market/{session[:4]}/{session}", f"data/raw/{session[:4]}/{session}"
            meta_path = f"{base}/_meta.json"
            expected_counts, optional_counts, generated = inputs._meta_contract(self._files[meta_path], session, now)
            self._metadata[session] = inputs._object(self._files[meta_path])
            meta_record = source(meta_path, inputs.MARKET_REPO, manifest["market_commit"], f"{remote_base}/_meta.json", "_meta", session)
            meta_record["source_generated_at_utc"] = generated
            expected_records[meta_path] = meta_record
            expected_market[session] = dict(metadata_path=meta_path, required_tables={}, optional_tables={})
            for table in inputs.REQUIRED_TABLES + inputs.OPTIONAL_TABLES:
                required = table in inputs.REQUIRED_TABLES
                if not required:
                    entry = market[session]["optional_tables"][table]
                    expected_count = optional_counts[table]
                    if expected_count is None:
                        _same(entry, {"status": "NOT_DECLARED_AVAILABLE"}, f"{session}/{table} not declared")
                        optional_gaps.append(dict(signal_date=session, table=table, reason="NOT_DECLARED_AVAILABLE"))
                        expected_market[session]["optional_tables"][table] = entry
                        continue
                    request_count += 1
                    if entry["status"] == "DECLARED_BUT_UNAVAILABLE":
                        optional_gaps.append(dict(signal_date=session, table=table, reason="DECLARED_BUT_UNAVAILABLE"))
                        expected_market[session]["optional_tables"][table] = entry
                        continue
                    if entry["status"] != "PRESENT":
                        raise BundleError(f"{session}/{table}: metadata declares available input")
                else:
                    expected_count = expected_counts[table]
                path = f"{base}/{table}.csv"
                result = source(path, inputs.MARKET_REPO, manifest["market_commit"], f"{remote_base}/{table}.csv", table, session)
                profile = inputs._csv_profile(self._files[path], table, session)
                if profile["row_count"] != expected_count:
                    raise BundleError(f"{session}/{table}: metadata row count mismatch")
                result.update(profile)
                expected_records[path] = result
                if required:
                    expected_market[session]["required_tables"][table] = path
                else:
                    expected_market[session]["optional_tables"][table] = dict(status="PRESENT", path=path)
        if self._files[calendar_path] != calendar_bytes:
            raise BundleError("bundled calendar differs from committed pinned SSE calendar")
        expected_records[calendar_path] = dict(sha256=inputs._sha(calendar_bytes), bytes=len(calendar_bytes),
            table="calendar", source_repository="njedu2023-prog/DC20", source_commit=manifest["source_commit"],
            source_path=inputs.CALENDAR, git_blob_sha1=calendar_blob)
        expected = dict(schema_version="dc20_forward_input_bundle_v1", manifest_path="manifest.json",
            status="REQUIRED_INPUTS_VERIFIED", signal_date=d, exec_date=t, exit_date=t1,
            collected_at_utc=now.isoformat(), source_repository="njedu2023-prog/DC20", source_commit=manifest["source_commit"],
            pred_commit=manifest["pred_commit"], market_commit=manifest["market_commit"],
            candidate_path=candidate_path, calendar_path=calendar_path,
            history_sessions=sessions[:-1], market_sessions=sessions,
            required_tables=list(inputs.REQUIRED_TABLES), optional_tables=list(inputs.OPTIONAL_TABLES),
            market=expected_market, files=expected_records, request_count=request_count,
            downloaded_bytes=total - len(calendar_bytes), optional_gaps=optional_gaps,
            optional_tables_complete=not optional_gaps,
            scope="independent_read_only_input_audit_not_full_P0_feature_equivalence",
            hash_basis="observed_original_bytes_at_immutable_HTTPS_commit_URL; upstream_metadata_has_no_file_checksums",
            source_generation_is_not_collection_time=True, historical_point_in_time_acquisition_claimed=False,
            model_inference_performed=False, training_performed=False, ledger_written=False,
            production_enabled=False, forward_ledger_eligible=False, collector_sha256=manifest["collector_sha256"])
        expected["bundle_sha256"] = inputs._sha(_canonical(expected))
        _same(manifest, expected, "reconstructed input manifest")

    @property
    def manifest(self):
        return copy.deepcopy(self._manifest)

    @property
    def manifest_sha256(self):
        return self._manifest_sha256

    @property
    def manifest_bytes(self):
        return self._manifest_bytes

    @property
    def bundle_sha256(self):
        return self._manifest["bundle_sha256"]

    @property
    def source_commit(self):
        return self._manifest["source_commit"]

    @property
    def signal_date(self):
        return self._manifest["signal_date"]

    @property
    def exec_date(self):
        return self._manifest["exec_date"]

    @property
    def exit_date(self):
        return self._manifest["exit_date"]

    def get_bytes(self, relative):
        if not isinstance(relative, str) or relative not in self._files:
            raise BundleError("only declared and validated input bytes may be consumed")
        return self._files[relative]

    def file_record(self, relative):
        self.get_bytes(relative)
        return copy.deepcopy(self._manifest["files"][relative])

    def read_candidate_bytes(self):
        return self.get_bytes(self._manifest["candidate_path"])

    def read_calendar_bytes(self):
        return self.get_bytes(self._manifest["calendar_path"])

    def read_market_bytes(self, date, table):
        if date not in self._manifest["market"] or table not in inputs.REQUIRED_TABLES + inputs.OPTIONAL_TABLES:
            raise BundleError("market read is outside the verified session/table scope")
        market = self._manifest["market"][date]
        if table in inputs.REQUIRED_TABLES:
            return self.get_bytes(market["required_tables"][table])
        optional = market["optional_tables"][table]
        return self.get_bytes(optional["path"]) if optional["status"] == "PRESENT" else None

    def read_market_metadata(self, date):
        if date not in self._metadata:
            raise BundleError("metadata read is outside the verified session scope")
        return copy.deepcopy(self._metadata[date])
