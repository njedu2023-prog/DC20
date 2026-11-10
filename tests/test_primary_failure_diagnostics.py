"""Fault injection: diagnostic delivery never grants formal prediction status."""
from copy import deepcopy
import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import primary_failure_diagnostics as diagnostic
from scripts import publish_primary_three_rank as primary


def audit_rows():
    return [
        {"ts_code": "002623.SZ", "declared_stage": 2, "computed_stage": 1,
         "status": "DROPPED", "reason": "RECOMPUTED_STAGE_MISMATCH"},
        {"ts_code": "600001.SH", "declared_stage": 3, "computed_stage": 3,
         "status": "INCLUDED", "reason": ""},
    ]


def sample():
    return diagnostic.build_diagnostic("20261110", audit_rows=audit_rows(),
        stock_names={"002623.SZ": "待核验股份", "600001.SH": "<script>测试</script>"},
        source_bindings={"code_revision": "a"*40, "candidate_sha256": "b"*64},
        run_id="1234", run_attempt="1", generated_at_utc="2026-11-10T14:00:00Z")


def test_typed_audit_keeps_complete_pool_and_never_reranks():
    rows = audit_rows()
    before = deepcopy(rows)
    result = diagnostic.build_diagnostic("20261110", audit_rows=rows)
    assert rows == before
    assert result["original_candidate_count"] == 2
    assert result["blocked_stock_count"] == 1
    assert result["identity_check_passed_count"] == 1
    assert result["candidate_pool_modified"] is False
    assert result["calculation_resumed"] is False
    assert result["formal_output_ready"] is False
    assert result["formal_ledger_admission"] is False
    assert all("rank" not in row and "score" not in row for row in result["rows"])
    assert diagnostic.validate_diagnostic(result) == result


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(formal_output_ready=True),
    lambda x: x.update(formal_ledger_admission=True),
    lambda x: x.update(calculation_resumed=True),
    lambda x: x.update(candidate_pool_modified=True),
    lambda x: x["rows"][1].update(candidate_rank=1),
    lambda x: x["rows"][1].update(computed_stage=2),
    lambda x: x.update(diagnostic_sha256="0"*64),
])
def test_diagnostic_mutation_cannot_be_misread_as_formal_or_verified(mutation):
    result = sample()
    mutation(result)
    with pytest.raises(ValueError):
        diagnostic.validate_diagnostic(result)


def test_unknown_failures_do_not_claim_the_remaining_stocks_are_healthy():
    result = diagnostic.build_diagnostic("20261110")
    assert result["status"] == "BLOCKED_BEFORE_IDENTITY_AUDIT"
    assert result["original_candidate_count"] is None
    assert result["identity_check_passed_count"] is None
    assert result["rows"] == []
    assert "逐股检查前停止" in diagnostic.render_html(result)


def test_unknown_reason_and_duplicate_identity_cannot_be_isolated():
    rows = audit_rows()
    rows[0]["reason"] = "UNKNOWN_ALLOW_ALL"
    with pytest.raises(ValueError, match="unsupported"):
        diagnostic.build_diagnostic("20261110", audit_rows=rows)
    rows = audit_rows()
    rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError, match="duplicated"):
        diagnostic.build_diagnostic("20261110", audit_rows=rows)


def test_html_escapes_untrusted_names_and_has_no_fake_rankings():
    rendered = diagnostic.render_html(sample())
    assert "<script>" not in rendered
    assert "&lt;script&gt;测试&lt;/script&gt;" in rendered
    assert "不是预测或冻结时间" in rendered
    assert "尚未形成正式排名" in rendered
    assert "身份与板数映射检查" in rendered


def test_diagnostic_writer_only_changes_diagnostic_files(tmp_path):
    sentinel = tmp_path / "outputs/decision/three_rank_index.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"original formal index")
    paths = diagnostic.write_diagnostic(tmp_path, sample())
    assert set(paths) == {"json", "html"}
    assert sentinel.read_bytes() == b"original formal index"
    assert json.loads(paths["json"].read_text()) == sample()
    assert all(path.relative_to(tmp_path).parts[:3] == ("outputs", "decision", "primary_diagnostics") for path in paths.values())
    assert sorted(path.name for path in sentinel.parent.iterdir()) == ["primary_diagnostics", "three_rank_index.json"]


def test_diagnostic_writer_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / "outputs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        diagnostic.write_diagnostic(tmp_path, sample())
    assert list(outside.iterdir()) == []


