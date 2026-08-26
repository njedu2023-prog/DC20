from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.research_context import (  # noqa: E402
    ResearchContextError,
    git_blob_sha,
    project_research_context,
    publish_research_context,
    publish_vendored_research_context,
    validate_historical_parity_context,
    validate_research_context,
)
from top10decision.decision import research_context as research_context_module  # noqa: E402
from top10decision.decision.three_rank import (  # noqa: E402
    THREE_RANK_CONTRACT_VERSION,
    build_three_rank_contract,
)
from scripts.decision_pages_truth import (  # noqa: E402
    project_report_index_action_truth,
)


def _plan() -> dict[str, object]:
    row = {
        "action": "BUY",
        "target_weight": 0.5,
        "trade_selected": 1,
        "market_order_allowed": False,
        "recommended_max_price": 12.34,
        "observation_max_price": 12.34,
        "ts_code": "000001.SZ",
        "name": "测试股票",
        "stage_transition": "2→3",
    }
    return {
        "schema_version": "decision_action_plan_v12_top10_trade_selector",
        "generated_at_utc": "2026-08-21T13:58:02+00:00",
        "report_date": "20260824",
        "signal_date": "20260821",
        "exec_date": "20260824",
        "exit_date": "20260825",
        "status_code": "ACTIONABLE_BUY",
        "status_label": "允许人工操作参考",
        "formal_buy_count": 1,
        "broker_connected": False,
        "execution_or_fill_claimed": False,
        "model": {
            "prediction_matches_report": True,
            "promoted": True,
        },
        "market_sentiment": {"score": 0.468, "limit_up_count": 50},
        "observation_statistics": {"observation_dates": 25},
        "execution_contract": {"guidance_only": True},
        "candidates": [copy.deepcopy(row)],
        "stage_watchlist": [{**row, "watch_label": "正式建议"}],
    }


def _sources() -> dict[str, str]:
    return {
        "outputs/decision/eval_20260824.json": "a" * 64,
        "outputs/auction_v3/predictions/pred_latest.csv": "b" * 64,
    }


def _independent_plan() -> dict[str, object]:
    plan = _plan()
    row = copy.deepcopy(plan["candidates"][0])
    row.update(
        {
            "three_rank_contract_version": THREE_RANK_CONTRACT_VERSION,
            "top10_selected": 1,
            "promotion_pool_size": 1,
            "feature_snapshot_sha256": "f" * 64,
            "promotion_rank": 1,
            "predicted_promotion_probability": 0.81,
            "big_loss_safety_rank": 1,
            "predicted_big_loss_probability": 0.08,
            "profit_rank": 1,
            "predicted_profit_probability": 0.72,
        }
    )
    for index, head in enumerate(("promotion", "big_loss", "profit"), 1):
        row.update(
            {
                f"{head}_model_status": "READY",
                f"{head}_model_version": f"{head}_v1",
                f"{head}_model_as_of_date": "20260820",
                f"{head}_model_artifact_sha256": str(index) * 64,
            }
        )
    plan["candidates"] = [row]
    plan["stage_watchlist"] = [{**row, "watch_label": "正式建议"}]
    plan["model"] = {
        **plan["model"],
        # A valid independent D-close contract remains research truth even
        # when the legacy report/eval date match is unavailable.
        "prediction_matches_report": False,
    }
    plan["three_rank"] = build_three_rank_contract(plan)
    return plan


