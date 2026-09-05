from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="JavaScript behavior checks require Node")


def _function(name: str) -> str:
    text = (ROOT / "decision.html").read_text()
    found = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", text, re.M | re.S)
    assert found, name
    return found.group()


def _payload() -> dict:
    day = dict(signal_date="20260828", exec_date="20260831", exit_date="20260901", rows=2,
               t_validated_rows=2, final_verified_trades=1, settled_rows=1,
               pending_t_rows=0, pending_t1_rows=0, missing_t_truth_rows=0, missing_t1_truth_rows=1,
               unresolved_exit_rows=0)
    return dict(schema_version="dc20_primary_observation_summary_v1", scope="frozen_primary_topn",
                public_start_signal_date="20260828", status="PARTIAL_TRUTH", as_of_date="20260904",
                generated_at_utc="2026-09-05T08:00:00Z", latest_signal_date=day["signal_date"],
                latest_exec_date=day["exec_date"], latest_exit_date=day["exit_date"],
                daily_summaries=[day], bindings=[dict(signal_date="20260828", status="PROSPECTIVE")],
                policy=dict(missing_truth_is_zero_return=False, mixed_shadow_ledger_included=False,
                            retrospective_recovery_included=False, official_trade_action_created=False,
                            performance_role="historical_reconstruction_of_frozen_P0_predictions",
                            policy_definition_date="20260905", return_strategy_forward_evidence=False),
                statistics=dict(observation_dates=1, observation_rows=2, premarket_valid_rows=2,
                                t_validated_rows=2, final_verified_trades=1, matured_portfolio_dates=0,
                                pending_t_rows=0, pending_t1_rows=0, missing_t_truth_rows=0,
                                missing_t1_truth_rows=1, unresolved_exit_rows=0, excluded_retrospective_rows=0,
                                continuation_hit_rate=0.5, market_positive_rate=0.5, final_win_rate=0.0,
                                equal_slot_cumulative_return=None, equal_slot_max_drawdown=None,
                                portfolio_curve_reason="UNRESOLVED_OR_MISSING_MATURE_TRUTH"))


def _run(body: str, payload: dict | None = None):
    names = ("emptyPublicObservationStatistics", "validatedPublicObservationStatistics",
             "refreshPublicObservationStatistics", "publicStatisticsPlan", "primaryObservationAvailability",
             "primaryObservationStatusHtml", "primaryPortfolioExplanation", "renderMetrics", "renderObservationStatistics", "canonicalYmd",
             "finiteNumber", "escapeHtml", "metric", "integerText", "pct", "signedPct", "valueTone",
             "dateText", "number")
    prelude = """
const PUBLIC_STATISTICS_START_SIGNAL_DATE = "20260828";
const PUBLIC_OBSERVATION_STATISTICS_PATH = "outputs/decision/primary_observation/summary.json";
const state = {index:0, currentThreeRank:{signal_date:"20260828"}, publicObservationLoad:{status:"ready"}};
const els = Object.fromEntries(["metrics","metricsCount","observationMetrics","observationUpdate","observationNote"].map(k=>[k,{innerHTML:"",textContent:""}]));
const validatedThreeRankContract = value => value;
"""
    script = prelude + "\n".join(_function(name) for name in names)
    script += "\nconst payload = " + json.dumps(payload if payload is not None else _payload()) + ";\n"
    script += "state.currentPublicObservationStatistics = payload;\n(async()=>{" + body + "})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([NODE, "-e", script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_primary_summary_is_valid_and_old_auction_scope_is_rejected():
    assert _run("console.log(JSON.stringify(!!validatedPublicObservationStatistics(payload)))") is True
    for field, value in (("schema_version", "decision_observation_validation_v4_auction_truth"),
                         ("scope", "mixed_profit_top2"), ("public_start_signal_date", "20260721")):
        item = _payload()
        item[field] = value
        assert _run("console.log(JSON.stringify(!!validatedPublicObservationStatistics(payload)))", item) is False


def test_wrong_counts_duplicate_days_and_mixed_shadow_statistics_are_rejected():
    for mutation in ("payload.statistics.observation_rows = 3;",
                     "payload.statistics.forward_shadow = {shadow_entries:2};",
                     "payload.daily_summaries.push(payload.daily_summaries[0]);",
                     "payload.policy.missing_truth_is_zero_return = true;"):
        assert _run(mutation + "console.log(JSON.stringify(!!validatedPublicObservationStatistics(payload)))") is False


@pytest.mark.parametrize("error,status", [("HTTP 404", "missing"), ("HTTP 503", "error")])
def test_unavailable_source_does_not_synthesize_zero_samples(error, status):
    result = _run(f"globalThis.fetchPath = async()=>{{throw new Error({json.dumps(error)})}};"
                  "await refreshPublicObservationStatistics();renderMetrics({});renderObservationStatistics({});"
                  "console.log(JSON.stringify({load:state.publicObservationLoad,stats:state.currentPublicObservationStatistics,html:els.metrics.innerHTML,note:els.observationNote.textContent}));")
    assert result["load"]["status"] == status
    assert result["stats"] is None
    assert "冻结名单样本" not in result["html"]
    assert "不展示0笔" in result["note"]


