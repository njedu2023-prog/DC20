from __future__ import annotations

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


def _run_primary_sync(root):
    env = dict(os.environ, CONTRACT_MODE="PRIMARY_FROZEN_FORECASTS", AS_OF_DATE="20260907",
               RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root / "step-output"),
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), VERIFY_TEST_PYTHON=sys.executable)
    # No network: test the actual workflow branch with successful upstream
    # commands replaced by no-ops. Any old minute candidate call is a failure.
    wrappers = '''
python() {
  case "$1" in
    scripts/sync_market_raw.py|scripts/sync_tushare_daily_close.py) return 0 ;;
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
    assert (root / "step-output").read_text() == "complete=true\n"
    report = json.loads((root / "verify-primary-daily-truth.json").read_text())
    assert report["as_of_date"] == "20260907"
    assert [row["endpoint"] for row in report["source_files"]] == ["daily", "stk_limit"]
    assert all(len(row["sha256"]) == 64 for row in report["source_files"])
    assert report["minute_truth_required"] is False
    assert report["all_frozen_rows_verified"] is False
    assert report["shadow_auction_requirement_unchanged"] is True
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
