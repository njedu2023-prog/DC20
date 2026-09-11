from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import validate_verify_forecast_inputs as gate


def _write_json(root: Path, relative: str | Path, value: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(gate, "load_model_freeze", lambda *a, **k: {"active": True})
    monkeypatch.setattr(gate, "model_freeze_active", lambda value: value["active"])
    def pins(*args, **kwargs):
        assert kwargs == {"force_enforcement": True}
        calls.append("pins")
        return {"enforced": True}
    monkeypatch.setattr(gate, "validate_pinned_files", pins)
    monkeypatch.setattr(gate, "load_strict_sse_dates", lambda *a, **k: ("20260907", "20260908", {}))
    index = {
        "latest_signal_date": "20260904",
        "latest_receipt_url": "outputs/decision/primary_d_receipt_20260904.json",
        "latest_runtime_features_url": "outputs/decision/primary_d_runtime_features_20260904.csv",
        "latest_three_rank_json_url": "outputs/decision/three_rank_top10_20260904.json",
        "latest_three_rank_csv_url": "outputs/decision/three_rank_top10_20260904.csv",
    }
    _write_json(tmp_path, gate.INDEX, index)
    _write_json(tmp_path, index["latest_receipt_url"], {"generation_mode": "NATURAL"})
    _write_json(tmp_path, "data/decision_executable_profit/forward/selections/shadow_20260904.json", {})
    monkeypatch.setattr(gate, "validate_primary_d_runtime_index", lambda value: calls.append("index_shape"))
    monkeypatch.setattr(gate, "build_primary_d_runtime_index", lambda *a, **k: index)
    def bundle(*args, **kwargs):
        assert kwargs == {"expected_signal_date": "20260904"}
        calls.append("P0_P1_hash_chain")
        return {"mixed": {"index": {"prospective": True}}}
    monkeypatch.setattr(gate, "validate_primary_profit_bundle", bundle)
    def inputs(*args, **kwargs):
        calls.append("dated_P0")
        return SimpleNamespace(three_rank={"exec_date": "20260907", "exit_date": "20260908"})
    monkeypatch.setattr(gate, "load_primary_inputs", inputs)
    monkeypatch.setattr(gate, "load_selection", lambda *a, **k: (None, {"exec_date": "20260907", "exit_date": "20260908"}, None))
    monkeypatch.setattr(gate, "validate_primary_profit_forward_shadow_repository_chain", lambda *a, **k: calls.append("Shadow_hash_chain"))
    return tmp_path, calls, index


def test_primary_gate_validates_exact_forecasts_without_reading_legacy_action(frozen):
    root, calls, _ = frozen
    _write_json(root, "outputs/decision/action_plan_latest.json", {"status_code": "STALE_BROKEN_LEGACY"})
    result = gate.validate_forecast_inputs(root, as_of_date="20260904")
    assert result["mode"] == "PRIMARY_FROZEN_FORECASTS"
    assert result["action_input_consumed"] is False
    assert result["forecast_model_retrained"] is False
    assert calls == ["pins", "index_shape", "P0_P1_hash_chain", "dated_P0", "Shadow_hash_chain"]


def test_pin_failure_cannot_switch_to_primary_or_legacy(frozen, monkeypatch):
    root, _, _ = frozen
    def bad(*a, **k):
        raise ValueError("frozen file drift")
    monkeypatch.setattr(gate, "validate_pinned_files", bad)
    with pytest.raises(ValueError, match="frozen file drift"):
        gate.validate_forecast_inputs(root, as_of_date="20260904")


def test_orphan_primary_receipt_never_falls_back_to_legacy(frozen):
    root, _, _ = frozen
    (root / gate.INDEX).unlink()
    with pytest.raises(ValueError, match="legacy fallback forbidden"):
        gate.validate_forecast_inputs(root, as_of_date="20260904")


def test_corrupt_primary_pointer_never_falls_back_to_legacy(frozen, monkeypatch):
    root, _, _ = frozen
    monkeypatch.setattr(gate, "build_primary_d_runtime_index", lambda *a, **k: {})
    with pytest.raises(ValueError, match="exact receipt/runtime/TopN"):
        gate.validate_forecast_inputs(root, as_of_date="20260904")


def test_forecast_after_asof_is_rejected(frozen):
    root, _, _ = frozen
    with pytest.raises(ValueError, match="predates the primary"):
        gate.validate_forecast_inputs(root, as_of_date="20260903")


def test_omitted_later_dated_receipt_is_rejected(frozen):
    root, _, _ = frozen
    _write_json(root, "outputs/decision/primary_d_receipt_20260907.json", {})
    with pytest.raises(ValueError, match="later dated receipt"):
        gate.validate_forecast_inputs(root, as_of_date="20260907")


def test_calendar_drift_is_rejected(frozen, monkeypatch):
    root, _, _ = frozen
    monkeypatch.setattr(gate, "load_strict_sse_dates", lambda *a, **k: ("20260905", "20260906", {}))
    with pytest.raises(ValueError, match="D/T/T\\+1 differs"):
        gate.validate_forecast_inputs(root, as_of_date="20260904")


def test_natural_top2_requires_exact_frozen_shadow(frozen):
    root, _, _ = frozen
    (root / "data/decision_executable_profit/forward/selections/shadow_20260904.json").unlink()
    with pytest.raises(ValueError, match="no exact-D frozen Shadow"):
        gate.validate_forecast_inputs(root, as_of_date="20260904")


def test_verify_workflow_gates_actual_truth_input_and_retains_diagnostics():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/verify_decision_observations.yml").read_text()
    assert "--expected-base-sha" in source
    assert 'if [ "${contract_mode}" = LEGACY_AUCTION ]; then' in source
    assert "steps.forecast_gate.outputs.contract_mode == 'LEGACY_AUCTION'" in source
    assert "steps.forecast_gate.outputs.contract_mode == 'PRIMARY_FROZEN_FORECASTS'" in source
    assert 'verify-frozen-replay.json" >/dev/null' not in source
    assert "verify-diagnostics-${{ github.run_id }}-${{ github.run_attempt }}" in source
    assert source.count("allowed += ('outputs/decision/primary_observation/summary.json','outputs/decision/primary_observation/rows.csv')") == 2
    assert 'python scripts/settle_primary_observations.py --root . --as-of-date "${AS_OF_DATE}" --validate-existing' in source


def test_all_verify_literal_run_blocks_are_valid_bash_without_executing():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/verify_decision_observations.yml").read_text()
    blocks = re.findall(r"(?m)^        run: \|\n((?:          .*\n|\n)*)", source)
    assert len(blocks) >= 15
    for block in blocks:
        result = subprocess.run(["bash", "-n"], input=textwrap.dedent(block), text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def _verify_path_gate_scripts():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/verify_decision_observations.yml").read_text()
    scripts = [
        textwrap.dedent(body)
        for body in re.findall(r"(?ms)^          [^\n]*python - <<'PY'\n(.*?)^          PY$", source)
        if "def dated_raw_path_is_valid(path):" in body
    ]
    assert len(scripts) == 2
    return scripts


def _run_verify_path_gates(root: Path, paths: list[str], *, mode="100644"):
    staged_paths = root / "paths.bin"
    staged_paths.write_bytes(b"\0".join(p.encode() for p in paths) + b"\0")
    staged_index = root / "index.bin"
    staged_index.write_bytes(b"".join(
        f"{mode} {'0' * 40} 0\t{path}\0".encode() for path in paths
    ))
    env = dict(os.environ, STAGED_PATHS=str(staged_paths), STAGED_INDEX=str(staged_index),
               PUBLISH_STAGED_PATHS=str(staged_paths), PUBLISH_STAGED_INDEX=str(staged_index))
    return [subprocess.run([sys.executable, "-"], input=script, env=env,
                           text=True, capture_output=True) for script in _verify_path_gate_scripts()]


def test_both_verify_path_gates_allow_only_exact_dated_v2_auction_evidence(tmp_path):
    paths = [f"data/market/raw/2026/20260914/{name}" for name in ("stk_auction.csv", "_sync_meta.json")]
    for result in _run_verify_path_gates(tmp_path, paths):
        assert result.returncode == 0, result.stderr
    for mode in ("100755", "120000"):
        for result in _run_verify_path_gates(tmp_path, paths, mode=mode):
            assert result.returncode != 0 and "non-regular Verify" in result.stderr


@pytest.mark.parametrize("relative", [
    "2026/20260914/arbitrary.csv", "2026/20260914/stk_auction.meta.json",
    "2026/20260914/_sync_meta_latest.json", "2026/20260914/sub/_sync_meta.json",
    "latest/stk_auction.csv", "2025/20260914/stk_auction.csv",
    "2026/20260230/_sync_meta.json", "2026/20260914/../_sync_meta.json",
])
def test_both_verify_path_gates_reject_foreign_or_misdated_auction_files(tmp_path, relative):
    for result in _run_verify_path_gates(tmp_path, ["data/market/raw/" + relative]):
        assert result.returncode != 0 and "non-allowlisted Verify" in result.stderr


def test_verify_raw_stage_loop_includes_both_bound_sources_without_whole_raw_directory(tmp_path):
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/verify_decision_observations.yml").read_text()
    raw_loop = textwrap.dedent(re.search(
        r"(?ms)^          raw_date_root=.*?^          done\n", source,
    ).group())
    relative = "data/market/raw/2026/20260914"
    partition = tmp_path / relative
    partition.mkdir(parents=True)
    for name in ("stk_auction.csv", "_sync_meta.json", "arbitrary.csv"):
        (partition / name).write_text("fixture", encoding="utf-8")
    wrapper = '''
git() {
  case "$1" in
    add) printf '%s\\n' "$*" ;;
    ls-files) return 1 ;;
    *) return 99 ;;
  esac
}
'''
    result = subprocess.run(["bash"], input=wrapper + raw_loop, cwd=tmp_path,
                            env=dict(os.environ, AS_OF_DATE="20260914"), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"add -A -- {relative}/stk_auction.csv", f"add -A -- {relative}/_sync_meta.json",
    ]


def _truth_sync_script():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/verify_decision_observations.yml").read_text()
    section = source.split("- name: Sync all same-date truth without optional bypass", 1)[1]
    return textwrap.dedent(re.search(r"(?m)^        run: \|\n((?:          .*\n|\n)*)", section).group(1))


def test_verify_selects_validated_forecast_mode_before_fetching_truth():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/verify_decision_observations.yml").read_text()
    assert source.index("id: forecast_gate") < source.index("id: upstream") < source.index("id: truth_sync")
    assert "CONTRACT_MODE: ${{ steps.forecast_gate.outputs.contract_mode }}" in source
    assert "continue-on-error" not in _truth_sync_script()
    assert "--optional" not in _truth_sync_script()
    # The split must not soften the separate required auction truth contract.
    assert "python scripts/sync_frozen_shadow_truth.py" in source
    assert "python scripts/settle_primary_observations.py" in source
    assert "Verify rewrote immutable Shadow truth" in source


def test_verify_market_sync_is_strict_dated_and_missing_required_source_stops_truth_gate(tmp_path):
    script = _truth_sync_script()
    assert 'python scripts/sync_market_raw.py --trade-date "${AS_OF_DATE}" --strict-dated-source' in script
    assert "--allow-latest" not in script and "--optional" not in script
    wrapper = '''
python() {
  case "$1" in
    -) "$VERIFY_TEST_PYTHON" "$@" ;;
    scripts/sync_market_raw.py)
      [ "$2" = --trade-date ] && [ "$3" = 20260914 ] && [ "$4" = --strict-dated-source ] || return 90
      echo 'required files unavailable for the requested session' >&2
      return 2 ;;
    *) echo 'truth gate continued after source failure' >&2; return 91 ;;
  esac
}
'''
    result = subprocess.run(["bash"], input=wrapper + script, cwd=tmp_path,
                            env=dict(os.environ, AS_OF_DATE="20260914", CONTRACT_MODE="PRIMARY_FROZEN_FORECASTS",
                                     VERIFY_TEST_PYTHON=sys.executable,
                                     PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src")),
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "required files unavailable for the requested session" in result.stderr
    assert "continued after source failure" not in result.stderr


@pytest.fixture
def primary_daily_truth(tmp_path):
    root = Path(__file__).resolve().parents[1]
    market = tmp_path / "data/market"
    market.mkdir(parents=True)
    shutil.copyfile(root / "data/market/trade_cal_sse.csv", market / "trade_cal_sse.csv")
    partition = market / "raw/2026/20260907"
    partition.mkdir(parents=True)
    (partition / "daily.csv").write_text(
        "ts_code,trade_date,open,close,pre_close,vol\n600001.SH,20260907,10,11,10,100\n")
    (partition / "stk_limit.csv").write_text(
        "ts_code,trade_date,up_limit,down_limit\n600001.SH,20260907,11,9\n")
    return tmp_path, partition


def _run_primary_sync(root, *, as_of_date="20260907", forbid_sync=False):
    env = dict(os.environ, CONTRACT_MODE="PRIMARY_FROZEN_FORECASTS", AS_OF_DATE=as_of_date,
               RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root / "step-output"),
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), VERIFY_TEST_PYTHON=sys.executable,
               VERIFY_SYNC_FORBIDDEN="true" if forbid_sync else "false")
    # No network: test the actual workflow branch with successful upstream
    # commands replaced by no-ops. Any old minute candidate call is a failure.
    wrappers = '''
python() {
  case "$1" in
    scripts/sync_market_raw.py|scripts/sync_tushare_daily_close.py)
      if [ "$VERIFY_SYNC_FORBIDDEN" = true ]; then echo 'bound raw was resynced' >&2; return 92; fi
      echo "sync-called:$1" >&2; return 0 ;;
    scripts/sync_tushare_minute.py) echo 'legacy minute dependency reached' >&2; return 91 ;;
    *) "$VERIFY_TEST_PYTHON" "$@" ;;
  esac
}
'''
    return subprocess.run(["bash"], input=wrappers + _truth_sync_script(), cwd=root,
                          env=env, text=True, capture_output=True)


def test_primary_daily_truth_does_not_require_old_auction_predictions_or_minutes(primary_daily_truth):
    root, _ = primary_daily_truth
    result = _run_primary_sync(root)
    assert result.returncode == 0, result.stderr
    assert "sync-called:scripts/sync_market_raw.py" in result.stderr
    assert "sync-called:scripts/sync_tushare_daily_close.py" in result.stderr
    assert (root / "step-output").read_text() == "complete=true\n"
    report = json.loads((root / "verify-primary-daily-truth.json").read_text())
    assert report["as_of_date"] == "20260907"
    assert [row["endpoint"] for row in report["source_files"]] == ["daily", "stk_limit"]
    assert all(len(row["sha256"]) == 64 for row in report["source_files"])
    assert report["minute_truth_required"] is False
    assert report["all_frozen_rows_verified"] is False
    assert report["legacy_shadow_auction_requirement_unchanged"] is True
    assert report["primary_shadow_entry_price_policy"] == "VERSIONED_AUCTION_OR_OPEN_PROXY"
    assert not (root / "outputs").exists()


@pytest.mark.parametrize("bad", ["wrong_date", "duplicate_code", "missing_column", "missing_partition", "wrong_calendar"])
def test_primary_daily_truth_malformed_or_missing_source_cannot_open_settlement_gate(primary_daily_truth, bad):
    root, partition = primary_daily_truth
    path = partition / "daily.csv"
    if bad == "wrong_date":
        path.write_text(path.read_text().replace("20260907", "20260904"))
    elif bad == "duplicate_code":
        path.write_text(path.read_text() + path.read_text().splitlines()[-1] + "\n")
    elif bad == "missing_column":
        path.write_text(path.read_text().replace("pre_close", "not_pre_close"))
    elif bad == "missing_partition":
        path.unlink()
    else:
        (root / "data/market/trade_cal_sse.csv").write_text("exchange,cal_date,is_open\nSSE,20260907,1\n")
    result = _run_primary_sync(root)
    assert result.returncode != 0
    assert not (root / "step-output").exists()
    assert not (root / "verify-primary-daily-truth.json").exists()


def _bound_truth_reuse():
    from top10decision.decision import executable_profit_shadow_settlement as settlement
    script = _truth_sync_script()
    body = script.split("reuse_bound_raw=\"$(python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    module = ast.parse(body)
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "reuse_bound_truth_partition")
    from datetime import datetime
    namespace = {"Path": Path, "json": json, "hashlib": hashlib, "re": re, "datetime": datetime,
                 "validate_t_verification": settlement.validate_t_verification,
                 "validate_t1_settlement": settlement.validate_t1_settlement}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "verify-bound-raw-reuse", "exec"), namespace)
    return namespace[function.name]


