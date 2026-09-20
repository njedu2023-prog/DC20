"""Full-list display uses SHA-bound existing rows, not new model or ledger data."""
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


def evaluate(data):
    html = (ROOT / "decision.html").read_text()
    functions = "\n".join(re.search(rf"^    function {name}\(.*?^    }}", html, re.M | re.S).group()
        for name in ("fullListPromotionStatistics", "executableProfitExpect"))
    script = functions + "\nconst input=" + json.dumps(data) + ";\n"
    script += "try{console.log(JSON.stringify({value:fullListPromotionStatistics(input.rows,input.summary,'20260910',input.report)}))}catch(e){console.log(JSON.stringify({error:e.message}))}"
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def fixture():
    days, rows = [], []
    for d, t, hits in [("20260909", "20260910", [1]), ("20260910", "20260911", [1, 0, 0, 0]),
                       ("20260911", "20260914", [1]), ("20260914", "20260915", [None, None])]:
        days.append(dict(signal_date=d, exec_date=t, rows=len(hits), t_validated_rows=sum(h is not None for h in hits)))
        for rank, hit in enumerate(hits, 1):
            rows.append(dict(signal_date=d, exec_date=t, ts_code=f"60000{rank}.SH", promotion_rank=str(rank),
                             continuation_limit_up_hit="" if hit is None else str(hit),
                             validation_status="MISSING_T_TRUTH" if hit is None else "PENDING_T1"))
    return dict(rows=rows, summary=dict(as_of_date="20260915", daily_summaries=days), report="20260914")


def test_full_list_is_inclusive_weighted_and_counts_repeated_stock_per_d():
    data = fixture()
    original = copy.deepcopy(data)
    assert evaluate(data)["value"] == dict(count=7, verified=5, hits=2, hit_rate=.4)
    assert data == original  # no frozen inputs or signed window mutations


def test_history_navigation_keeps_latest_cumulative_window():
    data = fixture()
    data["report"] = "20260911"
    assert evaluate(data)["value"] == dict(count=5, verified=5, hits=2, hit_rate=.4)


def test_empty_and_pending_are_not_zero_success_rates():
    data = fixture()
    data["summary"]["daily_summaries"] = [data["summary"]["daily_summaries"][-1]]
    data["rows"] = data["rows"][-2:]
    assert evaluate(data)["value"] == dict(count=2, verified=0, hits=0, hit_rate=None)
    data["summary"]["daily_summaries"] = []
    data["rows"] = []
    assert evaluate(data)["value"] == dict(count=0, verified=0, hits=0, hit_rate=None)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "rank", "code", "hit", "pending", "count", "future", "orphan"])
def test_full_list_rejects_inconsistent_source_rows(mutation):
    data = fixture()
    if mutation == "duplicate": data["rows"].append(dict(data["rows"][1]))
    elif mutation == "missing": data["rows"].pop(1)
    elif mutation == "rank": data["rows"][1]["promotion_rank"] = "2"
    elif mutation == "code": data["rows"][1]["ts_code"] = "invalid"
    elif mutation == "hit": data["rows"][1]["continuation_limit_up_hit"] = " "
    elif mutation == "pending": data["rows"][1]["validation_status"] = "PENDING_T"
    elif mutation == "count": data["summary"]["daily_summaries"][1]["t_validated_rows"] -= 1
    elif mutation == "future":
        data["summary"]["daily_summaries"][1]["exec_date"] = "20260916"
        for row in data["rows"]:
            if row["signal_date"] == "20260910": row["exec_date"] = "20260916"
    else: data["rows"][1]["signal_date"] = "20260912"
    assert "error" in evaluate(data)


def test_public_rows_full_list_matches_direct_independent_count():
    summary = json.loads((ROOT / "outputs/decision/primary_observation/summary.json").read_text())
    rows = list(csv.DictReader((ROOT / summary["rows_path"]).read_text().splitlines()))
    selected = [r for r in rows if "20260910" <= r["signal_date"] <= summary["as_of_date"]]
    verified = [r for r in selected if r["continuation_limit_up_hit"] != ""]
    hits = sum(int(float(r["continuation_limit_up_hit"])) for r in verified)
    result = evaluate(dict(rows=rows, summary=summary, report=summary["as_of_date"]))["value"]
    assert result == dict(count=len(selected), verified=len(verified), hits=hits, hit_rate=hits/len(verified) if verified else None)


def test_four_compact_cards_render_without_changing_existing_ranks():
    from runpy import run_path
    run = run_path(str(ROOT / "tests/test_compact_rank_statistics.py"))["run"]
    output = run("installRenderWindow(null,[{rank:1,count:6,verified:6,hitRate:.5},{rank:2,count:6,verified:6,hitRate:4/6},{rank:3,count:6,verified:6,hitRate:.5}]);state.currentCompactStatisticsWindow.allPromotion={count:7,verified:5,hits:2,hit_rate:.4};renderCompactDashboard();console.log(JSON.stringify(els.compactStatisticsContent.innerHTML))", {"renderer_only": True})
    assert "<dt>全名单</dt>" in output and "40.00%" in output
    assert "已验证 5 次 · 晋级成功 2 次" in output
    assert all(f"<dt>Top{i}</dt>" in output for i in (1, 2, 3))
    assert output.count('class="success-rate"') == 4 and "D 2026-09-10起" in output
    html = (ROOT / "decision.html").read_text()
    assert ".promotion-success-summary { display: grid; grid-template-columns: repeat(4," in html
    assert "allPromotion = fullListPromotionStatistics(sourceRows, summary, payload.start_signal_date, reportSignalDate);" in html
