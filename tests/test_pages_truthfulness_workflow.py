from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy_dc20_pages.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_pages_revision_exposes_freeze_and_report_freshness_truth() -> None:
    text = _workflow()
    assert 'freeze_path = Path("models/decision_model_freeze.json")' in text
    assert 'freeze_active = freeze_manifest.get("active") is True' in text
    assert 'datetime.strptime(report_date, "%Y%m%d")' in text
    assert 'report_age_days = (today - report_day).days' in text
    for field in ("freeze_active", "report_age_days", "stale", "stale_reason"):
        assert f'"{field}":' in text


def test_inactive_expired_and_future_reports_are_explicitly_stale() -> None:
    text = _workflow()
    assert 'stale_reasons.append("freeze_inactive")' in text
    assert 'stale_reasons.append("report_expired")' in text
    assert 'stale_reasons.append("report_date_in_future")' in text
    assert 'stale_reason = "+".join(stale_reasons) if stale_reasons else "none"' in text
    assert 'MAX_REPORT_AGE_DAYS: "1"' in text


def test_stale_banner_is_identical_at_both_public_entry_points() -> None:
    text = _workflow()
    assert 'banner_id = "dc20-truthfulness-banner"' in text
    assert "只读历史快照｜禁止据此交易" in text
    assert 'Path("_site/index.html").write_text(entry_html' in text
    assert 'Path("_site/decision.html").write_text(entry_html' in text
    assert 'root_html != decision_html' in text


def test_public_verifier_exactly_checks_revision_fields_and_banner() -> None:
    text = _workflow()
    for name in (
        "EXPECTED_FREEZE_ACTIVE",
        "EXPECTED_REPORT_AGE_DAYS",
        "EXPECTED_STALE",
        "EXPECTED_STALE_REASON",
    ):
        assert name in text
    assert 'revision.get("freeze_active") is not expected_freeze_active' in text
    assert 'revision.get("report_age_days") != expected_report_age_days' in text
    assert 'revision.get("stale") is not expected_stale' in text
    assert 'revision.get("stale_reason") != expected_stale_reason' in text
    assert "public stale banner does not match revision.json" in text
    assert "fresh active page unexpectedly contains stale banner" in text


def test_schedule_cannot_relabel_an_old_report_as_today() -> None:
    text = _workflow()
    assert 'report_date = str(report_index.get("latest_report_date", "")).strip()' in text
    assert '"report_date": report_date' in text
    assert "report_date = today" not in text
    assert "report_date=today" not in text
    assert "schedule:" in text


def test_freeze_manifest_change_triggers_truthfulness_redeployment() -> None:
    text = _workflow()
    push_header = text.split("schedule:", 1)[0]
    assert "models/decision_model_freeze.json" in push_header


def test_pages_has_no_writer_workflow_run_trigger_and_push_uses_event_commit() -> None:
    text = _workflow()
    header = text.split("\npermissions:", 1)[0]
    assert "workflow_run:" not in header
    assert "github.event.workflow_run" not in text
    assert "push:" in header and "schedule:" in header and "workflow_dispatch:" in header
    assert "if: ${{ github.ref == 'refs/heads/main' }}" in text
    checkout = text.split("actions/checkout@", 1)[1].split("actions/configure-pages@", 1)[0]
    assert "ref: main" not in checkout
    assert 'HEAD_SHA="$(git rev-parse HEAD)"' in text