def test_hard_pool_failure_preserves_all_rows_and_still_blocks():
    pool = pd.DataFrame([
        {"ts_code": "002623.SZ", "name": "待核验股份", "limit_times": 2},
        {"ts_code": "600001.SH", "name": "已知股份", "limit_times": 3},
    ])
    base = pool.copy(deep=True)
    base.loc[0, "limit_times"] = 1
    inference = base.copy(deep=True)
    originals = [frame.copy(deep=True) for frame in (pool, base, inference)]
    with pytest.raises(primary.PrimaryPoolIdentityError) as caught:
        primary.audit_complete_hard_pool(pool, base, inference, engine=None, signal_date="20261110")
    assert isinstance(caught.value, primary.PrimaryDGenerationError)
    assert caught.value.audit_rows == audit_rows()
    assert caught.value.stock_names["600001.SH"] == "已知股份"
    for frame, original in zip((pool, base, inference), originals):
        pd.testing.assert_frame_equal(frame, original)


def test_healthy_hard_pool_audit_output_remains_unchanged():
    pool = pd.DataFrame([{"ts_code": "600001.SH", "limit_times": 3}])
    assert primary.audit_complete_hard_pool(pool, pool.copy(), pool.copy(), engine=None,
        signal_date="20261110") == {
        "hard_stage_row_count": 1, "current_base_row_count": 1, "inference_row_count": 1,
        "loss_count": 0, "all_hard_identities_preserved": True,
        "rows": [{"ts_code": "600001.SH", "declared_stage": 3, "computed_stage": 3,
                  "status": "INCLUDED", "reason": ""}],
    }


def test_main_outputs_diagnostic_but_retains_failure_exit(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise primary.PrimaryPoolIdentityError("hard-stage mismatch", audit_rows=audit_rows(),
                                              stock_names={"600001.SH": "已知股份"})
    monkeypatch.setattr(primary, "publish_primary_three_rank", fail)
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    assert primary.main(["--root", str(tmp_path), "--signal-date", "20261110"]) == 2
    output = capsys.readouterr()
    assert "[P0 BLOCK]" in output.err
    assert '"status": "READY"' not in output.out
    value = json.loads((tmp_path / diagnostic.RELATIVE / "day_20261110.json").read_text())
    assert value["run_id"] == "1234" and value["formal_output_ready"] is False
    assert not (tmp_path / "outputs/decision/three_rank_index.json").exists()


def test_unsupported_date_does_not_write_diagnostic_or_change_exit(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise primary.PrimaryDGenerationError("invalid date")
    monkeypatch.setattr(primary, "publish_primary_three_rank", fail)
    assert primary.main(["--root", str(tmp_path), "--signal-date", "../../escape"]) == 2
    assert list(tmp_path.iterdir()) == []


def test_workflow_retains_failure_and_publishes_only_diagnostic_artifact():
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/run_primary_d_daily.yml").read_text()
    block = text.split("- name: Explain blocked primary generation", 1)[1].split("- name: Qualify optional", 1)[0]
    assert "failure() && steps.primary_generation.outcome == 'failure'" in block
    assert "steps.primary_diagnostics.outcome == 'success'" in block
    assert "--expected-run-id" in block and "--expected-run-attempt" in block
    assert "primary-d-diagnostic-${{ github.run_id }}-${{ github.run_attempt }}" in block
    assert "continue-on-error" not in block
    assert "contents: write" not in block
    assert "git push" not in block


@pytest.mark.parametrize("fault", ["other_attempt", "html_changed"])
def test_summary_rejects_stale_attempt_or_mixed_html(tmp_path, monkeypatch, fault):
    paths = diagnostic.write_diagnostic(tmp_path, sample())
    attempt = "1"
    if fault == "other_attempt":
        attempt = "2"
    else:
        paths["html"].write_text("old HTML")
    monkeypatch.setattr("sys.argv", ["diagnostic", "--root", str(tmp_path), "--signal-date", "20261110",
        "--expected-run-id", "1234", "--expected-run-attempt", attempt])
    with pytest.raises(ValueError):
        diagnostic.main()


def test_summary_accepts_only_matching_run_json_and_html(tmp_path, monkeypatch, capsys):
    diagnostic.write_diagnostic(tmp_path, sample())
    summary = tmp_path / "summary.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr("sys.argv", ["diagnostic", "--root", str(tmp_path), "--signal-date", "20261110",
        "--expected-run-id", "1234", "--expected-run-attempt", "1"])
    diagnostic.main()
    assert "这不是正式排名" in summary.read_text()
    assert "待核验：1" in capsys.readouterr().out


def test_all_production_identity_loss_reasons_have_explicit_diagnostics():
    tree = ast.parse(Path(primary.__file__).read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_current_base_loss_reason")
    reasons = {node.value.value for node in ast.walk(function) if isinstance(node, ast.Return)
               and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
    assert reasons <= diagnostic.REASONS.keys()
