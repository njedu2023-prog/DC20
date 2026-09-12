import hashlib
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from scripts import sync_exit_1000_minute_truth as sync
from top10decision.decision import shadow_exit_minute_truth as truth

DAY, CODE = "20260914", "000001.SZ"


def rows(day=DAY, code=CODE):
    return [dict(ts_code=code, trade_time=stamp, open=10, close=10.1,
                 high=10.2, low=9.9, vol=100, amount=1000)
            for stamp in truth.expected_bar_ends(day)]


def write_truth(root, values=None, day=DAY, code=CODE):
    raw, meta = truth.source_bytes(values if values is not None else rows(day, code), day, code,
                                  fetched_at_utc="2026-09-14T08:00:00+00:00")
    paths = truth.minute_paths(root, day, code)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    paths[0].write_bytes(raw)
    paths[1].write_bytes(meta)
    return paths


def test_exact_full_session_envelope_binds_both_files(tmp_path):
    paths = write_truth(tmp_path)
    value = truth.load_exit_minutes(tmp_path, DAY, CODE)
    assert value["schema_version"] == "dc20_exit_1000_minutes_v1"
    assert value["timestamp_semantics"] == "BAR_END"
    assert value["complete_session"] is True
    assert len(value["rows"]) == 240
    assert value["rows"][29]["bar_end"].endswith("10:00:00")
    assert value["rows"][30]["bar_end"].endswith("10:01:00")
    assert value["rows"][119]["bar_end"].endswith("11:30:00")
    assert value["rows"][120]["bar_end"].endswith("13:01:00")
    assert value["source_files"] == [{"path": p.relative_to(tmp_path).as_posix(),
                                      "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]


def test_0930_point_is_retained_in_raw_but_not_continuous_engine_rows(tmp_path):
    values = rows()
    values.append(dict(ts_code=CODE, trade_time="2026-09-14 09:30:00", open=10,
                       close=10, high=10, low=10, vol=99, amount=990))
    paths = write_truth(tmp_path, values)
    assert b"09:30:00" in paths[0].read_bytes()
    meta = json.loads(paths[1].read_bytes())
    assert meta["auction_point_0930_excluded"] is True
    assert meta["source_rows"] == 241
    assert len(truth.load_exit_minutes(tmp_path, DAY, CODE)["rows"]) == 240


@pytest.mark.parametrize("index", [0, 29, 30, 119, 120, 239])
def test_missing_any_required_bar_not_filled(index):
    values = rows()
    values.pop(index)
    with pytest.raises(ValueError, match="incomplete"):
        truth.normalize_source_rows(values, DAY, CODE)


@pytest.mark.parametrize("mutation", [
    lambda r: r.append(dict(r[0])),
    lambda r: r[0].update(ts_code="000002.SZ"),
    lambda r: r[0].update(trade_time="2026-09-15 09:31:00"),
    lambda r: r[0].update(trade_time="2026-09-14 12:00:00"),
    lambda r: r[0].update(close=float("nan")),
    lambda r: r[0].update(close=float("inf")),
    lambda r: r[0].update(low=11),
    lambda r: r[0].update(open=0),
    lambda r: r[0].update(vol=-1),
    lambda r: r[0].update(vol=0),
    lambda r: r[0].pop("amount"),
])
def test_invalid_source_is_rejected_without_cleanup(mutation):
    values = rows()
    mutation(values)
    with pytest.raises(ValueError):
        truth.normalize_source_rows(values, DAY, CODE)


def test_zero_volume_bars_are_retained():
    values = rows()
    values[29].update(open=10.1, high=10.1, low=10.1, close=10.1, vol=0, amount=0)
    result, _ = truth.normalize_source_rows(values, DAY, CODE)
    assert len(result) == 240 and result[29]["vol"] == 0


@pytest.mark.parametrize("key,value", [("timestamp_semantics", "BAR_START"), ("complete_session", False),
                                       ("trade_date", "20260915"), ("ts_code", "000002.SZ"),
                                       ("sha256", "0" * 64), ("interval_seconds", True)])
def test_metadata_drift_rejected(tmp_path, key, value):
    _, path = write_truth(tmp_path)
    meta = json.loads(path.read_bytes())
    meta[key] = value
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="metadata/SHA"):
        truth.load_exit_minutes(tmp_path, DAY, CODE)


def test_corrupt_bytes_and_orphan_pair_are_rejected(tmp_path):
    path, meta = write_truth(tmp_path)
    path.write_bytes(path.read_bytes().replace(b"10.1", b"10.15"))
    with pytest.raises(ValueError, match="SHA"):
        truth.load_exit_minutes(tmp_path, DAY, CODE)
    path.unlink()
    with pytest.raises(ValueError, match="pair"):
        truth.load_exit_minutes(tmp_path, DAY, CODE)
    meta.unlink()
    assert truth.load_exit_minutes(tmp_path, DAY, CODE) is None


def test_legacy_minute_and_symlink_cannot_be_used(tmp_path):
    legacy = tmp_path / "data/market/minute_1m/2026/20260914/000001_SZ.csv"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("not complete truth")
    assert truth.load_exit_minutes(tmp_path, DAY, CODE) is None
    target, _ = truth.minute_paths(tmp_path, DAY, CODE)
    target.parent.mkdir(parents=True)
    target.symlink_to(legacy)
    with pytest.raises(ValueError, match="unsafe"):
        truth.load_exit_minutes(tmp_path, DAY, CODE)


class Client:
    def __init__(self, values=None, error=None):
        self.values, self.error, self.calls = values, error, []

    def call(self, endpoint, params, fields):
        self.calls.append((endpoint, params, fields))
        if self.error:
            raise self.error
        return pd.DataFrame(self.values if self.values is not None else rows())


@pytest.fixture
def planned(monkeypatch):
    monkeypatch.setattr(sync, "required_partitions", lambda root, asof: {(DAY, CODE)})


def test_sync_is_one_exact_stock_day_and_never_overwrites(tmp_path, planned):
    client = Client()
    result = sync.sync_missing_minutes(tmp_path, DAY, client=client)
    assert result["status"] == "COMPLETE"
    assert len(client.calls) == 1
    endpoint, params, fields = client.calls[0]
    assert endpoint == "stk_mins" and fields == truth.FIELDS
    assert params == {"ts_code": CODE, "freq": "1min", "start_date": "2026-09-14 09:30:00",
                      "end_date": "2026-09-14 15:00:00"}
    assert len(result["written_paths"]) == 2
    assert len(sync.validated_written_paths(tmp_path, result, DAY)) == 2
    before = [p.read_bytes() for p in truth.minute_paths(tmp_path, DAY, CODE)]
    rerun = sync.sync_missing_minutes(tmp_path, DAY, client=Client(error=RuntimeError("no request")))
    assert rerun["network_requests"] == 0 and rerun["written_paths"] == []
    assert before == [p.read_bytes() for p in truth.minute_paths(tmp_path, DAY, CODE)]


def test_permission_error_is_pending_and_never_leaks_exception_token(tmp_path, planned):
    result = sync.sync_missing_minutes(tmp_path, DAY, client=Client(error=RuntimeError("secret token")))
    assert result["status"] == "PENDING_TRUTH"
    assert result["partitions"][0]["reason"] == "RuntimeError"
    assert "secret" not in json.dumps(result)
    assert result["written_paths"] == []


def test_partial_source_is_pending_without_writing(tmp_path, planned):
    result = sync.sync_missing_minutes(tmp_path, DAY, client=Client(rows()[:-1]))
    assert result["partitions"][0]["status"] == "PENDING_SOURCE_INVALID"
    assert result["written_paths"] == []
    assert truth.load_exit_minutes(tmp_path, DAY, CODE) is None


def test_duplicate_columns_cannot_be_dropped_by_dataframe_conversion(tmp_path, planned):
    class BadClient:
        def call(self, *args):
            frame = pd.DataFrame(rows())
            return pd.concat([frame, frame[["vol"]]], axis=1)
    result = sync.sync_missing_minutes(tmp_path, DAY, client=BadClient())
    assert result["partitions"][0]["status"] == "PENDING_SOURCE_INVALID"
    assert result["written_paths"] == []


def test_existing_incomplete_not_overwritten_or_retried(tmp_path, planned):
    _, meta = write_truth(tmp_path)
    meta.write_text("{}")
    client = Client()
    result = sync.sync_missing_minutes(tmp_path, DAY, client=client)
    assert result["partitions"][0]["status"] == "PENDING_EXISTING_INVALID_NOT_OVERWRITTEN"
    assert not client.calls and meta.read_text() == "{}"


def test_request_bound_and_absent_token_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "required_partitions", lambda root, asof: {(DAY, CODE), ("20260915", CODE)})
    result = sync.sync_missing_minutes(tmp_path, "20260915", client=Client(), max_requests=1)
    assert result["network_requests"] == 1
    assert result["partitions"][1]["status"] == "PENDING_REQUEST_LIMIT"
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    result = sync.sync_missing_minutes(tmp_path, "20260915")
    assert result["partitions"][1]["status"] == "PENDING_CREDENTIAL"


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(as_of_date="20260915"),
    lambda r: r["written_paths"].pop(),
    lambda r: r["written_paths"][0].update(sha256="0" * 64),
    lambda r: r["written_paths"][0].update(path="data/market/exit_1000_1m/2026/20260914/arbitrary.csv"),
    lambda r: r["partitions"][0].update(status="PENDING_SOURCE_INVALID"),
    lambda r: r["partitions"].append(dict(r["partitions"][0])),
])
def test_staging_requires_exact_report_identity_pair_and_sha(tmp_path, planned, mutation):
    report = sync.sync_missing_minutes(tmp_path, DAY, client=Client())
    mutation(report)
    with pytest.raises(ValueError):
        sync.validated_written_paths(tmp_path, report, DAY)


