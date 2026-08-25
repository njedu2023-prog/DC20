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
    CONTEXT_DATES = (
        "20260728",
        "20260729",
        "20260730",
        "20260731",
        "20260803",
        "20260804",
        "20260805",
        "20260806",
        "20260807",
        "20260810",
        "20260811",
        "20260812",
        "20260813",
        "20260814",
        "20260817",
        "20260818",
        "20260819",
        "20260820",
        "20260821",
        "20260824",
        "20260825",
    )

    @classmethod
    def _calendar_rows(cls) -> list[dict[str, str]]:
        previous = "20260727"
        rows: list[dict[str, str]] = []
        for trade_date in cls.CONTEXT_DATES:
            rows.append(
                {
                    "exchange": "SSE",
                    "cal_date": trade_date,
                    "is_open": "1",
                    "pretrade_date": previous,
                }
            )
            previous = trade_date
        return rows

    @staticmethod
    def _write_calendar(path: Path, rows: list[dict[str, str]]) -> None:
        lines = ["exchange,cal_date,is_open,pretrade_date"]
        lines.extend(
            ",".join(
                (
                    row["exchange"],
                    row["cal_date"],
                    row["is_open"],
                    row["pretrade_date"],
                )
            )
            for row in rows
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _install_strict_generation(
        *,
        trade_date: str,
        commit: str,
        specs: list[object],
        require_latest: bool,
    ) -> dict[str, object]:
        owner = "njedu2023-prog"
        repo = "a-share-top3-data"
        exact_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}"
        files: list[dict[str, object]] = []
        for spec in specs:
            if spec.date_scoped:
                payload = (
                    "trade_date,ts_code,close\n"
                    f"{trade_date},600000.SH,10.0\n"
                ).encode("utf-8")
                source_trade_date: str | None = trade_date
            else:
                payload = b"ts_code,name\n600000.SH,PF\n"
                source_trade_date = None
            dated_path = sync_market_raw._build_dated_path(
                spec.upstream_name,
                trade_date,
            )
            dated_path.parent.mkdir(parents=True, exist_ok=True)
            dated_path.write_bytes(payload)
            latest_path = sync_market_raw._build_latest_path(spec.upstream_name)
            if require_latest:
                latest_path.parent.mkdir(parents=True, exist_ok=True)
                latest_path.write_bytes(payload)
            files.append(
                {
                    "name": spec.local_stem,
                    "upstream_name": spec.upstream_name,
                    "required": spec.required,
                    "date_scoped": spec.date_scoped,
                    "success": True,
                    "source_url": (
                        f"{exact_base}/data/raw/{trade_date[:4]}/{trade_date}/"
                        f"{spec.upstream_name}"
                    ),
                    "source_trade_date": source_trade_date,
                    "status_code": 200,
                    "error": "",
                    "dated_path": str(dated_path),
                    "latest_path": str(latest_path) if require_latest else "",
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        meta: dict[str, object] = {
            "trade_date": trade_date,
            "requested_trade_date": trade_date,
            "resolved_trade_date": trade_date,
            "strict_dated_source": True,
            "dated_only": not require_latest,
            "source_repo": {
                "owner": owner,
                "repo": repo,
                "branch": "main",
                "resolved_commit": commit,
            },
            "upstream_meta_url": (
                f"{exact_base}/data/raw/{trade_date[:4]}/{trade_date}/_meta.json"
            ),
            "files": files,
            "required_failures": [],
            "write_failures": [],
        }
        meta_bytes = (
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        dated_meta = sync_market_raw._build_meta_dated_path(trade_date)
        dated_meta.write_bytes(meta_bytes)
        if require_latest:
            latest_meta = sync_market_raw._build_meta_latest_path()
            latest_meta.parent.mkdir(parents=True, exist_ok=True)
            latest_meta.write_bytes(meta_bytes)
        return meta

    def test_strict_dated_url_is_unique_and_exact_commit_bound(self) -> None:
        urls = sync_market_raw._build_candidate_urls(
            "njedu2023-prog",
            "a-share-top3-data",
            self.COMMIT,
            "daily.csv",
            "20260825",
            strict_dated=True,
        )
        self.assertEqual(
            urls,
            [
                "https://raw.githubusercontent.com/njedu2023-prog/"
                f"a-share-top3-data/{self.COMMIT}/data/raw/2026/20260825/"
                "daily.csv"
            ],
        )
        self.assertNotIn("latest", urls[0])
        with self.assertRaisesRegex(RuntimeError, "requires trade_date"):
            sync_market_raw._build_candidate_urls(
                "njedu2023-prog",
                "a-share-top3-data",
                self.COMMIT,
                "daily.csv",
                None,
                strict_dated=True,
            )

    def test_strict_dated_download_rejects_mixed_date_rows(self) -> None:
        mixed = (
            "trade_date,ts_code,close\n"
            "20260825,600000.SH,10.0\n"
            "20260824,600001.SH,11.0\n"
        )
        with mock.patch.object(
            sync_market_raw,
            "_http_get_text",
            return_value=(True, mixed, 200),
        ):
            url, text, code, source_date, error = (
                sync_market_raw._fetch_first_matching_trade_date(
                    ["https://example.test/exact/daily.csv"],
                    expected_trade_date="20260825",
                    date_scoped=True,
                    require_all_rows_match=True,
                )
            )
        self.assertIsNone(url)
        self.assertIsNone(text)
        self.assertEqual(code, 200)
        self.assertIsNone(source_date)
        self.assertTrue(error.startswith("trade_date_mismatch:"), error)
        self.assertFalse(
            sync_market_raw._all_csv_rows_match_trade_date(mixed, "20260825")
        )

    def test_strict_sse_calendar_returns_exact_21_open_session_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            calendar = Path(temp) / "trade_cal_sse.csv"
            self._write_calendar(calendar, self._calendar_rows())
            self.assertEqual(
                sync_market_raw._strict_sse_context_window(
                    calendar,
                    "20260825",
                    21,
                ),
                list(self.CONTEXT_DATES),
            )

    def test_strict_sse_calendar_corruption_fails_closed(self) -> None:
        cases: list[tuple[str, list[dict[str, str]], str]] = []

        duplicate = self._calendar_rows()
        duplicate.insert(5, dict(duplicate[4]))
        cases.append(("duplicate", duplicate, "row is invalid"))

        closed_d = self._calendar_rows()
        closed_d[-1]["is_open"] = "0"
        cases.append(("closed_d", closed_d, "not an open SSE session"))

        broken_open_chain = self._calendar_rows()
        broken_open_chain[-1]["pretrade_date"] = "20260820"
        cases.append(("broken_open_chain", broken_open_chain, "pretrade chain"))

        broken_closed_chain = self._calendar_rows()
        broken_closed_chain.insert(
            4,
            {
                "exchange": "SSE",
                "cal_date": "20260801",
                "is_open": "0",
                "pretrade_date": "20260730",
            },
        )
        cases.append(("broken_closed_chain", broken_closed_chain, "pretrade chain"))

        with tempfile.TemporaryDirectory() as temp:
            for name, rows, message in cases:
                with self.subTest(name=name):
                    calendar = Path(temp) / f"{name}.csv"
                    self._write_calendar(calendar, rows)
                    with self.assertRaisesRegex(RuntimeError, message):
                        sync_market_raw._strict_sse_context_window(
                            calendar,
                            "20260825",
                            21,
                        )

    def test_context_orchestration_spawns_21_exact_dated_generations(self) -> None:
        generated: set[str] = set()
        validation_calls: list[tuple[str, str, bool]] = []
        commands: list[list[str]] = []

        def valid(
            trade_date: str,
            resolved_commit: str,
            *,
            require_latest: bool,
        ) -> bool:
            validation_calls.append(
                (trade_date, resolved_commit, require_latest)
            )
            self.assertEqual(resolved_commit, self.COMMIT)
            self.assertEqual(require_latest, trade_date == "20260825")
            return trade_date in generated

        def run(command: list[str], *, check: bool) -> mock.Mock:
            self.assertFalse(check)
            commands.append(list(command))
            date_index = command.index("--trade-date") + 1
            generated.add(command[date_index])
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as temp:
            calendar = Path(temp) / "trade_cal_sse.csv"
            self._write_calendar(calendar, self._calendar_rows())
            with mock.patch.object(
                sync_market_raw,
                "_strict_generation_valid",
                side_effect=valid,
            ), mock.patch.object(
                sync_market_raw.subprocess,
                "run",
                side_effect=run,
            ):
                self.assertEqual(
                    sync_market_raw._sync_strict_sse_context(
                        trade_date="20260825",
                        resolved_commit=self.COMMIT,
                        calendar_path=calendar,
                        open_sessions=21,
                    ),
                    0,
                )

        self.assertEqual(len(commands), 21)
        self.assertEqual(generated, set(self.CONTEXT_DATES))
        self.assertEqual(len(validation_calls), 42)
        for index, command in enumerate(commands):
            self.assertEqual(command[0], sys.executable)
            self.assertIn("--strict-dated-source", command)
            self.assertEqual(
                command[command.index("--trade-date") + 1],
                self.CONTEXT_DATES[index],
            )
            if index < 20:
                self.assertIn("--dated-only", command)
            else:
                self.assertNotIn("--dated-only", command)

    def test_strict_generation_validator_binds_hash_path_commit_and_latest(self) -> None:
        specs = [
            sync_market_raw.SourceSpec(
                "daily",
                "daily.csv",
                required=True,
                date_scoped=True,
            ),
            sync_market_raw.SourceSpec(
                "stock_basic",
                "stock_basic.csv",
                required=True,
                date_scoped=False,
            ),
        ]
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            sync_market_raw,
            "SOURCE_SPECS",
            specs,
        ):
            old = Path.cwd()
            os.chdir(temp)
            try:
                prior_meta = self._install_strict_generation(
                    trade_date="20260824",
                    commit=self.COMMIT,
                    specs=specs,
                    require_latest=False,
                )
                final_meta = self._install_strict_generation(
                    trade_date="20260825",
                    commit=self.COMMIT,
                    specs=specs,
                    require_latest=True,
                )
                self.assertTrue(
                    sync_market_raw._strict_generation_valid(
                        "20260824",
                        self.COMMIT,
                        require_latest=False,
                    )
                )
                self.assertTrue(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )
                self.assertFalse(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        "c" * 40,
                        require_latest=True,
                    )
                )

                dated_meta_path = sync_market_raw._build_meta_dated_path(
                    "20260825"
                )
                latest_meta_path = sync_market_raw._build_meta_latest_path()
                missing_mode_meta = dict(final_meta)
                missing_mode_meta.pop("dated_only")
                missing_mode_bytes = (
                    json.dumps(missing_mode_meta, ensure_ascii=False, indent=2)
                    + "\n"
                ).encode("utf-8")
                dated_meta_path.write_bytes(missing_mode_bytes)
                latest_meta_path.write_bytes(missing_mode_bytes)
                self.assertFalse(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )
                restored_meta_bytes = (
                    json.dumps(final_meta, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
                dated_meta_path.write_bytes(restored_meta_bytes)
                latest_meta_path.write_bytes(restored_meta_bytes)

                dated_daily = sync_market_raw._build_dated_path(
                    "daily.csv",
                    "20260825",
                )
                original_daily = dated_daily.read_bytes()
                dated_daily.write_bytes(original_daily + b"tampered")
                self.assertFalse(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )
                dated_daily.write_bytes(original_daily)

                final_meta["files"][0]["dated_path"] = "wrong/daily.csv"
                wrong_path_bytes = (
                    json.dumps(final_meta, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
                dated_meta_path.write_bytes(wrong_path_bytes)
                latest_meta_path.write_bytes(wrong_path_bytes)
                self.assertFalse(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )
                final_meta["files"][0]["dated_path"] = str(dated_daily)

                restored_meta_bytes = (
                    json.dumps(final_meta, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
                dated_meta_path.write_bytes(restored_meta_bytes)
                latest_meta_path.write_bytes(restored_meta_bytes)
                latest_daily = sync_market_raw._build_latest_path("daily.csv")
                latest_original = latest_daily.read_bytes()
                latest_daily.write_bytes(latest_original + b"stale")
                self.assertFalse(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )
                latest_daily.write_bytes(latest_original)
                self.assertTrue(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )

                latest_meta_path.write_bytes(restored_meta_bytes + b" ")
                self.assertFalse(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )
                self.assertTrue(prior_meta["dated_only"])
            finally:
                os.chdir(old)

    def test_strict_dated_only_preserves_latest_until_final_d_generation(self) -> None:
        spec = sync_market_raw.SourceSpec(
            "daily",
            "daily.csv",
            required=True,
            date_scoped=True,
        )

        def load_meta(**kwargs: object) -> tuple[dict[str, str], str]:
            trade_date = str(kwargs["trade_date"])
            self.assertTrue(kwargs["strict_dated"])
            self.assertEqual(kwargs["branch"], self.COMMIT)
            return (
                {"trade_date": trade_date},
                "https://raw.githubusercontent.com/njedu2023-prog/"
                f"a-share-top3-data/{self.COMMIT}/data/raw/{trade_date[:4]}/"
                f"{trade_date}/_meta.json",
            )

        def fetch(
            urls: list[str],
            *,
            expected_trade_date: str,
            require_all_rows_match: bool,
            **_kwargs: object,
        ) -> tuple[str, str, int, str, str]:
            expected_url = (
                "https://raw.githubusercontent.com/njedu2023-prog/"
                f"a-share-top3-data/{self.COMMIT}/data/raw/"
                f"{expected_trade_date[:4]}/{expected_trade_date}/daily.csv"
            )
            self.assertEqual(urls, [expected_url])
            self.assertTrue(require_all_rows_match)
            body = (
                "trade_date,ts_code,close\n"
                f"{expected_trade_date},600000.SH,10.0\n"
            )
            return expected_url, body, 200, expected_trade_date, ""

        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {
                "MARKET_RAW_COMMIT": self.COMMIT,
                "MARKET_RAW_BRANCH": "main",
                "MARKET_RAW_OWNER": "njedu2023-prog",
                "MARKET_RAW_REPO": "a-share-top3-data",
            },
            clear=True,
        ), mock.patch.object(
            sync_market_raw,
            "SOURCE_SPECS",
            [spec],
        ), mock.patch.object(
            sync_market_raw,
            "_load_upstream_meta",
            side_effect=load_meta,
        ), mock.patch.object(
            sync_market_raw,
            "_fetch_first_matching_trade_date",
            side_effect=fetch,
        ):
            old = Path.cwd()
            os.chdir(temp)
            try:
                latest_daily = sync_market_raw._build_latest_path("daily.csv")
                latest_meta = sync_market_raw._build_meta_latest_path()
                latest_daily.parent.mkdir(parents=True, exist_ok=True)
                latest_daily.write_bytes(b"previous-final")
                latest_meta.write_bytes(b"previous-meta")

                with mock.patch.object(
                    sys,
                    "argv",
                    [
                        str(SCRIPT_PATH),
                        "--trade-date",
                        "20260824",
                        "--strict-dated-source",
                        "--dated-only",
                    ],
                ):
                    self.assertEqual(sync_market_raw.main(), 0)
                self.assertEqual(latest_daily.read_bytes(), b"previous-final")
                self.assertEqual(latest_meta.read_bytes(), b"previous-meta")
                self.assertTrue(
                    sync_market_raw._strict_generation_valid(
                        "20260824",
                        self.COMMIT,
                        require_latest=False,
                    )
                )

                with mock.patch.object(
                    sys,
                    "argv",
                    [
                        str(SCRIPT_PATH),
                        "--trade-date",
                        "20260825",
                        "--strict-dated-source",
                    ],
                ):
                    self.assertEqual(sync_market_raw.main(), 0)
                self.assertIn(b"20260825", latest_daily.read_bytes())
                self.assertTrue(
                    sync_market_raw._strict_generation_valid(
                        "20260825",
                        self.COMMIT,
                        require_latest=True,
                    )
                )
            finally:
                os.chdir(old)

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
