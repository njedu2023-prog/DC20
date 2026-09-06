"""Fast synthetic policy tests. Importing this file never builds real labels."""
import importlib.util
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dc20_execution_v2_label_tests_subject", HERE / "build_labels.py")
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)

DATES = ["20260827", "20260828", "20260831", "20260901", "20260902"]
ROW = dict(signal_date=DATES[0], exec_date=DATES[1], scheduled_exit_date=DATES[2], ts_code="600001.SH", stage="2", promotion_rank="1", top10_members_sha256="a" * 64)


def bar(op=10, close=10, pre=10, low=9.9, high=10.1, vol=100, down=9, up=11):
    return dict(open=op, close=close, pre_close=pre, low=low, high=high, vol=vol), dict(down_limit=down, up_limit=up)


class Evidence:
    def __init__(self, values, asof=DATES[-1]):
        self.values, self.asof, self.reads = values, asof, []

    def candidate(self, d, code):
        assert d <= self.asof
        self.reads.append(d)
        return self.values.get(d, (None, None))


def run(values, asof=DATES[-1]):
    return labels.label_row(ROW, DATES, asof, Evidence(values, asof))


def test_ordinary_exit_costs_and_identity():
    row = run({DATES[1]: bar(), DATES[2]: bar(op=10.5, close=10.7, high=10.8)})
    assert row["label_status"] == "SETTLED_OPEN_PROXY"
    assert row["slot_net_return"] == pytest.approx(.05 - .0045)
    assert row["slot_net_return_stress"] == pytest.approx(.05 - .009)
    assert row["conditional_net_return"] == row["slot_net_return"]
    assert row["label_available_date"] == DATES[2]
    assert {k: row[k] for k in ROW} == ROW
    assert not row["actual_order_fill_observed"]


def test_open_at_up_limit_is_not_bought_even_when_later_opens():
    row = run({DATES[1]: bar(op=11, high=11, low=10, close=10.5)})
    assert row["label_status"] == "NO_FILL_OPEN_LIMIT_UP_PROXY"
    assert row["slot_net_return"] == row["slot_net_return_stress"] == 0
    assert row["conditional_net_return"] is None
    assert row["label_available_date"] == DATES[1]


def test_explicit_zero_volume_is_cash_but_missing_is_unknown():
    row = run({DATES[1]: bar(op=0, close=0, low=0, high=0, vol=0)})
    assert row["label_status"] == "NO_FILL_ZERO_VOLUME_PROXY"
    assert row["slot_net_return"] == 0
    for evidence in ({}, {DATES[1]: (bar()[0], None)}):
        row = run(evidence)
        assert row["label_status"] == "MISSING_T_TRUTH"
        assert row["slot_net_return"] is None and row["proxy_fill"] is None


def test_delayed_exit_uses_first_eligible_open_never_later_best():
    evidence = Evidence({DATES[1]: bar(), DATES[2]: bar(op=9, low=9, close=9.5, high=10), DATES[3]: bar(op=9.6, pre=9.5, close=9.7, low=9.3, high=10, down=8.55, up=10.45), DATES[4]: bar(op=10.4, close=10.6, high=11)})
    row = labels.label_row(ROW, DATES, DATES[-1], evidence)
    assert row["actual_exit_date"] == row["label_available_date"] == DATES[3]
    assert row["blocked_exit_sessions"] == 1
    assert row["slot_net_return"] == pytest.approx(-.04 - .0045)
    assert DATES[4] not in evidence.reads


def test_suspension_with_explicit_bar_delays_but_absent_row_does_not():
    row = run({DATES[1]: bar(), DATES[2]: bar(vol=0), DATES[3]: bar(op=10.2, high=10.3)})
    assert row["label_status"] == "SETTLED_OPEN_PROXY" and row["blocked_exit_sessions"] == 1
    row = run({DATES[1]: bar(), DATES[3]: bar(op=10.2, high=10.3)})
    assert row["label_status"] == "MISSING_EXIT_TRUTH" and row["slot_net_return"] is None
    assert row["missing_evidence_date"] == DATES[2]


def test_blocked_asof_and_future_poison_stay_unknown():
    before = {DATES[1]: bar(), DATES[2]: bar(op=9, low=9, close=9.5, high=10)}
    row = run(before, asof=DATES[2])
    poisoned = run(dict(before, **{DATES[3]: bar(op=1000000)}), asof=DATES[2])
    assert row == poisoned
    assert row["label_status"] == "UNRESOLVED_EXIT" and row["slot_net_return"] is None
    assert row["label_available_date"] is None


