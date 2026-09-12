from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import build_compact_statistics_window as window

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 18, 9, tzinfo=timezone.utc)
DATES = ["20260909", "20260910", "20260911", "20260914", "20260915", "20260916", "20260917", "20260918"]


def put(root, path, value):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode()
    target.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def binding(root, path, value):
    return dict(path=path, sha256=put(root, path, value))


@pytest.fixture
def empty_root(tmp_path):
    put(tmp_path, window.CONFIG_PATH, window.CONFIG)
    exit_policy = window.settlement.EXIT_POLICY_PATH_1000.as_posix()
    put(tmp_path, exit_policy, (ROOT / exit_policy).read_bytes())
    calendar = tmp_path / window.settlement.CALENDAR_PATH
    calendar.parent.mkdir(parents=True, exist_ok=True)
    calendar.write_bytes((ROOT / window.settlement.CALENDAR_PATH).read_bytes())
    return tmp_path


@pytest.fixture
def profit_fixture(empty_root, monkeypatch):
    root = empty_root
    payloads, entries, calls = {}, [], []
    for offset, d in enumerate(DATES[:3]):
        t, t1 = DATES[offset+1:offset+3]
        inputs = {}
        for key, relative in (("mixed_projection", f"outputs/decision/executable_profit_research/projection_{d}.json"),
                              ("primary_receipt", f"outputs/decision/primary_d_receipt_{d}.json"),
                              ("runtime_features", f"outputs/decision/primary_d_runtime_features_{d}.csv")):
            inputs[key] = binding(root, relative, {})
            inputs[key]["generation_mode"] = "NATURAL"
        inputs["mixed_projection"].update(csv_path=f"outputs/decision/executable_profit_research/projection_{d}.csv")
        inputs["mixed_projection"]["csv_sha256"] = put(root, inputs["mixed_projection"]["csv_path"], b"code\n")
        inputs["three_rank"] = dict(json_path=f"outputs/decision/three_rank_top10_{d}.json", csv_path=f"outputs/decision/three_rank_top10_{d}.csv")
        for kind in ("json", "csv"):
            inputs["three_rank"][kind+"_sha256"] = put(root, inputs["three_rank"][kind+"_path"], {})
        selected = [dict(shadow_slot=i, ts_code=f"60000{i}.SH", stage_transition="2→3" if i==1 else "3→4") for i in (1,2)]
        selection = dict(schema_version=window.settlement.PRIMARY_MIXED_SELECTION_SCHEMA,
            signal_date=d, exec_date=t, exit_date=t1, snapshot_sha256="a"*64, top10_members_sha256="b"*64,
            source_bindings=inputs, rows=selected)
        path = f"data/decision_executable_profit/forward/selections/shadow_{d}.json"
        entries.append(binding(root, path, selection))
        expected = window.settlement._selection_binding(root/path, selection, selected)
        payloads[d] = (root/path, selection, selected)
        truth_sources = []
        for day in (t, t1):
            for table in ("daily", "stk_limit"):
                relative = f"data/market/raw/2026/{day}/{table}.csv"
                truth_sources.append(binding(root, relative, b"ts_code,trade_date\n"))
        no_fill = d == "20260911"
        verified = dict(signal_date=d, exec_date=t, exit_date=t1, selection=expected,
            source_files=truth_sources[:2], rows=[dict(shadow_slot=i, ts_code=f"60000{i}.SH", proxy_fill=0 if i==2 and no_fill else 1) for i in (1,2)])
        t_path = f"data/decision_executable_profit/forward/verifications/t_verification_{d}.json"
        t_binding = binding(root, t_path, verified)
        entries.append(t_binding)
        rows = []
        for row in verified["rows"]:
            ret = 1.0 if d == "20260909" else 0.1 if d == "20260910" and row["shadow_slot"] == 1 else -0.05 if d == "20260910" else -0.2
            rows.append(dict(row, scheduled_exit_date=t1, actual_exit_date=t1, delayed_trading_days=0, blocked_exit_sessions=0,
                net_return_after_cost=ret if row["proxy_fill"] else None,
                stress_net_return=ret-0.0045 if row["proxy_fill"] else None,
                strategy_slot_return=ret if row["proxy_fill"] else 0.0))
        settled = dict(signal_date=d, exec_date=t, exit_date=t1, selection=expected,
            t_verification=dict(file_sha256=t_binding["sha256"]), source_files=truth_sources, rows=rows)
        entries.append(binding(root, f"data/decision_executable_profit/forward/settlements/settlement_{d}.json", settled))
    summary = dict(as_of_date="20260916", snapshot_sha256="c"*64, input_files=sorted(entries,key=lambda i:i["path"]),
        probability_diagnostics=dict(status="UNCALIBRATED", brier_score=None, expected_calibration_error=None, log_loss=None))
    summary_path = "data/decision_executable_profit/forward/statistics/snapshots/fixture.json"
    stat_binding = binding(root, summary_path, summary)
    stat_binding.update(snapshot_sha256=summary["snapshot_sha256"], as_of_date=summary["as_of_date"])
    state = dict(signal_date="20260911", source_bindings=dict(statistics=stat_binding))
    state_path = "outputs/decision/executable_profit_research/shadow_fixture.json"
    state_binding = binding(root, state_path, state)
    index = dict(latest_signal_date="20260911", latest_state_url=state_path, latest_state_sha256=state_binding["sha256"])
    put(root, window.SHADOW_INDEX, index)

    def checked(function, *args, **kwargs):
        calls.append(function.__name__)
        if function is window.settlement.load_selection:
            return payloads[args[1]]
        if function is window.settlement._validate_adjacent_dates:
            return function(*args, **kwargs)
        return None
    monkeypatch.setattr(window, "original_validator", checked)
    return root, summary, payloads, calls