@pytest.mark.parametrize("path", [
    "data/market/exit_1000_1m/2025/20260914/000001_SZ.csv",
    "data/market/exit_1000_1m/2026/20260230/000001_SZ.csv",
    "data/market/exit_1000_1m/2026/20260911/000001_SZ.csv",
    "data/market/exit_1000_1m/2026/20260914/000001_HK.csv",
    "data/market/exit_1000_1m/2026/20260914/000001_SZ.json",
    "data/market/exit_1000_1m/2026/20260914/all.csv",
    "data/market/exit_1000_1m/2026/20260914/nested/000001_SZ.csv",
    "data/market/exit_1000_1m/2026/20260914/../000001_SZ.csv",
])
def test_precise_publish_allowlist_rejects_arbitrary_paths(path):
    assert not truth.exit_minute_path_is_valid(path)


def test_precise_publish_allowlist_accepts_only_expected_pair():
    for extension in ("csv", "meta.json"):
        assert truth.exit_minute_path_is_valid(f"data/market/exit_1000_1m/2026/20260914/000001_SZ.{extension}")


@pytest.fixture
def plan_inputs(tmp_path, monkeypatch):
    from scripts import settle_primary_observations as p0
    from top10decision.decision import executable_profit_shadow_settlement as settlement
    dates = ["20260828", "20260910", "20260911", "20260914", "20260915", "20260916"]
    path = tmp_path / "data/decision_executable_profit/forward/selections/shadow_20260910.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    monkeypatch.setattr(settlement, "_strict_open_dates", lambda root: dates)
    monkeypatch.setattr(settlement, "_strict_as_of_date", lambda dates, asof, **kwargs: asof)
    monkeypatch.setattr(p0, "plan_exit_minute_requests", lambda root, asof: [
        {"trade_date": "20260915", "ts_code": "000003.SZ"}])
    calls = []
    def selection(root, date):
        calls.append(date)
        return path, {"exec_date": "20260911", "exit_date": DAY}, []
    monkeypatch.setattr(settlement, "load_selection", selection)
    monkeypatch.setattr(settlement, "build_t_verification", lambda *a, **kw: ({"rows": [
        {"ts_code": CODE, "proxy_fill": 1, "entry_open_price": 10, "t_close_price": 11},
        {"ts_code": "000002.SZ", "proxy_fill": 0},
        {"ts_code": "000004.SZ", "proxy_fill": 1, "entry_open_price": 10, "t_close_price": 11},
    ]}, "verified"))
    def resolve(**kwargs):
        return ({} if kwargs["code"] == "000004.SZ" else None), "status", []
    monkeypatch.setattr(settlement, "_resolve_public_exit_1000", resolve)
    return tmp_path, settlement, p0, calls


