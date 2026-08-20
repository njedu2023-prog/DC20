from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "sync_market_raw.py"
)
SPEC = importlib.util.spec_from_file_location(
    "decision_sync_market_raw",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
sync_market_raw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_market_raw
SPEC.loader.exec_module(sync_market_raw)


class SyncMarketRawTest(unittest.TestCase):
    COMMIT = "b" * 40

    def test_explicit_date_rejects_stale_latest_fallback(self) -> None:
        urls = [
            "https://example.test/dated/daily.csv",
            "https://example.test/latest/daily.csv",
        ]
        stale = (
            "\ufefftrade_date,ts_code,open,close\n"
            "20260724,000001.SZ,10.0,10.1\n"
        )

        with mock.patch.object(
            sync_market_raw,
            "_http_get_text",
            side_effect=[
                (False, "", 404),
                (True, stale, 200),
            ],
        ):
            url, text, code, source_date, error = (
                sync_market_raw._fetch_first_matching_trade_date(
                    urls,
                    expected_trade_date="20260727",
                    date_scoped=True,
                )
            )

        self.assertIsNone(url)
        self.assertIsNone(text)
        self.assertEqual(code, 200)
        self.assertIsNone(source_date)
        self.assertEqual(
            error,
            "trade_date_mismatch:requested=20260727,actual=20260724",
        )

    def test_explicit_date_accepts_only_matching_snapshot(self) -> None:
        urls = [
            "https://example.test/latest/daily.csv",
            "https://example.test/root/daily.csv",
        ]
        stale = (
            "ts_code,trade_date,open,close\n"
            "000001.SZ,20260724,10.0,10.1\n"
        )
        current = (
            "ts_code,trade_date,open,close\n"
            "000001.SZ,20260727,10.2,10.3\n"
        )

        with mock.patch.object(
            sync_market_raw,
            "_http_get_text",
            side_effect=[
                (True, stale, 200),
                (True, current, 200),
            ],
        ):
            url, text, code, source_date, error = (
                sync_market_raw._fetch_first_matching_trade_date(
                    urls,
                    expected_trade_date="20260727",
                    date_scoped=True,
                )
            )

        self.assertEqual(url, urls[1])
        self.assertEqual(text, current)
        self.assertEqual(code, 200)
        self.assertEqual(source_date, "20260727")
        self.assertEqual(error, "")

    def test_static_reference_table_can_use_latest_snapshot(self) -> None:
        static = (
            "ts_code,name,industry\n"
            "000001.SZ,平安银行,银行\n"
        )
        with mock.patch.object(
            sync_market_raw,
            "_http_get_text",
            return_value=(True, static, 200),
        ):
            url, text, _, source_date, error = (
                sync_market_raw._fetch_first_matching_trade_date(
                    ["https://example.test/latest/stock_basic.csv"],
                    expected_trade_date="20260727",
                    date_scoped=False,
                )
            )

        self.assertIsNotNone(url)
        self.assertEqual(text, static)
        self.assertIsNone(source_date)
        self.assertEqual(error, "")

    def test_missing_commit_fails_before_any_directory_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {"MARKET_RAW_COMMIT": ""},
            clear=False,
        ), mock.patch.object(
            sync_market_raw,
            "_ensure_dir",
            side_effect=AssertionError("commit must resolve before write"),
        ), mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]):
            old = Path.cwd()
            os.chdir(temp)
            try:
                self.assertEqual(sync_market_raw.main(), 2)
                self.assertEqual(list(Path(temp).iterdir()), [])
            finally:
                os.chdir(old)

    def test_commit_alias_fails_before_any_directory_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {"MARKET_RAW_COMMIT": "main"},
            clear=False,
        ), mock.patch.object(
            sync_market_raw,
            "_ensure_dir",
            side_effect=AssertionError("commit alias must fail before write"),
        ), mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]):
            old = Path.cwd()
            os.chdir(temp)
            try:
                self.assertEqual(sync_market_raw.main(), 2)
                self.assertEqual(list(Path(temp).iterdir()), [])
            finally:
                os.chdir(old)

    def test_commit_with_newline_fails_before_download_or_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {"MARKET_RAW_COMMIT": self.COMMIT + "\n"},
            clear=False,
        ), mock.patch.object(
            sync_market_raw,
            "_load_upstream_meta",
            side_effect=AssertionError("newline commit must fail before download"),
        ), mock.patch.object(sys, "argv", [str(SCRIPT_PATH)]):
            old = Path.cwd()
            os.chdir(temp)
            try:
                self.assertEqual(sync_market_raw.main(), 2)
                self.assertEqual(list(Path(temp).iterdir()), [])
            finally:
                os.chdir(old)

    def test_success_meta_locks_commit_file_hash_and_body_change(self) -> None:
        first = "trade_date,ts_code,close\n20260820,600000.SH,10.0\n"
        second = "trade_date,ts_code,close\n20260820,600000.SH,10.1\n"
        spec = sync_market_raw.SourceSpec(
            "daily",
            "daily.csv",
            required=True,
        )
        current_body = {"text": first}

        def fetch(urls, **_kwargs):
            self.assertTrue(urls)
            self.assertTrue(all(f"/{self.COMMIT}/" in url for url in urls))
            return urls[0], current_body["text"], 200, "20260820", ""

        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {
                "MARKET_RAW_COMMIT": self.COMMIT,
                "MARKET_RAW_BRANCH": "main",
                "MARKET_RAW_OWNER": "njedu2023-prog",
                "MARKET_RAW_REPO": "a-share-top3-data",
            },
            clear=False,
        ), mock.patch.object(
            sync_market_raw,
            "SOURCE_SPECS",
            [spec],
        ), mock.patch.object(
            sync_market_raw,
            "_load_upstream_meta",
            return_value=({"trade_date": "20260820"}, f"https://raw.githubusercontent.com/njedu2023-prog/a-share-top3-data/{self.COMMIT}/data/raw/latest/_meta.json"),
        ) as load_meta, mock.patch.object(
            sync_market_raw,
            "_fetch_first_matching_trade_date",
            side_effect=fetch,
        ), mock.patch.object(
            sys,
            "argv",
            [str(SCRIPT_PATH), "--trade-date", "20260820"],
        ):
            old = Path.cwd()
            os.chdir(temp)
            try:
                self.assertEqual(sync_market_raw.main(), 0)
                meta_path = Path("data/market/raw/latest/_sync_meta.json")
                first_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                file_meta = first_meta["files"][0]
                self.assertEqual(
                    first_meta["source_repo"]["resolved_commit"],
                    self.COMMIT,
                )
                self.assertEqual(file_meta["bytes"], len(first.encode("utf-8")))
                self.assertEqual(
                    file_meta["sha256"],
                    hashlib.sha256(first.encode("utf-8")).hexdigest(),
                )
                self.assertIn(f"/{self.COMMIT}/", file_meta["source_url"])
                self.assertEqual(load_meta.call_args.kwargs["branch"], self.COMMIT)

                current_body["text"] = second
                self.assertEqual(sync_market_raw.main(), 0)
                second_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                self.assertNotEqual(
                    second_meta["files"][0]["sha256"],
                    file_meta["sha256"],
                )
            finally:
                os.chdir(old)

    def test_transaction_staged_write_failure_preserves_old_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            old_cwd = Path.cwd()
            os.chdir(temp)
            try:
                targets = {
                    Path("data/market/raw/2026/20260820/daily.csv"): b"new-dated",
                    Path("data/market/raw/latest/daily.csv"): b"new-latest",
                    Path("data/market/raw/latest/_sync_meta.json"): b"new-meta",
                }
                old = {
                    target: f"old-{index}".encode()
                    for index, target in enumerate(targets)
                }
                for target, body in old.items():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                real_write = sync_market_raw._write_staged_file
                calls = {"count": 0}

                def fail_second(path: Path, data: bytes) -> None:
                    calls["count"] += 1
                    if calls["count"] == 2:
                        raise OSError("injected staged write failure")
                    real_write(path, data)

                with mock.patch.object(
                    sync_market_raw,
                    "_write_staged_file",
                    side_effect=fail_second,
                ):
                    with self.assertRaisesRegex(OSError, "injected"):
                        sync_market_raw._transactional_replace(targets)
                self.assertEqual(
                    {target: target.read_bytes() for target in targets},
                    old,
                )
                self.assertEqual(
                    list(sync_market_raw.RAW_DIR.glob(".market-sync-*")),
                    [],
                )
            finally:
                os.chdir(old_cwd)

    def test_transaction_second_replace_failure_rolls_back_old_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            old_cwd = Path.cwd()
            os.chdir(temp)
            try:
                targets = {
                    Path("data/market/raw/2026/20260820/daily.csv"): b"new-dated",
                    Path("data/market/raw/latest/daily.csv"): b"new-latest",
                    Path("data/market/raw/latest/_sync_meta.json"): b"new-meta",
                }
                old = {
                    target: f"old-{index}".encode()
                    for index, target in enumerate(targets)
                }
                for target, body in old.items():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                calls = {"count": 0}

                def fail_second(source: Path, target: Path) -> None:
                    calls["count"] += 1
                    if calls["count"] == 2:
                        raise OSError("injected replace failure")
                    os.replace(source, target)

                with mock.patch.object(
                    sync_market_raw,
                    "_commit_replace",
                    side_effect=fail_second,
                ):
                    with self.assertRaisesRegex(OSError, "injected"):
                        sync_market_raw._transactional_replace(targets)
                self.assertEqual(
                    {target: target.read_bytes() for target in targets},
                    old,
                )
                self.assertEqual(
                    list(sync_market_raw.RAW_DIR.glob(".market-sync-*")),
                    [],
                )
            finally:
                os.chdir(old_cwd)

    def test_http_403_429_and_timeout_retry_policy_is_bounded(self) -> None:
        forbidden = mock.Mock(status_code=403, text="forbidden", encoding="utf-8")
        with mock.patch.object(
            sync_market_raw.requests,
            "get",
            return_value=forbidden,
        ) as get, mock.patch.object(sync_market_raw, "_retry_sleep"):
            self.assertEqual(
                sync_market_raw._http_get_text("https://example.test/file"),
                (False, "", 403),
            )
        self.assertEqual(get.call_count, 1)

        throttled = mock.Mock(status_code=429, text="", encoding="utf-8")
        success = mock.Mock(status_code=200, text="ok", encoding="utf-8")
        with mock.patch.object(
            sync_market_raw.requests,
            "get",
            side_effect=[throttled, success],
        ) as get, mock.patch.object(sync_market_raw, "_retry_sleep") as sleep:
            self.assertEqual(
                sync_market_raw._http_get_text("https://example.test/file"),
                (True, "ok", 200),
            )
        self.assertEqual(get.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

        with mock.patch.object(
            sync_market_raw.requests,
            "get",
            side_effect=sync_market_raw.requests.Timeout("timeout"),
        ) as get, mock.patch.object(sync_market_raw, "_retry_sleep"):
            self.assertEqual(
                sync_market_raw._http_get_text("https://example.test/file"),
                (False, "", 0),
            )
        self.assertEqual(get.call_count, 3)

    def test_invalid_meta_json_and_unavailable_required_file_fail_before_write(self) -> None:
        spec = sync_market_raw.SourceSpec("daily", "daily.csv", required=True)
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {"MARKET_RAW_COMMIT": self.COMMIT, "TRADE_DATE": ""},
            clear=False,
        ), mock.patch.object(
            sync_market_raw,
            "SOURCE_SPECS",
            [spec],
        ), mock.patch.object(
            sync_market_raw,
            "_fetch_first_available",
            return_value=("https://example.test/_meta.json", "{invalid", 200),
        ), mock.patch.object(
            sync_market_raw,
            "_fetch_first_matching_trade_date",
            side_effect=AssertionError(
                "invalid upstream meta must fail before source downloads"
            ),
        ), mock.patch.object(
            sys,
            "argv",
            [str(SCRIPT_PATH)],
        ):
            old_cwd = Path.cwd()
            os.chdir(temp)
            try:
                old_path = Path("data/market/raw/latest/daily.csv")
                old_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.write_bytes(b"old-generation")
                self.assertEqual(sync_market_raw.main(), 2)
                self.assertEqual(old_path.read_bytes(), b"old-generation")
                self.assertEqual(
                    list(sync_market_raw.RAW_DIR.glob(".market-sync-*")),
                    [],
                )
            finally:
                os.chdir(old_cwd)

    def test_second_required_download_failure_keeps_complete_old_generation(self) -> None:
        specs = [
            sync_market_raw.SourceSpec("daily", "daily.csv", required=True),
            sync_market_raw.SourceSpec(
                "daily_basic",
                "daily_basic.csv",
                required=True,
            ),
        ]
        first = "trade_date,ts_code,close\n20260820,600000.SH,10.0\n"
        calls = {"count": 0}

        def fetch(urls, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return urls[0], first, 200, "20260820", ""
            return None, None, 429, None, "not_found_in_candidate_urls"

        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {"MARKET_RAW_COMMIT": self.COMMIT},
            clear=False,
        ), mock.patch.object(
            sync_market_raw,
            "SOURCE_SPECS",
            specs,
        ), mock.patch.object(
            sync_market_raw,
            "_load_upstream_meta",
            return_value=({}, None),
        ), mock.patch.object(
            sync_market_raw,
            "_fetch_first_matching_trade_date",
            side_effect=fetch,
        ), mock.patch.object(
            sys,
            "argv",
            [str(SCRIPT_PATH), "--trade-date", "20260820"],
        ):
            old_cwd = Path.cwd()
            os.chdir(temp)
            try:
                old_paths = {
                    Path("data/market/raw/2026/20260820/daily.csv"): b"old-dated",
                    Path("data/market/raw/latest/daily.csv"): b"old-latest",
                    Path("data/market/raw/latest/_sync_meta.json"): b"old-meta",
                }
                for path, body in old_paths.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                self.assertEqual(sync_market_raw.main(), 2)
                self.assertEqual(
                    {path: path.read_bytes() for path in old_paths},
                    old_paths,
                )
                self.assertEqual(
                    list(sync_market_raw.RAW_DIR.glob(".market-sync-*")),
                    [],
                )
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
