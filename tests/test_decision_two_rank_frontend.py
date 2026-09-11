from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for renderer contract tests")


def _function(name: str) -> str:
    source = (ROOT / "decision.html").read_text(encoding="utf-8")
    match = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", source, re.M | re.S)
    assert match, name
    return match.group()


def _run(body: str, count: int = 2):
    day = "20260908" if count > 6 else "20260904"
    mixed = json.loads((ROOT / f"outputs/decision/executable_profit_research/projection_{day}.json").read_text())
    single = json.loads((ROOT / f"outputs/decision/legacy_profit_relative_research/projection_{day}.json").read_text())
    mixed["rows"] = mixed["rows"][:count]
    # Renderer fixtures; production loader binding validation remains tested separately.
    codes = {row["ts_code"] for row in mixed["rows"]}
    single["rows"] = [row for row in single["rows"] if row["ts_code"] in codes]
    contract = {key: mixed[key] for key in ("signal_date", "exec_date", "exit_date", "top10_members_sha256")}
    contract.update(bundle_sha256=mixed["source_bundle_sha256"], feature_snapshot_sha256=mixed["source_feature_snapshot_sha256"], rows=mixed["rows"],
                    models={"promotion": {"status": "READY"}}, generated_at_utc="2026-09-04T14:00:00Z")
    names = ("renderLegacyProfitBenchmark", "renderStatus", "unifiedProfitView", "renderThreeRankWatchlist",
             "renderPrimaryMixedProfitResearch", "refreshCurrentLegacyProfitRelativeResearch",
             "canonicalYmd", "finiteNumber", "escapeHtml", "integerText", "number", "pct",
             "signedPct", "valueTone", "dateText", "pathClass", "truthClass",
             "continuationLabel", "beijingDateTimeText", "threeRankRowTruth", "threeRankTruthClock", "threeRankTruthStatusLabel")
    script = """
const state = {index:0};
const bodyNode = {innerHTML:''};
const sortHandlers = {};
const sortButtons = ['promotion_rank','mixed_profit_rank'].map(field=>({dataset:{threeRankSort:field},classList:{toggle:()=>{}},setAttribute:()=>{},addEventListener:(type,fn)=>{sortHandlers[field]=fn;}}));
const els = new Proxy({}, {get(target, key) {
  return target[key] ??= {hidden:false, innerHTML:'', textContent:'', className:'',
    querySelector:()=>bodyNode, querySelectorAll:selector=>selector.startsWith('button')?sortButtons:[], setAttribute:()=>{}};
}});
const document = {querySelectorAll:()=>[]};
const validatedThreeRankContract = value=>value;
const localUrl = value=>value;
const EXECUTABLE_PROFIT_RESEARCH_ROOT = 'outputs/decision/executable_profit_research';
const LEGACY_PROFIT_RELATIVE_RESEARCH = {root:'outputs/decision/legacy_profit_relative_research'};
const INDEPENDENCE_CUTOVER_SIGNAL_DATE = '20260821';
const PRIMARY_MIXED_PROFIT_SCHEMA = 'dc20_primary_mixed_profit_research_projection_v1';
let shadowProjection = null;
function renderPrimaryProfitShadowSidecar(loaded) { shadowProjection = loaded.projection; }
"""
    script += "\n".join(_function(name) for name in names)
    script += f"\nconst mixed={json.dumps(mixed)}, single={json.dumps(single)}, contract={json.dumps(contract)};\n"
    script += "state.currentThreeRank=contract; state.currentLegacyProfitRelativeResearch=single; state.currentExecutableProfitResearch={status:'ready',kind:'primary_core',projection:mixed,index:{}};\n"
    script += "(async()=>{" + body + "})().catch(error=>{console.error(error);process.exit(1)});"
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_promotion_renderer_preserves_original_rank_and_path_without_legacy_sort():
    output = _run("const before=JSON.stringify(contract);renderThreeRankWatchlist({},contract);"
                  "console.log(JSON.stringify({header:els.stageContent.innerHTML,rows:bodyNode.innerHTML,"
                  "codes:[...contract.rows].sort((a,b)=>a.promotion_rank-b.promotion_rank).map(r=>r.ts_code),"
                  "unchanged:before===JSON.stringify(contract)}));")
    assert output["unchanged"]
    assert "单一盈利排序" not in output["header"]
    assert "legacy_profit_relative_rank" not in output["header"]
    assert "模型分值" not in output["header"]
    for label in ("连板路径", "路径变化", "晋级概率", "T晋级结果", "T+1净收益"):
        assert label in output["header"]
    assert output["rows"].index(output["codes"][0]) < output["rows"].index(output["codes"][1])


