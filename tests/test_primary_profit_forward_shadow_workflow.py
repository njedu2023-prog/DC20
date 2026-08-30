from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/run_primary_profit_forward_shadow.yml"
PAGES_WORKFLOW = ROOT / ".github/workflows/deploy_dc20_pages.yml"
FREEZER = ROOT / "scripts/freeze_primary_profit_forward_shadow.py"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_freezer_exposes_repository_root_before_importing_p1_validator() -> None:
    text = FREEZER.read_text(encoding="utf-8")
    root_bootstrap = "if str(ROOT) not in sys.path:"
    bridge_import = (
        "from top10decision.decision.primary_profit_forward_shadow_bridge import"
    )
    assert root_bootstrap in text
    assert text.index(root_bootstrap) < text.index(bridge_import)


def test_bridge_listens_only_to_successful_exact_p1_and_shares_writer() -> None:
    text = _text()
    assert "DC2.0 · Publish Primary Profit Rankings (P1)" in text
    assert "workflow_id == 343703610" in text
    assert "run_primary_profit_rankings.yml" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.run_attempt == 1" in text
    assert "group: decision-auction-main-writer" in text
    assert "cancel-in-progress: false" in text
    assert "confirm_prospective" in text
    assert "real prospective dispatch requires explicit confirmation" in text
    assert "reusable_suffix=' / deploy'" in text
    assert "raw_name.endswith(reusable_suffix)" in text


def test_bridge_calls_core_freezer_and_does_not_duplicate_scoring_or_schema() -> None:
    text = _text()
    assert (
        "python scripts/freeze_primary_profit_forward_shadow.py \\\n"
        '            --signal-date "${SIGNAL_DATE}"'
    ) in text
    assert "validate_primary_profit_forward_shadow_repository_chain" in text
    assert "run_decision_executable_profit_forward_shadow.py" not in text
    assert "internal_forward_challenger.pkl" not in text
    assert "research_joint_proxy_score" not in text
    assert "dc20_primary_profit_forward_shadow_public_state_v1" not in text
    assert "dc20_primary_profit_forward_shadow_public_index_v1" not in text


def test_bridge_is_sidecar_only_and_preserves_p1_action_and_legacy_pointer() -> None:
    text = _text()
    for path in (
        "outputs/decision/executable_profit_research/index.json",
        "outputs/decision/executable_profit_research/projection_{d}.json",
        "outputs/decision/executable_profit_research/projection_{d}.csv",
        "data/decision_executable_profit/forward/selections/index.json",
        "outputs/decision/report_index.json",
    ):
        assert path in text
    assert "Path('outputs/decision').glob('action_plan_*.json')" in text
    assert "Shadow bridge changed P1 projection, legacy pointer, or Action bytes" in text
    for path in (
        "data/decision_executable_profit/forward/selections/primary_mixed_index.json",
        "outputs/decision/executable_profit_research/shadow_index.json",
        "outputs/decision/executable_profit_research/shadow_state_${SIGNAL_DATE}_asof_${SIGNAL_DATE}.json",
    ):
        assert path in text
    assert "[dc20-shadow-pages-owned]" in text
    assert "uses: ./.github/workflows/deploy_dc20_pages.yml" in text
    assert "expected_head: ${{ needs.publish.outputs.published_head }}" in text


def test_bridge_uses_exact_base_candidate_and_minimal_permissions() -> None:
    text = _text()
    assert "permissions:\n  contents: read" in text
    assert "persist-credentials: false" in text
    assert "base_sha.txt" in text
    assert "git apply --index --binary" in text
    assert "git fetch origin main" in text
    assert 'test "$(git rev-parse origin/main)" = "${expected}"' in text
    assert "contents: write" in text
    assert "pages: write" in text
    assert "id-token: write" in text


def test_bridge_accepts_an_already_complete_exact_d_as_a_noop() -> None:
    text = _text()
    assert "if not paths.issubset(expected):" in text
    assert "if paths and not required.issubset(paths):" in text
    assert "if not paths or not paths.issubset(expected):" not in text
    assert "has_changes=false" in text


def test_pages_push_filters_use_github_supported_globs() -> None:
    text = PAGES_WORKFLOW.read_text(encoding="utf-8")
    push_filters = text.split("  workflow_dispatch:", 1)[0]
    assert "?" not in push_filters
    for pattern in (
        "shadow_*.json",
        "shadow_*.csv",
        "t_verification_*.json",
        "settlement_*.json",
    ):
        assert pattern in push_filters
