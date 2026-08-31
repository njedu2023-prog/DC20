import ast
import copy
import csv
import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy_dc20_pages.yml"
DASHBOARD = ROOT / "decision.html"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _embedded_python_functions(name: str) -> list:
    lines = _workflow().splitlines()
    functions = []
    for index, line in enumerate(lines):
        if "python3 - <<'PY'" not in line and "python - <<'PY'" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for raw in lines[index + 1 :]:
            if raw.strip() == "PY" and len(raw) - len(raw.lstrip()) == indent:
                break
            body.append(raw[indent:])
        module = ast.parse("\n".join(body))
        for node in module.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                namespace = {
                    "csv": csv,
                    "hashlib": hashlib,
                    "io": io,
                    "PurePosixPath": PurePosixPath,
                    "re": re,
                }
                function_module = ast.Module(body=[node], type_ignores=[])
                exec(compile(function_module, str(WORKFLOW), "exec"), namespace)
                functions.append(namespace[name])
    return functions


def _primary_runtime_closure_args() -> dict:
    runtime_index_path = (
        ROOT / "outputs" / "decision" / "primary_d_runtime_index.json"
    )
    runtime_index = json.loads(runtime_index_path.read_text(encoding="utf-8"))
    signal_date = str(runtime_index["latest_signal_date"])
    receipt_path = (
        ROOT / "outputs" / "decision" / f"primary_d_receipt_{signal_date}.json"
    )
    runtime_path = (
        ROOT
        / "outputs"
        / "decision"
        / f"primary_d_runtime_features_{signal_date}.csv"
    )
    contract_path = (
        ROOT / "outputs" / "decision" / f"three_rank_top10_{signal_date}.json"
    )
    contract_csv_path = (
        ROOT / "outputs" / "decision" / f"three_rank_top10_{signal_date}.csv"
    )
    return {
        "receipt": json.loads(receipt_path.read_text(encoding="utf-8")),
        "receipt_bytes": receipt_path.read_bytes(),
        "runtime_index": runtime_index,
        "runtime_bytes": runtime_path.read_bytes(),
        "contract": json.loads(contract_path.read_text(encoding="utf-8")),
        "contract_bytes": contract_path.read_bytes(),
        "contract_csv_bytes": contract_csv_path.read_bytes(),
        "signal_date": signal_date,
        "exec_date": str(runtime_index["latest_exec_date"]),
        "exit_date": str(runtime_index["latest_exit_date"]),
    }


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
    report_summary = "<summary>Decision 报告正文</summary>"
    assert 'banner_id = "dc20-truthfulness-banner"' in text
    assert "以下报告正文是只读历史快照｜禁止据此交易" in text
    assert "entry_html.count(report_summary) != 1" in text
    assert "entry_html.replace(" in text
    assert "report_summary + banner" in text
    assert DASHBOARD.read_text(encoding="utf-8").count(report_summary) == 1
    assert 'style="position:sticky' not in text
    assert 'Path("_site/index.html").write_text(entry_html' in text
    assert 'Path("_site/decision.html").write_text(entry_html' in text
    assert 'root_html != decision_html' in text


def test_pages_copies_and_publicly_verifies_d28_observation_statistics() -> None:
    text = _workflow()
    relative = "outputs/auction_v3/metrics/observation_cumulative_latest.json"
    assert f"- {relative}" in text.split("\npermissions:", 1)[0]
    assert f"'{relative}'" in text
    assert "statistics_target.read_bytes() != statistics_source.read_bytes()" in text
    assert "public_observation_statistics != local_observation_statistics" in text
    assert "statistics.get('public_start_signal_date') != '20260828'" in text
    assert "forward.get('start_signal_date') != '20260828'" in text
    assert "observation_statistics.get('public_start_signal_date') != '20260828'" in text
    assert "forward_observation.get('start_signal_date') != '20260828'" in text


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


