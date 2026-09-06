"""Execute the actual T/T+1 frontend loaders against committed, SHA-bound evidence."""
import json
import csv
import io
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required")


def run(body, *, date="20260828", now="2026-09-06T12:00:00Z"):
    text = (ROOT / "decision.html").read_text()
    names = ("loadCurrentThreeRankTTruth", "loadCurrentThreeRankObservationTruth",
             "refreshCurrentThreeRankTTruth", "threeRankTruthClock", "threeRankTruthStatusLabel",
             "threeRankRowTruth", "canonicalYmd", "finiteNumber", "dateText", "continuationLabel", "truthClass",
             "parseStrictCsvBytes", "sha256Hex", "isSha256")
    functions = []
    for name in names:
        functions.append(re.search(rf"^    (?:async )?function {name}\(.*?^    }}", text, re.M | re.S).group())
    contract = json.loads((ROOT / f"outputs/decision/three_rank_top10_{date}.json").read_text())
    summary = json.loads((ROOT / "outputs/decision/primary_observation/summary.json").read_text())
    paths = [summary["rows_path"]] + [f"data/market/raw/{contract['exec_date'][:4]}/{contract['exec_date']}/{name}.csv" for name in ("daily", "stk_limit")]
    files = {path: (ROOT / path).read_text() for path in paths if (ROOT / path).exists()}
    for path in list(files):
        if path.startswith("data/market/raw/"):
            reader = csv.DictReader(io.StringIO(files[path].lstrip("\ufeff")))
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
            writer.writeheader()
            codes = {row["ts_code"] for row in contract["rows"]}
            writer.writerows(row for row in reader if row["ts_code"] in codes)
            files[path] = out.getvalue()
    script = "\n".join(functions) + "\n" + f"""
const crypto = require('node:crypto').webcrypto;
Date.now = () => Date.parse({json.dumps(now)});
const contract = {json.dumps(contract)}, summary = {json.dumps(summary)}, files = {json.dumps(files)};
const state = {{currentPublicObservationStatistics:summary,publicObservationLoad:{{status:'ready'}}}};
const validatedThreeRankContract = plan => plan.three_rank;
const calls = [];
async function fetchPublishedBytes(path) {{
  calls.push(path); if (!(path in files)) throw new Error('exact revision HTTP 404');
  return new TextEncoder().encode(files[path]);
}}
const plan = {{three_rank:contract}};
""" + "\n(async()=>{" + body + "})().catch(error=>{console.error(error);process.exit(1)});"
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_sunday_and_t_intraday_never_read_close_or_show_premature_truth():
    for date, now, status in [("20260904", "2026-09-06T12:00:00Z", "PENDING_T"),
                              ("20260903", "2026-09-04T06:59:59Z", "PENDING_T_CLOSE")]:
        out = run("await refreshCurrentThreeRankTTruth(plan); console.log(JSON.stringify({truth:state.currentThreeRankTTruth, calls, row:threeRankRowTruth(contract,contract.rows[0].ts_code,state.currentThreeRankTTruth,state.currentThreeRankObservationTruth)}));", date=date, now=now)
        assert out["truth"]["status"] == status
        assert not any("data/market/raw" in path for path in out["calls"])
        assert out["row"]["continuation_limit_up_hit"] is None
        assert out["row"]["actual_net_return"] is None
        assert "收盘" in out["row"]["continuation_status_label"]


def test_complete_t1_proxy_and_no_fill_preserve_distinct_returns_without_mutating_ranks():
    out = run("const before=JSON.stringify(contract);await refreshCurrentThreeRankTTruth(plan);console.log(JSON.stringify({unchanged:before===JSON.stringify(contract), observation:state.currentThreeRankObservationTruth,rows:contract.rows.map(r=>threeRankRowTruth(contract,r.ts_code,state.currentThreeRankTTruth,state.currentThreeRankObservationTruth))}));")
    assert out["unchanged"] and out["observation"]["status"] == "READY"
    assert out["rows"][0]["validation_status"] == "FINAL_NO_FILL_PROXY"
    assert out["rows"][0]["actual_net_return"] is None
    assert out["rows"][1]["validation_status"] == "FINAL_VERIFIED_PROXY"
    assert out["rows"][1]["actual_net_return"] == pytest.approx(0.039686888808402716)
    assert "退出 2026-09-01" in out["rows"][1]["validation_status_label"]
    assert "等待T+1" not in out["rows"][1]["validation_status_label"]


