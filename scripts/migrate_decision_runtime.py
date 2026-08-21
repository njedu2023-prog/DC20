#!/usr/bin/env python3
"""Build and verify a fail-closed canonical Decision runtime migration bundle.

This program never publishes.  ``build`` is intended to run in a clean,
detached, exact-base, explicitly non-sparse worktree.  It invokes the reviewed
frozen replay, validates the complete runtime, removes every replay side effect
outside a finite runtime allowlist, validates the pruned workspace again, and
emits an independently verifiable bundle.  A GitHub workflow may then publish
that bundle with a single exact-base API compare-and-swap commit.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for _entry in (SRC, SCRIPTS):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))


SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DATE_RE = re.compile(r"20\d{6}")
RECEIPT_SCHEMA = "decision_runtime_migration_receipt_v1"
MIGRATION_SCHEMA = "decision_runtime_migration_v1"
ALLOWLIST_VERSION = "decision_runtime_migration_paths_v1"

STATIC_CANDIDATE_PATHS = frozenset(
    {
        "outputs/auction_v3/models/model_meta_latest.json",
        "outputs/auction_v3/metrics/backtest_latest.json",
        "outputs/auction_v3/metrics/backtest_top10_latest.csv",
        "outputs/auction_v3/metrics/backtest_trade_selector_oos_latest.csv",
        "outputs/auction_v3/predictions/pred_latest.csv",
        "outputs/decision/action_plan_latest.json",
        "outputs/decision/report_index.json",
    }
)

REQUIRED_EXACT_BASE_SOURCES = frozenset(
    {
        ".github/workflows/migrate_decision_runtime.yml",
        "models/decision_model_freeze.json",
        "scripts/decision_pages_truth.py",
        "scripts/migrate_decision_runtime.py",
        "scripts/publish_decision_action.py",
        "scripts/replay_frozen_canonical_v2.py",
        "scripts/validate_decision_model_freeze.py",
        "src/top10decision/decision/action_plan.py",
        "src/top10decision/decision/model_freeze.py",
    }
)

TRUTH_LEDGER_BINDINGS = {
    "formal_limit_proxy": (
        "outputs/auction_v3/verification/verify_latest.csv",
        "outputs/auction_v3/metrics/cumulative_latest.json",
    ),
    "market_open_observation": (
        "outputs/auction_v3/verification/observation_latest.csv",
        "outputs/auction_v3/metrics/observation_cumulative_latest.json",
    ),
    "manual_actual": (
        "outputs/auction_v3/verification/manual_actual_latest.csv",
        "outputs/auction_v3/metrics/manual_actual_cumulative_latest.json",
    ),
}

ACTION_OBSERVATION_TRUTH_FIELDS = (
    "observation_max_price",
    "observation_auction_change_pct",
    "observation_price_basis",
    "observation_price_is_formal",
    "observation_rank",
    "observation_pool_size",
    "validation_mode",
    "observation_execution_mode",
    "prediction_timing_status",
    "prediction_timing_valid",
    "prediction_deadline_utc",
    "validation_status",
    "actual_buy_date",
    "actual_open_price",
    "actual_t_close",
    "market_daily_return",
    "observation_fill",
    "observation_fill_reason",
    "observation_limit_accept",
    "observation_price_vs_cap",
    "market_buyable_diagnostic",
    "market_buyable_reason",
    "observation_t_return",
    "continuation_limit_up_hit",
    "actual_exit_date",
    "actual_exit_price",
    "actual_gross_return",
    "actual_net_return",
    "exit_reason",
    "truth_source",
    "truth_generated_at_utc",
)


class MigrationError(RuntimeError):
    """Raised when any migration precondition or proof fails closed."""


@dataclass(frozen=True)
class MigrationBinding:
    signal_date: str
    report_date: str
    exec_date: str
    exit_date: str
    evaluation_path: str
    report_path: str
    candidates_path: str
    execution_path: str


@dataclass(frozen=True)
class GitBase:
    sha: str
    tree_sha: str


def _fail(message: str) -> None:
    raise MigrationError(message)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    _fail(f"non-finite JSON constant is forbidden: {value}")


def load_strict_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        _fail(f"{label} is missing, empty, or a symlink: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except MigrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{label} is not strict UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict):
        _fail(f"{label} must contain exactly one JSON object")
    return payload


def _strict_date(value: Any, label: str) -> str:
    if type(value) is not str or DATE_RE.fullmatch(value) is None:
        _fail(f"{label} must be an exact YYYYMMDD string")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise MigrationError(f"{label} is not a calendar date: {value!r}") from exc
    if parsed.strftime("%Y%m%d") != value:
        _fail(f"{label} is not canonical: {value!r}")
    return value


def _strict_sha(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(f"{label} is not an exact lowercase hash")
    return value


def _safe_repo_path(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        _fail(f"{label} must be a nonempty repository-relative path")
    if "\\" in value or "\x00" in value:
        _fail(f"{label} contains forbidden path characters")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail(f"{label} is not a safe repository-relative path: {value!r}")
    canonical = pure.as_posix()
    if canonical != value:
        _fail(f"{label} is not canonical: {value!r}")
    return canonical


def _child(root: Path, relative: str, label: str) -> Path:
    relative = _safe_repo_path(relative, label)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            _fail(f"{label} contains a symlink component: {relative}")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MigrationError(f"{label} escapes migration root") from exc
    return candidate


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise MigrationError(f"cannot hash file: {path}") from exc
    return digest.hexdigest()


def _git(
    root: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=text,
    )
    if check and result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        _fail(f"git {' '.join(args)} failed: {stderr.strip()}")
    return result


def require_exact_base(
    root: Path,
    expected_base_sha: str,
    *,
    require_detached: bool = True,
) -> GitBase:
    expected_base_sha = _strict_sha(expected_base_sha, "base_sha", SHA1_RE)
    root = root.resolve()
    top = str(_git(root, ["rev-parse", "--show-toplevel"]).stdout).strip()
    if Path(top).resolve() != root:
        _fail("migration root is not the exact Git worktree root")
    head = str(_git(root, ["rev-parse", "HEAD"]).stdout).strip()
    if head != expected_base_sha:
        _fail(f"HEAD/base mismatch: head={head} expected={expected_base_sha}")
    symbolic = _git(root, ["symbolic-ref", "-q", "HEAD"], check=False)
    if require_detached and symbolic.returncode == 0:
        _fail("migration must run in a detached worktree")
    sparse = _git(root, ["config", "--bool", "core.sparseCheckout"], check=False)
    if sparse.returncode == 0 and str(sparse.stdout).strip() == "true":
        _fail("migration forbids sparse checkout; full raw history is required")
    tracked = _git(root, ["ls-files", "-t", "-z"], text=False).stdout
    assert isinstance(tracked, bytes)
    skip_worktree = []
    for record in tracked.split(b"\0"):
        if not record:
            continue
        if record.startswith(b"S "):
            try:
                skip_worktree.append(record[2:].decode("utf-8"))
            except UnicodeError as exc:
                raise MigrationError("skip-worktree path is not UTF-8") from exc
    if skip_worktree:
        _fail(
            "migration forbids skip-worktree paths; full checkout is required: "
            f"{skip_worktree[:5]!r}"
        )
    status_output = str(
        _git(root, ["status", "--porcelain=v1", "--untracked-files=all"]).stdout
    )
    if status_output:
        _fail("migration worktree must be clean before replay")
    tree_sha = str(_git(root, ["rev-parse", f"{head}^{{tree}}"]).stdout).strip()
    return GitBase(sha=head, tree_sha=_strict_sha(tree_sha, "base_tree_sha", SHA1_RE))


def candidate_paths(signal_date: str, report_date: str) -> frozenset[str]:
    signal = _strict_date(signal_date, "signal_date")
    report = _strict_date(report_date, "report_date")
    return frozenset(
        {
            *STATIC_CANDIDATE_PATHS,
            f"outputs/auction_v3/predictions/pred_{signal}.csv",
            f"outputs/decision/action_plan_{report}.json",
        }
    )


def required_receipt_source_paths(signal_date: str) -> frozenset[str]:
    signal = _strict_date(signal_date, "source signal_date")
    return frozenset(
        {
            *REQUIRED_EXACT_BASE_SOURCES,
            "data/pred/_pred_source_meta.json",
            "data/pred/pred_source_latest.csv",
            f"data/market/raw/{signal[:4]}/{signal}/_sync_meta.json",
        }
    )


def _git_tree_entry(root: Path, base_sha: str, relative: str) -> dict[str, Any] | None:
    relative = _safe_repo_path(relative, "git tree path")
    raw = _git(root, ["ls-tree", "-z", base_sha, "--", relative], text=False).stdout
    assert isinstance(raw, bytes)
    if not raw:
        return None
    records = [record for record in raw.split(b"\0") if record]
    if len(records) != 1:
        _fail(f"base tree path is ambiguous: {relative}")
    try:
        header, actual_path = records[0].split(b"\t", 1)
        mode, object_type, sha = header.decode("ascii").split(" ", 2)
        decoded_path = actual_path.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise MigrationError(f"cannot decode base tree entry: {relative}") from exc
    if decoded_path != relative:
        _fail(f"base tree returned a different path for {relative}")
    return {"mode": mode, "type": object_type, "sha": sha}


def _exact_base_file_evidence(
    root: Path, base_sha: str, relative: str, *, required: bool = True
) -> dict[str, Any] | None:
    relative = _safe_repo_path(relative, "exact-base source path")
    entry = _git_tree_entry(root, base_sha, relative)
    if entry is None:
        if required:
            _fail(f"exact-base source is absent: {relative}")
        return None
    if entry["mode"] != "100644" or entry["type"] != "blob":
        _fail(f"exact-base source is not a regular 100644 blob: {relative}")
    path = _child(root, relative, "exact-base source path")
    if not path.is_file() or path.is_symlink():
        _fail(f"exact-base source is not a regular worktree file: {relative}")
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode):
        _fail(f"exact-base source is not regular: {relative}")
    blob = str(_git(root, ["hash-object", "--", relative]).stdout).strip()
    if blob != entry["sha"]:
        _fail(f"worktree source does not match exact base blob: {relative}")
    return {
        "git_blob_sha1": _strict_sha(entry["sha"], f"{relative} blob", SHA1_RE),
        "sha256": _sha256_file(path),
        "size": path.stat().st_size,
    }


def discover_binding(root: Path, requested_signal_date: str = "") -> MigrationBinding:
    index = load_strict_json(
        root / "outputs" / "decision" / "report_index.json", "report_index"
    )
    if index.get("schema_version") != "decision_report_index_v2_action_truth":
        _fail("report_index is not action-truth schema V2")
    reports = index.get("reports")
    if not isinstance(reports, list) or not reports or not isinstance(reports[0], dict):
        _fail("report_index.reports must be a nonempty object list")
    report_date = _strict_date(index.get("latest_report_date"), "latest_report_date")
    first_date = _strict_date(reports[0].get("report_date"), "reports[0].report_date")
    if report_date != first_date:
        _fail("latest_report_date does not match reports[0]")
    expected_index_fields = {
        "report_file": f"decision_report_{report_date}.md",
        "report_url": f"outputs/decision/decision_report_{report_date}.md",
        "eval_url": f"outputs/decision/eval_{report_date}.json",
    }
    for key, expected in expected_index_fields.items():
        if reports[0].get(key) != expected:
            _fail(f"latest report index {key} is not exact")

    evaluation_path = f"outputs/decision/eval_{report_date}.json"
    evaluation = load_strict_json(_child(root, evaluation_path, "evaluation"), "evaluation")
    signal_date = _strict_date(evaluation.get("signal_date"), "evaluation.signal_date")
    exec_date = _strict_date(evaluation.get("exec_date"), "evaluation.exec_date")
    exit_date = _strict_date(evaluation.get("exit_date"), "evaluation.exit_date")
    if exec_date != report_date:
        _fail("evaluation.exec_date does not equal latest report_date")
    if not (signal_date < exec_date < exit_date):
        _fail("evaluation dates are not strictly signal < exec < exit")
    if requested_signal_date:
        requested = _strict_date(requested_signal_date, "requested signal_date")
        if requested != signal_date:
            _fail(
                "requested signal_date is not the exact latest report signal: "
                f"requested={requested} latest={signal_date}"
            )

    paths = evaluation.get("paths")
    if not isinstance(paths, dict):
        _fail("evaluation.paths must be an object")
    report_path = f"outputs/decision/decision_report_{report_date}.md"
    candidates_path = f"data/decision/decision_candidates_{signal_date}.csv"
    execution_path = f"data/decision/decision_execution_{report_date}.csv"
    for key, expected in (
        ("decision_report", report_path),
        ("candidates", candidates_path),
        ("execution", execution_path),
    ):
        if paths.get(key) != expected:
            _fail(f"evaluation.paths.{key} is not exact")
    for relative, label in (
        (evaluation_path, "evaluation"),
        (report_path, "dated report"),
        (candidates_path, "dated candidates"),
        (execution_path, "dated execution evidence"),
    ):
        path = _child(root, relative, label)
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            _fail(f"{label} is missing, empty, or a symlink: {relative}")
    return MigrationBinding(
        signal_date=signal_date,
        report_date=report_date,
        exec_date=exec_date,
        exit_date=exit_date,
        evaluation_path=evaluation_path,
        report_path=report_path,
        candidates_path=candidates_path,
        execution_path=execution_path,
    )


def annotate_retrospective_action(
    payload: dict[str, Any], binding: MigrationBinding, base_sha: str
) -> dict[str, Any]:
    """Return a public action payload that cannot imply a live trade or fill."""

    base_sha = _strict_sha(base_sha, "base_sha", SHA1_RE)
    result = copy.deepcopy(payload)
    if result.get("signal_date") != binding.signal_date:
        _fail("action signal_date does not match migration binding")
    if result.get("report_date") != binding.report_date:
        _fail("action report_date does not match migration binding")
    if result.get("exec_date") != binding.exec_date:
        _fail("action exec_date does not match migration binding")
    if result.get("exit_date") != binding.exit_date:
        _fail("action exit_date does not match migration binding")
    formal_count = result.get("formal_buy_count")
    if type(formal_count) is not int or formal_count != 0:
        _fail("runtime migration is forbidden for an action with formal buys")
    status_code = result.get("status_code")
    if type(status_code) is not str or not status_code.startswith("NO_TRADE"):
        _fail("runtime migration requires an explicit NO_TRADE action")
    if result.get("guidance_only") is not True:
        _fail("runtime migration action must remain guidance_only")
    if result.get("broker_connected") is not False:
        _fail("runtime migration action must remain broker-disconnected")
    if result.get("order_execution") != "manual_only":
        _fail("runtime migration action must remain manual_only")
    for collection in ("candidates", "stage_watchlist"):
        rows = result.get(collection)
        if not isinstance(rows, list):
            _fail(f"action {collection} must be a list")
        for row_number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                _fail(f"action {collection}[{row_number}] must be an object")
            if row.get("action") == "BUY":
                _fail(f"action {collection}[{row_number}] contains BUY")
            weight = row.get("target_weight", 0)
            if type(weight) not in {int, float} or float(weight) != 0.0:
                _fail(f"action {collection}[{row_number}] has nonzero target_weight")

    result["publication_timing"] = "RETROSPECTIVE"
    result["live_delivery_met"] = False
    result["execution_or_fill_claimed"] = False
    result["migration"] = {
        "schema_version": MIGRATION_SCHEMA,
        "source": "frozen_canonical_replay",
        "timing": "RETROSPECTIVE",
        "base_sha": base_sha,
        "signal_date": binding.signal_date,
        "report_date": binding.report_date,
        "exec_date": binding.exec_date,
        "exit_date": binding.exit_date,
        "live_delivery_met": False,
        "execution_created": False,
        "fill_created": False,
        "broker_execution_claimed": False,
        "observation_truth_is_not_a_fill_claim": True,
    }
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(
        payload, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=False
    )
    path.write_text(rendered + "\n", encoding="utf-8")


def _run_checked(
    command: list[str], *, cwd: Path, label: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        _fail(f"{label} failed with exit {completed.returncode}: {detail[-4000:]}")
    return completed


def run_frozen_replay(root: Path, binding: MigrationBinding, report_path: Path) -> dict[str, Any]:
    completed = _run_checked(
        [
            sys.executable,
            str(root / "scripts" / "replay_frozen_canonical_v2.py"),
            "--root",
            str(root),
            "--signal-date",
            binding.signal_date,
            "--report",
            str(report_path),
        ],
        cwd=root,
        label="frozen canonical V2 replay",
    )
    del completed
    replay = load_strict_json(report_path, "frozen replay report")
    if replay.get("status") != "pass":
        _fail("frozen replay report did not pass")
    if replay.get("diagnostic_mode") != "workspace_only_forced_frozen_canonical_v2":
        _fail("frozen replay did not use the reviewed workspace-only mode")
    if replay.get("force_prediction") is not True:
        _fail("frozen replay did not force a canonical prediction")
    runtime = replay.get("runtime_validation")
    if not isinstance(runtime, dict) or runtime.get("validated") is not True:
        _fail("frozen replay did not complete runtime validation")
    if runtime.get("canonical_v2_enforced") is not True:
        _fail("frozen replay did not enforce canonical V2")
    return replay


def _annotate_action_files(root: Path, binding: MigrationBinding, base_sha: str) -> None:
    dated_relative = f"outputs/decision/action_plan_{binding.report_date}.json"
    latest_relative = "outputs/decision/action_plan_latest.json"
    dated = load_strict_json(_child(root, dated_relative, "dated action"), "dated action")
    latest = load_strict_json(_child(root, latest_relative, "latest action"), "latest action")
    for key in ("schema_version", "signal_date", "report_date", "exec_date", "exit_date"):
        if dated.get(key) != latest.get(key):
            _fail(f"dated/latest actions disagree on {key}")
    _write_json(
        _child(root, dated_relative, "dated action"),
        annotate_retrospective_action(dated, binding, base_sha),
    )
    _write_json(
        _child(root, latest_relative, "latest action"),
        annotate_retrospective_action(latest, binding, base_sha),
    )


def rebind_model_truth_ledgers(root: Path) -> dict[str, Any]:
    """Bind allowed model metadata to the already-restored base truth JSON."""

    meta_path = root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json"
    model_meta = load_strict_json(meta_path, "model metadata")
    truth_ledgers = model_meta.get("truth_ledgers")
    if not isinstance(truth_ledgers, dict):
        _fail("model_meta.truth_ledgers must be an object")
    if set(truth_ledgers) != set(TRUTH_LEDGER_BINDINGS):
        _fail("model_meta.truth_ledgers key set is not exact")
    rebound: dict[str, dict[str, Any]] = {}
    for name, (ledger_path, metrics_path) in TRUTH_LEDGER_BINDINGS.items():
        entry = truth_ledgers.get(name)
        if not isinstance(entry, dict) or entry.get("path") != ledger_path:
            _fail(f"model_meta truth ledger path is not exact: {name}")
        ledger = _child(root, ledger_path, f"{name} truth ledger")
        if ledger.is_symlink() or not ledger.is_file() or ledger.stat().st_size <= 0:
            _fail(f"restored truth ledger is missing, empty, or a symlink: {ledger_path}")
        metrics = load_strict_json(
            _child(root, metrics_path, f"{name} truth metrics"),
            f"{name} truth metrics",
        )
        rebound[name] = {"path": ledger_path, "metrics": metrics}
    model_meta["truth_ledgers"] = rebound
    _write_json(meta_path, model_meta)
    return model_meta


def rebuild_current_action_after_prune(
    root: Path,
    binding: MigrationBinding,
    base_sha: str,
) -> dict[str, Any]:
    """Rebuild only the current action/index; never refresh historical actions."""

    from top10decision.decision.action_plan import build_action_plan, build_report_index

    plan = build_action_plan(root, binding.report_date)
    annotated = annotate_retrospective_action(plan, binding, base_sha)
    decision_root = root / "outputs" / "decision"
    _write_json(
        decision_root / f"action_plan_{binding.report_date}.json",
        annotated,
    )
    _write_json(decision_root / "action_plan_latest.json", annotated)
    index = build_report_index(root, binding.report_date)
    _write_json(decision_root / "report_index.json", index)
    return annotated


def validate_embedded_truth_bindings(
    root: Path,
    *,
    binding: MigrationBinding,
) -> dict[str, Any]:
    """Prove published summaries/watch rows equal their restored references."""

    from top10decision.decision.action_plan import _json_safe, _observation_frame

    model_meta = load_strict_json(
        root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json",
        "model metadata",
    )
    action = load_strict_json(
        root / "outputs" / "decision" / "action_plan_latest.json",
        "latest action",
    )
    truth_ledgers = model_meta.get("truth_ledgers")
    if not isinstance(truth_ledgers, dict) or set(truth_ledgers) != set(
        TRUTH_LEDGER_BINDINGS
    ):
        _fail("model truth ledger key set is not exact after pruning")
    for name, (ledger_path, metrics_path) in TRUTH_LEDGER_BINDINGS.items():
        entry = truth_ledgers.get(name)
        expected_metrics = load_strict_json(
            _child(root, metrics_path, f"{name} metrics reference"),
            f"{name} metrics reference",
        )
        if not isinstance(entry, dict) or entry != {
            "path": ledger_path,
            "metrics": expected_metrics,
        }:
            _fail(f"embedded model truth metrics differ from restored JSON: {name}")
    action_model = action.get("model")
    if not isinstance(action_model, dict):
        _fail("action.model is missing after current-only rebuild")
    if action_model.get("truth_ledgers") != truth_ledgers:
        _fail("action model truth ledgers differ from rebound model metadata")
    observation_metrics = load_strict_json(
        root
        / "outputs"
        / "auction_v3"
        / "metrics"
        / "observation_cumulative_latest.json",
        "observation cumulative metrics",
    )
    if action.get("observation_statistics") != observation_metrics:
        _fail("action observation_statistics differ from restored metrics JSON")

    truth = _observation_frame(root, binding.exec_date)
    lookup: dict[str, Any] = {}
    if not truth.empty and "ts_code" in truth.columns:
        lookup = {
            str(row.get("ts_code") or "").strip(): row
            for _, row in truth.drop_duplicates("ts_code", keep="last").iterrows()
        }
    watchlist = action.get("stage_watchlist")
    if not isinstance(watchlist, list):
        _fail("action stage_watchlist is not a list after current-only rebuild")
    matched_rows = 0
    for row_number, row in enumerate(watchlist, start=1):
        if not isinstance(row, dict):
            _fail(f"action stage_watchlist[{row_number}] is not an object")
        verified = lookup.get(str(row.get("ts_code") or "").strip())
        if verified is None:
            continue
        matched_rows += 1
        for field in ACTION_OBSERVATION_TRUTH_FIELDS:
            expected = _json_safe(verified.get(field))
            if row.get(field) != expected:
                _fail(
                    "action watchlist truth differs from restored observation bytes: "
                    f"row={row_number} field={field}"
                )
    return {
        "model_truth_metrics_exact": True,
        "action_truth_ledgers_exact": True,
        "action_observation_statistics_exact": True,
        "action_watchlist_truth_exact": True,
        "watchlist_rows": len(watchlist),
        "matched_observation_rows": matched_rows,
    }


def assert_truth_references_are_exact_base(
    root: Path,
    *,
    base_sha: str,
    binding: MigrationBinding,
) -> dict[str, dict[str, Any]]:
    paths = {
        relative
        for pair in TRUTH_LEDGER_BINDINGS.values()
        for relative in pair
    }
    dated_observation = (
        f"outputs/auction_v3/verification/observation_{binding.exec_date}.csv"
    )
    if _git_tree_entry(root, base_sha, dated_observation) is not None:
        paths.add(dated_observation)
    else:
        paths.add("outputs/auction_v3/verification/observation_latest.csv")
    evidence: dict[str, dict[str, Any]] = {}
    for relative in sorted(paths):
        item = _exact_base_file_evidence(root, base_sha, relative, required=True)
        assert item is not None
        evidence[relative] = item
    return evidence


def run_full_validators(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    from decision_pages_truth import validate_report_index_action_truth
    from top10decision.decision.model_freeze import (
        model_freeze_active,
        validate_action_plan_artifact,
        validate_pinned_files,
        validate_runtime_artifacts,
    )

    active = model_freeze_active(manifest)
    force_inactive = not active
    pins = validate_pinned_files(
        root, manifest, force_enforcement=force_inactive
    )
    if pins.get("validated") is not True or pins.get("enforced") is not True:
        _fail("freeze pins were not enforced")
    runtime = validate_runtime_artifacts(
        root,
        manifest,
        check_action_plan=True,
        force_enforcement=force_inactive,
    )
    if runtime.get("validated") is not True:
        _fail("full runtime validator did not pass")
    runtime_action = runtime.get("action_plan")
    if not isinstance(runtime_action, dict) or runtime_action.get("present") is not True:
        _fail("full runtime validator did not bind the current action")
    runtime_binding = runtime_action.get("runtime_binding")
    if not isinstance(runtime_binding, dict):
        _fail("full runtime validator omitted report/eval/candidate binding")
    for field in (
        "report_dates_exact",
        "evaluation_dates_exact",
        "candidate_rows_exact",
        "watchlist_rows_exact",
    ):
        if runtime_binding.get(field) is not True:
            _fail(f"full runtime validator did not prove {field}")
    action = validate_action_plan_artifact(
        root, manifest, force_enforcement=force_inactive
    )
    if action.get("validated") is not True or action.get("enforced") is not True:
        _fail("standalone action validator did not pass")
    action_plan = action.get("action_plan")
    if not isinstance(action_plan, dict) or action_plan.get("present") is not True:
        _fail("standalone action validator did not bind a current action")
    standalone_binding = action_plan.get("runtime_binding")
    if not isinstance(standalone_binding, dict):
        _fail("standalone action validator omitted report/eval/candidate binding")
    for field in (
        "report_dates_exact",
        "evaluation_dates_exact",
        "candidate_rows_exact",
        "watchlist_rows_exact",
    ):
        if standalone_binding.get(field) is not True:
            _fail(f"standalone action validator did not prove {field}")
    page_truth = validate_report_index_action_truth(
        report_index_path=root / "outputs" / "decision" / "report_index.json",
        site_root=root,
    )

    model_command = [
        sys.executable,
        str(root / "scripts" / "validate_decision_model_freeze.py"),
        "--root",
        str(root),
        "--runtime",
    ]
    if force_inactive:
        model_command.append("--force-inactive")
    model_cli = _run_checked(
        model_command, cwd=root, label="Decision model/runtime CLI validator"
    )
    try:
        model_cli_payload = json.loads(
            model_cli.stdout,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, MigrationError) as exc:
        raise MigrationError("model/runtime CLI emitted invalid JSON") from exc
    if (
        not isinstance(model_cli_payload, dict)
        or not isinstance(model_cli_payload.get("runtime"), dict)
        or model_cli_payload["runtime"].get("validated") is not True
    ):
        _fail("model/runtime CLI did not report a validated runtime")

    return {
        "pinned_files": True,
        "full_runtime": True,
        "standalone_model": True,
        "standalone_action": True,
        "report_contract": True,
        "evaluation_contract": True,
        "candidate_gates": True,
        "report_index_action_truth": True,
        "force_inactive": force_inactive,
        "action_status_code": str(action_plan.get("status_code") or ""),
        "action_formal_buy_count": action_plan.get("formal_buy_count"),
        "report_dates": list(page_truth.report_dates),
        "action_dates": list(page_truth.action_dates),
    }


def _changed_paths(root: Path, base_sha: str) -> set[str]:
    tracked_raw = _git(
        root,
        ["diff", "--name-only", "--no-renames", "-z", base_sha, "--"],
        text=False,
    ).stdout
    untracked_raw = _git(
        root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        text=False,
    ).stdout
    assert isinstance(tracked_raw, bytes) and isinstance(untracked_raw, bytes)
    result: set[str] = set()
    for raw in (tracked_raw, untracked_raw):
        for item in raw.split(b"\0"):
            if not item:
                continue
            try:
                decoded = item.decode("utf-8")
            except UnicodeError as exc:
                raise MigrationError("changed Git path is not UTF-8") from exc
            result.add(_safe_repo_path(decoded, "changed path"))
    return result


def restore_outside_allowlist(
    root: Path, base_sha: str, allowed: Iterable[str]
) -> tuple[str, ...]:
    """Restore every noncandidate replay side effect to the exact base bytes."""

    allowed_set = {_safe_repo_path(path, "allowed path") for path in allowed}
    restored: list[str] = []
    for relative in sorted(_changed_paths(root, base_sha).difference(allowed_set)):
        path = _child(root, relative, "replay side effect")
        entry = _git_tree_entry(root, base_sha, relative)
        if entry is not None:
            if entry["mode"] != "100644" or entry["type"] != "blob":
                _fail(f"refusing to restore non-regular base entry: {relative}")
            _git(
                root,
                ["restore", "--source", base_sha, "--worktree", "--", relative],
            )
        else:
            if path.exists() or path.is_symlink():
                if path.is_dir() and not path.is_symlink():
                    _fail(f"refusing to delete replay-created directory: {relative}")
                path.unlink()
        restored.append(relative)
    remaining = _changed_paths(root, base_sha)
    unexpected = sorted(remaining.difference(allowed_set))
    if unexpected:
        _fail(f"replay side effects survived pruning: {unexpected!r}")
    return tuple(restored)


def _assert_historical_actions_unchanged(
    root: Path, base_sha: str, target_report_date: str
) -> None:
    prefix = root / "outputs" / "decision"
    target = f"action_plan_{target_report_date}.json"
    for path in sorted(prefix.glob("action_plan_20*.json")):
        if path.name == target:
            continue
        relative = path.relative_to(root).as_posix()
        evidence = _exact_base_file_evidence(root, base_sha, relative, required=True)
        if evidence is None:  # pragma: no cover - required=True always fails first
            _fail(f"historical action did not exist at base: {relative}")


def _source_evidence(
    root: Path,
    git_base: GitBase,
    binding: MigrationBinding,
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    pins = manifest.get("pinned_files")
    if not isinstance(pins, dict) or not pins:
        _fail("freeze manifest pinned_files must be nonempty")
    sources = set(required_receipt_source_paths(binding.signal_date))
    sources.update(_safe_repo_path(path, "manifest pin") for path in pins)
    sources.update(
        {
            "data/market/trade_cal_sse.csv",
            "outputs/decision/report_index.json",
            binding.evaluation_path,
            binding.report_path,
            binding.candidates_path,
            binding.execution_path,
        }
    )
    sources.update(
        relative
        for pair in TRUTH_LEDGER_BINDINGS.values()
        for relative in pair
    )
    dated_observation = (
        f"outputs/auction_v3/verification/observation_{binding.exec_date}.csv"
    )
    if _git_tree_entry(root, git_base.sha, dated_observation) is not None:
        sources.add(dated_observation)
    evidence: dict[str, dict[str, Any]] = {}
    for relative in sorted(sources):
        item = _exact_base_file_evidence(root, git_base.sha, relative, required=True)
        assert item is not None
        if relative in pins and item["sha256"] != pins[relative]:
            _fail(f"manifest SHA256 pin differs from exact base source: {relative}")
        evidence[relative] = item
    return evidence


def _candidate_output_evidence(
    root: Path,
    base_sha: str,
    changed_paths: Iterable[str],
) -> tuple[dict[str, str], dict[str, int], dict[str, str | None]]:
    output_sha256: dict[str, str] = {}
    output_size: dict[str, int] = {}
    base_blob_sha1: dict[str, str | None] = {}
    for relative in sorted(changed_paths):
        path = _child(root, relative, "candidate output")
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            _fail(f"candidate output is missing, empty, or a symlink: {relative}")
        if not stat.S_ISREG(path.stat().st_mode):
            _fail(f"candidate output is not regular: {relative}")
        output_sha256[relative] = _sha256_file(path)
        output_size[relative] = path.stat().st_size
        base_entry = _git_tree_entry(root, base_sha, relative)
        if base_entry is None:
            base_blob_sha1[relative] = None
        else:
            if base_entry["mode"] != "100644" or base_entry["type"] != "blob":
                _fail(f"candidate base entry is not a regular blob: {relative}")
            base_blob_sha1[relative] = _strict_sha(
                base_entry["sha"], f"{relative} base blob", SHA1_RE
            )
    return output_sha256, output_size, base_blob_sha1


def _render_json(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _build_bundle(
    root: Path,
    output_root: Path,
    receipt: dict[str, Any],
    changed_paths: Iterable[str],
) -> tuple[str, Path]:
    if output_root.exists() or output_root.is_symlink():
        _fail("output_root must not already exist")
    output_parent = output_root.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="decision-runtime-bundle.", dir=output_parent))
    try:
        files_root = stage / "files"
        files_root.mkdir()
        for relative in sorted(changed_paths):
            source = _child(root, relative, "candidate output")
            destination = _child(files_root, relative, "bundle output")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            os.chmod(destination, 0o644)
        receipt_bytes = _render_json(receipt)
        receipt_sha = _sha256_bytes(receipt_bytes)
        (stage / "migration-receipt.json").write_bytes(receipt_bytes)
        (stage / "migration-receipt.sha256").write_text(
            receipt_sha + "\n", encoding="ascii"
        )
        verify_envelope(stage, expected_base_sha=receipt["base_sha"])
        os.replace(stage, output_root)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return receipt_sha, output_root / "migration-receipt.json"


def _walk_bundle_files(files_root: Path) -> list[str]:
    if files_root.is_symlink() or not files_root.is_dir():
        _fail("bundle files directory is missing or a symlink")
    paths: list[str] = []
    for current_root, directories, filenames in os.walk(files_root, followlinks=False):
        current = Path(current_root)
        for directory in directories:
            if (current / directory).is_symlink():
                _fail("bundle contains a symlink directory")
        for filename in filenames:
            path = current / filename
            if path.is_symlink():
                _fail("bundle contains a symlink output")
            if not path.is_file():
                _fail("bundle contains a non-regular output")
            relative = path.relative_to(files_root).as_posix()
            paths.append(_safe_repo_path(relative, "bundle output path"))
    return sorted(paths)


def _validate_retrospective_action_payload(
    path: Path,
    *,
    signal_date: str,
    report_date: str,
    exit_date: str,
    base_sha: str,
) -> None:
    action = load_strict_json(path, "migration action output")
    expected_dates = {
        "signal_date": signal_date,
        "report_date": report_date,
        "exec_date": report_date,
        "exit_date": exit_date,
    }
    for key, expected in expected_dates.items():
        if action.get(key) != expected:
            _fail(f"migration action {key} does not match receipt")
    if action.get("publication_timing") != "RETROSPECTIVE":
        _fail("migration action timing is not RETROSPECTIVE")
    if action.get("live_delivery_met") is not False:
        _fail("migration action falsely claims live delivery")
    if action.get("execution_or_fill_claimed") is not False:
        _fail("migration action claims execution or fill")
    if type(action.get("formal_buy_count")) is not int or action["formal_buy_count"] != 0:
        _fail("migration action formal_buy_count is not zero")
    if (
        type(action.get("status_code")) is not str
        or not action["status_code"].startswith("NO_TRADE")
    ):
        _fail("migration action status is not NO_TRADE")
    if action.get("guidance_only") is not True:
        _fail("migration action is not guidance_only")
    if action.get("broker_connected") is not False:
        _fail("migration action claims a broker connection")
    if action.get("order_execution") != "manual_only":
        _fail("migration action is not manual_only")
    migration = action.get("migration")
    if not isinstance(migration, dict):
        _fail("migration action metadata is missing")
    exact_migration = {
        "schema_version": MIGRATION_SCHEMA,
        "source": "frozen_canonical_replay",
        "timing": "RETROSPECTIVE",
        "base_sha": base_sha,
        "signal_date": signal_date,
        "report_date": report_date,
        "exec_date": report_date,
        "exit_date": exit_date,
        "live_delivery_met": False,
        "execution_created": False,
        "fill_created": False,
        "broker_execution_claimed": False,
        "observation_truth_is_not_a_fill_claim": True,
    }
    if migration != exact_migration:
        _fail("migration action metadata is not exact nonexecuting retrospective truth")
    for collection in ("candidates", "stage_watchlist"):
        rows = action.get(collection)
        if not isinstance(rows, list):
            _fail(f"migration action {collection} is not a list")
        for row_number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                _fail(f"migration action {collection}[{row_number}] is not an object")
            if row.get("action") == "BUY":
                _fail(f"migration action {collection}[{row_number}] contains BUY")
            weight = row.get("target_weight", 0)
            if type(weight) not in {int, float} or float(weight) != 0.0:
                _fail(
                    f"migration action {collection}[{row_number}] has nonzero target_weight"
                )


def verify_envelope(
    candidate_root: Path | str,
    *,
    expected_base_sha: str = "",
    exact_base_root: Path | str | None = None,
) -> dict[str, Any]:
    supplied_root = Path(candidate_root).absolute()
    if supplied_root.is_symlink():
        _fail("candidate_root is a symlink")
    root = supplied_root.resolve()
    if not root.is_dir():
        _fail("candidate_root is missing or a symlink")
    allowed_top = {
        "files",
        "migration-receipt.json",
        "migration-receipt.sha256",
    }
    actual_top = {path.name for path in root.iterdir()}
    if actual_top != allowed_top:
        _fail(f"bundle top-level path set is not exact: {sorted(actual_top)!r}")
    receipt_path = root / "migration-receipt.json"
    digest_path = root / "migration-receipt.sha256"
    receipt = load_strict_json(receipt_path, "migration receipt")
    if digest_path.is_symlink() or not digest_path.is_file():
        _fail("migration receipt digest is missing or a symlink")
    digest_text = digest_path.read_text(encoding="ascii")
    if not re.fullmatch(r"[0-9a-f]{64}\n", digest_text):
        _fail("migration receipt digest file is not canonical")
    if _sha256_file(receipt_path) != digest_text.strip():
        _fail("migration receipt digest mismatch")
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        _fail("migration receipt schema is not V1")
    if receipt.get("allowlist_version") != ALLOWLIST_VERSION:
        _fail("migration receipt allowlist version is unknown")
    base_sha = _strict_sha(receipt.get("base_sha"), "receipt.base_sha", SHA1_RE)
    _strict_sha(receipt.get("base_tree_sha"), "receipt.base_tree_sha", SHA1_RE)
    if expected_base_sha:
        expected = _strict_sha(expected_base_sha, "expected_base_sha", SHA1_RE)
        if base_sha != expected:
            _fail("migration receipt base SHA does not match expected base")
    signal_date = _strict_date(receipt.get("signal_date"), "receipt.signal_date")
    report_date = _strict_date(receipt.get("report_date"), "receipt.report_date")
    if receipt.get("exec_date") != report_date:
        _fail("migration receipt exec_date does not equal report_date")
    _strict_date(receipt.get("exit_date"), "receipt.exit_date")
    if receipt.get("timing") != "RETROSPECTIVE":
        _fail("migration receipt timing is not RETROSPECTIVE")
    if receipt.get("live_delivery_met") is not False:
        _fail("migration receipt falsely claims live delivery")
    if receipt.get("execution_or_fill_claimed") is not False:
        _fail("migration receipt claims execution or fill")
    if receipt.get("replay_source") != "frozen_canonical_replay":
        _fail("migration receipt replay source is not canonical")
    if receipt.get("replay_status") != "pass":
        _fail("migration receipt replay status did not pass")
    _strict_sha(
        receipt.get("replay_report_sha256"),
        "receipt.replay_report_sha256",
        SHA256_RE,
    )
    if receipt.get("validators_passed") is not True:
        _fail("migration receipt validators_passed is not true")
    if receipt.get("post_prune_validators_passed") is not True:
        _fail("migration receipt post-prune validators did not pass")
    if type(receipt.get("freeze_active")) is not bool:
        _fail("migration receipt freeze_active must be a bool")
    if type(receipt.get("forced_inactive")) is not bool:
        _fail("migration receipt forced_inactive must be a bool")
    if receipt["forced_inactive"] is receipt["freeze_active"]:
        _fail("migration receipt forced_inactive does not match freeze state")
    if receipt.get("pins_enforced") is not True:
        _fail("migration receipt does not prove enforced freeze pins")
    truth_summary = receipt.get("truth_binding_summary")
    if not isinstance(truth_summary, dict):
        _fail("migration receipt truth_binding_summary must be an object")
    for field in (
        "model_truth_metrics_exact",
        "action_truth_ledgers_exact",
        "action_observation_statistics_exact",
        "action_watchlist_truth_exact",
    ):
        if truth_summary.get(field) is not True:
            _fail(f"migration receipt does not prove truth binding: {field}")
    mode = receipt.get("mode")
    if mode not in {"dry_run", "publish_candidate"}:
        _fail("migration receipt mode is invalid")

    changed = receipt.get("changed_paths")
    if not isinstance(changed, list) or any(type(path) is not str for path in changed):
        _fail("migration receipt changed_paths must be a string list")
    if changed != sorted(set(changed)):
        _fail("migration receipt changed_paths is not sorted and unique")
    allowed = candidate_paths(signal_date, report_date)
    if not set(changed).issubset(allowed):
        _fail("migration receipt contains a path outside the finite allowlist")
    status_value = receipt.get("status")
    expected_status = "candidate_generated" if changed else "no_change"
    if status_value != expected_status:
        _fail("migration receipt status does not match changed path set")
    bundle_paths = _walk_bundle_files(root / "files")
    if bundle_paths != changed:
        _fail("bundle file path set differs from receipt changed_paths")

    if changed:
        expected_actions = {
            "outputs/decision/action_plan_latest.json",
            f"outputs/decision/action_plan_{report_date}.json",
        }
        if not expected_actions.issubset(changed):
            _fail("nonempty migration bundle lacks both current action outputs")

    hashes = receipt.get("output_sha256")
    sizes = receipt.get("output_size")
    base_blobs = receipt.get("base_blob_sha1")
    if not isinstance(hashes, dict) or set(hashes) != set(changed):
        _fail("migration receipt output_sha256 keys are not exact")
    if not isinstance(sizes, dict) or set(sizes) != set(changed):
        _fail("migration receipt output_size keys are not exact")
    if not isinstance(base_blobs, dict) or set(base_blobs) != set(changed):
        _fail("migration receipt base_blob_sha1 keys are not exact")
    for relative in changed:
        digest = _strict_sha(hashes[relative], f"{relative} output sha256", SHA256_RE)
        if type(sizes[relative]) is not int or sizes[relative] <= 0:
            _fail(f"{relative} output size is invalid")
        base_blob = base_blobs[relative]
        if base_blob is not None:
            _strict_sha(base_blob, f"{relative} base blob", SHA1_RE)
        path = _child(root / "files", relative, "bundle output")
        if path.stat().st_size != sizes[relative]:
            _fail(f"{relative} bundle size mismatch")
        if _sha256_file(path) != digest:
            _fail(f"{relative} bundle SHA256 mismatch")

    if changed:
        for relative in sorted(expected_actions):
            _validate_retrospective_action_payload(
                _child(root / "files", relative, "migration action output"),
                signal_date=signal_date,
                report_date=report_date,
                exit_date=receipt["exit_date"],
                base_sha=base_sha,
            )

    sources = receipt.get("source_evidence")
    if not isinstance(sources, dict) or not sources:
        _fail("migration receipt source_evidence must be nonempty")
    missing_exact_sources = sorted(
        required_receipt_source_paths(signal_date).difference(sources)
    )
    if missing_exact_sources:
        _fail(
            "migration receipt lacks exact-base source evidence: "
            f"{missing_exact_sources!r}"
        )
    for relative, evidence in sources.items():
        _safe_repo_path(relative, "source evidence path")
        if not isinstance(evidence, dict):
            _fail(f"source evidence is not an object: {relative}")
        _strict_sha(evidence.get("git_blob_sha1"), "source blob", SHA1_RE)
        _strict_sha(evidence.get("sha256"), "source sha256", SHA256_RE)
        if type(evidence.get("size")) is not int or evidence["size"] <= 0:
            _fail(f"source evidence size is invalid: {relative}")
    truth_references = receipt.get("truth_reference_evidence")
    if not isinstance(truth_references, dict) or not truth_references:
        _fail("migration receipt truth_reference_evidence must be nonempty")
    if not set(truth_references).issubset(sources):
        _fail("truth reference evidence is not a subset of exact-base sources")
    for relative, evidence in truth_references.items():
        if evidence != sources.get(relative):
            _fail(f"truth reference evidence differs from source evidence: {relative}")
    if exact_base_root is not None:
        source_root = Path(exact_base_root).resolve()
        git_base = require_exact_base(
            source_root,
            base_sha,
            require_detached=False,
        )
        if git_base.tree_sha != receipt["base_tree_sha"]:
            _fail("receipt base tree differs from exact-base checkout")
        source_binding = discover_binding(source_root, signal_date)
        if (
            source_binding.report_date != report_date
            or source_binding.exec_date != receipt["exec_date"]
            or source_binding.exit_date != receipt["exit_date"]
        ):
            _fail("receipt dates differ from exact-base report/evaluation binding")
        manifest = load_strict_json(
            source_root / "models" / "decision_model_freeze.json",
            "exact-base freeze manifest",
        )
        if manifest.get("schema_version") != "decision_model_freeze_v2":
            _fail("exact-base freeze manifest is not schema V2")
        if type(manifest.get("active")) is not bool:
            _fail("exact-base freeze manifest active must be a bool")
        if manifest["active"] is not receipt["freeze_active"]:
            _fail("receipt freeze state differs from exact-base manifest")
        pins = manifest.get("pinned_files")
        if not isinstance(pins, dict) or not pins:
            _fail("exact-base freeze manifest pinned_files must be nonempty")
        for relative, expected_sha in pins.items():
            _safe_repo_path(relative, "exact-base manifest pin")
            _strict_sha(expected_sha, f"manifest pin {relative}", SHA256_RE)
        expected_sources = _source_evidence(
            source_root,
            git_base,
            source_binding,
            manifest,
        )
        if sources != expected_sources:
            missing = sorted(set(expected_sources).difference(sources))
            extra = sorted(set(sources).difference(expected_sources))
            _fail(
                "receipt source evidence is not the exact reconstructed set: "
                f"missing={missing!r} extra={extra!r}"
            )
        expected_truth_references = assert_truth_references_are_exact_base(
            source_root,
            base_sha=base_sha,
            binding=source_binding,
        )
        if truth_references != expected_truth_references:
            _fail("receipt truth reference set differs from exact-base reconstruction")
        for relative in changed:
            entry = _git_tree_entry(source_root, base_sha, relative)
            claimed = base_blobs[relative]
            if entry is None:
                if claimed is not None:
                    _fail(f"receipt falsely claims an existing base blob: {relative}")
            else:
                if entry["mode"] != "100644" or entry["type"] != "blob":
                    _fail(f"candidate base path is not a regular blob: {relative}")
                if claimed != entry["sha"]:
                    _fail(f"receipt candidate base blob differs from Git: {relative}")
    return receipt


def build_migration(
    *,
    root: Path,
    base_sha: str,
    output_root: Path,
    requested_signal_date: str = "",
    mode: str = "dry_run",
) -> dict[str, Any]:
    if mode not in {"dry_run", "publish_candidate"}:
        _fail("mode must be dry_run or publish_candidate")
    root = root.resolve()
    supplied_output = output_root.absolute()
    if supplied_output.exists() or supplied_output.is_symlink():
        _fail("output_root must not already exist")
    output_parent = supplied_output.parent.resolve()
    output_root = output_parent / supplied_output.name
    try:
        output_root.relative_to(root)
    except ValueError:
        pass
    else:
        _fail("output_root must be outside the migration worktree")
    output_parent.mkdir(parents=True, exist_ok=True)
    git_base = require_exact_base(root, base_sha)
    binding = discover_binding(root, requested_signal_date)

    from top10decision.decision.model_freeze import (
        load_model_freeze,
        model_freeze_active,
        validate_pinned_files,
    )

    manifest = load_model_freeze(root, required=True)
    active = model_freeze_active(manifest)
    pins = validate_pinned_files(
        root, manifest, force_enforcement=not active
    )
    if pins.get("validated") is not True or pins.get("enforced") is not True:
        _fail("migration requires exact freeze-pin enforcement")
    source_evidence = _source_evidence(root, git_base, binding, manifest)
    allowed = candidate_paths(binding.signal_date, binding.report_date)

    with tempfile.TemporaryDirectory(
        prefix="decision-runtime-replay.", dir=output_root.parent
    ) as temporary:
        replay_report_path = Path(temporary) / "replay-report.json"
        replay = run_frozen_replay(root, binding, replay_report_path)
        replay_report_sha256 = _sha256_file(replay_report_path)

    _annotate_action_files(root, binding, git_base.sha)
    pre_prune = run_full_validators(root, manifest)
    restored = restore_outside_allowlist(root, git_base.sha, allowed)
    truth_reference_evidence = assert_truth_references_are_exact_base(
        root,
        base_sha=git_base.sha,
        binding=binding,
    )
    rebind_model_truth_ledgers(root)
    rebuild_current_action_after_prune(root, binding, git_base.sha)
    _assert_historical_actions_unchanged(root, git_base.sha, binding.report_date)
    truth_bindings = validate_embedded_truth_bindings(root, binding=binding)
    post_prune = run_full_validators(root, manifest)
    validate_embedded_truth_bindings(root, binding=binding)
    post_prune["truth_bindings"] = True

    remaining = sorted(_changed_paths(root, git_base.sha))
    if not set(remaining).issubset(allowed):
        _fail("post-prune changed paths escaped the candidate allowlist")
    if remaining and "outputs/decision/action_plan_latest.json" not in remaining:
        _fail("runtime migration changed files without changing action_plan_latest")
    output_sha256, output_size, base_blob_sha1 = _candidate_output_evidence(
        root, git_base.sha, remaining
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "allowlist_version": ALLOWLIST_VERSION,
        "status": "candidate_generated" if remaining else "no_change",
        "mode": mode,
        "base_sha": git_base.sha,
        "base_tree_sha": git_base.tree_sha,
        "signal_date": binding.signal_date,
        "report_date": binding.report_date,
        "exec_date": binding.exec_date,
        "exit_date": binding.exit_date,
        "timing": "RETROSPECTIVE",
        "live_delivery_met": False,
        "execution_or_fill_claimed": False,
        "replay_source": "frozen_canonical_replay",
        "replay_status": replay.get("status"),
        "replay_report_sha256": replay_report_sha256,
        "freeze_active": active,
        "forced_inactive": not active,
        "pins_enforced": True,
        "validators_passed": all(
            pre_prune.get(key) is True
            for key in (
                "pinned_files",
                "full_runtime",
                "standalone_model",
                "standalone_action",
                "report_contract",
                "evaluation_contract",
                "candidate_gates",
                "report_index_action_truth",
            )
        ),
        "post_prune_validators_passed": all(
            post_prune.get(key) is True
            for key in (
                "pinned_files",
                "full_runtime",
                "standalone_model",
                "standalone_action",
                "report_contract",
                "evaluation_contract",
                "candidate_gates",
                "report_index_action_truth",
                "truth_bindings",
            )
        ),
        "validator_summary": {
            "before_prune": pre_prune,
            "after_prune": post_prune,
        },
        "changed_paths": remaining,
        "restored_paths": list(restored),
        "output_sha256": output_sha256,
        "output_size": output_size,
        "base_blob_sha1": base_blob_sha1,
        "source_evidence": source_evidence,
        "truth_reference_evidence": truth_reference_evidence,
        "truth_binding_summary": truth_bindings,
    }
    if receipt["validators_passed"] is not True:
        _fail("pre-prune validators did not all pass")
    if receipt["post_prune_validators_passed"] is not True:
        _fail("post-prune validators did not all pass")
    receipt_sha, receipt_path = _build_bundle(root, output_root, receipt, remaining)
    summary = {
        "status": receipt["status"],
        "mode": mode,
        "base_sha": git_base.sha,
        "signal_date": binding.signal_date,
        "report_date": binding.report_date,
        "timing": "RETROSPECTIVE",
        "changed_paths": remaining,
        "restored_path_count": len(restored),
        "receipt_path": str(receipt_path),
        "receipt_sha256": receipt_sha,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify a canonical Decision runtime migration bundle"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--root", required=True)
    build.add_argument("--base-sha", required=True)
    build.add_argument("--output-root", required=True)
    build.add_argument("--signal-date", default="")
    build.add_argument(
        "--mode", choices=("dry_run", "publish_candidate"), default="dry_run"
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--candidate-root", required=True)
    verify.add_argument("--base-sha", default="")
    verify.add_argument(
        "--root",
        default="",
        help="optional clean exact-base checkout used to reconstruct source evidence",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command == "build":
            build_migration(
                root=Path(args.root),
                base_sha=args.base_sha,
                output_root=Path(args.output_root),
                requested_signal_date=args.signal_date,
                mode=args.mode,
            )
        else:
            receipt = verify_envelope(
                args.candidate_root,
                expected_base_sha=args.base_sha,
                exact_base_root=args.root or None,
            )
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "base_sha": receipt["base_sha"],
                        "changed_paths": receipt["changed_paths"],
                        "timing": receipt["timing"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    except MigrationError as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
