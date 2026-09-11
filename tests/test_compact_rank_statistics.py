"""Read-only presentation projections must never change frozen ranking data."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node required")


def function(name):
    match = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", (ROOT / "decision.html").read_text(), re.M | re.S)
    assert match, name
    return match.group()


def fixture():
    summary = json.loads((ROOT / "outputs/decision/primary_observation/summary.json").read_text())
    with (ROOT / summary["rows_path"]).open() as stream:
        rows = list(csv.DictReader(stream))
    contracts = [json.loads((ROOT / f"outputs/decision/three_rank_top10_{d['signal_date']}.json").read_text()) for d in summary["daily_summaries"]]
    return dict(summary=summary, rows=rows, contracts=contracts)


def run(body, data=None, extra=""):
    names = ["promotionSlotStatistics", "refreshPromotionSlotStatistics", "compactShadowSource", "renderCompactDashboard", "renderCompactProfitStatistics", "validatePrimaryProfitShadowCohorts", "executableProfitExpect", "validNullableFinite",
             "canonicalYmd", "finiteNumber", "escapeHtml", "dateText", "signedPct", "pct", "integerText", "primaryShadowStatus",
             "sha256Hex", "isSha256", "parseStrictCsvBytes"]
    prelude = """
const fs=require('fs'),vm=require('vm');
const PUBLIC_STATISTICS_START_SIGNAL_DATE='20260828';
const nodes=new Map();
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,{hidden:false,open:false,innerHTML:'',textContent:''});return nodes.get(id)}};
const els=Object.fromEntries(['compactLedgerContent','compactLedgerState','compactStatisticsContent'].map(id=>[id,document.getElementById(id)]));
const state={index:0,currentThreeRank:{signal_date:'20260908'}};
const location={search:''};
const validatedThreeRankContract=value=>value.three_rank || value;
const crypto=require('crypto').webcrypto;
"""
    script = prelude + "\n".join(function(n) for n in names)
    script += "\nconst input=" + json.dumps(data or fixture(), ensure_ascii=False) + ";\n"
    script += extra + "\n(async()=>{" + body + "})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_real_frozen_rows_produce_three_independent_ranks():
    result = run("console.log(JSON.stringify(promotionSlotStatistics(input.summary,input.rows,input.contracts)));")
    assert [r["rank"] for r in result] == [1, 2, 3]
    assert [r["count"] for r in result] == [4, 4, 4]
    assert [r["verified"] for r in result] == [3, 3, 3]
    assert [r["hitRate"] for r in result] == pytest.approx([1/3, 1/3, 2/3])
    assert result[0]["cumulative"] is None and result[0]["drawdown"] is None
    assert result[0]["noFill"] == 1 and result[0]["filled"] == 0
    assert result[1]["meanNet"] == pytest.approx(-0.046391849713445, abs=1e-6)
    assert result[1]["cumulative"] == pytest.approx(-0.098041, abs=1e-6)
    assert result[2]["meanNet"] == pytest.approx(-0.06515274, abs=1e-6)


@pytest.mark.parametrize("mutation", [
    "input.rows[0].promotion_rank='2'", "input.rows[0].name='wrong'",
    "input.rows[0].exec_date='20260901'", "input.rows[0].signal_date='20260831'",
    "input.rows[0].actual_net_return='0'", "input.rows[0].slot_net_return=''",
    "input.rows[0].actual_order_fill_observed='True'", "input.rows[0].truth_source='auction'",
    "input.rows[0].proxy_fill='1'", "input.rows[0].continuation_limit_up_hit=''",
    "input.rows[1].actual_exit_date='20990101'", "input.rows.push(input.rows[0])",
    "input.summary.daily_summaries[0].t_validated_rows=9",
])
def test_identity_scope_missing_truth_and_counts_fail_closed(mutation):
    result = run(mutation + ";try{promotionSlotStatistics(input.summary,input.rows,input.contracts);console.log(false)}catch(e){console.log(true)}")
    assert result is True


def small_fixture(n=3):
    d, t, t1 = "20260828", "20260831", "20260901"
    rows = [dict(signal_date=d,exec_date=t,exit_date=t1,ts_code=f"60000{i}.SH",name=f"name{i}",promotion_rank=str(i),
                 proxy_fill="1",continuation_limit_up_hit=str(i%2),actual_net_return="0.0555",slot_net_return="0.0555",
                 actual_exit_date=t1,blocked_exit_sessions="0",validation_status="FINAL_VERIFIED_PROXY",
                 truth_source="daily_open_proxy",actual_order_fill_observed="False") for i in range(1,n+1)]
    day = dict(signal_date=d,exec_date=t,exit_date=t1,rows=n,t_validated_rows=n,final_verified_trades=n,settled_rows=n,
               pending_t_rows=0,pending_t1_rows=0,missing_t_truth_rows=0,missing_t1_truth_rows=0,unresolved_exit_rows=0)
    return dict(rows=rows,summary=dict(as_of_date=t1,statistics=dict(observation_rows=n),daily_summaries=[day]),
                contracts=[dict(rows=[dict(ts_code=r["ts_code"],name=r["name"],promotion_rank=int(r["promotion_rank"])) for r in rows])])


@pytest.mark.parametrize("n", [0,1,2,3])
def test_short_candidate_lists_never_fill_missing_rank_slots(n):
    result = run("console.log(JSON.stringify(promotionSlotStatistics(input.summary,input.rows,input.contracts)))", small_fixture(n))
    assert [r["count"] for r in result] == [int(rank <= n) for rank in (1,2,3)]
    for r in result:
        assert r["meanNet"] == (0.0555 if r["rank"] <= n else None)  # already charged 45bp, no second deduction


def test_missing_and_delayed_rank_do_not_poison_other_rank_or_report_partial_nav():
    data = small_fixture()
    data["rows"][0].update(validation_status="MISSING_T1_TRUTH",slot_net_return="",actual_net_return="",actual_exit_date="")
    data["summary"]["daily_summaries"][0].update(final_verified_trades=2,settled_rows=2,missing_t1_truth_rows=1)
    data["rows"][1].update(actual_exit_date="20260902",blocked_exit_sessions="1")
    data["summary"]["as_of_date"] = "20260902"
    result = run("console.log(JSON.stringify(promotionSlotStatistics(input.summary,input.rows,input.contracts)))", data)
    assert result[0]["cumulative"] is None and result[1]["cumulative"] is None
    assert result[2]["cumulative"] == pytest.approx(0.0555)


def test_async_loader_binds_csv_and_each_frozen_day_sha_without_writes():
    result = run("state.currentPublicObservationStatistics=input.summary;await refreshPromotionSlotStatistics();console.log(JSON.stringify({rows:state.promotionSlotStatistics,error:state.promotionSlotError}))",
                 extra="const fetchPublishedBytes=async path=>new Uint8Array(fs.readFileSync("+json.dumps(str(ROOT))+"+'/'+path));")
    assert result["error"] == "" and len(result["rows"]) == 3


@pytest.mark.parametrize("mutation", ["input.summary.rows_sha256='0'.repeat(64)",
                                     "input.summary.policy.round_trip_cost_rate=0",
                                     "input.summary.bindings[0].status='EXCLUDED_RETROSPECTIVE'",
                                     "input.summary.bindings[0].p0.latest_three_rank_json_sha256='0'.repeat(64)"])
def test_bad_binding_only_closes_auxiliary_statistics(mutation):
    result = run(mutation+";state.currentPublicObservationStatistics=input.summary;await refreshPromotionSlotStatistics();console.log(JSON.stringify({rows:state.promotionSlotStatistics,error:state.promotionSlotError,primary:state.currentThreeRank}))",
                 extra="const fetchPublishedBytes=async path=>new Uint8Array(fs.readFileSync("+json.dumps(str(ROOT))+"+'/'+path));")
    assert result["rows"] is None and result["error"] and result["primary"]["signal_date"] == "20260908"


def test_compact_view_has_only_three_promotion_success_results():
    result = run("state.currentPublicObservationStatistics=input.summary;state.promotionSlotStatistics=promotionSlotStatistics(input.summary,input.rows,input.contracts);renderCompactDashboard();console.log(JSON.stringify({html:els.compactStatisticsContent.innerHTML,hidden:Object.fromEntries([...nodes].map(([k,v])=>[k,v.hidden]))}))")
    assert [result["html"].count(f"<dt>Top{rank}</dt>") for rank in (1, 2, 3)] == [1, 1, 1]
    assert result["html"].count('class="success-rate">33.33%') == 2
    assert result["html"].count('class="success-rate">66.67%') == 1
    assert result["html"].count("1 / 3 成功 / 已验证") == 2
    assert "2 / 3 成功 / 已验证" in result["html"]
    for removed in ("<table", "盈利第", "T+1结算", "代理可买率", "成交胜率", "净收益", "合成累计", "最大回撤"):
        assert removed not in result["html"]
    assert "尚未覆盖当前 D 2026-09-08" in result["html"]
    assert "数据截至 2026-09-04" in result["html"]
    assert "晋级不代表盈利" in result["html"]
    assert '<h2 id="compactStatisticsTitle">晋级成功率</h2>' in (ROOT / "decision.html").read_text()
    assert result["hidden"]["shadowWorkspace"] and result["hidden"]["historicalResearchDetails"]
    archive = run("location.search='?view=research';renderCompactDashboard();console.log(JSON.stringify(Object.fromEntries([...nodes].map(([k,v])=>[k,v.hidden]))))")
    assert archive["compactStatistics"] and not archive["historicalResearchDetails"]


def test_unavailable_statistics_are_not_fake_zero_and_do_not_borrow_shadow():
    result = run("state.promotionSlotError='SHA校验失败';renderCompactDashboard();console.log(JSON.stringify(els.compactStatisticsContent.innerHTML))")
    assert result.count("SHA校验失败") == 3
    assert 'class="success-rate">0.00%' not in result
    assert "盈利第" not in result
    assert "<td>0</td>" not in result


def test_success_results_distinguish_no_rank_pending_and_verified_zero():
    result = run("state.promotionSlotStatistics=[{rank:1,count:0,verified:0,hitRate:null},{rank:2,count:2,verified:0,hitRate:null},{rank:3,count:2,verified:2,hitRate:0}];renderCompactDashboard();console.log(JSON.stringify(els.compactStatisticsContent.innerHTML))")
    assert "暂无该名次样本" in result and "暂无已验证样本" in result
    assert result.count('class="success-rate">0.00%') == 1
    assert "0 / 2 成功 / 已验证" in result
    assert "数据截至" not in result


@pytest.mark.parametrize("n", [0, 1, 2])
def test_absent_promotion_ranks_do_not_gain_synthetic_success_samples(n):
    result = run("state.currentPublicObservationStatistics=input.summary;state.promotionSlotStatistics=promotionSlotStatistics(input.summary,input.rows,input.contracts);renderCompactDashboard();console.log(JSON.stringify(els.compactStatisticsContent.innerHTML))",small_fixture(n))
    assert result.count("暂无该名次样本") == 3 - n
    assert result.count("成功 / 已验证") == n


def test_archive_history_does_not_reexpose_latest_shadow_and_fatal_error_cannot_refill():
    result = run("location.search='?view=research';state.index=1;renderCompactDashboard();console.log(JSON.stringify(Object.fromEntries([...nodes].map(([k,v])=>[k,v.hidden]))))")
    assert result["shadowWorkspace"] and result["historicalResearchDetails"]
    assert not result["technicalDetails"]
    fatal = run("state.compactFatalError=true;els.compactLedgerContent.innerHTML='CLEARED';renderCompactDashboard();console.log(JSON.stringify(els.compactLedgerContent.innerHTML))")
    assert fatal == "CLEARED"


def test_future_asof_is_rejected_and_old_async_generation_cannot_return_statistics():
    data = fixture()
    data["summary"]["as_of_date"] = "20990101"
    assert run("try{promotionSlotStatistics(input.summary,input.rows,input.contracts);console.log(false)}catch(e){console.log(true)}",data)
    result = run("state.currentPublicObservationStatistics=input.summary;state.compactLoadSequence=1;const pending=refreshPromotionSlotStatistics();state.compactLoadSequence=2;await pending;console.log(JSON.stringify(state.promotionSlotStatistics));",
                 extra="const fetchPublishedBytes=async path=>new Uint8Array(fs.readFileSync("+json.dumps(str(ROOT))+"+'/'+path));")
    assert result is None


def test_real_daily_ledger_unifies_identity_and_only_joins_exact_current_shadow():
    data = fixture()
    base = ROOT / "outputs/decision/executable_profit_research"
    data["daily"] = json.loads((base / "daily_mixed_top2_index.json").read_text())
    index = json.loads((base / "index.json").read_text())
    shadow_index = json.loads((base / "shadow_index.json").read_text())
    shadow = json.loads((ROOT / shadow_index["latest_state_url"]).read_text())
    data["profit"] = dict(status="ready",kind="primary_core",index=index,shadow=dict(publicWindowReady=True,state=shadow))
    result = run("state.currentPrimaryMixedDailyTop2={status:'ready',index:input.daily};state.currentExecutableProfitResearch=input.profit;renderCompactDashboard();console.log(JSON.stringify(els.compactLedgerContent.innerHTML))", data)
    assert "不计前向收益" in result
    assert "未绑定该日验证" in result
    assert "自然冻结" in result
    assert "成交净收益" in result and "槽位收益" in result
    data["profit"]["shadow"]["state"]["signal_date"] = "20990101"
    mismatch = run("state.currentPrimaryMixedDailyTop2={status:'ready',index:input.daily};state.currentExecutableProfitResearch=input.profit;renderCompactDashboard();console.log(JSON.stringify(els.compactLedgerContent.innerHTML))", data)
    assert "未绑定该日验证" in mismatch
    assert "<td>自然冻结</td>" not in mismatch


def profit_fixture(daily_archive=None):
    """Keep denominator mutations on the audited 09/08 six-day cohort.

    Production ``latest`` pointers advance every trading day.  These rendering
    tests intentionally exercise 6 natural slots versus 16 archive slots, not
    whichever cohort happens to be current when CI runs.
    """
    data = {}
    base = ROOT / "outputs/decision/executable_profit_research"
    archive = daily_archive if daily_archive is not None else json.loads((base / "daily_mixed_top2_index.json").read_text())
    entries = copy.deepcopy([row for row in archive["entries"] if row["signal_date"] <= "20260908"])
    assert [row["signal_date"] for row in entries] == [
        "20260828", "20260831", "20260901", "20260902",
        "20260903", "20260904", "20260907", "20260908",
    ]
    data["daily"] = dict(
        public_start_signal_date="20260828", entries=entries,
        recorded_days=len(entries), recorded_slots=sum(len(row["rows"]) for row in entries),
    )
    shadow_bytes = (base / "shadow_state_20260908_asof_20260908.json").read_bytes()
    assert hashlib.sha256(shadow_bytes).hexdigest() == "5a18bbfa8ecb10b249ca7f94e726a143c56ea9f5fb45dddc877d1d3dd9d83feb"
    shadow = json.loads(shadow_bytes)
    projection_sha = hashlib.sha256((base / "projection_20260908.json").read_bytes()).hexdigest()
    assert projection_sha == entries[-1]["projection_json_sha256"] == shadow["source_bindings"]["mixed_projection"]["sha256"]
    index = dict(latest_signal_date="20260908", latest_projection_json_sha256=projection_sha)
    data["profit"] = dict(status="ready", kind="primary_core", index=index, shadow=dict(publicWindowReady=True, selectionOnlyCutover=False, state=shadow))
    return data


def profit_html(data, before=""):
    return run(before + ";state.currentPrimaryMixedDailyTop2={status:'ready',index:input.daily};state.currentExecutableProfitResearch=input.profit;renderCompactDashboard();console.log(JSON.stringify(els.compactLedgerContent.innerHTML))", data)


def test_profit_fixture_and_denominators_ignore_later_trading_days():
    base = ROOT / "outputs/decision/executable_profit_research"
    archive = json.loads((base / "daily_mixed_top2_index.json").read_text())
    # Simulate another successful future publication without editing artifacts.
    later = copy.deepcopy(archive["entries"][-1])
    later.update(signal_date="20990105", exec_date="20990106", exit_date="20990107")
    archive["entries"].append(later)
    archive.update(latest_signal_date="20990105", recorded_days=999, recorded_slots=1998)
    fixed = profit_fixture(archive)
    assert fixed == profit_fixture()
    assert fixed["daily"]["recorded_days"] == 8 and fixed["daily"]["recorded_slots"] == 16
    for slot in (1, 2):
        cohort = fixed["profit"]["shadow"]["state"]["cohorts"][f"shadow_slot_{slot}"]
        assert cohort["selected_slots"] == 6
        cohort.update(
            t_validated_slots=4, proxy_fill_slots=2, proxy_no_fill_slots=2, terminal_slots=4,
            t1_settled_slots=2, wins_after_cost=1, win_rate=0.5, mean_net_return_after_cost=0.02,
            pending_validation_slots=2, pending_settlement_slots=0, pending_slots=2,
            effective_dates=4, equal_weight_cumulative_return=0.039, maximum_drawdown=-0.02,
        )
    summary = profit_html(fixed).split('<details')[0]
    assert summary.count('<td>1 / 2</td><td>50.00%</td>') == 2
    assert "校验失败" not in summary
    fixed["profit"]["shadow"]["state"]["cohorts"]["shadow_slot_1"]["win_rate"] = 1 / 6
    assert "盈利累计统计校验失败" in profit_html(fixed)


def test_profit_summary_uses_natural_cohorts_not_sixteen_daily_archive_seats():
    data = profit_fixture()
    original = copy.deepcopy(data)
    result = profit_html(data)
    summary, details = result.split('<details class="compact-disclosure" id="compactProfitDailyDetails">')
    assert summary.count('class="rank-mark rank-profit"') == 2
    assert summary.count('<td>6</td><td>6 / 0</td><td>0 / 0</td><td>待验证</td>') == 2
    assert "0.00%" not in summary
    assert "验证快照截至 2026-09-08" in summary
    assert "8日 / 16席" in details
    assert details.count('data-field="code"') == 16
    assert details.count('data-field="stock"') == 16
    assert details.count('data-field="profit-rank"') == 16
    assert "股票 / 盈利名次" not in details
    assert '<th scope="col" class="left">代码</th><th scope="col" class="left">股票</th><th scope="col">盈利名次</th>' in details
    assert data == original


def test_profit_wins_use_filled_settled_denominator_not_no_fill_or_pending():
    data = profit_fixture()
    for slot, wins, mean in ((1, 1, 0.02), (2, 0, -0.01)):
        data["profit"]["shadow"]["state"]["cohorts"][f"shadow_slot_{slot}"].update(
            t_validated_slots=4, proxy_fill_slots=2, proxy_no_fill_slots=2, terminal_slots=4,
            t1_settled_slots=2, wins_after_cost=wins, win_rate=wins / 2, mean_net_return_after_cost=mean,
            pending_validation_slots=2, pending_settlement_slots=0, pending_slots=2,
            effective_dates=4, equal_weight_cumulative_return=0.039, maximum_drawdown=-0.02)
    summary = profit_html(data).split('<details')[0]
    assert '<td>1 / 2</td><td>50.00%</td>' in summary
    assert '<td>0 / 2</td><td>0.00%</td>' in summary
    assert '+2.00%' in summary and '-1.00%' in summary  # already charged; never charge again
    assert "4 个完整日" in summary
    assert "校验失败" not in summary


@pytest.mark.parametrize("field", ["pending_exit_slots", "delayed_exit_slots", "blocked_exit_sessions"])
def test_unresolved_or_delayed_exits_do_not_show_synthetic_nav_as_account_return(field):
    data = profit_fixture()
    data["profit"]["shadow"]["state"]["cohorts"]["shadow_slot_1"][field] = 1
    summary = profit_html(data).split('<details')[0]
    assert summary.count("暂不累计") == 2


@pytest.mark.parametrize("mutation", ["c.wins_after_cost=1", "c.win_rate=0", "c.terminal_slots=1", "c.t_validated_slots=7"])
def test_corrupt_profit_cohort_fails_closed_without_clearing_daily_list(mutation):
    result = profit_html(profit_fixture(), "const c=input.profit.shadow.state.cohorts.shadow_slot_1;" + mutation)
    assert "盈利累计统计校验失败" in result
    assert 'id="compactProfitDailyDetails"' in result
    assert result.count('data-field="code"') == 16


@pytest.mark.parametrize("mutation", ["input.profit.shadow.publicWindowReady=false", "input.profit.shadow.selectionOnlyCutover=true"])
def test_missing_or_selection_only_sidecar_never_fabricates_cumulative_zero(mutation):
    result = profit_html(profit_fixture(), mutation)
    assert "盈利累计统计尚未通过独立账本校验" in result
    assert 'class="three-rank-table profit-summary-table"' not in result
    assert result.count('data-field="code"') == 16


def test_zero_candidate_day_has_correct_eleven_column_span():
    data = profit_fixture()
    data["daily"]["entries"][0]["rows"] = []
    result = profit_html(data)
    assert 'colspan="8"' in result and "已记录0席，不补票" in result


def test_all_verified_no_fill_is_not_pending_or_fake_win_rate():
    data = profit_fixture()
    for slot in (1, 2):
        data["profit"]["shadow"]["state"]["cohorts"][f"shadow_slot_{slot}"].update(
            t_validated_slots=6, proxy_no_fill_slots=6, terminal_slots=6, pending_validation_slots=0,
            pending_slots=0, effective_dates=6, equal_weight_cumulative_return=0, maximum_drawdown=0)
    summary = profit_html(data).split('<details')[0]
    assert summary.count("暂无成交结算") == 4
    assert "待验证" not in summary
    assert summary.count('0.00%') == 4  # known no-fill slots produce zero cumulative/dd, not a zero win rate


def test_complete_script_parses_and_main_has_no_duplicate_statistic_cards():
    source = (ROOT / "decision.html").read_text()
    script = re.search(r"<script>(.*?)</script>",source,re.S).group(1)
    # Linux limits each command-line argument; send full HTML scripts over stdin.
    result = subprocess.run([NODE,"-e","new (require('vm').Script)(require('fs').readFileSync(0,'utf8'));"],input=script,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert 'id="historicalResearchDetails" hidden' in source
    assert 'id="technicalDetails" hidden' in source
    assert 'href="?view=research"' in source


def test_full_production_script_validates_real_frozen_contracts_not_a_stub():
    source = (ROOT / "decision.html").read_text()
    script = re.search(r"<script>(.*?)</script>",source,re.S).group(1).replace("initialize(false).catch(showError);", "")
    prelude = """