@pytest.mark.parametrize("mutation", [
    "summary.bindings.find(b=>b.signal_date===contract.signal_date).p0.latest_bundle_sha256='a'.repeat(64);",
    "summary.bindings.find(b=>b.signal_date===contract.signal_date).p0.latest_top10_members_sha256='a'.repeat(64);",
    "summary.bindings.find(b=>b.signal_date===contract.signal_date).p0.latest_exec_date='20260902';",
    "summary.rows_sha256='a'.repeat(64);",
    "summary.policy.round_trip_cost_rate=0.009;",
    "summary.policy.id='different_return_policy';",
    "files[summary.rows_path]=files[summary.rows_path].replace('20260901,28.83','20260831,28.83');summary.rows_sha256=await sha256Hex(new TextEncoder().encode(files[summary.rows_path]));",
    "files[summary.rows_path]=files[summary.rows_path].replace('20260901,28.83','20260907,28.83');summary.rows_sha256=await sha256Hex(new TextEncoder().encode(files[summary.rows_path]));",
    "files[summary.rows_path]=files[summary.rows_path].replace('20260831,20260901','20260902,20260901');summary.rows_sha256=await sha256Hex(new TextEncoder().encode(files[summary.rows_path]));",
    "files[summary.rows_path]=files[summary.rows_path].split('\\n').filter(line=>!line.includes('600540.SH')).join('\\n');summary.rows_sha256=await sha256Hex(new TextEncoder().encode(files[summary.rows_path]));"
])
def test_observation_bad_date_sha_or_missing_member_is_explicit_and_not_zero(mutation):
    out = run(mutation + "await refreshCurrentThreeRankTTruth(plan);console.log(JSON.stringify({observation:state.currentThreeRankObservationTruth,row:threeRankRowTruth(contract,contract.rows[0].ts_code,state.currentThreeRankTTruth,state.currentThreeRankObservationTruth)}));")
    assert out["observation"]["status"] == "OBSERVATION_INVALID"
    assert out["row"]["actual_net_return"] is None
    assert "校验失败" in out["row"]["validation_status_label"]


def test_mature_missing_t_csv_is_not_pending_and_stale_t1_cannot_be_zero():
    out = run("await refreshCurrentThreeRankTTruth(plan);console.log(JSON.stringify({t:state.currentThreeRankTTruth,row:threeRankRowTruth(contract,contract.rows[0].ts_code,state.currentThreeRankTTruth,state.currentThreeRankObservationTruth)}));", date="20260904", now="2026-09-08T08:00:00Z")
    assert out["t"]["status"] == "MISSING_T_TRUTH"
    assert "已到期" in out["row"]["continuation_status_label"]
    assert out["row"]["validation_status"] == "OBSERVATION_STALE"
    assert out["row"]["actual_net_return"] is None


def test_t1_intraday_does_not_expose_final_proxy_before_close_gate():
    out = run("await refreshCurrentThreeRankTTruth(plan);Date.now=()=>Date.parse('2026-09-01T06:59:59Z');console.log(JSON.stringify(threeRankRowTruth(contract,contract.rows[1].ts_code,state.currentThreeRankTTruth,state.currentThreeRankObservationTruth)));")
    assert out["validation_status"] == "PENDING_T1_CLOSE"
    assert out["actual_net_return"] is None


def test_final_actual_exit_later_than_current_closed_date_is_invalid():
    out = run("await refreshCurrentThreeRankTTruth(plan);console.log(JSON.stringify(state.currentThreeRankObservationTruth));", now="2026-09-01T06:59:59Z")
    assert out["status"] == "OBSERVATION_INVALID"
    assert "退出日期" in out["error"]


def test_conflicting_t_close_and_observation_truth_fail_closed():
    out = run("files[summary.rows_path]=files[summary.rows_path].replace('False,,1,20260831','False,,0,20260831');summary.rows_sha256=await sha256Hex(new TextEncoder().encode(files[summary.rows_path]));await refreshCurrentThreeRankTTruth(plan);console.log(JSON.stringify({t:state.currentThreeRankTTruth,o:state.currentThreeRankObservationTruth}));")
    assert out["t"]["status"] == "INVALID_T_TRUTH"
    assert out["o"]["status"] == "OBSERVATION_INVALID"


def test_proxy_settlement_badge_tones_preserve_pending_and_legacy_states():
    statuses = ["FINAL_VERIFIED_PROXY", "FINAL_NO_FILL_PROXY", "PENDING_T", "PENDING_T1",
                "MISSING_T_TRUTH", "MISSING_T1_TRUTH", "UNRESOLVED_EXIT_PROXY", "OBSERVATION_INVALID",
                "FINAL_VERIFIED", "FINAL_NO_FILL", "T_VERIFIED_NO_FILL"]
    result = run("console.log(JSON.stringify(" + json.dumps(statuses) + ".map(truthClass)));")
    assert result == ["final", "no-fill", "pending", "pending", "pending", "pending", "pending", "pending",
                      "final", "no-fill", "no-fill"]
