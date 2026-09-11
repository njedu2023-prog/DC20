from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/run_primary_profit_forward_shadow.yml"
PAGES_WORKFLOW = ROOT / ".github/workflows/deploy_dc20_pages.yml"
FREEZER = ROOT / "scripts/freeze_primary_profit_forward_shadow.py"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow_function(name):
    lines = _text().splitlines()
    for index, line in enumerate(lines):
        if "python3 - <<'PY'" not in line:
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
                namespace = {"Path": Path, "json": json, "hashlib": hashlib, "re": re}
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(WORKFLOW), "exec"), namespace)
                return namespace[node.name]
    raise AssertionError(f"Shadow workflow function is missing: {name}")


def _candidate_paths():
    return _workflow_function("exact_shadow_candidate_paths")


def _candidate_fixture(root: Path, *, versioned=True):
    d = "20260911"
    public = "outputs/decision/executable_profit_research/"
    statistics = "data/decision_executable_profit/forward/statistics/"

    def write(relative, raw):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    summary_bytes = b'{"as_of_date":"20260911","fixture":"current"}\n'
    summary_sha = hashlib.sha256(summary_bytes).hexdigest()
    summary_relative = statistics + "summary.json"
    write(summary_relative, summary_bytes)
    statistics_relative = (
        statistics + f"snapshots/summary_asof_{d}_sha256_{summary_sha}.json"
        if versioned else summary_relative
    )
    statistics_path = write(statistics_relative, summary_bytes)
    snapshot_sha = "a" * 64
    state_relative = public + f"shadow_state_{d}_asof_{d}"
    state_relative += f"_sha256_{snapshot_sha}.json" if versioned else ".json"
    state = {
        "signal_date": d, "as_of_date": d, "snapshot_sha256": snapshot_sha,
        "source_bindings": {"statistics": {
            "path": statistics_relative, "sha256": summary_sha, "as_of_date": d,
        }},
    }
    state_bytes = json.dumps(state, sort_keys=True).encode()
    state_path = write(state_relative, state_bytes)
    index = {
        "latest_signal_date": d, "latest_as_of_date": d,
        "latest_state_url": state_relative,
        "latest_state_snapshot_sha256": snapshot_sha,
        "latest_state_sha256": hashlib.sha256(state_bytes).hexdigest(),
    }
    index_path = write(public + "shadow_index.json", json.dumps(index).encode())
    chain = {
        "signal_date": d, "as_of_date": d, "public_state": state_path,
        "public_index": index_path, "statistics": statistics_path,
    }
    return d, chain, write


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
        "shadow_state_{d}_asof_{d}_sha256_{snapshot_sha}.json",
        "snapshots/summary_asof_{d}_sha256_{statistics_sha}.json",
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


def test_candidate_paths_follow_validated_chain_before_staging_and_protect_old_bytes() -> None:
    text = _text()
    candidate = text.split("- name: Build exact allowlisted Shadow candidate", 1)[1]
    assert candidate.index("chain=validate_primary_profit_forward_shadow_repository_chain(root,d)") < candidate.index(
        "expected,required=exact_shadow_candidate_paths(root,d,chain,previous_statistics)"
    ) < candidate.index("subprocess.run(['git','add','-A','--',*sorted(expected)],check=True)")
    for token in (
        "validate_statistics(summary)", "summary_sha=hashlib.sha256(summary_bytes).hexdigest()",
        "shadow-statistics-before.json", "'existed':archive_path in inventory",
        "verifications/t_verification_*.json", "settlements/settlement_*.json",
        "statistics/snapshots/summary_asof_*.json", "shadow_state_*.json",
        "after != before", "if not previous['existed']:",
    ):
        assert token in text
    assert "git add -A -- data/decision_executable_profit/forward/statistics" not in candidate


