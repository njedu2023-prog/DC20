from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import sync_tushare_minute as sync
from top10decision.rt_min_contract import RTMinContractError


class _FakeClient:
    def __init__(
        self,
        *,
        is_open: int,
        minutes: dict[str, pd.DataFrame] | None = None,
        auction: pd.DataFrame | None = None,
    ) -> None:
        self.is_open = is_open
        self.minutes = minutes or {}
        self.auction = auction if auction is not None else pd.DataFrame()
        self.minute_calls: list[str] = []
        self.auction_calls = 0

    def trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            [
                {
                    "exchange": "SSE",
                    "cal_date": "20260819",
                    "is_open": self.is_open,
                    "pretrade_date": "20260818",
                },
                {
                    "exchange": "SSE",
                    "cal_date": "20260820",
                    "is_open": self.is_open,
                    "pretrade_date": "20260819",
                }
            ]
        )

    def opening_auction(self, trade_date: str) -> pd.DataFrame:
        assert trade_date == "20260820"
        self.auction_calls += 1
        return self.auction.copy()

    def current_minute(self, code: str) -> pd.DataFrame:
        self.minute_calls.append(code)
        return self.minutes.get(code, pd.DataFrame()).copy()


def _minute(timestamp: str = "2026-08-20 10:40:00") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time": timestamp,
                "open": 10.0,
                "close": 10.1,
                "high": 10.2,
                "low": 9.9,
                "vol": 1000,
                "amount": 10000,
            }
        ]
    )


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeClient,
    *extra_args: str,
    workers: int = 1,
) -> int:
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(
        sync.TushareClient,
        "from_env",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        sync,
        "_collect_codes",
        lambda *_args, **_kwargs: ["600000.SH", "000001.SZ"],
    )
    return sync.main(
        [
            "--root",
            str(tmp_path),
            "--trade-date",
            "20260820",
            "--workers",
            str(workers),
            *extra_args,
        ],
        now_shanghai=datetime(2026, 8, 20, 10, 40, tzinfo=sync.SHANGHAI),
    )


def test_active_window_token_present_but_zero_valid_rows_fails_without_meta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(is_open=1)
    assert _run(tmp_path, monkeypatch, client) == 1

    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "fail"
    assert failure["reason"] == "no_valid_minute_rows_active_window"
    assert failure["sync_meta_written"] is False
    assert not (
        tmp_path / "data" / "market" / "minute_1m" / "sync_latest.json"
    ).exists()
    assert sorted(client.minute_calls) == ["000001.SZ", "600000.SH"]


def test_partial_minute_success_is_explicit_and_persists_only_valid_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(
        is_open=1,
        minutes={"600000.SH": _minute(), "000001.SZ": pd.DataFrame()},
        auction=pd.DataFrame(
            [{"ts_code": "600000.SH"}, {"ts_code": "000001.SZ"}]
        ),
    )
    assert _run(tmp_path, monkeypatch, client) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "partial_success"
    assert stdout["reason"] == "minute_partial_success"
    assert stdout["minute_files_written"] == 1
    meta_path = tmp_path / "data" / "market" / "minute_1m" / "sync_latest.json"
    persisted = json.loads(meta_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "partial_success"
    assert persisted["sync_meta_written"] is True
    assert persisted["failures"] == [
        {"ts_code": "000001.SZ", "reason": "no_valid_rows"}
    ]


@pytest.mark.parametrize("contract_reason", ["schema", "identity", "frequency"])
def test_hard_contract_failure_stages_then_writes_zero_minute_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    contract_reason: str,
) -> None:
    class _ContractFailureClient(_FakeClient):
        def current_minute(self, code: str) -> pd.DataFrame:
            self.minute_calls.append(code)
            if code == "000001.SZ":
                raise RTMinContractError(
                    "rt_min_daily: hard response contract failure",
                    reason=contract_reason,
                    row_count=1,
                )
            return _minute()

    client = _ContractFailureClient(is_open=1)
    assert _run(tmp_path, monkeypatch, client, workers=2) == 1

    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "fail"
    assert failure["reason"] == "rt_min_contract_failure"
    minute_root = tmp_path / "data" / "market" / "minute_1m"
    if minute_root.exists():
        assert not list(minute_root.rglob("*"))
    assert not (minute_root / "sync_latest.json").exists()
    assert sorted(client.minute_calls) == ["000001.SZ", "600000.SH"]


def test_closed_session_is_not_applicable_and_never_requests_market_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(is_open=0)
    assert _run(tmp_path, monkeypatch, client, "--optional") == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "not_applicable"
    assert stdout["reason"] == "exchange_closed"
    assert stdout["sync_meta_written"] is False
    assert client.auction_calls == 0
    assert client.minute_calls == []
    assert not (
        tmp_path / "data" / "market" / "minute_1m" / "sync_latest.json"
    ).exists()


def test_dry_run_requires_no_token_network_or_filesystem_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        sync.TushareClient,
        "from_env",
        lambda **_kwargs: pytest.fail("dry-run must not construct a client"),
    )
    assert sync.main(
        ["--root", str(tmp_path), "--trade-date", "20260820", "--dry-run"],
        now_shanghai=datetime(2026, 8, 20, 10, 40, tzinfo=sync.SHANGHAI),
    ) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "not_applicable"
    assert stdout["reason"] == "dry_run"
    assert stdout["filesystem_writes"] == 0
    assert list(tmp_path.iterdir()) == []


