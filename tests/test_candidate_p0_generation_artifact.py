"""Portable positive and adversarial cases for original P0 artifact binding."""
from __future__ import annotations
import base64
import copy
import difflib
import hashlib
import io
import json
import stat
import zipfile
import zlib

import pytest

from work.profit_1000_upgrade import candidate_p0_generation_artifact as sut

DAY = "20261014"
BASE = "a" * 40
CAS = "b" * 40
PRED = "c" * 40
MARKET = "d" * 40
RUN = 12345


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def js(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def section(kind, raw, *, compressed=None):
    compressed = zlib.compress(raw) if compressed is None else compressed
    out = [f"{kind} {len(raw)}".encode()]
    for pos in range(0, len(compressed), 52):
        part = compressed[pos:pos + 52]
        n = len(part)
        prefix = bytes([n + 64 if n <= 26 else n + 70])
        encoded = base64.b85encode(part + b"\0" * ((-n) % 4))
        out.append(prefix + encoded)
    return b"\n".join(out)


def binary(old, new, *, delta=False):
    forward = section("delta", bytes([len(old), len(new), len(new)]) + new) if delta else section("literal", new)
    reverse = section("literal", old)
    return b"GIT binary patch\n" + forward + b"\n\n" + reverse + b"\n\n"


def tree(files):
    return {path: {"path": path, "mode": "100644", "type": "blob", "sha": blob(raw), "size": len(raw)}
            for path, raw in files.items()}


def pack(members, *, duplicate=None, symlink=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, raw in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name == symlink:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, raw)
        if duplicate:
            z.writestr(duplicate, members[duplicate])
    return out.getvalue()


def fixture(*, pred_time="2026-10-14T12:20:34Z", market_time="2026-10-14 20:17:20"):
    pred_path = f"data/pred/archive/pred_source_{DAY}.csv"
    pred_csv = f"trade_date,verify_date,generated_at_utc\n{DAY},20261015,{pred_time}\n".encode()
    pred_meta = {"resolved_commit": PRED, "source_repository": "njedu2023-prog/a-top10",
        "resolved_trade_date": DAY, "source_ref": f"https://raw.githubusercontent.com/njedu2023-prog/a-top10/{PRED}/outputs/decisio/pred_decisio_{DAY}.csv",
        "sha256": sha(pred_csv), "body_sha256": sha(pred_csv),
        "created_at_utc": "2026-10-14T15:19:16Z"}
    meta_path = f"data/market/raw/2026/{DAY}/_sync_meta.json"
    market_meta = {"source_repo": {"owner": "njedu2023-prog", "repo": "a-share-top3-data", "resolved_commit": MARKET},
        "strict_dated_source": True, "requested_trade_date": DAY, "resolved_trade_date": DAY,
        "upstream_meta": {"requested_trade_date": DAY, "resolved_trade_date": DAY, "generated_at_bj": market_time},
        "created_at_utc": "2026-10-14T15:19:19Z"}
    daily_path = f"data/market/raw/2026/{DAY}/daily.csv"
    receipt = {"signal_date": DAY, "exec_date": "20261015", "exit_date": "20261016",
        "generation_mode": "NATURAL", "primary_status": "READY",
        "inputs": {"git_head": BASE,
            "candidate": {"path": pred_path, "meta_path": "data/pred/_pred_source_meta.json",
                "source_repository": "njedu2023-prog/a-top10", "resolved_commit": PRED,
                "sha256": sha(pred_csv), "meta_sha256": sha(js(pred_meta)), "row_count": 1},
            "market": {"meta_path": meta_path, "meta_sha256": sha(js(market_meta)),
                "source_repository": "njedu2023-prog/a-share-top3-data", "resolved_commit": MARKET,
                "tables": {"daily": {"path": daily_path}}}}}
    new = {pred_path: pred_csv, "data/pred/_pred_source_meta.json": js(pred_meta), meta_path: js(market_meta),
        daily_path: b"new\n", f"outputs/decision/primary_d_receipt_{DAY}.json": js(receipt),
        f"outputs/decision/primary_d_runtime_features_{DAY}.csv": b"a,b\n1,2\n",
        f"outputs/decision/three_rank_top10_{DAY}.csv": b"a,b\n1,2\n",
        f"outputs/decision/three_rank_top10_{DAY}.json": js({"signal_date": DAY})}
    old = {daily_path: b"old\n"}
    patches = []
    for path, raw in sorted(new.items()):
        before = old.get(path, b"")
        index = f"index {blob(before)}..{blob(raw)} 100644\n" if path in old else f"new file mode 100644\nindex {sut.ZERO}..{blob(raw)}\n"
        if path.endswith(".csv"):
            body = binary(before, raw, delta=path == daily_path)
        else:
            body = "".join(difflib.unified_diff(before.decode().splitlines(keepends=True), raw.decode().splitlines(keepends=True),
                fromfile="a/" + path if path in old else "/dev/null", tofile="b/" + path)).encode()
        patches.append(f"diff --git a/{path} b/{path}\n".encode() + index.encode() + body)
    patch = b"".join(patches)
    members = {"base_sha.txt": (BASE + "\n").encode(), "primary-d.patch": patch,
        "primary-d.patch.sha256": (sha(patch) + "\n").encode(),
        "primary-d-target-envelope.json": js({"signal_date": DAY, "generation_mode": "NATURAL",
            "pred_commit": PRED, "market_commit": MARKET, "dispatch_created_at": "2026-10-14T15:18:26Z"})}
    run = {"id": RUN, "workflow_id": 343703608, "path": sut.WORKFLOW_PATH, "head_branch": "main",
        "run_attempt": 1, "event": "workflow_dispatch", "status": "completed", "conclusion": "failure",
        "head_sha": BASE, "name": f"DC20 controlled daily NATURAL | D={DAY}",
        "display_title": f"DC20 controlled daily NATURAL | D={DAY}",
        "repository": {"full_name": sut.REPOSITORY}, "head_repository": {"full_name": sut.REPOSITORY},
        "created_at": "2026-10-14T15:18:26Z", "run_started_at": "2026-10-14T15:18:26Z",
        "updated_at": "2026-10-14T15:21:33Z"}
    archive = pack(members)
    artifact = {"id": 123, "name": f"primary-d-candidate-{RUN}-1", "size_in_bytes": len(archive),
        "digest": "sha256:" + sha(archive), "expired": False,
        "created_at": "2026-10-14T15:19:43Z", "updated_at": "2026-10-14T15:19:43Z",
        "workflow_run": {"id": RUN, "head_branch": "main", "head_sha": BASE}}
    cas = {"sha": CAS, "tree": {"sha": "e" * 40}, "parents": [{"sha": BASE}]}
    return {"archive": archive, "artifact": artifact, "run": run, "receipt": receipt, "cas_commit": cas,
            "base_tree": tree(old), "cas_tree": tree(new), "old": old, "new": new, "members": members}


def call(f):
    def loader(entries, path):
        return f["old"][path] if entries is f["base_tree"] else f["new"][path]
    return sut.validate_generation_archive(*(f[k] for k in ("archive", "artifact", "run", "receipt",
        "cas_commit", "base_tree", "cas_tree")), loader)


def repack(f, **kwargs):
    f["archive"] = pack(f["members"], **kwargs)
    f["artifact"]["digest"] = "sha256:" + sha(f["archive"])
    f["artifact"]["size_in_bytes"] = len(f["archive"])


def test_complete_literal_delta_text_positive_and_sync_time_honesty():
    result = call(fixture())
    assert result["status"] == "ORIGINAL_GENERATION_ARCHIVE_VERIFIED"
    assert len(result["p0_four_file_bindings"]) == 4
    assert {x["patch_encoding"] for x in result["patch_file_bindings"]} == {"text", "literal", "delta"}
    assert result["original_source_production_times"]["local_sync_timestamps_used_as_source_production_times"] is False
    assert result["original_source_production_times"]["pred_generated_at_utc"] == "2026-10-14T12:20:34Z"
    assert result["new_prediction_freeze_verified"] is False


@pytest.mark.parametrize(("obj", "key", "value"), [
    ("run", "run_attempt", 2), ("run", "head_branch", "other"), ("run", "conclusion", "success"),
    ("run", "workflow_id", 999), ("run", "event", "push"), ("run", "head_sha", "f" * 40),
    ("run", "created_at", "2026-10-14T16:00:00Z"), ("artifact", "expired", True),
    ("artifact", "digest", "sha256:" + "0" * 64), ("artifact", "size_in_bytes", 1),
])
def test_actual_identity_and_original_window_required(obj, key, value):
    f = fixture()
    f[obj][key] = value
    with pytest.raises(ValueError):
        call(f)


@pytest.mark.parametrize("mutation", ["extra_member", "duplicate", "symlink", "target_day", "target_source",
    "target_duplicate_key", "base", "patch_checksum", "patch_full_index", "patch_missing_file",
    "patch_added_body", "patch_mode", "patch_foreign_header", "cas_parent", "cas_two_parents",
    "cas_unrelated_leaf", "cas_unrelated_directory", "blob_body", "receipt_override"])
def test_resealed_malformed_original_material_still_rejected(mutation):
    f = fixture()
    patch = f["members"]["primary-d.patch"]
    if mutation == "extra_member":
        f["members"]["other.txt"] = b"unknown"
    elif mutation in ("duplicate", "symlink"):
        with pytest.warns(UserWarning) if mutation == "duplicate" else __import__("contextlib").nullcontext():
            repack(f, **{mutation: "base_sha.txt"})
        with pytest.raises(ValueError):
            call(f)
        return
    elif mutation in ("target_day", "target_source"):
        target = json.loads(f["members"]["primary-d-target-envelope.json"])
        target["signal_date" if mutation == "target_day" else "pred_commit"] = "20261013" if mutation == "target_day" else "f" * 40
        f["members"]["primary-d-target-envelope.json"] = js(target)
    elif mutation == "target_duplicate_key":
        raw = f["members"]["primary-d-target-envelope.json"]
        f["members"]["primary-d-target-envelope.json"] = raw.replace(b"{", b'{"signal_date":"20261013",', 1)
    elif mutation == "base":
        f["members"]["base_sha.txt"] = ("f" * 40 + "\n").encode()
    elif mutation == "patch_checksum":
        f["members"]["primary-d.patch.sha256"] = ("0" * 64 + "\n").encode()
    elif mutation.startswith("patch_"):
        if mutation == "patch_full_index":
            patch = patch.replace(sut.ZERO.encode(), ("f" * 40).encode(), 1)
        elif mutation == "patch_missing_file":
            patch = b"diff --git " + patch.split(b"diff --git ", 2)[2]
        elif mutation == "patch_added_body":
            patch = patch + b"trailing unregistered content\n"
        elif mutation == "patch_mode":
            patch = patch.replace(b"new file mode 100644", b"new file mode 120000", 1)
        else:
            patch = patch.replace(b"diff --git a/data/", b"diff --git a/../data/", 1)
        f["members"]["primary-d.patch"] = patch
        f["members"]["primary-d.patch.sha256"] = (sha(patch) + "\n").encode()
    elif mutation == "cas_parent":
        f["cas_commit"]["parents"][0]["sha"] = "f" * 40
    elif mutation == "cas_two_parents":
        f["cas_commit"]["parents"].append({"sha": "f" * 40})
    elif mutation == "cas_unrelated_leaf":
        f["cas_tree"]["unrelated"] = {"type": "blob", "mode": "100644", "sha": "f" * 40}
    elif mutation == "cas_unrelated_directory":
        f["cas_tree"]["unrelated"] = {"type": "tree", "mode": "040000", "sha": "f" * 40}
    elif mutation == "blob_body":
        f["new"][next(iter(f["new"]))] += b"x"
    else:
        f["receipt"]["fake_field"] = True
    repack(f)
    with pytest.raises(ValueError):
        call(f)


@pytest.mark.parametrize(("pred_time", "market_time"), [
    ("2026-10-14T15:18:27Z", "2026-10-14 20:17:20"),
    ("2026-10-14T07:00:00Z", "2026-10-14 20:17:20"),
    ("2026-10-14T12:20:34Z", "2026-10-14 23:18:27"),
    ("2026-10-14T12:20:34Z", "2026-10-14 15:00:00"),
])
def test_actual_source_production_times_must_precede_original_dispatch(pred_time, market_time):
    with pytest.raises(ValueError, match="SOURCES_OUTSIDE"):
        call(fixture(pred_time=pred_time, market_time=market_time))


def test_delta_copy_insert_and_zero_default_size():
    assert sut._delta(bytes([3, 4, 0x90, 2, 2]) + b"XY", b"abc") == b"abXY"
    n = bytes([0x80, 0x80, 4])
    assert sut._delta(n + n + bytes([0x80]), b"x" * 65536) == b"x" * 65536


@pytest.mark.parametrize("program", [
    b"", b"\x80", bytes([4, 1, 1, 65]), bytes([3, 1, 0]),
    bytes([3, 2, 2, 65]), bytes([3, 1, 0x91, 3, 1]),
    bytes([3, 1, 0x90]), bytes([3, 1, 2, 65, 66]), bytes([3, 2, 1, 65]),
    bytes([0x83, 0, 1, 1, 65]), bytes([0x80, 0x80, 0x80, 0x80, 0x80, 0]),
])
def test_delta_malformed_boundaries(program):
    with pytest.raises(ValueError):
        sut._delta(program, b"abc")


@pytest.mark.parametrize("compressed", [
    zlib.compress(b"abc") + b"junk", zlib.compress(b"abc")[:-1],
    zlib.compress(b"abc") + zlib.compress(b"def"), zlib.compress(b"abcd"),
])
def test_inflate_rejects_trailing_truncated_and_wrong_expansion(compressed):
    with pytest.raises(ValueError):
        sut._inflate(section("literal", b"abc", compressed=compressed))


def test_binary_reverse_cannot_lie_about_original_base():
    with pytest.raises(ValueError, match="REVERSE"):
        sut._binary(b"GIT binary patch\n" + section("literal", b"new") + b"\n\n"
                    + section("literal", b"wrong") + b"\n\n", b"old")


def test_text_unterminated_lines_and_exact_context():
    patch = b"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n"
    assert sut._text(patch, b"old", "x", False)[0] == b"new"
    with pytest.raises(ValueError, match="BASE_MISMATCH"):
        sut._text(patch, b"other", "x", False)


@pytest.mark.parametrize("body", [
    b"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n@@ -1 +1 @@\n-old\n+new\n",
    b"--- a/x\n+++ b/x\n@@ -1,2 +1 @@\n-old\n+new\n",
    b"--- a/x\n+++ b/x\n@@ -1 +2 @@\n-old\n+new\n",
    b"--- a/x\n+++ b/y\n@@ -1 +1 @@\n-old\n+new\n",
    b"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n\\ No newline at end of file\n",
    b"--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n-old\n+new\n\\ No newline at end of file\n+next\n",
])
def test_text_overlap_count_path_and_sentinel_fail(body):
    with pytest.raises(ValueError):
        sut._text(body, b"old\n", "x", False)