def test_stock_code_industry_are_separate_and_ready_legend_is_removed():
    output = _run("renderThreeRankWatchlist({},contract);console.log(JSON.stringify({header:els.stageContent.innerHTML,html:bodyNode.innerHTML,rows:contract.rows}));", count=6)
    for label in ("股票", "代码", "行业"):
        assert f'<th scope="col" class="left">{label}</th>' in output["header"]
    headers = re.findall(r'<th\b[^>]*>(.*?)</th>', output["header"], re.S)
    assert headers[:6] == ["代码", "股票", "行业", "晋级", "连板路径", "路径变化"]
    for removed in ("股票 / 代码 / 行业", "rank-legend", "晋1–3", "盈1–2", "同D盈利已校验"):
        assert removed not in output["header"]
    for row in output["rows"]:
        rendered = re.search(rf'<tr data-code="{re.escape(row["ts_code"])}".*?</tr>', output["html"], re.S).group()
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', rendered, re.S)
        assert len(cells) == 12
        assert cells[0] == row["ts_code"]
        assert row["name"] in cells[1] and row["ts_code"] not in cells[1]
        assert cells[2] == row["industry"] and "company-line" in cells[1]
        assert cells[3] == row["stage_transition"]
        assert "truth-badge" in cells[4] and "<small" not in cells[4]
        assert "<" not in cells[5]
    assert output["html"].count('aria-label="晋级排序第') == 3
    assert output["html"].count('aria-label="盈利排序第') == 2


@pytest.mark.parametrize("status,now,expected", [
    ("MISSING_T_TRUTH", "2026-09-07T08:00:00Z", "待更新"),
    ("INVALID_T_TRUTH", "2026-09-07T08:00:00Z", "T 真值读取或校验失败"),
    ("MISSING_T_TRUTH", "2026-09-07T01:00:00Z", "T 2026-09-07 尚未收盘"),
    ("READY", "2026-09-07T08:00:00Z", "晋级涨停"),
])
def test_t_result_shortens_only_due_missing_truth_without_changing_validation(status, now, expected):
    output = _run(f"Date.now=()=>Date.parse('{now}');"
                  f"state.currentThreeRankTTruth={{...contract,status:'{status}',rows:contract.rows.map(r=>({{ts_code:r.ts_code,continuation_limit_up_hit:1}}))}};"
                  "const before=JSON.stringify(state.currentThreeRankTTruth);renderThreeRankWatchlist({},contract);"
                  "console.log(JSON.stringify({html:bodyNode.innerHTML,unchanged:before===JSON.stringify(state.currentThreeRankTTruth)}));")
    cells = re.findall(r'<td[^>]*data-field="t-promotion"[^>]*>(.*?)</td>', output["html"], re.S)
    assert cells and all(cell == expected for cell in cells)
    assert output["unchanged"]
    if expected == "待更新":
        assert 'title="T 2026-09-07 已到期，行情待更新"' in output["html"]


def test_independent_rank_never_borrows_old_action_or_wrong_bundle_returns():
    output = _run("Date.now=()=>Date.parse('2026-09-09T08:00:00Z');"
                  "state.currentThreeRankObservationTruth={...contract,bundle_sha256:'wrong',status:'READY',rows:contract.rows.map(r=>({...r,actual_net_return:.99,validation_status:'FINAL_VERIFIED_PROXY'}))};"
                  "const old={stage_watchlist:contract.rows.map(r=>({...r,actual_net_return:.88,continuation_limit_up_hit:1,validation_status:'FINAL_VERIFIED',validation_status_label:'旧Action已结算'}))};"
                  "const before=JSON.stringify(contract);renderThreeRankWatchlist(old,contract);"
                  "console.log(JSON.stringify({rows:bodyNode.innerHTML,unchanged:before===JSON.stringify(contract)}));")
    assert output["unchanged"]
    assert "99.00%" not in output["rows"] and "88.00%" not in output["rows"]
    assert "旧Action已结算" not in output["rows"]
    assert "逐行验证尚未发布" in output["rows"]