def update_summary(fixture):
    root, summary, _, _ = fixture
    index = json.loads((root/window.SHADOW_INDEX).read_text())
    state = json.loads((root/index["latest_state_url"]).read_text())
    entry = state["source_bindings"]["statistics"]
    entry["sha256"] = put(root, entry["path"], summary)
    entry["as_of_date"] = summary["as_of_date"]
    index["latest_state_sha256"] = put(root, index["latest_state_url"], state)
    put(root, window.SHADOW_INDEX, index)


def run_profit(fixture):
    return window.profit_section(window.Sources(fixture[0]), "20260911", DATES, "20260918")


def test_profit_cutover_is_inclusive_and_rebases_nav_and_denominators(profit_fixture):
    result = run_profit(profit_fixture)
    assert result["recorded_days"] == 2 and result["recorded_slots"] == 4
    one, two = [result["cohorts"][f"shadow_slot_{slot}"] for slot in (1,2)]
    assert one["selected_slots"] == 2 and one["wins_after_cost"] == 1
    assert one["win_rate"] == .5 and one["mean_net_return_after_cost"] == -.05
    assert one["equal_weight_cumulative_return"] == -.12 and one["maximum_drawdown"] == -.2
    assert two["t1_settled_slots"] == 1 and two["proxy_no_fill_slots"] == 1
    assert two["win_rate"] == 0 and two["mean_net_return_after_cost"] == -.05
    assert all(row["signal_date"] >= window.START for c in result["cohorts"].values() for row in c["daily_portfolio"])
    assert "validate_t_verification" in profit_fixture[3] and "validate_t1_settlement" in profit_fixture[3]
    assert "load_selection" in profit_fixture[3] and "validate_primary_profit_forward_shadow_public_state" in profit_fixture[3]


def test_unbound_later_settlement_is_not_read_or_assumed_zero(profit_fixture):
    root, summary, _, _ = profit_fixture
    relative = "data/decision_executable_profit/forward/settlements/settlement_20260911.json"
    summary["input_files"] = [i for i in summary["input_files"] if i["path"] != relative]
    update_summary(profit_fixture)
    assert (root/relative).is_file()
    result = run_profit(profit_fixture)
    one = result["cohorts"]["shadow_slot_1"]
    assert one["pending_settlement_slots"] == 1 and one["t1_settled_slots"] == 1
    assert one["mean_net_return_after_cost"] == .1
    assert result["cohorts"]["shadow_slot_2"]["proxy_no_fill_slots"] == 1


