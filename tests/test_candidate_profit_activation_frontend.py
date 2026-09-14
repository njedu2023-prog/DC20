"""Run the actual browser script with synthetic SHA-bound public artifacts.

No source collection, publication, model fit, or real future outcome is used.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from top10decision.decision import candidate_profit_publication as publication
from top10decision.decision import candidate_formal_shadow_summary as ledger

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node required for real JS execution")
PREFIX = "outputs/decision/candidate_profit_v1/"


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else raw(value)).hexdigest()


def fixture():
    config = publication.activation_identity(publication.EXPECTED_ACTIVATION)
    rows = [{"ts_code": code, "name": name, "industry": "测试行业", "promotion_rank": promotion,
             "candidate_rank": rank, "candidate_score": score}
            for code, name, promotion, rank, score in [
                ("600002.SH", "测试乙", 2, 1, -0.02), ("600001.SH", "测试甲", 1, 2, -0.03)]]
    p0 = {"signal_date": "20260914", "exec_date": "20260915", "exit_date": "20260916",
          "rows": [{**r, "stage_transition": "2→3", "predicted_promotion_probability": 0.6} for r in rows],
          "models": {"promotion": {"status": "READY"}}, "promotion_pool_size": 2,
          "generated_at_utc": "2026-09-14T08:01:00Z", "bundle_sha256": "c" * 64}
    slots = [{"slot": r["candidate_rank"], "status": "PENDING_T", "board_stage": 2,
              **{k: r[k] for k in ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")},
              "proxy_fill": None, "net_return": None, "slot_net_return": None} for r in rows]
    day = {"schema_version": publication.DAY_SCHEMA, "status": "FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION",
           **{k: v for k, v in config.items() if k != "enabled"},
           **{k: p0[k] for k in ("signal_date", "exec_date", "exit_date")},
           "rows": rows, "candidate_count": 2, "candidate_slots": slots,
           "promotion_slots": [{**r, "slot": i + 1} for i, r in enumerate(sorted(slots, key=lambda r: r["promotion_rank"]))],
           "snapshot_file_sha256": "a" * 64, "source_main_sha": "b" * 40,
           "snapshot_source": {"path": "work/profit_1000_upgrade/candidate_natural_forward/day_20260914.json", "sha256": "a" * 64},
           "p0_file_sha256": sha(p0), "p0_source": {"path": "outputs/decision/three_rank_top10_20260914.json", "sha256": sha(p0)},
           "prediction_generated_at_utc": "2026-09-14T08:02:00Z", "pre_cas_freeze_at_utc": "2026-09-14T08:03:00Z",
           "projection_generated_at_utc": "2026-09-14T08:04:00Z", "formal_publication_deadline_utc": "2026-09-15T01:20:00Z",
           "provenance": {"observer_run_id": 123, "evidence_commit": "d" * 40, "evidence_manifest_sha256": "e" * 64, "publication_observation_sha256": "f" * 64},
           **{k: publication.EXPECTED_ACTIVATION[k] for k in ("entry_policy_id", "exit_policy_id", "round_trip_cost_rate", "shadow_notional_cny")},
           "promotion_ranking_unchanged": True, "historical_reranking_performed": False, "model_retrained": False,
           "score_is_probability": False, "profitability_improvement_proven": False, "actual_execution_claimed": False,
           "actual_capacity_verified": False, "source_price_observation_only": True}
    publication.validate_public_day(raw(day), expected_sha256=sha(day), activation=publication.EXPECTED_ACTIVATION)
    index = publication.build_public_index([day], activation=publication.EXPECTED_ACTIVATION,
        source_main_sha="b" * 40, generated_at_utc="2026-09-14T08:05:00Z")
    # build_public_index hashes its own canonical encoding; serve those bytes.
    encoded_day = publication.encoded(day).decode()
    sequences = {
        "candidate_top1": [
            {"signal_date": "20260914", "status": "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY", "slot_net_return": 0.1, "proxy_fill": 1, "snapshot_file_sha256": "a" * 64},
            {"signal_date": "20260915", "status": "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY", "slot_net_return": -0.05, "proxy_fill": 1, "snapshot_file_sha256": "a" * 64}],
        "candidate_top2": [
            {"signal_date": "20260914", "status": "NO_FILL_CAPACITY", "slot_net_return": 0.0, "proxy_fill": 0, "snapshot_file_sha256": "a" * 64},
            {"signal_date": "20260915", "status": "PENDING_EXIT_MINUTE_SOURCE", "slot_net_return": None, "proxy_fill": 1, "snapshot_file_sha256": "a" * 64}]}
    summary = {"schema_version": ledger.SCHEMA, **{k: v for k, v in config.items() if k != "enabled"},
               "model_version": config["activation_id"], "activation_signal_date": "20260914", "as_of_date": "20260916",
               "enabled": True, "publication_paused": False,
               "status": "FULL_ENUMERATED_SHADOW_STATISTICS", "test_only": False, "coverage_complete": True,
               "signal_dates": ["20260914", "20260915"], "published_signal_dates": ["20260914", "20260915"], "missing_signal_dates": [],
               "expected_day_count": 2, "published_day_count": 2, "expected_slot_count": 4,
               "universe_scope": "EVERY_PINNED_CALENDAR_D_FROM_ACTIVATION_THROUGH_ASOF",
               "groups": {k: ledger._metrics(v, coverage_complete=True) for k, v in sequences.items()},
               **{k: publication.EXPECTED_ACTIVATION[k] for k in ("entry_policy_id", "exit_policy_id", "round_trip_cost_rate", "shadow_notional_cny")},
               **ledger.FLAGS}
    wrapper = {**{k: p0[k] for k in ("signal_date", "exec_date", "exit_date")}, "three_rank": p0,
               "three_rank_source_index": {"json_path": day["p0_source"]["path"], "json_sha256": sha(p0)}}
    revision = {"candidate_profit_activation": config, "candidate_profit_index_url": PREFIX + "index.json",
                "candidate_profit_index_sha256": sha(index), "candidate_profit_summary_url": PREFIX + "summary.json",
                "candidate_profit_summary_sha256": sha(summary)}
    return {"config": config, "index": index, "day": day, "day_raw": encoded_day, "summary": summary,
            "summary_raw": raw(summary).decode(), "wrapper": wrapper, "revision": revision}


def run(body, data=None):
    script = re.search(r"<script>(.*?)</script>", (ROOT / "decision.html").read_text(), re.S).group(1)
    script = script.replace("initialize(false).catch(showError);", "")
    prelude = r"""
