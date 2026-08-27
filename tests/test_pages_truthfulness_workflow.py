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


def test_generic_pages_cannot_relabel_an_old_report_as_today() -> None:
    text = _workflow()
    assert 'report_date = str(report_index.get("latest_report_date", "")).strip()' in text
    assert '"report_date": report_date' in text
    assert "report_date = today" not in text
    assert "report_date=today" not in text
    assert "schedule:" not in text.split("\npermissions:", 1)[0]


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
    assert "push:" in header and "workflow_dispatch:" in header
    assert "schedule:" not in header
    assert "group: dc20-pages" in text
    assert "cancel-in-progress: false" in text
    assert "github.ref == 'refs/heads/main'" in text
    checkout = text.split("actions/checkout@", 1)[1].split("actions/configure-pages@", 1)[0]
    assert "ref: main" not in checkout
    assert "ref: ${{ inputs.expected_head || github.sha }}" in checkout
    assert "EXPECTED_HEAD: ${{ inputs.expected_head || github.sha }}" in checkout
    assert "echo \"${EXPECTED_HEAD}\" | grep -Eq '^[0-9a-f]{40}$'" in checkout
    assert 'test "${actual}" = "${EXPECTED_HEAD}"' in checkout
    assert 'HEAD_SHA="$(git rev-parse HEAD)"' in text


def test_primary_owned_commits_skip_only_the_generic_push_deployer() -> None:
    text = _workflow()
    job_header = text.split("    environment:", 1)[0]
    assert "github.ref == 'refs/heads/main'" in job_header
    assert "github.event_name == 'push'" in job_header
    for marker in ("[dc20-p0-pages-owned]", "[dc20-p1-pages-owned]"):
        assert f"contains(github.event.head_commit.message, '{marker}')" in job_header
    assert "workflow_call:" in text.split("\npermissions:", 1)[0]
    assert "workflow_dispatch: {}" in text.split("\npermissions:", 1)[0]
    assert "schedule:" not in text.split("\npermissions:", 1)[0]


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
    assert 'research_kind = latest_row.get("research_kind", "")' in text
    assert 'if research_kind == "dc20_independent":' in text
    assert 'elif research_kind in {"legacy_daily", "historical_archive"}:' in text
    assert 'f"outputs/decision/research_context_dc20_{report_date}.json"' in text
    assert 'f"outputs/decision/research_context_{report_date}.json"' in text
    assert 'raise SystemExit("latest research_kind is unsupported")' in text
    assert 'if research_url or research_kind:' in text
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


def test_pages_quarantines_invalid_legacy_profit_research_before_copy() -> None:
    text = _workflow()
    validation_step = text.split(
        "- name: Validate optional legacy-profit relative research chain", 1
    )[1].split("- uses: actions/configure-pages@", 1)[0]
    site_step = text.split("- name: Build isolated DC2.0 Decision site", 1)[1]

    assert "validate_repository_chain(root)" in validation_step
    assert "OUTPUT_ROOT" in validation_step
    assert "publish = False" in validation_step
    assert "except Exception as exc:" in validation_step
    assert "Legacy profit research quarantined" in validation_step
    assert "handle.write(f'publish={str(publish).lower()}\\n')" in validation_step

    assert (
        "PUBLISH_LEGACY_PROFIT_RELATIVE: "
        "${{ steps.legacy_profit_relative.outputs.publish }}"
    ) in site_step
    build_step = site_step.split(
        "      - uses: actions/upload-pages-artifact@", 1
    )[0]
    site_run = build_step.split("        run: |\n", 1)[1]
    assert "${{" not in site_run
    assert "os.environ.get('PUBLISH_LEGACY_PROFIT_RELATIVE') == 'true'" in site_run
    assert "optional_name = 'legacy_profit_relative_research'" in site_step
    assert "if child.name == optional_name:" in site_step
    assert "if publish_optional:" in site_step
    assert "shutil.copytree(optional_source, destination / optional_name)" in site_step
    assert "rm -rf" not in site_step

    validation_position = text.index("validate_repository_chain(root)")
    copy_position = text.index("source = Path('outputs/decision').resolve(strict=True)")
    assert validation_position < copy_position