def test_pages_revision_is_primary_d_first_and_keeps_report_body_audit_separate() -> None:
    text = _workflow()
    assert "validate_three_rank_index_truth(" in text
    assert "signal_date = str(primary_contract['signal_date'])" in text
    assert "report_signal_date = truth.signal_date" in text
    assert '"schema_version": "decision_pages_revision_v4_primary_first"' in text
    for field in (
        "primary_d_signal_date",
        "primary_d_exec_date",
        "primary_d_exit_date",
        "primary_d_status",
        "primary_d_generation_mode",
        "primary_d_bundle_sha256",
        "primary_d_top10_count",
        "primary_d_receipt_url",
        "report_signal_date",
    ):
        assert f'"{field}":' in text
    assert '"freshness_scope": "report_body_only"' in text
    assert '"report_freshness_state": freshness_state' in text
    assert '"report_stale": stale' in text
    assert '"report_stale_reason": stale_reason' in text
    assert "primary_inputs.signal_date > signal_date" in text
    assert "same-D primary-profit membership drifted" in text


def test_public_verifier_checks_exact_primary_d_bytes_and_two_date_domains() -> None:
    text = _workflow()
    for name in (
        "EXPECTED_PRIMARY_EXEC_DATE",
        "EXPECTED_PRIMARY_EXIT_DATE",
        "EXPECTED_PRIMARY_GENERATION_MODE",
        "EXPECTED_PRIMARY_BUNDLE_SHA256",
        "EXPECTED_PRIMARY_TOP10_COUNT",
        "EXPECTED_PRIMARY_RECEIPT_URL",
        "EXPECTED_REPORT_SIGNAL_DATE",
    ):
        assert name in text
    for path in (
        'primary_index_url = "outputs/decision/three_rank_index.json"',
        'f"{expected_signal_date}.json"',
        'f"{expected_signal_date}.csv"',
        '"outputs/decision/primary_d_runtime_index.json"',
        '"outputs/decision/primary_d_runtime_features_"',
    ):
        assert path in text
    assert "public Primary-D bytes differ from exact build" in text
    assert "public Primary-D index/receipt/bundle binding is invalid" in text
    assert 'revision.get("primary_d_signal_date")' in text
    assert 'revision.get("report_signal_date")' in text
    assert (
        'str(public_research.get("signal_date")) != expected_report_signal_date'
        in text
    )
    assert (
        'str(public_eval.get("signal_date")) != expected_report_signal_date'
        in text
    )
    assert (
        'str(legacy.get("signal_date")) != expected_report_signal_date'
        in text
    )


@pytest.mark.parametrize(
    "function_name",
    (
        "validate_primary_d_runtime_closure",
        "validate_public_primary_d_runtime_closure",
    ),
)
def test_primary_d_runtime_closure_accepts_current_exact_release(
    function_name: str,
) -> None:
    functions = _embedded_python_functions(function_name)
    assert len(functions) == 1
    functions[0](**_primary_runtime_closure_args())


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("runtime_dependency_on_top10_decision", True),
        ("future_market_data_consumed", True),
        ("latest_fallback_used", True),
        (
            "secondary_outputs_generated",
            {
                "action_plan": False,
                "big_loss": False,
                "profit": True,
                "p_fill_shadow": False,
                "executable_profit": False,
            },
        ),
    ),
)
@pytest.mark.parametrize(
    "function_name",
    (
        "validate_primary_d_runtime_closure",
        "validate_public_primary_d_runtime_closure",
    ),
)
def test_primary_d_runtime_closure_rejects_receipt_semantic_drift(
    function_name: str,
    field: str,
    bad_value: object,
) -> None:
    function = _embedded_python_functions(function_name)[0]
    arguments = _primary_runtime_closure_args()
    arguments["receipt"] = copy.deepcopy(arguments["receipt"])
    arguments["receipt"][field] = bad_value
    with pytest.raises(ValueError):
        function(**arguments)


@pytest.mark.parametrize(
    "function_name",
    (
        "validate_primary_d_runtime_closure",
        "validate_public_primary_d_runtime_closure",
    ),
)
def test_primary_d_runtime_closure_rejects_runtime_index_or_bytes_drift(
    function_name: str,
) -> None:
    function = _embedded_python_functions(function_name)[0]

    bad_index_arguments = _primary_runtime_closure_args()
    bad_index_arguments["runtime_index"] = copy.deepcopy(
        bad_index_arguments["runtime_index"]
    )
    bad_index_arguments["runtime_index"]["latest_runtime_features_url"] = (
        "outputs/decision/primary_d_runtime_features_20260827.csv"
    )
    with pytest.raises(ValueError):
        function(**bad_index_arguments)

    bad_bytes_arguments = _primary_runtime_closure_args()
    bad_bytes_arguments["runtime_bytes"] += b"\n"
    with pytest.raises(ValueError):
        function(**bad_bytes_arguments)


