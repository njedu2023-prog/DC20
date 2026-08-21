from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "recover_decision_action_gaps.py"
SPEC = importlib.util.spec_from_file_location("recover_decision_action_gaps", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recovery
SPEC.loader.exec_module(recovery)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


class ExactBaseFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.output = tmp_path / "candidate"
        self.root.mkdir()
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.name", "DC20 Test")
        _git(self.root, "config", "user.email", "dc20-test@example.invalid")
        self._write_common()
        self._write_day("20260720", "20260721", "20260722", "600001.SH")
        self._write_day("20260721", "20260722", "20260723", "000001.SZ")
        latest = self.root / "outputs" / "decision" / "action_plan_latest.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text('{"sentinel":"must-not-change"}\n', encoding="utf-8")
        self.commit("fixture")

    def _write_common(self) -> None:
        manifest = self.root / "models" / "decision_model_freeze.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "decision_model_freeze_v2",
                    "active": True,
                    "freeze_id": "fixture-active-freeze",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        rows = []
        previous_open = "20260717"
        closed = {"20260718", "20260719"}
        for day in (
            "20260718",
            "20260719",
            "20260720",
            "20260721",
            "20260722",
            "20260723",
            "20260724",
        ):
            is_open = 0 if day in closed else 1
            rows.append(
                {
                    "exchange": "SSE",
                    "cal_date": day,
                    "is_open": is_open,
                    "pretrade_date": previous_open,
                }
            )
            if is_open:
                previous_open = day
        _write_csv(
            self.root / "data" / "market" / "trade_cal_sse.csv",
            ["exchange", "cal_date", "is_open", "pretrade_date"],
            rows,
        )

    def _write_day(self, signal: str, report: str, exit_date: str, code: str) -> None:
        output = self.root / "outputs" / "decision"
        data = self.root / "data" / "decision"
        output.mkdir(parents=True, exist_ok=True)
        data.mkdir(parents=True, exist_ok=True)
        report_path = output / f"decision_report_{report}.md"
        report_path.write_text(
            "".join(
                (
                    f"# Decision Report ({report})\n\n",
                    f"- signal_date: **{signal}**\n",
                    f"- exec_date: **{report}**\n",
                    f"- exit_date: **{exit_date}**\n",
                )
            ),
            encoding="utf-8",
        )
        candidate_relative = f"data/decision/decision_candidates_{signal}.csv"
        execution_relative = f"data/decision/decision_execution_{report}.csv"
        report_relative = f"outputs/decision/decision_report_{report}.md"
        (output / f"eval_{report}.json").write_text(
            json.dumps(
                {
                    "signal_date": signal,
                    "exec_date": report,
                    "exit_date": exit_date,
                    "paths": {
                        "candidates": candidate_relative,
                        "execution": execution_relative,
                        "decision_report": report_relative,
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _write_csv(
            data / f"decision_candidates_{signal}.csv",
            [
                "trade_date",
                "target_trade_date",
                "signal_date",
                "exec_date",
                "exit_date",
                "ts_code",
                "name",
                "industry",
                "advance_stage",
                "recommended_max_price",
            ],
            [
                {
                    "trade_date": signal,
                    "target_trade_date": report,
                    "signal_date": signal,
                    "exec_date": report,
                    "exit_date": exit_date,
                    "ts_code": code,
                    "name": f"fixture-{code}",
                    "industry": "fixture-industry",
                    "advance_stage": "2→3",
                    "recommended_max_price": "99.99",
                }
            ],
        )
        _write_csv(
            data / f"decision_execution_{report}.csv",
            [
                "exec_date",
                "ts_code",
                "jq_code",
                "filled_flag",
                "buy_time",
                "buy_price",
                "fail_reason",
                "buy_slippage_bp",
            ],
            [],
        )

    def commit(self, message: str) -> str:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", message)
        self.base_sha = _git(self.root, "rev-parse", "HEAD")
        return self.base_sha

    def load_json(self, relative: str) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def write_json(self, relative: str, value: dict) -> None:
        path = self.root / relative
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def exact_base(tmp_path: Path) -> ExactBaseFixture:
    return ExactBaseFixture(tmp_path)


def _past_now() -> datetime:
    return datetime(2026, 7, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_recovery_builds_only_isolated_dated_reject_plans_and_index(
    exact_base: ExactBaseFixture,
) -> None:
    latest = exact_base.root / "outputs" / "decision" / "action_plan_latest.json"
    latest_before = latest.read_bytes()
    receipt = recovery.recover(
        exact_base.root,
        ["20260721,20260722"],
        exact_base.base_sha,
        exact_base.output,
        now=_past_now(),
    )

    assert receipt["status"] == "candidate_generated"
    assert receipt["report_dates"] == ["20260721", "20260722"]
    assert receipt["changed_paths"] == [
        "outputs/decision/action_plan_20260721.json",
        "outputs/decision/action_plan_20260722.json",
        "outputs/decision/report_index.json",
    ]
    assert receipt["action_plan_latest_changed"] is False
    assert latest.read_bytes() == latest_before
    assert not (exact_base.output / "outputs/decision/action_plan_latest.json").exists()

    for report_date in receipt["report_dates"]:
        relative = f"outputs/decision/action_plan_{report_date}.json"
        path = exact_base.output / relative
        plan = json.loads(path.read_text(encoding="utf-8"))
        assert plan["schema_version"] == "decision_action_plan_v12_top10_trade_selector"
        assert plan["timing_status"] == "RETROSPECTIVE_LATE_GENERATION"
        assert plan["status_code"] == "NO_TRADE_MISSED_LIVE_AUCTION"
        assert plan["retrospective"] is True
        assert plan["live_delivery_met"] is False
        assert plan["formal_buy_count"] == 0
        assert plan["shadow_count"] == 0
        assert plan["risk_budget"] == 0.0
        assert plan["recovery"]["base_sha"] == exact_base.base_sha
        assert plan["recovery"]["external_data_read"] is False
        assert plan["recovery"]["minute_data_read"] is False
        assert plan["recovery"]["t_truth_read"] is False
        assert plan["recovery"]["t1_truth_read"] is False
        for row in [*plan["candidates"], *plan["stage_watchlist"]]:
            assert row["action"] == "REJECT"
            assert row["target_weight"] == 0.0
            assert row["trade_shadow_selected"] == 0
            assert row["trade_selected"] == 0
            assert row["market_order_allowed"] == 0
            assert row["risk_gate_pass"] == 0
            assert row["recommended_max_gap"] is None
            assert row["recommended_max_price"] is None
            assert row["max_auction_change_pct"] is None
            assert row["observation_max_price"] is None
            assert row["take_profit_price"] is None
            assert row["stop_loss_price"] is None
        assert receipt["output_sha256"][relative] == hashlib.sha256(path.read_bytes()).hexdigest()

    source_paths = set(receipt["source_sha256"])
    assert "models/decision_model_freeze.json" in source_paths
    assert "data/market/trade_cal_sse.csv" in source_paths
    assert len(source_paths) == 10
    assert all("minute" not in path and "auction_v3" not in path for path in source_paths)
    index = json.loads(
        (exact_base.output / "outputs/decision/report_index.json").read_text(encoding="utf-8")
    )
    assert index["schema_version"] == "decision_report_index_v2_action_truth"
    assert index["latest_report_date"] == "20260722"
    assert [item["report_date"] for item in index["reports"]] == ["20260722", "20260721"]
    assert all(item["action_available"] is True for item in index["reports"])
    assert index["latest_action_report_date"] == "20260722"
    assert index["latest_action_url"] == "outputs/decision/action_plan_20260722.json"
    assert index["recovery_candidate"]["report_dates"] == ["20260721", "20260722"]
    assert index["recovery_candidate"]["action_plan_latest_changed"] is False


def test_cli_prints_one_compact_json_receipt_line(exact_base: ExactBaseFixture) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--root",
            str(exact_base.root),
            "--report-dates",
            "20260721,20260722",
            "--base-sha",
            exact_base.base_sha,
            "--output-root",
            str(exact_base.output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
    assert len(completed.stdout.splitlines()) == 1
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "candidate_generated"
    assert receipt["base_sha"] == exact_base.base_sha


@pytest.mark.parametrize(
    "dates",
    [
        [],
        ["20260721", "20260721"],
        ["20260722", "20260721"],
        ["20260719", "20260720", "20260721", "20260722", "20260723", "20260724"],
    ],
)
def test_report_dates_must_be_unique_ascending_and_bounded(
    exact_base: ExactBaseFixture,
    dates: list[str],
) -> None:
    with pytest.raises(recovery.RecoveryError):
        recovery.recover(
            exact_base.root,
            dates,
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_inactive_manifest_is_rejected(exact_base: ExactBaseFixture) -> None:
    manifest = exact_base.load_json("models/decision_model_freeze.json")
    manifest["active"] = False
    exact_base.write_json("models/decision_model_freeze.json", manifest)
    exact_base.commit("inactive")
    with pytest.raises(recovery.RecoveryError, match="active Decision freeze"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_future_and_same_day_before_close_are_rejected(exact_base: ExactBaseFixture) -> None:
    with pytest.raises(recovery.RecoveryError, match="future report"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=datetime(2026, 7, 20, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    with pytest.raises(recovery.RecoveryError, match="before market close"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=datetime(2026, 7, 21, 9, 29, tzinfo=ZoneInfo("Asia/Shanghai")),
        )


def test_signal_exec_exit_chain_is_calendar_strict(exact_base: ExactBaseFixture) -> None:
    evaluation = exact_base.load_json("outputs/decision/eval_20260721.json")
    evaluation["exit_date"] = "20260723"
    exact_base.write_json("outputs/decision/eval_20260721.json", evaluation)
    exact_base.commit("bad chain")
    with pytest.raises(recovery.RecoveryError, match="exec to exit date chain"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_dirty_source_is_not_accepted_as_exact_base(exact_base: ExactBaseFixture) -> None:
    path = exact_base.root / "outputs/decision/eval_20260721.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="differs from exact base"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_nonempty_execution_evidence_blocks_no_trade_recovery(
    exact_base: ExactBaseFixture,
) -> None:
    _write_csv(
        exact_base.root / "data/decision/decision_execution_20260721.csv",
        [
            "exec_date",
            "ts_code",
            "jq_code",
            "filled_flag",
            "buy_time",
            "buy_price",
            "fail_reason",
            "buy_slippage_bp",
        ],
        [
            {
                "exec_date": "20260721",
                "ts_code": "600001.SH",
                "jq_code": "600001.XSHG",
                "filled_flag": 1,
                "buy_time": "09:30:00",
                "buy_price": 10,
                "fail_reason": "",
                "buy_slippage_bp": 0,
            }
        ],
    )
    exact_base.commit("execution evidence")
    with pytest.raises(recovery.RecoveryError, match="zero execution rows"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_candidate_date_mismatch_is_rejected(exact_base: ExactBaseFixture) -> None:
    path = exact_base.root / "data/decision/decision_candidates_20260720.csv"
    text = path.read_text(encoding="utf-8-sig").replace("20260722", "20260723")
    path.write_text(text, encoding="utf-8-sig")
    exact_base.commit("candidate mismatch")
    with pytest.raises(recovery.RecoveryError, match="candidate exit_date mismatch"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_existing_action_or_candidate_output_conflict_fails_closed(
    exact_base: ExactBaseFixture,
) -> None:
    conflict = exact_base.root / "outputs/decision/action_plan_20260721.json"
    conflict.write_text("{}\n", encoding="utf-8")
    exact_base.commit("existing action")
    with pytest.raises(recovery.RecoveryError, match="existing action plan conflict"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_output_root_must_be_isolated_and_empty_at_targets(
    exact_base: ExactBaseFixture,
) -> None:
    with pytest.raises(recovery.RecoveryError, match="isolated"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.root / "candidate",
            now=_past_now(),
        )
    target = exact_base.output / "outputs/decision/action_plan_20260721.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="candidate output conflict"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )


def test_root_and_candidate_child_symlinks_are_rejected_without_repo_writes(
    exact_base: ExactBaseFixture,
) -> None:
    root_link = exact_base.root.parent / "repo-link"
    root_link.symlink_to(exact_base.root, target_is_directory=True)
    with pytest.raises(recovery.RecoveryError, match="--root must not be a symlink"):
        recovery.recover(
            root_link,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )

    escape_target = exact_base.root / "candidate-escape-target"
    escape_target.mkdir()
    exact_base.output.mkdir()
    (exact_base.output / "outputs").symlink_to(
        escape_target,
        target_is_directory=True,
    )
    with pytest.raises(recovery.RecoveryError, match="parent must not be a symlink"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )
    assert list(escape_target.rglob("*")) == []
    assert not (
        exact_base.root / "outputs/decision/action_plan_20260721.json"
    ).exists()


def test_exact_base_outputs_are_byte_deterministic_across_replays(
    exact_base: ExactBaseFixture,
) -> None:
    first_output = exact_base.root.parent / "candidate-first"
    second_output = exact_base.root.parent / "candidate-second"
    first = recovery.recover(
        exact_base.root,
        ["20260721", "20260722"],
        exact_base.base_sha,
        first_output,
        now=datetime(2026, 7, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    second = recovery.recover(
        exact_base.root,
        ["20260721", "20260722"],
        exact_base.base_sha,
        second_output,
        now=datetime(2026, 7, 25, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert first["changed_paths"] == second["changed_paths"]
    assert first["source_sha256"] == second["source_sha256"]
    assert first["output_sha256"] == second["output_sha256"]
    for relative in first["changed_paths"]:
        assert (first_output / relative).read_bytes() == (
            second_output / relative
        ).read_bytes()


def test_base_sha_must_equal_head(exact_base: ExactBaseFixture) -> None:
    with pytest.raises(recovery.RecoveryError, match="exact-base mismatch"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            "0" * 40,
            exact_base.output,
            now=_past_now(),
        )


def test_untracked_report_cannot_change_exact_base_index(
    exact_base: ExactBaseFixture,
) -> None:
    path = exact_base.root / "outputs/decision/decision_report_20260723.md"
    path.write_text("# Decision Report (20260723)\n", encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="index inventory differs"):
        recovery.recover(
            exact_base.root,
            ["20260721"],
            exact_base.base_sha,
            exact_base.output,
            now=_past_now(),
        )