def _legacy_artifacts(plan: dict[str, object]) -> tuple[bytes, bytes, bytes]:
    action_raw = (
        json.dumps(plan, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    report_raw = (
        "# Decision Report (20260824)\n\n"
        "- signal_date: **20260821**\n"
        "- exec_date: **20260824**\n"
        "- exit_date: **20260825**\n\n"
        "## Legacy-only report\n\nold exact markdown\n"
    ).encode("utf-8")
    evaluation_raw = (
        json.dumps(
            {
                "signal_date": "20260821",
                "exec_date": "20260824",
                "exit_date": "20260825",
                "legacy_metric": 0.1234567890123,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return action_raw, report_raw, evaluation_raw


def test_research_projection_keeps_full_fields_but_removes_every_action() -> None:
    context = project_research_context(_plan(), source_files=_sources())

    assert context["schema_version"] == "decision_research_context_v1_daily"
    assert context["artifact_kind"] == "daily_research_context"
    assert context["research_only"] is True
    assert context["action_authorized"] is False
    assert context["formal_buy_count"] == 0
    assert context["market_sentiment"] == {"score": 0.468, "limit_up_count": 50}
    assert context["observation_statistics"] == {"observation_dates": 25}
    assert context["candidates"][0]["recommended_max_price"] == 12.34
    assert context["candidates"][0]["action"] == "WATCH"
    assert context["candidates"][0]["target_weight"] == 0.0
    assert context["candidates"][0]["trade_selected"] == 0
    assert context["stage_watchlist"][0]["watch_label"] == "仅观察"
    assert context["model"]["promoted"] is True
    assert context["model"]["action_authorized"] is False
    validate_research_context(context, expected_report_date="20260824")


def test_independent_cutover_accepts_only_complete_a_contract() -> None:
    context = project_research_context(
        _independent_plan(),
        source_files=_sources(),
    )

    assert context["independent_dc20_context"] is True
    assert context["independence_cutover_signal_date"] == "20260821"
    assert context["active_evidence_scope"] == (
        "dc20_owned_dated_three_rank_bundle_only"
    )
    assert context["historical_parity"] is False
    assert context["three_rank"]["models"]["promotion"]["status"] == "READY"
    assert context["model"]["prediction_matches_report"] is False
    assert all(row["action"] == "WATCH" for row in context["candidates"])
    with pytest.raises(ResearchContextError, match="cutover-bound"):
        validate_research_context(context, expected_report_date="20260824")
    validate_research_context(
        context,
        expected_report_date="20260824",
        require_independent_downloads=False,
    )

    invalid = copy.deepcopy(context)
    invalid["three_rank"]["models"]["promotion"]["status"] = (
        "NOT_READY_INVALID_MEMBERSHIP"
    )
    with pytest.raises(ResearchContextError, match="cutover-bound"):
        validate_research_context(
            invalid,
            expected_report_date="20260824",
            require_independent_downloads=False,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("action_authorized", True, "cannot authorize action"),
        ("formal_buy_count", 1, "formal_buy_count"),
        ("broker_connected", True, "cannot connect a broker"),
        ("execution_or_fill_claimed", True, "cannot claim execution or fill"),
    ],
)
def test_research_projection_fails_closed_on_root_action_claims(
    field: str,
    value: object,
    message: str,
) -> None:
    context = project_research_context(_plan(), source_files=_sources())
    context[field] = value
    with pytest.raises(ResearchContextError, match=message):
        validate_research_context(context)


def test_research_projection_rejects_row_action_and_external_source() -> None:
    context = project_research_context(_plan(), source_files=_sources())
    context["candidates"][0]["action"] = "BUY"
    with pytest.raises(ResearchContextError, match="non-WATCH"):
        validate_research_context(context)

    context = project_research_context(_plan(), source_files=_sources())
    context["source_binding"]["files"] = {
        "https://njedu2023-prog.github.io/top10-decision/action.json": "c" * 64
    }
    with pytest.raises(ResearchContextError, match="repository-local"):
        validate_research_context(context)


def test_dashboard_loads_research_context_without_weakening_action_truth() -> None:
    text = (ROOT / "decision.html").read_text(encoding="utf-8")
    assert "decision_research_context_v1_daily" in text
    assert "decision_research_context_v1_historical_parity" in text
    assert "daily_research_context" in text
    assert "validatedResearchContext(info, context)" in text
    assert "info.research_available === true" in text
    assert "researchResult.status !== \"fulfilled\"" in text
    assert "同日完整研究上下文合同无效" in text
    assert 'info.action_available === true && info.action_url' in text
    assert "同日行动计划读取失败，不能降级成尚未生成" in text
    assert "decodeHistoricalParityContext(info, wrapper)" in text
    assert "历史原始数值逐字节复现" in text
    assert "payloads_base64" in text
    assert "historical_report_md" in text
    assert "historical_evaluation" in text
    assert "本页不缓存该历史载荷" in text
    assert "plan?.historical_parity === true || plan?.schema_version" in text
    assert "cachedPlan?.schema_version === \"decision_research_context_v1_historical_parity\"" in text
    assert "本页已验证来源 commit" not in text
    assert "validatedActionPlan(info, plan)" in text
    assert 'startsWith("decision_action_plan_v")' in text
    assert "if (actionExpected && planResult.status !== \"fulfilled\")" in text
    assert "if (researchPlan.historical_parity === true)" in text
    assert "plan = researchPlan" in text
    assert "els.researchPanel.hidden = fullContext" in text
    assert "top10-decision" not in text


def test_daily_workflow_builds_research_in_isolation_and_preserves_action() -> None:
    text = (ROOT / ".github/workflows/run_decision_daily.yml").read_text(
        encoding="utf-8"
    )
    assert 'research_root="${RUNNER_TEMP}/dc20-daily-research-root"' in text
    assert "timeout-minutes: 120" in text
    assert "scripts/run_deterministic_numeric.py" in text
    assert "scripts/run_auction_v3.py" in text
    assert "scripts/publish_decision_research_context.py" in text
    assert '--source-root "${research_root}"' in text
    assert '--output-root "${research_root}"' in text
    assert (
        'research_path="${research_root}/outputs/decision/'
        'research_context_dc20_${report_date}.json"'
    ) in text
    assert "'isolated research root'" in text
    assert "Daily research generation modified the preserved " in text
    assert "Auction action plan in {label}" in text
    assert "outputs/decision/action_plan_*.json" in text
    assert "outputs/auction_v3/predictions/pred_20??????.csv" in text
    assert (
        "expected_prediction=f'outputs/auction_v3/predictions/"
        "pred_{signal_date}.csv'"
    ) in text
    assert "outputs/auction_v3/**" not in text
    assert "outputs/decision/research_context_20??????.json" in text
    assert "DECISION_RUN_RECEIPT_PATH" in text
    assert "validate_decision_run_receipt" in text
    assert "Daily preserved historical parity research context" in text
    assert "sorted(Path('outputs/decision').glob('eval_20??????.json')" not in text


def test_research_context_json_is_strictly_serializable() -> None:
    context = project_research_context(_plan(), source_files=_sources())
    encoded = json.dumps(context, ensure_ascii=False, allow_nan=False)
    assert json.loads(encoded)["action_authorized"] is False


def test_pages_projection_advertises_only_valid_same_date_research(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "report_index.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "schema_version": "decision_report_index_v2_action_truth",
                "generated_at_utc": "2026-08-21T14:00:00+00:00",
                "latest_report_date": "20260824",
                "latest_report_file": "decision_report_20260824.md",
                "latest_action_report_date": "",
                "latest_action_url": "",
                "reports": [],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "_site" / "outputs" / "decision"
    output.mkdir(parents=True)
    (output / "decision_report_20260824.md").write_text(
        "# Decision Report (20260824)\n\n"
        "- signal_date: **20260821**\n"
        "- exec_date: **20260824**\n",
        encoding="utf-8",
    )
    (output / "eval_20260824.json").write_text(
        json.dumps({"signal_date": "20260821", "exec_date": "20260824"}),
        encoding="utf-8",
    )
    context = project_research_context(_plan(), source_files=_sources())
    (output / "research_context_20260824.json").write_text(
        json.dumps(context, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )

    truth = project_report_index_action_truth(
        source_report_index_path=source,
        site_root=tmp_path / "_site",
    )
    index = json.loads((output / "report_index.json").read_text(encoding="utf-8"))

    assert index["reports"][0]["action_available"] is False
    assert index["reports"][0]["research_available"] is True
    assert index["reports"][0]["research_url"] == (
        "outputs/decision/research_context_20260824.json"
    )
    assert truth.action_dates == ()
    assert truth.research_dates == ("20260824",)


def test_one_time_vendored_history_preserves_numeric_display_fields(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan["backtest"] = {
        "win_rate": 0.625,
        "cumulative_return": 0.123456789,
        "max_drawdown": -0.071234567,
    }
    plan["observation_statistics"] = {
        "observation_dates": 25,
        "final_win_rate": 0.571428571428,
        "equal_slot_cumulative_return": 0.082345678901,
    }
    raw = (json.dumps(plan, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    source = tmp_path / "legacy-action.json"
    source.write_bytes(raw)
    content_sha256 = hashlib.sha256(raw).hexdigest()
    blob_sha = git_blob_sha(raw)

    path, context = publish_vendored_research_context(
        source,
        tmp_path / "dc20",
        repository="njedu2023-prog/top10-decision",
        commit_sha="1" * 40,
        source_path="outputs/decision/action_plan_20260824.json",
        blob_sha=blob_sha,
        raw_sha256=content_sha256,
    )

    assert path.name == "research_context_20260824.json"
    assert context["schema_version"] == (
        "decision_research_context_v1_historical_parity"
    )
    decoded, decoded_plan = validate_historical_parity_context(
        context,
        expected_report_date="20260824",
    )
    assert decoded == raw
    assert base64.b64decode(context["payload_base64"], validate=True) == raw
    assert decoded_plan["backtest"] == plan["backtest"]
    assert decoded_plan["market_sentiment"] == plan["market_sentiment"]
    assert decoded_plan["observation_statistics"] == plan["observation_statistics"]
    assert decoded_plan["candidates"][0]["recommended_max_price"] == (
        plan["candidates"][0]["recommended_max_price"]
    )
    assert context["source_binding"] == {
        "scope": "vendored_immutable_legacy_snapshot",
        "repository": "njedu2023-prog/top10-decision",
        "commit_sha": "1" * 40,
        "path": "outputs/decision/action_plan_20260824.json",
        "blob_sha": blob_sha,
        "raw_sha256": content_sha256,
        "import_mode": "one_time_vendored_snapshot",
        "runtime_network_dependency": False,
    }
    assert context["action_authorized"] is False
    assert context["historical_parity"] is True
    assert decoded_plan["candidates"][0]["action"] == "BUY"


def test_three_vendored_history_artifacts_are_all_preserved_bit_exact(
    tmp_path: Path,
) -> None:
    action_raw, report_raw, evaluation_raw = _legacy_artifacts(_plan())
    action_path = tmp_path / "legacy-action.json"
    report_path = tmp_path / "legacy-report.md"
    evaluation_path = tmp_path / "legacy-eval.json"
    action_path.write_bytes(action_raw)
    report_path.write_bytes(report_raw)
    evaluation_path.write_bytes(evaluation_raw)

    _path, context = publish_vendored_research_context(
        action_path,
        tmp_path / "dc20",
        repository="njedu2023-prog/top10-decision",
        commit_sha="1" * 40,
        source_path="outputs/decision/action_plan_20260824.json",
        blob_sha=git_blob_sha(action_raw),
        raw_sha256=hashlib.sha256(action_raw).hexdigest(),
        report_input_path=report_path,
        report_source_path="outputs/decision/decision_report_20260824.md",
        report_blob_sha=git_blob_sha(report_raw),
        report_raw_sha256=hashlib.sha256(report_raw).hexdigest(),
        evaluation_input_path=evaluation_path,
        evaluation_source_path="outputs/decision/eval_20260824.json",
        evaluation_blob_sha=git_blob_sha(evaluation_raw),
        evaluation_raw_sha256=hashlib.sha256(evaluation_raw).hexdigest(),
    )

    decoded_action, decoded_plan = validate_historical_parity_context(
        context,
        expected_report_date="20260824",
    )
    assert decoded_action == action_raw
    assert decoded_plan == _plan()
    assert {
        key: base64.b64decode(value, validate=True)
        for key, value in context["payloads_base64"].items()
    } == {
        "action_plan": action_raw,
        "decision_report": report_raw,
        "evaluation": evaluation_raw,
    }
    assert context["source_binding"]["artifacts"] == {
        "action_plan": {
            "path": "outputs/decision/action_plan_20260824.json",
            "blob_sha": git_blob_sha(action_raw),
            "raw_sha256": hashlib.sha256(action_raw).hexdigest(),
        },
        "decision_report": {
            "path": "outputs/decision/decision_report_20260824.md",
            "blob_sha": git_blob_sha(report_raw),
            "raw_sha256": hashlib.sha256(report_raw).hexdigest(),
        },
        "evaluation": {
            "path": "outputs/decision/eval_20260824.json",
            "blob_sha": git_blob_sha(evaluation_raw),
            "raw_sha256": hashlib.sha256(evaluation_raw).hexdigest(),
        },
    }


def test_daily_publisher_preserves_existing_valid_historical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_raw = json.dumps(_plan(), ensure_ascii=False).encode("utf-8")
    source = tmp_path / "legacy-action.json"
    source.write_bytes(action_raw)
    target, _context = publish_vendored_research_context(
        source,
        tmp_path,
        repository="njedu2023-prog/top10-decision",
        commit_sha="1" * 40,
        source_path="outputs/decision/action_plan_20260824.json",
        blob_sha=git_blob_sha(action_raw),
        raw_sha256=hashlib.sha256(action_raw).hexdigest(),
    )
    original_bytes = target.read_bytes()
    daily = project_research_context(_plan(), source_files=_sources())
    monkeypatch.setattr(
        research_context_module,
        "build_research_context",
        lambda _root, _date="": daily,
    )

    path, preserved = publish_research_context(
        tmp_path / "isolated-source",
        tmp_path,
        "20260824",
    )

    assert path == target
    assert target.read_bytes() == original_bytes
    assert preserved["schema_version"] == (
        "decision_research_context_v1_historical_parity"
    )


def test_cutover_publisher_keeps_history_archive_and_writes_dc20_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_raw = json.dumps(_plan(), ensure_ascii=False).encode("utf-8")
    legacy_source = tmp_path / "legacy-action.json"
    legacy_source.write_bytes(action_raw)
    historical_path, _historical = publish_vendored_research_context(
        legacy_source,
        tmp_path,
        repository="njedu2023-prog/top10-decision",
        commit_sha="1" * 40,
        source_path="outputs/decision/action_plan_20260824.json",
        blob_sha=git_blob_sha(action_raw),
        raw_sha256=hashlib.sha256(action_raw).hexdigest(),
    )
    historical_bytes = historical_path.read_bytes()
    independent = project_research_context(
        _independent_plan(),
        source_files=_sources(),
    )
    monkeypatch.setattr(
        research_context_module,
        "build_research_context",
        lambda _root, _date="": copy.deepcopy(independent),
    )

    path, context = publish_research_context(
        tmp_path / "isolated-dc20",
        tmp_path,
        "20260824",
    )

    assert path.name == "research_context_dc20_20260824.json"
    assert historical_path.read_bytes() == historical_bytes
    assert context["independent_dc20_context"] is True
    assert context["three_rank"]["downloads"]["json_url"] == (
        "outputs/decision/three_rank_top10_20260821.json"
    )
    assert (tmp_path / "outputs/decision/three_rank_index.json").is_file()
    validate_research_context(context, expected_report_date="20260824")

    changed_plan = _independent_plan()
    changed_plan["candidates"][0]["predicted_promotion_probability"] = 0.79
    changed_plan["stage_watchlist"] = [
        {**changed_plan["candidates"][0], "watch_label": "正式建议"}
    ]
    changed_plan["three_rank"] = build_three_rank_contract(changed_plan)
    changed = project_research_context(changed_plan, source_files=_sources())
    monkeypatch.setattr(
        research_context_module,
        "build_research_context",
        lambda _root, _date="": copy.deepcopy(changed),
    )
    with pytest.raises(ValueError, match="cannot be overwritten"):
        publish_research_context(
            tmp_path / "isolated-dc20",
            tmp_path,
            "20260824",
        )


def test_daily_isolated_publisher_materializes_step17_handoff_in_one_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    independent = project_research_context(
        _independent_plan(),
        source_files=_sources(),
    )
    monkeypatch.setattr(
        research_context_module,
        "build_research_context",
        lambda _root, _date="": copy.deepcopy(independent),
    )
    research_root = tmp_path / "daily-research-root"
    workspace_root = tmp_path / "workspace-root"

    context_path, context = publish_research_context(
        research_root,
        research_root,
        "20260824",
    )

    decision_root = research_root / "outputs/decision"
    json_path = decision_root / "three_rank_top10_20260821.json"
    csv_path = decision_root / "three_rank_top10_20260821.csv"
    index_path = decision_root / "three_rank_index.json"
    assert context_path == decision_root / "research_context_dc20_20260824.json"
    for path in (context_path, json_path, csv_path, index_path):
        assert path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    assert context["three_rank"] == json.loads(json_path.read_text(encoding="utf-8"))
    assert context["source_binding"]["files"] == {
        "outputs/decision/three_rank_top10_20260821.json": hashlib.sha256(
            json_path.read_bytes()
        ).hexdigest(),
        "outputs/decision/three_rank_top10_20260821.csv": hashlib.sha256(
            csv_path.read_bytes()
        ).hexdigest(),
    }
    assert not (workspace_root / "outputs/decision").exists()


def test_vendored_history_dated_file_is_write_once(tmp_path: Path) -> None:
    first_raw = json.dumps(_plan(), ensure_ascii=False).encode("utf-8")
    first = tmp_path / "legacy-first.json"
    first.write_bytes(first_raw)
    target, _ = publish_vendored_research_context(
        first,
        tmp_path,
        repository="njedu2023-prog/top10-decision",
        commit_sha="1" * 40,
        source_path="outputs/decision/action_plan_20260824.json",
        blob_sha=git_blob_sha(first_raw),
        raw_sha256=hashlib.sha256(first_raw).hexdigest(),
    )
    original = target.read_bytes()

    changed_plan = _plan()
    changed_plan["status_label"] = "不同历史内容"
    changed_raw = json.dumps(changed_plan, ensure_ascii=False).encode("utf-8")
    changed = tmp_path / "legacy-changed.json"
    changed.write_bytes(changed_raw)
    with pytest.raises(ResearchContextError, match="cannot be overwritten"):
        publish_vendored_research_context(
            changed,
            tmp_path,
            repository="njedu2023-prog/top10-decision",
            commit_sha="2" * 40,
            source_path="outputs/decision/action_plan_20260824.json",
            blob_sha=git_blob_sha(changed_raw),
            raw_sha256=hashlib.sha256(changed_raw).hexdigest(),
        )
    assert target.read_bytes() == original


def test_pages_prefers_independent_dc20_context_and_archives_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_index = tmp_path / "source" / "report_index.json"
    source_index.parent.mkdir(parents=True)
    source_index.write_text(
        json.dumps(
            {
                "schema_version": "decision_report_index_v2_action_truth",
                "generated_at_utc": "2026-08-21T14:00:00+00:00",
                "latest_report_date": "20260824",
                "latest_report_file": "decision_report_20260824.md",
                "latest_action_report_date": "",
                "latest_action_url": "",
                "reports": [],
            }
        ),
        encoding="utf-8",
    )
    site_root = tmp_path / "_site"
    output = site_root / "outputs" / "decision"
    output.mkdir(parents=True)
    action_raw, report_raw, evaluation_raw = _legacy_artifacts(_plan())
    (output / "decision_report_20260824.md").write_bytes(report_raw)
    (output / "eval_20260824.json").write_bytes(evaluation_raw)
    action_source = tmp_path / "legacy-action.json"
    report_source = tmp_path / "legacy-report.md"
    evaluation_source = tmp_path / "legacy-eval.json"
    action_source.write_bytes(action_raw)
    report_source.write_bytes(report_raw)
    evaluation_source.write_bytes(evaluation_raw)
    historical_path, _ = publish_vendored_research_context(
        action_source,
        site_root,
        repository="njedu2023-prog/top10-decision",
        commit_sha="1" * 40,
        source_path="outputs/decision/action_plan_20260824.json",
        blob_sha=git_blob_sha(action_raw),
        raw_sha256=hashlib.sha256(action_raw).hexdigest(),
        report_input_path=report_source,
        report_source_path="outputs/decision/decision_report_20260824.md",
        report_blob_sha=git_blob_sha(report_raw),
        report_raw_sha256=hashlib.sha256(report_raw).hexdigest(),
        evaluation_input_path=evaluation_source,
        evaluation_source_path="outputs/decision/eval_20260824.json",
        evaluation_blob_sha=git_blob_sha(evaluation_raw),
        evaluation_raw_sha256=hashlib.sha256(evaluation_raw).hexdigest(),
    )
    independent = project_research_context(
        _independent_plan(),
        source_files=_sources(),
    )
    monkeypatch.setattr(
        research_context_module,
        "build_research_context",
        lambda _root, _date="": copy.deepcopy(independent),
    )
    dc20_path, _ = publish_research_context(
        tmp_path / "isolated-dc20",
        site_root,
        "20260824",
    )

    truth = project_report_index_action_truth(
        source_report_index_path=source_index,
        site_root=site_root,
    )
    public_index = json.loads(
        (output / "report_index.json").read_text(encoding="utf-8")
    )
    row = public_index["reports"][0]

    assert truth.research_dates == ("20260824",)
    assert row["research_kind"] == "dc20_independent"
    assert row["research_url"] == (
        "outputs/decision/research_context_dc20_20260824.json"
    )
    assert row["research_archive_available"] is True
    assert row["research_archive_url"] == (
        "outputs/decision/research_context_20260824.json"
    )
    assert dc20_path.name == "research_context_dc20_20260824.json"
    assert historical_path.name == "research_context_20260824.json"


def test_vendored_history_rejects_wrong_blob_identity(tmp_path: Path) -> None:
    raw = json.dumps(_plan(), ensure_ascii=False).encode("utf-8")
    source = tmp_path / "legacy-action.json"
    source.write_bytes(raw)
    with pytest.raises(ResearchContextError, match="Git blob SHA"):
        publish_vendored_research_context(
            source,
            tmp_path / "dc20",
            repository="njedu2023-prog/top10-decision",
            commit_sha="1" * 40,
            source_path="outputs/decision/action_plan_20260824.json",
            blob_sha="2" * 40,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
        )


def test_pages_projection_accepts_bit_exact_historical_parity_wrapper(
    tmp_path: Path,
) -> None:
    source_index = tmp_path / "source" / "report_index.json"
    source_index.parent.mkdir(parents=True)
    source_index.write_text(
        json.dumps(
            {
                "schema_version": "decision_report_index_v2_action_truth",
                "generated_at_utc": "2026-08-21T14:00:00+00:00",
                "latest_report_date": "20260824",
                "latest_report_file": "decision_report_20260824.md",
                "latest_action_report_date": "",
                "latest_action_url": "",
                "reports": [],
            }
        ),
        encoding="utf-8",
    )
    site_root = tmp_path / "_site"
    output = site_root / "outputs" / "decision"
    output.mkdir(parents=True)
    (output / "decision_report_20260824.md").write_text(
        "# Decision Report (20260824)\n\n"
        "- signal_date: **20260821**\n"
        "- exec_date: **20260824**\n",
        encoding="utf-8",
    )
    (output / "eval_20260824.json").write_text(
        json.dumps({"signal_date": "20260821", "exec_date": "20260824"}),
        encoding="utf-8",
    )
    raw, legacy_report_raw, legacy_eval_raw = _legacy_artifacts(_plan())
    legacy = tmp_path / "legacy.json"
    legacy_report = tmp_path / "legacy.md"
    legacy_eval = tmp_path / "legacy-eval.json"
    legacy.write_bytes(raw)
    legacy_report.write_bytes(legacy_report_raw)
    legacy_eval.write_bytes(legacy_eval_raw)
    publish_vendored_research_context(
        legacy,
        site_root,
        repository="njedu2023-prog/top10-decision",
        commit_sha="1" * 40,
        source_path="outputs/decision/action_plan_20260824.json",
        blob_sha=git_blob_sha(raw),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        report_input_path=legacy_report,
        report_source_path="outputs/decision/decision_report_20260824.md",
        report_blob_sha=git_blob_sha(legacy_report_raw),
        report_raw_sha256=hashlib.sha256(legacy_report_raw).hexdigest(),
        evaluation_input_path=legacy_eval,
        evaluation_source_path="outputs/decision/eval_20260824.json",
        evaluation_blob_sha=git_blob_sha(legacy_eval_raw),
        evaluation_raw_sha256=hashlib.sha256(legacy_eval_raw).hexdigest(),
    )

    truth = project_report_index_action_truth(
        source_report_index_path=source_index,
        site_root=site_root,
    )
    public_index = json.loads((output / "report_index.json").read_text())

    assert truth.research_dates == ("20260824",)
    assert public_index["reports"][0]["research_available"] is True