def test_optional_cannot_soften_an_open_production_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(is_open=1)
    assert _run(tmp_path, monkeypatch, client, "--optional") == 1

    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "fail"
    assert failure["reason"] == "optional_not_allowed_for_open_production_session"
    assert client.auction_calls == 0
    assert client.minute_calls == []


def test_explicit_research_optional_path_can_report_no_data_without_success_meta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(is_open=1)
    assert _run(
        tmp_path,
        monkeypatch,
        client,
        "--optional",
        "--research-mode",
    ) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "not_applicable"
    assert stdout["reason"] == "research_mode_no_valid_minute_rows"
    assert stdout["sync_meta_written"] is False
    assert not (
        tmp_path / "data" / "market" / "minute_1m" / "sync_latest.json"
    ).exists()


def _run_at(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeClient,
    *,
    hour: int,
    minute: int,
    trade_date: str = "20260820",
    extra_args: tuple[str, ...] = (),
) -> int:
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(sync.TushareClient, "from_env", lambda **_kwargs: client)
    monkeypatch.setattr(
        sync,
        "_collect_codes",
        lambda *_args, **_kwargs: ["600000.SH", "000001.SZ"],
    )
    return sync.main(
        [
            "--root",
            str(tmp_path),
            "--trade-date",
            trade_date,
            "--workers",
            "1",
            *extra_args,
        ],
        now_shanghai=datetime(
            2026,
            8,
            20,
            hour,
            minute,
            tzinfo=sync.SHANGHAI,
        ),
    )


def test_default_current_open_outside_window_is_zero_market_data_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sync,
        "_collect_codes",
        lambda *_args, **_kwargs: pytest.fail("outside window must not collect candidates"),
    )
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        sync.TushareClient,
        "from_env",
        lambda **_kwargs: pytest.fail("outside window must not construct a client"),
    )

    assert sync.main(
        ["--root", str(tmp_path), "--trade-date", "20260820"],
        now_shanghai=datetime(2026, 8, 20, 21, 15, tzinfo=sync.SHANGHAI),
    ) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "not_applicable"
    assert stdout["reason"] == "outside_active_minute_window"
    assert stdout["network_requests"] == 0
    assert stdout["market_data_network_requests"] == 0
    assert stdout["market_data_files_written"] == 0
    assert stdout["filesystem_writes"] == 0
    assert list(tmp_path.iterdir()) == []


def test_post_close_truth_success_requests_only_current_minutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(
        is_open=1,
        minutes={"600000.SH": _minute(), "000001.SZ": _minute()},
    )
    assert _run_at(
        tmp_path,
        monkeypatch,
        client,
        hour=21,
        minute=15,
        extra_args=("--post-close-truth",),
    ) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "success"
    assert stdout["reason"] == "post_close_truth_success"
    assert stdout["active_window"] is False
    assert stdout["post_close_truth"] is True
    assert stdout["minute_files_written"] == 2
    assert client.auction_calls == 0
    assert sorted(client.minute_calls) == ["000001.SZ", "600000.SH"]


def test_post_close_truth_partial_has_explicit_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(
        is_open=1,
        minutes={"600000.SH": _minute(), "000001.SZ": pd.DataFrame()},
    )
    assert _run_at(
        tmp_path,
        monkeypatch,
        client,
        hour=21,
        minute=15,
        extra_args=("--post-close-truth",),
    ) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "partial_success"
    assert stdout["reason"] == "post_close_truth_partial"
    assert stdout["minute_files_written"] == 1
    assert stdout["market_data_network_requests"] == 2
    assert stdout["market_data_files_written"] == 1
    assert client.auction_calls == 0


def test_post_close_truth_zero_valid_rows_fails_hard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(is_open=1)
    assert _run_at(
        tmp_path,
        monkeypatch,
        client,
        hour=21,
        minute=15,
        extra_args=("--post-close-truth",),
    ) == 1

    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "fail"
    assert failure["reason"] == "no_valid_minute_rows_post_close_truth"
    assert client.auction_calls == 0
    assert sorted(client.minute_calls) == ["000001.SZ", "600000.SH"]
    assert not (
        tmp_path / "data" / "market" / "minute_1m" / "sync_latest.json"
    ).exists()


def test_post_close_truth_closed_session_is_not_applicable_without_market_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(is_open=0)
    assert _run_at(
        tmp_path,
        monkeypatch,
        client,
        hour=21,
        minute=15,
        extra_args=("--post-close-truth",),
    ) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "not_applicable"
    assert stdout["reason"] == "exchange_closed"
    assert client.auction_calls == 0
    assert client.minute_calls == []


def test_post_close_truth_historical_date_is_not_applicable_without_market_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(is_open=1)
    assert _run_at(
        tmp_path,
        monkeypatch,
        client,
        hour=21,
        minute=15,
        trade_date="20260819",
        extra_args=("--post-close-truth",),
    ) == 0

    stdout = json.loads(capsys.readouterr().out)
    assert stdout["status"] == "not_applicable"
    assert stdout["reason"] == "non_current_trade_date"
    assert client.auction_calls == 0
    assert client.minute_calls == []
