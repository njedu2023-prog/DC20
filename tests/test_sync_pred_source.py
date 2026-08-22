from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest import mock
from urllib import error as urllib_error

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_pred_source.py"
SPEC = importlib.util.spec_from_file_location("decision_sync_pred_source", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
sync_pred = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_pred
SPEC.loader.exec_module(sync_pred)


COMMIT = "a" * 40
IMMUTABLE_URL = (
    "https://raw.githubusercontent.com/njedu2023-prog/a-top10/"
    f"{COMMIT}/outputs/decisio/pred_decisio_latest.csv"
)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _clear_source_env(monkeypatch) -> None:
    for name in (
        "TOP10_PRED_URL",
        "TOP10_PRED_PATH",
        "TOP10_PRED_RESOLVED_COMMIT",
        "TRADE_DATE",
        "TOP10_ALLOW_OLDER_PRED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_mutable_main_url_is_rejected_before_download_or_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "TOP10_PRED_URL",
        "https://raw.githubusercontent.com/njedu2023-prog/a-top10/main/outputs/decisio/pred_decisio_latest.csv",
    )
    monkeypatch.setenv("TOP10_PRED_RESOLVED_COMMIT", COMMIT)
    with mock.patch.object(
        sync_pred,
        "_download_bytes",
        side_effect=AssertionError("mutable URL must fail before download"),
    ):
        assert sync_pred.main() == 2
    assert not (tmp_path / "data").exists()


def test_commit_alias_is_rejected_before_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOP10_PRED_URL", IMMUTABLE_URL)
    monkeypatch.setenv("TOP10_PRED_RESOLVED_COMMIT", "main")
    with mock.patch.object(
        sync_pred,
        "_download_bytes",
        side_effect=AssertionError("commit alias must fail before download"),
    ):
        assert sync_pred.main() == 2
    assert not (tmp_path / "data").exists()


def test_commit_with_newline_is_rejected_before_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOP10_PRED_URL", IMMUTABLE_URL)
    monkeypatch.setenv("TOP10_PRED_RESOLVED_COMMIT", COMMIT + "\n")
    with mock.patch.object(
        sync_pred,
        "_download_bytes",
        side_effect=AssertionError("newline commit must fail before download"),
    ):
        assert sync_pred.main() == 2
    assert not (tmp_path / "data").exists()


def test_missing_resolved_commit_fails_before_download_or_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOP10_PRED_URL", IMMUTABLE_URL)
    with mock.patch.object(
        sync_pred,
        "_download_bytes",
        side_effect=AssertionError("missing commit must fail before download"),
    ):
        assert sync_pred.main() == 2
    assert not (tmp_path / "data").exists()


def test_remote_meta_locks_commit_and_rejects_same_date_archive_rewrite(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOP10_PRED_URL", IMMUTABLE_URL)
    monkeypatch.setenv("TOP10_PRED_RESOLVED_COMMIT", COMMIT)
    monkeypatch.setenv("TRADE_DATE", "20260820")
    first = b"trade_date,ts_code\n20260820,600000.SH\n"
    second = b"trade_date,ts_code\n20260820,000001.SZ\n"

    with mock.patch.object(sync_pred, "_download_bytes", return_value=first):
        assert sync_pred.main() == 0
    meta_path = tmp_path / "data" / "pred" / "_pred_source_meta.json"
    first_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert first_meta["resolved_commit"] == COMMIT
    assert first_meta["source_repository"] == "njedu2023-prog/a-top10"
    assert first_meta["body_sha256"] == hashlib.sha256(first).hexdigest()
    assert first_meta["body_bytes"] == len(first)
    generation_paths = (
        tmp_path / "data" / "pred" / "pred_source_latest.csv",
        tmp_path / "data" / "pred" / "archive" / "pred_source_20260820.csv",
        meta_path,
    )
    first_generation = {path: path.read_bytes() for path in generation_paths}

    with mock.patch.object(sync_pred, "_download_bytes", return_value=second):
        assert sync_pred.main() == 2
    assert {path: path.read_bytes() for path in generation_paths} == first_generation
    error = capsys.readouterr().err
    assert "immutable prediction archive conflict" in error
    assert hashlib.sha256(first).hexdigest() in error
    assert hashlib.sha256(second).hexdigest() in error


def test_local_path_mode_remains_available_without_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "local_pred_20260820.csv"
    body = b"trade_date,ts_code\n20260820,600000.SH\n"
    source.write_bytes(body)
    monkeypatch.setenv("TOP10_PRED_PATH", str(source))

    assert sync_pred.main() == 0
    meta = json.loads(
        (tmp_path / "data" / "pred" / "_pred_source_meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["source"] == "path"
    assert meta["resolved_commit"] == ""
    assert meta["body_sha256"] == hashlib.sha256(body).hexdigest()


def test_csv_source_date_rejects_two_mixed_rows() -> None:
    body = b"trade_date,ts_code\n20260820,600000.SH\n20260821,000001.SZ\n"
    with pytest.raises(RuntimeError, match="mixed source dates"):
        sync_pred._extract_trade_date_from_csv_bytes(body)


@pytest.mark.parametrize("matching_rows", [500, 1000])
def test_csv_source_date_scans_every_row_beyond_old_sample_limits(
    matching_rows: int,
) -> None:
    rows = ["trade_date,ts_code"]
    rows.extend(f"20260820,{index:06d}.SH" for index in range(matching_rows))
    rows.append("20260821,999999.SZ")
    body = ("\n".join(rows) + "\n").encode("utf-8")
    with pytest.raises(RuntimeError, match="mixed source dates"):
        sync_pred._extract_trade_date_from_csv_bytes(body)


def test_trade_date_environment_must_match_csv_body(monkeypatch) -> None:
    monkeypatch.setenv("TRADE_DATE", "20260821")
    body = b"trade_date,ts_code\n20260820,600000.SH\n"
    with pytest.raises(RuntimeError, match="TRADE_DATE differs"):
        sync_pred._resolve_trade_date(url="", path="pred_source_latest.csv", data=body)


@pytest.mark.parametrize(
    "env_date",
    ["2026-08-20", "20260230", "202608200", "20260820 "],
)
def test_invalid_trade_date_environment_fails_closed(
    monkeypatch,
    env_date: str,
) -> None:
    monkeypatch.setenv("TRADE_DATE", env_date)
    body = b"trade_date,ts_code\n20260820,600000.SH\n"
    with pytest.raises(RuntimeError, match="TRADE_DATE"):
        sync_pred._resolve_trade_date(url="", path="pred_source_latest.csv", data=body)


def test_csv_source_date_with_surrounding_whitespace_fails_closed() -> None:
    body = b"trade_date,ts_code\n20260820 ,600000.SH\n"
    with pytest.raises(RuntimeError, match="exactly YYYYMMDD"):
        sync_pred._extract_trade_date_from_csv_bytes(body)


@pytest.mark.parametrize(
    ("url", "path"),
    [
        ("", "/tmp/pred_source_20260821.csv"),
        ("https://example.test/pred_source_20260821.csv", ""),
    ],
)
def test_source_basename_date_must_match_csv_body(url: str, path: str) -> None:
    body = b"trade_date,ts_code\n20260820,600000.SH\n"
    with pytest.raises(RuntimeError, match="basename date differs"):
        sync_pred._resolve_trade_date(url=url, path=path, data=body)


def test_latest_url_uses_csv_body_date() -> None:
    body = b"signal_date,verify_date,ts_code\n20260820,20260821,600000.SH\n"
    assert (
        sync_pred._resolve_trade_date(
            url="https://example.test/pred_source_latest.csv",
            path="",
            data=body,
        )
        == "20260820"
    )


def test_commit_sha_date_token_is_not_misread_as_source_basename_date() -> None:
    commit_with_date = "20260821" + "a" * 32
    body = b"trade_date,ts_code\n20260820,600000.SH\n"
    url = (
        "https://raw.githubusercontent.com/njedu2023-prog/a-top10/"
        f"{commit_with_date}/outputs/decisio/pred_decisio_latest.csv"
    )
    assert sync_pred._resolve_trade_date(url=url, path="", data=body) == "20260820"


def test_trade_and_signal_date_columns_must_match_row_by_row() -> None:
    body = (
        b"trade_date,signal_date,verify_date,ts_code\n"
        b"20260820,20260821,20260822,600000.SH\n"
    )
    with pytest.raises(RuntimeError, match="trade_date/signal_date mismatch"):
        sync_pred._extract_trade_date_from_csv_bytes(body)


def test_verify_and_target_dates_are_not_source_dates() -> None:
    body = (
        b"signal_date,verify_date,target_trade_date,ts_code\n"
        b"20260820,20260821,20260824,600000.SH\n"
        b"20260820,20260822,20260825,000001.SZ\n"
    )
    assert sync_pred._extract_trade_date_from_csv_bytes(body) == "20260820"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "empty"),
        (b"trade_date,ts_code\n", "no data rows"),
        (b"verify_date,ts_code\n20260820,600000.SH\n", "no canonical"),
        (b"target_trade_date,ts_code\n20260820,600000.SH\n", "no canonical"),
        (b"trade_date,ts_code\n20260230,600000.SH\n", "real calendar date"),
        (b"trade_date,ts_code\n,600000.SH\n", "exactly YYYYMMDD"),
    ],
)
def test_csv_source_date_empty_missing_or_invalid_fails_closed(
    body: bytes,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        sync_pred._extract_trade_date_from_csv_bytes(body)


def test_existing_same_archive_bytes_are_idempotent_and_not_replaced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "pred_source_latest.csv"
    body = b"trade_date,ts_code\n20260820,600000.SH\n"
    source.write_bytes(body)
    monkeypatch.setenv("TOP10_PRED_PATH", str(source))
    assert sync_pred.main() == 0

    archive = tmp_path / "data" / "pred" / "archive" / "pred_source_20260820.csv"
    with mock.patch.object(
        sync_pred,
        "_commit_replace",
        wraps=sync_pred._commit_replace,
    ) as replace:
        assert sync_pred.main() == 0
    replaced_targets = [call.args[1] for call in replace.call_args_list]
    assert archive.read_bytes() == body
    assert sync_pred.ARCHIVE_DIR / "pred_source_20260820.csv" not in replaced_targets


@pytest.mark.parametrize("archive_kind", ["symlink", "directory"])
def test_archive_symlink_or_nonregular_target_fails_before_generation_change(
    tmp_path: Path,
    monkeypatch,
    archive_kind: str,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "pred_source_latest.csv"
    body = b"trade_date,ts_code\n20260820,600000.SH\n"
    source.write_bytes(body)
    monkeypatch.setenv("TOP10_PRED_PATH", str(source))

    snapshot = tmp_path / "data" / "pred" / "pred_source_latest.csv"
    meta = tmp_path / "data" / "pred" / "_pred_source_meta.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    old_snapshot = b"trade_date,ts_code\n20260819,000001.SZ\n"
    old_meta = b'{"old": true}\n'
    snapshot.write_bytes(old_snapshot)
    meta.write_bytes(old_meta)
    archive = tmp_path / "data" / "pred" / "archive" / "pred_source_20260820.csv"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive_kind == "symlink":
        symlink_target = tmp_path / "outside.csv"
        symlink_target.write_bytes(b"outside")
        archive.symlink_to(symlink_target)
    else:
        archive.mkdir()

    assert sync_pred.main() == 2
    assert snapshot.read_bytes() == old_snapshot
    assert meta.read_bytes() == old_meta
    if archive_kind == "symlink":
        assert archive.is_symlink()
    else:
        assert archive.is_dir()


def test_two_distinct_dates_create_separate_immutable_archives(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_source_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    first = tmp_path / "pred_source_20260820.csv"
    second = tmp_path / "pred_source_20260821.csv"
    first_body = b"trade_date,ts_code\n20260820,600000.SH\n"
    second_body = b"trade_date,ts_code\n20260821,000001.SZ\n"
    first.write_bytes(first_body)
    second.write_bytes(second_body)

    monkeypatch.setenv("TOP10_PRED_PATH", str(first))
    assert sync_pred.main() == 0
    monkeypatch.setenv("TOP10_PRED_PATH", str(second))
    assert sync_pred.main() == 0

    archive_dir = tmp_path / "data" / "pred" / "archive"
    assert (archive_dir / "pred_source_20260820.csv").read_bytes() == first_body
    assert (archive_dir / "pred_source_20260821.csv").read_bytes() == second_body
    assert (
        tmp_path / "data" / "pred" / "pred_source_latest.csv"
    ).read_bytes() == second_body


def test_transaction_staged_write_failure_preserves_complete_old_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    targets = {
        sync_pred.SNAPSHOT_PATH: b"new-snapshot",
        sync_pred.ARCHIVE_DIR / "pred_source_20260820.csv": b"new-archive",
        sync_pred.META_PATH: b"new-meta",
    }
    old = {target: f"old-{index}".encode() for index, target in enumerate(targets)}
    for target, body in old.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    real_write = sync_pred._write_staged_file
    calls = {"count": 0}

    def fail_second(path: Path, data: bytes) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected staged write failure")
        real_write(path, data)

    monkeypatch.setattr(sync_pred, "_write_staged_file", fail_second)
    with pytest.raises(OSError, match="injected"):
        sync_pred._transactional_replace(targets)
    assert {target: target.read_bytes() for target in targets} == old
    assert list(sync_pred.SNAPSHOT_PATH.parent.glob(".pred-sync-*")) == []


def test_transaction_second_replace_failure_rolls_back_complete_old_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    targets = {
        sync_pred.SNAPSHOT_PATH: b"new-snapshot",
        sync_pred.ARCHIVE_DIR / "pred_source_20260820.csv": b"new-archive",
        sync_pred.META_PATH: b"new-meta",
    }
    old = {target: f"old-{index}".encode() for index, target in enumerate(targets)}
    for target, body in old.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    calls = {"count": 0}

    def fail_second(source: Path, target: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected replace failure")
        os.replace(source, target)

    monkeypatch.setattr(sync_pred, "_commit_replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        sync_pred._transactional_replace(targets)
    assert {target: target.read_bytes() for target in targets} == old
    assert list(sync_pred.SNAPSHOT_PATH.parent.glob(".pred-sync-*")) == []


def test_transaction_replace_precheck_failure_preserves_old_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    targets = {
        sync_pred.SNAPSHOT_PATH: b"new-snapshot",
        sync_pred.META_PATH: b"new-meta",
    }
    old = {target: f"old-{index}".encode() for index, target in enumerate(targets)}
    for target, body in old.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    real_validate = sync_pred._validate_transaction_targets
    calls = {"count": 0}

    def fail_second(payloads: dict[Path, bytes]) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("injected replace precheck failure")
        real_validate(payloads)

    monkeypatch.setattr(sync_pred, "_validate_transaction_targets", fail_second)
    with pytest.raises(RuntimeError, match="precheck"):
        sync_pred._transactional_replace(targets)
    assert {target: target.read_bytes() for target in targets} == old
    assert list(sync_pred.SNAPSHOT_PATH.parent.glob(".pred-sync-*")) == []


def test_http_403_fails_without_retry() -> None:
    forbidden = urllib_error.HTTPError(IMMUTABLE_URL, 403, "forbidden", None, None)
    with mock.patch.object(
        sync_pred.urllib.request,
        "urlopen",
        side_effect=forbidden,
    ) as call, mock.patch.object(sync_pred, "_retry_sleep"):
        with pytest.raises(urllib_error.HTTPError):
            sync_pred._download_bytes(IMMUTABLE_URL)
    assert call.call_count == 1


def test_http_429_and_timeout_have_bounded_retry() -> None:
    throttled = urllib_error.HTTPError(IMMUTABLE_URL, 429, "throttled", None, None)
    with mock.patch.object(
        sync_pred.urllib.request,
        "urlopen",
        side_effect=[throttled, _Response(b"ok")],
    ) as call, mock.patch.object(sync_pred, "_retry_sleep") as sleep:
        assert sync_pred._download_bytes(IMMUTABLE_URL) == b"ok"
    assert call.call_count == 2
    assert sleep.call_count == 1

    with mock.patch.object(
        sync_pred.urllib.request,
        "urlopen",
        side_effect=TimeoutError("timeout"),
    ) as call, mock.patch.object(sync_pred, "_retry_sleep"):
        with pytest.raises(TimeoutError):
            sync_pred._download_bytes(IMMUTABLE_URL)
    assert call.call_count == 3


def test_daily_resolves_both_upstreams_before_using_only_sha_raw_urls() -> None:
    workflow = (
        SCRIPT_PATH.parents[1]
        / ".github"
        / "workflows"
        / "run_decision_daily.yml"
    ).read_text(encoding="utf-8")
    resolve = workflow.index("Resolve immutable upstream commits before any source write")
    sync = workflow.index("Sync prediction and market source snapshots")
    assert resolve < sync
    assert "/repos/{owner}/{repo}/commits/main" in workflow
    assert "re.fullmatch(r'[0-9a-f]{40}', sha)" in workflow
    assert "TOP10_PRED_RESOLVED_COMMIT: ${{ steps.upstream.outputs.pred_sha }}" in workflow
    assert "MARKET_RAW_COMMIT: ${{ steps.upstream.outputs.market_sha }}" in workflow
    assert "a-top10/${A_TOP10_COMMIT}/outputs/decisio" in workflow
    assert "a-top10/main/outputs/decisio" not in workflow
    compute = workflow[workflow.index("  compute:") : workflow.index("\n  publish:")]
    assert "default: true" in workflow
    assert "--force-inactive" in compute
    assert "steps.mode.outputs.publish == 'true'" in compute