@pytest.mark.parametrize(
    "function_name",
    (
        "validate_primary_d_runtime_closure",
        "validate_public_primary_d_runtime_closure",
    ),
)
def test_primary_d_runtime_closure_rejects_hash_bound_wrong_d_csv(
    function_name: str,
) -> None:
    function = _embedded_python_functions(function_name)[0]
    arguments = _primary_runtime_closure_args()
    runtime_text = arguments["runtime_bytes"].decode("utf-8")
    signal_date = arguments["signal_date"]
    wrong_signal_date = "19000101" if signal_date != "19000101" else "19000102"
    wrong_d_runtime = runtime_text.replace(
        f"{signal_date},", f"{wrong_signal_date},", 1
    ).encode("utf-8")
    wrong_d_sha = hashlib.sha256(wrong_d_runtime).hexdigest()
    arguments["runtime_bytes"] = wrong_d_runtime
    arguments["receipt"] = copy.deepcopy(arguments["receipt"])
    arguments["receipt"]["outputs"]["runtime_features_sha256"] = wrong_d_sha
    arguments["runtime_index"] = copy.deepcopy(arguments["runtime_index"])
    arguments["runtime_index"]["latest_runtime_features_sha256"] = wrong_d_sha
    with pytest.raises(
        ValueError,
        match="another D|closure (?:is invalid|failed)",
    ):
        function(**arguments)


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


def test_pages_hides_grandfathered_shadow_cumulative_chain_but_keeps_exact_d_slots() -> None:
    text = _workflow()
    site_step = text.split("- name: Build isolated DC2.0 Decision site", 1)[1]
    public_step = text.split(
        "- name: Verify public primary-profit bundle when present", 1
    )[1].split("- name: Verify public DC2.0 Pages revision", 1)[0]
    grandfather = site_step.split(
        "if chain_status == 'GRANDFATHERED_PRE_CUTOVER_HIDDEN':", 1
    )[1].split("elif chain_status == 'D28_CUTOVER_VALID':", 1)[0]
    standard = site_step.split(
        "elif chain_status == 'D28_CUTOVER_VALID':", 1
    )[1].split("else:", 1)[0]

    assert "PRIMARY_SHADOW_PUBLIC_INDEX_PATH" in grandfather
    assert "state_relative" in grandfather
    assert "statistics_relative" in grandfather
    assert "target.unlink()" in grandfather
    assert "signal_date != '20260828'" in grandfather
    assert "selected_slots != 2" in grandfather
    assert "dc20_primary_profit_forward_shadow_cutover_index_v1" in grandfather
    assert "primary_profit_forward_shadow_selection_only_cutover" in grandfather
    for field in (
        "public_start_signal_date",
        "mixed_projection_sha256",
        "snapshot_sha256",
        "selection_identity_sha256",
        "csv_sha256",
        "boundaries",
    ):
        assert f"'{field}'" in grandfather

    assert "Pages standard Shadow summary lacks D28 cutover marker" in standard
    assert "grandfather_target = site_root / grandfather_state_relative" in standard
    assert "grandfather_target.unlink()" in standard
    assert "cutover_target.unlink()" in standard
    assert "shadow_state_20260828_asof_20260828.json" in site_step

    assert "shadow_cutover_index" in public_step
    assert "grandfathered Shadow artifact remained public" in public_step
    assert "selection-only cutover pointer remained beside standard Shadow chain" in public_step
    assert "grandfathered Shadow state remained public" in public_step
    assert "public primary Shadow has neither a standard nor cutover pointer" in public_step
    assert "public standard Shadow summary lacks D28 cutover" in public_step
    assert "public primary Shadow cutover surface/path drifted" in public_step
    assert "validate_primary_profit_forward_shadow_index(primary_index)" in public_step
    assert "validate_primary_profit_forward_shadow(" in public_step
    assert "selection_payload.get('snapshot_sha256')" in public_step
    assert "selection_payload.get('selection_identity_sha256')" in public_step
    assert "selection_payload.get('top10_members_sha256')" in public_step