def test_single_baseline_missing_does_not_block_two_main_rankings():
    result = _run("state.currentLegacyProfitRelativeResearch=null;state.legacyProfitBenchmarkLoad={status:'missing'};"
                  "renderStatus({});console.log(JSON.stringify({status:els.statusTitle.textContent,baseline:els.legacyProfitBenchmarkContent.innerHTML}));")
    assert result["status"] == "晋级榜与盈利排序已生成"
    assert "同D研究基准尚未发布" in result["baseline"]


@pytest.mark.parametrize("mutation", ["state.currentExecutableProfitResearch.status='invalid';",
                                     "mixed.signal_date='20260903';",
                                     "mixed.source_bundle_sha256='wrong';",
                                     "mixed.top10_members_sha256='wrong';"])
def test_invalid_profit_binding_cannot_show_ready_or_substitute_single(mutation):
    assert "独立校验中" in _run(mutation + "renderStatus({});console.log(JSON.stringify(els.statusTitle.textContent));")


def test_history_and_mismatched_baseline_clear_previously_visible_data():
    result = _run("renderLegacyProfitBenchmark(contract);const present=els.legacyProfitBenchmarkContent.innerHTML;"
                  "single.source_bundle_sha256='wrong';renderLegacyProfitBenchmark(contract);const rejected=els.legacyProfitBenchmarkContent.innerHTML;"
                  "state.index=1;renderLegacyProfitBenchmark(contract);console.log(JSON.stringify({present,rejected,history:els.legacyProfitBenchmarkContent.innerHTML,hidden:els.legacyProfitBenchmarkPanel.hidden}));")
    assert "下载基准 CSV" in result["present"]
    assert "同D研究基准绑定不一致" in result["rejected"]
    assert "下载基准 CSV" not in result["rejected"]
    assert result["hidden"] and result["history"] == ""


@pytest.mark.parametrize("count", [0, 1, 2, 6, 10])
def test_profit_top2_no_padding_same_frozen_rows_and_explicit_research_engine(count):
    result = _run("const before=JSON.stringify(mixed);renderThreeRankWatchlist({},contract);renderPrimaryMixedProfitResearch(state.currentExecutableProfitResearch);"
                  "console.log(JSON.stringify({html:els.executableProfitResearchContent.innerHTML,rows:bodyNode.innerHTML,unchanged:before===JSON.stringify(mixed),"
                  "sameShadow:shadowProjection===mixed,codes:mixed.rows.map(r=>r.ts_code)}));", count=count)
    assert result["unchanged"] and result["sameShadow"]
    assert '<article' not in result["html"]
    assert '<details' not in result["html"]
    assert result["rows"].count('aria-label="盈利排序第') == min(count, 2)
    assert "当前来源：混合盈利研究引擎" in result["html"]
    assert "不是盈利概率或预期收益" in result["html"]
    if count:
        assert "完整盈利排序" in result["html"]
        assert "联合代理分（非胜率）" in result["html"]
        assert result["html"].count('<table ') == 0
        assert result["rows"].count('<td class="left name" data-field="stock">') == count
        for code in result["codes"]:
            assert result["rows"].count(f'data-code="{code}"') == 1
    else:
        assert "真实N=0" in result["html"]


def test_top2_badges_follow_profit_rank_after_company_name_not_promotion_or_input_order():
    result = _run("mixed.rows.reverse();const before=JSON.stringify(mixed);"
                  "renderThreeRankWatchlist({},contract);sortHandlers.mixed_profit_rank();"
                  "console.log(JSON.stringify({html:bodyNode.innerHTML,"
                  "unchanged:before===JSON.stringify(mixed),rows:mixed.rows}));", count=6)
    assert result["unchanged"]
    body = result["html"]
    cells = re.findall(r'<td class="left name" data-field="stock">(.*?)</td>', body)
    ranked = sorted(result["rows"], key=lambda row: row["executable_profit_research_rank"])
    assert len(cells) == len(ranked) == 6
    for row, cell in zip(ranked, cells):
        rank = row["executable_profit_research_rank"]
        assert cell.startswith('<span class="company-line">' + row["name"])
        if rank in (1, 2):
            assert f'aria-label="盈利排序第{rank}名">盈{rank}</span>' in cell
        else:
            assert 'rank-profit' not in cell


