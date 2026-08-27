from __future__ import annotations

import hashlib
import json
import shutil
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from scripts.publish_primary_profit_rankings import (
    MODE_STATUS,
    MIXED_SCHEMA,
    SINGLE_SCHEMA,
    PrimaryProfitRankingError,
    publish_primary_profit_rankings,
    validate_primary_profit_bundle,
)
from top10decision.decision.three_rank import (
    build_three_rank_contract,
    materialize_three_rank_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
D = "20260826"
T = "20260827"
T1 = "20260828"
FEATURE_SHA = "f" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _model_meta() -> dict[str, dict[str, object]]:
    return {
        "promotion": {
            "status": "READY",
            "version": "promotion_fixture_v1",
            "as_of_date": "20260709",
            "artifact_sha256": "1" * 64,
            "validation_gate_pass_count": 26,
            "validation_gate_total_count": 26,
            "validation_gate_score_pct": 100.0,
        },
        "big_loss": {
            "status": "NOT_READY_PRIMARY_ONLY",
            "version": "",
            "as_of_date": "",
            "artifact_sha256": "",
            "validation_gate_pass_count": None,
            "validation_gate_total_count": None,
            "validation_gate_score_pct": None,
        },
        "profit": {
            "status": "NOT_READY_PRIMARY_ONLY",
            "version": "",
            "as_of_date": "",
            "artifact_sha256": "",
            "validation_gate_pass_count": None,
            "validation_gate_total_count": None,
            "validation_gate_score_pct": None,
        },
    }


def _write_primary_fixture(
    target: Path,
    *,
    mode: str = "RETROSPECTIVE_RECOVERY",
) -> tuple[Path, list[dict[str, object]]]:
    contract_target = target / "models/decision_primary_profit_research_contract.json"
    contract_target.parent.mkdir(parents=True)
    shutil.copy2(
        ROOT / "models/decision_primary_profit_research_contract.json",
        contract_target,
    )
    output = target / "outputs/decision"
    output.mkdir(parents=True)

    frozen_rows: list[dict[str, object]] = []
    runtime_rows: list[dict[str, object]] = []
    generated = (
        "2026-08-26T13:35:00+00:00"
        if mode == "NATURAL"
        else "2026-08-27T06:14:05+00:00"
    )
    for rank in range(1, 12):
        code = f"000{rank:03d}.SZ"
        transition = "3→4" if rank % 3 == 0 else "2→3"
        selected = int(rank <= 10)
        probability = 0.95 - rank * 0.025
        runtime_rows.append(
            {
                "identity": f"{D}|{code}|{transition}",
                "signal_date": D,
                "ts_code": code,
                "name": f"样本{rank}",
                "industry": "测试行业",
                "stage": 3 if transition == "3→4" else 2,
                "stage_transition": transition,
                "board": "SZ_MAIN",
                "generated_at_utc": generated,
                "feature_snapshot_sha256": FEATURE_SHA,
                "top10_selected": selected,
                "promotion_rank": rank,
                "predicted_promotion_probability": probability,
                "fixture_numeric_feature": float(rank),
            }
        )
        if selected:
            frozen_rows.append(
                {
                    "ts_code": code,
                    "name": f"样本{rank}",
                    "industry": "测试行业",
                    "stage_transition": transition,
                    "top10_selected": 1,
                    "promotion_pool_size": 11,
                    "promotion_rank": rank,
                    "predicted_promotion_probability": probability,
                    "big_loss_safety_rank": None,
                    "predicted_big_loss_probability": None,
                    "profit_rank": None,
                    "predicted_profit_probability": None,
                    "p_fill_shadow_rank": None,
                    "p_fill_shadow_probability": None,
                    "p_fill_shadow_status": "SHADOW_NOT_READY_PRIMARY_ONLY",
                }
            )
    three_rank = build_three_rank_contract(
        {
            "signal_date": D,
            "exec_date": T,
            "exit_date": T1,
            "generated_at_utc": generated,
            "feature_snapshot_sha256": FEATURE_SHA,
            "candidates": frozen_rows,
            "model": {"three_rank_models": _model_meta()},
        }
    )
    json_path, csv_path, three_rank = materialize_three_rank_artifacts(
        target,
        three_rank,
    )
    runtime_path = output / f"primary_d_runtime_features_{D}.csv"
    pd.DataFrame(runtime_rows).to_csv(runtime_path, index=False)
    identity_sha = _canonical_sha(
        {
            "schema": "dc20_primary_d_runtime_identity_v1",
            "signal_date": D,
            "rows": [
                {
                    "identity": row["identity"],
                    "ts_code": row["ts_code"],
                    "stage_transition": row["stage_transition"],
                    "top10_selected": row["top10_selected"],
                    "promotion_rank": row["promotion_rank"],
                }
                for row in sorted(runtime_rows, key=lambda item: str(item["ts_code"]))
            ],
        }
    )
    receipt = {
        "schema_version": "dc20_primary_d_receipt_v1",
        "artifact_kind": "p0_promotion_only_d_list_receipt",
        "generation_mode": mode,
        "prospective": mode == "NATURAL",
        "forward_eligible": mode == "NATURAL",
        "not_forward_generated": mode != "NATURAL",
        "signal_date": D,
        "exec_date": T,
        "exit_date": T1,
        "primary_status": "READY",
        "action_authorized": False,
        "action_input_consumed": False,
        "formal_trade_count": 0,
        "secondary_outputs_generated": {
            "action_plan": False,
            "big_loss": False,
            "profit": False,
            "p_fill_shadow": False,
            "executable_profit": False,
        },
        "outputs": {
            "json_path": json_path.relative_to(target).as_posix(),
            "json_sha256": _sha256(json_path),
            "csv_path": csv_path.relative_to(target).as_posix(),
            "csv_sha256": _sha256(csv_path),
            "bundle_sha256": three_rank["bundle_sha256"],
            "feature_snapshot_sha256": FEATURE_SHA,
            "top10_members_sha256": three_rank["top10_members_sha256"],
            "promotion_pool_size": 11,
            "top10_count": 10,
            "runtime_features_path": runtime_path.relative_to(target).as_posix(),
            "runtime_features_sha256": _sha256(runtime_path),
            "runtime_feature_row_count": 11,
            "runtime_selected_count": 10,
            "runtime_identity_sha256": identity_sha,
        },
    }
    receipt_path = output / f"primary_d_receipt_{D}.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime_path, runtime_rows


def _single_stub(inputs, *, bump: float = 0.0):
    rows = []
    for relative_rank, frozen in enumerate(reversed(inputs.three_rank["rows"]), start=1):
        rows.append(
            {
                "ts_code": frozen["ts_code"],
                "name": frozen["name"],
                "industry": frozen["industry"],
                "stage_transition": frozen["stage_transition"],
                "promotion_rank": frozen["promotion_rank"],
                "legacy_profit_relative_rank": relative_rank,
                "legacy_profit_raw_score": 0.4 + (11 - relative_rank) * 0.01 + bump,
                "legacy_profit_relative_percentile": (11 - relative_rank) / 10,
                "rank_tied": False,
                "rank_group_size": 1,
            }
        )
    return rows, {
        "head": "profit",
        "official_status": "NOT_READY_VALIDATION_GATE",
        "formal_ranking_ready": False,
        "formal_probability_ready": False,
        "score_semantics": "raw_model_relative_score_not_probability",
    }


def _mixed_stub(inputs):
    rows = []
    for rank, frozen in enumerate(inputs.three_rank["rows"], start=1):
        fill = 0.90 - rank * 0.01
        conditional = 0.55 - rank * 0.01
        joint = fill * conditional
        rows.append(
            {
                "ts_code": frozen["ts_code"],
                "name": frozen["name"],
                "industry": frozen["industry"],
                "stage_transition": frozen["stage_transition"],
                "promotion_rank": frozen["promotion_rank"],
                "predicted_promotion_probability": frozen[
                    "predicted_promotion_probability"
                ],
                "executable_profit_research_rank": rank,
                "estimated_executable_profit_probability": joint,
                "research_joint_proxy_score": joint,
                "research_fill_proxy_score": fill,
                "research_conditional_profit_score": conditional,
                "rank_tied": False,
                "rank_group_size": 1,
            }
        )
    return rows, {
        "status": "INTERNAL_CHALLENGER_NOT_READY",
        "artifact_status": "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY",
        "artifact_sha256": "2" * 64,
        "feature_columns_sha256": "3" * 64,
        "feature_count": 156,
        "model_loaded": True,
        "inference_performed": True,
        "calibrated_probability_output": False,
    }


def test_retrospective_p1_publishes_two_exact_topn_research_rankings_only(
    tmp_path: Path,
) -> None:
    _write_primary_fixture(tmp_path)
    # An invalid historical Action file is irrelevant and must not be read.
    (tmp_path / "outputs/decision/action_plan_latest.json").write_text(
        "not-json\n", encoding="utf-8"
    )
    result = publish_primary_profit_rankings(
        tmp_path,
        D,
        generation_mode="RETROSPECTIVE_RECOVERY",
        single_scorer=_single_stub,
        mixed_scorer=_mixed_stub,
    )
    assert result["status"] == MODE_STATUS["RETROSPECTIVE_RECOVERY"]
    assert result["candidate_count"] == 10
    assert result["forward_selection_created"] is False
    assert result["forward_statistics_updated"] is False
    assert result["action_input_consumed"] is False

    single = json.loads(result["single"]["json"].read_text(encoding="utf-8"))
    mixed = json.loads(result["mixed"]["json"].read_text(encoding="utf-8"))
    assert single["schema_version"] == SINGLE_SCHEMA
    assert mixed["schema_version"] == MIXED_SCHEMA
    assert single["status"] == mixed["status"] == "RETROSPECTIVE_NON_FORWARD_RESEARCH"
    assert single["prospective"] is mixed["prospective"] is False
    assert single["retrospective_non_forward"] is True
    assert len(single["rows"]) == len(mixed["rows"]) == 10
    assert {row["ts_code"] for row in single["rows"]} == {
        row["ts_code"] for row in mixed["rows"]
    }
    assert mixed["source_bindings"]["runtime_features"]["row_count"] == 11
    assert mixed["source_bindings"]["runtime_features"]["selected_count"] == 10
    assert not (tmp_path / "data/decision_executable_profit/forward").exists()
    assert (tmp_path / "outputs/decision/action_plan_latest.json").read_text() == "not-json\n"


def test_natural_p1_is_prospective_research_but_still_not_a_shadow_selection(
    tmp_path: Path,
) -> None:
    _write_primary_fixture(tmp_path, mode="NATURAL")
    result = publish_primary_profit_rankings(
        tmp_path,
        D,
        generation_mode="NATURAL",
        single_scorer=_single_stub,
        mixed_scorer=_mixed_stub,
    )
    mixed = json.loads(result["mixed"]["json"].read_text(encoding="utf-8"))
    assert mixed["status"] == "PROSPECTIVE_RESEARCH"
    assert mixed["prospective"] is True
    assert mixed["retrospective_non_forward"] is False
    assert mixed["boundaries"]["forward_selection_created"] is False
    assert mixed["boundaries"]["forward_statistics_updated"] is False
    assert not (tmp_path / "data/decision_executable_profit/forward").exists()


def test_shared_bundle_validator_accepts_both_modes_and_fails_closed_on_index_drift(
    tmp_path: Path,
) -> None:
    for mode in ("RETROSPECTIVE_RECOVERY", "NATURAL"):
        root = tmp_path / mode.lower()
        _write_primary_fixture(root, mode=mode)
        result = publish_primary_profit_rankings(
            root,
            D,
            generation_mode=mode,
            single_scorer=_single_stub,
            mixed_scorer=_mixed_stub,
        )
        bundle = validate_primary_profit_bundle(
            root,
            expected_signal_date=D,
            expected_generation_mode=mode,
        )
        assert bundle["inputs"].generation_mode == mode
        assert bundle["mixed"]["projection_json_sha256"] == _sha256(
            result["mixed"]["json"]
        )

        mixed_index = result["mixed"]["index"]
        tampered = json.loads(mixed_index.read_text(encoding="utf-8"))
        tampered["unexpected"] = True
        mixed_index.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(PrimaryProfitRankingError, match="index surface drifted"):
            validate_primary_profit_bundle(root)


def test_p1_fails_closed_on_runtime_sha_or_membership_drift(tmp_path: Path) -> None:
    runtime, _rows = _write_primary_fixture(tmp_path)
    runtime.write_text(runtime.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(PrimaryProfitRankingError, match="runtime SHA drifted"):
        publish_primary_profit_rankings(
            tmp_path,
            D,
            generation_mode="RETROSPECTIVE_RECOVERY",
            single_scorer=_single_stub,
            mixed_scorer=_mixed_stub,
        )


def test_p1_dated_outputs_are_immutable_and_pointer_cannot_rewrite_same_d(
    tmp_path: Path,
) -> None:
    _write_primary_fixture(tmp_path)
    publish_primary_profit_rankings(
        tmp_path,
        D,
        generation_mode="RETROSPECTIVE_RECOVERY",
        single_scorer=_single_stub,
        mixed_scorer=_mixed_stub,
    )

    def changed(inputs):
        return _single_stub(inputs, bump=0.001)

    with pytest.raises(PrimaryProfitRankingError, match="immutable P1 artifact rewrite"):
        publish_primary_profit_rankings(
            tmp_path,
            D,
            generation_mode="RETROSPECTIVE_RECOVERY",
            single_scorer=changed,
            mixed_scorer=_mixed_stub,
        )


def test_p1_source_and_workflow_never_use_action_or_forward_writer_inputs() -> None:
    script = (ROOT / "scripts/publish_primary_profit_rankings.py").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github/workflows/run_primary_profit_rankings.yml").read_text(
        encoding="utf-8"
    )
    assert "primary_d_runtime_features_" in script
    assert "three_rank_top10_" in script
    assert "primary_d_receipt_" in script
    assert "outputs/auction_v3/predictions" not in script
    assert "action_plan_latest" not in script
    assert "data/decision_executable_profit/forward/selections" not in script
    assert "data/decision_executable_profit/forward/statistics" not in script
    header = workflow.split("\npermissions:", 1)[0]
    assert "schedule:" not in header and "cron:" not in header
    assert "'decision-auction-main-writer'" in workflow
    assert "dc20-p1-ignored-{0}" in workflow
    assert "workflow_run:" in workflow
    assert "DC2.0 · Publish Primary D List (P0)" in workflow
    assert "github.event.workflow_run.status == 'completed'" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'schedule'" in workflow
    assert "github.event.workflow_run.run_attempt == 1" in workflow
    assert "github.event.workflow_run.workflow_id == 343703608" in workflow
    assert "github.event.workflow_run.path == '.github/workflows/run_primary_d_daily.yml'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.repository.full_name == github.repository" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "Verify checked-out main is current GitHub API main" in workflow
    assert "f'https://api.github.com/repos/{repository}/commits/main'" in workflow
    assert "P1 checkout main differs from GitHub API commits/main" in workflow
    assert "UPSTREAM_CREATED_AT" in workflow
    assert "UPSTREAM_REPOSITORY" in workflow
    assert "UPSTREAM_RUN_ATTEMPT" in workflow
    assert "str(run.get('run_attempt') or '') == expected['run_attempt']" in workflow
    assert "str((run.get('repository') or {}).get('full_name') or '') == expected['repository']" in workflow
    assert "str((run.get('head_repository') or {}).get('full_name') or '') == expected['head_repository']" in workflow
    assert "P1 upstream P0 API identity drifted" in workflow
    assert "receipt_mode" in workflow
    assert "[dc20-p1-pages-owned]" in workflow
    assert "RETROSPECTIVE_RECOVERY" in workflow
    assert "primary_d_runtime_features_${signal_date}.csv" in workflow
    assert "outputs/decision/action_plan_*.json" in workflow


def test_p1_public_acceptance_revalidates_bytes_and_executes_dynamic_dom() -> None:
    workflow = (ROOT / ".github/workflows/run_primary_profit_rankings.yml").read_text(
        encoding="utf-8"
    )
    public = workflow.split(
        "- name: Verify exact public P1 revision and complete SHA-bound bundle", 1
    )[1].split("- name: Execute public dashboard and verify rendered P1 DOM", 1)[0]
    dom = workflow.split(
        "- name: Execute public dashboard and verify rendered P1 DOM", 1
    )[1]

    for token in (
        "models/decision_primary_profit_research_contract.json",
        'primary_d_receipt_${SIGNAL_DATE}.json',
        'primary_d_runtime_features_${SIGNAL_DATE}.csv',
        'three_rank_top10_${SIGNAL_DATE}.json',
        'three_rank_top10_${SIGNAL_DATE}.csv',
        "outputs/decision/legacy_profit_relative_research/index.json",
        'legacy_profit_relative_research/projection_${SIGNAL_DATE}.json',
        'legacy_profit_relative_research/projection_${SIGNAL_DATE}.csv',
        "outputs/decision/executable_profit_research/index.json",
        'executable_profit_research/projection_${SIGNAL_DATE}.json',
        'executable_profit_research/projection_${SIGNAL_DATE}.csv',
        "validate_primary_profit_bundle",
        "expected_signal_date=signal_date",
        "expected_generation_mode=mode",
        "primary_single_profit_projection_sha256",
        "primary_mixed_profit_projection_sha256",
    ):
        assert token in public
    shared = (ROOT / "scripts/publish_primary_profit_rankings.py").read_text(
        encoding="utf-8"
    )
    for token in (
        "PRIMARY_INDEX_KEYS",
        "inputs = load_primary_inputs(root, signal_date, generation_mode)",
        'index["latest_projection_json_sha256"] == _sha256(json_path)',
        'index["latest_projection_csv_sha256"] == _sha256(csv_path)',
        '== projection["source_bindings"]',
        "== inputs.source_bindings",
        "csv_path.read_bytes() == _csv_bytes(projection, row_fields)",
        'single_projection["source_bindings"]',
        '== mixed_projection["source_bindings"]',
    ):
        assert token in shared
    assert "curl -fsSL" in public
    assert " >/dev/null" not in public

    for token in (
        "command -v google-chrome",
        "--headless=new",
        "--virtual-time-budget=20000",
        "--dump-dom",
        "RenderedDashboardParser",
        "data-three-rank-sort",
        "legacy_profit_relative_rank",
        "status == '晋级榜、单一盈利与混合盈利排序已生成'",
        "parser.single_buttons == 1",
        "parser.stage_rows == expected_n",
        "parser.mixed_rows == expected_n",
        "'单一盈利排序' in parser.stage_headers",
        "'混合盈利排序' in parser.mixed_headers",
        "f'真实候选 {expected_n} 支' in mixed_state",
    ):
        assert token in dom
    assert "grep -F" not in dom

    public_python = public.split(
        "PUBLIC_ROOT=\"${public_root}\" python - <<'PY'\n", 1
    )[1].split("\n          PY", 1)[0]
    dom_python = dom.split(
        "DOM_PATH=\"${dom}\" PUBLIC_ROOT=\"${RUNNER_TEMP}/public-p1\" python - <<'PY'\n",
        1,
    )[1].split("\n          PY", 1)[0]
    compile(textwrap.dedent(public_python), "<p1-public-acceptance>", "exec")
    compile(textwrap.dedent(dom_python), "<p1-dom-acceptance>", "exec")


def test_p1_public_acceptance_embedded_scripts_run_against_real_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_primary_fixture(tmp_path)
    result = publish_primary_profit_rankings(
        tmp_path,
        D,
        generation_mode="RETROSPECTIVE_RECOVERY",
        single_scorer=_single_stub,
        mixed_scorer=_mixed_stub,
    )
    expected_head = "a" * 40
    revision = {
        "schema_version": "decision_pages_revision_v5_primary_profit",
        "head_sha": expected_head,
        "signal_date": D,
        "primary_profit_status": result["status"],
        "primary_profit_generation_mode": result["generation_mode"],
        "primary_profit_candidate_count": result["candidate_count"],
        "primary_profit_top10_members_sha256": result["top10_members_sha256"],
        "primary_single_profit_projection_sha256": hashlib.sha256(
            result["single"]["json"].read_bytes()
        ).hexdigest(),
        "primary_mixed_profit_projection_sha256": hashlib.sha256(
            result["mixed"]["json"].read_bytes()
        ).hexdigest(),
    }
    (tmp_path / "revision.json").write_text(
        json.dumps(revision), encoding="utf-8"
    )

    workflow = (ROOT / ".github/workflows/run_primary_profit_rankings.yml").read_text(
        encoding="utf-8"
    )
    public = workflow.split(
        "- name: Verify exact public P1 revision and complete SHA-bound bundle", 1
    )[1].split("- name: Execute public dashboard and verify rendered P1 DOM", 1)[0]
    public_python = textwrap.dedent(
        public.split("PUBLIC_ROOT=\"${public_root}\" python - <<'PY'\n", 1)[1].split(
            "\n          PY", 1
        )[0]
    )
    monkeypatch.setenv("PUBLIC_ROOT", str(tmp_path))
    monkeypatch.setenv("SIGNAL_DATE", D)
    monkeypatch.setenv("GENERATION_MODE", "RETROSPECTIVE_RECOVERY")
    monkeypatch.setenv("EXPECTED_HEAD", expected_head)
    exec(compile(public_python, "<p1-public-fixture>", "exec"), {})

    n = int(result["candidate_count"])
    stage_rows = "".join("<tr><td>row</td></tr>" for _ in range(n))
    mixed_rows = "".join("<tr><td>row</td></tr>" for _ in range(n))
    rendered = f"""<!doctype html><html><body>
    <h2 id="statusTitle">晋级榜、单一盈利与混合盈利排序已生成</h2>
    <section id="stagePanel"><span id="stageSignalDate">D：2026-08-26</span><span id="stageCount">{n}</span>
      <div id="stageContent"><button data-three-rank-sort="legacy_profit_relative_rank">单一盈利排序</button>
        <table><thead><tr><th>单一盈利排序</th><th>模型分值</th></tr></thead>
        <tbody data-three-rank-body>{stage_rows}</tbody></table></div></section>
    <section id="executableProfitResearchPanel"><span id="executableProfitResearchState">历史恢复 · 非前向研究 · 真实候选 {n} 支 · 不足10不补票</span>
      <div id="executableProfitResearchContent"><table class="executable-profit-table">
        <thead><tr><th>混合盈利排序</th></tr></thead><tbody>{mixed_rows}</tbody>
      </table></div></section></body></html>"""
    dom_path = tmp_path / "rendered.html"
    dom_path.write_text(rendered, encoding="utf-8")
    dom = workflow.split(
        "- name: Execute public dashboard and verify rendered P1 DOM", 1
    )[1]
    dom_python = textwrap.dedent(
        dom.split(
            "DOM_PATH=\"${dom}\" PUBLIC_ROOT=\"${RUNNER_TEMP}/public-p1\" python - <<'PY'\n",
            1,
        )[1].split("\n          PY", 1)[0]
    )
    monkeypatch.setenv("DOM_PATH", str(dom_path))
    exec(compile(dom_python, "<p1-dom-fixture>", "exec"), {})

    result["mixed"]["csv"].write_bytes(result["mixed"]["csv"].read_bytes() + b"\n")
    with pytest.raises(SystemExit, match="index/projection/CSV binding failed"):
        exec(compile(public_python, "<p1-public-tampered>", "exec"), {})