def test_pages_shadow_policy_allows_recovery_without_shadow_but_keeps_natural_strict() -> None:
    functions = _embedded_python_functions(
        "resolve_primary_shadow_publication_policy"
    )
    assert len(functions) == 1
    resolve = functions[0]
    assert (
        resolve(
            generation_mode="RETROSPECTIVE_RECOVERY",
            signal_date="20260831",
            public_shadow_pointer_exists=True,
            public_shadow_signal_date="20260828",
            selection_index_pointer_exists=True,
            selection_index_signal_date="20260828",
            same_d_shadow_json_exists=False,
            same_d_shadow_csv_exists=False,
        )
        == "OMIT_RETROSPECTIVE_SHADOW"
    )
    assert (
        resolve(
            generation_mode="NATURAL",
            signal_date="20260831",
            public_shadow_pointer_exists=True,
            public_shadow_signal_date="20260828",
            selection_index_pointer_exists=True,
            selection_index_signal_date="20260828",
            same_d_shadow_json_exists=False,
            same_d_shadow_csv_exists=False,
        )
        == "OMIT_NATURAL_PENDING_SHADOW"
    )
    assert (
        resolve(
            generation_mode="NATURAL",
            signal_date="20260831",
            public_shadow_pointer_exists=True,
            public_shadow_signal_date="20260831",
            selection_index_pointer_exists=True,
            selection_index_signal_date="20260831",
            same_d_shadow_json_exists=True,
            same_d_shadow_csv_exists=True,
        )
        == "REQUIRE_SAME_D_SHADOW"
    )
    with pytest.raises(ValueError, match="must not publish same-D Shadow"):
        resolve(
            generation_mode="RETROSPECTIVE_RECOVERY",
            signal_date="20260831",
            public_shadow_pointer_exists=True,
            public_shadow_signal_date="20260831",
            selection_index_pointer_exists=True,
            selection_index_signal_date="20260831",
            same_d_shadow_json_exists=True,
            same_d_shadow_csv_exists=True,
        )
    invalid_states = (
        {
            "public_shadow_signal_date": "20260831",
            "selection_index_signal_date": "20260828",
        },
        {
            "public_shadow_signal_date": "20260901",
            "selection_index_signal_date": "20260901",
        },
        {"same_d_shadow_json_exists": True},
        {
            "same_d_shadow_json_exists": True,
            "same_d_shadow_csv_exists": True,
        },
    )
    for drift in invalid_states:
        natural_pending = {
            "generation_mode": "NATURAL",
            "signal_date": "20260831",
            "public_shadow_pointer_exists": True,
            "public_shadow_signal_date": "20260828",
            "selection_index_pointer_exists": True,
            "selection_index_signal_date": "20260828",
            "same_d_shadow_json_exists": False,
            "same_d_shadow_csv_exists": False,
        }
        natural_pending.update(drift)
        with pytest.raises(ValueError):
            resolve(**natural_pending)

    assert (
        resolve(
            generation_mode="NATURAL",
            signal_date="20260831",
            public_shadow_pointer_exists=False,
            public_shadow_signal_date=None,
            selection_index_pointer_exists=False,
            selection_index_signal_date=None,
            same_d_shadow_json_exists=False,
            same_d_shadow_csv_exists=False,
        )
        == "OMIT_NATURAL_PENDING_SHADOW"
    )
    with pytest.raises(ValueError, match="existence/date binding is invalid"):
        resolve(
            generation_mode="NATURAL",
            signal_date="20260831",
            public_shadow_pointer_exists=True,
            public_shadow_signal_date=None,
            selection_index_pointer_exists=True,
            selection_index_signal_date=None,
            same_d_shadow_json_exists=False,
            same_d_shadow_csv_exists=False,
        )
    with pytest.raises(ValueError, match="pointer inventory is mixed"):
        resolve(
            generation_mode="NATURAL",
            signal_date="20260831",
            public_shadow_pointer_exists=True,
            public_shadow_signal_date="20260828",
            selection_index_pointer_exists=False,
            selection_index_signal_date=None,
            same_d_shadow_json_exists=False,
            same_d_shadow_csv_exists=False,
        )
    with pytest.raises(ValueError, match="existence/date binding is invalid"):
        resolve(
            generation_mode="NATURAL",
            signal_date="20260831",
            public_shadow_pointer_exists=False,
            public_shadow_signal_date="20260828",
            selection_index_pointer_exists=False,
            selection_index_signal_date="20260828",
            same_d_shadow_json_exists=False,
            same_d_shadow_csv_exists=False,
        )