def test_future_entry_and_exit_are_pending_without_future_reads():
    assert run({}, DATES[0])["label_status"] == "PENDING_T"
    row = run({DATES[1]: bar()}, DATES[1])
    assert row["label_status"] == "PENDING_T1" and row["slot_net_return"] is None


def test_reference_discontinuity_cannot_be_called_profit():
    row = run({DATES[1]: bar(), DATES[2]: bar(op=5.1, close=5.2, pre=5, high=5.3, low=5, up=5.5, down=4.5)})
    assert row["label_status"] == "CORPORATE_ACTION_UNRESOLVED"
    assert row["slot_net_return"] is None


def test_zero_volume_cannot_bridge_an_unexplained_price_jump():
    row = run({DATES[1]: bar(), DATES[2]: bar(vol=0, close=12), DATES[3]: bar(op=12.1, close=12.3, pre=12, low=12, high=12.5, up=13.2, down=10.8)})
    assert row["label_status"] == "CORPORATE_ACTION_UNRESOLVED"
    assert row["slot_net_return"] is None and row["actual_exit_date"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, None])
def test_invalid_volume_is_not_zero_cash(value):
    row = run({DATES[1]: bar(vol=value)})
    assert row["label_status"] == "INVALID_T_TRUTH" and row["slot_net_return"] is None


def test_invalid_bar_is_not_valid_execution():
    row = run({DATES[1]: bar(op=11.01)})
    assert row["label_status"] == "INVALID_T_TRUTH"


def test_calendar_checks_no_weekend_or_duplicate_or_padded_scope():
    labels.validate_identities([ROW], DATES)
    with pytest.raises(ValueError, match="adjacent"):
        labels.validate_identities([dict(ROW, scheduled_exit_date="20260829")], DATES)
    with pytest.raises(ValueError, match="duplicate"):
        labels.validate_identities([ROW, ROW], DATES)
    with pytest.raises(ValueError, match="rank"):
        labels.validate_identities([dict(ROW, promotion_rank="2")], DATES)


def market_file(root, text):
    path = root / "data/market/raw/2026/20260828/daily.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_exact_date_and_duplicate_market_rows_rejected(tmp_path):
    path = market_file(tmp_path, "trade_date,ts_code\n20260827,600001.SH\n")
    with pytest.raises(ValueError, match="wrong date"):
        labels.MarketEvidence(tmp_path, DATES[-1]).rows(DATES[1], "daily")
    path.write_text("trade_date,ts_code\n20260828,600001.SH\n20260828,600001.SH\n")
    with pytest.raises(ValueError, match="duplicate"):
        labels.MarketEvidence(tmp_path, DATES[-1]).rows(DATES[1], "daily")


def test_market_read_binds_sha_and_never_reads_future(tmp_path):
    path = market_file(tmp_path, "trade_date,ts_code\n20260828,600001.SH\n")
    source = labels.MarketEvidence(tmp_path, DATES[1])
    assert source.rows(DATES[1], "daily")["600001.SH"]["trade_date"] == DATES[1]
    assert source.bindings[str(path.relative_to(tmp_path))] == labels.sha(path)
    with pytest.raises(ValueError, match="future"):
        source.rows(DATES[2], "daily")


def test_inputs_and_outputs_cannot_escape_or_follow_symlinks(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="unsafe"):
        labels.safe_file(tmp_path, "../production.json")
    (tmp_path / "bad").symlink_to(HERE / "PLAN.json")
    with pytest.raises(ValueError, match="symlink"):
        labels.safe_file(tmp_path, "bad")
    research = tmp_path / "research"
    research.mkdir()
    (research / "outputs").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(labels, "HERE", research)
    with pytest.raises(ValueError, match="symlink"):
        labels.output_directory()


def test_duplicate_locations_cannot_silently_select_conflicting_truth(tmp_path):
    market_file(tmp_path, "trade_date,ts_code\n20260828,600001.SH\n")
    second = tmp_path / "data/market/raw/daily_20260828.csv"
    second.write_text("trade_date,ts_code\n20260828,600002.SH\n")
    with pytest.raises(ValueError, match="conflicting"):
        labels.MarketEvidence(tmp_path, DATES[-1]).rows(DATES[1], "daily")


def test_output_cannot_overwrite_a_hardlinked_production_file(tmp_path, monkeypatch):
    research = tmp_path / "research"
    research.mkdir()
    monkeypatch.setattr(labels, "HERE", research)
    protected = tmp_path / "production.json"
    protected.write_text("unchanged")
    target = labels.output_directory() / "label_manifest.json"
    os.link(protected, target)
    with pytest.raises(ValueError, match="aliased"):
        labels.write_bytes(target, b"changed")
    assert protected.read_text() == "unchanged"