def test_plan_only_verified_unfinished_fills_including_all_intervening_sessions(plan_inputs):
    root, _, _, calls = plan_inputs
    assert sync.required_partitions(root, "20260916") == {
        (DAY, CODE), ("20260915", CODE), ("20260916", CODE), ("20260915", "000003.SZ")}
    assert calls == ["20260910"]


def test_before_policy_cutover_no_minute_request_or_p0_evaluation(plan_inputs, monkeypatch):
    root, _, p0, calls = plan_inputs
    monkeypatch.setattr(p0, "plan_exit_minute_requests", lambda *a: pytest.fail("not due"))
    assert sync.required_partitions(root, "20260911") == set()
    assert calls == []


def test_bad_frozen_or_entry_binding_cannot_be_silently_used_for_collection(plan_inputs, monkeypatch):
    root, settlement, _, _ = plan_inputs
    def fail(*a, **kw):
        raise ValueError("immutable T source SHA conflict")
    monkeypatch.setattr(settlement, "build_t_verification", fail)
    with pytest.raises(ValueError, match="SHA conflict"):
        sync.required_partitions(root, "20260916")


def test_missing_entry_truth_does_not_collect_shadow_candidates(plan_inputs, monkeypatch):
    root, settlement, _, _ = plan_inputs
    monkeypatch.setattr(settlement, "build_t_verification", lambda *a, **kw: (None, "PENDING_T"))
    assert sync.required_partitions(root, "20260916") == {("20260915", "000003.SZ")}


