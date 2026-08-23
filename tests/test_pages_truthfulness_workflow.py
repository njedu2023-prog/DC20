from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy_dc20_pages.yml"
DASHBOARD = ROOT / "decision.html"


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


def test_pages_supports_exact_head_reusable_handoff_and_event_commits() -> None:
    text = _workflow()
    header = text.split("\npermissions:", 1)[0]
    assert "workflow_call:" in header
    assert "expected_head:" in header
    workflow_call = header.split("workflow_call:", 1)[1]
    assert "required: true" in workflow_call
    assert "type: string" in workflow_call
    assert "workflow_run:" not in header
    assert "github.event.workflow_run" not in text
    assert "push:" in header and "schedule:" in header and "workflow_dispatch:" in header
    assert "group: dc20-pages" in text
    assert "cancel-in-progress: false" in text
    assert "if: ${{ github.ref == 'refs/heads/main' }}" in text
    checkout = text.split("actions/checkout@", 1)[1].split("actions/configure-pages@", 1)[0]
    assert "ref: main" not in checkout
    assert "ref: ${{ inputs.expected_head || github.sha }}" in checkout
    assert "EXPECTED_HEAD: ${{ inputs.expected_head || github.sha }}" in checkout
    assert "echo \"${EXPECTED_HEAD}\" | grep -Eq '^[0-9a-f]{40}$'" in checkout
    assert 'test "${actual}" = "${EXPECTED_HEAD}"' in checkout
    assert 'HEAD_SHA="$(git rev-parse HEAD)"' in text


def test_pages_projects_site_inventory_before_validating_or_selecting_latest() -> None:
    text = _workflow()
    projection = text.index("project_report_index_action_truth(")
    validation = text.index("action_truth = validate_report_index_action_truth(")
    selection = text.index("report_index = json.loads(index_path.read_text")
    assert projection < validation < selection
    assert 'source_index_path = Path("outputs/decision/report_index.json")' in text
    assert 'source_report_index_path=source_index_path' in text
    assert 'site_root=Path("_site")' in text
    assert '"report_index_inventory_projected": inventory_projected' in text
    assert '"source_index_latest_report_date": source_latest_report_date' in text
    assert (
        'inventory_projected and report_index.get("generated_at_utc") is not None'
        in text
    )
    assert (
        'public_index.get("generated_at_utc") != revision.get('
        in text
    )
    assert '"report_generated_at_utc": None' in text
    assert (
        '"report_index_generated_at_utc": report_index.get("generated_at_utc")'
        in text
    )
    assert 'revision.get("report_generated_at_utc") is not None' in text
    assert '"report_index_generated_at_utc"' in text
    assert (
        'public_index.get("inventory_projected") is not revision.get('
        in text
    )


def test_pages_projects_and_publicly_verifies_full_research_context() -> None:
    text = _workflow()
    assert 'research_available = latest_row.get("research_available", False)' in text
    assert 'expected_research_url = f"outputs/decision/research_context_{report_date}.json"' in text
    assert '"latest_research_available": research_available' in text
    assert '"latest_research_url": research_url' in text
    assert "EXPECTED_RESEARCH_AVAILABLE" in text
    assert "EXPECTED_RESEARCH_URL" in text
    assert 'json.loads(fetch_text(expected_research_url))' in text
    assert 'research_schema == "decision_research_context_v1_daily"' in text
    assert 'research_schema == "decision_research_context_v1_historical_parity"' in text
    assert 'public_research.get("action_authorized") is not False' in text
    assert 'public_research.get("formal_buy_count") != 0' in text
    assert 'row.get("action") != "WATCH"' in text
    assert 'base64.b64decode(' in text
    assert 'artifact_keys = {"action_plan", "decision_report", "evaluation"}' in text
    assert 'hashlib.sha256(raw).hexdigest() != artifact_binding.get("raw_sha256")' in text
    assert 'hashlib.sha1(git_header + raw).hexdigest() != artifact_binding.get("blob_sha")' in text
    assert 'decoded["action_plan"]' in text
    assert 'decoded["decision_report"]' in text
    assert 'decoded["evaluation"]' in text
    assert "public historical parity report heading is wrong" in text
    assert "public historical parity evaluation {field} binding is wrong" in text


def test_dashboard_never_falls_back_to_an_unrelated_latest_action() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "info.action_available === true && info.action_url" in text
    assert 'String(plan.report_date || "") !== String(info.report_date || "")' in text
    assert "latest_action_report_date" in text
    assert "latestActionDate === String(info.report_date || \"\")" in text
    assert 'fetchPath("outputs/decision/action_plan_latest.json")' not in text
    assert "action_plan_${info.report_date}" not in text
    assert 'String(cached.plan.report_date || "") !== String(cached.info?.report_date || "")' in text