def test_legacy_payload_does_not_pass_as_empty_primary_statistics():
    result = _run("globalThis.fetchPath=async()=>({schema_version:'decision_observation_validation_v4_auction_truth',observation_rows:0});"
                  "await refreshPublicObservationStatistics();console.log(JSON.stringify(state.publicObservationLoad));")
    assert result["status"] == "invalid"


def test_stale_summary_does_not_claim_current_primary_list_has_zero_rows():
    view = _run("state.currentThreeRank.signal_date='20260904';console.log(JSON.stringify(primaryObservationAvailability()));")
    assert view["ready"] is False
    assert view["title"] == "当前D榜尚未进入验证统计"
    assert "2026-09-04" in view["message"]


def test_future_dates_and_missing_mature_truth_have_different_messages():
    missing = _run("console.log(JSON.stringify(primaryObservationAvailability()));")
    assert "到期" in missing["title"]
    pending = _payload()
    pending["status"] = "PENDING_DATES"
    pending["statistics"].update(missing_t1_truth_rows=0, pending_t1_rows=1)
    view = _run("console.log(JSON.stringify(primaryObservationAvailability()));", pending)
    assert view["title"] == "等待验证日期"
    assert "未到期" in view["message"]


def test_render_uses_primary_timestamp_counts_and_scope_without_old_action_or_shadow():
    result = _run("const plan={observation_validation:{signal_date:'20260826',generated_at_utc:'2026-08-26T00:00:00Z'},observation_statistics:{observation_rows:999,forward_shadow:{shadow_entries:99}}};"
                  "renderMetrics(plan);renderObservationStatistics(plan);console.log(JSON.stringify(els));")
    assert "2条" in result["metricsCount"]["textContent"]
    assert "二筛影子入选" not in result["metrics"]["innerHTML"]
    assert "正式人工建议数" not in result["metrics"]["innerHTML"]
    assert "2026-08-31" in result["observationMetrics"]["innerHTML"]
    assert "尚未生成" not in result["observationNote"]["textContent"]
    assert "8/26" not in result["observationNote"]["textContent"]
    assert "不合并混合盈利Top1/Top2" in result["observationNote"]["textContent"]


def test_retrospective_current_day_is_explicitly_excluded_instead_of_reported_missing():
    result = _run("state.currentThreeRank.signal_date='20260901';payload.bindings.push({signal_date:'20260901',status:'EXCLUDED_RETROSPECTIVE'});console.log(JSON.stringify(primaryObservationAvailability()));")
    assert result["ready"] is True
    assert result["title"] == "当前D为回溯记录"


def test_confirmed_zero_candidate_day_remains_distinct_from_unavailable_data():
    result = _run("payload.status='READY';for(const k of ['rows','t_validated_rows','final_verified_trades','settled_rows','missing_t1_truth_rows'])payload.daily_summaries[0][k]=0;"
                  "for(const k of ['observation_rows','premarket_valid_rows','t_validated_rows','final_verified_trades','missing_t1_truth_rows'])payload.statistics[k]=0;"
                  "payload.statistics.continuation_hit_rate=null;payload.statistics.market_positive_rate=null;payload.statistics.final_win_rate=null;"
                  "renderMetrics({});console.log(JSON.stringify({valid:!!validatedPublicObservationStatistics(payload),count:els.metricsCount.textContent,html:els.metrics.innerHTML}));")
    assert result["valid"] is True
    assert "1日 / 0条" in result["count"]
    assert "冻结名单样本" in result["html"]


def test_incomplete_portfolio_scope_is_visible_next_to_cumulative_returns():
    result = _run("renderMetrics({});renderObservationStatistics({});console.log(JSON.stringify(els));")
    assert "到期样本尚未全部完成，暂不报告完整窗口累计收益和回撤" in result["metrics"]["innerHTML"]
    assert "从严格T+1起首个可交易日开盘退出" in result["observationNote"]["textContent"]
    assert "完整窗口按D合成累计" in result["metrics"]["innerHTML"]
    assert "非新策略前向实盘成绩" in result["metrics"]["innerHTML"]


def test_reconstructed_policy_cannot_claim_forward_performance_or_report_partial_nav():
    for mutation in ("payload.policy.return_strategy_forward_evidence=true;",
                     "payload.statistics.equal_slot_cumulative_return=0.0037;"):
        assert _run(mutation + "console.log(JSON.stringify(!!validatedPublicObservationStatistics(payload)))") is False


def test_delayed_exit_capital_overlap_is_reported_without_synthetic_profit():
    result = _run("payload.statistics.portfolio_curve_reason='DELAYED_EXIT_CAPITAL_OVERLAP_NOT_MODELED';renderMetrics({});console.log(JSON.stringify(els.metrics.innerHTML));")
    assert "资金占用重叠尚未建模" in result
    assert "暂不报告完整窗口累计收益和回撤" in result