@pytest.fixture
def immutable_v2_truth(tmp_path, monkeypatch):
    # Reuse the settlement suite's market fixture, but build and validate real
    # v2 T/T+1 payloads. Only upstream selection scoring is outside this test.
    import importlib.util
    fixture_spec = importlib.util.spec_from_file_location(
        "verify_settlement_market_fixture",
        Path(__file__).with_name("test_decision_executable_profit_shadow_settlement.py"),
    )
    fixture_module = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture_module)
    from top10decision.decision import executable_profit_shadow_settlement as settlement
    monkeypatch.setattr(settlement, "validate_internal_forward_shadow_payload", lambda *a, **k: None)
    monkeypatch.setattr(settlement, "_validate_primary_mixed_selection", lambda *a, **k: None)
    root = fixture_module._price_v2_repo(tmp_path, source="stk_auction")
    verification, _ = settlement.build_t_verification(root, "20260824", as_of_date="20260826")
    settlement.materialize_t_verification(root, verification)
    settled, _ = settlement.build_t1_settlement(root, "20260824", verification=verification, as_of_date="20260826")
    settlement.materialize_t1_settlement(root, settled)
    return root


@pytest.mark.parametrize("as_of_date", ["20260825", "20260826"])
def test_existing_valid_t_or_t1_sources_reuse_whole_partition_and_keep_full_truth_gate(immutable_v2_truth, as_of_date):
    root = immutable_v2_truth
    partition = root / "data/market/raw/2026" / as_of_date
    before = {path.name: path.read_bytes() for path in partition.iterdir()}
    assert _bound_truth_reuse()(root, as_of_date) is True
    result = _run_primary_sync(root, as_of_date=as_of_date, forbid_sync=True)
    assert result.returncode == 0, result.stderr
    assert (root / "step-output").read_text() == "complete=true\n"
    report = json.loads((root / "verify-primary-daily-truth.json").read_text())
    assert report["as_of_date"] == as_of_date
    assert [item["endpoint"] for item in report["source_files"]] == ["daily", "stk_limit"]
    assert {path.name: path.read_bytes() for path in partition.iterdir()} == before
    assert "sync-called:" not in result.stderr and "bound raw was resynced" not in result.stderr


