"""Read-only presentation projections must never change frozen ranking data."""
from __future__ import annotations

import copy
import csv
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
    names = ["promotionSlotStatistics", "refreshPromotionSlotStatistics", "compactShadowSource", "renderCompactDashboard",
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


def test_compact_view_has_exactly_five_cohorts_and_archive_does_not_delete_data():
    result = run("state.currentPublicObservationStatistics=input.summary;state.promotionSlotStatistics=promotionSlotStatistics(input.summary,input.rows,input.contracts);renderCompactDashboard();console.log(JSON.stringify({html:els.compactStatisticsContent.innerHTML,hidden:Object.fromEntries([...nodes].map(([k,v])=>[k,v.hidden]))}))")
    assert result["html"].count("晋级第") == 3 and result["html"].count("盈利第") == 2
    assert "尚未覆盖当前 D 2026-09-08" in result["html"]
    assert "非重建新账本的前向实盘成绩" in result["html"]
    assert result["hidden"]["shadowWorkspace"] and result["hidden"]["historicalResearchDetails"]
    archive = run("location.search='?view=research';renderCompactDashboard();console.log(JSON.stringify(Object.fromEntries([...nodes].map(([k,v])=>[k,v.hidden]))))")
    assert archive["compactStatistics"] and not archive["historicalResearchDetails"]


def test_unavailable_statistics_are_not_fake_zero_and_do_not_borrow_shadow():
    result = run("state.promotionSlotError='SHA校验失败';renderCompactDashboard();console.log(JSON.stringify(els.compactStatisticsContent.innerHTML))")
    assert result.count("SHA校验失败") == 3
    assert "独立自然冻结累计尚未通过校验" in result
    assert "<td>0</td>" not in result


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
