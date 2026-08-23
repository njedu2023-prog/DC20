from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.decision_pages_truth import (  # noqa: E402
    DecisionPagesTruthError,
    assess_decision_pages_truth,
    project_report_index_action_truth,
    validate_report_index_action_truth,
)


WORKFLOW = ROOT / ".github" / "workflows" / "deploy_dc20_pages.yml"


def _write_eval(root: Path, signal_date: str, exec_date: str) -> Path:
    path = root / f"eval_{exec_date}.json"
    path.write_text(
        json.dumps(
            {"signal_date": signal_date, "exec_date": exec_date},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def _write_calendar(root: Path, rows: list[tuple[str, int]]) -> Path:
    path = root / "trade_cal_sse.csv"
    body = "exchange,cal_date,is_open,pretrade_date\n" + "".join(
        f"SSE,{cal_date},{is_open},\n" for cal_date, is_open in rows
    )
    path.write_text(body, encoding="utf-8")
    return path


def _friday_to_monday_calendar() -> list[tuple[str, int]]:
    return [
        ("20260821", 1),
        ("20260822", 0),
        ("20260823", 0),
        ("20260824", 1),
        ("20260825", 1),
        ("20260826", 1),
    ]


def _report_entry(report_date: str, action_available: bool) -> dict[str, object]:
    row: dict[str, object] = {
        "report_date": report_date,
        "report_file": f"decision_report_{report_date}.md",
        "report_url": f"outputs/decision/decision_report_{report_date}.md",
        "eval_url": f"outputs/decision/eval_{report_date}.json",
        "action_available": action_available,
    }
    if action_available:
        row["action_url"] = f"outputs/decision/action_plan_{report_date}.json"
    return row


def _write_action_index(
    site_root: Path,
    reports: list[dict[str, object]],
    latest_action_date: str = "",
) -> Path:
    output = site_root / "outputs" / "decision"
    output.mkdir(parents=True, exist_ok=True)
    latest_report_date = str(reports[0]["report_date"])
    path = output / "report_index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "decision_report_index_v2_action_truth",
                "generated_at_utc": "2026-08-21T14:00:00+00:00",
                "latest_report_date": latest_report_date,
                "latest_report_file": f"decision_report_{latest_report_date}.md",
                "latest_action_report_date": latest_action_date,
                "latest_action_url": (
                    f"outputs/decision/action_plan_{latest_action_date}.json"
                    if latest_action_date
                    else ""
                ),
                "reports": reports,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def _write_dated_action(site_root: Path, report_date: str, payload_date: str = "") -> Path:
    path = (
        site_root
        / "outputs"
        / "decision"
        / f"action_plan_{report_date}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    bound_date = payload_date or report_date
    bound_day = datetime.strptime(bound_date, "%Y%m%d").date()
    path.write_text(
        json.dumps(
            {
                "schema_version": "decision_action_plan_v99_test",
                "report_date": bound_date,
                "signal_date": (bound_day - timedelta(days=1)).strftime("%Y%m%d"),
                "exec_date": bound_date,
                "exit_date": (bound_day + timedelta(days=1)).strftime("%Y%m%d"),
                "broker_connected": False,
                "execution_or_fill_claimed": False,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def _write_site_report_pair(
    site_root: Path,
    report_date: str,
    signal_date: str,
) -> tuple[Path, Path]:
    output = site_root / "outputs" / "decision"
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / f"decision_report_{report_date}.md"
    report_path.write_text(
        f"# Decision Report ({report_date})\n\n"
        f"- signal_date: **{signal_date}**\n"
        f"- exec_date: **{report_date}**\n",
        encoding="utf-8",
    )
    evaluation_path = output / f"eval_{report_date}.json"
    evaluation_path.write_text(
        json.dumps(
            {"signal_date": signal_date, "exec_date": report_date},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return report_path, evaluation_path


def test_friday_report_for_monday_is_prospective_not_future_stale(
    tmp_path: Path,
) -> None:
    truth = assess_decision_pages_truth(
        evaluation_path=_write_eval(tmp_path, "20260821", "20260824"),
        calendar_path=_write_calendar(tmp_path, _friday_to_monday_calendar()),
        report_date="20260824",
        today=date(2026, 8, 21),
        freeze_active=True,
        max_report_age_days=1,
    )

    assert truth.signal_date == "20260821"
    assert truth.next_open_date == "20260824"
    assert truth.report_age_days == -3
    assert truth.prospective is True
    assert truth.stale is False
    assert truth.stale_reason == "none"
    assert truth.freshness_state == "PROSPECTIVE_NEXT_SESSION"


def test_prospective_report_stays_valid_during_the_weekend(tmp_path: Path) -> None:
    truth = assess_decision_pages_truth(
        evaluation_path=_write_eval(tmp_path, "20260821", "20260824"),
        calendar_path=_write_calendar(tmp_path, _friday_to_monday_calendar()),
        report_date="20260824",
        today=date(2026, 8, 23),
        freeze_active=True,
        max_report_age_days=1,
    )

    assert truth.report_age_days == -1
    assert truth.prospective is True
    assert truth.freshness_state == "PROSPECTIVE_NEXT_SESSION"


def test_arbitrary_later_future_session_is_stale_not_prospective(
    tmp_path: Path,
) -> None:
    truth = assess_decision_pages_truth(
        evaluation_path=_write_eval(tmp_path, "20260821", "20260825"),
        calendar_path=_write_calendar(tmp_path, _friday_to_monday_calendar()),
        report_date="20260825",
        today=date(2026, 8, 21),
        freeze_active=True,
        max_report_age_days=1,
    )

    assert truth.next_open_date == "20260824"
    assert truth.prospective is False
    assert truth.stale is True
    assert truth.stale_reason == "report_date_in_future"
    assert truth.freshness_state == "STALE"


def test_exchange_holiday_gap_uses_calendar_not_natural_day_delta(
    tmp_path: Path,
) -> None:
    rows = [("20260430", 1)] + [
        (f"2026050{day}", 0) for day in range(1, 6)
    ] + [("20260506", 1)]
    truth = assess_decision_pages_truth(
        evaluation_path=_write_eval(tmp_path, "20260430", "20260506"),
        calendar_path=_write_calendar(tmp_path, rows),
        report_date="20260506",
        today=date(2026, 4, 30),
        freeze_active=True,
        max_report_age_days=1,
    )

    assert truth.report_age_days == -6
    assert truth.next_open_date == "20260506"
    assert truth.prospective is True
    assert truth.stale is False


def test_inactive_freeze_remains_stale_even_when_timing_is_prospective(
    tmp_path: Path,
) -> None:
    truth = assess_decision_pages_truth(
        evaluation_path=_write_eval(tmp_path, "20260821", "20260824"),
        calendar_path=_write_calendar(tmp_path, _friday_to_monday_calendar()),
        report_date="20260824",
        today=date(2026, 8, 21),
        freeze_active=False,
        max_report_age_days=1,
    )

    assert truth.prospective is True
    assert truth.stale is True
    assert truth.stale_reason == "freeze_inactive"
    assert truth.freshness_state == "STALE"


def test_exec_day_becomes_current_and_then_expires(tmp_path: Path) -> None:
    evaluation_path = _write_eval(tmp_path, "20260821", "20260824")
    calendar_path = _write_calendar(tmp_path, _friday_to_monday_calendar())
    current = assess_decision_pages_truth(
        evaluation_path=evaluation_path,
        calendar_path=calendar_path,
        report_date="20260824",
        today=date(2026, 8, 24),
        freeze_active=True,
        max_report_age_days=1,
    )
    expired = assess_decision_pages_truth(
        evaluation_path=evaluation_path,
        calendar_path=calendar_path,
        report_date="20260824",
        today=date(2026, 8, 26),
        freeze_active=True,
        max_report_age_days=1,
    )

    assert current.prospective is False
    assert current.report_age_days == 0
    assert current.freshness_state == "CURRENT"
    assert current.stale is False
    assert expired.report_age_days == 2
    assert expired.stale_reason == "report_expired"
    assert expired.freshness_state == "STALE"


def test_eval_and_calendar_contracts_fail_closed(tmp_path: Path) -> None:
    calendar_path = _write_calendar(tmp_path, _friday_to_monday_calendar())
    mismatched_eval = _write_eval(tmp_path, "20260821", "20260825")
    with pytest.raises(DecisionPagesTruthError, match="exec_date does not match"):
        assess_decision_pages_truth(
            evaluation_path=mismatched_eval,
            calendar_path=calendar_path,
            report_date="20260824",
            today=date(2026, 8, 21),
            freeze_active=True,
            max_report_age_days=1,
        )

    duplicate_eval = tmp_path / "duplicate.json"
    duplicate_eval.write_text(
        '{"signal_date":"20260821","signal_date":"20260820",'
        '"exec_date":"20260824"}',
        encoding="utf-8",
    )
    with pytest.raises(DecisionPagesTruthError, match="duplicate JSON key"):
        assess_decision_pages_truth(
            evaluation_path=duplicate_eval,
            calendar_path=calendar_path,
            report_date="20260824",
            today=date(2026, 8, 21),
            freeze_active=True,
            max_report_age_days=1,
        )

    incomplete_calendar = _write_calendar(
        tmp_path,
        [("20260821", 1), ("20260822", 0), ("20260824", 1)],
    )
    with pytest.raises(DecisionPagesTruthError, match="does not cover"):
        assess_decision_pages_truth(
            evaluation_path=_write_eval(tmp_path, "20260821", "20260824"),
            calendar_path=incomplete_calendar,
            report_date="20260824",
            today=date(2026, 8, 21),
            freeze_active=True,
            max_report_age_days=1,
        )


def test_action_index_truth_accepts_only_valid_dated_plans_and_ignores_alias(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    reports = [
        _report_entry("20260821", False),
        _report_entry("20260820", True),
        _report_entry("20260819", True),
    ]
    index_path = _write_action_index(site_root, reports, "20260820")
    _write_dated_action(site_root, "20260820")
    _write_dated_action(site_root, "20260819")
    alias = site_root / "outputs" / "decision" / "action_plan_latest.json"
    alias.write_text(
        json.dumps({"report_date": "20260801"}),
        encoding="utf-8",
    )

    truth = validate_report_index_action_truth(
        report_index_path=index_path,
        site_root=site_root,
    )

    assert truth.report_dates == ("20260821", "20260820", "20260819")
    assert truth.action_dates == ("20260820", "20260819")
    assert truth.latest_action_report_date == "20260820"
    assert truth.latest_action_url == "outputs/decision/action_plan_20260820.json"


def test_action_index_truth_rejects_advertised_missing_or_hidden_valid_plan(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    advertised = [_report_entry("20260821", True)]
    advertised_index = _write_action_index(site_root, advertised, "20260821")
    with pytest.raises(DecisionPagesTruthError, match="advertised dated action is invalid"):
        validate_report_index_action_truth(
            report_index_path=advertised_index,
            site_root=site_root,
        )

    _write_dated_action(site_root, "20260821")
    hidden = [_report_entry("20260821", False)]
    hidden_index = _write_action_index(site_root, hidden)
    with pytest.raises(DecisionPagesTruthError, match="hidden"):
        validate_report_index_action_truth(
            report_index_path=hidden_index,
            site_root=site_root,
        )


def test_action_index_truth_rejects_nonboolean_and_inexact_action_url(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    row = _report_entry("20260821", False)
    row["action_available"] = "false"
    index_path = _write_action_index(site_root, [row])
    with pytest.raises(DecisionPagesTruthError, match="must be a bool"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )

    _write_dated_action(site_root, "20260821")
    row = _report_entry("20260821", True)
    row["action_url"] = "outputs/decision/action_plan_latest.json"
    index_path = _write_action_index(site_root, [row], "20260821")
    with pytest.raises(DecisionPagesTruthError, match="action_url is not exact"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )


def test_action_index_truth_rejects_wrong_date_or_corrupt_advertised_plan(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    reports = [_report_entry("20260821", True)]
    index_path = _write_action_index(site_root, reports, "20260821")
    action_path = _write_dated_action(site_root, "20260821", "20260820")
    with pytest.raises(DecisionPagesTruthError, match="different report_date"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )

    action_path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(DecisionPagesTruthError, match="not strict UTF-8 JSON"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("schema", "schema_version"),
        ("exec", "exec_date must equal report_date"),
        ("order", "D < T < T\\+1"),
        ("broker", "cannot connect a broker"),
        ("fill", "cannot claim execution or fill"),
    ],
)
def test_action_index_truth_enforces_minimum_read_only_action_contract(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    site_root = tmp_path / "_site"
    index_path = _write_action_index(
        site_root,
        [_report_entry("20260821", True)],
        "20260821",
    )
    action_path = _write_dated_action(site_root, "20260821")
    payload = json.loads(action_path.read_text(encoding="utf-8"))
    if case == "schema":
        payload["schema_version"] = "legacy_action"
    elif case == "exec":
        payload["exec_date"] = "20260820"
    elif case == "order":
        payload["signal_date"] = "20260821"
    elif case == "broker":
        payload["broker_connected"] = True
    else:
        payload["execution_or_fill_claimed"] = True
    action_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DecisionPagesTruthError, match=message):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )


def test_action_index_truth_accepts_reviewed_v12_before_explicit_fill_marker(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    index_path = _write_action_index(
        site_root,
        [_report_entry("20260821", True)],
        "20260821",
    )
    action_path = _write_dated_action(site_root, "20260821")
    payload = json.loads(action_path.read_text(encoding="utf-8"))
    payload.pop("execution_or_fill_claimed")
    action_path.write_text(json.dumps(payload), encoding="utf-8")

    truth = validate_report_index_action_truth(
        report_index_path=index_path,
        site_root=site_root,
    )
    assert truth.action_dates == ("20260821",)


def test_action_index_truth_requires_latest_fields_to_name_newest_valid_plan(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    reports = [
        _report_entry("20260821", False),
        _report_entry("20260820", True),
        _report_entry("20260819", True),
    ]
    index_path = _write_action_index(site_root, reports, "20260819")
    _write_dated_action(site_root, "20260820")
    _write_dated_action(site_root, "20260819")

    with pytest.raises(DecisionPagesTruthError, match="newest valid dated action"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )


def test_action_index_truth_requires_exact_report_urls_and_order(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    wrong_url = _report_entry("20260821", False)
    wrong_url["eval_url"] = "outputs/decision/eval_latest.json"
    index_path = _write_action_index(site_root, [wrong_url])
    with pytest.raises(DecisionPagesTruthError, match="eval_url is not exact"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )

    unsorted = [
        _report_entry("20260820", False),
        _report_entry("20260821", False),
    ]
    index_path = _write_action_index(site_root, unsorted)
    with pytest.raises(DecisionPagesTruthError, match="strictly descending"):
        validate_report_index_action_truth(
            report_index_path=index_path,
            site_root=site_root,
        )


def test_site_projection_advances_stale_source_index_and_leaves_latest_action_pending(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_index = _write_action_index(
        source_root,
        [_report_entry("20260817", True)],
        "20260817",
    )
    source_index_bytes = source_index.read_bytes()
    site_root = tmp_path / "_site"
    _write_site_report_pair(site_root, "20260817", "20260814")
    _write_site_report_pair(site_root, "20260824", "20260821")
    _write_dated_action(site_root, "20260817")
    stale_alias = site_root / "outputs" / "decision" / "action_plan_latest.json"
    stale_alias.write_text(
        json.dumps({"report_date": "20260801"}),
        encoding="utf-8",
    )

    truth = project_report_index_action_truth(
        source_report_index_path=source_index,
        site_root=site_root,
    )
    projected = json.loads(
        (site_root / "outputs" / "decision" / "report_index.json").read_text(
            encoding="utf-8"
        )
    )

    assert projected["latest_report_date"] == "20260824"
    assert projected["reports"][0] == _report_entry("20260824", False)
    assert projected["generated_at_utc"] is None
    assert projected["inventory_projected"] is True
    assert projected["source_latest_report_date"] == "20260817"
    assert projected["latest_action_report_date"] == "20260817"
    assert projected["latest_action_url"] == (
        "outputs/decision/action_plan_20260817.json"
    )
    assert truth.report_dates == ("20260824", "20260817")
    assert truth.action_dates == ("20260817",)
    assert source_index.read_bytes() == source_index_bytes

    pages_truth = assess_decision_pages_truth(
        evaluation_path=(
            site_root / "outputs" / "decision" / "eval_20260824.json"
        ),
        calendar_path=_write_calendar(tmp_path, _friday_to_monday_calendar()),
        report_date=projected["latest_report_date"],
        today=date(2026, 8, 21),
        freeze_active=True,
        max_report_age_days=1,
    )
    assert pages_truth.freshness_state == "PROSPECTIVE_NEXT_SESSION"
    assert pages_truth.stale is False


@pytest.mark.parametrize("broken_part", ["report", "evaluation"])
def test_site_projection_fails_closed_on_damaged_report_or_evaluation(
    tmp_path: Path,
    broken_part: str,
) -> None:
    source_root = tmp_path / "source"
    source_index = _write_action_index(
        source_root,
        [_report_entry("20260824", False)],
    )
    site_root = tmp_path / "_site"
    report_path, evaluation_path = _write_site_report_pair(
        site_root, "20260824", "20260821"
    )
    if broken_part == "report":
        report_path.write_text(
            "# Decision Report (20260823)\n\n"
            "- signal_date: **20260821**\n"
            "- exec_date: **20260824**\n",
            encoding="utf-8",
        )
        match = "heading does not match"
    else:
        evaluation_path.write_text(
            '{"signal_date":"20260821","exec_date":"20260823"}',
            encoding="utf-8",
        )
        match = "exec_date does not match"

    with pytest.raises(DecisionPagesTruthError, match=match):
        project_report_index_action_truth(
            source_report_index_path=source_index,
            site_root=site_root,
        )


def test_site_projection_requires_regular_v2_source_index(tmp_path: Path) -> None:
    site_root = tmp_path / "_site"
    _write_site_report_pair(site_root, "20260824", "20260821")
    source_index = tmp_path / "report_index.json"
    source_index.write_text(
        json.dumps({"schema_version": "decision_report_index_v1"}),
        encoding="utf-8",
    )
    with pytest.raises(DecisionPagesTruthError, match="schema_version"):
        project_report_index_action_truth(
            source_report_index_path=source_index,
            site_root=site_root,
        )

    target = tmp_path / "real_index.json"
    target.write_text(
        json.dumps({"schema_version": "decision_report_index_v2_action_truth"}),
        encoding="utf-8",
    )
    source_index.unlink()
    source_index.symlink_to(target)
    with pytest.raises(DecisionPagesTruthError, match="symlink"):
        project_report_index_action_truth(
            source_report_index_path=source_index,
            site_root=site_root,
        )


def test_site_projection_preserves_only_strict_source_time_when_inventory_matches(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_index = _write_action_index(
        source_root,
        [_report_entry("20260817", False)],
    )
    site_root = tmp_path / "_site"
    _write_site_report_pair(site_root, "20260817", "20260814")

    project_report_index_action_truth(
        source_report_index_path=source_index,
        site_root=site_root,
    )
    projected = json.loads(
        (site_root / "outputs" / "decision" / "report_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert projected["generated_at_utc"] == "2026-08-21T14:00:00+00:00"
    assert projected["inventory_projected"] is False

    source_payload = json.loads(source_index.read_text(encoding="utf-8"))
    source_payload["generated_at_utc"] = "2026-08-21 14:00:00"
    source_index.write_text(json.dumps(source_payload), encoding="utf-8")
    with pytest.raises(DecisionPagesTruthError, match="UTC timestamp"):
        project_report_index_action_truth(
            source_report_index_path=source_index,
            site_root=site_root,
        )


@pytest.mark.parametrize(
    "linked_name",
    [
        "decision_report_20260824.md",
        "eval_20260824.json",
        "action_plan_20260824.json",
    ],
)
def test_site_projection_rejects_symlinked_dated_inventory_leaf(
    tmp_path: Path,
    linked_name: str,
) -> None:
    source_root = tmp_path / "source"
    source_index = _write_action_index(
        source_root,
        [_report_entry("20260824", True)],
        "20260824",
    )
    site_root = tmp_path / "_site"
    _write_site_report_pair(site_root, "20260824", "20260821")
    _write_dated_action(site_root, "20260824")
    target = site_root / "outputs" / "decision" / linked_name
    outside = tmp_path / f"outside-{linked_name}"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(DecisionPagesTruthError, match="symlink path component"):
        project_report_index_action_truth(
            source_report_index_path=source_index,
            site_root=site_root,
        )


@pytest.mark.parametrize("linked_component", ["outputs", "decision"])
def test_site_projection_rejects_symlinked_directory_component(
    tmp_path: Path,
    linked_component: str,
) -> None:
    source_root = tmp_path / "source"
    source_index = _write_action_index(
        source_root,
        [_report_entry("20260824", False)],
    )
    site_root = tmp_path / "_site"
    outside = tmp_path / "outside"
    (outside / "decision").mkdir(parents=True)
    if linked_component == "outputs":
        site_root.mkdir()
        (site_root / "outputs").symlink_to(outside, target_is_directory=True)
    else:
        (site_root / "outputs").mkdir(parents=True)
        (site_root / "outputs" / "decision").symlink_to(
            outside / "decision", target_is_directory=True
        )

    with pytest.raises(DecisionPagesTruthError, match="symlink path component"):
        project_report_index_action_truth(
            source_report_index_path=source_index,
            site_root=site_root,
        )


def test_site_projection_rejects_symlinked_output_index_before_write(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_index = _write_action_index(
        source_root,
        [_report_entry("20260824", False)],
    )
    site_root = tmp_path / "_site"
    _write_site_report_pair(site_root, "20260824", "20260821")
    outside_index = tmp_path / "outside-index.json"
    outside_index.write_text("do not overwrite\n", encoding="utf-8")
    site_index = site_root / "outputs" / "decision" / "report_index.json"
    site_index.symlink_to(outside_index)

    with pytest.raises(DecisionPagesTruthError, match="symlink path component"):
        project_report_index_action_truth(
            source_report_index_path=source_index,
            site_root=site_root,
        )
    assert outside_index.read_text(encoding="utf-8") == "do not overwrite\n"


def test_action_truth_validator_rejects_index_path_outside_site_root(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "_site"
    (site_root / "outputs" / "decision").mkdir(parents=True)
    outside_index = _write_action_index(
        tmp_path / "outside",
        [_report_entry("20260824", False)],
    )

    with pytest.raises(DecisionPagesTruthError, match="escapes or is not the exact"):
        validate_report_index_action_truth(
            report_index_path=outside_index,
            site_root=site_root,
        )


def test_pages_workflow_wires_and_verifies_prospective_truth_fields() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    push_header = text.split("schedule:", 1)[0]
    assert "scripts/decision_pages_truth.py" in push_header
    assert "data/market/trade_cal_sse.csv" in push_header
    assert "assess_decision_pages_truth" in text
    for field in ("signal_date", "prospective", "freshness_state"):
        assert f'"{field}":' in text
    for variable in (
        "EXPECTED_SIGNAL_DATE",
        "EXPECTED_PROSPECTIVE",
        "EXPECTED_FRESHNESS_STATE",
    ):
        assert variable in text
    assert 'revision.get("signal_date") != expected_signal_date' in text
    assert 'revision.get("prospective") is not expected_prospective' in text
    assert 'revision.get("freshness_state") != expected_freshness_state' in text
    assert "validate_report_index_action_truth" in text
    assert "project_report_index_action_truth" in text
    assert 'source_report_index_path=source_index_path' in text
    assert 'site_root=Path("_site")' in text