@pytest.mark.parametrize("count", [0, 1])
def test_empty_and_short_natural_lists_are_not_padded(profit_fixture, count):
    root, summary, payloads, _ = profit_fixture
    path, selection, selected = payloads["20260911"]
    selected[:] = selected[:count]
    selection["rows"] = selected
    sha = put(root, str(path.relative_to(root)), selection)
    remaining = []
    for item in summary["input_files"]:
        if item["path"].endswith("_20260911.json"):
            if "/selections/" not in item["path"]:
                continue
            item["sha256"] = sha
        remaining.append(item)
    summary["input_files"] = remaining
    update_summary(profit_fixture)
    result = run_profit(profit_fixture)
    assert result["recorded_days"] == 2
    assert result["recorded_slots"] == 2 + count
    assert result["cohorts"]["shadow_slot_2"]["selected_slots"] == 1
    assert result["cohorts"]["shadow_slot_1"]["pending_validation_slots"] == count


@pytest.mark.parametrize("problem", ["sha", "duplicate", "retrospective", "future", "missing"])
def test_invalid_profit_sources_rejected(profit_fixture, problem):
    root, summary, payloads, _ = profit_fixture
    target = next(i for i in summary["input_files"] if "selections/shadow_20260910" in i["path"])
    if problem == "sha": target["sha256"] = "0"*64
    elif problem == "duplicate": summary["input_files"].append(dict(target))
    elif problem == "retrospective": payloads["20260910"][1]["source_bindings"]["mixed_projection"]["generation_mode"] = "RECOVERY"
    elif problem == "future": summary["as_of_date"] = "20260910"
    else: (root/target["path"]).unlink()
    update_summary(profit_fixture)
    with pytest.raises(window.WindowSourceError):
        run_profit(profit_fixture)


def test_invalid_section_is_unavailable_without_leaking_unvalidated_sources(empty_root, monkeypatch):
    path = "outputs/decision/private-unverified.json"
    put(empty_root, path, {})
    def invalid(sources, *_):
        sources.read(path)
        raise window.WindowSourceError("SOURCE_VALIDATION_FAILED")
    monkeypatch.setattr(window, "profit_section", invalid)
    payload = window.build_window(empty_root, signal_date="20260911", now=NOW)
    assert payload["profit"] == dict(status="UNAVAILABLE", reason="SOURCE_VALIDATION_FAILED")
    assert payload["promotion"]["status"] == "UNAVAILABLE"
    assert path not in [item["path"] for item in payload["source_files"]]
    window.validate_window(payload)


@pytest.mark.parametrize("error", [RuntimeError("bug"), ImportError("missing runtime")])
def test_software_faults_are_not_hidden_as_business_unavailability(empty_root, monkeypatch, error):
    def broken(*_): raise error
    monkeypatch.setattr(window, "profit_section", broken)
    with pytest.raises(type(error)):
        window.build_window(empty_root, signal_date="20260911", now=NOW)


def test_config_is_fixed_and_invalid_config_is_fatal(empty_root):
    bad = dict(window.CONFIG, start_signal_date="20260828")
    put(empty_root, window.CONFIG_PATH, bad)
    with pytest.raises(ValueError, match="configuration"):
        window.build_window(empty_root, signal_date="20260911", now=NOW)


@pytest.mark.parametrize("kind", ["duplicate", "nan", "symlink", "traversal", "changed"])
def test_sources_reject_unsafe_bytes_and_toctou(empty_root, kind):
    sources = window.Sources(empty_root)
    path = "outputs/decision/test.json"
    put(empty_root, path, {})
    with pytest.raises(window.WindowSourceError):
        if kind == "duplicate":
            put(empty_root, path, b'{"x":1,"x":2}')
            sources.json(path)
        elif kind == "nan":
            put(empty_root, path, b'{"x":NaN}')
            sources.json(path)
        elif kind == "symlink":
            (empty_root/path).unlink()
            (empty_root/path).symlink_to(empty_root/window.CONFIG_PATH)
            sources.json(path)
        elif kind == "traversal": sources.read("../outside")
        else:
            sources.read(path)
            put(empty_root, path, {"changed": True})
            sources.finish()


def test_integral_floats_use_javascript_compatible_snapshot(empty_root):
    assert window.canonical_sha256({"x": 0.0, "y": 1.0}) == window.canonical_sha256({"x": 0, "y": 1})
    payload = window.build_window(empty_root, signal_date="20260911", now=NOW)
    payload["promotion"]["reason"] = "modified"
    with pytest.raises(window.WindowSourceError, match="snapshot"):
        window.validate_window(payload)