const crypto=require('crypto').webcrypto;
const nodes=new Map(),bodyNode={innerHTML:''},handlers={};
const buttons=['promotion_rank','mixed_profit_rank'].map(field=>({dataset:{threeRankSort:field},classList:{toggle(){}},setAttribute(){},addEventListener(t,fn){handlers[field]=fn}}));
const element=()=>({innerHTML:'',textContent:'',hidden:false,open:false,setAttribute(){},addEventListener(){},querySelector(){return bodyNode},querySelectorAll(s){return s.startsWith('button')?buttons:[]},classList:{toggle(){},add(){},remove(){}}});
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},querySelectorAll(){return []},addEventListener(){}};
const location={search:'',protocol:'https:',origin:'https://example.test',href:'https://example.test/DC20/'};
const window={location,addEventListener(){}};
"""
    tail = "\nconst input=" + json.dumps(data or fixture(), ensure_ascii=False) + ";\n"
    tail += r"""
Date.now=()=>Date.parse('2026-09-17T08:00:00Z');
const calls=[],revision=input.revision,index=input.index,day=input.day,summary=input.summary;
let dayRaw=input.day_raw,summaryRaw=input.summary_raw,hook=async()=>{};
const encode=x=>new TextEncoder().encode(JSON.stringify(x));
const rehash=async()=>{dayRaw=JSON.stringify(day);summaryRaw=JSON.stringify(summary);if(index.days.length)index.days[0].sha256=await sha256Hex(new TextEncoder().encode(dayRaw));revision.candidate_profit_index_sha256=await sha256Hex(encode(index));revision.candidate_profit_summary_sha256=await sha256Hex(new TextEncoder().encode(summaryRaw));};
fetchPagesOnlyPath=async(path,kind)=>{calls.push(path);await hook(path);let bytes;
 if(path===CANDIDATE_PROFIT_ROOT+'/index.json')bytes=encode(index);
 else if(path===CANDIDATE_PROFIT_ROOT+'/summary.json')bytes=new TextEncoder().encode(summaryRaw);
 else if(path===CANDIDATE_PROFIT_ROOT+'/day_20260914.json')bytes=new TextEncoder().encode(dayRaw);
 else throw Error('forbidden fixture path '+path);
 return kind==='bytes'?bytes:JSON.parse(new TextDecoder().decode(bytes));};
