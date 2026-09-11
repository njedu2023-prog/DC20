"""Execute the real public loader against immutable in-memory source variants."""
from __future__ import annotations

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


def run_case(case):
    from top10decision.decision.primary_profit_forward_shadow_bridge import _public_state_snapshot

    base = "outputs/decision/executable_profit_research/"
    index = json.loads((ROOT / (base + "shadow_index.json")).read_text())
    # Keep this historical v1 fixture even when the latest pointer advances.
    state = json.loads((ROOT / (base + "shadow_state_20260910_asof_20260910.json")).read_text())
    index.update(latest_signal_date=state["signal_date"], latest_exec_date=state["exec_date"], latest_exit_date=state["exit_date"], latest_as_of_date=state["as_of_date"], latest_selection_identity_sha256=state["source_bindings"]["selection"]["selection_identity_sha256"], latest_mixed_projection_sha256=state["source_bindings"]["mixed_projection"]["sha256"])
    primary = {"latest_projection_json_url": state["source_bindings"]["mixed_projection"]["path"], "latest_projection_json_sha256": state["source_bindings"]["mixed_projection"]["sha256"]}
    source = state["source_bindings"]["statistics"]
    archive = f"data/decision_executable_profit/forward/statistics/snapshots/summary_asof_{state['as_of_date']}_sha256_{source['sha256']}.json"
    raw = (ROOT / archive).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == source["sha256"]
    overlay = {archive: raw.decode()}
    if case == "v2_mixed_source_mismatch":
        state["source_bindings"]["mixed_projection"]["sha256"] = "a" * 64
    if case == "v2_selection_sha_mismatch":
        state["source_bindings"]["selection"]["sha256"] = "a" * 64
    if case == "v2_cohort_count_drift":
        state["cohorts"]["shadow_slot_1"]["selected_slots"] += 1
    versioned = not case.startswith("v1_")
    if versioned:
        state["schema_version"] = "dc20_primary_profit_forward_shadow_public_state_v2"
        source["path"] = archive
        state["snapshot_sha256"] = _public_state_snapshot(state)
    index["schema_version"] = f"dc20_primary_profit_forward_shadow_public_index_v{2 if versioned else 1}"
    index["latest_state_snapshot_sha256"] = state["snapshot_sha256"]
    index["latest_state_url"] = base + f"shadow_state_{state['signal_date']}_asof_{state['as_of_date']}" + (f"_sha256_{state['snapshot_sha256']}" if versioned else "") + ".json"
    encoded = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    index["latest_state_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    overlay[index["latest_state_url"]] = encoded
    if case == "v2_bad_path":
        index["latest_state_url"] = index["latest_state_url"].replace(state["snapshot_sha256"], "a" * 64)
    if case == "v2_bad_schema":
        index["schema_version"] = "dc20_primary_profit_forward_shadow_public_index_v1"
    if case in {"v2_corrupt", "v1_corrupt_archive"}:
        overlay[archive] = "{}"
    if case == "v2_missing":
        overlay[archive] = None
    if case.startswith("v1_"):
        overlay["data/decision_executable_profit/forward/statistics/summary.json"] = "{}"
    overlay[base + "shadow_index.json"] = json.dumps(index)
    if case == "v2_missing_sidecar":
        overlay[base + "shadow_index.json"] = None
        overlay[base + "shadow_cutover_index.json"] = None
    script = re.search(r"<script>(.*?)</script>", (ROOT / "decision.html").read_text(), re.S).group(1).replace("initialize(false).catch(showError);", "")
    prelude = """
const fs=require('fs'),crypto=require('crypto').webcrypto;
const element=()=>({innerHTML:'',textContent:'',hidden:false,open:false,setAttribute(){},addEventListener(){},querySelectorAll(){return []},classList:{toggle(){},add(){},remove(){}}});
const document={getElementById:()=>element(),querySelectorAll:()=>[],addEventListener(){}};
const location={search:'',protocol:'https:',href:'https://njedu2023-prog.github.io/DC20/'};
const window={location,addEventListener(){}};
"""
    tail = "\nconst root=" + json.dumps(str(ROOT)) + ",overlay=" + json.dumps(overlay, ensure_ascii=False) + ",requests=[];\n"
    tail += "fetchPagesOnlyPath=async(path,type)=>{requests.push(path);if(Object.hasOwn(overlay,path)&&overlay[path]===null)throw Error('HTTP 404 '+path);const bytes=new Uint8Array(Object.hasOwn(overlay,path)?Buffer.from(overlay[path]):fs.readFileSync(root+'/'+path));return type==='bytes'?bytes:JSON.parse(new TextDecoder().decode(bytes))};\n"
    tail += "const primary=" + json.dumps(primary) + ";\n"
    tail += """
(async()=>{
  const daily=await fetchPagesOnlyPath(PRIMARY_MIXED_DAILY_TOP2_INDEX_PATH);
  state.currentPrimaryMixedDailyTop2={status:'ready',index:await validatePrimaryMixedDailyTop2Index(daily)};
  const render=sidecar=>{
    state.currentExecutableProfitResearch={status:'ready',kind:'primary_core',index:primary,shadow:sidecar};
    const before=JSON.stringify({daily,sidecar});
    renderCompactDashboard();
    return {html:els.compactLedgerContent.innerHTML,header:els.compactLedgerState.textContent,
      unchanged:JSON.stringify({daily,sidecar})===before};
  };
  try {
    const projection=await fetchPagesOnlyPath(primary.latest_projection_json_url);
    const r=await loadPrimaryProfitShadowSidecar(projection,primary);
    console.log(JSON.stringify({ready:r?.publicWindowReady===true,requests,...render(r)}));
  } catch(e) {
    console.log(JSON.stringify({error:e.message,requests,...render(null)}));
  }
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run([NODE, "-"], input=prelude + script + tail, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), archive


@pytest.mark.parametrize("case", [
    "v2_ok", "v2_corrupt", "v2_missing", "v2_bad_path", "v2_bad_schema",
    "v1_archive", "v1_corrupt_archive", "v2_missing_sidecar",
    "v2_mixed_source_mismatch", "v2_selection_sha_mismatch", "v2_cohort_count_drift",
])
def test_versioned_snapshot_and_legacy_archive_are_exact_sha_bound(case):
    result, archive = run_case(case)
    if case in {"v2_ok", "v1_archive"}:
        assert result["ready"] is True
        assert archive in result["requests"]
        assert result["html"].count('class="three-rank-table profit-summary-table"') == 1
        assert result["html"].count('class="rank-mark rank-profit"') == 2
        assert result["html"].endswith("</tbody></table></div>")
    elif case == "v2_missing_sidecar":
        assert result["ready"] is False
        assert "error" not in result
        assert result["html"] == ""
    else:
        assert result.get("error")
        assert result["html"] == ""
    assert result["unchanged"] is True
    assert "日 / " in result["header"] and result["header"].endswith("席")
    assert "outputs/decision/executable_profit_research/daily_mixed_top2_index.json" in result["requests"]
    for removed in ("<details", "compactProfitDailyDetails", "每日记录与验证", "stats-note", 'data-field="code"'):
        assert removed not in result["html"]
    if case.startswith("v2_"):
        assert "data/decision_executable_profit/forward/statistics/summary.json" not in result["requests"]
    expected_error = {
        "v2_mixed_source_mismatch": "没有绑定同D同SHA混合盈利投影",
        "v2_selection_sha_mismatch": "冻结选择字节SHA256不一致",
        "v2_cohort_count_drift": "summary分组不一致",
    }.get(case)
    if expected_error:
        assert expected_error in result["error"]
