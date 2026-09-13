"""Synthetic file contracts (P0 checker explicitly stubbed), plus opt-in real P0.

Synthetic tests replace ONLY the frozen history hash/count with their fabricated
ledger and use a declared P0 index stub; they are not actual source admission.
The opt-in smoke uses all actual files and the unmodified P0 checker, never fit
or score, and never rewrites an old D into a future signal.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import socket
from types import MappingProxyType

import pytest

from work.profit_1000_upgrade import candidate_d_source_adapter as a
from work.profit_1000_upgrade.test_candidate_d_feature_projection import fixture, Trap


def csv_bytes(rows, keys):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(keys))
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode()


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def make_case(tmp_path, monkeypatch, *, size=2, missing_bar=False):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    inputs, options = fixture(size)
    runtime, contract, index, history, bars = inputs
    day = options["signal_date"]
    dates = [(datetime.strptime(day, "%Y%m%d") - timedelta(days=20-i)).strftime("%Y%m%d") for i in range(21)]
    if not history:
        history = [{"signal_date": "20260814", "ts_code": "600999.SH", "promotion_hit": 0}]
    history_body = gzip.compress(csv_bytes(history, ("signal_date", "ts_code", "promotion_hit")), mtime=0)
    history_sha = write(root / a.HISTORY_PATH, history_body)
    monkeypatch.setattr(a, "HISTORY_SHA", history_sha)
    monkeypatch.setattr(a, "HISTORY_ROWS", len(history))
    calendar_dates = ["20260814", *dates, contract["exec_date"], contract["exit_date"]]
    calendar_sha = write(root / a.CALENDAR_PATH, csv_bytes(
        [{"exchange": "SSE", "cal_date": d, "is_open": "1"} for d in calendar_dates], ("exchange", "cal_date", "is_open")))
    registered = []
    for i, d in enumerate(dates):
        values = [{"trade_date": d, "ts_code": row["ts_code"], "close": 10+i/10, "vol": 100+i,
            "pre_close": "THIS_IS_NOT_USED", "future_outcome": "NOT_READ"} for row in runtime[:min(10, size)]]
        if missing_bar and i == 3 and values:
            values.pop(0)
        rel = f"data/market/raw/{d[:4]}/{d}/daily.csv"
        digest = write(root / rel, csv_bytes(values, ("trade_date", "ts_code", "close", "vol", "pre_close", "future_outcome")))
        record = {"table": "daily", "trade_date": d, "path": rel, "sha256": digest}
        if d == day:
            record.update(row_count=len(values), date_scoped=True)
        else:
            record.update(git_blob_sha1=a._git_blob((root/rel).read_bytes()), git_mode="100644")
        registered.append(record)
    meta_path = f"data/market/raw/{day[:4]}/{day}/_sync_meta.json"
    meta = {"trade_date": day, "source_repo": {"owner": "example", "repo": "source", "resolved_commit": "a"*40},
        "files": [{"name": "daily", "sha256": registered[-1]["sha256"],
            "dated_path": registered[-1]["path"], "bytes": (root / registered[-1]["path"]).stat().st_size}]}
    meta_sha = write(root / meta_path, json_bytes(meta))
    receipt = {"signal_date": day, "inputs": {
        "calendar": {"path": a.CALENDAR_PATH, "sha256": calendar_sha, "runtime_context_dates": dates,
            "historical_dates": dates[:-1], "historical_session_count": 20, "signal_date_is_open": True},
        "market": {"meta_path": meta_path, "meta_sha256": meta_sha, "resolved_commit": "a"*40,
            "source_repository": "example/source", "tables": {"daily": deepcopy(registered[-1])}},
        "runtime_prior_ledger": {"path": a.HISTORY_PATH, "sha256": history_sha},
        "committed_history_context": {"dates": dates[:-1], "files": deepcopy(registered[:-1])},
        "runtime_consumed_market_files": registered,
        "promotion_model": {"as_of_date": options["promotion_train_end"]}}}
    runtime_body = csv_bytes([{k: row[k] for k in a.RUNTIME_ID + a.RUNTIME_NUMERIC} for row in runtime], a.RUNTIME_ID + a.RUNTIME_NUMERIC)
    p0_body = {"receipt": json_bytes(receipt), "runtime_features": runtime_body,
        "three_rank_json": json_bytes(contract), "three_rank_csv": b"synthetic,not_real_P0\n"}
    expected = {k: write(root / rel, p0_body[k]) for k, rel in a._p0_paths(day).items()}
    def synthetic_checker(source_root, **paths):
        # Explicit synthetic fixture; actual opt-in test does not patch this.
        out = deepcopy(index)
        for role, rel in a._p0_paths(day).items():
            out[f"latest_{role}_sha256"] = a._digest((source_root / rel).read_bytes())
        return out
    monkeypatch.setattr(a.publisher, "build_primary_d_runtime_index", synthetic_checker)
    return {"root": root, "day": day, "expected": expected, "map": {}, "receipt": receipt,
        "contract": contract, "runtime": runtime, "history": history, "index": index, "meta": meta}


def run(case):
    return a.project_bound_p0_d(case["root"], signal_date=case["day"],
        expected_p0_sha256=case["expected"], source_path_map=case["map"])


def update_receipt(case):
    rel = a._p0_paths(case["day"])["receipt"]
    case["expected"]["receipt"] = write(case["root"] / rel, json_bytes(case["receipt"]))


def update_source(case, rel, body):
    sha = write(case["root"] / rel, body)
    inp = case["receipt"]["inputs"]
    for item in inp["runtime_consumed_market_files"]:
        if item["path"] == rel:
            item["sha256"] = sha
            if "git_blob_sha1" in item: item["git_blob_sha1"] = a._git_blob(body)
    for item in inp["committed_history_context"]["files"]:
        if item["path"] == rel: item.update(sha256=sha, git_blob_sha1=a._git_blob(body))
    if rel == a.CALENDAR_PATH: inp["calendar"]["sha256"] = sha
    if rel == a.HISTORY_PATH:
        inp["runtime_prior_ledger"]["sha256"] = sha
    update_receipt(case)
    return sha


@pytest.mark.parametrize("size", [0, 1, 2, 10, 13])
def test_synthetic_exact_pool_and_topn_no_authority_or_scoring(tmp_path, monkeypatch, size):
    case = make_case(tmp_path, monkeypatch, size=size)
    before = {p: p.read_bytes() for p in case["root"].rglob("*") if p.is_file()}
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("NETWORK"))
    result = run(case)
    assert result["status"] == ("EMPTY_P0_CANDIDATE_INPUT" if size == 0 else "PROJECTED_UNVERIFIED_D_FEATURES")
    assert result["projection"]["full_pool_size"] == size
    assert len(result["projection"]["rows"]) == min(10, size)
    assert len(result["source_file_bindings"]) == 28
    assert result["registered_daily_file_count"] == 21
    assert all(row["promotion_rank"] == i+1 for i, row in enumerate(result["projection"]["rows"]))
    assert all(row["focus_pool_size"] == size for row in result["projection"]["rows"])
    for key in ("source_independently_verified", "source_authority_issued", "git_membership_verified",
        "point_in_time_availability_verified", "natural_freeze_verified", "production_activation_allowed",
        "model_artifact_loaded_or_verified", "model_training_performed", "model_predictions_computed", "scoring_authorized"):
        assert result[key] is False
    assert result["files_written"] == result["network_calls_performed"] == 0
    assert before == {p: p.read_bytes() for p in case["root"].rglob("*") if p.is_file()}
    assert type(result) is MappingProxyType and type(result["source_file_bindings"][0]) is MappingProxyType
    if size:
        assert all(result["projection"]["rows"][0][key] is None for key in a.projection.MISSING_SIGNALS)
        with pytest.raises(TypeError): result["projection"]["rows"][0]["promotion_rank"] = 99


def test_actual_observation_missing_blocks_no_price_substitution(tmp_path, monkeypatch):
    result = run(make_case(tmp_path, monkeypatch, missing_bar=True))
    assert result["status"] == "BLOCKED_DAILY_FEATURE_HISTORY"
    assert result["projection"]["diagnostics"][0]["observed_bar_count"] == 20
    assert all(result["projection"]["rows"][0][key] is None for key in a.projection.DAILY_FEATURES)


def test_real_historical_vs_D_receipt_shapes_are_distinct_not_imputed(tmp_path, monkeypatch):
    case=make_case(tmp_path,monkeypatch)
    items=case["receipt"]["inputs"]["runtime_consumed_market_files"]
    assert all("date_scoped" not in x and "row_count" not in x and "git_blob_sha1" in x for x in items[:-1])
    assert "git_blob_sha1" not in items[-1] and items[-1]["date_scoped"] is True
    before=deepcopy(case["receipt"])
    assert run(case)["registered_daily_file_count"]==21
    assert case["receipt"]==before


@pytest.mark.parametrize("mutation", ["bad_blob", "missing_mode", "extra_count", "committed_drift", "third_shape"])
def test_historical_registration_requires_original_blob_contract(tmp_path,monkeypatch,mutation):
    case=make_case(tmp_path,monkeypatch);inp=case["receipt"]["inputs"]
    row=inp["runtime_consumed_market_files"][0]
    if mutation=="bad_blob":
        row["git_blob_sha1"]="0"*40;inp["committed_history_context"]["files"][0]["git_blob_sha1"]="0"*40
    elif mutation=="missing_mode":row.pop("git_mode")
    elif mutation=="extra_count":row["row_count"]=2
    elif mutation=="committed_drift":inp["committed_history_context"]["files"][0]["sha256"]="0"*64
    else:
        row.pop("git_blob_sha1");row.pop("git_mode");row.update(row_count=2,date_scoped=True)
    update_receipt(case)
    with pytest.raises(ValueError):run(case)


def test_full_market_BJ_identity_is_not_a_candidate_or_price_input(tmp_path,monkeypatch):
    case=make_case(tmp_path,monkeypatch)
    item=case["receipt"]["inputs"]["runtime_consumed_market_files"][0]
    rows=a._csv((case["root"]/item["path"]).read_bytes(),("trade_date","ts_code","close","vol"))
    rows.append({"trade_date":item["trade_date"],"ts_code":"920001.BJ","close":"NOT_A_PRICE","vol":"NOT_A_VOLUME"})
    update_source(case,item["path"],csv_bytes(rows,("trade_date","ts_code","close","vol")))
    result=run(case)
    assert result["status"]=="PROJECTED_UNVERIFIED_D_FEATURES" and result["projection"]["full_pool_size"]==2
    assert all(r["ts_code"].endswith(".SH") for r in result["projection"]["rows"])


def test_BJ_cannot_enter_runtime_pool(tmp_path,monkeypatch):
    case=make_case(tmp_path,monkeypatch);case["runtime"][0]["ts_code"]="920001.BJ"
    role="runtime_features";rel=a._p0_paths(case["day"])[role]
    case["expected"][role]=write(case["root"]/rel,csv_bytes(
        [{k:r[k] for k in a.RUNTIME_ID+a.RUNTIME_NUMERIC} for r in case["runtime"]],a.RUNTIME_ID+a.RUNTIME_NUMERIC))
    with pytest.raises(ValueError,match="STOCK_CODE"):run(case)


@pytest.mark.parametrize("kind", ["trade_date","commit","daily_sha","daily_bytes","duplicate_daily"])
def test_original_meta_never_downgraded_to_unbound_information(tmp_path,monkeypatch,kind):
    case=make_case(tmp_path,monkeypatch);meta=case["meta"]
    if kind=="trade_date":meta["trade_date"]="20260915"
    elif kind=="commit":meta["source_repo"]["resolved_commit"]="0"*40
    elif kind=="daily_sha":meta["files"][0]["sha256"]="0"*64
    elif kind=="daily_bytes":meta["files"][0]["bytes"]+=1
    else:meta["files"].append(deepcopy(meta["files"][0]))
    market=case["receipt"]["inputs"]["market"]
    market["meta_sha256"]=write(case["root"]/market["meta_path"],json_bytes(meta));update_receipt(case)
    with pytest.raises(ValueError):run(case)


@pytest.mark.parametrize("kind", ["not_open","duplicate","wrong_exchange","wrong_future_T"])
def test_calendar_is_checked_not_only_hashed(tmp_path,monkeypatch,kind):
    case=make_case(tmp_path,monkeypatch)
    rows=a._csv((case["root"]/a.CALENDAR_PATH).read_bytes(),("exchange","cal_date","is_open"))
    if kind=="not_open":rows[-3]["is_open"]="0"
    elif kind=="duplicate":rows[-1]=deepcopy(rows[0])
    elif kind=="wrong_exchange":rows[0]["exchange"]="SZSE"
    else:rows[-2]["cal_date"]="20260917"
    update_source(case,a.CALENDAR_PATH,csv_bytes(rows,("exchange","cal_date","is_open")))
    with pytest.raises(ValueError):run(case)


def test_explicit_original_blob_mapping_is_read_only_and_keeps_receipt_sha(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    rel = case["receipt"]["inputs"]["runtime_consumed_market_files"][-1]["path"]
    original = tmp_path.resolve() / "original.csv"
    original.write_bytes((case["root"] / rel).read_bytes())
    (case["root"] / rel).write_bytes(b"changed mirror\n")
    with pytest.raises(ValueError, match="SHA_MISMATCH"): run(case)
    case["map"][rel] = original
    result = run(case)
    mapped = [x for x in result["source_file_bindings"] if x["explicit_original_blob_mapping"]]
    assert len(mapped) == 1 and mapped[0]["sha256"] == case["receipt"]["inputs"]["runtime_consumed_market_files"][-1]["sha256"]
    assert (case["root"] / rel).read_bytes() == b"changed mirror\n"


@pytest.mark.parametrize("kind", ["wrong_sha", "missing_role", "extra_role", "bool_hash", "redated_D"])
def test_external_four_file_contract_fail_closed(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    if kind == "wrong_sha": case["expected"]["receipt"] = "0"*64
    elif kind == "missing_role": case["expected"].pop("receipt")
    elif kind == "extra_role": case["expected"]["extra"] = "0"*64
    elif kind == "bool_hash": case["expected"]["receipt"] = True
    else: case["day"] = "20260915"
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("key", ["unregistered.csv", "../escape.csv", "/absolute.csv",
    "data/market/raw/latest/daily.csv", "outputs/decision/primary_d_receipt_20260914.json",
    "data/market/raw/2026/20260915/daily.csv"])
def test_map_never_expands_scope_or_maps_p0(tmp_path, monkeypatch, key):
    case = make_case(tmp_path, monkeypatch)
    path = tmp_path.resolve() / "extra.csv"; path.write_bytes(b"extra")
    case["map"][key] = path
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("kind", ["root_symlink", "file_symlink", "hardlink", "parent_symlink", "missing"])
def test_unsafe_or_missing_sources_block(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    rel = a._p0_paths(case["day"])["runtime_features"]; path = case["root"] / rel
    if kind == "root_symlink":
        alias = tmp_path.resolve()/"alias"; alias.symlink_to(case["root"], target_is_directory=True); case["root"] = alias
    elif kind == "parent_symlink":
        parent = tmp_path.resolve()/"parent"; parent.symlink_to(case["root"], target_is_directory=True)
        case["map"][a.HISTORY_PATH] = parent/a.HISTORY_PATH
    elif kind == "hardlink": os.link(path, tmp_path.resolve()/"alias")
    elif kind == "file_symlink":
        path.rename(path.with_suffix(".saved")); path.symlink_to(path.with_suffix(".saved"))
    else: path.unlink()
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("kind", ["missing_daily", "duplicate_daily", "future_daily", "wrong_path", "false_scoped",
    "bool_count", "wrong_count", "calendar_dates", "history_sha", "model_end", "different_market_daily"])
def test_registered_source_contract_mutations_rejected(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch); inp = case["receipt"]["inputs"]
    ds = inp["runtime_consumed_market_files"]
    if kind == "missing_daily": ds.pop()
    elif kind == "duplicate_daily": ds[-1] = deepcopy(ds[0])
    elif kind == "future_daily": ds[0]["trade_date"] = "20260915"
    elif kind == "wrong_path": ds[0]["path"] = "data/market/raw/latest/daily.csv"
    elif kind == "false_scoped": ds[0]["date_scoped"] = False
    elif kind == "bool_count": ds[0]["row_count"] = True
    elif kind == "wrong_count": ds[0]["row_count"] = 999
    elif kind == "calendar_dates": inp["calendar"]["runtime_context_dates"][-2] = "20260915"
    elif kind == "history_sha": inp["runtime_prior_ledger"]["sha256"] = "0"*64
    elif kind == "model_end": inp["promotion_model"]["as_of_date"] = case["day"]
    else: inp["market"]["tables"]["daily"]["sha256"] = "0"*64
    update_receipt(case)
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("kind", ["duplicate", "future", "wrong_code", "zero_price", "nonfinite", "missing_col"])
def test_registered_daily_original_value_errors_do_not_become_prices(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch); item=case["receipt"]["inputs"]["runtime_consumed_market_files"][0]
    rows = a._csv((case["root"]/item["path"]).read_bytes(), ("trade_date", "ts_code", "close", "vol"))
    if kind == "duplicate": rows[1]["ts_code"] = rows[0]["ts_code"]
    elif kind == "future": rows[-1]["trade_date"] = "20260915"; rows[0]["close"] = "invalid"
    elif kind == "wrong_code": rows[-1]["ts_code"] = "NOT_A_CODE"
    elif kind == "zero_price": rows[0]["close"] = "0"
    elif kind == "nonfinite": rows[0]["close"] = "NaN"
    fields = ("trade_date", "ts_code", "close", "vol") if kind != "missing_col" else ("trade_date", "ts_code", "close")
    update_source(case,item["path"],csv_bytes([{k:r[k] for k in fields} for r in rows],fields))
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("field", ["stage", "promotion_rank", "top10_selected", "focus_pool_size"])
def test_native_numeric_contract_is_not_permissive_csv_coercion(tmp_path, monkeypatch, field):
    case = make_case(tmp_path, monkeypatch)
    case["runtime"][0][field] = "1.0" if field == "promotion_rank" else "NaN"
    rel = a._p0_paths(case["day"])["runtime_features"]
    case["expected"]["runtime_features"] = write(case["root"]/rel,
        csv_bytes([{k:r[k] for k in a.RUNTIME_ID+a.RUNTIME_NUMERIC} for r in case["runtime"]], a.RUNTIME_ID+a.RUNTIME_NUMERIC))
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("where", ["runtime", "history", "bar"])
def test_whole_identity_pass_precedes_any_numeric_or_truth_value(where, monkeypatch):
    monkeypatch.setattr(a,"HISTORY_ROWS",1)
    runtime=[{"signal_date":"20260914","ts_code":"600001.SH","promotion_model_as_of_date":"20260709","stage":Trap()}]
    history=[{"signal_date":"20260814","ts_code":"600001.SH","promotion_hit":Trap()}]
    daily=[("20260914",[{"trade_date":"20260914","ts_code":"600001.SH","close":Trap()}])]
    if where == "runtime": runtime[-1]["signal_date"]="20260915"
    elif where == "history": history[-1]["signal_date"]="20260914"
    else: daily[-1][1][-1]["trade_date"]="20260915"
    with pytest.raises(ValueError): a._identities(runtime,history,daily,"20260914")


def test_native_projection_never_reads_unknown_values_or_preclose(monkeypatch):
    inputs,_=fixture(1)
    row={k:str(inputs[0][0][k]) for k in a.RUNTIME_ID+a.RUNTIME_NUMERIC}
    for key in (*a.projection.MISSING_SIGNALS,"T_price","future_outcome"):
        row[key]=Trap()
    history=[{"signal_date":"20260814","ts_code":row["ts_code"],"promotion_hit":"1","net_return":Trap()}]
    daily=[("20260914",[{"trade_date":"20260914","ts_code":row["ts_code"],"close":"10","vol":"100",
        "pre_close":Trap(),"pct_chg":Trap(),"future_outcome":Trap()}])]
    monkeypatch.setattr(a,"HISTORY_ROWS",1)
    a._identities([row],history,daily,"20260914")
    runtime,prior,bars=a._native([row],history,daily,inputs[1])
    assert not set(a.projection.MISSING_SIGNALS)&set(runtime[0])
    assert prior==[{"signal_date":"20260814","ts_code":row["ts_code"],"promotion_hit":1.}]
    assert bars[row["ts_code"]]==[{"trade_date":"20260914","ts_code":row["ts_code"],"close":10.,"volume":100.}]


def test_end_guard_rechecks_mutable_external_hash_map_after_code_guard(tmp_path,monkeypatch):
    case=make_case(tmp_path,monkeypatch);original=a._code_guard;calls=[]
    def guarded():
        result=original();calls.append(1)
        if len(calls)==2:case["expected"]["receipt"]="e"*64
        return result
    monkeypatch.setattr(a,"_code_guard",guarded)
    with pytest.raises(ValueError,match="CALLER_SOURCE_ARGUMENTS_CHANGED"):run(case)


def test_missing_mapped_original_is_not_replaced_by_available_current_source(tmp_path,monkeypatch):
    case=make_case(tmp_path,monkeypatch)
    case["map"][a.HISTORY_PATH]=tmp_path.resolve()/"not-present.gz"
    with pytest.raises(ValueError,match="MISSING_REGISTERED_SOURCE"):run(case)


@pytest.mark.parametrize("mutation", ["source", "same_bytes_new_inode", "expected", "map", "extra_p0_date"])
def test_late_projection_mutations_fail_closed(tmp_path, monkeypatch, mutation):
    case=make_case(tmp_path,monkeypatch); original=a.projection.project_candidate_d_features
    def wrapped(*args,**kwargs):
        result=original(*args,**kwargs)
        if mutation in ("source","same_bytes_new_inode"):
            target=case["root"]/a.HISTORY_PATH
            body=target.read_bytes(); target.unlink(); target.write_bytes(body if mutation=="same_bytes_new_inode" else b"changed")
        elif mutation=="expected": case["expected"]["receipt"]="f"*64
        elif mutation=="map": case["map"][a.HISTORY_PATH]=case["root"]/a.HISTORY_PATH
        else: case["day"]="20260915"
        return result
    monkeypatch.setattr(a.projection,"project_candidate_d_features",wrapped)
    # signal_date is immutable and passed by value; changing a test wrapper's
    # unrelated dict is not an argument mutation and cannot change returned D.
    if mutation=="extra_p0_date": assert run(case)["signal_date"]=="20260914"
    else:
        with pytest.raises(ValueError): run(case)


def test_frozen_code_hash_drift_rejected(tmp_path,monkeypatch):
    case=make_case(tmp_path,monkeypatch)
    monkeypatch.setattr(a,"SELF_SHA","0"*64)
    with pytest.raises(ValueError,match="FROZEN_CODE_SHA_CHANGED"):run(case)


def test_actual_three_rank_validator_is_explicitly_pinned():
    relative="src/top10decision/decision/three_rank.py"
    assert a.CODE_PINS[relative]=="f39196f352c2b110c3aa2a4e2f94730d1bdded3a0d70f107ae577700fe577d3a"
    assert a.publisher.validate_three_rank_contract is a.three_rank.validate_three_rank_contract
    assert a.publisher.validate_three_rank_contract.__code__.co_filename==str(a.CODE_ROOT/relative)


@pytest.mark.parametrize("late", [False,True])
@pytest.mark.parametrize("mutation", ["code_bytes","module_origin","bound_function","both_function_aliases"])
def test_actual_three_rank_dependency_closed_at_start_and_end(tmp_path,monkeypatch,late,mutation):
    case=make_case(tmp_path,monkeypatch)
    original_read=a._read
    def mutate():
        if mutation=="code_bytes":
            def changed_read(path):
                body,identity=original_read(path)
                if Path(path)==a.CODE_ROOT/"src/top10decision/decision/three_rank.py":body+=b"\n# synthetic drift\n"
                return body,identity
            monkeypatch.setattr(a,"_read",changed_read)
        elif mutation=="module_origin":monkeypatch.setattr(a.three_rank,"__file__",str(tmp_path.resolve()/"three_rank.py"))
        else:
            def fake_validator(value):return None
            monkeypatch.setattr(a.publisher,"validate_three_rank_contract",fake_validator)
            if mutation=="both_function_aliases":monkeypatch.setattr(a.three_rank,"validate_three_rank_contract",fake_validator)
    if late:
        original=a.projection.project_candidate_d_features
        def wrapped(*args,**kwargs):
            result=original(*args,**kwargs);mutate();return result
        monkeypatch.setattr(a.projection,"project_candidate_d_features",wrapped)
    else:mutate()
    with pytest.raises(ValueError,match="FROZEN_CODE_SHA_CHANGED|DEPENDENCY_IMPORT_ORIGIN_CHANGED|ACTUAL_THREE_RANK_VALIDATOR_ORIGIN_CHANGED"):
        run(case)


@pytest.mark.parametrize("payload", [b'{"x":1,"x":2}',b'{"x":NaN}',b'[]'])
def test_strict_json(payload):
    with pytest.raises(ValueError):a._json(payload)


@pytest.mark.parametrize("payload", [b'a,a\n1,2\n',b'a,b\n1,2,3\n',b'a\n1\n'])
def test_strict_csv(payload):
    with pytest.raises(ValueError):a._csv(payload,("a","b"))


@pytest.mark.skipif(os.environ.get("DC20_RUN_BOUND_P0_SOURCE_ADAPTER_SMOKE") != "1", reason="real P0 source smoke explicitly opt-in")
def test_actual_20260911_original_blob_mapping_unmodified_checker_no_score():
    root=a.CODE_ROOT; day="20260911"
    expected={"receipt":"55d46d16c5c0dba2649744926f03a600d646a8fa6e186ee92632d30d1a895a11",
        "runtime_features":"0e65485eb5cac60a9f43e634db55ce461d78d58e1e61a2b7edba1a5973da156a",
        "three_rank_json":"7898f87a9e936ad85a83e98bf72331e846904d564a38c2d7c0b241bd89d6bf4e",
        "three_rank_csv":"5a3e5dc7f9a5a0a4556315fc8fda1307f2198eca30b9e64b0b60dc189c71f31a"}
    mapping={"data/market/raw/2026/20260911/daily.csv":Path('/private/tmp/dc20-clean-release-check.uwIS4X/upstream-daily-20260911.csv'),
        "data/market/raw/2026/20260911/_sync_meta.json":Path('/private/tmp/dc20-clean-release-check.uwIS4X/p0-published-sync-meta-20260911.json')}
    result=a.project_bound_p0_d(root,signal_date=day,expected_p0_sha256=expected,source_path_map=mapping)
    assert result["status"]=="PROJECTED_UNVERIFIED_D_FEATURES"
    p=result["projection"]
    assert p["full_pool_size"]==p["candidate_count"]==6 and p["history_row_count"]==12322
    assert p["observed_history_end"]=="20260814"
    assert [r["five_year_stock_prior_rate"] for r in p["rows"]]==[1/3,.5,5/11,5/11,3/7,.4]
    assert all(x["observed_bar_count"]==21 for x in p["diagnostics"])
    assert sum(x["explicit_original_blob_mapping"] for x in result["source_file_bindings"])==2
    assert result["source_authority_issued"] is result["production_activation_allowed"] is result["model_predictions_computed"] is False