@pytest.mark.parametrize("fault", ["missing", "corrupt_metadata", "corrupt_csv", "symlink", "invalid_payload"])
def test_bound_raw_missing_corrupt_or_unsafe_fails_before_sync(immutable_v2_truth, fault):
    root = immutable_v2_truth
    meta = root / "data/market/raw/2026/20260825/_sync_meta.json"
    if fault == "missing":
        meta.unlink()
    elif fault == "corrupt_metadata":
        meta.write_bytes(meta.read_bytes() + b" ")
    elif fault == "corrupt_csv":
        raw = meta.with_name("stk_auction.csv")
        raw.write_bytes(raw.read_bytes() + b"\n")
    elif fault == "symlink":
        saved = root / "saved-meta.json"
        meta.replace(saved)
        meta.symlink_to(saved)
    else:
        path = root / "data/decision_executable_profit/forward/verifications/t_verification_20260824.json"
        payload = json.loads(path.read_bytes())
        payload["snapshot_sha256"] = "f" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
    result = _run_primary_sync(root, as_of_date="20260825", forbid_sync=True)
    assert result.returncode != 0
    assert "sync-called:" not in result.stderr and "bound raw was resynced" not in result.stderr
    assert not (root / "step-output").exists()


def test_other_date_binding_does_not_skip_normal_sync(immutable_v2_truth):
    root = immutable_v2_truth
    assert _bound_truth_reuse()(root, "20260827") is False
    source = root / "data/market/raw/2026/20260826"
    target = root / "data/market/raw/2026/20260827"
    target.mkdir()
    for name in ("daily.csv", "stk_limit.csv"):
        (target / name).write_text((source / name).read_text().replace("20260826", "20260827"))
    result = _run_primary_sync(root, as_of_date="20260827")
    assert result.returncode == 0, result.stderr
    assert "sync-called:scripts/sync_market_raw.py" in result.stderr
    assert "sync-called:scripts/sync_tushare_daily_close.py" in result.stderr


def test_bound_raw_reuse_cannot_skip_later_complete_daily_gate(immutable_v2_truth):
    root = immutable_v2_truth
    calendar = root / "data/market/trade_cal_sse.csv"
    calendar.write_text("exchange,cal_date,is_open\nSSE,20260825,1\n")
    assert _bound_truth_reuse()(root, "20260825") is True
    result = _run_primary_sync(root, as_of_date="20260825", forbid_sync=True)
    assert result.returncode != 0
    assert "bound raw was resynced" not in result.stderr
    assert not (root / "step-output").exists()