publishedRevisionMetadata=async()=>revision;
validatedThreeRankContract=v=>v?.three_rank||v;
state.compactLoadSequence=1;state.currentThreeRank=input.wrapper;
const load=async()=>{await refreshCandidateProfitActivation();await refreshCurrentExecutableProfitResearch(input.wrapper);await refreshCandidateProfitSummary();};
const render=()=>{renderThreeRankWatchlist({},input.wrapper.three_rank);renderCompactDashboard();return {
 activation:state.candidateProfitActivation?.status,profit:state.currentExecutableProfitResearch?.status,
 summary:state.candidateProfitSummaryLoad,view:unifiedProfitView(input.wrapper.three_rank).ready,
 header:els.stageContent.innerHTML,rows:bodyNode.innerHTML,stats:els.compactLedgerContent.innerHTML,
 statsHeader:els.compactLedgerState.textContent,calls};};
"""
    result = subprocess.run([NODE, "-"], input=prelude + script + tail + "\n(async()=>{" + body + "})().catch(e=>{console.error(e);process.exit(1)});", text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_sha_bound_native_shape_ranking_negative_scores_two_slots_and_new_statistics():
    out = run("const before=JSON.stringify(input);await load();const out=render();handlers.mixed_profit_rank();out.sorted=bodyNode.innerHTML;out.unchanged=JSON.stringify(input)===before;console.log(JSON.stringify(out));")
    assert out["activation"] == out["profit"] == "ready" and out["view"] and out["unchanged"]
    assert out["summary"]["status"] == "ready"
    assert out["rows"].index("600001.SH") < out["rows"].index("600002.SH")
    assert out["sorted"].index("600002.SH") < out["sorted"].index("600001.SH")
    assert '-0.0200' in out["rows"] and '-0.0300' in out["rows"]
    assert out["rows"].count('aria-label="盈利排序第') == 2
    assert "验证中" in out["header"] and "非资金净值" in out["stats"]
    assert '+4.50% ↑' in out["stats"] and '-5.00% ↓' in out["stats"]
    assert 'class="profit-cumulative-inline"' in out["stats"] and "2 个完整日" in out["stats"] and "1 个完整日" in out["stats"]
    assert 'class="positive"' in out["stats"] and 'class="negative"' in out["stats"]


@pytest.mark.parametrize("mutation", [
    "revision.candidate_profit_index_sha256='0'.repeat(64)",
    "index.model_canonical_sha256='0'.repeat(64);await rehash()",
    "index.days.push({...index.days[0]});await rehash()",
    "index.days[0].p0_file_sha256='0'.repeat(64);await rehash()",
    "day.rows[0].promotion_rank=1;await rehash()",
    "day.rows[0].candidate_rank=2;await rehash()",
    "day.rows[0].candidate_score=null;await rehash()",
    "day.rows[0].candidate_score=999;await rehash()",
    "day.candidate_slots[0].ts_code='600001.SH';await rehash()",
    "day.exec_date='20260916';await rehash()",
    "day.p0_source.sha256='0'.repeat(64);await rehash()",
    "day.prediction_generated_at_utc='2026-09-15T01:21:00Z';await rehash()",
    "day.profitability_improvement_proven=true;await rehash()",
    "day.round_trip_cost_rate=0;await rehash()",
])
def test_invalid_new_rank_never_borrows_old_rank_or_changes_promotion(mutation):
    out = run(mutation + ";await load();console.log(JSON.stringify(render()));")
    assert not out["view"] and out["profit"] == "invalid"
    assert out["rows"].count('data-profit-rank=""') == 2
    assert out["rows"].count('data-promotion-rank=') == 2
    assert not any('executable_profit_research' in path for path in out["calls"])


@pytest.mark.parametrize("mutation", [
    "delete revision.candidate_profit_index_sha256",
    "index.days=[];index.status='ACTIVE_WAITING_FIRST_NATURAL_D';await rehash()",
])
def test_enabled_missing_index_or_same_d_prediction_is_waiting_not_old_fallback(mutation):
    if "days=[]" in mutation:
        mutation = "index.days=[];index.status='ACTIVE_WAITING_FIRST_NATURAL_D';revision.candidate_profit_index_sha256=await sha256Hex(encode(index))"
    out = run(mutation + ";await load();console.log(JSON.stringify(render()));")
    assert not out["view"] and 'data-profit-rank=""' in out["rows"]
    assert "等待" in out["header"] and out["rows"].count('data-promotion-rank=') == 2


@pytest.mark.parametrize("mutation", [
    "revision.candidate_profit_summary_sha256='0'.repeat(64)",
    "summary.model_version='legacy';await rehash()",
    "summary.signal_dates[0]='20260911';await rehash()",
    "summary.groups.candidate_top1.filled_proxy_win_rate=.99;await rehash()",
    "summary.groups.candidate_top1.synthetic_max_drawdown=0;await rehash()",
    "summary.groups.candidate_top2.daily_sequence[1].slot_net_return=0;await rehash()",
    "summary.groups.candidate_top2.daily_sequence[0].slot_net_return=.1;await rehash()",
    "summary.old_model_results_consumed=true;await rehash()",
    "summary.test_only=true;await rehash()",
    "summary.as_of_date='20990101';await rehash()",
])
def test_bad_new_cumulative_keeps_rank_but_no_old_totals_or_zero(mutation):
    out = run(mutation + ";await load();console.log(JSON.stringify(render()));")
    assert out["view"] and out["summary"]["status"] == "invalid"
    assert out["stats"].count("待验证") >= 2 and "+4.50%" not in out["stats"]
    assert "D 2026-09-14起" in out["statsHeader"]


def test_history_navigation_keeps_latest_new_statistics_and_original_daily_rows():
    out = run("await load();const summaryBefore=state.candidateProfitSummary;const oldRows=JSON.stringify(input.wrapper.three_rank.rows);state.dailyRequestedDate='20260911';state.currentThreeRank={signal_date:'20260911'};renderCompactDashboard();console.log(JSON.stringify({same:summaryBefore===state.candidateProfitSummary,rows:oldRows===JSON.stringify(input.wrapper.three_rank.rows),header:els.compactLedgerState.textContent,html:els.compactLedgerContent.innerHTML}));")
    assert out["same"] and out["rows"] and "截至 2026-09-16" in out["header"]
    assert "+4.50%" in out["html"]


def test_disable_keeps_already_frozen_new_day_and_pauses_new_dates():
    out = run("revision.candidate_profit_activation.enabled=false;index.enabled=false;index.activation.enabled=false;index.status='DISABLED';summary.enabled=false;summary.publication_paused=true;summary.status='FORMAL_PUBLICATION_PAUSED';await rehash();await load();const out=render();out.future=candidateProfitApplies('20260915');console.log(JSON.stringify(out));")
    assert out["view"] and out["profit"] == "ready" and not out["future"]
    assert "暂停新增" in out["statsHeader"]


def test_old_revision_without_activation_preserves_legacy_route():
    out = run("delete revision.candidate_profit_activation;await refreshCandidateProfitActivation();console.log(JSON.stringify({status:state.candidateProfitActivation.status,applies:candidateProfitApplies('20260914'),calls}));")
    assert out == {"status": "legacy_deployment", "applies": False, "calls": []}


def test_stale_statistics_cannot_inject_into_new_navigation_load():
    out = run("await refreshCandidateProfitActivation();let release,entered;const gate=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);hook=async p=>{if(p.endsWith('/summary.json')){entered();await gate}};const pending=refreshCandidateProfitSummary();await waiting;state.compactLoadSequence++;release();await pending;console.log(JSON.stringify({injected:state.candidateProfitSummary!==null}));")
    assert not out["injected"]


def test_real_homepage_copy_paths_share_the_changed_single_source():
    for filename in ("deploy_dc20_pages.yml", "run_primary_d_daily.yml", "run_primary_profit_rankings.yml"):
        workflow = (ROOT / ".github/workflows" / filename).read_text()
        assert "cp decision.html _site/index.html" in workflow
        assert "cp decision.html _site/decision.html" in workflow
    assert not (ROOT / "index.html").exists()


@pytest.mark.parametrize("count", [0, 1])
def test_zero_or_one_candidate_keeps_complete_promotion_pool_without_padding(count):
    data = fixture()
    p0, day = data["wrapper"]["three_rank"], data["day"]
    p0["rows"] = p0["rows"][:count]
    day["rows"] = day["rows"][:count]
    p0["promotion_pool_size"] = day["candidate_count"] = count
    if count:
        p0["rows"][0]["promotion_rank"] = day["rows"][0]["promotion_rank"] = 1
    for key in ("candidate_slots", "promotion_slots"):
        for i, slot in enumerate(day[key]):
            if i < count:
                slot.update({k: day["rows"][i][k] for k in ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")})
            else:
                slot.update(status="MISSING_CANDIDATE", **{k: None for k in ("ts_code", "candidate_rank", "candidate_score", "promotion_rank", "board_stage")})
    day["p0_file_sha256"] = day["p0_source"]["sha256"] = data["wrapper"]["three_rank_source_index"]["json_sha256"] = sha(p0)
    data["index"] = publication.build_public_index([day], activation=publication.EXPECTED_ACTIVATION,
        source_main_sha="b" * 40, generated_at_utc="2026-09-14T08:05:00Z")
    data["day_raw"] = publication.encoded(day).decode()
    data["revision"]["candidate_profit_index_sha256"] = sha(data["index"])
    out = run("await load();console.log(JSON.stringify(render()));", data)
    assert out["view"] and out["profit"] == "ready"
    assert out["rows"].count('data-promotion-rank=') == count
    assert out["rows"].count('aria-label="盈利排序第') == count
    if not count:
        assert "不补票" in out["header"]


def test_missing_published_d_retains_known_metrics_but_disables_whole_window_curve():
    data = fixture()
    summary = data["summary"]
    summary.update(coverage_complete=False, status="FULL_ENUMERATED_WITH_MISSING_D",
                   missing_signal_dates=["20260915"], published_signal_dates=["20260914"], published_day_count=1)
    summary["groups"] = {group: ledger._metrics([values["daily_sequence"][0], ledger._missing_day("20260915", i)], coverage_complete=False)
                         for i, (group, values) in enumerate(summary["groups"].items(), 1)}
    data["summary_raw"] = raw(summary).decode()
    data["revision"]["candidate_profit_summary_sha256"] = sha(summary)
    out = run("await load();console.log(JSON.stringify(render()));", data)
    assert out["summary"]["status"] == "ready"
    assert "部分D冻结缺失，暂停累计" in out["stats"]
    assert "+10.00%" in out["stats"]  # Known filled mean, not missing-D zero.
    assert "+4.50%" not in out["stats"]


def test_first_activation_empty_summary_has_two_waiting_rows_and_no_zero_profit():
    data = fixture()
    summary = data["summary"]
    summary.update(as_of_date="20260911", status="AWAITING_FIRST_ACTIVATED_D", signal_dates=[], published_signal_dates=[],
                   expected_day_count=0, published_day_count=0, expected_slot_count=0)
    summary["groups"] = {group: ledger._metrics([], coverage_complete=True) for group in summary["groups"]}
    data["summary_raw"] = raw(summary).decode()
    data["revision"]["candidate_profit_summary_sha256"] = sha(summary)
    out = run("await load();console.log(JSON.stringify(render()));", data)
    assert out["summary"]["status"] == "ready"
    assert out["stats"].count('class="rank-mark rank-profit"') == 2
    assert "0.00%" not in out["stats"] and "待验证" in out["stats"]


def test_bad_activation_config_cannot_fall_through_to_old_profit():
    out = run("revision.candidate_profit_activation.model_canonical_sha256='0'.repeat(64);await load();console.log(JSON.stringify(render()));")
    assert not out["view"] and out["profit"] == "invalid"
    assert "激活配置待核验" in out["statsHeader"]
    assert not any('executable_profit_research' in path for path in out["calls"])


def test_disabled_corrupt_index_cannot_rerank_an_unknown_existing_day():
    out = run("revision.candidate_profit_activation.enabled=false;revision.candidate_profit_index_sha256='0'.repeat(64);await load();console.log(JSON.stringify(render()));")
    assert not out["view"] and out["profit"] == "invalid"
    assert not any('executable_profit_research' in path for path in out["calls"])


def test_stale_profit_day_response_cannot_inject_after_reload():
    out = run("await refreshCandidateProfitActivation();let release,entered;const gate=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);hook=async p=>{if(p.endsWith('/day_20260914.json')){entered();await gate}};const pending=refreshCurrentExecutableProfitResearch(input.wrapper);await waiting;state.compactLoadSequence++;release();await pending;console.log(JSON.stringify({injected:state.currentExecutableProfitResearch!==null}));")
    assert not out["injected"]