def test_inline_profit_badge_preserves_name_escaping_and_tied_score_label():
    html = _run("contract.rows[0].name='<img src=x onerror=alert(1)>';mixed.rows[0].rank_tied=true;"
                "renderThreeRankWatchlist({},contract);"
                "console.log(JSON.stringify(bodyNode.innerHTML));", count=2)
    assert '<img' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;<span class="rank-mark rank-profit"' in html
    assert '并列分' in html


def test_optional_baseline_refresh_clears_old_projection_on_error():
    result = _run("globalThis.loadCurrentLegacyProfitRelativeResearch=async()=>{throw new Error('HTTP 404')};"
                  "await refreshCurrentLegacyProfitRelativeResearch(contract);console.log(JSON.stringify({projection:state.currentLegacyProfitRelativeResearch,load:state.legacyProfitBenchmarkLoad}));")
    assert result["projection"] is None
    assert result["load"]["status"] == "missing"


@pytest.mark.parametrize("mutation", [
    "state.currentExecutableProfitResearch=null;",
    "state.currentExecutableProfitResearch.status='invalid';",
    "state.index=1;",
    "mixed.signal_date='20260903';",
    "mixed.exec_date='20260908';",
    "mixed.exit_date='20260909';",
    "mixed.source_bundle_sha256='wrong';",
    "mixed.source_feature_snapshot_sha256='wrong';",
    "mixed.top10_members_sha256='wrong';",
    "mixed.rows[0].promotion_rank=999;",
    "mixed.rows[0].predicted_promotion_probability=.999;",
    "mixed.rows[0].ts_code=mixed.rows[1].ts_code;",
    "mixed.rows[0].executable_profit_research_rank=2;",
    "mixed.rows[0].research_joint_proxy_score=null;",
    "state.currentExecutableProfitResearch.kind='forward_shadow';",
])
def test_unified_table_rejects_stale_or_unbound_profit_without_losing_promotion(mutation):
    result = _run("renderThreeRankWatchlist({},contract);" + mutation +
                  "const before=JSON.stringify(contract);renderThreeRankWatchlist({},contract);"
                  "const first=bodyNode.innerHTML;sortHandlers.mixed_profit_rank();"
                  "console.log(JSON.stringify({html:els.stageContent.innerHTML,rows:bodyNode.innerHTML,"
                  "count:contract.rows.length,unchanged:before===JSON.stringify(contract),ignored:first===bodyNode.innerHTML}));", count=6)
    assert result["unchanged"] and result["ignored"]
    assert result["rows"].count('<tr data-code=') == result["count"]
    assert 'aria-label="盈利排序第' not in result["rows"]
    assert result["rows"].count('data-profit-rank=""') == result["count"]
    assert re.search(r'data-three-rank-sort="mixed_profit_rank"[^>]*disabled', result["html"])


def test_sort_toggle_preserves_both_frozen_orders_and_member_identity():
    result = _run("mixed.rows.reverse();const before=JSON.stringify({contract,mixed});renderThreeRankWatchlist({},contract);"
                  "const promotion=bodyNode.innerHTML;sortHandlers.mixed_profit_rank();const profit=bodyNode.innerHTML;"
                  "sortHandlers.promotion_rank();console.log(JSON.stringify({promotion,profit,restored:bodyNode.innerHTML===promotion,"
                  "unchanged:before===JSON.stringify({contract,mixed})}));", count=10)
    assert result["unchanged"] and result["restored"]
    assert [int(x) for x in re.findall(r'data-promotion-rank="(\d+)"', result["promotion"])] == list(range(1, 11))
    assert [int(x) for x in re.findall(r'data-profit-rank="(\d+)"', result["profit"])] == list(range(1, 11))
    assert sorted(re.findall(r'data-code="([^"]+)"', result["promotion"])) == sorted(re.findall(r'data-code="([^"]+)"', result["profit"]))


def test_compact_home_has_one_main_table_and_no_duplicate_profit_table():
    source = (ROOT / "decision.html").read_text()
    assert 'compact-two-ranks-v16-promotion-success' in source
    assert '<table class="executable-profit-table">' not in source
    assert 'font-size: 16px' in source
    for name in ('profitDetails', 'historicalResearchDetails', 'technicalDetails'):
        tag = re.search(rf'<details[^>]*id="{name}"[^>]*>', source).group()
        assert ' open' not in tag
        if name == 'profitDetails':
            assert ' hidden' in tag
    assert '[hidden] { display: none !important; }' in source
    assert source.index('id="executableProfitShadowPanel"') > source.index('id="shadowWorkspace"')


