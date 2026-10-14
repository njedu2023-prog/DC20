"""Pure validation of an original controlled P0 generation candidate archive.

This module has no network or filesystem operations. Callers must validate the
complete API run/job evidence and Git tree objects independently. This checks
the archive's original bytes and applies every exact Git patch in both
directions for binary records; it never grants publication or freeze authority.
"""
from __future__ import annotations

import base64
import csv
from datetime import datetime, time
import hashlib
import io
import json
import re
import stat
import zipfile
import zlib
from zoneinfo import ZoneInfo

SCHEMA = "dc20_original_p0_generation_artifact_binding_v1"
REPOSITORY = "njedu2023-prog/DC20"
WORKFLOW_PATH = ".github/workflows/run_primary_d_daily.yml"
MEMBERS = frozenset(("base_sha.txt", "primary-d.patch", "primary-d.patch.sha256",
                     "primary-d-target-envelope.json"))
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_BLOB_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BLOB_BYTES = 128 * 1024 * 1024
MAX_PATCH_FILES = 64
ZERO = "0" * 40
TABLES = frozenset(("daily", "daily_basic", "hot_boards", "intraday_features",
    "limit_list_d", "limit_up_tags", "moneyflow_hsgt", "namechange", "stk_auction",
    "stk_limit", "stock_basic", "top_list"))


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def _exact_sha(value, width=40):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % width, value),
             "GENERATION_EXACT_SHA_REQUIRED")
    return value


def _json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            _require(key not in out, "GENERATION_DUPLICATE_JSON_KEY")
            out[key] = value
        return out
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: _require(False, "GENERATION_NONFINITE_JSON"))


def _utc(value):
    _require(type(value) is str, "GENERATION_AWARE_TIMESTAMP_REQUIRED")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(stamp.tzinfo is not None and stamp.utcoffset() is not None,
             "GENERATION_AWARE_TIMESTAMP_REQUIRED")
    return stamp