const fs=require('fs');
const crypto=require('crypto').webcrypto;
const element=()=>({innerHTML:'',textContent:'',hidden:false,open:false,setAttribute(){},addEventListener(){},querySelectorAll(){return []},classList:{toggle(){},add(){},remove(){}}});
const document={getElementById:()=>element(),querySelectorAll:()=>[],addEventListener(){}};
const location={search:'',protocol:'https:',href:'https://njedu2023-prog.github.io/DC20/'};
const window={location,addEventListener(){}};
"""
    tail = "\nfetchPublishedBytes=async path=>new Uint8Array(fs.readFileSync("+json.dumps(str(ROOT))+"+'/'+path));\n"
    tail += "state.currentPublicObservationStatistics="+json.dumps(fixture()["summary"])+";\n"
    tail += "refreshPromotionSlotStatistics().then(()=>console.log(JSON.stringify({result:state.promotionSlotStatistics,error:state.promotionSlotError}))).catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([NODE,"-"],input=prelude+script+tail,text=True,capture_output=True)
    assert result.returncode == 0,result.stderr
    payload=json.loads(result.stdout)
    assert payload["error"] == "" and len(payload["result"]) == 3


@pytest.mark.parametrize("drift", [False, True])
def test_full_shadow_loader_binds_summary_cohorts_before_display(drift):
    source = (ROOT / "decision.html").read_text()
    script = re.search(r"<script>(.*?)</script>", source, re.S).group(1).replace("initialize(false).catch(showError);", "")
    prelude = """
