"""Synthetic natural-clock/P0 fixtures and real unchanged native economic paths.

No future real outcomes, network, fitting or source-authority fixtures. The fixed
999666 model is used to make the test snapshot. Only fixture envelope/calendar
and P0 registration hashes/index are explicitly patched, never the label kernel.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket

import pytest

from work.profit_1000_upgrade import candidate_natural_outcomes as m
from work.profit_1000_upgrade.candidate_natural_forward_test import setup_case, run as freeze
from work.profit_1000_upgrade.test_candidate_d_source_adapter import update_source
from work.profit_1000_upgrade.test_labels import daily


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def source_pair(case, code, *, kind="normal", amount=20_000_000):
    root, t = case["roots"][code], case["t"]
    row = [code, t, 10, 2_000_000, amount, 10]
    if kind == "capacity": row[3:5] = [100_000, 1_000_000]
    if kind == "zero_auction": row[2:5] = [None, 0, 0]
    if kind == "price_conflict": row[2] = 10.01
    if kind == "opening_limit": row[2:5] = [11, 2_000_000, 22_000_000]
    if kind == "wrong_preclose": row[5] = 0
    data = {"fields": list(m.labels.auction_truth.FIELDS), "items": [] if kind in ("empty", "suspended") else [row],
        "has_more": False, "count": 0}
    raw = json.dumps({"code": 2002 if kind == "denied" else 0,
        "msg": "permission denied" if kind == "denied" else "", "detail": "..." if kind != "denied" else "",
        "data": None if kind == "denied" else data}).encode()
    request = m.labels.auction_truth.request_contract(t, None if kind == "full_market_request" else code)
    data_raw, meta_raw = m.auction_http_v3.source_bytes(raw, t, request=request,
        fetched_at_utc=datetime.strptime(t, "%Y%m%d").strftime("%Y-%m-%dT08:00:00Z"), network_request_performed=True)
    for path, body in zip(m.labels.auction_truth.source_paths(root, t), (data_raw, meta_raw)):
        write(path, body)


def minute_pair(case, code, day, *, price=9.8):
    minute = m.labels.minute_truth
    fields = list(minute.FIELDS)
    rows = [{"ts_code": code, "trade_time": stamp, "open": price, "close": price,
        "high": price, "low": price, "vol": 100, "amount": 1000}
        for stamp in minute._minute.expected_bar_ends(day)]
    raw = json.dumps({"code": 0, "data": {"fields": fields,
        "items": [[row[key] for key in fields] for row in rows], "count": 0, "has_more": False}}).encode()
    pair = minute.source_bytes(raw, day, code, request_params=minute.request_parameters(day, code),
        fetched_at_utc=datetime.strptime(day, "%Y%m%d").strftime("%Y-%m-%dT08:00:00Z"))
    for path, body in zip(minute.paths(case["roots"][code], day, code), pair):
        write(path, body)


def refresh(case, asof=None):
    asof = asof or case["asof"]
    for code, root in case["roots"].items():
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                # Fixture-only inventory. Runtime never scans or guesses files.
                date = relative.split("/")[-2]
                if date <= asof:
                    entries.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        case["bundle"]["by_code"][code] = {"source_root": str(root), "bindings": entries}


def make_case(tmp_path, monkeypatch, *, size=2, kind="normal"):
    base = setup_case(tmp_path, monkeypatch, size=size)
    calendar_path = base["root"] / m.labels.settlement.CALENDAR_PATH
    calendar_raw = calendar_path.read_bytes() + b"SSE,20260917,1\n"
    calendar_sha = update_source(base, str(m.labels.settlement.CALENDAR_PATH), calendar_raw)
    receipt = freeze(base)
    snapshot = json.loads(Path(receipt["snapshot_path"]).read_bytes())
    monkeypatch.setattr(m, "REGISTRATION_SHA", snapshot["registration_sha256"])
    monkeypatch.setattr(m.labels.settlement, "CALENDAR_SHA256", calendar_sha)
    codes = sorted({slot["ts_code"] for key in ("candidate_slots", "promotion_slots")
        for slot in snapshot[key] if slot["ts_code"]})
    roots = {code: tmp_path.resolve() / ("prices_" + code.replace(".", "_")) for code in codes}
    for root in roots.values(): root.mkdir()
    case = {"base": base, "snapshot": Path(receipt["snapshot_path"]), "snapshot_sha": receipt["snapshot_file_sha256"],
        "frozen": snapshot, "roots": roots, "codes": codes, "day": snapshot["signal_date"],
        "t": snapshot["exec_date"], "t1": snapshot["exit_date"], "asof": snapshot["exit_date"],
        "output": tmp_path.resolve() / "candidate_natural_outcomes",
        "bundle": {"calendar": {"origin_path": str(calendar_path), "sha256": calendar_sha}, "by_code": {}}}
    for index, code in enumerate(codes):
        selected_kind = kind if index == 0 else "normal"
        daily(roots[code], case["t"], code=code,
            price=11 if selected_kind == "opening_limit" else 10,
            volume=0 if selected_kind == "suspended" else 24000)
        daily(roots[code], case["t1"], code=code, price=9.8)
        if selected_kind != "missing_auction": source_pair(case, code, kind=selected_kind)
        if selected_kind != "missing_minutes": minute_pair(case, code, case["t1"])
    refresh(case)
    return case


def run(case, **options):
    return m.evaluate_natural_outcomes(case["snapshot"], case["output"],
        expected_snapshot_sha256=options.pop("expected_snapshot_sha256", case["snapshot_sha"]),
        as_of_date=options.pop("as_of_date", case["asof"]), source_bundle=case["bundle"],
        clock=options.pop("clock", lambda: datetime(2026, 9, 17, 8, tzinfo=timezone.utc)), **options)


def report(receipt):
    return json.loads(Path(receipt["ledger_path"]).read_bytes())["versions"][-1]


@pytest.mark.parametrize("size", [0, 1, 2, 10, 13])
def test_full_frozen_N_and_exact_four_slots_native_singleton_price_loss(tmp_path, monkeypatch, size):
    case = make_case(tmp_path, monkeypatch, size=size)
    before = case["snapshot"].read_bytes()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NETWORK"))
    original = m.labels.build_labels; seen = []
    def native(root, manifest, **options):
        assert len(manifest["rows"]) == 1
        assert manifest["feature_columns"] == ["board_stage"]
        assert set(manifest["rows"][0]["features"]) == {"board_stage"}
        assert manifest["rows"][0]["shadow_max_price"] is None
        assert m.labels._binding(root, manifest["source_bindings"][0])[1] == before
        value = original(root, manifest, **options); seen.append(deepcopy(value)); return value
    monkeypatch.setattr(m.labels, "build_labels", native)
    receipt = run(case); out = report(receipt)
    assert out["full_frozen_candidate_count"] == min(10, size)
    assert out["full_frozen_prediction"] == case["frozen"]["prediction"]
    assert len(seen) == len(case["codes"])
    assert list(out["native_singleton_reports"].values()) == seen
    assert case["snapshot"].read_bytes() == before
    for key in ("candidate_slots", "promotion_slots"):
        assert len(out[key]) == 2
        for slot in out[key][:min(size, 2)]:
            assert slot["status"] == m.labels.SETTLED
            assert slot["slot_net_return"] == pytest.approx(-.0245)
            assert slot["slot_net_return"] == slot["net_return"] == slot["conditional_net_return"]
        for slot in out[key][min(size, 2):]:
            assert slot["status"] == "MISSING_CANDIDATE" and slot["slot_net_return"] is None
    for value in seen:
        assert value["historical_counterfactual"] is True
        assert value["rows"][0]["promotion_rank"] == next(r["promotion_rank"] for r in case["frozen"]["prediction"]["rows"]
            if r["ts_code"] == value["rows"][0]["ts_code"])
        assert value["feature_evidence_kind"] == "RETROSPECTIVE_D_ONLY_RECONSTRUCTION"
    assert out["native_report_scope"] == "ONE_STOCK_NOT_FULL_N_TRAINING_COHORT"
    assert out["natural_forward_admission_issued"] is out["production_activation_allowed"] is False
    assert out["clock_mode"] == out["frozen_clock_mode"] == "INJECTED_TEST_CLOCK_RESEARCH_ONLY"


@pytest.mark.parametrize("kind,status", [
    ("capacity", "NO_FILL_CAPACITY"), ("zero_auction", "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME"),
    ("suspended", "NO_FILL_SUSPENDED"), ("opening_limit", "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"),
    ("missing_auction", "PENDING_T_MISSING_CANONICAL_AUCTION"),
    ("missing_minutes", "PENDING_EXIT_MISSING_MINUTES"),
    ("price_conflict", "PENDING_ENTRY_PRICE_DAILY_OPEN_CONFLICT"),
    ("wrong_preclose", "PENDING_ENTRY_INVALID_PRE_CLOSE")])
def test_native_no_fill_and_missing_truth_never_conflated(tmp_path, monkeypatch, kind, status):
    case = make_case(tmp_path, monkeypatch, kind=kind)
    row = report(run(case))["native_singleton_reports"][case["codes"][0]]["rows"][0]
    assert row["label_status"] == status
    assert row["net_return"] is row["conditional_net_return"] is None
    if status.startswith("NO_FILL"):
        assert row["proxy_fill"] == 0 and row["slot_net_return"] == 0
    else:
        assert row["slot_net_return"] is None


@pytest.mark.parametrize("kind", ["empty", "denied", "amount_None", "amount_mismatch"])
def test_only_native_qualified_fallback_or_unknown_amount_retains_price(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch, kind=kind)
    code = case["codes"][0]
    if kind.startswith("amount_"):
        source_pair(case, code, amount=None if kind == "amount_None" else 20_000_001)
        refresh(case)
    row = report(run(case))["native_singleton_reports"][code]["rows"][0]
    assert row["label_status"] == m.labels.SETTLED and row["slot_net_return"] == pytest.approx(-.0245)
    assert row["capacity_evidence"] == "UNKNOWN" and row["capacity_amount"] is None
    assert row["entry_price_source"] == ("DAILY_OPEN_PROXY" if kind in ("empty", "denied") else "TUSHARE_STK_AUCTION")


def test_corrupt_canonical_bytes_bound_but_never_fallback(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    code = case["codes"][0]
    _, meta = m.labels.auction_truth.source_paths(case["roots"][code], case["t"])
    meta.write_bytes(b"{broken"); refresh(case)
    row = report(run(case))["native_singleton_reports"][code]["rows"][0]
    assert row["label_status"] == "PENDING_INVALID_CANONICAL_AUCTION_SOURCE"
    assert row["entry_price"] is row["slot_net_return"] is None


def test_undeclared_complete_minutes_in_original_root_are_never_searched_or_read(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    code = case["codes"][0]
    case["bundle"]["by_code"][code]["bindings"] = [b for b in case["bundle"]["by_code"][code]["bindings"]
        if not b["path"].startswith("research_inputs/minute_truth_0931/")]
    original = m.natural._read
    def read(path, *a, **k):
        if case["roots"][code] in Path(path).parents and "minute_truth_0931" in Path(path).parts:
            pytest.fail("UNDECLARED_MINUTE_FILE_READ")
        return original(path, *a, **k)
    monkeypatch.setattr(m.natural, "_read", read)
    row = report(run(case))["native_singleton_reports"][code]["rows"][0]
    assert row["label_status"] == "PENDING_EXIT_MISSING_MINUTES" and row["slot_net_return"] is None


def test_full_market_request_is_not_rewrapped_as_singleton_authority(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, kind="full_market_request")
    with pytest.raises(ValueError, match="SINGLE_STOCK_NATIVE"):
        run(case)
    assert not case["output"].exists()


def test_future_unbound_paths_rejected_before_any_outcome_body_is_opened(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    code = case["codes"][-1]
    case["bundle"]["by_code"][code]["bindings"].append(
        {"path": "data/market/raw/2026/20260917/daily.csv", "sha256": "0" * 64})
    original = m.natural._read
    def read(path, *a, **k):
        if any(root in Path(path).parents for root in case["roots"].values()):
            pytest.fail("OUTCOME_BODY_READ_BEFORE_ALL_SCOPE_IDENTITIES")
        return original(path, *a, **k)
    monkeypatch.setattr(m.natural, "_read", read)
    with pytest.raises(ValueError, match="OUT_OF_SCOPE"):
        run(case)


@pytest.mark.parametrize("kind", ["extra_code", "missing_code", "duplicate_binding", "legacy_auction", "wrong_minute_code", "wrong_sha"])
def test_source_whitelist_and_external_binding_fail_closed(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    code = case["codes"][0]; entries = case["bundle"]["by_code"][code]["bindings"]
    if kind == "extra_code": case["bundle"]["by_code"]["600999.SH"] = deepcopy(case["bundle"]["by_code"][code])
    elif kind == "missing_code": del case["bundle"]["by_code"][code]
    elif kind == "duplicate_binding": entries.append(deepcopy(entries[0]))
    elif kind == "legacy_auction": entries.append({"path": f"data/market/raw/2026/{case['t']}/stk_auction_o.csv", "sha256": "0"*64})
    elif kind == "wrong_minute_code": entries.append({"path": f"research_inputs/minute_truth_0931/2026/{case['t1']}/600999_SH.data.json", "sha256": "0"*64})
    else: entries[0]["sha256"] = "0"*64
    with pytest.raises(ValueError): run(case)
    assert not case["output"].exists()


@pytest.mark.parametrize("kind", ["snapshot_sha", "candidate_rank", "promotion_rank", "candidate_slot", "late_freeze", "future_feature", "activation"])
def test_frozen_contract_tampering_not_a_new_choice(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    if kind == "snapshot_sha":
        with pytest.raises(ValueError, match="SNAPSHOT_SHA"):
            run(case, expected_snapshot_sha256="0"*64)
        return
    frozen = deepcopy(case["frozen"])
    if kind == "candidate_rank": frozen["prediction"]["rows"][0]["candidate_rank"] = 1.0
    elif kind == "promotion_rank": frozen["prediction"]["rows"][0]["promotion_rank"] = 2.5
    elif kind == "candidate_slot": frozen["candidate_slots"][0]["candidate_score"] = 99
    elif kind == "late_freeze": frozen["pre_cas_freeze_at_utc"] = "2026-09-15T01:25:00+00:00"
    elif kind == "future_feature": frozen["prediction"]["rows"][0]["feature_as_of_date"] = case["t"]
    else: frozen["production_activation_allowed"] = True
    frozen.pop("snapshot_sha256"); frozen["snapshot_sha256"] = m.natural.scorer.canonical_sha(frozen)
    case["snapshot_sha"] = write(case["snapshot"], m.natural.storage.encoded(frozen))
    with pytest.raises(ValueError): run(case)
    assert not case["output"].exists()


def test_asof_before_close_rejected_before_snapshot_or_prices(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_guard", lambda: ())
    monkeypatch.setattr(m.natural, "registration", lambda: ({}, m.REGISTRATION_SHA, ()))
    monkeypatch.setattr(m.natural, "_read", lambda *a, **k: pytest.fail("FUTURE_OUTCOME_READ"))
    with pytest.raises(ValueError, match="COMPLETED_SESSION"):
        m.evaluate_natural_outcomes(tmp_path, tmp_path, expected_snapshot_sha256="0"*64,
            as_of_date="20260916", source_bundle={}, clock=lambda: datetime(2026,9,16,6,59,tzinfo=timezone.utc))


def test_same_asof_external_sha_idempotent_and_future_asof_appends_unchanged_terminal(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    first = run(case); original = Path(first["ledger_path"]).read_bytes()
    with pytest.raises(ValueError, match="EXTERNAL_PRIOR"):
        run(case)
    second = run(case, expected_existing_ledger_sha256=first["ledger_file_sha256"])
    assert not second["new_version_written"] and Path(first["ledger_path"]).read_bytes() == original
    third = run(case, as_of_date="20260917", expected_existing_ledger_sha256=first["ledger_file_sha256"])
    ledger = json.loads(Path(third["ledger_path"]).read_bytes())
    assert third["new_version_written"] and third["version_count"] == 2
    assert ledger["versions"][0] == json.loads(original)["versions"][0]
    assert ledger["versions"][0]["native_singleton_reports"][case["codes"][0]]["rows"] == ledger["versions"][1]["native_singleton_reports"][case["codes"][0]]["rows"]


def test_terminal_outcome_source_change_rejected_even_when_price_same(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    first = run(case); original = Path(first["ledger_path"]).read_bytes()
    code = case["codes"][0]
    path = case["roots"][code] / f"data/market/raw/2026/{case['t']}/daily.csv"
    path.write_bytes(path.read_bytes() + b"\n"); refresh(case)
    with pytest.raises(ValueError, match="PRIOR_TERMINAL_OUTCOME_OR_SOURCE"):
        run(case, as_of_date="20260917", expected_existing_ledger_sha256=first["ledger_file_sha256"])
    assert Path(first["ledger_path"]).read_bytes() == original


def test_pending_matures_only_in_new_asof_without_replacing_old_version(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, kind="missing_minutes")
    first = run(case); before = report(first)
    minute_pair(case, case["codes"][0], case["t1"]); refresh(case)
    with pytest.raises(ValueError, match="SAME_ASOF_OUTCOME_CONFLICT"):
        run(case, expected_existing_ledger_sha256=first["ledger_file_sha256"])
    second = run(case, as_of_date="20260917", expected_existing_ledger_sha256=first["ledger_file_sha256"])
    versions = json.loads(Path(second["ledger_path"]).read_bytes())["versions"]
    assert versions[0] == before
    assert versions[1]["native_singleton_reports"][case["codes"][0]]["rows"][0]["label_status"] == m.labels.SETTLED


def test_native_limit_hold_matures_next_session_without_reimplementing_exit_math(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    code, t1, nxt = case["codes"][0], case["t1"], "20260917"
    daily(case["roots"][code], t1, code=code, price=11)
    minute_pair(case, code, t1, price=11); refresh(case)
    first = run(case)
    native = report(first)["native_singleton_reports"][code]["rows"][0]
    assert native["label_status"] == "PENDING_EXIT_LIMIT_UP_HELD" and native["slot_net_return"] is None
    daily(case["roots"][code], nxt, code=code, price=11.5, pre=11, up=12.1, down=9.9)
    minute_pair(case, code, nxt, price=11.5); refresh(case, nxt)
    second = run(case, as_of_date=nxt, expected_existing_ledger_sha256=first["ledger_file_sha256"])
    settled = report(second)["native_singleton_reports"][code]["rows"][0]
    assert settled["label_status"] == m.labels.SETTLED
    assert settled["held_limit_up_sessions"] == 1 and settled["actual_exit_date"] == nxt
    assert settled["net_return"] == pytest.approx(.1455)
    assert settled["exit_evidence"]["gross_return"] - .0045 == settled["net_return"]


def test_completed_asof_cannot_precede_future_d_freeze_timestamp(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    case["bundle"]["by_code"] = {code: {"source_root": str(root), "bindings": []}
        for code, root in case["roots"].items()}
    with pytest.raises(ValueError, match="PREDICTION_IS_IN_CLOCK_FUTURE"):
        run(case, as_of_date=case["day"], clock=lambda: datetime(2026,9,14,7,1,tzinfo=timezone.utc))


def test_source_fetch_timestamp_cannot_be_after_actual_observation_clock(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    _, path = m.labels.auction_truth.source_paths(case["roots"][case["codes"][0]], case["t"])
    value = json.loads(path.read_bytes()); value["fetched_at_utc"] = "2026-09-18T08:00:00Z"
    path.write_bytes(m.labels.auction_truth._json(value)); refresh(case)
    with pytest.raises(ValueError, match="FETCH_TIMESTAMP_IS_IN_CLOCK_FUTURE"):
        run(case)


@pytest.mark.parametrize("kind", ["source", "snapshot", "caller", "code_guard"])
def test_mid_native_call_mutation_fails_final_guard_without_output(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    original = m.labels.build_labels
    def changed(*a, **k):
        result = original(*a, **k)
        if kind == "source":
            code = case["codes"][0]; relative = case["bundle"]["by_code"][code]["bindings"][0]["path"]
            path = case["roots"][code] / relative; path.write_bytes(path.read_bytes() + b" ")
        elif kind == "snapshot": case["snapshot"].write_bytes(case["snapshot"].read_bytes() + b" ")
        elif kind == "caller": case["bundle"]["calendar"]["origin_path"] += ".changed"
        else: monkeypatch.setattr(m, "_guard", lambda: ("changed",))
        return result
    monkeypatch.setattr(m.labels, "build_labels", changed)
    with pytest.raises(ValueError, match="CHANGED"):
        run(case)
    assert not case["output"].exists()


def test_cas_conflict_preserves_existing_winner(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    original = m.natural.storage.compare_and_swap
    def conflict(path, value, expected):
        write(path, b'{"other":"winner"}')
        return original(path, value, expected)
    monkeypatch.setattr(m.natural.storage, "compare_and_swap", conflict)
    with pytest.raises(ValueError, match="CAS conflict"): run(case)
    assert json.loads((case["output"] / ("day_" + case["day"] + ".json")).read_bytes()) == {"other": "winner"}


@pytest.mark.parametrize("kind", ["source", "saved_ledger"])
def test_after_cas_change_does_not_return_a_success_receipt(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    original = m.natural.storage.compare_and_swap
    def changed(path, value, expected):
        result = original(path, value, expected)
        if kind == "source":
            code = case["codes"][0]; relative = case["bundle"]["by_code"][code]["bindings"][0]["path"]
            changed_path = case["roots"][code] / relative
        else:
            changed_path = path
        changed_path.write_bytes(changed_path.read_bytes() + b" ")
        return result
    monkeypatch.setattr(m.natural.storage, "compare_and_swap", changed)
    with pytest.raises(ValueError, match="CHANGED"): run(case)
    stored = json.loads((case["output"] / ("day_" + case["day"] + ".json")).read_bytes())
    assert stored["natural_forward_admission_issued"] is stored["source_authority_issued"] is False


def test_current_code_pin_drift_is_rejected_before_price_or_snapshot_reads(tmp_path, monkeypatch):
    monkeypatch.setitem(m.PINS, "work/profit_1000_upgrade/labels_v3.py", "0" * 64)
    with pytest.raises(ValueError, match="FROZEN_OUTCOME_DEPENDENCY_CHANGED"):
        m.evaluate_natural_outcomes(tmp_path / "absent", tmp_path, expected_snapshot_sha256="0"*64,
            as_of_date="20260916", source_bundle={})


@pytest.mark.parametrize("kind", ["wrong_name", "production", "inside_source", "symlink"])
def test_output_isolation(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    if kind == "wrong_name": case["output"] = tmp_path / "ledger"
    elif kind == "production": case["output"] = m.ROOT / "outputs" / "candidate_natural_outcomes"
    elif kind == "inside_source": case["output"] = case["roots"][case["codes"][0]] / "candidate_natural_outcomes"
    else:
        destination = tmp_path / "actual"; destination.mkdir()
        case["output"].symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError): run(case)
