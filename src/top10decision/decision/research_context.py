from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .action_plan import build_action_plan


RESEARCH_CONTEXT_SCHEMA = "decision_research_context_v1_daily"
RESEARCH_CONTEXT_KIND = "daily_research_context"
HISTORICAL_PARITY_SCHEMA = "decision_research_context_v1_historical_parity"
HISTORICAL_PARITY_KIND = "historical_parity_research_context"
HISTORICAL_ARTIFACT_KEYS = ("action_plan", "decision_report", "evaluation")
DATE_RE = re.compile(r"20\d{6}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_OBJECT_RE = re.compile(r"[0-9a-f]{40}")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class ResearchContextError(ValueError):
    """Raised when a Daily research projection could masquerade as an action."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise ResearchContextError(f"research source is missing, empty, or a symlink: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_sources(root: Path, report_date: str) -> dict[str, str]:
    paths = (
        f"outputs/decision/decision_report_{report_date}.md",
        f"outputs/decision/eval_{report_date}.json",
        "outputs/auction_v3/predictions/pred_latest.csv",
        "outputs/auction_v3/metrics/backtest_latest.json",
        "outputs/auction_v3/models/model_meta_latest.json",
    )
    return {relative: _sha256(root / relative) for relative in paths}


def _research_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchContextError("research candidate row must be an object")
    row = copy.deepcopy(value)
    row.update(
        {
            "action": "WATCH",
            "target_weight": 0.0,
            "trade_selected": 0,
            "market_order_allowed": False,
            "order_type": "NONE_RESEARCH_ONLY",
            "entry_rule": "Daily research context only; same-date Auction action_plan is required",
            "rejection_reason": "RESEARCH_ONLY_PENDING_SAME_DATE_AUCTION",
            "recommended_max_price_is_formal": False,
            "observation_price_is_formal": 0,
        }
    )
    if "watch_label" in row:
        row["watch_label"] = "仅观察"
    return row


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def project_research_context(plan: dict[str, Any], *, source_files: dict[str, str]) -> dict[str, Any]:
    """Project one rich Auction-model run into a non-action Daily artifact.

    The source run must already be exactly date-bound.  This projection keeps
    research fields used by the dashboard, but strips every formal selection
    and order claim.  A same-date Auction action_plan remains the only action
    authority.
    """

    if not isinstance(plan, dict):
        raise ResearchContextError("source action projection must be an object")
    model = plan.get("model")
    if not isinstance(model, dict) or model.get("prediction_matches_report") is not True:
        raise ResearchContextError("research source prediction is not exactly bound to the report")

    context = copy.deepcopy(plan)
    source_status_code = str(context.get("status_code") or "")
    source_status_label = str(context.get("status_label") or "")
    context.update(
        {
            "schema_version": RESEARCH_CONTEXT_SCHEMA,
            "artifact_kind": RESEARCH_CONTEXT_KIND,
            "research_only": True,
            "daily_research_only": True,
            "action_authorized": False,
            "formal_buy_count": 0,
            "status_code": "RESEARCH_ONLY_PENDING_AUCTION",
            "status_label": (
                "日线研究完成 · "
                f"{source_status_label or source_status_code or '模型研究已生成'}"
                " · 等待T日同日竞价行动"
            ),
            "research_model_status_code": source_status_code,
            "research_model_status_label": source_status_label,
            "guidance_only": True,
            "broker_connected": False,
            "order_execution": "disabled_research_only",
            "publication_timing": "D_CLOSE_RESEARCH",
            "live_delivery_met": False,
            "execution_or_fill_claimed": False,
            "source_binding": {
                "scope": "same_repository_local_artifacts_only",
                "files": dict(sorted(source_files.items())),
            },
        }
    )
    context.pop("migration", None)
    context["candidates"] = [
        _research_row(row) for row in context.get("candidates", [])
    ]
    context["stage_watchlist"] = [
        _research_row(row) for row in context.get("stage_watchlist", [])
    ]
    context["stage_watch_count"] = len(context["stage_watchlist"])
    research_model = copy.deepcopy(model)
    research_model["action_authorized"] = False
    context["model"] = research_model
    contract = copy.deepcopy(context.get("execution_contract") or {})
    contract.update(
        {
            "artifact_layer": "daily_research_context",
            "action_authority": "same-date Auction action_plan only",
            "guidance_only": True,
            "broker_connected": False,
        }
    )
    context["execution_contract"] = contract
    validate_research_context(context)
    return context


def validate_research_context(context: dict[str, Any], *, expected_report_date: str = "") -> None:
    if not isinstance(context, dict):
        raise ResearchContextError("research context must be an object")
    if context.get("schema_version") != RESEARCH_CONTEXT_SCHEMA:
        raise ResearchContextError("research context schema_version is invalid")
    if context.get("artifact_kind") != RESEARCH_CONTEXT_KIND:
        raise ResearchContextError("research context artifact_kind is invalid")
    for field in ("research_only", "daily_research_only"):
        if context.get(field) is not True:
            raise ResearchContextError(f"research context {field} must be true")
    if context.get("action_authorized") is not False:
        raise ResearchContextError("research context cannot authorize action")
    if context.get("formal_buy_count") != 0:
        raise ResearchContextError("research context formal_buy_count must be zero")
    if context.get("broker_connected") is not False:
        raise ResearchContextError("research context cannot connect a broker")
    if context.get("execution_or_fill_claimed") is not False:
        raise ResearchContextError("research context cannot claim execution or fill")

    dates: dict[str, str] = {}
    for field in ("report_date", "signal_date", "exec_date", "exit_date"):
        value = context.get(field)
        if type(value) is not str or DATE_RE.fullmatch(value) is None:
            raise ResearchContextError(f"research context {field} must be YYYYMMDD")
        dates[field] = value
    if dates["report_date"] != dates["exec_date"]:
        raise ResearchContextError("research context report_date must equal exec_date")
    if expected_report_date and dates["report_date"] != expected_report_date:
        raise ResearchContextError("research context does not match expected report_date")
    if not dates["signal_date"] < dates["exec_date"] < dates["exit_date"]:
        raise ResearchContextError("research context date order is invalid")

    model = context.get("model")
    if not isinstance(model, dict) or model.get("prediction_matches_report") is not True:
        raise ResearchContextError("research context model is not date-bound")
    if model.get("action_authorized") is not False:
        raise ResearchContextError("research context model cannot authorize action")

    binding = context.get("source_binding")
    if not isinstance(binding, dict):
        raise ResearchContextError("research context source scope is invalid")
    scope = binding.get("scope")
    if scope == "same_repository_local_artifacts_only":
        files = binding.get("files")
        if not isinstance(files, dict) or not files:
            raise ResearchContextError("research context source files are missing")
        for relative, digest in files.items():
            if (
                type(relative) is not str
                or relative.startswith("/")
                or ".." in Path(relative).parts
                or "://" in relative
            ):
                raise ResearchContextError("research context source path is not repository-local")
            if type(digest) is not str or SHA256_RE.fullmatch(digest) is None:
                raise ResearchContextError("research context source digest is invalid")
    else:
        raise ResearchContextError("research context source scope is invalid")

    for collection in ("candidates", "stage_watchlist"):
        rows = context.get(collection)
        if not isinstance(rows, list):
            raise ResearchContextError(f"research context {collection} must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ResearchContextError(f"research context {collection} row must be an object")
            if row.get("action") != "WATCH":
                raise ResearchContextError(f"research context {collection} contains non-WATCH action")
            if row.get("target_weight") not in (0, 0.0):
                raise ResearchContextError(f"research context {collection} contains nonzero weight")
            if row.get("trade_selected") not in (0, False):
                raise ResearchContextError(f"research context {collection} contains selected action")
            if row.get("market_order_allowed") is not False:
                raise ResearchContextError(f"research context {collection} permits a market order")


def build_research_context(root: Path, report_date: str = "") -> dict[str, Any]:
    root = root.resolve()
    plan = build_action_plan(root, report_date)
    chosen_date = str(plan.get("report_date") or "")
    return project_research_context(
        plan,
        source_files=_relative_sources(root, chosen_date),
    )


def _decode_historical_payload(value: Any, *, label: str = "payload_base64") -> bytes:
    if type(value) is not str or not value:
        raise ResearchContextError(f"historical parity {label} is missing")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as exc:
        raise ResearchContextError(f"historical parity {label} is invalid") from exc


def _strict_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ResearchContextError(f"{label} contains duplicate JSON key: {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except ResearchContextError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchContextError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ResearchContextError(f"{label} must be one JSON object")
    return payload


def _validate_vendored_artifact(
    raw: bytes,
    binding: Any,
    *,
    label: str,
) -> None:
    if not isinstance(binding, dict):
        raise ResearchContextError(f"historical parity {label} binding is invalid")
    source_path = binding.get("path")
    if (
        type(source_path) is not str
        or source_path.startswith("/")
        or ".." in Path(source_path).parts
        or "://" in source_path
    ):
        raise ResearchContextError(f"historical parity {label} source path is invalid")
    blob_sha = binding.get("blob_sha")
    if type(blob_sha) is not str or GIT_OBJECT_RE.fullmatch(blob_sha) is None:
        raise ResearchContextError(f"historical parity {label} blob_sha is invalid")
    raw_sha256 = binding.get("raw_sha256")
    if type(raw_sha256) is not str or SHA256_RE.fullmatch(raw_sha256) is None:
        raise ResearchContextError(f"historical parity {label} raw_sha256 is invalid")
    if hashlib.sha256(raw).hexdigest() != raw_sha256:
        raise ResearchContextError(
            f"historical parity {label} raw SHA256 does not match payload"
        )
    if git_blob_sha(raw) != blob_sha:
        raise ResearchContextError(
            f"historical parity {label} Git blob SHA does not match payload"
        )


def _historical_artifacts(
    context: dict[str, Any],
) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    binding = context["source_binding"]
    encoded_artifacts = context.get("payloads_base64")
    artifact_bindings = binding.get("artifacts")
    if encoded_artifacts is None and artifact_bindings is None:
        raw = _decode_historical_payload(context.get("payload_base64"))
        legacy_binding = {
            "path": binding.get("path"),
            "blob_sha": binding.get("blob_sha"),
            "raw_sha256": binding.get("raw_sha256"),
        }
        _validate_vendored_artifact(raw, legacy_binding, label="action_plan")
        return {"action_plan": raw}, {"action_plan": legacy_binding}
    if not isinstance(encoded_artifacts, dict) or set(encoded_artifacts) != set(
        HISTORICAL_ARTIFACT_KEYS
    ):
        raise ResearchContextError("historical parity payloads_base64 artifact set is invalid")
    if not isinstance(artifact_bindings, dict) or set(artifact_bindings) != set(
        HISTORICAL_ARTIFACT_KEYS
    ):
        raise ResearchContextError("historical parity source artifact set is invalid")
    decoded: dict[str, bytes] = {}
    normalized_bindings: dict[str, dict[str, Any]] = {}
    for key in HISTORICAL_ARTIFACT_KEYS:
        raw = _decode_historical_payload(
            encoded_artifacts.get(key),
            label=f"payloads_base64.{key}",
        )
        _validate_vendored_artifact(raw, artifact_bindings.get(key), label=key)
        decoded[key] = raw
        normalized_bindings[key] = artifact_bindings[key]
    return decoded, normalized_bindings


def validate_historical_parity_context(
    context: dict[str, Any],
    *,
    expected_report_date: str = "",
) -> tuple[bytes, dict[str, Any]]:
    if not isinstance(context, dict):
        raise ResearchContextError("historical parity context must be an object")
    if context.get("schema_version") != HISTORICAL_PARITY_SCHEMA:
        raise ResearchContextError("historical parity schema_version is invalid")
    if context.get("artifact_kind") != HISTORICAL_PARITY_KIND:
        raise ResearchContextError("historical parity artifact_kind is invalid")
    if context.get("historical_parity") is not True or context.get("research_only") is not True:
        raise ResearchContextError("historical parity context must be research-only")
    if context.get("action_authorized") is not False:
        raise ResearchContextError("historical parity context cannot authorize action")
    if context.get("runtime_network_dependency") is not False:
        raise ResearchContextError("historical parity context cannot have a runtime dependency")

    binding = context.get("source_binding")
    if not isinstance(binding, dict) or binding.get("scope") != "vendored_immutable_legacy_snapshot":
        raise ResearchContextError("historical parity source binding is invalid")
    repository = binding.get("repository")
    if type(repository) is not str or REPOSITORY_RE.fullmatch(repository) is None:
        raise ResearchContextError("historical parity repository is invalid")
    commit_sha = binding.get("commit_sha")
    if type(commit_sha) is not str or GIT_OBJECT_RE.fullmatch(commit_sha) is None:
        raise ResearchContextError("historical parity commit_sha is invalid")
    if binding.get("import_mode") != "one_time_vendored_snapshot":
        raise ResearchContextError("historical parity import_mode is invalid")
    if binding.get("runtime_network_dependency") is not False:
        raise ResearchContextError("historical parity binding has a runtime dependency")

    artifacts, _artifact_bindings = _historical_artifacts(context)
    raw = artifacts["action_plan"]
    plan = _strict_json_object(raw, label="historical parity action_plan payload")

    dates: dict[str, str] = {}
    for field in ("report_date", "signal_date", "exec_date", "exit_date"):
        value = plan.get(field)
        if type(value) is not str or DATE_RE.fullmatch(value) is None:
            raise ResearchContextError(f"historical parity payload {field} must be YYYYMMDD")
        dates[field] = value
    if dates["report_date"] != dates["exec_date"]:
        raise ResearchContextError("historical parity report_date must equal exec_date")
    if expected_report_date and dates["report_date"] != expected_report_date:
        raise ResearchContextError("historical parity payload does not match expected report_date")
    if context.get("report_date") != dates["report_date"]:
        raise ResearchContextError("historical parity wrapper report_date mismatch")
    if context.get("signal_date") != dates["signal_date"]:
        raise ResearchContextError("historical parity wrapper signal_date mismatch")
    if context.get("exec_date") != dates["exec_date"]:
        raise ResearchContextError("historical parity wrapper exec_date mismatch")
    if context.get("exit_date") != dates["exit_date"]:
        raise ResearchContextError("historical parity wrapper exit_date mismatch")
    if not dates["signal_date"] < dates["exec_date"] < dates["exit_date"]:
        raise ResearchContextError("historical parity date order is invalid")

    if set(artifacts) == set(HISTORICAL_ARTIFACT_KEYS):
        try:
            report = artifacts["decision_report"].decode("utf-8-sig")
        except UnicodeError as exc:
            raise ResearchContextError(
                "historical parity decision_report payload is not UTF-8"
            ) from exc
        report_lines = report.splitlines()
        if report_lines[:1] != [f"# Decision Report ({dates['report_date']})"]:
            raise ResearchContextError(
                "historical parity decision_report heading is date-inconsistent"
            )
        for field in ("signal_date", "exec_date", "exit_date"):
            expected = f"- {field}: **{dates[field]}**"
            if report_lines.count(expected) != 1:
                raise ResearchContextError(
                    f"historical parity decision_report {field} binding is invalid"
                )

        evaluation = _strict_json_object(
            artifacts["evaluation"],
            label="historical parity evaluation payload",
        )
        for field in ("signal_date", "exec_date", "exit_date"):
            if evaluation.get(field) != dates[field]:
                raise ResearchContextError(
                    f"historical parity evaluation {field} binding is invalid"
                )
    return raw, plan


def build_historical_parity_context(
    raw: bytes,
    *,
    repository: str,
    commit_sha: str,
    source_path: str,
    blob_sha: str,
    raw_sha256: str,
    report_raw: bytes | None = None,
    report_source_path: str = "",
    report_blob_sha: str = "",
    report_raw_sha256: str = "",
    evaluation_raw: bytes | None = None,
    evaluation_source_path: str = "",
    evaluation_blob_sha: str = "",
    evaluation_raw_sha256: str = "",
) -> dict[str, Any]:
    if hashlib.sha256(raw).hexdigest() != raw_sha256:
        raise ResearchContextError("historical parity raw SHA256 does not match input")
    if git_blob_sha(raw) != blob_sha:
        raise ResearchContextError("historical parity Git blob SHA does not match input")
    plan = _strict_json_object(raw, label="historical parity input")
    triple_artifact = report_raw is not None or evaluation_raw is not None
    if triple_artifact and (report_raw is None or evaluation_raw is None):
        raise ResearchContextError(
            "historical parity report and evaluation inputs must be provided together"
        )
    context = {
        "schema_version": HISTORICAL_PARITY_SCHEMA,
        "artifact_kind": HISTORICAL_PARITY_KIND,
        "historical_parity": True,
        "research_only": True,
        "action_authorized": False,
        "runtime_network_dependency": False,
        "report_date": plan.get("report_date"),
        "signal_date": plan.get("signal_date"),
        "exec_date": plan.get("exec_date"),
        "exit_date": plan.get("exit_date"),
        "source_binding": {
            "scope": "vendored_immutable_legacy_snapshot",
            "repository": repository,
            "commit_sha": commit_sha,
            "import_mode": "one_time_vendored_snapshot",
            "runtime_network_dependency": False,
        },
    }
    if triple_artifact:
        artifact_inputs = {
            "action_plan": (
                raw,
                source_path,
                blob_sha,
                raw_sha256,
            ),
            "decision_report": (
                report_raw,
                report_source_path,
                report_blob_sha,
                report_raw_sha256,
            ),
            "evaluation": (
                evaluation_raw,
                evaluation_source_path,
                evaluation_blob_sha,
                evaluation_raw_sha256,
            ),
        }
        context["source_binding"]["artifacts"] = {
            key: {"path": value[1], "blob_sha": value[2], "raw_sha256": value[3]}
            for key, value in artifact_inputs.items()
        }
        context["payloads_base64"] = {
            key: base64.b64encode(value[0]).decode("ascii")
            for key, value in artifact_inputs.items()
        }
    else:
        context["source_binding"].update(
            {"path": source_path, "blob_sha": blob_sha, "raw_sha256": raw_sha256}
        )
        context["payload_base64"] = base64.b64encode(raw).decode("ascii")
    validate_historical_parity_context(context)
    return context


def publish_vendored_research_context(
    input_path: Path,
    output_root: Path,
    *,
    repository: str,
    commit_sha: str,
    source_path: str,
    blob_sha: str,
    raw_sha256: str,
    report_input_path: Path | None = None,
    report_source_path: str = "",
    report_blob_sha: str = "",
    report_raw_sha256: str = "",
    evaluation_input_path: Path | None = None,
    evaluation_source_path: str = "",
    evaluation_blob_sha: str = "",
    evaluation_raw_sha256: str = "",
) -> tuple[Path, dict[str, Any]]:
    raw = input_path.read_bytes()
    context = build_historical_parity_context(
        raw,
        repository=repository,
        commit_sha=commit_sha,
        source_path=source_path,
        blob_sha=blob_sha,
        raw_sha256=raw_sha256,
        report_raw=report_input_path.read_bytes() if report_input_path else None,
        report_source_path=report_source_path,
        report_blob_sha=report_blob_sha,
        report_raw_sha256=report_raw_sha256,
        evaluation_raw=(
            evaluation_input_path.read_bytes() if evaluation_input_path else None
        ),
        evaluation_source_path=evaluation_source_path,
        evaluation_blob_sha=evaluation_blob_sha,
        evaluation_raw_sha256=evaluation_raw_sha256,
    )
    report_date = str(context["report_date"])
    output = output_root.resolve() / "outputs" / "decision"
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"research_context_{report_date}.json"
    path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path, context


def publish_research_context(
    source_root: Path,
    output_root: Path,
    report_date: str = "",
) -> tuple[Path, dict[str, Any]]:
    context = build_research_context(source_root, report_date)
    chosen_date = str(context["report_date"])
    output = output_root.resolve() / "outputs" / "decision"
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"research_context_{chosen_date}.json"
    if path.exists():
        try:
            existing = _strict_json_object(
                path.read_bytes(),
                label="existing research context",
            )
        except (OSError, ResearchContextError) as exc:
            raise ResearchContextError(
                f"existing research context is not readable strict JSON: {path}"
            ) from exc
        if isinstance(existing, dict) and existing.get("schema_version") == HISTORICAL_PARITY_SCHEMA:
            validate_historical_parity_context(
                existing,
                expected_report_date=chosen_date,
            )
            return path, existing
    path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path, context


__all__ = [
    "HISTORICAL_PARITY_KIND",
    "HISTORICAL_PARITY_SCHEMA",
    "HISTORICAL_ARTIFACT_KEYS",
    "RESEARCH_CONTEXT_KIND",
    "RESEARCH_CONTEXT_SCHEMA",
    "ResearchContextError",
    "build_historical_parity_context",
    "build_research_context",
    "git_blob_sha",
    "project_research_context",
    "publish_research_context",
    "publish_vendored_research_context",
    "validate_historical_parity_context",
    "validate_research_context",
]
