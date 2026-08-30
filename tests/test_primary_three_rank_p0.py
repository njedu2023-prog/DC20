from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import textwrap
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.publish_primary_three_rank import (
    HISTORY_CONTEXT_TABLES,
    PRIMARY_RUNTIME_INDEX_KEYS,
    PRIMARY_RUNTIME_CODE_PATHS,
    PROMOTION_PRIOR_SOURCE_PATHS,
    PrimaryDGenerationError,
    audit_complete_hard_pool,
    build_primary_d_runtime_index,
    materialize_primary_d_runtime_index,
    publish_primary_three_rank,
    validate_primary_d_runtime_index,
)
from top10decision.decision.three_engine_models import (
    ThreeEngineArtifactError,
    load_promotion_only_artifacts,
    load_three_engine_artifacts,
)
from top10decision.decision.three_rank import validate_three_rank_contract


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/run_primary_d_daily.yml"
FULL_DAILY_WORKFLOW = ROOT / ".github/workflows/run_decision_daily.yml"
SIGNAL_DATE = "20260826"
EXEC_DATE = "20260827"
EXIT_DATE = "20260828"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workflow_resolver_function(name: str):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    block = workflow.split(
        "- name: Resolve exact D and reject a completed duplicate", 1
    )[1]
    source = block.split("python - <<'PY'", 1)[1].split("\n          PY", 1)[0]
    tree = ast.parse(textwrap.dedent(source))
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    namespace = {
        "datetime": datetime,
        "time": time,
        "timedelta": timedelta,
        "timezone": timezone,
        "ZoneInfo": ZoneInfo,
        "re": re,
    }
    module = ast.Module(body=matches, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<p0-schedule-resolver>", "exec"), namespace)
    return namespace[name]


def _workflow_schedule_identities() -> dict[str, dict[str, object]]:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    block = workflow.split(
        "- name: Resolve exact D and reject a completed duplicate", 1
    )[1]
    source = block.split("python - <<'PY'", 1)[1].split("\n          PY", 1)[0]
    tree = ast.parse(textwrap.dedent(source))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "identities"
            for target in node.targets
        )
    ]
    assert len(matches) == 1
    expression = ast.Expression(body=matches[0].value)
    return eval(
        compile(ast.fix_missing_locations(expression), "<p0-schedule-identities>", "eval"),
        {"range": range},
    )


def _copy_model_runtime(target: Path, *, all_models: bool) -> Path:
    model_root = target / "models/decision_three_engines"
    model_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "models/decision_three_engines/validation_latest.json",
        model_root / "validation_latest.json",
    )
    names = ("promotion", "big_loss", "profit", "p_fill_shadow") if all_models else ("promotion",)
    for name in names:
        shutil.copy2(
            ROOT / f"models/decision_three_engines/{name}.joblib",
            model_root / f"{name}.joblib",
        )
    ledger_root = target / "data/decision_three_engines"
    ledger_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "five_year_supervised_ledger.csv.gz",
        "five_year_ledger_manifest.json",
    ):
        shutil.copy2(ROOT / f"data/decision_three_engines/{name}", ledger_root / name)
    return model_root / "validation_latest.json"


