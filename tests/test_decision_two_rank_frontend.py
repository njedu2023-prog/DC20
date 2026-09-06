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
    mixed = json.loads((ROOT / "outputs/decision/executable_profit_research/projection_20260904.json").read_text())
    single = json.loads((ROOT / "outputs/decision/legacy_profit_relative_research/projection_20260904.json").read_text())
    mixed["rows"] = mixed["rows"][:count]
    # Renderer fixtures; production loader binding validation remains tested separately.
    codes = {row["ts_code"] for row in mixed["rows"]}
    single["rows"] = [row for row in single["rows"] if row["ts_code"] in codes]
    contract = {key: mixed[key] for key in ("signal_date", "exec_date", "exit_date", "top10_members_sha256")}
    contract.update(bundle_sha256=mixed["source_bundle_sha256"], rows=mixed["rows"],
                    models={"promotion": {"status": "READY"}}, generated_at_utc="2026-09-04T14:00:00Z")
    names = ("renderLegacyProfitBenchmark", "renderStatus", "renderThreeRankWatchlist",
             "renderPrimaryMixedProfitResearch", "refreshCurrentLegacyProfitRelativeResearch",
             "canonicalYmd", "finiteNumber", "escapeHtml", "integerText", "number", "pct",
             "signedPct", "valueTone", "dateText", "pathClass", "truthClass",
             "continuationLabel", "beijingDateTimeText")
    script = """
const state = {index:0};
const bodyNode = {innerHTML:''};
const els = new Proxy({}, {get(target, key) {
  return target[key] ??= {hidden:false, innerHTML:'', textContent:'', className:'',
    querySelector:()=>bodyNode, querySelectorAll:()=>[], setAttribute:()=>{}};
}});
const document = {querySelectorAll:()=>[]};
const validatedThreeRankContract = value=>value;
const localUrl = value=>value;
const EXECUTABLE_PROFIT_RESEARCH_ROOT = 'outputs/decision/executable_profit_research';
const LEGACY_PROFIT_RELATIVE_RESEARCH = {root:'outputs/decision/legacy_profit_relative_research'};
let shadowProjection = null;
function renderPrimaryProfitShadowSidecar(loaded) { shadowProjection = loaded.projection; }
"""
    script += "\n".join(_function(name) for name in names)
    script += f"\nconst mixed={json.dumps(mixed)}, single={json.dumps(single)}, contract={json.dumps(contract)};\n"
    script += "state.currentThreeRank=contract; state.currentLegacyProfitRelativeResearch=single; state.currentExecutableProfitResearch={status:'ready',kind:'primary_core',projection:mixed,index:{}};\n"
    script += "(async()=>{" + body + "})().catch(error=>{console.error(error);process.exit(1)});"
    result = subprocess.run([NODE, "-e", script], check=True, text=True, capture_output=True)
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


@pytest.mark.parametrize("count", [0, 1, 2, 6])
def test_profit_top2_no_padding_same_frozen_rows_and_explicit_research_engine(count):
    result = _run("const before=JSON.stringify(mixed);renderPrimaryMixedProfitResearch(state.currentExecutableProfitResearch);"
                  "console.log(JSON.stringify({html:els.executableProfitResearchContent.innerHTML,unchanged:before===JSON.stringify(mixed),"
                  "sameShadow:shadowProjection===mixed,codes:mixed.rows.map(r=>r.ts_code)}));", count=count)
    assert result["unchanged"] and result["sameShadow"]
    assert '<article' not in result["html"]
    assert '<details' not in result["html"]
    assert result["html"].count('class="profit-top-badge"') == min(count, 2)
    assert "当前来源：混合盈利研究引擎" in result["html"]
    assert "不是盈利概率或预期收益" in result["html"]
    if count:
        assert "完整盈利排序" in result["html"]
        assert "联合代理分（非胜率）" in result["html"]
        assert result["html"].count('<table ') == 1
        assert result["html"].count('<td class="left name">') == count
        for code in result["codes"]:
            assert result["html"].count(code) == 1
    else:
        assert "真实N=0" in result["html"]
    if count >= 2:
        assert result["html"].index(result["codes"][0]) < result["html"].index(result["codes"][1])