@pytest.mark.parametrize("versioned", [False, True])
def test_candidate_paths_keep_complete_exact_bundle_and_legacy_noop(tmp_path, versioned):
    d, chain, _write = _candidate_fixture(tmp_path, versioned=versioned)
    expected, required = _candidate_paths()(tmp_path, d, chain, None)
    assert expected == required
    assert len(expected) == (7 if versioned else 6)
    assert chain["public_state"].relative_to(tmp_path).as_posix() in expected
    assert chain["statistics"].relative_to(tmp_path).as_posix() in expected
    assert f"data/decision_executable_profit/forward/selections/shadow_{d}.json" in required
    assert f"data/decision_executable_profit/forward/selections/shadow_{d}.csv" in required
    validate = _workflow_function("validate_staged_shadow_candidate")
    validate(set(), expected, required)
    validate(required, expected, required)
    for omitted in required:
        incomplete = required - {omitted}
        with pytest.raises(SystemExit, match="incomplete"):
            validate(incomplete, expected, required)
    with pytest.raises(SystemExit, match="foreign paths"):
        validate(expected | {"outputs/decision/action_plan_latest.json"}, expected, required)


@pytest.mark.parametrize("existed", [False, True])
def test_candidate_only_allows_exact_pre_freeze_summary_archive(tmp_path, existed):
    d, chain, write = _candidate_fixture(tmp_path)
    raw = b'{"as_of_date":"20260910","fixture":"previous"}\n'
    digest = hashlib.sha256(raw).hexdigest()
    relative = (
        "data/decision_executable_profit/forward/statistics/snapshots/"
        f"summary_asof_20260910_sha256_{digest}.json"
    )
    write(relative, raw)
    previous = {"path": relative, "as_of_date": "20260910", "sha256": digest, "existed": existed}
    expected, required = _candidate_paths()(tmp_path, d, chain, previous)
    assert len(expected) == 8 and relative in expected
    assert (relative in required) is (not existed)
    assert chain["statistics"].relative_to(tmp_path).as_posix() in required
    validate = _workflow_function("validate_staged_shadow_candidate")
    validate(required, expected, required)
    if not existed:
        with pytest.raises(SystemExit, match="incomplete"):
            validate(required - {relative}, expected, required)
    for field, value in (
        ("path", relative.replace(digest, "f" * 64)), ("as_of_date", "20260912"),
        ("sha256", "f" * 63), ("existed", "false"),
    ):
        corrupted = dict(previous, **{field: value})
        with pytest.raises(ValueError, match="archive drifted"):
            _candidate_paths()(tmp_path, d, chain, corrupted)
    write(relative, raw + b" ")
    with pytest.raises(ValueError, match="archive drifted"):
        _candidate_paths()(tmp_path, d, chain, previous)


@pytest.mark.parametrize("drift", ["chain_date", "chain_path", "state_sha", "address", "summary", "snapshot"])
def test_candidate_rejects_unbound_dates_paths_and_bytes(tmp_path, drift):
    d, chain, write = _candidate_fixture(tmp_path)
    chain = copy.deepcopy(chain)
    if drift == "chain_date":
        chain["as_of_date"] = "20260910"
    elif drift == "chain_path":
        chain["statistics"] = chain["public_state"]
    elif drift in {"state_sha", "address"}:
        index = json.loads(chain["public_index"].read_bytes())
        if drift == "state_sha":
            index["latest_state_sha256"] = "f" * 64
        else:
            index["latest_state_url"] = "outputs/decision/executable_profit_research/../foreign.json"
        chain["public_index"].write_text(json.dumps(index), encoding="utf-8")
    elif drift == "summary":
        write("data/decision_executable_profit/forward/statistics/summary.json", b"{}")
    else:
        chain["statistics"].write_bytes(b"{}")
    with pytest.raises(ValueError):
        _candidate_paths()(tmp_path, d, chain, None)


def test_pages_push_filters_use_github_supported_globs() -> None:
    text = PAGES_WORKFLOW.read_text(encoding="utf-8")
    push_filters = text.split("  workflow_dispatch:", 1)[0]
    assert "?" not in push_filters
    for pattern in (
        "shadow_*.json",
        "shadow_*.csv",
        "t_verification_*.json",
        "settlement_*.json",
        "statistics/snapshots/summary_asof_*.json",
    ):
        assert pattern in push_filters