def _lf_lines(raw):
    parts = raw.split(b"\n")
    return [x + b"\n" for x in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _inflate(section):
    """Decode exactly one bounded Git binary literal/delta section."""
    lines = section.split(b"\n")
    match = re.fullmatch(rb"(literal|delta) ([0-9]+)", lines[0])
    _require(match is not None and len(lines) >= 2, "GENERATION_BINARY_HEADER")
    kind, declared = match[1].decode(), int(match[2])
    _require(0 <= declared <= MAX_BLOB_BYTES, "GENERATION_BINARY_SIZE_LIMIT")
    pieces = []
    for index, line in enumerate(lines[1:]):
        _require(len(line) > 1, "GENERATION_BINARY_LINE")
        prefix = line[0]
        count = prefix - 64 if 65 <= prefix <= 90 else prefix - 70 if 97 <= prefix <= 122 else 0
        _require(1 <= count <= 52 and len(line) == 1 + ((count + 3) // 4) * 5,
                 "GENERATION_BINARY_LINE_SIZE")
        _require(index == len(lines) - 2 or count == 52, "GENERATION_NONCANONICAL_BINARY_LINE")
        try:
            decoded = base64.b85decode(line[1:])
        except (ValueError, OverflowError):
            raise ValueError("GENERATION_BINARY_BASE85") from None
        _require(base64.b85encode(decoded) == line[1:]
                 and decoded[count:] == b"\0" * (len(decoded) - count),
                 "GENERATION_BINARY_PADDING")
        pieces.append(decoded[:count])
    compressed = b"".join(pieces)
    _require(len(compressed) <= MAX_MEMBER_BYTES, "GENERATION_BINARY_INPUT_LIMIT")
    try:
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(compressed, declared + 1)
    except zlib.error:
        raise ValueError("GENERATION_BINARY_ZLIB") from None
    _require(len(decoded) == declared and inflater.eof
             and not inflater.unused_data and not inflater.unconsumed_tail,
             "GENERATION_BINARY_INFLATE_BOUNDARY")
    return kind, decoded


def _delta(program, source):
    """Interpret Git's bounded copy/insert delta program without subprocesses."""
    cursor = 0

    def varint():
        nonlocal cursor
        value, shift, encoded = 0, 0, []
        while True:
            _require(cursor < len(program) and shift <= 28, "GENERATION_DELTA_VARINT")
            byte = program[cursor]
            cursor += 1
            encoded.append(byte)
            value |= (byte & 127) << shift
            if not byte & 128:
                _require(len(encoded) == 1 or byte != 0, "GENERATION_NONCANONICAL_DELTA_VARINT")
                _require(value <= MAX_BLOB_BYTES, "GENERATION_DELTA_SIZE_LIMIT")
                return value
            shift += 7

    _require(varint() == len(source), "GENERATION_DELTA_SOURCE_SIZE")
    target_size = varint()
    result = bytearray()
    while cursor < len(program):
        operation = program[cursor]
        cursor += 1
        _require(operation != 0, "GENERATION_DELTA_ZERO_OPCODE")
        if operation & 128:
            offset, size = 0, 0
            for bit in range(4):
                if operation & (1 << bit):
                    _require(cursor < len(program), "GENERATION_DELTA_TRUNCATED_COPY")
                    offset |= program[cursor] << (8 * bit)
                    cursor += 1
            for bit in range(3):
                if operation & (1 << (bit + 4)):
                    _require(cursor < len(program), "GENERATION_DELTA_TRUNCATED_COPY")
                    size |= program[cursor] << (8 * bit)
                    cursor += 1
            size = size or 0x10000
            _require(offset <= len(source) and size <= len(source) - offset,
                     "GENERATION_DELTA_COPY_OUT_OF_RANGE")
            piece = source[offset:offset + size]
        else:
            size = operation
            _require(size <= len(program) - cursor, "GENERATION_DELTA_TRUNCATED_INSERT")
            piece = program[cursor:cursor + size]
            cursor += size
        _require(len(result) + len(piece) <= target_size, "GENERATION_DELTA_TARGET_OVERFLOW")
        result.extend(piece)
    _require(len(result) == target_size, "GENERATION_DELTA_TARGET_SIZE")
    return bytes(result)


def _binary(body, old):
    match = re.fullmatch(rb"GIT binary patch\n(.+?)\n\n(.+?)\n\n", body, re.S)
    _require(match is not None, "GENERATION_EXACT_TWO_BINARY_SECTIONS")
    forward_kind, forward = _inflate(match[1])
    reverse_kind, reverse = _inflate(match[2])
    new = forward if forward_kind == "literal" else _delta(forward, old)
    restored = reverse if reverse_kind == "literal" else _delta(reverse, new)
    _require(restored == old, "GENERATION_BINARY_REVERSE_MISMATCH")
    return new, forward_kind


def _text(body, old, path, is_new):
    old_path = "/dev/null" if is_new else "a/" + path
    header = ("--- " + old_path + "\n+++ b/" + path + "\n").encode()
    _require(body.startswith(header), "GENERATION_EXACT_TEXT_PATHS")
    lines, source = _lf_lines(body[len(header):]), _lf_lines(old)
    _require(lines, "GENERATION_TEXT_HUNK_REQUIRED")
    cursor, old_cursor, output, hunks = 0, 0, [], 0
    while cursor < len(lines):
        match = re.fullmatch(rb"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@[^\n]*\n", lines[cursor])
        _require(match is not None, "GENERATION_TEXT_HUNK_HEADER")
        start_old, count_old, start_new, count_new = (
            int(match[1]), int(match[2]) if match[2] is not None else 1,
            int(match[3]), int(match[4]) if match[4] is not None else 1)
        pos_old = start_old if count_old == 0 else start_old - 1
        pos_new = start_new if count_new == 0 else start_new - 1
        _require(0 <= old_cursor <= pos_old <= len(source), "GENERATION_TEXT_HUNK_OVERLAP")
        output.extend(source[old_cursor:pos_old])
        _require(pos_new == len(output), "GENERATION_TEXT_NEW_OFFSET")
        old_cursor, consumed, produced = pos_old, 0, 0
        cursor += 1
        while cursor < len(lines) and not lines[cursor].startswith(b"@@ "):
            line = lines[cursor]
            _require(line[:1] in (b" ", b"+", b"-") and line.endswith(b"\n"),
                     "GENERATION_TEXT_HUNK_LINE")
            marker, content = line[:1], line[1:]
            cursor += 1
            if cursor < len(lines) and lines[cursor] == b"\\ No newline at end of file\n":
                content = content[:-1]
                cursor += 1
            if marker in (b" ", b"-"):
                _require(old_cursor < len(source) and source[old_cursor] == content,
                         "GENERATION_TEXT_BASE_MISMATCH")
                old_cursor += 1
                consumed += 1
            if marker in (b" ", b"+"):
                output.append(content)
                produced += 1
            _require(consumed <= count_old and produced <= count_new, "GENERATION_TEXT_HUNK_COUNT")
        _require(consumed == count_old and produced == count_new, "GENERATION_TEXT_HUNK_COUNT")
        hunks += 1
    output.extend(source[old_cursor:])
    _require(all(line.endswith(b"\n") for line in output[:-1]),
             "GENERATION_TEXT_NO_NEWLINE_ONLY_AT_EOF")
    new = b"".join(output)
    _require(hunks > 0 and len(new) <= MAX_BLOB_BYTES, "GENERATION_TEXT_OUTPUT_LIMIT")
    return new, "text"


def _allowed(day, receipt):
    year = day[:4]
    exact = {"data/pred/_pred_source_meta.json", "data/pred/pred_source_latest.csv",
             f"data/pred/archive/pred_source_{day}.csv", "data/market/trade_cal_sse.csv",
             f"data/market/raw/{year}/{day}/_sync_meta.json",
             "outputs/decision/three_rank_index.json", "outputs/decision/primary_d_runtime_index.json"}
    four = {f"outputs/decision/{stem}_{day}.{ext}" for stem, ext in (
        ("primary_d_receipt", "json"), ("primary_d_runtime_features", "csv"),
        ("three_rank_top10", "json"), ("three_rank_top10", "csv"))}
    tables = receipt["inputs"]["market"]["tables"]
    _require(type(tables) is dict and set(tables) <= TABLES, "GENERATION_MARKET_TABLE_SCOPE")
    for table, binding in tables.items():
        path = f"data/market/raw/{year}/{day}/{table}.csv"
        _require(type(binding) is dict and binding.get("path") == path,
                 "GENERATION_MARKET_TABLE_PATH")
        exact.add(path)
    return exact | four, four


def _archive(archive, artifact, run):
    _require(type(archive) is bytes and 0 < len(archive) <= MAX_ARCHIVE_BYTES,
             "GENERATION_ARCHIVE_SIZE_LIMIT")
    _require(type(artifact) is dict and type(artifact.get("id")) is int and artifact["id"] > 0
             and artifact.get("name") == f"primary-d-candidate-{run['id']}-1"
             and artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int
             and artifact["size_in_bytes"] == len(archive)
             and artifact.get("digest") == "sha256:" + _sha(archive),
             "GENERATION_ARTIFACT_IDENTITY_OR_DIGEST")
    origin = artifact.get("workflow_run")
    _require(type(origin) is dict and type(origin.get("id")) is int and origin["id"] == run["id"]
             and origin.get("head_branch") == "main" and origin.get("head_sha") == run["head_sha"],
             "GENERATION_ARTIFACT_ORIGIN")
    _require(_utc(run["run_started_at"]) <= _utc(artifact["created_at"])
             <= _utc(artifact["updated_at"]) <= _utc(run["updated_at"]),
             "GENERATION_ARTIFACT_TIME_ORDER")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            infos = z.infolist()
            _require(len(infos) == 4 and {entry.filename for entry in infos} == MEMBERS,
                     "GENERATION_EXACT_FOUR_ARCHIVE_MEMBERS")
            result = {}
            for entry in infos:
                mode = entry.external_attr >> 16
                _require(not entry.is_dir() and not entry.flag_bits & 1
                         and stat.S_IFMT(mode) in (0, stat.S_IFREG)
                         and not stat.S_ISLNK(mode) and 0 < entry.file_size <= MAX_MEMBER_BYTES
                         and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                         "GENERATION_UNSAFE_ARCHIVE_MEMBER")
                with z.open(entry) as stream:
                    value = stream.read(MAX_MEMBER_BYTES + 1)
                _require(len(value) == entry.file_size, "GENERATION_MEMBER_SIZE")
                result[entry.filename] = value
            _require(sum(map(len, result.values())) <= MAX_MEMBER_BYTES,
                     "GENERATION_ARCHIVE_EXPANSION_LIMIT")
            return result
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError):
        raise ValueError("GENERATION_INVALID_ARCHIVE") from None


def _source_times(receipt, target, loaded, cas_tree, blob_loader, created):
    """Original producer times, explicitly excluding later local sync times."""
    day = target["signal_date"]
    candidate, market = receipt["inputs"]["candidate"], receipt["inputs"]["market"]
    expected = {candidate["meta_path"]: candidate["meta_sha256"],
                candidate["path"]: candidate["sha256"], market["meta_path"]: market["meta_sha256"]}
    _require(candidate["path"] == f"data/pred/archive/pred_source_{day}.csv"
             and candidate["meta_path"] == "data/pred/_pred_source_meta.json"
             and market["meta_path"] == f"data/market/raw/{day[:4]}/{day}/_sync_meta.json",
             "GENERATION_EXACT_SOURCE_METADATA_PATHS")
    bodies = {}
    for path, expected_sha in expected.items():
        _exact_sha(expected_sha, 64)
        entry = cas_tree.get(path, {})
        _require(entry.get("type") == "blob" and entry.get("mode") == "100644",
                 "GENERATION_SOURCE_METADATA_BLOB")
        raw = loaded[path] if path in loaded else blob_loader(cas_tree, path)
        _require(type(raw) is bytes and len(raw) <= MAX_BLOB_BYTES and _sha(raw) == expected_sha
                 and _blob(raw) == entry.get("sha"), "GENERATION_SOURCE_METADATA_BYTES")
        bodies[path] = raw
    pred_meta, market_meta = (_json(bodies[p]) for p in (candidate["meta_path"], market["meta_path"]))
    _require(pred_meta.get("resolved_commit") == target["pred_commit"]
             and pred_meta.get("source_repository") == "njedu2023-prog/a-top10"
             and pred_meta.get("resolved_trade_date") == day
             and pred_meta.get("source_ref") == f"https://raw.githubusercontent.com/njedu2023-prog/a-top10/{target['pred_commit']}/outputs/decisio/pred_decisio_{day}.csv"
             and pred_meta.get("body_sha256") == candidate["sha256"]
             and pred_meta.get("sha256") == candidate["sha256"], "GENERATION_PRED_SOURCE_META")
    reader = csv.DictReader(io.StringIO(bodies[candidate["path"]].decode("utf-8-sig"), newline=""))
    fields = reader.fieldnames
    _require(type(fields) is list and len(fields) == len(set(fields))
             and {"trade_date", "verify_date", "generated_at_utc"} <= set(fields), "GENERATION_PRED_COLUMNS")
    rows = list(reader)
    _require(type(candidate.get("row_count")) is int and len(rows) == candidate["row_count"] > 0
             and all(None not in row and all(value is not None for value in row.values())
                     and row["trade_date"] == day and row["verify_date"] == receipt["exec_date"] for row in rows),
             "GENERATION_EXACT_D_PRED_ROWS")
    times = sorted({row["generated_at_utc"] for row in rows})
    _require(len(times) == 1, "GENERATION_SINGLE_PRED_PRODUCTION_TIME")
    pred_produced = _utc(times[0])
    source_repo = market_meta.get("source_repo", {})
    upstream = market_meta.get("upstream_meta", {})
    _require(source_repo.get("owner") == "njedu2023-prog" and source_repo.get("repo") == "a-share-top3-data"
             and source_repo.get("resolved_commit") == target["market_commit"]
             and market_meta.get("strict_dated_source") is True
             and market_meta.get("requested_trade_date") == market_meta.get("resolved_trade_date") == day
             and upstream.get("requested_trade_date") == upstream.get("resolved_trade_date") == day,
             "GENERATION_MARKET_SOURCE_META")
    market_time = upstream.get("generated_at_bj")
    _require(type(market_time) is str and re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", market_time),
             "GENERATION_MARKET_PRODUCTION_TIMESTAMP")
    market_produced = datetime.strptime(market_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    close = datetime.combine(datetime.strptime(day, "%Y%m%d").date(), time(15), ZoneInfo("Asia/Shanghai"))
    _require(close < pred_produced <= created and close < market_produced <= created,
             "GENERATION_SOURCES_OUTSIDE_ORIGINAL_DISPATCH_WINDOW")
    return {"pred_generated_at_utc": times[0], "market_generated_at_bj": market_time,
        "original_dispatch_created_at": target["dispatch_created_at"],
        "local_sync_timestamps_used_as_source_production_times": False,
        "upstream_commit_api_times_checked_by_this_module": False,
        "source_metadata_bindings": [{"path": p, "sha256": _sha(raw), "bytes": len(raw)}
                                     for p, raw in sorted(bodies.items())]}


def validate_generation_archive(archive, artifact, run, receipt, cas_commit,
                                base_tree, cas_tree, blob_loader):
    """Return verified bindings only; no I/O, authority, scoring or freezing.

    Trees must already have complete recursive Git object validation. The blob
    loader accepts (tree_mapping, exact_path); returned bytes are independently
    checked against that tree's Git object identity.
    """
    day = receipt.get("signal_date")
    _require(type(day) is str and re.fullmatch(r"20[0-9]{6}", day), "GENERATION_EXACT_D")
    _require(datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d") == day, "GENERATION_EXACT_D")
    _require(receipt.get("generation_mode") == "NATURAL" and receipt.get("primary_status") == "READY",
             "GENERATION_NATURAL_READY_RECEIPT")
    _require(type(run) is dict and type(run.get("id")) is int and run["id"] > 0
             and run.get("workflow_id") == 343703608 and run.get("path") == WORKFLOW_PATH
             and run.get("head_branch") == "main" and type(run.get("run_attempt")) is int
             and run["run_attempt"] == 1 and run.get("event") == "workflow_dispatch"
             and run.get("status") == "completed" and run.get("conclusion") == "failure"
             and run.get("name") == run.get("display_title") == f"DC20 controlled daily NATURAL | D={day}"
             and run.get("repository", {}).get("full_name") == REPOSITORY
             and run.get("head_repository", {}).get("full_name") == REPOSITORY,
             "GENERATION_EXACT_ORIGINAL_CONTROLLED_RUN")
    base = _exact_sha(run["head_sha"])
    created = _utc(run["created_at"])
    _require(created <= _utc(run["run_started_at"]) <= _utc(run["updated_at"]), "GENERATION_RUN_TIME_ORDER")
    created_bj = created.astimezone(ZoneInfo("Asia/Shanghai"))
    _require(created_bj.strftime("%Y%m%d") == day and time(15) < created_bj.time() < time(23, 30),
             "GENERATION_ORIGINAL_DAILY_WINDOW")
    _exact_sha(cas_commit.get("sha"))
    _exact_sha(cas_commit.get("tree", {}).get("sha"))
    _require(type(cas_commit.get("parents")) is list and len(cas_commit["parents"]) == 1
             and cas_commit["parents"][0].get("sha") == base == receipt["inputs"]["git_head"],
             "GENERATION_EXACT_ORIGINAL_CAS_PARENT")
    members = _archive(archive, artifact, run)
    _require(members["base_sha.txt"] == (base + "\n").encode(), "GENERATION_ARCHIVE_BASE")
    patch = members["primary-d.patch"]
    _require(members["primary-d.patch.sha256"] == (_sha(patch) + "\n").encode(),
             "GENERATION_PATCH_SHA")
    target = _json(members["primary-d-target-envelope.json"])
    _require(type(target) is dict and set(target) == {"signal_date", "generation_mode", "pred_commit",
        "market_commit", "dispatch_created_at"} and target["signal_date"] == day
        and target["generation_mode"] == "NATURAL" and _utc(target["dispatch_created_at"]) == created,
        "GENERATION_EXACT_TARGET_ENVELOPE")
    candidate, market = receipt["inputs"]["candidate"], receipt["inputs"]["market"]
    _require(candidate.get("source_repository") == "njedu2023-prog/a-top10"
             and market.get("source_repository") == "njedu2023-prog/a-share-top3-data"
             and _exact_sha(target["pred_commit"]) == candidate.get("resolved_commit")
             and _exact_sha(target["market_commit"]) == market.get("resolved_commit"),
             "GENERATION_ORIGINAL_IMMUTABLE_UPSTREAMS")
    _require(type(base_tree) is dict and type(cas_tree) is dict, "GENERATION_COMPLETE_TREE_MAPPINGS")
    leaf = lambda tree: {p: (e.get("mode"), e.get("type"), e.get("sha"))
                         for p, e in tree.items() if e.get("type") != "tree"}
    old_leaves, new_leaves = leaf(base_tree), leaf(cas_tree)
    changed = {p for p in set(old_leaves) | set(new_leaves) if old_leaves.get(p) != new_leaves.get(p)}
    allowed, four = _allowed(day, receipt)
    _require(four <= changed <= allowed and len(changed) <= MAX_PATCH_FILES,
             "GENERATION_EXACT_ALLOWED_PATCH_SCOPE")
    _require(not four.intersection(base_tree) and all(p in cas_tree for p in four),
             "GENERATION_FOUR_OUTPUTS_MUST_BE_NEW")
    for path in changed:
        _require(new_leaves.get(path, ())[:2] == ("100644", "blob")
                 and (path not in base_tree or old_leaves.get(path, ())[:2] == ("100644", "blob")),
                 "GENERATION_REGULAR_BLOBS_ONLY")
    added = set(cas_tree) - set(base_tree)
    parents = {"/".join(p.split("/")[:i]) for p in changed for i in range(1, len(p.split("/")))}
    _require(set(base_tree) <= set(cas_tree) and added <= changed | parents
             and all(cas_tree[p].get("type") == "tree" and cas_tree[p].get("mode") == "040000"
                     for p in added - changed)
             and all(cas_tree[p] == entry for p, entry in base_tree.items()
                     if entry.get("type") == "tree" and p not in parents),
             "GENERATION_UNRELATED_TREE_CHANGE")
    blocks = re.split(rb"(?m)^diff --git ", patch)
    _require(blocks[0] == b"" and 1 < len(blocks) <= MAX_PATCH_FILES + 1,
             "GENERATION_PATCH_FORMAT")
    bindings, loaded, total = {}, {}, 0
    for block in blocks[1:]:
        _require(b"\n" in block, "GENERATION_PATCH_HEADER")
        header, body = block.split(b"\n", 1)
        match = re.fullmatch(rb"a/([A-Za-z0-9_./-]+) b/([A-Za-z0-9_./-]+)", header)
        _require(match is not None and match[1] == match[2], "GENERATION_EXACT_DIFF_PATH")
        path = match[1].decode()
        _require(path in changed and path not in bindings, "GENERATION_DUPLICATE_OR_UNKNOWN_PATCH_PATH")
        before = base_tree.get(path)
        old_sha = before["sha"] if before else ZERO
        new_sha = cas_tree[path]["sha"]
        prefix = (f"index {old_sha}..{new_sha} 100644\n" if before else
                  f"new file mode 100644\nindex {ZERO}..{new_sha}\n").encode()
        _require(body.startswith(prefix), "GENERATION_FULL_INDEX_OR_MODE_MISMATCH")
        old = blob_loader(base_tree, path) if before else b""
        new = blob_loader(cas_tree, path)
        _require(type(old) is bytes and type(new) is bytes
                 and len(old) <= MAX_BLOB_BYTES and len(new) <= MAX_BLOB_BYTES
                 and (not before or _blob(old) == old_sha) and _blob(new) == new_sha,
                 "GENERATION_ORIGINAL_BLOB_IDENTITY")
        total += len(old) + len(new)
        _require(total <= MAX_TOTAL_BLOB_BYTES, "GENERATION_TOTAL_BLOB_LIMIT")
        body = body[len(prefix):]
        rebuilt, encoding = _binary(body, old) if body.startswith(b"GIT binary patch\n") else _text(body, old, path, not before)
        _require(rebuilt == new and _blob(rebuilt) == new_sha, "GENERATION_PATCH_DOES_NOT_REBUILD_CAS_BLOB")
        loaded[path] = new
        bindings[path] = {"path": path, "before_git_blob_sha1": old_sha,
            "after_git_blob_sha1": new_sha, "sha256": _sha(new), "bytes": len(new), "patch_encoding": encoding}
    _require(set(bindings) == changed, "GENERATION_PATCH_TREE_DIFF_NOT_CLOSED")
    receipt_path = f"outputs/decision/primary_d_receipt_{day}.json"
    _require(_json(loaded[receipt_path]) == receipt, "GENERATION_ORIGINAL_RECEIPT_BYTES")
    source_times = _source_times(receipt, target, loaded, cas_tree, blob_loader, created)
    return {"schema_version": SCHEMA, "status": "ORIGINAL_GENERATION_ARCHIVE_VERIFIED",
        "original_generation_run_id": run["id"], "original_generation_head_sha": base,
        "original_cas_commit_sha": cas_commit["sha"], "original_cas_tree_sha": cas_commit["tree"]["sha"],
        "artifact": {k: artifact[k] for k in ("id", "name", "size_in_bytes", "digest", "expired", "created_at", "updated_at")},
        "target_envelope": target, "patch_sha256": _sha(patch),
        "original_source_production_times": source_times,
        "member_bindings": [{"path": p, "sha256": _sha(raw), "bytes": len(raw)} for p, raw in sorted(members.items())],
        "patch_file_bindings": [bindings[p] for p in sorted(bindings)],
        "p0_four_file_bindings": [bindings[p] for p in sorted(four)],
        "complete_patch_and_tree_diff_verified": True, "original_api_jobs_verified_by_this_module": False,
        "new_prediction_freeze_verified": False, "source_authority_issued": False,
        "research_only": True, "remote_writes_performed": 0}

