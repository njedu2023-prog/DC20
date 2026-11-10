"""Append-only suspension facts, separate from the immutable model baseline.

Every byte is supplied by a caller bound to the captured source commit.  This
module does not fetch URLs, infer missing bars, choose candidates or score them.
Official PDF bytes accompany each new fact; old facts remain in the frozen v1
file.  The live pointer selects an immutable chain, never overwrites records.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit

ACTIVE_FROM = "20261111"
PREFIX = "data/market/suspension_evidence/"
INDEX_PATH = PREFIX + "index.json"
SCHEMA = "dc20_suspension_registry_v1"
DECLARATION_SCHEMA = "dc20_bound_suspension_registry_v1"
MAX_FILES = 512
MAX_VERSIONS = 128
MAX_BYTES = 16 * 1024 * 1024
MAX_OBJECT_BYTES = 4 * 1024 * 1024
OFFICIAL_HOSTS = {"static.cninfo.com.cn", "www.cninfo.com.cn", "www.sse.com.cn", "www.szse.cn", "disc.static.szse.cn"}


class SuspensionRegistryError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise SuspensionRegistryError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "duplicate suspension JSON key")
            out[key] = value
        return out
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise SuspensionRegistryError("invalid suspension JSON") from exc
    require(type(value) is dict, "suspension object required")
    return value


def _date(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{8}", value), "invalid suspension date")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise SuspensionRegistryError("invalid suspension date") from exc
    return value


def object_path(kind, digest):
    require(kind in {"versions", "records", "pdfs"}, "invalid suspension object kind")
    require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "invalid suspension object hash")
    return PREFIX + kind + "/" + digest + (".pdf" if kind == "pdfs" else ".json")


def validate_binding(binding):
    require(type(binding) is dict and set(binding) == {"path", "sha256", "git_blob_sha1", "git_mode"},
            "exact suspension HEAD binding required")
    path = binding["path"]
    require(path == INDEX_PATH or (type(path) is str and re.fullmatch(
        re.escape(PREFIX) + r"(?:versions/[0-9a-f]{64}\.json|records/[0-9a-f]{64}\.json|pdfs/[0-9a-f]{64}\.pdf)", path)),
        "suspension path outside registry")
    require(binding["git_mode"] == "100644" and type(binding["git_blob_sha1"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", binding["git_blob_sha1"])
            and type(binding["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]),
            "invalid suspension HEAD binding")
    return binding


def is_registry_path(path):
    return type(path) is str and (path == INDEX_PATH or re.fullmatch(
        re.escape(PREFIX) + r"(?:versions/[0-9a-f]{64}\.json|records/[0-9a-f]{64}\.json|pdfs/[0-9a-f]{64}\.pdf)", path) is not None)


def load_registry(read_bound, *, signal_date, legacy_records=()):
    """Read a complete immutable chain using callback(path)->(bytes, binding).

    Return exact source bindings for inclusion in the P0 receipt.  An absent or
    broken post-activation registry is an error, never treated as an empty list.
    Future records may be retained but cannot be used for an earlier D.
    """
    day = _date(signal_date)
    require(day >= ACTIVE_FROM, "registry unavailable before migration date")
    consumed = {}
    raw_cache = {}
    total_bytes = 0

    def read(path, digest=None):
        nonlocal total_bytes
        if path not in raw_cache:
            raw, binding = read_bound(path)
            validate_binding(binding)
            require(binding["path"] == path and type(raw) is bytes and 0 < len(raw) <= MAX_OBJECT_BYTES,
                    "invalid suspension bound bytes")
            require(sha(raw) == binding["sha256"] and hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == binding["git_blob_sha1"],
                "suspension bytes differ from HEAD binding")
            total_bytes += len(raw)
            require(total_bytes <= MAX_BYTES and len(consumed) < MAX_FILES, "suspension registry exceeds bounds")
            consumed[path], raw_cache[path] = dict(binding), raw
        raw = raw_cache[path]
        require(digest is None or sha(raw) == digest, "immutable suspension object hash mismatch")
        return raw

    index = _json(read(INDEX_PATH))
    require(set(index) == {"schema_version", "active_from", "version_sha256"}
            and index["schema_version"] == SCHEMA and index["active_from"] == ACTIVE_FROM,
            "invalid suspension registry pointer")
    current = index["version_sha256"]
    chain = []
    seen = set()
    while current is not None:
        require(current not in seen and len(chain) < MAX_VERSIONS, "suspension chain cycle or overflow")
        seen.add(current)
        version = _json(read(object_path("versions", current), current))
        require(set(version) == {"schema_version", "sequence", "parent_sha256", "added_record_sha256", "total_records"}
                and version["schema_version"] == SCHEMA and type(version["sequence"]) is int
                and type(version["total_records"]) is int and type(version["added_record_sha256"]) is list,
                "invalid suspension version object")
        chain.append(version)
        current = version["parent_sha256"]
    chain.reverse()
    require(chain and chain[0] == {"schema_version": SCHEMA, "sequence": 0, "parent_sha256": None,
            "added_record_sha256": [], "total_records": 0}, "exact empty suspension genesis required")
    hashes = []
    for sequence, version in enumerate(chain):
        additions = version["added_record_sha256"]
        require(version["sequence"] == sequence and (sequence == 0 or additions), "non-append suspension version")
        require(all(type(x) is str and re.fullmatch(r"[0-9a-f]{64}", x) for x in additions),
                "invalid suspension record reference")
        hashes.extend(additions)
        require(version["total_records"] == len(hashes) and len(hashes) == len(set(hashes)),
                "suspension append count or duplicate record mismatch")
    records = []
    identities = [(r["ts_code"], r["start_date"], r["resume_date"]) for r in legacy_records]
    for digest in hashes:
        record = _json(read(object_path("records", digest), digest))
        require(set(record) == {"schema_version", "ts_code", "start_date", "resume_date", "sources"}
                and record["schema_version"] == "dc20_suspension_fact_v1"
                and type(record["ts_code"]) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", record["ts_code"]),
                "invalid suspension fact identity")
        code = record["ts_code"]
        start, resume = _date(record["start_date"]), _date(record["resume_date"])
        require(start < resume, "invalid suspension interval")
        require(not any(c == code and max(s, start) < min(e, resume) for c, s, e in identities),
                "duplicate or overlapping suspension interval")
        identities.append((code, start, resume))
        sources = record["sources"]
        require(type(sources) is list and 1 <= len(sources) <= 20, "official suspension sources required")
        source_ids = set()
        for source in sources:
            require(type(source) is dict and set(source) == {"url", "published_date", "pdf_sha256"},
                    "exact official suspension source required")
            require(type(source["url"]) is str, "official suspension URL required")
            try:
                url = urlsplit(source["url"])
                port = url.port
            except ValueError as exc:
                raise SuspensionRegistryError("invalid suspension disclosure URL") from exc
            require(url.scheme == "https" and url.hostname in OFFICIAL_HOSTS and port is None
                    and url.username is None and url.password is None and not url.fragment
                    and url.path.lower().endswith(".pdf"), "unapproved suspension disclosure origin")
            published = _date(source["published_date"])
            require(source["url"] not in source_ids, "duplicate suspension source")
            source_ids.add(source["url"])
            if resume <= day:
                require(published <= day, "future suspension disclosure")
            pdf = read(object_path("pdfs", source["pdf_sha256"]), source["pdf_sha256"])
            require(pdf.startswith(b"%PDF-") and b"%%EOF" in pdf[-2048:], "official PDF evidence bytes required")
        if resume <= day:
            records.append({key: value for key, value in record.items() if key != "schema_version"})
    declaration = {"schema_version": DECLARATION_SCHEMA, "index_path": INDEX_PATH,
                   "index_sha256": consumed[INDEX_PATH]["sha256"],
                   "files": [consumed[path] for path in sorted(consumed)]}
    return records, declaration


def validate_declaration(value):
    require(type(value) is dict and set(value) == {"schema_version", "index_path", "index_sha256", "files"}
            and value["schema_version"] == DECLARATION_SCHEMA and value["index_path"] == INDEX_PATH
            and type(value["files"]) is list and 2 <= len(value["files"]) <= MAX_FILES,
            "invalid suspension registry declaration")
    by_path = {}
    for binding in value["files"]:
        validate_binding(binding)
        require(binding["path"] not in by_path, "duplicate suspension registry binding")
        by_path[binding["path"]] = binding
    require(list(by_path) == sorted(by_path) and INDEX_PATH in by_path
            and by_path[INDEX_PATH]["sha256"] == value["index_sha256"], "suspension index binding mismatch")
    return by_path


def validate_append_transition(previous_read_bound, next_read_bound, *, signal_date,
                               expected_previous_index_sha256, legacy_records=()):
    """Validate a reviewed, one-version append before committing a registry update.

    The caller must also commit with compare-and-swap against the source HEAD
    from which ``previous_read_bound`` was created. This function issues no Git
    write authority. A hash chain alone does not authenticate pointer updates.
    """
    _, before = load_registry(previous_read_bound, signal_date=signal_date, legacy_records=legacy_records)
    _, after = load_registry(next_read_bound, signal_date=signal_date, legacy_records=legacy_records)
    require(before["index_sha256"] == expected_previous_index_sha256, "suspension update CAS index mismatch")
    old_files, new_files = validate_declaration(before), validate_declaration(after)
    def reread(callback, path, expected):
        raw, actual = callback(path)
        require(actual == expected and sha(raw) == expected["sha256"] and hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected["git_blob_sha1"],
            "suspension update source changed during verification")
        return _json(raw)
    before_index = reread(previous_read_bound, INDEX_PATH, old_files[INDEX_PATH])
    after_index = reread(next_read_bound, INDEX_PATH, new_files[INDEX_PATH])
    old_hash, new_hash = before_index["version_sha256"], after_index["version_sha256"]
    old_path, new_path = object_path("versions", old_hash), object_path("versions", new_hash)
    old_version = reread(previous_read_bound, old_path, old_files[old_path])
    new_version = reread(next_read_bound, new_path, new_files[new_path])
    require(new_version["parent_sha256"] == old_hash and
            new_version["sequence"] == old_version["sequence"] + 1 and
            new_version["total_records"] > old_version["total_records"], "suspension update must append directly")
    require(all(path == INDEX_PATH or new_files.get(path) == binding for path, binding in old_files.items()),
            "suspension update changed or removed immutable history")
    return {"previous_index_sha256": before["index_sha256"], "next_index_sha256": after["index_sha256"],
            "previous_version_sha256": old_hash, "next_version_sha256": new_hash,
            "source_head_compare_and_swap_required": True}