const fs=require('fs'),crypto=require('crypto').webcrypto;
const element=()=>({innerHTML:'',textContent:'',hidden:false,open:false,setAttribute(){},addEventListener(){},querySelectorAll(){return []},classList:{toggle(){},add(){},remove(){}}});
const document={getElementById:()=>element(),querySelectorAll:()=>[],addEventListener(){}};
const location={search:'',protocol:'https:',href:'https://njedu2023-prog.github.io/DC20/'};
const window={location,addEventListener(){}};
"""
    tail = "\nconst root=" + json.dumps(str(ROOT)) + ";\n"
    tail += "fetchPagesOnlyPath=async (path,type)=>{const bytes=new Uint8Array(fs.readFileSync(root+'/'+path));return type==='bytes'?bytes:JSON.parse(new TextDecoder().decode(bytes))};\n"
    if drift:
        # Corruption introduced after the real SHA reader: exercise equality guard rather than a hash stub.
        tail += "const originalReader=fetchPagesOnlyShaBoundJson;fetchPagesOnlyShaBoundJson=async (...args)=>{const r=await originalReader(...args);if(args[0].endsWith('/statistics/summary.json'))r.payload.cohorts.shadow_slot_1.selected_slots+=1;return r};\n"
    tail += "(async()=>{try{const index=await fetchPagesOnlyPath(EXECUTABLE_PROFIT_RESEARCH_ROOT+'/index.json');const projection=await fetchPagesOnlyPath(index.latest_projection_json_url);const result=await loadPrimaryProfitShadowSidecar(projection,index);console.log(JSON.stringify({ready:result.publicWindowReady,selected:result.state.cohorts.shadow_slot_1.selected_slots}));}catch(error){console.log(JSON.stringify({error:error.message}));}})();"
    result = subprocess.run([NODE, "-"], input=prelude + script + tail, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    if drift:
        assert "summary分组不一致" in payload["error"]
    else:
        # This integration test deliberately follows real latest pointers, so
        # compare against the real SHA-bound summary rather than a dated count.
        base = ROOT / "outputs/decision/executable_profit_research"
        shadow_index = json.loads((base / "shadow_index.json").read_text())
        state_bytes = (ROOT / shadow_index["latest_state_url"]).read_bytes()
        assert hashlib.sha256(state_bytes).hexdigest() == shadow_index["latest_state_sha256"]
        shadow = json.loads(state_bytes)
        binding = shadow["source_bindings"]["statistics"]
        summary_bytes = (ROOT / binding["path"]).read_bytes()
        assert hashlib.sha256(summary_bytes).hexdigest() == binding["sha256"]
        selected = json.loads(summary_bytes)["cohorts"]["shadow_slot_1"]["selected_slots"]
        assert payload == {"ready": True, "selected": selected}