def _write_empty_exact_inputs(target: Path) -> None:
    calendar = target / "data/market/trade_cal_sse.csv"
    calendar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "data/market/trade_cal_sse.csv", calendar)

    archive = target / f"data/pred/archive/pred_source_{SIGNAL_DATE}.csv"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        "trade_date,verify_date,rank,ts_code,name,limit_times\n",
        encoding="utf-8",
    )
    archive_sha = _sha256(archive)
    meta = target / "data/pred/_pred_source_meta.json"
    meta.write_text(
        json.dumps(
            {
                "created_at_utc": "2026-08-26T13:15:00+00:00",
                "source_repository": "njedu2023-prog/a-top10",
                "resolved_commit": "a" * 40,
                "resolved_trade_date": SIGNAL_DATE,
                "sha256": archive_sha,
                "body_sha256": archive_sha,
                "csv_profile": {
                    "rows_sampled": 0,
                    "trade_date": SIGNAL_DATE,
                    "target_trade_date": EXEC_DATE,
                },
                "consistency": {
                    "archive_path": f"data/pred/archive/pred_source_{SIGNAL_DATE}.csv",
                    "target_trade_date": EXEC_DATE,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    market_root = target / f"data/market/raw/{SIGNAL_DATE[:4]}/{SIGNAL_DATE}"
    market_root.mkdir(parents=True, exist_ok=True)
    frames = {
        "daily": pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": SIGNAL_DATE,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "pre_close": 10.0,
                    "vol": 1.0,
                    "amount": 10.0,
                }
            ]
        ),
        "daily_basic": pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": SIGNAL_DATE,
                    "turnover_rate": 1.0,
                }
            ]
        ),
        "limit_list_d": pd.DataFrame(
            columns=("trade_date", "ts_code", "name", "limit_type", "limit_times")
        ),
        "stk_limit": pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": SIGNAL_DATE,
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
            ]
        ),
        "stock_basic": pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "测试股份",
                    "industry": "测试",
                    "list_date": "20000101",
                }
            ]
        ),
    }
    records = []
    for name, frame in frames.items():
        path = market_root / f"{name}.csv"
        frame.to_csv(path, index=False)
        date_scoped = name != "stock_basic"
        records.append(
            {
                "name": name,
                "success": True,
                "date_scoped": date_scoped,
                "source_trade_date": SIGNAL_DATE if date_scoped else None,
                "dated_path": (
                    f"data/market/raw/{SIGNAL_DATE[:4]}/{SIGNAL_DATE}/{name}.csv"
                ),
                "sha256": _sha256(path),
            }
        )
    (market_root / "_sync_meta.json").write_text(
        json.dumps(
            {
                "requested_trade_date": SIGNAL_DATE,
                "resolved_trade_date": SIGNAL_DATE,
                "strict_dated_source": True,
                "source_repo": {
                    "owner": "njedu2023-prog",
                    "repo": "a-share-top3-data",
                    "resolved_commit": "b" * 40,
                },
                "files": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _commit_primary_runtime_closure(target: Path) -> None:
    calendar = pd.read_csv(ROOT / "data/market/trade_cal_sse.csv", dtype=str)
    opened = calendar.loc[
        pd.to_numeric(calendar["is_open"], errors="coerce").eq(1), "cal_date"
    ].astype(str).tolist()
    position = opened.index(SIGNAL_DATE)
    history_dates = opened[position - 20 : position]
    assert len(history_dates) == 20
    for trade_date in history_dates:
        for table in HISTORY_CONTEXT_TABLES:
            relative = Path(
                f"data/market/raw/{trade_date[:4]}/{trade_date}/{table}.csv"
            )
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
    for relative_text in (*PRIMARY_RUNTIME_CODE_PATHS, *PROMOTION_PRIOR_SOURCE_PATHS):
        relative = Path(relative_text)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "-c",
            "user.name=P0 Test",
            "-c",
            "user.email=p0@example.invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        check=True,
    )


def test_secondary_artifact_corruption_does_not_block_promotion_loader(
    tmp_path: Path,
) -> None:
    validation = _copy_model_runtime(tmp_path, all_models=True)
    profit = tmp_path / "models/decision_three_engines/profit.joblib"
    profit.write_bytes(profit.read_bytes() + b"secondary-corruption")

    loaded = load_promotion_only_artifacts(validation, root=tmp_path)
    assert loaded.metadata["promotion"]["status"] == "READY"
    assert loaded.metadata["big_loss"]["status"] == "NOT_READY_PRIMARY_ONLY"
    assert loaded.metadata["profit"]["status"] == "NOT_READY_PRIMARY_ONLY"
    assert loaded.metadata["p_fill_shadow"]["status"] == (
        "SHADOW_NOT_READY_PRIMARY_ONLY"
    )
    with pytest.raises(ThreeEngineArtifactError, match="profit artifact hash mismatch"):
        load_three_engine_artifacts(validation, root=tmp_path)


def test_promotion_artifact_corruption_still_fails_closed(tmp_path: Path) -> None:
    validation = _copy_model_runtime(tmp_path, all_models=False)
    promotion = tmp_path / "models/decision_three_engines/promotion.joblib"
    promotion.write_bytes(promotion.read_bytes() + b"primary-corruption")
    with pytest.raises(
        ThreeEngineArtifactError,
        match="promotion artifact hash mismatch",
    ):
        load_promotion_only_artifacts(validation, root=tmp_path)


def test_primary_publisher_accepts_truthful_zero_candidate_day(tmp_path: Path) -> None:
    _copy_model_runtime(tmp_path, all_models=False)
    _write_empty_exact_inputs(tmp_path)
    _commit_primary_runtime_closure(tmp_path)

    result = publish_primary_three_rank(
        tmp_path,
        SIGNAL_DATE,
        generation_mode="RETROSPECTIVE_RECOVERY",
    )
    contract = result["contract"]
    validate_three_rank_contract(contract)
    assert contract["signal_date"] == SIGNAL_DATE
    assert contract["exec_date"] == EXEC_DATE
    assert contract["exit_date"] == EXIT_DATE
    assert contract["status"] == "PARTIAL_MODELS_NOT_READY"
    assert contract["models"]["promotion"]["status"] == "READY"
    assert contract["models"]["big_loss"]["status"] == "NOT_READY_PRIMARY_ONLY"
    assert contract["models"]["profit"]["status"] == "NOT_READY_PRIMARY_ONLY"
    assert contract["promotion_pool_size"] == 0
    assert contract["top10_count"] == 0
    assert contract["rows"] == []
    assert result["receipt"]["action_authorized"] is False
    assert result["receipt"]["forward_eligible"] is False
    assert result["receipt"]["not_forward_generated"] is True
    assert result["receipt"]["action_input_consumed"] is False
    assert result["receipt"]["formal_trade_count"] == 0
    assert result["receipt"]["shadow_forward_ledger_eligible"] is False
    assert result["receipt"]["future_market_data_consumed"] is False
    assert result["receipt"]["latest_fallback_used"] is False
    assert result["receipt"]["secondary_outputs_generated"] == {
        "action_plan": False,
        "big_loss": False,
        "profit": False,
        "p_fill_shadow": False,
        "executable_profit": False,
    }
    runtime_path = result["paths"]["runtime_features"]
    runtime = pd.read_csv(runtime_path, low_memory=False)
    assert runtime.empty
    assert {
        "signal_date",
        "ts_code",
        "name",
        "industry",
        "stage",
        "stage_transition",
        "board",
        "generated_at_utc",
        "identity",
        "feature_snapshot_sha256",
        "top10_selected",
        "promotion_rank",
        "predicted_promotion_probability",
    }.issubset(runtime.columns)
    outputs = result["receipt"]["outputs"]
    assert outputs["runtime_features_path"] == (
        f"outputs/decision/primary_d_runtime_features_{SIGNAL_DATE}.csv"
    )
    assert outputs["runtime_features_sha256"] == _sha256(runtime_path)
    assert outputs["runtime_feature_row_count"] == 0
    assert outputs["runtime_selected_count"] == 0
    assert len(outputs["runtime_identity_sha256"]) == 64
    assert result["receipt"]["inputs"]["committed_history_context"][
        "session_count"
    ] == 20
    assert result["receipt"]["pool_audit"]["hard_to_inference"][
        "all_hard_identities_preserved"
    ] is True
    runtime_index = result["runtime_index"]
    validate_primary_d_runtime_index(runtime_index)
    assert set(runtime_index) == PRIMARY_RUNTIME_INDEX_KEYS == {
        "schema_version",
        "index_kind",
        "data_alias",
        "latest_signal_date",
        "latest_exec_date",
        "latest_exit_date",
        "latest_receipt_url",
        "latest_receipt_sha256",
        "latest_runtime_features_url",
        "latest_runtime_features_sha256",
        "runtime_feature_row_count",
        "runtime_selected_count",
        "runtime_identity_sha256",
        "latest_feature_snapshot_sha256",
        "latest_top10_members_sha256",
        "latest_three_rank_json_url",
        "latest_three_rank_json_sha256",
        "latest_three_rank_csv_url",
        "latest_three_rank_csv_sha256",
        "latest_bundle_sha256",
    }
    assert runtime_index["schema_version"] == "dc20_primary_d_runtime_index_v1"
    assert runtime_index["index_kind"] == "dated_primary_d_runtime_pointer_only"
    assert runtime_index["data_alias"] is False
    assert runtime_index["latest_signal_date"] == SIGNAL_DATE
    assert runtime_index["latest_exec_date"] == EXEC_DATE
    assert runtime_index["latest_exit_date"] == EXIT_DATE
    assert runtime_index["latest_receipt_url"] == (
        f"outputs/decision/primary_d_receipt_{SIGNAL_DATE}.json"
    )
    assert runtime_index["latest_receipt_sha256"] == _sha256(
        result["paths"]["receipt"]
    )
    assert runtime_index["latest_runtime_features_url"] == (
        f"outputs/decision/primary_d_runtime_features_{SIGNAL_DATE}.csv"
    )
    assert runtime_index["latest_runtime_features_sha256"] == _sha256(runtime_path)
    assert runtime_index["runtime_feature_row_count"] == 0
    assert runtime_index["runtime_selected_count"] == 0
    assert runtime_index["runtime_identity_sha256"] == outputs[
        "runtime_identity_sha256"
    ]
    assert runtime_index["latest_feature_snapshot_sha256"] == contract[
        "feature_snapshot_sha256"
    ]
    assert runtime_index["latest_top10_members_sha256"] == contract[
        "top10_members_sha256"
    ]
    assert runtime_index["latest_three_rank_json_sha256"] == _sha256(
        result["paths"]["json"]
    )
    assert runtime_index["latest_three_rank_csv_sha256"] == _sha256(
        result["paths"]["csv"]
    )
    assert runtime_index["latest_bundle_sha256"] == contract["bundle_sha256"]
    for path in result["paths"].values():
        assert path.is_file()
    runtime_path.write_bytes(runtime_path.read_bytes() + b"\n")
    with pytest.raises(
        PrimaryDGenerationError,
        match="primary runtime index receipt output bindings drifted",
    ):
        build_primary_d_runtime_index(
            tmp_path,
            receipt_path=result["paths"]["receipt"],
            runtime_path=runtime_path,
            three_rank_json_path=result["paths"]["json"],
            three_rank_csv_path=result["paths"]["csv"],
        )


def test_primary_runtime_index_is_idempotent_monotonic_and_byte_strict(
    tmp_path: Path,
) -> None:
    def payload(signal_date: str, exec_date: str, exit_date: str, seed: str) -> dict:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return {
            "schema_version": "dc20_primary_d_runtime_index_v1",
            "index_kind": "dated_primary_d_runtime_pointer_only",
            "data_alias": False,
            "latest_signal_date": signal_date,
            "latest_exec_date": exec_date,
            "latest_exit_date": exit_date,
            "latest_receipt_url": (
                f"outputs/decision/primary_d_receipt_{signal_date}.json"
            ),
            "latest_receipt_sha256": digest,
            "latest_runtime_features_url": (
                f"outputs/decision/primary_d_runtime_features_{signal_date}.csv"
            ),
            "latest_runtime_features_sha256": digest,
            "runtime_feature_row_count": 0,
            "runtime_selected_count": 0,
            "runtime_identity_sha256": digest,
            "latest_feature_snapshot_sha256": digest,
            "latest_top10_members_sha256": digest,
            "latest_three_rank_json_url": (
                f"outputs/decision/three_rank_top10_{signal_date}.json"
            ),
            "latest_three_rank_json_sha256": digest,
            "latest_three_rank_csv_url": (
                f"outputs/decision/three_rank_top10_{signal_date}.csv"
            ),
            "latest_three_rank_csv_sha256": digest,
            "latest_bundle_sha256": digest,
        }

    first = payload("20260826", "20260827", "20260828", "first")
    invalid = dict(first)
    invalid["unexpected"] = True
    with pytest.raises(
        PrimaryDGenerationError,
        match="primary runtime index field surface drifted",
    ):
        materialize_primary_d_runtime_index(tmp_path, invalid)
    path, materialized = materialize_primary_d_runtime_index(tmp_path, first)
    first_bytes = path.read_bytes()
    assert materialized == first

    same_path, same = materialize_primary_d_runtime_index(tmp_path, dict(first))
    assert same_path == path
    assert same == first
    assert path.read_bytes() == first_bytes

    drifted = dict(first)
    drifted["latest_bundle_sha256"] = hashlib.sha256(b"drift").hexdigest()
    with pytest.raises(
        PrimaryDGenerationError,
        match="same-date primary runtime index bytes drifted",
    ):
        materialize_primary_d_runtime_index(tmp_path, drifted)
    assert path.read_bytes() == first_bytes

    newer = payload("20260827", "20260828", "20260831", "newer")
    _, advanced = materialize_primary_d_runtime_index(tmp_path, newer)
    advanced_bytes = path.read_bytes()
    assert advanced == newer
    assert advanced_bytes != first_bytes

    _, retained = materialize_primary_d_runtime_index(tmp_path, first)
    assert retained == newer
    assert path.read_bytes() == advanced_bytes


def test_hard_pool_cannot_be_published_as_a_fake_empty_inference() -> None:
    class FakeEngine:
        class Config:
            max_mechanism_limit_pct = 10.0

        config = Config()

        @staticmethod
        def _row(frame: pd.DataFrame, code: str) -> pd.Series | None:
            if frame.empty or code not in frame.index:
                return None
            return frame.loc[code]

        @staticmethod
        def _limit_ratio(_daily: pd.Series, _limit: pd.Series) -> float:
            return 0.10

        def market_table(self, _date: str, name: str) -> pd.DataFrame:
            if name == "daily":
                return pd.DataFrame(
                    [{"ts_code": "000002.SZ", "close": 11.0}]
                ).set_index("ts_code", drop=False)
            return pd.DataFrame(columns=("ts_code", "up_limit")).set_index(
                "ts_code", drop=False
            )

    hard_pool = pd.DataFrame(
        [
            {
                "signal_date": SIGNAL_DATE,
                "ts_code": "000002.SZ",
                "name": "测试股份",
                "industry": "测试",
                "limit_times": 2.0,
                "stage": 2.0,
            }
        ]
    )
    with pytest.raises(
        PrimaryDGenerationError,
        match="MISSING_EXACT_D_STK_LIMIT_ROW.*000002.SZ",
    ):
        audit_complete_hard_pool(
            hard_pool,
            pd.DataFrame(),
            pd.DataFrame(),
            engine=FakeEngine(),  # type: ignore[arg-type]
            signal_date=SIGNAL_DATE,
        )


def test_primary_workflow_owns_staggered_evening_slots_and_established_bridge() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    full_daily = FULL_DAILY_WORKFLOW.read_text(encoding="utf-8")
    header = workflow.split("\npermissions:", 1)[0]
    full_daily_header = full_daily.split("\npermissions:", 1)[0]
    expected_direct = {
        f"15 {hour} * * {weekday}"
        for weekday in range(1, 6)
        for hour in (13, 14, 15, 16)
    }
    expected_bridge = {
        f"{minute} {hour} * * {weekday}"
        for weekday in range(1, 6)
        for hour, minute in (
            (13, 25),
            (14, 25),
            (15, 25),
            (15, 45),
            (16, 25),
        )
    }
    assert set(re.findall(r'cron: "([^"]+)"', header)) == expected_direct
    assert set(re.findall(r'cron: "([^"]+)"', full_daily_header)) == expected_bridge
    assert len(re.findall(r'cron: "([^"]+)"', header)) == len(expected_direct)
    assert len(re.findall(r'cron: "([^"]+)"', full_daily_header)) == len(
        expected_bridge
    )
    assert not any("1-5" in cron for cron in expected_direct | expected_bridge)
    identities = _workflow_schedule_identities()
    assert identities["343703608"]["schedules"] == expected_direct
    assert identities["335484130"]["schedules"] == expected_bridge
    assert "workflow_call:" in header
    assert "for weekday in range(1, 6)" in workflow
    assert "for hour in (13, 14, 15, 16)" in workflow
    assert "for hour, minute in (" in workflow
    assert "uses: ./.github/workflows/run_primary_d_daily.yml" in full_daily
    assert "github.event_name == 'workflow_dispatch'" in full_daily
    assert "dc20-p0-scheduler-bridge-{0}" in full_daily
    assert "secrets: inherit" not in full_daily
    assert "group: decision-auction-main-writer" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "scripts/publish_primary_three_rank.py" in workflow
    assert "primary_d_receipt_${SIGNAL_DATE}.json" in workflow
    assert "primary_d_runtime_features_${SIGNAL_DATE}.csv" in workflow
    assert "--ensure-sse-open-context" not in workflow
    assert "PrimaryDReadOnlyEngine" in (
        ROOT / "scripts/publish_primary_three_rank.py"
    ).read_text(encoding="utf-8")
    assert "real manual P0 publication is recovery-only" in workflow
    assert "partial immutable P0 D bundle exists" in workflow
    assert "P0 candidate lacks the immutable dated bundle" in workflow
    assert "non-P0 path staged" in workflow
    assert "cmp \"${RUNNER_TEMP}/protected-before.bin\"" in workflow
    assert "action_plan_*.json" in workflow
    assert "data/decision_executable_profit" in workflow
    assert "outputs/decision/executable_profit_research" in workflow
    assert "outputs/decision/legacy_profit_relative_research" in workflow
    assert "needs.publish.outputs.published_head" in workflow
    assert "primary_d_bundle_sha256" in workflow
    assert "runtime_identity_sha256" in workflow
    assert "P0 scheduled run API identity drifted" in workflow
    assert "str(run.get('run_attempt') or '') != '1'" in workflow
    assert "resolve_nominal_schedule_slot(schedule, created)" in workflow
    assert "nominal_slot.strftime('%Y%m%d')" in workflow
    assert "if event in {'schedule', 'workflow_run'}:" in workflow
    assert "enforce_pre_t0920(signal_date, opened, datetime.now(timezone.utc))" in workflow
    assert "P0 scheduled run crossed the T 09:20 safety boundary" in workflow
    assert "shanghai.hour not in {0, 21, 22, 23}" not in workflow
    assert "signal_day -= timedelta(days=1)" not in workflow
    assert "'343703608'" in workflow and "'335484130'" in workflow
    assert "DC2.0 · Test Decision Core" in header
    assert "DC2.0 · Tushare Health Check" in header
    assert "github.event.workflow_run.workflow_id == 335484132" in workflow
    assert "github.event.workflow_run.workflow_id == 335582723" in workflow
    assert "P0 workflow_run upstream API identity drifted" in workflow
    assert "dc20-p0-ignored-{0}" in workflow
    assert "if shanghai.hour < 20:" in workflow
    assert "P0 workflow_run has no preceding strict SSE open day" in workflow

    publish = workflow[workflow.index("  publish:") : workflow.index("\n  deploy-primary-pages:")]
    assert "GENERATION_MODE: ${{ needs.compute.outputs.generation_mode }}" in publish
    assert "SIGNAL_DATE: ${{ needs.compute.outputs.signal_date }}" in publish
    assert "safety_cutoff = deadline - timedelta(minutes=5)" in publish
    assert "P0 CAS publication missed the T 09:15 safety cutoff" in publish
    assert publish.index("safety_cutoff = deadline - timedelta(minutes=5)") < publish.index(
        'git push "https://x-access-token:${GITHUB_TOKEN}'
    )


@pytest.mark.parametrize("weekday", range(1, 6))
@pytest.mark.parametrize(
    ("hour", "minute"),
    (
        (13, 15),
        (14, 15),
        (15, 15),
        (16, 15),
        (13, 25),
        (14, 25),
        (15, 25),
        (15, 45),
        (16, 25),
    ),
)
def test_primary_schedule_slots_bind_to_their_nominal_utc_d(
    weekday: int,
    hour: int,
    minute: int,
) -> None:
    resolve = _workflow_resolver_function("resolve_nominal_schedule_slot")
    nominal = datetime(2026, 8, 31, hour, minute, tzinfo=timezone.utc) + timedelta(
        days=weekday - 1
    )
    schedule = f"{minute} {hour} * * {weekday}"
    assert resolve(schedule, nominal + timedelta(hours=7)) == nominal


@pytest.mark.parametrize(
    ("schedule", "created_at"),
    (
        ("15 13 * * 5", datetime(2026, 8, 29, 0, 5, tzinfo=timezone.utc)),
        ("15 15 * * 5", datetime(2026, 8, 29, 0, 5, tzinfo=timezone.utc)),
        ("15 16 * * 5", datetime(2026, 8, 29, 0, 30, tzinfo=timezone.utc)),
        ("25 16 * * 5", datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)),
        ("15 13 * * 5", datetime(2026, 8, 31, 13, 16, tzinfo=timezone.utc)),
        ("25 13 * * 5", datetime(2026, 8, 31, 13, 26, tzinfo=timezone.utc)),
    ),
)
def test_primary_schedule_delay_keeps_the_latest_provable_nominal_d(
    schedule: str,
    created_at: datetime,
) -> None:
    resolve = _workflow_resolver_function("resolve_nominal_schedule_slot")
    assert resolve(schedule, created_at).strftime("%Y%m%d") == "20260828"


@pytest.mark.parametrize(
    ("schedule", "created_at"),
    (
        ("15 13 * * 5", datetime(2026, 8, 31, 13, 16, tzinfo=timezone.utc)),
        ("15 14 * * 5", datetime(2026, 8, 31, 14, 16, tzinfo=timezone.utc)),
        ("15 15 * * 5", datetime(2026, 8, 31, 15, 16, tzinfo=timezone.utc)),
        ("15 16 * * 5", datetime(2026, 8, 31, 16, 16, tzinfo=timezone.utc)),
        ("25 13 * * 5", datetime(2026, 8, 31, 13, 26, tzinfo=timezone.utc)),
        ("25 14 * * 5", datetime(2026, 8, 31, 14, 26, tzinfo=timezone.utc)),
        ("25 15 * * 5", datetime(2026, 8, 31, 15, 26, tzinfo=timezone.utc)),
        ("45 15 * * 5", datetime(2026, 8, 31, 15, 46, tzinfo=timezone.utc)),
        ("25 16 * * 5", datetime(2026, 8, 31, 16, 26, tzinfo=timezone.utc)),
    ),
)
def test_primary_schedule_identity_does_not_roll_delayed_friday_into_monday(
    schedule: str,
    created_at: datetime,
) -> None:
    resolve = _workflow_resolver_function("resolve_nominal_schedule_slot")
    assert resolve(schedule, created_at) == datetime(
        2026,
        8,
        28,
        int(schedule.split()[1]),
        int(schedule.split()[0]),
        tzinfo=timezone.utc,
    )


def test_primary_schedule_rejects_unknown_or_naive_cron_evidence() -> None:
    resolve = _workflow_resolver_function("resolve_nominal_schedule_slot")
    aware = datetime(2026, 8, 28, 13, 15, tzinfo=timezone.utc)
    with pytest.raises(SystemExit, match="cron cannot be resolved"):
        resolve("30 13 * * *", aware)
    with pytest.raises(SystemExit, match="cron cannot be resolved"):
        resolve("15 13 * * 1-5", aware)
    with pytest.raises(SystemExit, match="timezone-aware"):
        resolve("15 13 * * 5", aware.replace(tzinfo=None))


def test_primary_schedule_must_finish_strictly_before_t0920() -> None:
    enforce = _workflow_resolver_function("enforce_pre_t0920")
    opened = {"20260828", "20260831", "20260901"}
    enforce(
        "20260828",
        opened,
        datetime(2026, 8, 31, 9, 19, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    with pytest.raises(SystemExit, match="T 09:20 safety boundary"):
        enforce(
            "20260828",
            opened,
            datetime(2026, 8, 31, 9, 20, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    with pytest.raises(SystemExit, match="not a strict SSE open day"):
        enforce(
            "20260829",
            opened,
            datetime(2026, 8, 31, 9, 19, tzinfo=ZoneInfo("Asia/Shanghai")),
        )


def test_primary_workflow_redeploys_an_already_committed_bundle() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    compute = workflow[workflow.index("  compute:") : workflow.index("\n  publish:")]
    deploy = workflow[workflow.index("\n  deploy-primary-pages:") :]

    assert "base_head: ${{ steps.mode.outputs.base_head }}" in compute
    assert 'echo "base_head=${base_head}" >> "${GITHUB_OUTPUT}"' in compute
    assert "existing P0 D receipt generation mode is invalid" in compute
    assert "natural P0 cannot adopt a retrospective recovery receipt" in compute
    assert "mode = receipt_mode" in compute
    assert "needs: [compute, publish]" in deploy
    assert "always()" in deploy
    assert "needs.compute.outputs.already_complete == 'true'" in deploy
    assert "needs.publish.result == 'skipped'" in deploy
    deploy_head = (
        "${{ needs.publish.outputs.published_head || "
        "needs.compute.outputs.base_head }}"
    )
    legacy, canonical = deploy.split("\n  deploy-primary-pages-canonical:", 1)
    assert legacy.count(deploy_head) == 3
    assert "name: Deprecated partial primary D deploy (disabled)" in legacy
    assert "if: ${{ false }}" in legacy
    assert "SIGNAL_DATE: ${{ needs.compute.outputs.signal_date }}" in legacy
    assert "GENERATION_MODE: ${{ needs.compute.outputs.generation_mode }}" in legacy
    assert canonical.count(deploy_head) == 1
    assert "name: Deploy exact primary D revision" in canonical
    assert "uses: ./.github/workflows/deploy_dc20_pages.yml" in canonical
    assert "expected_head: " + deploy_head in canonical


def test_primary_commit_has_exclusive_pages_owner_marker() -> None:
    primary = WORKFLOW.read_text(encoding="utf-8")
    pages = (ROOT / ".github/workflows/deploy_dc20_pages.yml").read_text(
        encoding="utf-8"
    )
    marker = "[dc20-p0-pages-owned]"

    assert f'git commit -m "auto: publish primary D list ${{signal_date}} {marker}"' in primary
    assert "github.event_name == 'push'" in pages
    assert f"contains(github.event.head_commit.message, '{marker}')" in pages


def test_primary_workflow_does_not_call_secondary_generators() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "scripts/run_v2.py",
        "scripts/run_auction_v3.py",
        "scripts/publish_decision_action.py",
        "scripts/run_decision_executable_profit_forward_shadow.py",
        "scripts/project_decision_executable_profit_research.py",
    ):
        assert forbidden not in workflow