def test_top2_badges_follow_profit_rank_after_company_name_not_promotion_or_input_order():
    result = _run("mixed.rows.reverse();const before=JSON.stringify(mixed);"
                  "renderPrimaryMixedProfitResearch(state.currentExecutableProfitResearch);"
                  "console.log(JSON.stringify({html:els.executableProfitResearchContent.innerHTML,"
                  "unchanged:before===JSON.stringify(mixed),rows:mixed.rows}));", count=6)
    assert result["unchanged"]
    body = result["html"].split('<tbody>', 1)[1].split('</tbody>', 1)[0]
    cells = re.findall(r'<td class="left name">(.*?)</td>', body)
    ranked = sorted(result["rows"], key=lambda row: row["executable_profit_research_rank"])
    assert len(cells) == len(ranked) == 6
    for row, cell in zip(ranked, cells):
        rank = row["executable_profit_research_rank"]
        assert cell.startswith(row["name"])
        if rank in (1, 2):
            assert f'aria-label="盈利排序第{rank}名">Top{rank}</span>' in cell
        else:
            assert 'profit-top-badge' not in cell


def test_inline_profit_badge_preserves_name_escaping_and_tied_score_label():
    html = _run("mixed.rows[0].name='<img src=x onerror=alert(1)>';mixed.rows[0].rank_tied=true;"
                "renderPrimaryMixedProfitResearch(state.currentExecutableProfitResearch);"
                "console.log(JSON.stringify(els.executableProfitResearchContent.innerHTML));", count=2)
    assert '<img' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;<span class="profit-top-badge"' in html
    assert '1（并列分）' in html


def test_optional_baseline_refresh_clears_old_projection_on_error():
    result = _run("globalThis.loadCurrentLegacyProfitRelativeResearch=async()=>{throw new Error('HTTP 404')};"
                  "await refreshCurrentLegacyProfitRelativeResearch(contract);console.log(JSON.stringify({projection:state.currentLegacyProfitRelativeResearch,load:state.legacyProfitBenchmarkLoad}));")
    assert result["projection"] is None
    assert result["load"]["status"] == "missing"


def test_baseline_is_default_collapsed_and_outside_main_rankings():
    source = (ROOT / "decision.html").read_text()
    main = source.split('<section id="rankingWorkspace"', 1)[1].split('<section id="shadowWorkspace"', 1)[0]
    assert "单一盈利" not in main
    assert '<h2>盈利排序（研究）</h2>' in main
    baseline = re.search(r'<details id="legacyProfitBenchmarkPanel"([^>]*)>', source)
    assert baseline and "open" not in baseline.group(1)
    assert source.index('id="legacyProfitBenchmarkPanel"') > source.index('id="reviewWorkspace"')


def test_p1_empty_day_dom_acceptance_does_not_require_padded_tables(tmp_path, monkeypatch):
    workflow = (ROOT / ".github/workflows/run_primary_profit_rankings.yml").read_text()
    snippet = workflow.split('DOM_PATH="${dom}" PUBLIC_ROOT="${RUNNER_TEMP}/public-p1" python - <<\'PY\'\n', 1)[1].split("\n          PY", 1)[0]
    code = compile(textwrap.dedent(snippet), "<p1-empty-dom>", "exec")
    output = tmp_path / "outputs/decision/executable_profit_research"
    output.mkdir(parents=True)
    (output / "projection_20260904.json").write_text('{"candidate_count":0}')
    html = '<h2 id="statusTitle">晋级榜与盈利排序已生成</h2><section id="stagePanel"><div id="stageContent">D日没有符合硬范围的候选</div></section><section id="executableProfitResearchPanel"><span id="executableProfitResearchState">真实候选 0 支</span><div id="executableProfitResearchContent">展示真实N=0，不从池外补票。</div></section>'
    path = tmp_path / "empty.html"
    path.write_text(html)
    monkeypatch.setenv("PUBLIC_ROOT", str(tmp_path))
    monkeypatch.setenv("SIGNAL_DATE", "20260904")
    monkeypatch.setenv("DOM_PATH", str(path))
    exec(code, {})
    path.write_text(html.replace("D日没有符合硬范围的候选", '<table><tbody data-three-rank-body><tr><td>补票</td></tr></tbody></table>'))
    with pytest.raises(SystemExit, match="public rendered P1 DOM failed"):
        exec(code, {})