def test_pages_recovery_removes_only_generated_public_shadow_surfaces(
    tmp_path: Path,
) -> None:
    functions = _embedded_python_functions(
        "remove_retrospective_shadow_public_surfaces"
    )
    assert len(functions) == 1
    remove = functions[0]
    shadow_root = (
        tmp_path / "outputs" / "decision" / "executable_profit_research"
    )
    shadow_root.mkdir(parents=True)
    stale = (
        "shadow_index.json",
        "shadow_cutover_index.json",
        "shadow_state_20260828_asof_20260828.json",
        "shadow_statistics_20260828_asof_20260828.json",
        "shadow_state_20260831_asof_20260901.json",
        "shadow_statistics_20260831_asof_20260901.json",
    )
    for name in stale:
        (shadow_root / name).write_text("{}\n", encoding="utf-8")
    current_projection = shadow_root / "projection_20260831.json"
    current_projection.write_text('{"signal_date":"20260831"}\n', encoding="utf-8")
    unrelated_observation = (
        tmp_path / "outputs" / "auction_v3" / "metrics" / "observation.json"
    )
    unrelated_observation.parent.mkdir(parents=True)
    unrelated_observation.write_text('{"start":"20260828"}\n', encoding="utf-8")

    removed = remove(tmp_path)
    assert set(removed) == {
        f"outputs/decision/executable_profit_research/{name}" for name in stale
    }
    assert current_projection.is_file()
    assert unrelated_observation.is_file()
    assert not any((shadow_root / name).exists() for name in stale)


def test_pages_public_removed_shadow_inventory_is_closed_and_path_safe() -> None:
    functions = _embedded_python_functions(
        "validate_removed_shadow_surface_inventory"
    )
    assert len(functions) == 1
    validate = functions[0]
    inventory = [
        "outputs/decision/executable_profit_research/shadow_index.json",
        (
            "outputs/decision/executable_profit_research/"
            "shadow_state_20260828_asof_20260828.json"
        ),
        (
            "outputs/decision/executable_profit_research/"
            "shadow_statistics_20260828_asof_20260828.json"
        ),
    ]
    inventory.sort()
    assert validate(inventory, "OMIT_NATURAL_PENDING_SHADOW") == tuple(
        inventory
    )
    assert validate([], "REQUIRE_SAME_D_SHADOW") == ()
    for invalid in (
        list(reversed(inventory)),
        inventory + [inventory[-1]],
        ["outputs/decision/executable_profit_research/projection_20260831.json"],
        ["outputs/decision/executable_profit_research/../shadow_index.json"],
    ):
        with pytest.raises(ValueError):
            validate(invalid, "OMIT_RETROSPECTIVE_SHADOW")
    with pytest.raises(ValueError, match="removed active surfaces"):
        validate(inventory, "REQUIRE_SAME_D_SHADOW")


def test_pages_recovery_public_verifier_requires_shadow_surfaces_to_be_absent() -> None:
    text = _workflow()
    site_step = text.split("- name: Build isolated DC2.0 Decision site", 1)[1]
    public_step = text.split(
        "- name: Verify public primary-profit bundle when present", 1
    )[1].split("- name: Verify public DC2.0 Pages revision", 1)[0]
    assert "P1 retrospective recovery must not publish same-D Shadow" in site_step
    assert "P1 same-D Shadow pointer/files are mixed" in site_step
    assert "OMIT_NATURAL_PENDING_SHADOW" in site_step
    assert "PUBLIC_PRIMARY_SHADOW_PENDING_NATURAL" in site_step
    assert "PUBLIC_PRIMARY_SHADOW_OMITTED_RETROSPECTIVE" in site_step
    assert "remove_retrospective_shadow_public_surfaces(site_root)" in site_step
    assert "primary_shadow_removed_surfaces_json=" in site_step
    assert "GENERATION_MODE: ${{ steps.site.outputs.primary_profit_generation_mode }}" in public_step
    assert (
        "EXPECTED_REMOVED_SHADOW_SURFACES_JSON: "
        "${{ steps.site.outputs.primary_shadow_removed_surfaces_json }}"
        in public_step
    )
    assert "validate_removed_shadow_surface_inventory(" in public_step
    assert 'done < "${removed_shadow_surfaces_file}"' in public_step
    assert "removed Shadow surface remained public" in public_step
    assert "P1 pending-Shadow publication exposed a Shadow surface" in public_step
    assert "public P1 pending-Shadow state exposed or authorized Shadow" in public_step


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
