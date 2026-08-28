from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "decision.html"


def test_dashboard_exposes_daily_research_without_fabricating_action_truth() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "日线研究候选（非买入建议）" in text
    assert "正式行动仍只认同同日 Auction action_plan" in text
    assert 'id="researchCount" class="count" aria-live="polite"' in text
    assert "fetchPath(info.eval_url ||" in text
    assert 'String(markdownExecDate) === reportDate' in text
    assert 'String(evaluation?.exec_date || "") === reportDate' in text
    assert 'parseMarkdownTable(md, "Full Candidate Pool")' in text
    assert "renderResearchOutput(md, evaluation, info, plan)" in text
    assert "researchOnlyPlan(info, evaluation, md)" in text
    assert "daily_research_only: true" in text
    assert "candidates: []" in text
    assert 'metric("Daily 入选（非行动）"' in text
    assert 'metric("正式目标"' not in text
    assert "同日竞价行动尚未生成" in text
    assert "只有同日 action_plan 中明确标记的行动才属于正式行动" in text


def test_dashboard_research_fallback_preserves_dates_gate_and_cache() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert 'signal_date: evaluation?.signal_date || extractField(md, "signal_date")' in text
    assert 'exec_date: evaluation?.exec_date || extractField(md, "exec_date")' in text
    assert 'exit_date: evaluation?.exit_date || extractField(md, "exit_date")' in text
    assert 'evaluation?.execution_gate || extractField(md, "execution_gate")' in text
    assert 'const CACHE_KEY = "dc20-decision-dashboard-v5-cache"' in text
    assert "JSON.stringify({ info, plan, md, evaluation" in text
    assert 'els.reportDetails.open = info.action_available !== true' not in text
    assert text.count("els.reportDetails.open = false;") == 3
    assert '<details id="reportDetails" class="panel" open>' not in text
    assert "validatedCachedState(loadCache(), targetInfo)" in text
    assert 'const identityFields = ["report_date", "report_file", "report_url", "eval_url", "action_url", "research_url", "research_kind", "research_archive_url"]' in text
    assert "targetInfo?.action_available === true" in text
    assert "if (!evidence.available) return null" in text


def test_dashboard_fails_closed_when_same_day_evidence_is_missing() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "const actionExpected = info.action_available === true" in text
    assert "if (actionExpected && !actionPath)" in text
    assert 'planResult.status !== "fulfilled"' in text
    assert 'typeof plan !== "object" || Array.isArray(plan)' in text
    assert "同日行动计划内容无效，不能降级成研究状态" in text
    assert "同日行动计划读取失败，不能降级成尚未生成" in text
    assert "同日行动计划日期与报告索引不一致" in text
    assert "if (!evidence.available) throw new Error" in text
    assert "同日报告与评估均不可用或日期不匹配" in text
    assert "未展示任何研究候选" in text
    assert "markdownSignalDate !== evaluationSignalDate" in text
    assert "markdownExitDate !== evaluationExitDate" in text
    assert "validatedActionPlan(info, plan)" in text
    assert 'startsWith("decision_action_plan_v")' in text
    assert "plan.broker_connected !== false" in text
    assert "plan.execution_or_fill_claimed === true" in text
    assert "signalDate < execDate && execDate < exitDate" in text


def test_dashboard_places_research_first_and_hides_unavailable_auction_panels() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert text.index('id="researchContent"') < text.index('id="sentimentPanel"')
    assert text.index('id="researchContent"') < text.index('id="stagePanel"')
    assert '<details id="sentimentPanel" class="panel observation-disclosure">' in text
    assert '<details id="sentimentPanel" class="panel observation-disclosure" open>' not in text
    assert 'const available = plan?.daily_research_only !== true' in text
    assert 'els.sentimentPanel.hidden = !available' in text
    assert 'els.stagePanel.hidden = !available' not in text
    assert (
        'els.stagePanel.hidden = !(available || '
        '(primaryContract && primaryContract !== false))'
        in text
    )
    assert 'els.auditPanel.hidden = !available' in text
    assert 'els.researchPanel.hidden = fullContext' in text
    assert 'if (researchPlan.historical_parity === true)' in text
    assert 'window.location.replace(latestUrl.toString())' in text
    assert 'title="重新加载最新版页面"' in text
    assert (
        'const DASHBOARD_VERSION = "independent-three-rank-v9-primary-path-truth"'
        in text
    )
    assert 'const researchExpected = info.research_available === true' in text
    assert "validatedResearchContext(info, researchResult.value)" in text


def test_current_three_rank_survives_missing_or_stale_action_report() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    status = text.split("function renderStatus(plan)", 1)[1].split(
        "function renderAuctionOnlyPanels", 1
    )[0]
    auction_panels = text.split("function renderAuctionOnlyPanels(plan)", 1)[1].split(
        "function renderMetrics", 1
    )[0]
    initializer = text.split("async function initialize(force = false)", 1)[1].split(
        'els.refreshBtn.addEventListener("click"', 1
    )[0]
    core_only = text.split("function renderPrimaryCoreOnly(error)", 1)[1].split(
        "async function initialize", 1
    )[0]

    # The current D/T/T+1 identity comes from the validated three-rank
    # contract, before any old Action/report dates are considered.
    assert "validatedThreeRankContract(state.currentThreeRank)" in status
    assert "primaryContract.signal_date" in status
    assert "primaryContract.exec_date" in status
    assert "primaryContract.exit_date" in status
    assert "D日核心排名（不依赖历史Action）" in status

    # Only Action-owned observation panels are hidden.  A valid P0 contract
    # keeps the promotion stage panel visible and renders it independently.
    assert "els.sentimentPanel.hidden = !available" in auction_panels
    assert "els.auditPanel.hidden = !available" in auction_panels
    assert "primaryContract && primaryContract !== false" in auction_panels
    assert "renderStageWatchlist(plan)" in auction_panels

    # P0/P1 loading precedes report_index/Action, and any missing, stale, or
    # invalid old report chain falls back to the independent core view.
    assert initializer.index("state.currentThreeRank = await loadCurrentThreeRank()") < initializer.index(
        'fetchPath("outputs/decision/report_index.json")'
    )
    assert "renderPrimaryCoreOnly(error)" in initializer
    assert "旧报告/Action未参与核心排名" in core_only
    assert "renderStatus(plan)" in core_only
    assert "renderAuctionOnlyPanels(plan)" in core_only


def test_dashboard_prefers_hash_bound_dc20_cutover_evidence() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert 'const INDEPENDENCE_CUTOVER_SIGNAL_DATE = "20260821"' in text
    assert 'fetchPath("outputs/decision/three_rank_index.json")' in text
    assert 'index.index_kind !== "dated_three_rank_pointer_only"' in text
    assert "index.data_alias !== false" in text
    assert "index.latest_contract_sha256" in text
    assert 'fetchPath(index.latest_contract_url, "bytes")' in text
    assert 'fetchPath(index.latest_csv_url, "bytes")' in text
    assert "sha256Hex(artifactBytes)" in text
    assert "NOT_READY_NO_FROZEN_TOP10" in text
    assert 'info?.research_kind === "dc20_independent"' in text
    assert "if (!independentDc20Action) plan = researchPlan" in text
