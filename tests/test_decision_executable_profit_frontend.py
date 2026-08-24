from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "decision.html"


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag
        for key, value in attrs:
            if key == "id" and value:
                self.ids.append(value)


def _text() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _function(text: str, name: str, next_name: str) -> str:
    pattern = rf"    (?:async )?function {re.escape(name)}\("
    start = re.search(pattern, text)
    assert start is not None, name
    end = re.search(
        rf"\n    (?:async )?function {re.escape(next_name)}\(",
        text[start.start() :],
    )
    assert end is not None, next_name
    return text[start.start() : start.start() + end.start()]


def test_public_research_layer_is_separate_from_frozen_promotion_panel() -> None:
    text = _text()
    collector = _IdCollector()
    collector.feed(text)

    required_ids = {
        "stagePanel",
        "stageContent",
        "executableProfitResearchPanel",
        "executableProfitResearchBanner",
        "executableProfitResearchState",
        "executableProfitResearchContent",
        "executableProfitShadowPanel",
        "executableProfitShadowState",
        "executableProfitShadowMetrics",
        "executableProfitShadowContent",
        "executableProfitShadowNote",
    }
    assert required_ids.issubset(collector.ids)
    assert len(collector.ids) == len(set(collector.ids))
    assert text.index('id="stageContent"') < text.index(
        'id="executableProfitResearchPanel"'
    )
    assert "在冻结晋级名单内独立排序；晋级成员与晋级排名保持不变" in text
    assert "promotion_rank_is_independent === true" in text
    assert "may_change_promotion_membership_or_rank: false" in text


def test_public_research_layer_labels_proxy_scores_without_probability_claim() -> None:
    text = _text()
    for token in (
        "可实现盈利研究排序",
        "公开研究可见 · 未校准代理分 · 非正式概率 · 仅供人工决策参考",
        "联合代理分",
        "可买代理分",
        "条件盈利代理分",
        "三项都不是已校准概率",
        "formal_probability_allowed: false",
        "formal_rank_allowed: false",
        "human_decision_support_only: true",
        "official_trade_action_allowed: false",
        "broker_or_order_integration_allowed: false",
    ):
        assert token in text
    assert "research_joint_proxy_score - row.research_fill_proxy_score * row.research_conditional_profit_score" in text
    assert 'number(row.research_joint_proxy_score, 4)' in text
    assert 'pct(row.research_joint_proxy_score)' not in text


def test_real_candidate_count_and_shadow_slots_are_never_padded() -> None:
    text = _text()
    for token in (
        "D日硬范围候选为0 · 不补票 · 当日无Shadow",
        "完整候选1支 · 仅冻结Shadow Top1 · 不补Top2",
        "不足10不补票",
        "Math.min(2, candidateCount)",
        'ranking.candidate_count_rule === "show exactly N for 0<=N<=10; never pad"',
        'ranking.shadow_slot_rule === "min(2, N); no padding"',
        'ranking.shadow_price_use === "D-frozen research price cap only; not a buy instruction"',
        "Shadow T验证行数与min(2,N)不一致",
        "当日没有2→3或3→4候选",
        "不从池外补票",
    ):
        assert token in text
    assert "rows.length <= 10" in text
    assert "projection.candidate_count === rows.length" in text
    assert "row.executable_profit_research_rank === indexValue + 1" in text


def test_d_frozen_price_is_visible_and_is_not_a_buy_instruction() -> None:
    text = _text()
    for token in (
        "shadow_max_price",
        "shadow_price_basis",
        "shadow_price_source_sha256",
        "D冻结最高研究价",
        "T代理价超过该价即记为未成交，不是购买建议",
        "D日正式安全价冻结",
        "D日观察价冻结",
        "D日模型诊断价",
        "D收盘保守价",
        "publicRow.shadow_price_source_sha256 === selection.source_d_feature.file_sha256",
    ):
        assert token in text
    assert "超过该价" in text
    assert "不是购买建议" in text