def test_baseline_is_default_collapsed_and_outside_main_rankings():
    source = (ROOT / "decision.html").read_text()
    main = source.split('<section id="rankingWorkspace"', 1)[1].split('<section id="shadowWorkspace"', 1)[0]
    assert "单一盈利" not in main
    assert '<h2>盈利排序（研究）</h2>' in main
    baseline = re.search(r'<details id="legacyProfitBenchmarkPanel"([^>]*)>', source)
    assert baseline and "open" not in baseline.group(1)
    assert source.index('id="legacyProfitBenchmarkPanel"') > source.index('id="reviewWorkspace"')


@pytest.mark.parametrize("status,kind,label,tone", [
    ("PENDING_T_NOT_REACHED", "t", "未到T验证时间（截至快照）", "pending"),
    ("PENDING_T1_NOT_REACHED", "t1", "未到T+1结算时间（截至快照）", "pending"),
    ("PENDING_T_TRUTH", "t", "T真值待更新", "missing"),
    ("PENDING_T1_TRUTH", "t1", "退出真值待更新", "missing"),
    ("PENDING_T_VERIFICATION", "t1", "等待T验证", "pending"),
    ("T_VERIFIED_PROXY_FILLED", "t", "代理可买 · 已验证", "final"),
    ("T_VERIFIED_PROXY_NO_FILL_AUCTION_DAILY_CONFLICT", "t", "未买入 · 竞价与日线冲突", "no-fill"),
    ("T_VERIFIED_PROXY_NO_FILL_ABOVE_FROZEN_CAP", "t", "未买入 · 超过冻结限价", "no-fill"),
    ("T_VERIFIED_PROXY_NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED", "t", "未买入 · 涨停开盘未确认成交", "no-fill"),
    ("T_VERIFIED_PROXY_NO_FILL_ONE_PRICE_LIMIT_UP", "t", "未买入 · 一字涨停", "no-fill"),
    ("T_VERIFIED_PROXY_NO_FILL_CAPACITY", "t", "未买入 · 竞价容量不足", "no-fill"),
    ("FINAL_PROXY_NO_FILL", "t1", "未买入 · 槽位0收益", "no-fill"),
    ("FINAL_FIRST_TRADABLE_OPEN_PUBLIC_MARKET_PROXY", "t1", "已结算（开盘代理）", "final"),
    ("FINAL_UNKNOWN", "t1", "状态待核验", "missing"),
    ("T_VERIFIED_PROXY_NO_FILL_UNKNOWN", "t", "状态待核验", "missing"),
    ("", "t", "状态待核验", "missing"),
])
def test_shadow_status_labels_do_not_call_missing_truth_settled(status, kind, label, tone):
    script = _function("primaryShadowStatus") + f"\nconsole.log(JSON.stringify(primaryShadowStatus({json.dumps(status)}, {json.dumps(kind)})));"
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"label": label, "tone": tone}


def test_p1_empty_day_dom_acceptance_does_not_require_padded_tables(tmp_path, monkeypatch):
    workflow = (ROOT / ".github/workflows/run_primary_profit_rankings.yml").read_text()
    snippet = workflow.split('DOM_PATH="${dom}" PUBLIC_ROOT="${RUNNER_TEMP}/public-p1" python - <<\'PY\'\n', 1)[1].split("\n          PY", 1)[0]
    code = compile(textwrap.dedent(snippet), "<p1-empty-dom>", "exec")
    output = tmp_path / "outputs/decision/executable_profit_research"
    output.mkdir(parents=True)
    (output / "projection_20260904.json").write_text('{"candidate_count":0}')
    html = '<h2 id="statusTitle">晋级榜与盈利排序已生成</h2><section id="stagePanel"><span id="stageSignalDate">D：2026-09-04</span><div id="stageContent">D日没有符合硬范围的候选</div></section>'
    path = tmp_path / "empty.html"
    path.write_text(html)
    monkeypatch.setenv("PUBLIC_ROOT", str(tmp_path))
    monkeypatch.setenv("SIGNAL_DATE", "20260904")
    monkeypatch.setenv("DOM_PATH", str(path))
    exec(code, {})
    path.write_text(html.replace("D日没有符合硬范围的候选", '<table><tbody data-three-rank-body><tr><td>补票</td></tr></tbody></table>'))
    with pytest.raises(SystemExit, match="public rendered P1 DOM failed"):
        exec(code, {})
