from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.run_v2 import _apply_ev_upgrade_v1  # noqa: E402
from scripts.backfill_decision_v11_history import (  # noqa: E402
    _completed_open_dates,
    _latest_target_dates,
    _retry_frame,
    _write_csv as write_backfill_csv,
)
from scripts.validate_backfill_artifacts import (  # noqa: E402
    BackfillArtifactError,
    EXPECTED_HISTORY_COLUMNS,
    HISTORY_NULLABLE_NUMERIC_COLUMNS,
    HISTORY_TEXT_COLUMNS,
    _covered_signal_dates,
    _date_list,
    _native_int,
    _read_json,
    _sha256_frame as backfill_frame_sha256,
    validate_backfill_artifacts,
)
from scripts.build_eret_truth import infer_eret_label  # noqa: E402
from scripts.build_fill_truth import infer_fill_label  # noqa: E402
from scripts.resolve_sample_maturity import (  # noqa: E402
    SampleMaturityRow,
    resolve_sample_maturity_rows,
    resolve_trade_calendar,
    write_csv as write_sample_maturity_csv,
)
from scripts.sync_tushare_minute import _collect_codes  # noqa: E402
from scripts.validate_io_contract import (  # noqa: E402
    _allows_unpromoted_no_trade,
    _strict_picked_count,
    _validate_learning_acceptance,
)
from top10decision.data.tushare_minute import (  # noqa: E402
    MINUTE_FIELDS,
    TushareClient,
    TushareResponseSchemaError,
    opening_auction_price_from_snapshot,
)
from top10decision.rt_min_contract import RTMinContractError  # noqa: E402
from top10decision.auction_v3.config import (  # noqa: E402
    TARGET_HISTORY_DATES,
    TARGET_INDEPENDENT_OOS_DATES,
    WALKFORWARD_WARMUP_DATES,
)
from top10decision.decision.action_plan import (  # noqa: E402
    _canonical_decimals,
    _selector_prediction_domain,
    _strict_all_true_column,
    build_action_plan,
)
from top10decision.decision.canonical_fingerprint import (  # noqa: E402
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_mapping_sha256,
    canonical_policy_fingerprint,
    compose_artifact_fingerprint,
)
from top10decision.decision.contracts import (  # noqa: E402
    ACTUAL_ORDER_FILL_OBSERVED_COLUMN,
    ACTUAL_ORDER_FILL_TARGET_COLUMN,
    EXIT_LATEST_TIME,
    EXIT_POLICY_VERSION,
    EXIT_STOP_LOSS_PCT,
    EXIT_TAKE_PROFIT_PCT,
    HISTORY_CONTRACT_VERSION,
    PFILL_EXECUTION_CONTRACT,
    PREOPEN_AUCTION_GATE_AUDIT,
    PUBLIC_MARKET_BUYABLE_TARGET_COLUMN,
)
from top10decision.decision.eligibility import filter_standard_limit_universe  # noqa: E402
from top10decision.decision.exit_policy import simulate_tplus1_exit  # noqa: E402
from top10decision.decision.observation import rank_observation_rows  # noqa: E402
from top10decision.writers import io_contract  # noqa: E402


class DecisionCalendarContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.original_calendar_path = io_contract.TRADE_CALENDAR_PATH
        io_contract.TRADE_CALENDAR_PATH = Path(self.temp.name) / "missing_calendar.csv"
        io_contract._load_exchange_calendar.cache_clear()

    def tearDown(self) -> None:
        io_contract.TRADE_CALENDAR_PATH = self.original_calendar_path
        io_contract._load_exchange_calendar.cache_clear()
        self.temp.cleanup()

    def test_official_2026_holidays_are_skipped(self) -> None:
        self.assertEqual(io_contract.choose_exec_date("20260430", "20260501"), "20260506")
        self.assertEqual(io_contract.choose_exit_date("20260506"), "20260507")
        self.assertEqual(io_contract.choose_exec_date("20260618", "20260619"), "20260622")
        self.assertEqual(io_contract.choose_exit_date("20260622"), "20260623")

    def test_synced_calendar_is_authoritative_and_fails_closed(self) -> None:
        calendar_path = Path(self.temp.name) / "trade_cal_sse.csv"
        pd.DataFrame(
            [
                {"exchange": "SSE", "cal_date": "20260720", "is_open": 1},
                {"exchange": "SSE", "cal_date": "20260721", "is_open": 0},
                {"exchange": "SSE", "cal_date": "20260722", "is_open": 1},
            ]
        ).to_csv(calendar_path, index=False)
        io_contract.TRADE_CALENDAR_PATH = calendar_path
        io_contract._load_exchange_calendar.cache_clear()

        self.assertEqual(io_contract.choose_exec_date("20260720", "20260721"), "20260722")
        with self.assertRaises(RuntimeError):
            io_contract.is_a_share_trading_day("20260723")

    def test_maturity_uses_only_explicit_exchange_calendar(self) -> None:
        calendar_path = Path(self.temp.name) / "strict_calendar.csv"
        days = [
            ("20260430", 1),
            ("20260501", 0),
            ("20260502", 0),
            ("20260503", 0),
            ("20260504", 0),
            ("20260505", 0),
            ("20260506", 1),
            ("20260507", 1),
        ]
        pd.DataFrame(
            [{"exchange": "SSE", "cal_date": day, "is_open": flag} for day, flag in days]
        ).to_csv(calendar_path, index=False)

        calendar = resolve_trade_calendar(
            raw_trade_dates=["20260430", "20260502", "20260506", "20260507"],
            candidate_trade_dates=["20260430"],
            current_run_date="20260507",
            trade_calendar_file=calendar_path,
        )
        rows = resolve_sample_maturity_rows(
            current_run_date="20260507",
            all_trade_dates_from_raw=["20260430", "20260502", "20260506", "20260507"],
            candidate_trade_dates=["20260430"],
            trade_calendar_dates=calendar,
        )
        self.assertEqual(rows[0].exec_date, "20260506")
        self.assertEqual(rows[0].target_date, "20260507")
        self.assertEqual(rows[0].FULLY_READY, 1)
        self.assertNotIn("20260502", calendar)

    def test_maturity_calendar_gap_fails_closed(self) -> None:
        calendar_path = Path(self.temp.name) / "calendar_with_gap.csv"
        pd.DataFrame(
            [
                {"cal_date": "20260430", "is_open": 1},
                {"cal_date": "20260501", "is_open": 0},
                {"cal_date": "20260502", "is_open": 0},
                {"cal_date": "20260503", "is_open": 0},
                # 20260504 intentionally absent.
                {"cal_date": "20260505", "is_open": 0},
                {"cal_date": "20260506", "is_open": 1},
                {"cal_date": "20260507", "is_open": 1},
            ]
        ).to_csv(calendar_path, index=False)
        with self.assertRaisesRegex(RuntimeError, "存在缺口"):
            resolve_trade_calendar(
                raw_trade_dates=["20260430", "20260506", "20260507"],
                candidate_trade_dates=["20260430"],
                current_run_date="20260507",
                trade_calendar_file=calendar_path,
            )

    def test_sample_maturity_csv_is_lf_and_newline_parse_equivalent(self) -> None:
        rows = [
            SampleMaturityRow(
                trade_date="20260814",
                exec_date="20260817",
                target_date="20260818",
                sample_maturity="FULLY_READY",
                PFILL_READY=1,
                ERET_READY=1,
                FULLY_READY=1,
            )
        ]
        output = Path(self.temp.name) / "sample_maturity_latest.csv"

        write_sample_maturity_csv(rows, output)

        payload = output.read_bytes()
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", payload)
        self.assertEqual(payload.count(b"\n"), len(rows) + 1)

        def parse(value: bytes) -> list[dict[str, str]]:
            text = value.decode("utf-8-sig")
            return list(csv.DictReader(io.StringIO(text, newline="")))

        self.assertEqual(parse(payload), parse(payload.replace(b"\n", b"\r\n")))

    def test_two_year_oos_backfill_keeps_training_warmup(self) -> None:
        open_dates = [
            value.strftime("%Y%m%d")
            for value in pd.bdate_range("2023-01-02", periods=750)
        ]
        target_window, missing = _latest_target_dates(
            open_dates,
            {open_dates[102], open_dates[103]},
            max_missing_dates=3,
        )

        self.assertEqual(TARGET_INDEPENDENT_OOS_DATES, 500)
        self.assertEqual(WALKFORWARD_WARMUP_DATES, 200)
        self.assertEqual(TARGET_HISTORY_DATES, 700)
        self.assertEqual(len(target_window), 700)
        self.assertEqual(target_window[0], open_dates[42])
        self.assertEqual(target_window[-1], open_dates[-9])
        self.assertEqual(missing, open_dates[42:45])

    def test_intraday_backfill_excludes_unfinished_current_session(self) -> None:
        dates = ["20260724", "20260727", "20260728"]
        before_close = datetime(
            2026,
            7,
            28,
            13,
            30,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        after_ready = datetime(
            2026,
            7,
            28,
            21,
            15,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )

        self.assertEqual(
            _completed_open_dates(dates, now=before_close),
            dates[:-1],
        )
        self.assertEqual(
            _completed_open_dates(dates, now=after_ready),
            dates,
        )

    def test_backfill_retries_transient_empty_required_endpoint(self) -> None:
        responses = [
            pd.DataFrame(),
            pd.DataFrame([{"ts_code": "600000.SH"}]),
        ]

        with mock.patch(
            "scripts.backfill_decision_v11_history.time.sleep"
        ) as sleep:
            result = _retry_frame(
                lambda: responses.pop(0),
                label="daily:20260727",
                required=True,
            )

        self.assertEqual(result["ts_code"].tolist(), ["600000.SH"])
        sleep.assert_called_once_with(2.0)

    def _write_backfill_validation_fixture(self, root: Path) -> tuple[Path, Path]:
        history_root = root / "data/auction_v3/history" / EXIT_POLICY_VERSION
        history_root.mkdir(parents=True)
        requested_dates = [
            value.strftime("%Y%m%d")
            for value in pd.date_range("2020-01-01", "2026-08-21", freq="D")
        ]
        target_end = datetime.strptime("20260805", "%Y%m%d")
        target_window = [
            (target_end - timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(TARGET_HISTORY_DATES - 1, -1, -1)
        ]
        maturity_dates = [
            value.strftime("%Y%m%d")
            for value in pd.date_range("2026-08-06", "2026-08-13", freq="D")
        ]
        open_dates = set(target_window + maturity_dates + ["20260821"])
        calendar_rows: list[dict[str, object]] = []
        previous_open = "20191231"
        for date in requested_dates:
            is_open = int(date in open_dates)
            calendar_rows.append(
                {
                    "exchange": "SSE",
                    "cal_date": date,
                    "is_open": is_open,
                    "pretrade_date": previous_open,
                }
            )
            if is_open:
                previous_open = date
        calendar = pd.DataFrame(calendar_rows)
        calendar_path = root / "data/market/trade_cal_sse.csv"
        write_backfill_csv(calendar, calendar_path)
        calendar_raw = calendar_path.read_bytes()
        coverage = pd.DataFrame(
            [
                {
                    **{column: "" for column in EXPECTED_HISTORY_COLUMNS},
                    "signal_date": signal_date,
                    "ts_code": "600000.SH",
                }
                for signal_date in target_window[:-1]
            ],
            columns=EXPECTED_HISTORY_COLUMNS,
        )
        coverage_path = history_root / (
            f"training_{target_window[0]}_{target_window[-2]}.csv"
        )
        write_backfill_csv(coverage, coverage_path)
        output = history_root / "training_20260805_20260805.csv"
        def history_row(rank: int, code: str) -> dict[str, object]:
            row: dict[str, object] = {}
            for column in EXPECTED_HISTORY_COLUMNS:
                if column in HISTORY_TEXT_COLUMNS or column in HISTORY_NULLABLE_NUMERIC_COLUMNS:
                    row[column] = ""
                else:
                    row[column] = 0.0
            row.update(
                {
                    "signal_date": "20260805",
                    "buy_date": "20260806",
                    "target_exit_date": "20260807",
                    "actual_exit_date": "20260807",
                    "exit_delay_days": 0,
                    "ts_code": code,
                    "name": f"fixture-{rank}",
                    "industry": "fixture-industry",
                    "stage": "1→2",
                    "source_rank": rank,
                    "d_close": 10.0,
                    "buy_open": 10.0,
                    "auction_vwap": 10.0,
                    "auction_amount": 100_000_000.0,
                    "auction_truth_source": "tushare_stk_auction_o",
                    "exit_open": 10.1,
                    "actual_buy_gap": 0.0,
                    "gross_return": 0.01,
                    "net_return": 0.0055,
                    "profit_hit": 1,
                    "big_loss_hit": 0,
                    "continuation_limit_up_hit": 0,
                    "exit_on_time": 1,
                    "market_fill": 1,
                    "public_market_buyable": 1,
                    "actual_order_fill_observed": 0,
                    "actual_order_fill": "",
                    "mechanism_limit_pct": 10.0,
                    "fill_reason": "market_proxy_buyable",
                    "exit_reason": "fixed_open_0930",
                    "exit_policy_version": EXIT_POLICY_VERSION,
                    "take_profit_pct": EXIT_TAKE_PROFIT_PCT,
                    "stop_loss_pct": EXIT_STOP_LOSS_PCT,
                    "latest_exit_time": EXIT_LATEST_TIME,
                    "history_source": "tushare_compact_backfill",
                    "history_contract_version": HISTORY_CONTRACT_VERSION,
                    "limit_times": 1.0,
                    "d_return": 0.1,
                    "limit_ratio": 0.1,
                    "proposed_gap": 0.0,
                    "market_sentiment_regime_code": "NEUTRAL",
                    "market_sentiment_regime_label": "震荡",
                    "path_label_code": "STABLE_STRONG",
                    "path_label": "持续强势",
                    "path_explanation": "fixture",
                    "stage_pool_size": 2.0,
                    "focus_pool_size": 0.0,
                    "market_max_limit_times": 1.0,
                    "same_industry_stage_count": 2.0,
                    "stage_pool_share": 1.0,
                }
            )
            self.assertEqual(tuple(row), EXPECTED_HISTORY_COLUMNS)
            return row

        history = pd.DataFrame(
            [history_row(1, "600000.SH"), history_row(2, "600001.SH")],
            columns=EXPECTED_HISTORY_COLUMNS,
        )
        history.to_csv(output, index=False, encoding="utf-8-sig", lineterminator="\n")
        raw = output.read_bytes()
        canonical_sha = backfill_frame_sha256(
            pd.read_csv(output, encoding="utf-8-sig", low_memory=False)
        )
        manifest = {
            "schema_version": "decision_v11_history_manifest_v2",
            "generated_at_utc": "2026-08-21T00:00:00+00:00",
            "evaluated_at_utc": "2026-08-21T00:00:00+00:00",
            "calendar_source": "tushare:trade_cal:SSE",
            "strict_calendar": True,
            "calendar_file": "data/market/trade_cal_sse.csv",
            "calendar_bytes_sha256": hashlib.sha256(calendar_raw).hexdigest(),
            "calendar_bytes": len(calendar_raw),
            "calendar_open_dates": TARGET_HISTORY_DATES + 8,
            "requested_start_date": "20200101",
            "requested_end_date": "20260821",
            "target_signal_dates": ["20260805"],
            "target_window_start": target_window[0],
            "target_window_end": "20260805",
            "target_window_open_sessions": TARGET_HISTORY_DATES,
            "target_window_signal_dates": target_window,
            "target_history_sessions": TARGET_HISTORY_DATES,
            "walkforward_warmup_sessions": WALKFORWARD_WARMUP_DATES,
            "max_missing_dates": 1,
            "target_signal_date_count": 1,
            "produced_signal_dates": 1,
            "produced_rows": 2,
            "official_auction_truth_rows": 2,
            "auction_truth_coverage": 1.0,
            "total_compact_signal_dates": TARGET_HISTORY_DATES,
            "target_independent_dates": TARGET_INDEPENDENT_OOS_DATES,
            "exit_policy": {
                "version": EXIT_POLICY_VERSION,
                "take_profit_pct": EXIT_TAKE_PROFIT_PCT,
                "stop_loss_pct": EXIT_STOP_LOSS_PCT,
                "latest_exit_time": EXIT_LATEST_TIME,
                "requires_intraday_truth": False,
            },
            "output_file": (
                "data/auction_v3/history/"
                f"{EXIT_POLICY_VERSION}/training_20260805_20260805.csv"
            ),
            "output_sha256": canonical_sha,
            "output_canonical_sha256": canonical_sha,
            "output_bytes_sha256": hashlib.sha256(raw).hexdigest(),
            "output_bytes": len(raw),
            "endpoint_rows": {
                "daily": 2,
                "stk_limit": 2,
                "daily_basic": 2,
                "limit_list_d": 2,
                "stk_auction_o": 2,
            },
            "failures": [],
            "credential_persisted": False,
        }
        (history_root / "manifest_latest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        receipt = root / "backfill-receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": "decision_v11_backfill_receipt_v1",
                    "status": "produced",
                    "requested_start_date": "20200101",
                    "requested_end_date": "20260821",
                    "max_missing_dates": 1,
                    "credential_persisted": False,
                    "manifest": manifest,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return receipt, output

    @staticmethod
    def _persist_backfill_receipt(
        root: Path,
        receipt: Path,
        payload: dict[str, object],
    ) -> None:
        history_root = root / "data/auction_v3/history" / EXIT_POLICY_VERSION
        (history_root / "manifest_latest.json").write_text(
            json.dumps(payload["manifest"], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        receipt.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _resign_backfill_output(
        self,
        root: Path,
        receipt: Path,
        output: Path,
        mutate: object,
    ) -> None:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        exact = pd.read_csv(
            output,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
        mutate(exact)
        write_backfill_csv(exact, output)
        raw = output.read_bytes()
        canonical = backfill_frame_sha256(
            pd.read_csv(output, encoding="utf-8-sig", low_memory=False)
        )
        manifest = payload["manifest"]
        manifest["output_bytes"] = len(raw)
        manifest["output_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
        manifest["output_sha256"] = canonical
        manifest["output_canonical_sha256"] = canonical
        self._persist_backfill_receipt(root, receipt, payload)

    def test_owner_scoped_backfill_receipt_binds_raw_and_canonical_history(self) -> None:
        root = Path(self.temp.name) / "backfill-validation"
        receipt, output = self._write_backfill_validation_fixture(root)

        result = validate_backfill_artifacts(
            root,
            receipt,
            start_date="20200101",
            end_date="20260821",
            max_missing_dates=1,
        )

        self.assertTrue(result["validated"])
        self.assertEqual(result["status"], "produced")
        self.assertEqual(result["produced_rows"], 2)
        frame = pd.read_csv(output, encoding="utf-8-sig")
        self.assertNotIn("backfill_generated_at_utc", frame.columns)

        output.write_bytes(output.read_bytes() + b"\n")
        with self.assertRaises(BackfillArtifactError):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )

    def test_backfill_csv_is_byte_deterministic_utf8_bom_lf(self) -> None:
        root = Path(self.temp.name) / "backfill-deterministic"
        first = root / "first.csv"
        second = root / "second.csv"
        frame = pd.DataFrame(
            [
                {"signal_date": "20260805", "ts_code": "600000.SH", "value": 1.25},
                {"signal_date": "20260805", "ts_code": "600001.SH", "value": 2.5},
            ]
        )
        write_backfill_csv(frame, first)
        write_backfill_csv(frame.copy(), second)
        payload = first.read_bytes()
        self.assertEqual(payload, second.read_bytes())
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", payload)
        self.assertNotIn(b"backfill_generated_at_utc", payload)

    def test_owner_scoped_backfill_rejects_duplicate_json_and_endpoint_failure(self) -> None:
        root = Path(self.temp.name) / "backfill-negative"
        receipt, _output = self._write_backfill_validation_fixture(root)
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["manifest"]["failures"] = [
            {"trade_date": "20260805", "endpoint": "limit_list_d", "reason": "TimeoutError"}
        ]
        (
            root
            / "data/auction_v3/history"
            / EXIT_POLICY_VERSION
            / "manifest_latest.json"
        ).write_text(
            json.dumps(payload["manifest"], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        receipt.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(BackfillArtifactError, "endpoint failures"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )

    def test_owner_scoped_backfill_rejects_resigned_type_and_policy_aliases(self) -> None:
        manifest_path_parts = (
            "data",
            "auction_v3",
            "history",
            EXIT_POLICY_VERSION,
            "manifest_latest.json",
        )

        def persist(root: Path, receipt: Path, payload: dict[str, object]) -> None:
            manifest = payload["manifest"]
            (root.joinpath(*manifest_path_parts)).write_text(
                json.dumps(manifest, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            receipt.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        for label, key, value, message in (
            ("invalid-time", "generated_at_utc", "2026-99-99T99:99:99+00:00", "real timestamp"),
            ("float-count", "target_history_sessions", float(TARGET_HISTORY_DATES), "native integer"),
            ("request-start", "requested_start_date", "19990101", "start date mismatch"),
            ("request-end", "requested_end_date", "20991231", "end date mismatch"),
            ("request-max", "max_missing_dates", 2, "max_missing_dates mismatch"),
        ):
            with self.subTest(label=label):
                root = Path(self.temp.name) / f"backfill-{label}"
                receipt, _output = self._write_backfill_validation_fixture(root)
                payload = json.loads(receipt.read_text(encoding="utf-8"))
                payload["manifest"][key] = value
                persist(root, receipt, payload)
                with self.assertRaisesRegex(BackfillArtifactError, message):
                    validate_backfill_artifacts(
                        root,
                        receipt,
                        start_date="20200101",
                        end_date="20260821",
                        max_missing_dates=1,
                    )

        for label, column, value, message in (
            ("date-alias", "signal_date", "20260805.0", "YYYYMMDD"),
            ("rank-fraction", "source_rank", "1.5", "must be an integer"),
            ("take-profit", "take_profit_pct", "0.123", "take-profit policy"),
        ):
            with self.subTest(label=label):
                root = Path(self.temp.name) / f"backfill-{label}"
                receipt, output = self._write_backfill_validation_fixture(root)
                payload = json.loads(receipt.read_text(encoding="utf-8"))
                exact = pd.read_csv(
                    output,
                    encoding="utf-8-sig",
                    dtype="string",
                    keep_default_na=False,
                )
                exact.loc[0, column] = value
                write_backfill_csv(exact, output)
                raw = output.read_bytes()
                canonical = backfill_frame_sha256(
                    pd.read_csv(output, encoding="utf-8-sig", low_memory=False)
                )
                manifest = payload["manifest"]
                manifest["output_bytes"] = len(raw)
                manifest["output_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
                manifest["output_sha256"] = canonical
                manifest["output_canonical_sha256"] = canonical
                persist(root, receipt, payload)
                with self.assertRaisesRegex(BackfillArtifactError, message):
                    validate_backfill_artifacts(
                        root,
                        receipt,
                        start_date="20200101",
                        end_date="20260821",
                        max_missing_dates=1,
                    )

    def test_owner_scoped_backfill_rejects_resigned_calendar_drift(self) -> None:
        root = Path(self.temp.name) / "backfill-calendar-drift"
        receipt, _output = self._write_backfill_validation_fixture(root)
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        calendar_path = root / "data/market/trade_cal_sse.csv"
        calendar = pd.read_csv(
            calendar_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
        calendar.loc[calendar["cal_date"].eq("20260805"), "is_open"] = "0"
        write_backfill_csv(calendar, calendar_path)
        raw = calendar_path.read_bytes()
        payload["manifest"]["calendar_bytes"] = len(raw)
        payload["manifest"]["calendar_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
        manifest_path = (
            root
            / "data/auction_v3/history"
            / EXIT_POLICY_VERSION
            / "manifest_latest.json"
        )
        manifest_path.write_text(
            json.dumps(payload["manifest"], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        receipt.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BackfillArtifactError, "SSE trade calendar"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )

    def test_owner_scoped_backfill_rejects_resigned_training_semantic_poison(self) -> None:
        cases = (
            (
                "nonfinite-return",
                lambda frame: frame.__setitem__(
                    "gross_return",
                    frame["gross_return"].where(frame.index != 0, "NaN"),
                ),
                "must be finite",
            ),
            (
                "market-fill-domain",
                lambda frame: frame.__setitem__(
                    "market_fill",
                    frame["market_fill"].where(frame.index != 0, "2"),
                ),
                "exact binary text",
            ),
            (
                "profit-label-alias",
                lambda frame: frame.__setitem__(
                    "profit_hit",
                    frame["profit_hit"].where(frame.index != 0, "true"),
                ),
                "numeric text|exact binary text",
            ),
            (
                "unexpected-column",
                lambda frame: frame.__setitem__("unreviewed_target", "1"),
                "exact schema mismatch",
            ),
            (
                "nullable-binary-domain",
                lambda frame: frame.__setitem__(
                    "is_hot_board",
                    frame["is_hot_board"].where(frame.index != 0, "2"),
                ),
                "must be binary",
            ),
            (
                "negative-count",
                lambda frame: frame.__setitem__(
                    "market_limit_up_count",
                    frame["market_limit_up_count"].where(frame.index != 0, "-1"),
                ),
                "must be nonnegative",
            ),
            (
                "ratio-out-of-range",
                lambda frame: frame.__setitem__(
                    "path_one_price_ratio",
                    frame["path_one_price_ratio"].where(frame.index != 0, "1.1"),
                ),
                r"within \[0, 1\]",
            ),
            (
                "negative-auction-amount",
                lambda frame: frame.__setitem__(
                    "auction_amount",
                    frame["auction_amount"].where(frame.index != 0, "-1"),
                ),
                "auction_amount must be nonnegative",
            ),
        )
        for label, mutate, message in cases:
            with self.subTest(label=label):
                root = Path(self.temp.name) / f"backfill-semantic-{label}"
                receipt, output = self._write_backfill_validation_fixture(root)
                self._resign_backfill_output(root, receipt, output, mutate)
                with self.assertRaisesRegex(BackfillArtifactError, message):
                    validate_backfill_artifacts(
                        root,
                        receipt,
                        start_date="20200101",
                        end_date="20260821",
                        max_missing_dates=1,
                    )

        root = Path(self.temp.name) / "backfill-semantic-filtered-rank-gap"
        receipt, output = self._write_backfill_validation_fixture(root)
        self._resign_backfill_output(
            root,
            receipt,
            output,
            lambda frame: frame.__setitem__(
                "path_one_price_ratio",
                frame["path_one_price_ratio"].where(frame.index != 0, "0.333333333333"),
            ),
        )
        self._resign_backfill_output(
            root,
            receipt,
            output,
            lambda frame: frame.__setitem__(
                "source_rank",
                frame["source_rank"].where(frame.index != 1, "3"),
            ),
        )
        result = validate_backfill_artifacts(
            root,
            receipt,
            start_date="20200101",
            end_date="20260821",
            max_missing_dates=1,
        )
        self.assertTrue(result["validated"])

        root = Path(self.temp.name) / "backfill-semantic-legitimate-missing-market"
        receipt, output = self._write_backfill_validation_fixture(root)
        producer_nullable = (
            "open_board_count",
            "limit_open_times",
            "limit_first_time_minutes",
            "limit_last_time_minutes",
            "limit_fd_amount_log",
            "limit_seal_to_amount",
            "limit_seal_to_float_mv",
            "d_turnover_rate",
            "d_volume_ratio",
            "d_float_mv_log",
            "d_amount_percentile",
            "order_to_d_amount",
            "order_to_float_mv",
            "relative_d_return",
            "market_failed_limit_up_rate",
            "market_reseal_rate",
            "market_prev_limit_up_mean_return",
            "market_prev_limit_up_positive_rate",
            "market_prev_limit_up_open_gap_mean",
            "market_focus_promotion_rate",
            "market_limit_up_industry_concentration",
            "market_limit_up_amount_top3_share",
            "market_amount_ratio_5d",
            "market_max_streak",
            "market_sentiment_delta",
            "market_sentiment_acceleration",
            "market_sentiment_promotion_score",
            "market_sentiment_profit_effect_score",
            "market_sentiment_liquidity_score",
            "path_strength_latest",
            "path_one_price_ratio",
        )

        def make_legitimate_missing_market(frame: pd.DataFrame) -> None:
            frame.loc[0, list(producer_nullable)] = ""
            frame.loc[0, "auction_amount"] = "0"
            frame.loc[0, "market_fill"] = "0"
            frame.loc[0, "public_market_buyable"] = "0"
            frame.loc[0, "fill_reason"] = "auction_daily_open_conflict"

        self._resign_backfill_output(
            root,
            receipt,
            output,
            make_legitimate_missing_market,
        )
        result = validate_backfill_artifacts(
            root,
            receipt,
            start_date="20200101",
            end_date="20260821",
            max_missing_dates=1,
        )
        self.assertTrue(result["validated"])

    def test_owner_scoped_backfill_rejects_bare_cr_and_invalid_full_calendar(self) -> None:
        for label, target in (("history", "history"), ("calendar", "calendar")):
            with self.subTest(label=label):
                root = Path(self.temp.name) / f"backfill-bare-cr-{label}"
                receipt, output = self._write_backfill_validation_fixture(root)
                payload = json.loads(receipt.read_text(encoding="utf-8"))
                path = (
                    output
                    if target == "history"
                    else root / "data/market/trade_cal_sse.csv"
                )
                path.write_bytes(path.read_bytes().replace(b"\n", b"\r"))
                raw = path.read_bytes()
                if target == "history":
                    canonical = backfill_frame_sha256(
                        pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
                    )
                    payload["manifest"]["output_bytes"] = len(raw)
                    payload["manifest"]["output_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
                    payload["manifest"]["output_sha256"] = canonical
                    payload["manifest"]["output_canonical_sha256"] = canonical
                else:
                    payload["manifest"]["calendar_bytes"] = len(raw)
                    payload["manifest"]["calendar_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
                self._persist_backfill_receipt(root, receipt, payload)
                with self.assertRaisesRegex(BackfillArtifactError, "LF line endings"):
                    validate_backfill_artifacts(
                        root,
                        receipt,
                        start_date="20200101",
                        end_date="20260821",
                        max_missing_dates=1,
                    )

        root = Path(self.temp.name) / "backfill-invalid-calendar-date"
        receipt, _output = self._write_backfill_validation_fixture(root)
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        calendar_path = root / "data/market/trade_cal_sse.csv"
        calendar = pd.read_csv(
            calendar_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
        calendar.loc[len(calendar)] = ["SSE", "20991399", "1", "20260821"]
        write_backfill_csv(calendar, calendar_path)
        raw = calendar_path.read_bytes()
        payload["manifest"]["calendar_bytes"] = len(raw)
        payload["manifest"]["calendar_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
        self._persist_backfill_receipt(root, receipt, payload)
        with self.assertRaisesRegex(BackfillArtifactError, "real calendar date"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )

        receipt.write_text(
            '{"schema_version":"decision_v11_backfill_receipt_v1",'
            '"schema_version":"duplicate","status":"up_to_date"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BackfillArtifactError, "duplicate key"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )

    def test_owner_scoped_up_to_date_receipt_proves_target_window_coverage(self) -> None:
        root = Path(self.temp.name) / "backfill-up-to-date"
        _receipt, _output = self._write_backfill_validation_fixture(root)
        receipt = root / "up-to-date-receipt.json"
        payload = {
            "schema_version": "decision_v11_backfill_receipt_v1",
            "status": "up_to_date",
            "requested_start_date": "20200101",
            "requested_end_date": "20260821",
            "max_missing_dates": 1,
            "evaluated_at_utc": "2026-08-21T00:00:00+00:00",
            "calendar_file": "data/market/trade_cal_sse.csv",
            "calendar_bytes_sha256": json.loads(
                _receipt.read_text(encoding="utf-8")
            )["manifest"]["calendar_bytes_sha256"],
            "calendar_bytes": json.loads(
                _receipt.read_text(encoding="utf-8")
            )["manifest"]["calendar_bytes"],
            "calendar_open_dates": TARGET_HISTORY_DATES + 8,
            "covered_signal_dates": TARGET_HISTORY_DATES,
            "target_window_signal_dates": json.loads(
                _receipt.read_text(encoding="utf-8")
            )["manifest"]["target_window_signal_dates"],
            "missing_signal_dates": [],
            "credential_persisted": False,
        }
        receipt.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = validate_backfill_artifacts(
            root,
            receipt,
            start_date="20200101",
            end_date="20260821",
            max_missing_dates=1,
        )
        self.assertEqual(result["status"], "up_to_date")

        window = payload["target_window_signal_dates"]
        coverage_path = (
            root
            / "data/auction_v3/history"
            / EXIT_POLICY_VERSION
            / f"training_{window[0]}_{window[-2]}.csv"
        )
        original_coverage = coverage_path.read_bytes()
        coverage = pd.read_csv(
            coverage_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
        coverage.loc[0, "signal_date"] = f"{coverage.loc[0, 'signal_date']}.0"
        write_backfill_csv(coverage, coverage_path)
        with self.assertRaisesRegex(BackfillArtifactError, "YYYYMMDD"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )
        coverage_path.write_bytes(original_coverage)

        write_backfill_csv(
            pd.DataFrame({"signal_date": window[:-1]}),
            coverage_path,
        )
        with self.assertRaisesRegex(BackfillArtifactError, "schema is not reviewed"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )
        coverage_path.write_bytes(original_coverage)

        coverage = pd.read_csv(
            coverage_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
        coverage.loc[0, "signal_date"] = window[-1]
        write_backfill_csv(coverage, coverage_path)
        with self.assertRaisesRegex(BackfillArtifactError, "outside its filename range"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )
        coverage_path.write_bytes(original_coverage)

        def one_coverage_row(signal_date: str, code: str) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        **{column: "" for column in EXPECTED_HISTORY_COLUMNS},
                        "signal_date": signal_date,
                        "ts_code": code,
                    }
                ],
                columns=EXPECTED_HISTORY_COLUMNS,
            )

        duplicate_path = coverage_path.parent / (
            f"training_{window[0]}_{window[0]}_part001.csv"
        )
        write_backfill_csv(
            one_coverage_row(window[0], "600000.SH"),
            duplicate_path,
        )
        with self.assertRaisesRegex(BackfillArtifactError, "duplicate identity"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )
        duplicate_path.unlink()

        coverage = pd.read_csv(
            coverage_path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
        coverage.loc[0, ["signal_date", "ts_code"]] = [window[1], "600001.SH"]
        write_backfill_csv(coverage, coverage_path)
        with self.assertRaisesRegex(BackfillArtifactError, "filename differs"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )
        coverage_path.write_bytes(original_coverage)

        gap_first = coverage_path.parent / "training_20190101_20190103_part001.csv"
        gap_third = coverage_path.parent / "training_20190101_20190103_part003.csv"
        write_backfill_csv(one_coverage_row("20190101", "600010.SH"), gap_first)
        write_backfill_csv(one_coverage_row("20190103", "600011.SH"), gap_third)
        with self.assertRaisesRegex(BackfillArtifactError, "not contiguous"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )
        gap_first.unlink()
        gap_third.unlink()

        short_part = coverage_path.parent / "training_20190101_20190102_part001.csv"
        write_backfill_csv(one_coverage_row("20190101", "600012.SH"), short_part)
        with self.assertRaisesRegex(BackfillArtifactError, "partition group differs"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )
        short_part.unlink()

        checked_in_history_root = (
            ROOT / "data/auction_v3/history" / EXIT_POLICY_VERSION
        )
        checked_in_coverage = _covered_signal_dates(checked_in_history_root)
        checked_in_manifest = _read_json(
            checked_in_history_root / "manifest_latest.json",
            "checked-in history manifest",
        )
        checked_in_total = _native_int(
            checked_in_manifest.get("total_compact_signal_dates"),
            "checked-in total_compact_signal_dates",
            minimum=TARGET_HISTORY_DATES,
        )
        checked_in_targets = _date_list(
            checked_in_manifest.get("target_signal_dates"),
            "checked-in target_signal_dates",
            allow_empty=False,
        )
        self.assertEqual(len(checked_in_coverage), checked_in_total)
        self.assertTrue(set(checked_in_targets).issubset(checked_in_coverage))

        payload["target_window_signal_dates"] = payload[
            "target_window_signal_dates"
        ][1:]
        receipt.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(BackfillArtifactError, "verified SSE calendar"):
            validate_backfill_artifacts(
                root,
                receipt,
                start_date="20200101",
                end_date="20260821",
                max_missing_dates=1,
            )


class DecisionExecutionSemanticsContractTests(unittest.TestCase):
    def test_public_buyability_is_not_claimed_as_actual_order_fill(self) -> None:
        self.assertEqual(
            PUBLIC_MARKET_BUYABLE_TARGET_COLUMN,
            "y_public_market_buyable",
        )
        self.assertEqual(ACTUAL_ORDER_FILL_TARGET_COLUMN, "actual_order_fill")
        self.assertEqual(
            ACTUAL_ORDER_FILL_OBSERVED_COLUMN,
            "actual_order_fill_observed",
        )
        self.assertIn("public_market_fillability_proxy", PFILL_EXECUTION_CONTRACT)

    def test_preopen_microstructure_gate_fails_closed_without_snapshots(self) -> None:
        self.assertFalse(PREOPEN_AUCTION_GATE_AUDIT["enabled"])
        self.assertEqual(
            PREOPEN_AUCTION_GATE_AUDIT["decision_deadline"],
            "T 09:24:50 Asia/Shanghai",
        )
        missing = PREOPEN_AUCTION_GATE_AUDIT["required_missing_fields"]
        self.assertIn("indicative_match_price", missing)
        self.assertIn("order_imbalance", missing)
        self.assertIn("cancel_pressure_0920_092450", missing)


class DecisionStrictSemanticContractTests(unittest.TestCase):
    def test_unpromoted_v8_no_trade_accepts_failed_legacy_learning_gate(self) -> None:
        plan = {
            "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
            "formal_buy_count": 0,
            "guidance_only": True,
            "broker_connected": False,
            "order_execution": "manual_only",
            "candidates": [{"action": "SHADOW_ONLY"}, {"action": "REJECT"}],
            "model": {"promoted": False},
        }
        self.assertTrue(_allows_unpromoted_no_trade(plan, picked=0))

    def test_pending_model_is_never_a_missing_learning_exception(self) -> None:
        plan = {
            "status_code": "PENDING_AUCTION_MODEL",
            "formal_buy_count": 0,
            "guidance_only": True,
            "broker_connected": False,
            "order_execution": "manual_only",
            "candidates": [{"action": "PENDING"}, {"action": "SHADOW_ONLY"}],
            "model": {"promoted": False},
        }
        self.assertFalse(_allows_unpromoted_no_trade(plan, picked=0))
        self.assertFalse(_allows_unpromoted_no_trade(plan, picked=1))
        self.assertFalse(_allows_unpromoted_no_trade(plan, picked=-1))

    def test_strict_picked_rejects_non_json_integer_aliases(self) -> None:
        self.assertEqual(_strict_picked_count({"picked": 0}), 0)
        for alias in (False, 0.0, "0", float("nan"), None, -1):
            with self.subTest(alias=alias):
                with self.assertRaisesRegex(SystemExit, "2"):
                    _strict_picked_count({"picked": alias})

    def test_absent_learning_file_uses_only_the_strict_frozen_no_trade_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_path = root / "outputs/learning/learning_acceptance_latest.json"
            action_path = root / "outputs/decision/action_plan_latest.json"
            action_path.parent.mkdir(parents=True)
            action_path.write_text(
                json.dumps(
                    {
                        "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
                        "formal_buy_count": 0,
                        "guidance_only": True,
                        "broker_connected": False,
                        "order_execution": "manual_only",
                        "candidates": [{"action": "SHADOW_ONLY"}],
                        "model": {"promoted": False},
                    }
                ),
                encoding="utf-8",
            )
            expected_reason = (
                "model_rejected_by_learning_acceptance:acceptance_file_missing"
            )
            candidates = pd.DataFrame(
                {
                    "p_fill_model_loaded": ["False"],
                    "eret_model_loaded": ["False"],
                    "p_fill_model_acceptance_pass": ["False"],
                    "eret_model_acceptance_pass": ["False"],
                    "p_fill_degrade_reason": [expected_reason],
                    "eret_degrade_reason": [expected_reason],
                }
            )

            _validate_learning_acceptance(
                learning_path=learning_path,
                action_plan_path=action_path,
                picked=0,
                cand_text_df=candidates,
            )

            with self.assertRaisesRegex(SystemExit, "2"):
                _validate_learning_acceptance(
                    learning_path=learning_path,
                    action_plan_path=action_path,
                    picked=0,
                    cand_text_df=candidates.iloc[0:0],
                )

            for alias in (0, False, "false", "FALSE", "0", ""):
                unsafe_alias = candidates.copy().astype(object)
                unsafe_alias.loc[0, "p_fill_model_loaded"] = alias
                with self.subTest(alias=alias):
                    with self.assertRaisesRegex(SystemExit, "2"):
                        _validate_learning_acceptance(
                            learning_path=learning_path,
                            action_plan_path=action_path,
                            picked=0,
                            cand_text_df=unsafe_alias,
                        )

            for column in candidates.columns:
                unsafe = candidates.copy().astype(object)
                if column.endswith("reason"):
                    unsafe.loc[0, column] = "acceptance_file_missing"
                else:
                    unsafe.loc[0, column] = True
                with self.subTest(column=column, kind="mutation"):
                    with self.assertRaisesRegex(SystemExit, "2"):
                        _validate_learning_acceptance(
                            learning_path=learning_path,
                            action_plan_path=action_path,
                            picked=0,
                            cand_text_df=unsafe,
                        )
                missing_column = candidates.drop(columns=[column])
                with self.subTest(column=column, kind="missing"):
                    with self.assertRaisesRegex(SystemExit, "2"):
                        _validate_learning_acceptance(
                            learning_path=learning_path,
                            action_plan_path=action_path,
                            picked=0,
                            cand_text_df=missing_column,
                        )

            learning_path.parent.mkdir(parents=True)
            for payload in (
                "not-json",
                json.dumps({"overall_pass": False}),
                json.dumps({"overall_pass": 0}),
                json.dumps({"overall_pass": True}),
                json.dumps({"arbitrary": "stub"}),
            ):
                learning_path.write_text(payload, encoding="utf-8")
                with self.subTest(existing_acceptance=payload):
                    with self.assertRaisesRegex(SystemExit, "2"):
                        _validate_learning_acceptance(
                            learning_path=learning_path,
                            action_plan_path=action_path,
                            picked=0,
                            cand_text_df=candidates,
                        )

    def test_no_trade_exception_fails_closed_when_any_guard_is_missing(self) -> None:
        plan = {
            "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
            "formal_buy_count": 0,
            "guidance_only": True,
            "broker_connected": False,
            "order_execution": "manual_only",
            "candidates": [{"action": "SHADOW_ONLY"}],
            "model": {"promoted": False},
        }
        for path, unsafe in (
            (("status_code",), "PENDING_AUCTION_MODEL"),
            (("formal_buy_count",), 1),
            (("guidance_only",), False),
            (("broker_connected",), True),
            (("order_execution",), "broker_api"),
            (("model", "promoted"), True),
            (("candidates", 0, "action"), "PENDING"),
            (("candidates", 0, "action"), "BUY"),
        ):
            mutated = json.loads(json.dumps(plan))
            target = mutated
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = unsafe
            with self.subTest(path=path):
                self.assertFalse(_allows_unpromoted_no_trade(mutated, picked=0))
        self.assertFalse(_allows_unpromoted_no_trade(plan, picked=1))


class DecisionRealtimeMinuteContractTests(unittest.TestCase):
    @staticmethod
    def _row(
        *,
        code: str = "600000.SH",
        freq: str = "1MIN",
    ) -> list[object]:
        return [
            code,
            freq,
            "2026-08-20 10:40:00",
            10.0,
            10.1,
            10.2,
            9.9,
            1000,
            10000,
        ]

    def test_current_minute_requests_identity_and_frequency_before_persist(self) -> None:
        client = TushareClient(token="secret")
        response_fields = ["code", *MINUTE_FIELDS[1:]]
        with mock.patch.object(
            TushareClient,
            "_call_rows",
            autospec=True,
            return_value=(response_fields, [self._row()]),
        ) as call:
            frame = client.current_minute("600000.SH")

        _, api_name, params, fields = call.call_args.args
        self.assertEqual(api_name, "rt_min_daily")
        self.assertEqual(params, {"ts_code": "600000.SH", "freq": "1MIN"})
        self.assertEqual(tuple(fields), MINUTE_FIELDS)
        self.assertEqual(
            frame.columns.tolist(),
            ["ts_code", "time", "open", "close", "high", "low", "vol", "amount"],
        )
        self.assertEqual(frame["ts_code"].tolist(), ["600000.SH"])

    def test_current_minute_accepts_canonical_ts_code_header(self) -> None:
        client = TushareClient(token="secret")
        with mock.patch.object(
            TushareClient,
            "_call_rows",
            autospec=True,
            return_value=(list(MINUTE_FIELDS), [self._row()]),
        ):
            frame = client.current_minute("600000.SH")
        self.assertEqual(frame["time"].tolist(), ["2026-08-20 10:40:00"])

    def test_current_minute_empty_response_is_ordinary_no_data(self) -> None:
        client = TushareClient(token="secret")
        with mock.patch.object(
            TushareClient,
            "_call_rows",
            autospec=True,
            return_value=([], []),
        ):
            frame = client.current_minute("600000.SH")
        self.assertTrue(frame.empty)

    def test_current_minute_maps_response_shape_failure_to_hard_schema(self) -> None:
        client = TushareClient(token="secret")
        with mock.patch.object(
            TushareClient,
            "_call_rows",
            autospec=True,
            side_effect=TushareResponseSchemaError("invalid response shape"),
        ):
            with self.assertRaises(RTMinContractError) as caught:
                client.current_minute("600000.SH")
        self.assertEqual(caught.exception.reason, "schema")
        self.assertEqual(caught.exception.row_count, 0)

    def test_call_rows_rejects_malformed_response_objects(self) -> None:
        client = TushareClient(token="secret")
        payloads = [
            [],
            {"code": None, "data": {}},
            {"code": 0, "data": "not-an-object"},
            {"code": 0, "data": []},
            {"code": 0, "data": {"fields": "time", "items": []}},
            {"code": 0, "data": {"fields": {}, "items": []}},
            {"code": 0, "data": {"fields": [], "items": {}}},
            {"code": 0, "data": {"fields": [], "items": 0}},
            {"code": 0, "data": {"fields": ["time"], "items": [{}]}},
        ]
        for payload in payloads:
            response = mock.Mock()
            response.json.return_value = payload
            with self.subTest(payload=payload), mock.patch(
                "top10decision.data.tushare_minute.requests.post",
                return_value=response,
            ):
                with self.assertRaises(TushareResponseSchemaError):
                    client._call_rows("rt_min_daily", {}, MINUTE_FIELDS)
            response.raise_for_status.assert_called_once_with()

    def test_current_minute_rejects_wrong_or_mixed_identity_and_frequency(self) -> None:
        fields = list(MINUTE_FIELDS)
        cases = {
            "wrong_identity": (fields, [self._row(code="000001.SZ")], "identity"),
            "mixed_identity": (
                fields,
                [self._row(), self._row(code="000001.SZ")],
                "identity",
            ),
            "wrong_frequency": (fields, [self._row(freq="5MIN")], "frequency"),
            "mixed_frequency": (
                fields,
                [self._row(), self._row(freq="5MIN")],
                "frequency",
            ),
        }
        client = TushareClient(token="secret")
        for name, (response_fields, rows, reason) in cases.items():
            with self.subTest(name=name), mock.patch.object(
                TushareClient,
                "_call_rows",
                autospec=True,
                return_value=(response_fields, rows),
            ):
                with self.assertRaises(RTMinContractError) as caught:
                    client.current_minute("600000.SH")
                self.assertEqual(caught.exception.reason, reason)

    def test_current_minute_rejects_ambiguous_fields_and_bad_row_width(self) -> None:
        cases = {
            "ambiguous_identity": (
                ["ts_code", "code", *MINUTE_FIELDS[1:]],
                [["600000.SH", *self._row()]],
            ),
            "short_row": (list(MINUTE_FIELDS), [self._row()[:-1]]),
            "long_row": (list(MINUTE_FIELDS), [[*self._row(), "extra"]]),
            "empty_required_value": (
                list(MINUTE_FIELDS),
                [[*self._row()[:-1], None]],
            ),
        }
        client = TushareClient(token="secret")
        for name, (response_fields, rows) in cases.items():
            with self.subTest(name=name), mock.patch.object(
                TushareClient,
                "_call_rows",
                autospec=True,
                return_value=(response_fields, rows),
            ):
                with self.assertRaises(RTMinContractError) as caught:
                    client.current_minute("600000.SH")
                self.assertEqual(caught.exception.reason, "schema")


class DecisionExecutionTruthTests(unittest.TestCase):
    def test_exit_contract_is_fixed_tplus1_open_0930(self) -> None:
        self.assertIsNone(EXIT_TAKE_PROFIT_PCT)
        self.assertIsNone(EXIT_STOP_LOSS_PCT)
        self.assertEqual(EXIT_LATEST_TIME, "09:30")

    def test_historical_minute_normalizes_tushare_pro_bar(self) -> None:
        response = pd.DataFrame(
                [
                    {
                        "ts_code": "600000.SH",
                        "trade_time": "2026-07-22 11:00:00",
                        "open": 10.2,
                        "high": 10.3,
                        "low": 10.1,
                        "close": 10.25,
                        "vol": 100,
                        "amount": 1025,
                    },
                    {
                        "ts_code": "600000.SH",
                        "trade_time": "2026-07-22 09:30:00",
                        "open": 10.0,
                        "high": 10.1,
                        "low": 9.9,
                        "close": 10.05,
                        "vol": 200,
                        "amount": 2010,
                    },
                ]
            )
        client = TushareClient(token="secret")
        with mock.patch.object(
            TushareClient,
            "call",
            autospec=True,
            return_value=response,
        ) as call:
            frame = client.historical_minute(
                "600000.SH",
                "20260722",
                latest_time="11:00",
            )

        self.assertEqual(frame["time"].tolist(), [
            "2026-07-22 09:30:00",
            "2026-07-22 11:00:00",
        ])
        _, api_name, params, fields = call.call_args.args
        self.assertEqual(api_name, "stk_mins")
        self.assertEqual(params["freq"], "1min")
        self.assertTrue(str(params["end_date"]).endswith("11:00:59"))
        self.assertIn("trade_time", fields)

    def test_minute_sync_cap_prioritizes_formal_and_stage_watch_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prediction_root = root / "outputs" / "auction_v3" / "predictions"
            prediction_root.mkdir(parents=True)
            pd.DataFrame(
                [
                    {
                        "ts_code": "600002.SH",
                        "name": "普通观察",
                        "expected_buy_date": "20260721",
                        "selected": 0,
                        "stage_focus": 0,
                        "predicted_continuation_limit_up_probability": 0.2,
                        "conservative_ev": -0.01,
                    },
                    {
                        "ts_code": "600003.SH",
                        "name": "二进三",
                        "expected_buy_date": "20260721",
                        "selected": 0,
                        "stage_focus": 1,
                        "predicted_continuation_limit_up_probability": 0.5,
                        "conservative_ev": 0.01,
                    },
                    {
                        "ts_code": "600004.SH",
                        "name": "正式信号",
                        "expected_buy_date": "20260721",
                        "selected": 1,
                        "stage_focus": 1,
                        "predicted_continuation_limit_up_probability": 0.4,
                        "conservative_ev": 0.02,
                    },
                ]
            ).to_csv(prediction_root / "pred_20260720.csv", index=False)

            codes = _collect_codes(root, "20260721", "", max_codes=2)

        self.assertEqual(codes, ["600004.SH", "600003.SH"])

    def test_later_intraday_break_does_not_count_as_auction_fill(self) -> None:
        label = infer_fill_label(
            pd.Series(
                {
                    "open_t1": 11.0,
                    "up_limit_t1": 11.0,
                    "open_times_t1": 3,
                    "break_open_times_t1": 3,
                }
            )
        )
        self.assertEqual(label[0], 0)
        self.assertEqual(label[1], "strong_opening_auction_limit_up_unconfirmed")

    def test_eret_gap_up_uses_fixed_open_not_hindsight_close(self) -> None:
        label = infer_eret_label(
            pd.Series(
                {
                    "y_fill": 1,
                    "entry_price_proxy_t1": 10.0,
                    "auction_price_t2": 12.0,
                    "open_t2": 12.0,
                    "high_t2": 20.0,
                    "low_t2": 12.0,
                    "close_t2": 20.0,
                }
            )
        )
        self.assertAlmostEqual(float(label[0]), 0.20)
        self.assertEqual(label[2], 1)
        self.assertEqual(label[3], 12.0)
        self.assertEqual(label[6], "fixed_open_0930")

    def test_daily_high_low_and_close_do_not_change_fixed_open_exit(self) -> None:
        result = simulate_tplus1_exit(
            entry_price=10.0,
            open_price=10.0,
            high_price=11.6,
            low_price=9.4,
            close_price=10.2,
        )
        self.assertTrue(result.executable)
        self.assertEqual(result.exit_price, 10.0)
        self.assertEqual(result.reason, "fixed_open_0930")

    def test_intraday_minutes_do_not_change_fixed_open_exit(self) -> None:
        minute = pd.DataFrame(
            [
                {"time": "2026-07-22 09:30:00", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4},
                {"time": "2026-07-22 09:31:00", "open": 10.4, "high": 11.6, "low": 10.3, "close": 11.5},
                {"time": "2026-07-22 09:32:00", "open": 11.5, "high": 11.5, "low": 9.4, "close": 9.8},
            ]
        )
        result = simulate_tplus1_exit(
            entry_price=10.0,
            open_price=10.0,
            high_price=11.6,
            low_price=9.4,
            close_price=9.8,
            minute_frame=minute,
        )
        self.assertEqual(result.exit_price, 10.0)
        self.assertEqual(result.reason, "fixed_open_0930")

    def test_minute_0930_open_is_only_a_fallback_when_daily_open_is_missing(self) -> None:
        minute = pd.DataFrame(
            [
                {
                    "time": "2026-07-22 09:30:00",
                    "open": 10.2,
                    "high": 10.2,
                    "low": 10.2,
                    "close": 10.2,
                },
            ]
        )
        result = simulate_tplus1_exit(
            entry_price=10.0,
            open_price=float("nan"),
            high_price=10.2,
            low_price=10.2,
            close_price=10.2,
            minute_frame=minute,
        )
        self.assertTrue(result.executable)
        self.assertEqual(result.exit_price, 10.2)
        self.assertEqual(result.source, "minute_0930_open_fallback")
        self.assertEqual(result.latest_exit_time, "09:30")

    def test_one_price_limit_down_is_not_an_executable_tplus1_exit(self) -> None:
        label = infer_eret_label(
            pd.Series(
                {
                    "y_fill": 1,
                    "entry_price_proxy_t1": 10.0,
                    "open_t2": 9.0,
                    "high_t2": 9.0,
                    "low_t2": 9.0,
                    "close_t2": 9.0,
                    "down_limit_t2": 9.0,
                }
            )
        )
        self.assertIsNone(label[0])
        self.assertEqual(label[1], "blocked_one_price_limit_down")
        self.assertEqual(label[2], 0)
        self.assertEqual(label[5], 0)

    def test_minute_snapshot_uses_first_0930_open(self) -> None:
        frame = pd.DataFrame(
            [
                {"time": "2026-07-21 09:30:00", "open": 10.2, "close": 10.3},
                {"time": "2026-07-21 09:31:00", "open": 10.4, "close": 10.5},
            ]
        )
        self.assertEqual(opening_auction_price_from_snapshot(frame), 10.2)


class DecisionUniverseAndEvTests(unittest.TestCase):
    def test_only_price_limit_mechanisms_at_or_below_ten_percent_survive(self) -> None:
        frame = pd.DataFrame(
            [
                {"ts_code": "600001.SH", "name": "主板A"},
                {"ts_code": "000002.SZ", "name": "主板B"},
                {"ts_code": "002003.SZ", "name": "ST样本"},
                {"ts_code": "300001.SZ", "name": "创业板"},
                {"ts_code": "688001.SH", "name": "科创板"},
                {"ts_code": "920001.BJ", "name": "北交所"},
                {"ts_code": "600010.SH", "name": "新股", "limit_type": "no_limit"},
                {"ts_code": "600011.SH", "name": "价格取整", "pre_close": 3.79, "up_limit": 4.17},
                {"ts_code": "600012.SH", "name": "上市初期", "trade_date": "20260720", "list_date": "20260715"},
            ]
        )
        eligible, audit = filter_standard_limit_universe(frame)
        self.assertEqual(set(eligible["ts_code"]), {"600001.SH", "000002.SZ", "002003.SZ", "600011.SH"})
        self.assertEqual(audit["rejected_rows"], 5)
        self.assertTrue((eligible["decision_limit_pct"] <= 10.0).all())

    def test_ev_cost_and_risk_are_subtracted_once(self) -> None:
        frame = pd.DataFrame(
            [{"p_fill_pred": 0.8, "e_ret_pred": 0.05, "cost_est": 0.004, "risk_penalty": 0.006}]
        )
        result = _apply_ev_upgrade_v1(frame).iloc[0]
        self.assertAlmostEqual(float(result["ev_pred"]), 0.03, places=12)
        self.assertEqual(float(result["ev_penalty_total_extra"]), 0.0)


class DecisionObservationContractTests(unittest.TestCase):
    def test_watchlist_is_capped_and_legacy_price_is_reproducible(self) -> None:
        rows = [
            {
                "ts_code": f"600{i:03d}.SH",
                "stage_transition": "2→3",
                "mechanism_limit_pct": 10.0,
                "d_close": 10.0 + i,
                "estimated_up_limit": 11.0 + i,
                "predicted_continuation_limit_up_probability": 0.9 - i * 0.01,
                "predicted_big_loss_probability": 0.1 + i * 0.01,
                "conservative_ev": 0.02 - i * 0.001,
                "rank": i + 1,
            }
            for i in range(12)
        ]
        ranked, total = rank_observation_rows(rows)
        self.assertEqual(total, 12)
        self.assertEqual(len(ranked), 10)
        self.assertEqual([row["observation_rank"] for row in ranked], list(range(1, 11)))
        self.assertEqual(ranked[0]["observation_max_price"], 10.0)
        self.assertEqual(ranked[0]["observation_price_basis"], "legacy_d_close_cap")

    def test_watchlist_places_safe_candidate_before_high_probability_high_risk(self) -> None:
        rows = [
            {
                "ts_code": "600001.SH",
                "stage_transition": "2→3",
                "mechanism_limit_pct": 10.0,
                "d_close": 10.0,
                "estimated_up_limit": 11.0,
                "risk_gate_pass": 0,
                "predicted_continuation_limit_up_probability": 0.95,
                "predicted_big_loss_probability": 0.60,
                "predicted_return_lcb": -0.08,
                "predicted_exit_probability": 0.70,
                "conservative_ev": 0.03,
                "rank": 1,
            },
            {
                "ts_code": "600002.SH",
                "stage_transition": "2→3",
                "mechanism_limit_pct": 10.0,
                "d_close": 10.0,
                "estimated_up_limit": 11.0,
                "risk_gate_pass": 1,
                "predicted_continuation_limit_up_probability": 0.70,
                "predicted_big_loss_probability": 0.10,
                "predicted_return_lcb": 0.01,
                "predicted_exit_probability": 0.95,
                "conservative_ev": 0.01,
                "rank": 2,
            },
        ]
        ranked, _ = rank_observation_rows(rows)
        self.assertEqual(ranked[0]["ts_code"], "600002.SH")
        self.assertEqual(ranked[0]["observation_risk_label"], "正式安全门槛")
        self.assertEqual(ranked[1]["observation_risk_label"], "高风险观察")


class DecisionActionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "outputs" / "decision").mkdir(parents=True)
        (self.root / "outputs" / "auction_v3" / "predictions").mkdir(parents=True)
        (self.root / "outputs" / "auction_v3" / "metrics").mkdir(parents=True)
        (self.root / "outputs" / "auction_v3" / "models").mkdir(parents=True)
        (self.root / "data" / "decision").mkdir(parents=True)
        (self.root / "outputs" / "decision" / "decision_report_20260721.md").write_text("# report\n", encoding="utf-8")
        (self.root / "outputs" / "decision" / "eval_20260721.json").write_text(
            json.dumps(
                {
                    "signal_date": "20260720",
                    "exec_date": "20260721",
                    "exit_date": "20260722",
                    "risk_budget": 0.6,
                    "stop_trading": False,
                    "paths": {"candidates": "data/decision/decision_candidates_20260720.csv"},
                }
            ),
            encoding="utf-8",
        )
        pd.DataFrame(
            [
                {"ts_code": "600001.SH", "name": "主板", "industry": "银行", "p_fill_pred": 0.8, "e_ret_pred": 0.02, "ev_pred": 0.01},
                {"ts_code": "300001.SZ", "name": "创业板", "industry": "软件", "p_fill_pred": 0.9, "e_ret_pred": 0.03, "ev_pred": 0.02},
            ]
        ).to_csv(self.root / "data" / "decision" / "decision_candidates_20260720.csv", index=False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_market_close_snapshot(
        self,
        trade_date: str,
        returns: list[float],
        limit_up_industries: list[str],
    ) -> None:
        market_root = (
            self.root
            / "data"
            / "market"
            / "raw"
            / trade_date[:4]
            / trade_date
        )
        market_root.mkdir(parents=True, exist_ok=True)
        codes = [f"600{100 + index:03d}.SH" for index in range(len(returns))]
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 11.0 if index < len(limit_up_industries) else 10.2,
                    "low": 9.8,
                    "close": 11.0 if index < len(limit_up_industries) else 10.0 * (1.0 + value),
                    "pre_close": 10.0,
                    "pct_chg": 10.0 if index < len(limit_up_industries) else value * 100.0,
                    "vol": 1_000_000,
                    "amount": 20_000_000,
                }
                for index, (code, value) in enumerate(zip(codes, returns))
            ]
        ).to_csv(market_root / "daily.csv", index=False)
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
                for code in codes
            ]
        ).to_csv(market_root / "stk_limit.csv", index=False)
        pd.DataFrame(
            [
                {
                    "ts_code": codes[index],
                    "trade_date": trade_date,
                    "limit_type": "U",
                    "industry": industry,
                }
                for index, industry in enumerate(limit_up_industries)
            ]
        ).to_csv(market_root / "limit_list_d.csv", index=False)

    def _write_model_artifacts(
        self,
        *,
        promoted: bool,
        version: str = "auction_v12_top10_trade_selector_oos_1",
        artifact: str = "a" * 64,
    ) -> None:
        trade_artifact = "c" * 64
        model_v2_version = (
            "decision_model_canonical_v2_q8_half_even_raw_execution"
        )
        model_canonical_contract = {
            "schema": "dc20_canonical_fingerprint_v2",
            "layer": "model",
            "decimals": 8,
            "rounding": "decimal_string_half_even",
            "execution_mode": "raw_float64",
            "raw_execution_preserved": True,
        }
        selector_v2_version = (
            "trade_selector_canonical_v2_q8_half_even_raw_execution"
        )
        selector_canonical_contract = {
            "schema": "dc20_canonical_fingerprint_v2",
            "layer": "trade_selector",
            "decimals": 8,
            "rounding": "decimal_string_half_even",
            "execution_mode": "raw_float64",
            "raw_execution_preserved": True,
        }
        model_policy_projection = {
            "version": version,
            "ready": True,
            "reason": "chronological_policy_holdout_passed",
            "max_positions": 2,
            "thresholds": {
                "max_big_loss_probability": 0.2,
                "min_mean_return_lcb": 0.0,
                "min_fill_probability": 0.5,
                "min_exit_probability": 0.8,
                "min_conservative_ev": 0.0,
                "min_selection_score": 0.1,
            },
        }
        selector_policy_projection = {
            "version": "trade_selector_v2_nested_oos_top10_promotion_rank",
            "ready": bool(promoted),
            "reason": "chronological_policy_holdout_passed",
            "max_positions": 2,
            "tail_risk_weight": 0.0,
            "thresholds": {
                "min_trade_score": 0.1,
                "min_mean_return_lcb": 0.0,
                "min_fill_probability": 0.5,
                "max_big_loss_probability": 0.2,
            },
        }
        model_policy_sha256 = canonical_mapping_sha256(
            {
                "schema": CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": model_policy_projection,
            },
            decimals=8,
            exact_strings=True,
        )
        selector_policy_sha256 = canonical_policy_fingerprint(
            selector_policy_projection,
            decimals=8,
        )["sha256"]
        model_provenance_sha256 = "1" * 64
        model_semantic_sha256 = "2" * 64
        selector_provenance_sha256 = "3" * 64
        selector_semantic_sha256 = "4" * 64
        model_v2_artifact = compose_artifact_fingerprint(
            artifact_kind="decision_model_canonical_runtime_v2",
            provenance_sha256=model_provenance_sha256,
            semantic_sha256=model_semantic_sha256,
            policy_sha256=model_policy_sha256,
            decimals=8,
        )
        selector_v2_artifact = compose_artifact_fingerprint(
            artifact_kind="decision_trade_selector_canonical_runtime_v2",
            provenance_sha256=selector_provenance_sha256,
            semantic_sha256=selector_semantic_sha256,
            policy_sha256=selector_policy_sha256,
            decimals=8,
        )
        model_fingerprint_v2 = {
            "schema": CANONICAL_FINGERPRINT_SCHEMA,
            "canonical_version": model_v2_version,
            "canonical_contract": model_canonical_contract,
            "provenance_sha256": model_provenance_sha256,
            "semantic_sha256": model_semantic_sha256,
            "policy_sha256": model_policy_sha256,
            "policy_projection": model_policy_projection,
            "artifact_sha256": model_v2_artifact,
            "schema_valid": True,
            "missing_columns": [],
            "invalid_cell_count": 0,
        }
        selector_fingerprint_v2 = {
            "schema": CANONICAL_FINGERPRINT_SCHEMA,
            "canonical_version": selector_v2_version,
            "canonical_contract": selector_canonical_contract,
            "provenance_sha256": selector_provenance_sha256,
            "semantic_sha256": selector_semantic_sha256,
            "policy_sha256": selector_policy_sha256,
            "policy_projection": selector_policy_projection,
            "artifact_sha256": selector_v2_artifact,
            "schema_valid": True,
            "missing_columns": [],
            "invalid_cell_count": 0,
        }
        prediction = pd.DataFrame(
            [
                {
                    "signal_date": "20260720",
                    "expected_buy_date": "20260721",
                    "expected_exit_date": "20260722",
                    "ts_code": "600001.SH",
                    "name": "主板",
                    "industry": "银行",
                    "selected": 1,
                    "trade_selected": 1,
                    "trade_shadow_selected": int(promoted),
                    "trade_rank": 1,
                    "trade_selector_promoted": int(promoted),
                    "trade_selector_artifact_sha256": trade_artifact,
                    "trade_selector_canonical_v2_version": selector_v2_version,
                    "trade_selector_artifact_v2_sha256": selector_v2_artifact,
                    "trade_selector_canonical_schema": selector_canonical_contract["schema"],
                    "trade_selector_canonical_decimals": selector_canonical_contract["decimals"],
                    "trade_selector_execution_numeric_mode": selector_canonical_contract["execution_mode"],
                    "trade_selector_raw_execution_preserved": 1,
                    "action": "BUY",
                    "recommended_max_price": 10.5,
                    "mechanism_limit_pct": 10.036,
                    "predicted_fill_probability": 0.8,
                    "predicted_exit_probability": 0.95,
                    "predicted_big_loss_probability": 0.05,
                    "predicted_continuation_limit_up_probability": 0.60,
                    "predicted_net_return": 0.02,
                    "predicted_return_lcb": 0.005,
                    "conservative_ev": 0.004,
                    "risk_gate_pass": 1,
                    "stage": "2→3",
                    "stage_focus": 1,
                    "order_type": "LIMIT_ONLY_MANUAL",
                    "market_order_allowed": 0,
                    "model_ready": 1,
                    "model_promoted": int(promoted),
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                    "model_canonical_v2_version": model_v2_version,
                    "model_artifact_v2_sha256": model_v2_artifact,
                    "model_canonical_schema": model_canonical_contract["schema"],
                    "model_canonical_decimals": model_canonical_contract["decimals"],
                    "model_execution_numeric_mode": model_canonical_contract["execution_mode"],
                    "model_raw_execution_preserved": 1,
                },
                {
                    "signal_date": "20260720",
                    "expected_buy_date": "20260721",
                    "expected_exit_date": "20260722",
                    "ts_code": "300001.SZ",
                    "name": "创业板",
                    "selected": 1,
                    "trade_selected": 1,
                    "trade_shadow_selected": int(promoted),
                    "trade_rank": 2,
                    "trade_selector_promoted": int(promoted),
                    "trade_selector_artifact_sha256": trade_artifact,
                    "trade_selector_canonical_v2_version": selector_v2_version,
                    "trade_selector_artifact_v2_sha256": selector_v2_artifact,
                    "trade_selector_canonical_schema": selector_canonical_contract["schema"],
                    "trade_selector_canonical_decimals": selector_canonical_contract["decimals"],
                    "trade_selector_execution_numeric_mode": selector_canonical_contract["execution_mode"],
                    "trade_selector_raw_execution_preserved": 1,
                    "action": "BUY",
                    "model_ready": 1,
                    "model_promoted": int(promoted),
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                    "model_canonical_v2_version": model_v2_version,
                    "model_artifact_v2_sha256": model_v2_artifact,
                    "model_canonical_schema": model_canonical_contract["schema"],
                    "model_canonical_decimals": model_canonical_contract["decimals"],
                    "model_execution_numeric_mode": model_canonical_contract["execution_mode"],
                    "model_raw_execution_preserved": 1,
                },
            ]
        )
        prediction["observation_selected"] = 1
        prediction["promotion_rank"] = [1, 2]
        prediction["promotion_rank_score"] = [0.8, 0.7]
        prediction["predicted_promotion_probability"] = [0.8, 0.7]
        prediction["trade_score"] = [0.02, 0.01]
        prediction["trade_predicted_conditional_net_return"] = 0.02
        prediction["trade_predicted_mean_return_lcb"] = 0.01
        prediction["trade_predicted_fill_probability"] = 0.8
        prediction["trade_predicted_public_market_buyable_probability"] = 0.8
        prediction["trade_predicted_big_loss_probability"] = 0.05
        prediction["trade_predicted_outcome_q10"] = -0.01
        prediction["trade_tail_loss_proxy"] = -0.01
        prediction["trade_base_score"] = 0.02
        prediction["trade_tail_risk_weight"] = 0.0
        prediction["trade_gate_pass"] = 1
        prediction["trade_selector_policy_ready"] = int(promoted)
        prediction["trade_model_reason"] = "learned_policy_pass"
        prediction["trade_selector_version"] = (
            "trade_selector_v2_nested_oos_top10_promotion_rank"
        )
        prediction.to_csv(
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv",
            index=False,
        )
        (self.root / "outputs" / "auction_v3" / "metrics" / "backtest_latest.json").write_text(
            json.dumps(
                {
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                    "model_canonical_v2_version": model_v2_version,
                    "model_artifact_v2_sha256": model_v2_artifact,
                    "model_fingerprint_v2": model_fingerprint_v2,
                    "model_canonical_contract": model_canonical_contract,
                    "promoted": promoted,
                    "promotion_failures": [],
                    "trade_selector": {
                        "promoted": promoted,
                        "version": "trade_selector_v2_nested_oos_top10_promotion_rank",
                        "production_artifact_sha256": trade_artifact,
                        "canonical_v2_version": selector_v2_version,
                        "production_artifact_v2_sha256": selector_v2_artifact,
                        "production_fingerprint_v2": selector_fingerprint_v2,
                        "canonical_contract": selector_canonical_contract,
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json").write_text(
            json.dumps(
                {
                    "model_version": version,
                    "model_artifact_sha256": artifact,
                    "model_canonical_v2_version": model_v2_version,
                    "model_artifact_v2_sha256": model_v2_artifact,
                    "model_fingerprint_v2": model_fingerprint_v2,
                    "model_canonical_contract": model_canonical_contract,
                    "ready": True,
                    "promoted": promoted,
                    "trade_selector": {
                        "promoted": promoted,
                        "version": "trade_selector_v2_nested_oos_top10_promotion_rank",
                        "production_artifact_sha256": trade_artifact,
                        "canonical_v2_version": selector_v2_version,
                        "production_artifact_v2_sha256": selector_v2_artifact,
                        "production_fingerprint_v2": selector_fingerprint_v2,
                        "canonical_contract": selector_canonical_contract,
                    },
                    "current_market_sentiment": {
                        "market_limit_up_industry_top10": [
                            {
                                "rank": 1,
                                "industry": "银行",
                                "limit_up_count": 3,
                                "share": 0.3,
                            },
                            {
                                "rank": 2,
                                "industry": "软件",
                                "limit_up_count": 2,
                                "share": 0.2,
                            },
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _mark_selector_outside_domain(
        prediction: pd.DataFrame,
        row_index: int,
    ) -> None:
        prediction.loc[row_index, "observation_selected"] = 0
        for column in (
            "promotion_rank_score",
            "predicted_promotion_probability",
            "trade_score",
            "trade_predicted_conditional_net_return",
            "trade_predicted_mean_return_lcb",
            "trade_predicted_fill_probability",
            "trade_predicted_public_market_buyable_probability",
            "trade_predicted_big_loss_probability",
            "trade_predicted_outcome_q10",
            "trade_tail_loss_proxy",
            "trade_base_score",
            "trade_tail_risk_weight",
            "promotion_rank",
            "trade_rank",
            "trade_selector_artifact_sha256",
            "trade_selector_artifact_v2_sha256",
        ):
            prediction.loc[row_index, column] = None
        for column in (
            "trade_gate_pass",
            "trade_shadow_selected",
            "trade_selected",
            "trade_selector_policy_ready",
        ):
            prediction.loc[row_index, column] = 0
        prediction.loc[
            row_index,
            "trade_model_reason",
        ] = "outside_observation_top10"

    def test_unpromoted_model_can_never_emit_formal_buy(self) -> None:
        self._write_model_artifacts(promoted=False)
        plan = build_action_plan(self.root)
        self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
        self.assertEqual(plan["formal_buy_count"], 0)
        self.assertEqual(plan["shadow_count"], 1)
        self.assertFalse(any(row["action"] == "BUY" for row in plan["candidates"]))
        self.assertEqual(plan["stage_watch_count"], 1)
        self.assertEqual(plan["stage_watchlist"][0]["watch_label"], "二筛影子")
        self.assertEqual(
            plan["schema_version"],
            "decision_action_plan_v12_top10_trade_selector",
        )
        self.assertEqual(plan["stage_watchlist"][0]["observation_max_price"], 10.5)
        self.assertIn("observation_statistics", plan)
        self.assertIn("market_sentiment", plan)
        self.assertEqual(
            plan["market_sentiment"]["limit_up_industry_top10"],
            [
                {
                    "rank": 1,
                    "industry": "银行",
                    "limit_up_count": 3,
                    "share": 0.3,
                },
                {
                    "rank": 2,
                    "industry": "软件",
                    "limit_up_count": 2,
                    "share": 0.2,
                },
            ],
        )
        self.assertEqual(
            plan["market_sentiment"]["limit_up_industry_top5"],
            plan["market_sentiment"]["limit_up_industry_top10"][:5],
        )
        self.assertIn("market_close_comparison", plan)
        self.assertFalse(plan["market_close_comparison"]["model_input"])
        self.assertFalse(plan["market_close_comparison"]["t"]["available"])

    def test_unpromoted_plan_selects_exactly_two_relative_best_candidates(self) -> None:
        self._write_model_artifacts(promoted=False)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        template = prediction.iloc[0].copy()
        additions = []
        for code, name, trade_rank, big_loss in (
            ("600002.SH", "主板二", 2, 0.06),
            ("600003.SH", "主板三", 3, 0.07),
            ("600004.SH", "Top10外诊断票", None, 0.01),
        ):
            row = template.copy()
            row["ts_code"] = code
            row["name"] = name
            row["trade_rank"] = trade_rank
            row["trade_shadow_selected"] = 0
            row["trade_selected"] = 0
            row["selected"] = 0
            row["predicted_big_loss_probability"] = big_loss
            additions.append(row)
        pd.concat([prediction, pd.DataFrame(additions)], ignore_index=True).to_csv(
            prediction_path,
            index=False,
        )

        plan = build_action_plan(self.root)

        shadow = sorted(
            [
                row
                for row in plan["candidates"]
                if row["action"] == "SHADOW_ONLY"
            ],
            key=lambda row: row["trade_rank"],
        )
        self.assertEqual(plan["formal_buy_count"], 0)
        self.assertEqual(plan["shadow_count"], 2)
        self.assertEqual(
            [row["ts_code"] for row in shadow],
            ["600001.SH", "600002.SH"],
        )
        outside = next(
            row for row in plan["candidates"] if row["ts_code"] == "600004.SH"
        )
        self.assertEqual(outside["action"], "REJECT")
        self.assertEqual(outside["trade_rank"], 0)

    def test_pending_plan_uses_relative_best_two_without_formal_buy(self) -> None:
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "name": name,
                    "industry": "银行",
                    "advance_stage": "2→3",
                    "decision_limit_pct": 10.0,
                    "promotion_rank": promotion_rank,
                    "predicted_big_loss_probability": big_loss,
                    "predicted_return_lcb": return_lcb,
                }
                for code, name, promotion_rank, big_loss, return_lcb in (
                    ("600001.SH", "主板一", 3, 0.05, 0.03),
                    ("600002.SH", "主板二", 1, 0.20, 0.01),
                    ("600003.SH", "主板三", 1, 0.08, 0.00),
                )
            ]
        ).to_csv(
            self.root / "data" / "decision" / "decision_candidates_20260720.csv",
            index=False,
        )

        plan = build_action_plan(self.root)

        shadow = sorted(
            [
                row
                for row in plan["candidates"]
                if row["action"] == "SHADOW_ONLY"
            ],
            key=lambda row: row["trade_rank"],
        )
        self.assertEqual(plan["status_code"], "PENDING_AUCTION_MODEL")
        self.assertEqual(plan["formal_buy_count"], 0)
        self.assertEqual(plan["shadow_count"], 2)
        self.assertEqual(
            [row["ts_code"] for row in shadow],
            ["600003.SH", "600002.SH"],
        )

    def test_market_close_comparison_waits_for_complete_t_snapshot(self) -> None:
        self._write_model_artifacts(promoted=False)
        self._write_market_close_snapshot(
            "20260720",
            [0.10, 0.10, 0.02, 0.01, 0.00, -0.01, -0.02, 0.03, -0.04, 0.01],
            ["电力", "电网设备"],
        )
        self._write_market_close_snapshot(
            "20260721",
            [0.10, 0.02, 0.01, 0.00, -0.01, -0.02, 0.03, -0.04],
            ["电力"],
        )
        incomplete = build_action_plan(self.root)["market_close_comparison"]
        self.assertFalse(incomplete["t"]["available"])
        self.assertEqual(
            incomplete["t"]["maturity_status"],
            "INCOMPLETE_T_CLOSE",
        )

        self._write_market_close_snapshot(
            "20260721",
            [0.10, 0.02, 0.01, 0.00, -0.01, -0.02, 0.03, -0.04, 0.01],
            ["电力"],
        )
        complete = build_action_plan(self.root)["market_close_comparison"]
        self.assertTrue(complete["d"]["available"])
        self.assertTrue(complete["t"]["available"])
        self.assertEqual(complete["t"]["maturity_status"], "FINAL_T_CLOSE")
        self.assertAlmostEqual(complete["t"]["coverage_against_d"], 0.9)
        self.assertEqual(complete["d"]["up_count"], 6)
        self.assertEqual(complete["d"]["down_count"], 3)
        self.assertEqual(complete["d"]["flat_count"], 1)
        self.assertEqual(complete["d"]["industry_counts"]["电力"], 1)
        self.assertEqual(complete["t"]["industry_counts"]["电力"], 1)

    def test_promoted_model_still_rejects_above_ten_percent_board(self) -> None:
        self._write_model_artifacts(promoted=True)
        plan = build_action_plan(self.root)
        actions = {row["ts_code"]: row["action"] for row in plan["candidates"]}
        self.assertEqual(actions["600001.SH"], "BUY")
        self.assertEqual(actions["300001.SZ"], "REJECT")
        self.assertEqual(plan["formal_buy_count"], 1)
        main_board = next(row for row in plan["candidates"] if row["ts_code"] == "600001.SH")
        self.assertEqual(main_board["mechanism_limit_pct"], 10.0)
        self.assertEqual(main_board["stage_transition"], "2→3")
        self.assertEqual(main_board["market_order_allowed"], 0)
        self.assertFalse(plan["broker_connected"])
        self.assertEqual(plan["stage_watchlist"][0]["watch_label"], "正式买入")
        self.assertLessEqual(plan["stage_watch_count"], 10)

    def test_artifact_version_mismatch_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        meta_path = self.root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json"
        meta_path.write_text(json.dumps({"model_version": "stale", "ready": True, "promoted": True}), encoding="utf-8")
        plan = build_action_plan(self.root)
        self.assertFalse(plan["model"]["promoted"])
        self.assertFalse(plan["model"]["artifact_versions_match"])
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_v1_mismatch_is_audited_when_v2_is_valid(self) -> None:
        self._write_model_artifacts(promoted=True)
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["model_artifact_sha256"] = "b" * 64
        meta["trade_selector"]["production_artifact_sha256"] = "b" * 64
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        plan = build_action_plan(self.root)
        self.assertTrue(plan["model"]["promoted"])
        self.assertFalse(plan["model"]["artifact_versions_match"])
        self.assertFalse(plan["model"]["artifact_fingerprints_match"])
        self.assertFalse(plan["model"]["trade_selector_artifacts_match"])
        self.assertTrue(plan["model"]["legacy_v1_audit_only"])
        self.assertTrue(plan["model"]["v2_integrity_match"])
        self.assertEqual(plan["status_code"], "ACTIONABLE_BUY")
        self.assertEqual(plan["formal_buy_count"], 1)

    def test_v2_integrity_fields_are_published(self) -> None:
        self._write_model_artifacts(promoted=True)

        plan = build_action_plan(self.root)
        model = plan["model"]

        self.assertTrue(model["v2_integrity_enforced"])
        self.assertTrue(model["v2_integrity_match"])
        self.assertTrue(model["v2_eligibility_match"])
        self.assertEqual(model["v2_integrity_failures"], [])
        self.assertTrue(model["canonical_v2_versions_match"])
        self.assertTrue(model["artifact_v2_fingerprints_match"])
        self.assertTrue(model["fingerprint_v2_valid"])
        self.assertTrue(model["canonical_policy_ready"])
        self.assertTrue(model["canonical_contracts_match"])
        self.assertTrue(model["canonical_decimals_match"])
        self.assertEqual(model["canonical_decimals"], 8)
        self.assertEqual(model["execution_numeric_mode"], "raw_float64")
        self.assertTrue(model["raw_execution_preserved"])
        self.assertTrue(
            model["trade_selector_canonical_v2_versions_match"]
        )
        self.assertTrue(model["trade_selector_artifacts_v2_match"])
        self.assertTrue(model["trade_selector_fingerprint_v2_valid"])
        self.assertTrue(model["trade_selector_canonical_policy_ready"])
        self.assertTrue(
            model["trade_selector_canonical_contracts_match"]
        )
        self.assertTrue(
            model["trade_selector_canonical_decimals_match"]
        )
        self.assertEqual(model["trade_selector_canonical_decimals"], 8)
        self.assertEqual(
            model["trade_selector_execution_numeric_mode"],
            "raw_float64",
        )
        self.assertTrue(model["trade_selector_raw_execution_preserved"])
        selected = next(
            row
            for row in plan["candidates"]
            if row["ts_code"] == "600001.SH"
        )
        self.assertEqual(
            selected["model_artifact_v2_sha256"],
            model["artifact_v2_sha256"],
        )
        self.assertEqual(
            selected["trade_selector_artifact_v2_sha256"],
            model["trade_selector_artifact_v2_sha256"],
        )
        self.assertEqual(
            selected["model_execution_numeric_mode"],
            "raw_float64",
        )
        self.assertTrue(selected["model_raw_execution_preserved"])
        self.assertNotIn("model_numeric_contract", selected)

    def test_one_prediction_row_v2_hash_tamper_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        prediction.loc[1, "model_artifact_v2_sha256"] = "f" * 64
        prediction.to_csv(prediction_path, index=False)

        plan = build_action_plan(self.root)

        self.assertFalse(plan["model"]["artifact_v2_fingerprints_match"])
        self.assertIn(
            "model.artifact_v2_sha256",
            plan["model"]["v2_integrity_failures"],
        )
        self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_missing_v2_canonical_contract_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        backtest_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "metrics"
            / "backtest_latest.json"
        )
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        del backtest["model_canonical_contract"]
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")

        plan = build_action_plan(self.root)

        self.assertFalse(plan["model"]["canonical_contracts_match"])
        self.assertFalse(plan["model"]["canonical_decimals_match"])
        self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_mixed_prediction_v2_version_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        prediction.loc[1, "model_canonical_v2_version"] = "mixed-v2"
        prediction.to_csv(prediction_path, index=False)

        plan = build_action_plan(self.root)

        self.assertFalse(plan["model"]["canonical_v2_versions_match"])
        self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_v2_canonical_contract_or_decimals_mismatch_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["model_canonical_contract"]["rounding"] = "different"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        contract_plan = build_action_plan(self.root)
        self.assertFalse(contract_plan["model"]["canonical_contracts_match"])
        self.assertEqual(contract_plan["formal_buy_count"], 0)

        self._write_model_artifacts(promoted=True)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        prediction["model_canonical_decimals"] = 7
        prediction.to_csv(prediction_path, index=False)

        decimals_plan = build_action_plan(self.root)
        self.assertFalse(decimals_plan["model"]["canonical_decimals_match"])
        self.assertEqual(decimals_plan["formal_buy_count"], 0)

        self._write_model_artifacts(promoted=True)
        prediction = pd.read_csv(prediction_path)
        prediction.loc[1, "model_raw_execution_preserved"] = 0
        prediction.to_csv(prediction_path, index=False)

        raw_execution_plan = build_action_plan(self.root)
        self.assertFalse(
            raw_execution_plan["model"]["canonical_contracts_match"]
        )
        self.assertEqual(raw_execution_plan["formal_buy_count"], 0)

    def test_selector_v2_mismatch_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["trade_selector"]["production_artifact_v2_sha256"] = "f" * 64
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        plan = build_action_plan(self.root)

        self.assertTrue(plan["model"]["artifact_v2_fingerprints_match"])
        self.assertFalse(plan["model"]["trade_selector_artifacts_v2_match"])
        self.assertIn(
            "trade_selector.artifact_v2_sha256",
            plan["model"]["v2_integrity_failures"],
        )
        self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_v2_policy_projection_must_be_complete_and_finite(self) -> None:
        self._write_model_artifacts(promoted=True)
        backtest_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "metrics"
            / "backtest_latest.json"
        )
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for payload in (backtest, meta):
            del payload["model_fingerprint_v2"]["policy_projection"][
                "thresholds"
            ]["min_exit_probability"]
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        missing_plan = build_action_plan(self.root)
        self.assertTrue(
            missing_plan["model"]["artifact_v2_fingerprints_match"]
        )
        self.assertFalse(missing_plan["model"]["fingerprint_v2_valid"])
        self.assertEqual(missing_plan["formal_buy_count"], 0)

        self._write_model_artifacts(promoted=True)
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for payload in (backtest, meta):
            payload["trade_selector"]["production_fingerprint_v2"][
                "policy_projection"
            ]["thresholds"]["min_trade_score"] = None
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        nonfinite_plan = build_action_plan(self.root)
        self.assertFalse(
            nonfinite_plan["model"][
                "trade_selector_fingerprint_v2_valid"
            ]
        )
        self.assertEqual(nonfinite_plan["formal_buy_count"], 0)

        self._write_model_artifacts(promoted=True)
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        fingerprint = backtest["trade_selector"]["production_fingerprint_v2"]
        fingerprint["policy_projection"]["ready"] = False
        fingerprint["policy_sha256"] = canonical_policy_fingerprint(
            fingerprint["policy_projection"],
            decimals=8,
        )["sha256"]
        fingerprint["artifact_sha256"] = compose_artifact_fingerprint(
            artifact_kind="decision_trade_selector_canonical_runtime_v2",
            provenance_sha256=fingerprint["provenance_sha256"],
            semantic_sha256=fingerprint["semantic_sha256"],
            policy_sha256=fingerprint["policy_sha256"],
            decimals=8,
        )
        for payload in (backtest, meta):
            payload["trade_selector"]["production_fingerprint_v2"] = dict(
                fingerprint
            )
            payload["trade_selector"]["production_artifact_v2_sha256"] = (
                fingerprint["artifact_sha256"]
            )
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        prediction["trade_selector_artifact_v2_sha256"] = fingerprint[
            "artifact_sha256"
        ]
        prediction.to_csv(prediction_path, index=False)

        not_ready_plan = build_action_plan(self.root)
        self.assertTrue(
            not_ready_plan["model"][
                "trade_selector_fingerprint_v2_valid"
            ]
        )
        self.assertFalse(
            not_ready_plan["model"][
                "trade_selector_canonical_policy_ready"
            ]
        )
        self.assertFalse(not_ready_plan["model"]["v2_eligibility_match"])
        self.assertEqual(not_ready_plan["formal_buy_count"], 0)

    def test_v2_policy_projection_rejects_json_type_aliases(self) -> None:
        backtest_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "metrics"
            / "backtest_latest.json"
        )
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        mutations = (
            (
                "numeric-string threshold",
                lambda fingerprint: fingerprint["policy_projection"][
                    "thresholds"
                ].__setitem__("min_exit_probability", "0.8"),
            ),
            (
                "numeric-string max_positions",
                lambda fingerprint: fingerprint["policy_projection"].__setitem__(
                    "max_positions",
                    "2",
                ),
            ),
            (
                "integer version",
                lambda fingerprint: fingerprint["policy_projection"].__setitem__(
                    "version",
                    123,
                ),
            ),
            (
                "integer ready alias",
                lambda fingerprint: fingerprint["policy_projection"].__setitem__(
                    "ready",
                    1,
                ),
            ),
            (
                "numeric-string invalid count",
                lambda fingerprint: fingerprint.__setitem__(
                    "invalid_cell_count",
                    "0",
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                self._write_model_artifacts(promoted=True)
                backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for payload in (backtest, meta):
                    mutate(payload["model_fingerprint_v2"])
                backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
                meta_path.write_text(json.dumps(meta), encoding="utf-8")

                plan = build_action_plan(self.root)

                self.assertFalse(plan["model"]["fingerprint_v2_valid"])
                self.assertEqual(plan["formal_buy_count"], 0)

        for label, layer, key, value in (
            (
                "model reason whitespace",
                "model",
                "reason",
                " chronological_policy_holdout_passed ",
            ),
            (
                "selector version unicode",
                "trade_selector",
                "version",
                "ｔrade_selector_v2_nested_oos_top10_promotion_rank",
            ),
        ):
            with self.subTest(exact_string_tamper=label):
                self._write_model_artifacts(promoted=True)
                backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if layer == "model":
                    fingerprints = (
                        backtest["model_fingerprint_v2"],
                        meta["model_fingerprint_v2"],
                    )
                else:
                    fingerprints = (
                        backtest["trade_selector"]["production_fingerprint_v2"],
                        meta["trade_selector"]["production_fingerprint_v2"],
                    )
                for fingerprint in fingerprints:
                    fingerprint["policy_projection"][key] = value
                backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
                meta_path.write_text(json.dumps(meta), encoding="utf-8")

                exact_plan = build_action_plan(self.root)

                self.assertFalse(exact_plan["model"]["v2_integrity_match"])
                self.assertEqual(exact_plan["formal_buy_count"], 0)

        for label, key, value in (
            ("numeric-string decimals", "decimals", "8"),
            ("text raw-preserved", "raw_execution_preserved", "true"),
        ):
            with self.subTest(contract_type_alias=label):
                self._write_model_artifacts(promoted=True)
                backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for payload in (backtest, meta):
                    payload["model_canonical_contract"][key] = value
                    payload["model_fingerprint_v2"]["canonical_contract"][key] = value
                backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
                meta_path.write_text(json.dumps(meta), encoding="utf-8")

                contract_plan = build_action_plan(self.root)

                self.assertFalse(contract_plan["model"]["canonical_contracts_match"])
                self.assertEqual(contract_plan["formal_buy_count"], 0)

    def test_v2_policy_projection_rejects_noncanonical_q8_aliases(self) -> None:
        backtest_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "metrics"
            / "backtest_latest.json"
        )
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        mutations = (
            ("model", "min_selection_score", 0.1000000004),
            ("trade_selector", "min_trade_score", 0.1000000004),
            ("trade_selector", "tail_risk_weight", 0.0000000004),
            ("model", "min_mean_return_lcb", 0),
            ("trade_selector", "min_mean_return_lcb", 0),
            ("trade_selector", "tail_risk_weight", 0),
        )
        for layer, field, value in mutations:
            with self.subTest(layer=layer, field=field):
                self._write_model_artifacts(promoted=True)
                backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for payload in (backtest, meta):
                    if layer == "model":
                        projection = payload["model_fingerprint_v2"][
                            "policy_projection"
                        ]
                        projection["thresholds"][field] = value
                    else:
                        projection = payload["trade_selector"][
                            "production_fingerprint_v2"
                        ]["policy_projection"]
                        if field == "tail_risk_weight":
                            projection[field] = value
                        else:
                            projection["thresholds"][field] = value
                if type(value) is int:
                    for payload in (backtest, meta):
                        if layer == "model":
                            fingerprint = payload["model_fingerprint_v2"]
                            policy_sha = canonical_mapping_sha256(
                                {
                                    "schema": CANONICAL_FINGERPRINT_SCHEMA,
                                    "artifact_kind": "decision_model_executable_policy",
                                    "projection": fingerprint["policy_projection"],
                                },
                                decimals=8,
                                exact_strings=True,
                            )
                            artifact_kind = "decision_model_canonical_runtime_v2"
                        else:
                            selector = payload["trade_selector"]
                            fingerprint = selector["production_fingerprint_v2"]
                            policy_sha = canonical_policy_fingerprint(
                                fingerprint["policy_projection"], decimals=8
                            )["sha256"]
                            artifact_kind = (
                                "decision_trade_selector_canonical_runtime_v2"
                            )
                        fingerprint["policy_sha256"] = policy_sha
                        artifact = compose_artifact_fingerprint(
                            artifact_kind=artifact_kind,
                            provenance_sha256=fingerprint["provenance_sha256"],
                            semantic_sha256=fingerprint["semantic_sha256"],
                            policy_sha256=policy_sha,
                            decimals=8,
                        )
                        fingerprint["artifact_sha256"] = artifact
                        if layer == "model":
                            payload["model_artifact_v2_sha256"] = artifact
                        else:
                            selector["production_artifact_v2_sha256"] = artifact
                    prediction = pd.read_csv(prediction_path)
                    artifact_column = (
                        "model_artifact_v2_sha256"
                        if layer == "model"
                        else "trade_selector_artifact_v2_sha256"
                    )
                    prediction[artifact_column] = artifact
                    prediction.to_csv(prediction_path, index=False)
                # Deliberately retain the original q8 policy/artifact hashes:
                # raw aliases use the old q8 hashes; JSON int aliases are fully
                # re-signed so only the strict envelope type gate can reject.
                backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
                meta_path.write_text(json.dumps(meta), encoding="utf-8")

                plan = build_action_plan(self.root)

                valid_field = (
                    "fingerprint_v2_valid"
                    if layer == "model"
                    else "trade_selector_fingerprint_v2_valid"
                )
                self.assertFalse(plan["model"][valid_field])
                self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
                self.assertEqual(plan["formal_buy_count"], 0)

    def test_v2_prediction_hard_types_and_all_row_promotion(self) -> None:
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        self.assertIsNone(_canonical_decimals("8"))
        self.assertFalse(
            _strict_all_true_column(
                pd.DataFrame({"flag": ["1", "true"]}),
                "flag",
            )
        )

        self._write_model_artifacts(promoted=True)
        prediction = pd.read_csv(prediction_path)
        prediction["trade_gate_pass"] = ["1", "1"]
        self.assertFalse(_selector_prediction_domain(prediction)["valid"])

        for column in ("model_ready", "trade_selector_promoted"):
            with self.subTest(mixed_promotion_column=column):
                self._write_model_artifacts(promoted=True)
                prediction = pd.read_csv(prediction_path)
                prediction.loc[1, column] = 0
                prediction.to_csv(prediction_path, index=False)

                plan = build_action_plan(self.root)

                self.assertFalse(plan["model"]["promoted"])
                self.assertEqual(plan["formal_buy_count"], 0)

        self._write_model_artifacts(promoted=True)
        prediction = pd.read_csv(prediction_path)
        prediction["model_raw_execution_preserved"] = prediction[
            "model_raw_execution_preserved"
        ].astype(object)
        prediction.loc[1, "model_raw_execution_preserved"] = "true"
        prediction.to_csv(prediction_path, index=False)

        raw_alias_plan = build_action_plan(self.root)
        self.assertFalse(raw_alias_plan["model"]["canonical_contracts_match"])
        self.assertEqual(raw_alias_plan["formal_buy_count"], 0)

    def test_v2_fingerprint_schema_audit_must_be_clean(self) -> None:
        self._write_model_artifacts(promoted=True)
        backtest_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "metrics"
            / "backtest_latest.json"
        )
        meta_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "models"
            / "model_meta_latest.json"
        )
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for payload in (backtest, meta):
            fingerprint = payload["model_fingerprint_v2"]
            fingerprint["schema_valid"] = False
            fingerprint["missing_columns"] = ["required_feature"]
            fingerprint["invalid_cell_count"] = 1
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        plan = build_action_plan(self.root)

        self.assertFalse(plan["model"]["fingerprint_v2_valid"])
        self.assertIn(
            "model.fingerprint_v2",
            plan["model"]["v2_integrity_failures"],
        )
        self.assertEqual(plan["status_code"], "NO_TRADE_MODEL_NOT_PROMOTED")
        self.assertEqual(plan["formal_buy_count"], 0)

    def test_selector_fingerprint_ignores_non_top10_blank_rows(self) -> None:
        self._write_model_artifacts(promoted=True)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        self._mark_selector_outside_domain(prediction, 1)
        prediction.to_csv(prediction_path, index=False)

        plan = build_action_plan(self.root)

        self.assertTrue(plan["model"]["trade_selector_artifacts_match"])
        self.assertTrue(plan["model"]["promoted"])
        self.assertEqual(plan["formal_buy_count"], 1)

    def test_selector_v2_domain_missing_or_mixed_fails_closed(self) -> None:
        self._write_model_artifacts(promoted=True)
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        prediction = pd.read_csv(prediction_path)
        prediction = prediction.drop(columns=["observation_selected"])
        prediction.to_csv(prediction_path, index=False)

        missing = build_action_plan(self.root)
        self.assertFalse(missing["model"]["v2_integrity_match"])
        self.assertIn(
            "trade_selector.prediction_domain",
            missing["model"]["v2_integrity_failures"],
        )
        self.assertEqual(missing["formal_buy_count"], 0)

        self._write_model_artifacts(promoted=True)
        prediction = pd.read_csv(prediction_path)
        self._mark_selector_outside_domain(prediction, 0)
        prediction.loc[0, "trade_selector_artifact_v2_sha256"] = "e" * 64
        prediction.to_csv(prediction_path, index=False)

        outside_artifact = build_action_plan(self.root)
        self.assertFalse(outside_artifact["model"]["v2_integrity_match"])
        self.assertEqual(outside_artifact["formal_buy_count"], 0)

    def test_selector_v2_outside_execution_contract_is_strict(self) -> None:
        prediction_path = (
            self.root
            / "outputs"
            / "auction_v3"
            / "predictions"
            / "pred_latest.csv"
        )
        for column, value in (
            ("trade_score", 0.0),
            ("trade_gate_pass", 1),
            ("trade_model_reason", "below_learned_policy"),
        ):
            self._write_model_artifacts(promoted=True)
            prediction = pd.read_csv(prediction_path)
            self._mark_selector_outside_domain(prediction, 0)
            prediction.loc[0, column] = value
            prediction.to_csv(prediction_path, index=False)

            plan = build_action_plan(self.root)
            self.assertFalse(plan["model"]["v2_integrity_match"])
            self.assertEqual(plan["formal_buy_count"], 0)


class DecisionWorkflowSerializationTests(unittest.TestCase):
    def test_all_decision_main_writers_share_one_non_cancelling_lock(self) -> None:
        workflow_root = ROOT / ".github" / "workflows"
        for name in (
            "run_decision_daily.yml",
            "run_auction_v3.yml",
            "backfill_decision_v11_history.yml",
        ):
            text = (workflow_root / name).read_text(encoding="utf-8")
            self.assertIn("group: decision-auction-main-writer", text)
            self.assertIn("cancel-in-progress: false", text)

    def test_learning_migration_has_time_and_avoids_shared_pred_meta(self) -> None:
        text = (
            ROOT / ".github" / "workflows" / "run_decision_daily.yml"
        ).read_text(encoding="utf-8")
        learning = text.split("  pfill_learning:", 1)[1].split(
            "  decision_refresh_after_learning:",
            1,
        )[0]
        self.assertIn("timeout-minutes: 120", learning)
        self.assertNotIn(
            "git add data/pred/_pred_source_meta.json",
            learning,
        )


if __name__ == "__main__":
    unittest.main()