def test_index_projection_statistics_and_all_sources_are_sha_bound() -> None:
    text = _text()
    for token in (
        "dc20_executable_profit_public_research_index_v1",
        "dated_executable_profit_research_pointer_only",
        "dc20_executable_profit_public_research_projection_v1",
        "immutable_d_frozen_executable_profit_research_projection",
        "dc20_executable_profit_public_shadow_statistics_v1",
        "immutable_asof_executable_profit_shadow_statistics_projection",
        "shadow_statistics_${signalDate}_asof_${asOfDate}.json",
        "fetchShaBoundJson(index.latest_projection_json_url",
        "fetchPath(index.latest_projection_csv_url, \"bytes\")",
        "fetchShaBoundJson(index.latest_statistics_url",
        "fetchShaBoundJson(bindings.selection.json_path",
        "fetchShaBoundJson(bindings.three_rank.json_path",
        "冻结Shadow选择CSV字节SHA256不一致",
        "冻结晋级三排名CSV字节SHA256不一致",
        "sourceContract.bundle_sha256 === projection.source_bundle_sha256",
        "currentContract.top10_members_sha256 === projection.top10_members_sha256",
        "executableProfitMembersSha256(projection.signal_date, codes)",
        "公开Shadow T验证与SHA绑定来源不一致",
        "公开Shadow T+1结算与SHA绑定来源不一致",
        "公开累计统计与SHA绑定summary来源不一致",
    ):
        assert token in text
    assert "top10-decision" not in _function(
        text,
        "loadCurrentExecutableProfitResearch",
        "refreshCurrentExecutableProfitResearch",
    )


def test_hash_or_date_failure_hides_only_new_layer() -> None:
    text = _text()
    reset = _function(
        text,
        "resetExecutableProfitResearchView",
        "renderExecutableProfitCohorts",
    )
    refresh = _function(
        text,
        "refreshCurrentExecutableProfitResearch",
        "executableProfitPriceBasisLabel",
    )

    assert "研究投影校验失败 · 新层已隐藏 · 晋级排序不受影响" in reset
    assert "失败只关闭本层，不会覆盖晋级排序" in reset
    assert "executableProfitResearchContent" in reset
    assert "executableProfitShadowContent" in reset
    assert "stageContent" not in reset
    assert "stageCount" not in reset
    assert 'status: error?.executableProfitIndexMissing === true ? "missing" : "invalid"' in refresh
    assert "showError" not in refresh


def test_shadow_truth_statistics_and_human_results_are_separate() -> None:
    text = _text()
    for token in (
        "Shadow Top1 / Top2 · 前向验证与累计统计",
        "latest_selected_rows",
        "t_validation_status",
        "t1_settlement_status",
        "net_return_after_cost",
        "strategy_slot_return",
        "actual_human_trade_return === null",
        "all_selected_slots",
        "shadow_slot_1",
        "shadow_slot_2",
        "observed_signal_dates",
        "target_signal_dates === 180",
        "source_as_of_date",
        "input_files_sha256",
        "proxy_buyable_rate",
        "equal_weight_cumulative_return",
        "manual_actual_trade_ledger",
        "official_trade_action_ledger",
        "模型 Shadow 与人工作业结果严格隔离",
        "人工作业结果使用独立账本，不回填模型Shadow",
    ):
        assert token in text
    assert "diagnostics.status === \"UNCALIBRATED\"" in text
    assert "diagnostics.brier_score == null" in text


def test_downloads_are_exact_dated_projection_and_asof_statistics() -> None:
    text = _text()
    for token in (
        "下载研究索引",
        "下载研究 CSV",
        "下载研究 JSON",
        "下载 Shadow 统计",
        "index.latest_projection_csv_url",
        "index.latest_projection_json_url",
        "index.latest_statistics_url",
        "projection_${signalDate}",
        "shadow_statistics_${signalDate}_asof_${asOfDate}.json",
        "等待首个功能完整D日快照 · 不回填旧日",
        "历史页无dated绑定 · 不回填",
    ):
        assert token in text