def test_frozen_daily_sync_includes_only_p0_planned_missing_session_codes(plan_inputs, monkeypatch):
    from scripts import sync_frozen_shadow_truth as daily_sync
    root, _, _, _ = plan_inputs
    monkeypatch.setattr(daily_sync, "_strict_open_dates", lambda root: ["20260910", "20260911", DAY, "20260915"])
    monkeypatch.setattr(daily_sync, "_strict_as_of_date", lambda *a, **kw: "20260915")
    monkeypatch.setattr(daily_sync, "load_selection", lambda *a: (None, {"exec_date": "20260911", "exit_date": DAY}, []))
    assert daily_sync.required_partitions(root, "20260915") == {
        ("20260915", "daily"): {"000003.SZ"}, ("20260915", "stk_limit"): {"000003.SZ"}}


def workflow_text():
    return (Path(__file__).resolve().parents[1] / ".github/workflows/verify_decision_observations.yml").read_text()


def test_workflow_keeps_full_gate_and_orders_truth_before_both_settlements():
    text = workflow_text()
    sync_start = text.index("      - name: Sync exact minute sessions")
    next_step = text.index("      - name:", sync_start + 12)
    block = text[sync_start:next_step]
    assert "steps.truth_sync.outputs.complete == 'true'" in block
    assert "steps.forecast_gate.outputs.contract_mode == 'PRIMARY_FROZEN_FORECASTS'" in block
    assert "--optional" not in block and "continue-on-error" not in block
    assert sync_start < text.index("      - name: Settle Action-independent primary")
    assert sync_start < text.index('audit_before="${RUNNER_TEMP}/executable-profit-verify-before.json"')
    assert "--strict-dated-source" in text and "--expected-base-sha" in text
    assert "Stage validated exact exit-minute source pairs" in text
    assert "validated_written_paths(Path('.'), report, os.environ['AS_OF_DATE'])" in text
    assert "git add -A -- data/market/exit_1000_1m" not in text