@pytest.mark.parametrize("number", [1e-6, -1e-6, 1e-7, -1e-7, 1e-12, -1e-12, 1/3])
def test_small_return_numbers_preserve_values_across_backend_json_round_trip(number):
    # Browsers authenticate published raw bytes, not a cross-language JSON
    # reserialization. The backend's semantic snapshot remains self-checking.
    payload = window.normalize({"return": number, "zero": 0.0})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    decoded = json.loads(raw)
    assert decoded["return"] == number and type(decoded["return"]) is float
    assert window.canonical_sha256(decoded) == window.canonical_sha256(payload)


def test_real_bound_sources_read_only_and_exact_daily_cutover():
    index = json.loads((ROOT/window.SHADOW_INDEX).read_text())
    signal = index["latest_signal_date"]
    paths = [ROOT/window.SHADOW_INDEX, ROOT/window.PRIMARY_SUMMARY, ROOT/window.PRIMARY_ROWS,
             ROOT/"data/decision_executable_profit/forward/statistics/summary.json"]
    before = {path: path.read_bytes() for path in paths}
    clock = datetime.strptime(signal, "%Y%m%d").replace(tzinfo=timezone.utc) + timedelta(days=2)
    payload = window.build_window(ROOT, signal_date=signal, now=clock)
    assert payload["profit"]["status"] == "READY", payload["profit"]
    assert payload["promotion"]["status"] == "READY", payload["promotion"]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert window.SHADOW_INDEX not in {item["path"] for item in payload["source_files"]}
    assert all(item["sha256"] == hashlib.sha256((ROOT/item["path"]).read_bytes()).hexdigest() for item in payload["source_files"])
    assert [row["rank"] for row in payload["promotion"]["ranks"]] == [1,2,3]
    assert all(row["hit_rate"] is None if not row["verified"] else row["hit_rate"] == row["hits"]/row["verified"] for row in payload["promotion"]["ranks"])
    window.validate_window(payload)


@pytest.fixture
def promotion_fixture(empty_root):
    root = empty_root
    summary = json.loads((ROOT/window.PRIMARY_SUMMARY).read_text())
    paths = {window.PRIMARY_SUMMARY, window.PRIMARY_ROWS}
    for item in summary["bindings"]:
        if item["signal_date"] >= window.START and item["status"] == "PROSPECTIVE":
            paths.update(item["p0"][key] for key in ("latest_receipt_url", "latest_runtime_features_url", "latest_three_rank_json_url", "latest_three_rank_csv_url"))
    paths.update(item["path"] for item in summary["source_files"])
    for path in paths:
        target = root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/path, target)
    return root, summary


@pytest.mark.parametrize("problem", ["code", "name", "rank", "nan", "future", "status", "duplicate", "receipt_sha", "invented_hit"])
def test_promotion_bad_identity_maturity_or_frozen_binding_rejected(promotion_fixture, problem):
    root, summary = promotion_fixture
    raw = (root/window.PRIMARY_ROWS).read_text()
    reader = csv.DictReader(io.StringIO(raw))
    rows, columns = list(reader), reader.fieldnames
    row = next(row for row in rows if row["signal_date"] >= window.START)
    if problem == "code": row["ts_code"] = "999999.SH"
    elif problem == "name": row["name"] = "changed"
    elif problem == "rank": row["promotion_rank"] = "999"
    elif problem == "nan": row["continuation_limit_up_hit"] = "NaN"
    elif problem == "future": row["exec_date"] = "20990101"
    elif problem == "status": row["validation_status"] = "MADE_UP_SUCCESS"
    elif problem == "duplicate": rows.append(dict(row))
    elif problem == "invented_hit":
        row = next(row for row in rows if row["signal_date"] >= window.START and row["continuation_limit_up_hit"] in ("0", "1", "0.0", "1.0"))
        row["continuation_limit_up_hit"] = str(1-int(float(row["continuation_limit_up_hit"])))
    else:
        b = next(b for b in summary["bindings"] if b["signal_date"] >= window.START)
        b["p0"]["latest_receipt_sha256"] = "0"*64
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    summary["rows_sha256"] = put(root, window.PRIMARY_ROWS, out.getvalue().encode())
    put(root, window.PRIMARY_SUMMARY, summary)
    with pytest.raises(window.WindowSourceError):
        window.promotion_section(window.Sources(root), summary["latest_signal_date"], window.settlement._strict_open_dates(root), "20990101")