def test_pages_accepts_primary_profit_only_through_complete_shared_p0_lineage() -> None:
    text = _workflow()
    validation_step = text.split(
        "- name: Validate optional legacy-profit relative research chain", 1
    )[1].split("- uses: actions/configure-pages@", 1)[0]
    site_step = text.split("- name: Build isolated DC2.0 Decision site", 1)[1]
    public_step = text.split(
        "- name: Verify public primary-profit bundle when present", 1
    )[1].split("- name: Verify public DC2.0 Pages revision", 1)[0]

    assert "index.get('schema_version') == SINGLE_INDEX_SCHEMA" in validation_step
    assert "bundle = validate_primary_profit_bundle(root)" in validation_step
    assert "contract = 'primary'" in validation_step
    assert "except Exception as exc:" in validation_step
    assert validation_step.index("validate_primary_profit_bundle(root)") < validation_step.index(
        "except Exception as exc:"
    )

    assert "candidate_index.get('schema_version') == MIXED_INDEX_SCHEMA" in site_step
    assert "bundle = validate_primary_profit_bundle(repo_root)" in site_step
    assert "PRIMARY_PROFIT_CONTRACT_PATH" in site_step
    assert "public_bundle = validate_primary_profit_bundle(" in site_step
    assert "expected_generation_mode=bundle['inputs'].generation_mode" in site_step
    assert "_validate_existing_index_chain(repo_root, index)" in site_step
    assert "top10-decision" not in site_step

    for token in (
        "revision.json",
        "models/decision_primary_profit_research_contract.json",
        'primary_d_receipt_${SIGNAL_DATE}.json',
        'primary_d_runtime_features_${SIGNAL_DATE}.csv',
        'three_rank_top10_${SIGNAL_DATE}.json',
        'three_rank_top10_${SIGNAL_DATE}.csv',
        "outputs/decision/legacy_profit_relative_research/index.json",
        "outputs/decision/executable_profit_research/index.json",
        "cmp -s \"_site/${relative}\" \"${public_root}/${relative}\"",
        "validate_primary_profit_bundle(",
        "expected_signal_date=os.environ['SIGNAL_DATE']",
        "public generic revision is not bound to the complete P1 bundle",
    ):
        assert token in public_step

    for field in (
        "primary_profit_status",
        "primary_profit_generation_mode",
        "primary_profit_candidate_count",
        "primary_profit_top10_members_sha256",
        "primary_single_profit_projection_sha256",
        "primary_mixed_profit_projection_sha256",
        "primary_profit_source_bundle_sha256",
        "primary_profit_source_feature_snapshot_sha256",
    ):
        assert f'"{field}":' in site_step
        assert f"EXPECTED_{field.upper()}" in text
        assert f"revision.get('{field}')" in text or f"revision.get(\n                              '{field}'" in text
    assert "unsupported executable-profit research index schema" in site_step


def test_legacy_profit_research_code_changes_trigger_pages_validation() -> None:
    text = _workflow()
    push_header = text.split("schedule:", 1)[0]
    assert (
        "src/top10decision/decision/legacy_profit_relative_research.py"
        in push_header
    )
    assert (
        "scripts/project_decision_legacy_profit_relative_research.py"
        in push_header
    )
    assert "scripts/publish_primary_profit_rankings.py" in push_header
    assert "models/decision_primary_profit_research_contract.json" in push_header


def test_dashboard_never_falls_back_to_an_unrelated_latest_action() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "info.action_available === true && info.action_url" in text
    assert 'String(plan.report_date || "") !== String(info.report_date || "")' in text
    assert "latest_action_report_date" in text
    assert "latestActionDate === String(info.report_date || \"\")" in text
    assert 'fetchPath("outputs/decision/action_plan_latest.json")' not in text
    assert "action_plan_${info.report_date}" not in text
    assert 'String(cached.plan.report_date || "") !== String(cached.info?.report_date || "")' in text