def test_every_embedded_python_and_bash_script_compiles_without_execution():
    text = workflow_text()
    for body in re.findall(r"(?ms)^          [^\n]*python - <<'PY'\n(.*?)^          PY$", text):
        compile(textwrap.dedent(body), "verify-embedded", "exec")
    for body in re.findall(r"(?m)^        run: \|\n((?:          .*\n|\n)*)", text):
        result = subprocess.run(["bash", "-n"], input=textwrap.dedent(body), text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def test_actual_cli_bootstrap_before_cutover_is_read_only_and_needs_no_token(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report_path = tmp_path / "report.json"
    result = subprocess.run([sys.executable, str(root / "scripts/sync_exit_1000_minute_truth.py"),
                             "--root", str(root), "--as-of-date", "20260911", "--report", str(report_path)],
                            cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(root / "src"), TUSHARE_TOKEN=""),
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text())
    assert report["network_requests"] == 0 and report["written_paths"] == []
    assert report["status"] == "COMPLETE"


@pytest.mark.parametrize("relative,allowed", [
    ("2026/20260914/000001_SZ.csv", True),
    ("2026/20260914/000001_SZ.meta.json", True),
    ("2026/20260914/arbitrary.csv", False),
    ("2025/20260914/000001_SZ.csv", False),
    ("2026/20260230/000001_SZ.csv", False),
    ("2026/20260911/000001_SZ.csv", False),
    ("2026/20260914/000001_BJ.csv", False),
    ("2026/20260914/nested/000001_SZ.csv", False),
])
def test_both_actual_workflow_path_gates_are_exact(tmp_path, relative, allowed):
    path = "data/market/exit_1000_1m/" + relative
    paths, index = tmp_path / "paths", tmp_path / "index"
    paths.write_bytes(path.encode() + b"\0")
    index.write_bytes(f"100644 {'0'*40} 0\t{path}\0".encode())
    env = dict(os.environ, STAGED_PATHS=str(paths), STAGED_INDEX=str(index),
               PUBLISH_STAGED_PATHS=str(paths), PUBLISH_STAGED_INDEX=str(index))
    scripts = [textwrap.dedent(body) for body in
               re.findall(r"(?ms)^          [^\n]*python - <<'PY'\n(.*?)^          PY$", workflow_text())
               if "def dated_raw_path_is_valid(path):" in body]
    assert len(scripts) == 2
    for script in scripts:
        result = subprocess.run([sys.executable, "-"], input=script, env=env, text=True, capture_output=True)
        assert (result.returncode == 0) is allowed, result.stderr


def actual_bound_reuse_function():
    from datetime import datetime
    from top10decision.decision import executable_profit_shadow_settlement as settlement
    body = workflow_text().split("reuse_bound_raw=\"$(python - <<'PY'\n", 1)[1].split("\n          PY\n", 1)[0]
    tree = ast.parse(textwrap.dedent(body))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "reuse_bound_truth_partition")
    namespace = dict(Path=Path, datetime=datetime, re=re, json=json, hashlib=hashlib,
                     validate_t_verification=settlement.validate_t_verification,
                     validate_t1_settlement=settlement.validate_t1_settlement)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "actual-Verify-reuse", "exec"), namespace)
    return namespace[function.name]


@pytest.fixture
def real_v3_bound_truth(tmp_path, monkeypatch):
    # Reuse the real shared-engine settlement fixture, not a fabricated v3 JSON
    # or a stubbed truth validator. All writes are confined to pytest tmp_path.
    source = Path(__file__).with_name("test_shadow_exit_1000_settlement.py")
    spec = importlib.util.spec_from_file_location("minute_v3_reuse_fixture", source)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    root = helpers.repo.__wrapped__(tmp_path, monkeypatch)
    helpers.install_minutes(root)
    result = helpers.settlement.settle_signal_date(root, "20260910", as_of_date=DAY)
    assert result["t1_settlement_path"]
    payload = json.loads((root / result["t1_settlement_path"]).read_bytes())
    assert payload["schema_version"] == helpers.settlement.SETTLEMENT_SCHEMA_V3
    helpers.settlement.validate_t1_settlement(payload)
    return root


def test_real_v3_same_day_reuse_validates_minutes_and_keeps_raw_bytes(real_v3_bound_truth):
    root = real_v3_bound_truth
    paths = list((root / "data/market").rglob("*"))
    before = {p: p.read_bytes() for p in paths if p.is_file()}
    assert actual_bound_reuse_function()(root, DAY) is True
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert actual_bound_reuse_function()(root, "20260915") is False


@pytest.mark.parametrize("fault", ["missing_csv", "missing_meta", "bad_sha", "bad_metadata", "symlink"])
def test_real_v3_reuse_rejects_invalid_bound_minute_pairs(real_v3_bound_truth, fault):
    root = real_v3_bound_truth
    csv_path, meta_path = truth.minute_paths(root, DAY, "600001.SH")
    if fault == "missing_csv":
        csv_path.unlink()
    elif fault == "missing_meta":
        meta_path.unlink()
    elif fault == "bad_sha":
        csv_path.write_bytes(csv_path.read_bytes() + b"\n")
    elif fault == "bad_metadata":
        meta = json.loads(meta_path.read_bytes())
        meta["timestamp_semantics"] = "BAR_START"
        meta_path.write_text(json.dumps(meta))
    else:
        raw = csv_path.read_bytes()
        csv_path.unlink()
        other = root / "outside.csv"
        other.write_bytes(raw)
        csv_path.symlink_to(other)
    with pytest.raises(ValueError):
        actual_bound_reuse_function()(root, DAY)
