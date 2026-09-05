from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import sync_frozen_shadow_truth as sync


CODE = "603269.SH"
DATE = "20260831"


def frame(date=DATE):
    return pd.DataFrame([{"ts_code": CODE, "trade_date": date, "open": 10., "high": 11.,
                          "low": 9., "close": 10.5, "amount": 1000000.,
                          "vol": 10000., "vwap": 10.5, "up_limit": 11.5, "down_limit": 9.5}])


class Client:
    def __init__(self, value=None):
        self.value = frame() if value is None else value
        self.calls = []

    def opening_auction(self, date):
        self.calls.append(("stk_auction_o", date))
        return self.value.copy()

    def daily_close(self, date):
        self.calls.append(("daily", date))
        return self.value.copy()

    def daily_limits(self, date):
        self.calls.append(("stk_limit", date))
        return self.value.copy()


@pytest.fixture
def plan(monkeypatch):
    monkeypatch.setattr(sync, "required_partitions", lambda *a: {(DATE, "stk_auction_o"): {CODE}})


def test_exact_auction_endpoint_and_metadata_are_written_once(tmp_path, plan):
    client = Client()
    result = sync.sync_missing_truth(tmp_path, "20260904", client=client)
    assert client.calls == [("stk_auction_o", DATE)]
    assert result["selection_created"] is False
    assert result["existing_truth_overwritten"] is False
    assert len(result["written_paths"]) == 2
    path = tmp_path / f"data/market/raw/2026/{DATE}/stk_auction_o.csv"
    metadata_path = path.with_suffix(".meta.json")
    metadata = json.loads(metadata_path.read_text())
    assert metadata["source"] == "tushare:stk_auction_o"
    assert metadata["trade_date"] == DATE
    assert metadata["credential_persisted"] is False
    before = (path.read_bytes(), metadata_path.read_bytes())
    second = sync.sync_missing_truth(tmp_path, "20260904", client=client)
    assert second["network_requests"] == 0
    assert before == (path.read_bytes(), metadata_path.read_bytes())


@pytest.mark.parametrize("bad", [frame("20260901"), frame().drop(columns="amount"), frame().assign(ts_code="600108.SH")])
def test_wrong_date_missing_field_or_missing_frozen_code_remains_pending(tmp_path, plan, bad):
    result = sync.sync_missing_truth(tmp_path, "20260904", client=Client(bad))
    assert result["status"] == "PENDING_TRUTH"
    assert result["written_paths"] == []
    assert result["partitions"][0]["status"] == "PENDING_SOURCE_INVALID"


def test_unavailable_auction_never_uses_daily_proxy_or_leaks_error(tmp_path, plan):
    class Unavailable(Client):
        def opening_auction(self, date):
            raise RuntimeError("private_token_should_never_be_persisted")
        def daily_close(self, date):
            raise AssertionError("auction must not fall back to daily")
    result = sync.sync_missing_truth(tmp_path, "20260904", client=Unavailable())
    assert result["partitions"][0]["status"] == "PENDING_SOURCE_UNAVAILABLE"
    assert "private_token" not in json.dumps(result)
    assert result["written_paths"] == []


def test_existing_incomplete_partition_is_not_replaced(tmp_path, plan):
    path = tmp_path / f"data/market/raw/2026/{DATE}/stk_auction_o.csv"
    path.parent.mkdir(parents=True)
    path.write_text(frame().assign(ts_code="600108.SH").to_csv(index=False))
    before = path.read_bytes()
    client = Client()
    result = sync.sync_missing_truth(tmp_path, "20260904", client=client)
    assert result["partitions"][0]["status"] == "PENDING_EXISTING_INCOMPLETE_NOT_OVERWRITTEN"
    assert path.read_bytes() == before
    assert client.calls == []


@pytest.mark.parametrize("outside", [True, False])
def test_missing_partition_rejects_symlink_parent_before_fetch(tmp_path, plan, outside):
    root = tmp_path / "repo"
    root.mkdir()
    destination = tmp_path / "outside" if outside else root / "redirected"
    destination.mkdir()
    parent = root / "data/market/raw/2026"
    parent.mkdir(parents=True)
    (parent / DATE).symlink_to(destination, target_is_directory=True)
    client = Client()
    with pytest.raises(ValueError, match="unsafe parent"):
        sync.sync_missing_truth(root, "20260904", client=client)
    assert client.calls == []
    assert list(destination.iterdir()) == []


def test_daily_and_limit_exact_missing_sources_have_hash_bound_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "required_partitions", lambda *a: {(DATE, name): {CODE} for name in ("daily", "stk_limit")})
    result = sync.sync_missing_truth(tmp_path, "20260904", client=Client())
    assert result["status"] == "COMPLETE"
    for name in ("daily", "stk_limit"):
        path = tmp_path / f"data/market/raw/2026/{DATE}/{name}.csv"
        meta = json.loads(path.with_suffix(".meta.json").read_text())
        assert meta["source"] == f"tushare:{name}"
        assert meta["requested_trade_date"] == meta["trade_date"] == DATE
        assert meta["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_request_budget_is_bounded_and_unfetched_rows_remain_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "required_partitions", lambda *a: {(DATE, name): {CODE} for name in ("daily", "stk_limit")})
    result = sync.sync_missing_truth(tmp_path, "20260904", client=Client(), max_requests=1)
    assert result["network_requests"] == 1
    assert result["partitions"][1]["status"] == "PENDING_REQUEST_LIMIT"


def test_date_plan_uses_only_existing_frozen_post_cutover_due_sessions(tmp_path, monkeypatch):
    days = ["20260828", "20260831", "20260901", "20260902", "20260903", "20260904", "20260907", "20260908"]
    monkeypatch.setattr(sync, "_strict_open_dates", lambda root: days)
    seen = []
    def selected(root, d):
        seen.append(d)
        i = days.index(d)
        return None, {"exec_date": days[i + 1], "exit_date": days[i + 2]}, [{"ts_code": CODE}]
    monkeypatch.setattr(sync, "load_selection", selected)
    selections = tmp_path / "data/decision_executable_profit/forward/selections"
    selections.mkdir(parents=True)
    for d in ("20260825", "20260828", "20260903", "20260904"):
        (selections / f"shadow_{d}.json").write_text("{}")
    result = sync.required_partitions(tmp_path, "20260904")
    assert "20260825" not in seen
    assert all(date <= "20260904" for date, name in result)
    assert ("20260831", "stk_auction_o") in result
    assert ("20260904", "stk_auction_o") in result
    assert ("20260907", "stk_auction_o") not in result
    assert ("20260901", "daily") in result
    assert all(codes == {CODE} for codes in result.values())
