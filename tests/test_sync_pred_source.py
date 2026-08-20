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


def test_remote_meta_locks_commit_and_each_downloaded_body(
    tmp_path: Path,
    monkeypatch,
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

    with mock.patch.object(sync_pred, "_download_bytes", return_value=second):
        assert sync_pred.main() == 0
    second_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert second_meta["resolved_commit"] == COMMIT
    assert second_meta["body_sha256"] == hashlib.sha256(second).hexdigest()
    assert second_meta["body_sha256"] != first_meta["body_sha256"]


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
