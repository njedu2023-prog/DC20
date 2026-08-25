from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.decision_pages_truth import (
    DecisionPagesTruthError,
    validate_report_index_action_truth,
    validate_three_rank_index_truth,
)
from top10decision.decision.three_rank import (
    THREE_RANK_CONTRACT_VERSION,
    build_three_rank_contract,
    materialize_three_rank_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_exposes_exact_three_rank_fields_and_downloads() -> None:
    text = (ROOT / "decision.html").read_text(encoding="utf-8")

    for token in (
        "decision_three_rank_top10_v1",
        "decision_three_rank_v1",
        "promotion_rank",
        "predicted_promotion_probability",
        "big_loss_safety_rank",
        "predicted_big_loss_probability",
        "profit_rank",
        "predicted_profit_probability",
        "top10_members_sha256",
        "晋级排序",
        "大跌排序（安全优先）",
        "盈利排序",
        "下载 CSV",
        "下载 JSON",
        "验证门通过率",
        "100%为全部门通过；不是收益率或准确率",
        "价值：在D日2→3/3→4全池中估计T日晋级涨停概率",
        "价值：只在冻结Top10内估计T至T+1净收益≤-3%的概率",
        "价值：只在冻结Top10内估计T+1净收益>0的概率",
        "P_fill 可买性影子",
        "仅影子，不得改变成员或三核心排名",
        "SHADOW_ONLY · 不进入三核心模型READY聚合",
    ):
        assert token in text


def test_dashboard_fails_closed_and_shadow_cannot_override_core_rank() -> None:
    text = (ROOT / "decision.html").read_text(encoding="utf-8")

    assert "系统不会使用常数或旧观察顺序伪造Top10" in text
    assert "不会回退到旧排名或展示部分数据" in text
    assert "model.status === \"READY\"" in text
    assert "shadow.may_change_membership !== false" in text
    assert "shadow.may_override_core_ranks !== false" in text
    assert "P_fill 可买性影子 Top2（独立影子模块）" not in text
    assert "Trade Selector 二筛影子（旧策略链路，仅注释）" not in text
    assert "历史三排名研究归档" not in text
    assert "pFillShadowTop2Html" not in text
    assert "loadThreeRankHistorySummary" not in text
    # The new renderer may join truth fields, but the frozen artifact row is
    # spread last so truth/shadow records cannot replace any core rank field.
    assert "...(truthByCode.get(String(row.ts_code)) || {}),\n          ...row," in text


def test_daily_and_auction_writers_allow_exact_dated_three_rank_artifacts() -> None:
    daily = (ROOT / ".github/workflows/run_decision_daily.yml").read_text(
        encoding="utf-8"
    )
    auction = (ROOT / ".github/workflows/run_auction_v3.yml").read_text(
        encoding="utf-8"
    )

    assert daily.count("outputs/decision/three_rank_top10_20??????.json") == 2
    assert daily.count("outputs/decision/three_rank_top10_20??????.csv") == 2
    assert auction.count("outputs/decision/three_rank_top10_20??????.json") == 2
    assert auction.count("outputs/decision/three_rank_top10_20??????.csv") == 2
    assert daily.count("'outputs/decision/three_rank_index.json'") == 2
    assert auction.count("'outputs/decision/three_rank_index.json'") == 2
    assert daily.count("outputs/decision/research_context_dc20_20??????.json") == 2
    assert "outputs/decision/three_rank_top10_latest" not in daily
    assert "outputs/decision/three_rank_top10_latest" not in auction
    assert "outputs/decision/three_rank_index_20" not in daily
    assert "outputs/decision/three_rank_index_20" not in auction

    daily_compute = daily.split(
        "- name: Build exact allowlisted Daily candidate patch", 1
    )[1].split("- name: Upload immutable candidate patch", 1)[0]
    daily_publish = daily.split(
        "- name: Apply exact base candidate and create one commit", 1
    )[1].split("- name: Publish exact CAS commit", 1)[0]
    auction_compute = auction.split(
        "- name: Build exact allowlisted candidate patch", 1
    )[1].split("- name: Upload immutable candidate patch", 1)[0]
    auction_publish = auction.split(
        "- name: Apply candidate with base-SHA CAS and create one commit", 1
    )[1].split("- name: Publish exact CAS commit", 1)[0]
    for segment in (
        daily_compute,
        daily_publish,
        auction_compute,
        auction_publish,
    ):
        assert segment.count("'outputs/decision/three_rank_index.json'") == 1
        assert "three_rank_index*" not in segment
        assert "three_rank_index_" not in segment


def _ready_contract_plan() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for rank in range(1, 4):
        row: dict[str, object] = {
            "ts_code": f"60000{rank}.SH",
            "name": f"样本{rank}",
            "industry": "测试",
            "stage_transition": "2→3" if rank != 2 else "3→4",
            "top10_selected": 1,
            "three_rank_contract_version": THREE_RANK_CONTRACT_VERSION,
            "promotion_pool_size": 3,
            "feature_snapshot_sha256": "f" * 64,
            "promotion_rank": rank,
            "predicted_promotion_probability": 0.9 - rank * 0.1,
            "big_loss_safety_rank": 4 - rank,
            "predicted_big_loss_probability": rank * 0.05,
            "profit_rank": rank,
            "predicted_profit_probability": 0.8 - rank * 0.1,
            "p_fill_shadow_rank": rank,
            "p_fill_shadow_probability": 0.5,
            "p_fill_shadow_status": "SHADOW_READY",
            "p_fill_shadow_model_version": "p_fill_shadow_v1",
            "p_fill_shadow_model_as_of_date": "20260819",
            "p_fill_shadow_model_artifact_sha256": "4" * 64,
            "p_fill_shadow_validation_gate_pass_count": 26,
            "p_fill_shadow_validation_gate_total_count": 26,
            "p_fill_shadow_validation_gate_score_pct": 100.0,
        }
        gate_scores = {
            "promotion": (26, 26, 100.0),
            "big_loss": (17, 26, 65.4),
            "profit": (20, 26, 76.9),
        }
        for index, head in enumerate(("promotion", "big_loss", "profit"), 1):
            pass_count, total_count, score = gate_scores[head]
            row.update(
                {
                    f"{head}_model_status": "READY",
                    f"{head}_model_version": f"{head}_v1",
                    f"{head}_model_as_of_date": "20260819",
                    f"{head}_model_artifact_sha256": str(index) * 64,
                    f"{head}_validation_gate_pass_count": pass_count,
                    f"{head}_validation_gate_total_count": total_count,
                    f"{head}_validation_gate_score_pct": score,
                }
            )
        rows.append(row)
    return {
        "generated_at_utc": "2026-08-20T13:20:00Z",
        "signal_date": "20260820",
        "exec_date": "20260821",
        "exit_date": "20260822",
        "candidates": rows,
        "model": {},
    }


def test_dashboard_shows_legacy_profit_relative_research_without_promoting_it() -> None:
    text = (ROOT / "decision.html").read_text(encoding="utf-8")

    for token in (
        "dc20_legacy_profit_relative_research_projection_v1",
        "PUBLIC_RESEARCH_ONLY_NOT_FORMAL",
        "raw_model_relative_score_not_probability",
        "legacy_profit_relative_rank",
        "legacy_profit_raw_score",
        "legacy_profit_relative_percentile",
        "原盈利模型·相对实验排序（未放行）",
        "按原盈利模型实验顺序（未放行）",
        "原模型相对分（非概率）",
        "未融合P_fill",
        "不等于下方新的“可实现盈利概率排序”",
        'model.official_status === "NOT_READY_VALIDATION_GATE"',
        "model.validation_gate_score_pct === 76.9",
        "source.bundle_sha256 === contract.bundle_sha256",
        "projection.top10_members_sha256 === contract.top10_members_sha256",
        "projection.feature_snapshot_sha256 === contract.feature_snapshot_sha256",
    ):
        assert token in text

    renderer = text.split("function renderThreeRankWatchlist", 1)[1].split(
        "function renderStageWatchlist", 1
    )[0]
    audit = text.split("function threeRankAuditSectionHtml", 1)[1].split(
        "function renderAudit", 1
    )[0]
    assert 'renderRows("promotion_rank")' in renderer
    assert "threeRankModelCardsHtml(contract)" not in renderer
    assert "threeRankModelCardsHtml(contract)" in audit
    assert "同一TopN集合指纹" in audit
    assert "models.profit?.status === \"READY\"" in renderer


def test_legacy_profit_relative_sidecar_is_same_origin_only() -> None:
    text = (ROOT / "decision.html").read_text(encoding="utf-8")

    reader = text.split(
        "async function fetchLegacyProfitRelativePath", 1
    )[1].split("async function validateLegacyProfitRelativeProjection", 1)[0]
    loader = text.split(
        "async function loadCurrentLegacyProfitRelativeResearch", 1
    )[1].split("async function refreshCurrentLegacyProfitRelativeResearch", 1)[0]

    # On GitHub Pages, the optional experiment may only use files actually
    # published on the same origin. RAW_BASE is retained solely for file://
    # development and must never become a production fallback.
    assert 'location.protocol === "file:"' in reader
    assert "RAW_BASE + path" in reader
    assert "new URL(path, location.href).href" in reader
    assert "new URL(url).origin !== location.origin" in reader
    assert "原盈利实验排序拒绝跨源读取" in reader
    assert "[primary, RAW_BASE + path]" not in reader

    # The pointer and both dated artifacts share the isolated reader. A Pages
    # 404 therefore hides only this experiment instead of reading raw/main.
    assert loader.count("fetchLegacyProfitRelativePath(") == 3
    assert "fetchPath(" not in loader


def test_ready_fixture_builds_rank_bound_pfill_shadow_top2() -> None:
    contract = build_three_rank_contract(_ready_contract_plan())

    assert contract["shadow_contract"]["model_status"] == "SHADOW_READY"
    assert contract["shadow_contract"]["may_change_membership"] is False
    assert contract["shadow_contract"]["may_override_core_ranks"] is False
    assert contract["shadow_top2"]["may_create_trade_action"] is False
    assert contract["shadow_top2"]["actual_slots"] == 2
    assert [row["p_fill_shadow_rank"] for row in contract["shadow_top2"]["rows"]] == [
        1,
        2,
    ]
    assert [row["ts_code"] for row in contract["shadow_top2"]["rows"]] == [
        "600001.SH",
        "600002.SH",
    ]


def _write_public_three_rank_action(site_root: Path) -> tuple[Path, Path]:
    contract = build_three_rank_contract(_ready_contract_plan())
    _, csv_path, enriched = materialize_three_rank_artifacts(
        site_root, contract
    )
    output = site_root / "outputs" / "decision"
    action_path = output / "action_plan_20260821.json"
    action_path.write_text(
        json.dumps(
            {
                "schema_version": "decision_action_plan_v99_test",
                "report_date": "20260821",
                "signal_date": "20260820",
                "exec_date": "20260821",
                "exit_date": "20260822",
                "broker_connected": False,
                "execution_or_fill_claimed": False,
                "three_rank": enriched,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    index_path = output / "report_index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "decision_report_index_v2_action_truth",
                "generated_at_utc": "2026-08-20T14:00:00+00:00",
                "latest_report_date": "20260821",
                "latest_report_file": "decision_report_20260821.md",
                "latest_action_report_date": "20260821",
                "latest_action_url": (
                    "outputs/decision/action_plan_20260821.json"
                ),
                "reports": [
                    {
                        "report_date": "20260821",
                        "report_file": "decision_report_20260821.md",
                        "report_url": (
                            "outputs/decision/decision_report_20260821.md"
                        ),
                        "eval_url": "outputs/decision/eval_20260821.json",
                        "action_available": True,
                        "action_url": (
                            "outputs/decision/action_plan_20260821.json"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return index_path, csv_path


def test_pages_gate_verifies_three_rank_json_csv_and_rejects_hash_drift(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    index_path, csv_path = _write_public_three_rank_action(site_root)

    truth = validate_report_index_action_truth(
        report_index_path=index_path,
        site_root=site_root,
    )
    assert truth.action_dates == ("20260821",)

    csv_path.write_bytes(csv_path.read_bytes() + b"\n")
    with pytest.raises(DecisionPagesTruthError, match="CSV download hash drifted"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )


def test_pages_gate_rejects_inconsistent_validation_gate_score(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    index_path, _ = _write_public_three_rank_action(site_root)
    action_path = site_root / "outputs" / "decision" / "action_plan_20260821.json"
    action = json.loads(action_path.read_text(encoding="utf-8"))
    action["three_rank"]["models"]["big_loss"][
        "validation_gate_score_pct"
    ] = 65.5
    action_path.write_text(json.dumps(action), encoding="utf-8")

    with pytest.raises(DecisionPagesTruthError, match="gate score is inconsistent"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )


def test_pages_gate_rejects_three_rank_index_alias_or_date_drift(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    _write_public_three_rank_action(site_root)
    pointer = site_root / "outputs" / "decision" / "three_rank_index.json"

    validate_three_rank_index_truth(index_path=pointer, site_root=site_root)
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["data_alias"] = True
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DecisionPagesTruthError, match="index contract is invalid"):
        validate_three_rank_index_truth(index_path=pointer, site_root=site_root)

    payload["data_alias"] = False
    payload["latest_contract_url"] = "outputs/decision/three_rank_top10_latest.json"
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DecisionPagesTruthError, match="index URL is not dated"):
        validate_three_rank_index_truth(index_path=pointer, site_root=site_root)

    payload["latest_contract_url"] = (
        "outputs/decision/three_rank_top10_20260820.json"
    )
    payload["latest_contract_sha256"] = "0" * 64
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        DecisionPagesTruthError,
        match="differs from its immutable dated contract",
    ):
        validate_three_rank_index_truth(index_path=pointer, site_root=site_root)
