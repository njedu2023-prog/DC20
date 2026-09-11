from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy_dc20_pages.yml"
WINDOW = "outputs/decision/compact_statistics_window.json"
CONFIG = "models/decision_compact_statistics_window_v1.json"
HEAD = "a" * 40
D = "20260910"


def _steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]["steps"]


def _embedded_function(name, *, builder, validator):
    for step in _steps():
        script = step.get("run", "")
        for body in re.findall(r"(?ms)^python3 - <<'PY'\n(.*?)^PY$", script):
            module = ast.parse(body)
            for node in module.body:
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    namespace = {
                        "Path": Path, "json": json, "hashlib": hashlib, "re": re,
                        "build_window": builder, "validate_window": validator,
                    }
                    exec(compile(ast.Module(body=[node], type_ignores=[]), str(WORKFLOW), "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"missing compact-window workflow function: {name}")


def _write(root, relative, raw):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _seal(payload):
    payload.pop("snapshot_sha256", None)
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


@pytest.fixture
def publication(tmp_path):
    # This suite isolates publication mechanics; the real builder/validator
    # integration below covers the production section schema and calculation.
    repo, site = tmp_path / "repo", tmp_path / "site"
    repo.mkdir()
    site.mkdir()
    config = _write(repo, CONFIG, b'{"fixture":"compact-window-config"}\n')
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    payload = _seal({
        "schema_version": "dc20_compact_statistics_window_v1",
        "start_signal_date": D, "report_signal_date": D, "config_sha256": digest,
        "source_files": [{"path": CONFIG, "sha256": digest}],
        "profit": {"status": "UNAVAILABLE", "reason": "SOURCE_MISSING"},
        "promotion": {"status": "UNAVAILABLE", "reason": "SOURCE_MISSING"},
    })
    revision = {"head_sha": HEAD, "signal_date": D, "primary_d_status": "READY", "sentinel": "unchanged"}
    _write(site, "revision.json", _json_bytes(revision))
    calls = []

    def builder(root, *, signal_date):
        calls.append((root, signal_date))
        return copy.deepcopy(payload)

    def validator(value):
        assert value == _seal(copy.deepcopy(value)), "window snapshot drifted"

    materialize = _embedded_function("materialize_compact_statistics_window", builder=builder, validator=validator)
    verify = _embedded_function("verify_public_compact_statistics_window", builder=builder, validator=validator)
    return repo, site, payload, revision, calls, materialize, verify


def test_window_steps_are_after_daily_build_before_upload_and_after_deployment():
    steps = _steps()
    names = [step.get("name", step.get("uses", "")) for step in steps]
    build = names.index("Build independent compact statistics window")
    upload = next(i for i, name in enumerate(names) if name.startswith("actions/upload-pages-artifact@"))
    assert names.index("Build isolated DC2.0 Decision site") < build < upload
    assert names.index("Deploy DC2.0 Pages") < names.index("Verify public compact statistics window")
    assert names.index("Verify public compact statistics window") < names.index("Verify public primary-profit bundle when present")
    text = WORKFLOW.read_text()
    for path in (CONFIG, "scripts/build_compact_statistics_window.py", "tests/test_compact_statistics_window.py", "tests/test_compact_statistics_window_pages.py"):
        assert f"      - {path}\n" in text.split("  workflow_dispatch:", 1)[0]
    for name in ("Build independent compact statistics window", "Verify public compact statistics window"):
        step = next(step for step in steps if step.get("name") == name)
        assert "continue-on-error" not in step and "if" not in step
        assert "set -euo pipefail" in step["run"]
        result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        for body in re.findall(r"(?ms)^python3 - <<'PY'\n(.*?)^PY$", step["run"]):
            compile(body, str(WORKFLOW), "exec")


def test_explicit_unavailable_is_published_without_changing_daily_or_old_statistics(publication):
    repo, site, payload, original, calls, materialize, verify = publication
    old = _write(repo, "data/decision_executable_profit/forward/statistics/summary.json", b"immutable-old-statistics")
    result = materialize(repo, site)
    assert result == payload and calls == [(repo.resolve(), D)]
    revision = json.loads((site / "revision.json").read_bytes())
    assert all(revision[key] == value for key, value in original.items())
    assert revision["compact_statistics_window_url"] == WINDOW
    assert revision["compact_statistics_window_start_signal_date"] == D
    assert revision["compact_statistics_window_sha256"] == hashlib.sha256((site / WINDOW).read_bytes()).hexdigest()
    assert (site / CONFIG).read_bytes() == (repo / CONFIG).read_bytes()
    assert old.read_bytes() == b"immutable-old-statistics"
    assert not (site / old.relative_to(repo)).exists()
    assert all(result[section] == {"status": "UNAVAILABLE", "reason": "SOURCE_MISSING"} for section in ("profit", "promotion"))
    fetched = []

    def fetch(path):
        fetched.append(path)
        return (site / path).read_bytes()

    assert verify(site, fetch, HEAD, D) == payload
    assert set(fetched) == {"revision.json", WINDOW, CONFIG}


def test_window_copies_only_exact_sha_named_inputs(publication):
    repo, site, payload, _, _, materialize, verify = publication
    relative = "data/market/raw/2026/20260911/daily.csv"
    raw = b"ts_code,trade_date,close\n600001.SH,20260911,10\n"
    _write(repo, relative, raw)
    _write(repo, "data/market/raw/2026/20260911/foreign.csv", b"do not publish")
    payload["source_files"].append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    _seal(payload)
    materialize(repo, site)
    assert (site / relative).read_bytes() == raw
    assert not (site / "data/market/raw/2026/20260911/foreign.csv").exists()
    assert verify(site, lambda path: (site / path).read_bytes(), HEAD, D) == payload


@pytest.mark.parametrize("relative", [
    "outputs/decision/executable_profit_research/shadow_index.json",
    "outputs/decision/executable_profit_research/shadow_cutover_index.json",
    "data/decision_executable_profit/forward/selections/primary_mixed_index.json",
    "data/decision_executable_profit/forward/statistics/summary.json",
    "models/model.pkl", "../outside.json", "/outside.json",
])
def test_window_never_exposes_mutable_shadow_pointers_or_foreign_sources(publication, relative):
    repo, site, payload, original, _, materialize, _ = publication
    payload["source_files"].append({"path": relative, "sha256": "a" * 64})
    _seal(payload)
    with pytest.raises(ValueError, match="allowlisted evidence"):
        materialize(repo, site)
    assert not (site / WINDOW).exists()
    assert json.loads((site / "revision.json").read_bytes()) == original


def test_window_cannot_restore_a_hidden_shadow_state(publication):
    repo, site, payload, _, _, materialize, _ = publication
    relative = "outputs/decision/executable_profit_research/shadow_state_20260910_asof_20260910_sha256_" + "a" * 64 + ".json"
    raw = b"{}"
    _write(repo, relative, raw)
    payload["source_files"].append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    _seal(payload)
    with pytest.raises(ValueError, match="hidden Shadow state"):
        materialize(repo, site)
    assert not (site / relative).exists() and not (site / WINDOW).exists()


def test_window_does_not_overwrite_an_existing_different_public_projection(publication):
    repo, site, payload, _, _, materialize, _ = publication
    relative = "outputs/decision/primary_observation/summary.json"
    raw = b"source"
    _write(repo, relative, raw)
    target = _write(site, relative, b"existing-public-projection")
    payload["source_files"].append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    _seal(payload)
    with pytest.raises(ValueError, match="different public source"):
        materialize(repo, site)
    assert target.read_bytes() == b"existing-public-projection"


@pytest.mark.parametrize("fault", ["wrong_d", "wrong_start", "wrong_config", "bad_source_sha", "symlink"])
def test_window_build_rejects_unbound_dates_configuration_and_bytes(publication, fault):
    repo, site, payload, _, _, materialize, _ = publication
    if fault == "wrong_d":
        payload["report_signal_date"] = "20260909"
    elif fault == "wrong_start":
        payload["start_signal_date"] = "20260828"
    elif fault == "wrong_config":
        payload["config_sha256"] = "a" * 64
    elif fault == "bad_source_sha":
        payload["source_files"][0]["sha256"] = "a" * 64
    else:
        path = repo / CONFIG
        saved = repo / "saved-config.json"
        path.replace(saved)
        path.symlink_to(saved)
    _seal(payload)
    with pytest.raises(ValueError):
        materialize(repo, site)
    assert not (site / WINDOW).exists()


def test_unknown_builder_error_is_not_silently_published_as_empty_metrics(publication):
    repo, site, _, original, _, _, _ = publication

    def builder(*args, **kwargs):
        raise RuntimeError("unexpected program error")

    materialize = _embedded_function("materialize_compact_statistics_window", builder=builder, validator=lambda value: None)
    with pytest.raises(RuntimeError, match="unexpected program error"):
        materialize(repo, site)
    assert not (site / WINDOW).exists()
    assert json.loads((site / "revision.json").read_bytes()) == original


@pytest.mark.parametrize("corrupt", ["revision.json", WINDOW, CONFIG])
def test_public_window_verifier_rejects_stale_or_changed_exact_bytes(publication, corrupt):
    repo, site, _, _, _, materialize, verify = publication
    materialize(repo, site)

    def fetch(path):
        raw = (site / path).read_bytes()
        return raw + b" " if path == corrupt else raw

    with pytest.raises(ValueError):
        verify(site, fetch, HEAD, D)


@pytest.mark.parametrize("head,date", [("b" * 40, D), (HEAD, "20260909")])
def test_public_window_verifier_requires_exact_writer_head_and_signal_date(publication, head, date):
    repo, site, _, _, _, materialize, verify = publication
    materialize(repo, site)
    with pytest.raises(ValueError, match="revision/date/start"):
        verify(site, lambda path: (site / path).read_bytes(), head, date)


def test_real_builder_window_can_be_published_and_verified_without_repo_writes(tmp_path):
    from scripts.build_compact_statistics_window import build_window, validate_window
    site = tmp_path / "site"
    site.mkdir()
    report_date = json.loads((ROOT / "outputs/decision/primary_d_runtime_index.json").read_bytes())["latest_signal_date"]
    payload = build_window(ROOT, signal_date=report_date)
    validate_window(payload)
    tracked = {item["path"]: (ROOT / item["path"]).read_bytes() for item in payload["source_files"]}
    # The ordinary site build has already copied/validated public output files.
    for relative, raw in tracked.items():
        if relative.startswith("outputs/decision/"):
            _write(site, relative, raw)
    _write(site, "revision.json", _json_bytes({"head_sha": HEAD, "signal_date": report_date, "primary_d_status": "READY"}))
    materialize = _embedded_function("materialize_compact_statistics_window", builder=build_window, validator=validate_window)
    verify = _embedded_function("verify_public_compact_statistics_window", builder=build_window, validator=validate_window)
    materialize(ROOT, site)
    actual = verify(site, lambda path: (site / path).read_bytes(), HEAD, report_date)
    assert actual["start_signal_date"] == D
    assert actual["report_signal_date"] == report_date
    assert all((ROOT / relative).read_bytes() == raw for relative, raw in tracked.items())
