"""Read-only, externally pinned staging evidence; never a release or admission.

No inference, model loading, old raw/prediction/ledger read, network, or write is
performed. Natural-origin evidence must remain in its original authenticated
run/window and still has generation_mode=REPLAY. The returned objects cannot
grant NATURAL status or production eligibility.
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import inputs
from .promotion_schema import validate_bundle
from .bundle import _open_physical_directory, _regular_bytes
from .bundle_rehearsal import verify_natural_evidence, UPSTREAMS
from .daybook import _promotion_rows
from .schedule import dates_for
from .storage import encoded
from .trigger import bind_schedule

HASH = re.compile(r"[0-9a-f]{64}\Z")
ORIGINS = {"HISTORICAL_PINNED_INPUT_REPLAY", "NATURAL_SCHEDULE_STAGING"}


class ReleaseEvidenceError(ValueError):
    """Evidence is incomplete, inconsistent, stale, or not a staging artifact."""


class _PromotionInfo(dict):
    """JSON-compatible public data plus original, non-serialized hash anchors."""
    __slots__ = ("_anchors",)


def _require(ok, message):
    if not ok:
        raise ReleaseEvidenceError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value, label):
    _require(isinstance(value, str) and HASH.fullmatch(value), f"{label}: external SHA256 required")
    return value


def _same(left, right, label):
    _require(encoded(left) == encoded(right), f"{label}: binding mismatch")


def _number(value):
    _require(type(value) in (int, float) and math.isfinite(value), "finite numeric proxy score required")
    return float(value)


def _read(directory, name, expected):
    _hash(expected, name)
    descriptor = _open_physical_directory(directory)
    try:
        raw = _regular_bytes(descriptor, name, inputs.MAX_FILE_BYTES)
    finally:
        os.close(descriptor)
    _require(_sha(raw) == expected, f"{name}: external artifact SHA mismatch")
    return inputs._object(raw), raw


def _context(root, now):
    root = Path(root).resolve(strict=True)
    git_root = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"], text=True).strip()
    _require(Path(git_root).resolve() == root, "root must be the inference source checkout")
    dates, _, head, _ = inputs._calendar(root)
    config_path = root / "forward/config.json"
    _require(not any(p.is_symlink() for p in (config_path, *config_path.parents)), "config symlink rejected")
    config = inputs._object(config_path.read_bytes())
    _require(config.get("phase") == "MIGRATION_ACCEPTANCE"
        and config.get("production_enabled") is False and config.get("activated_at_utc") is None
        and config.get("start_signal_date") is None and config.get("legacy_statistics_import_allowed") is False
        and config.get("formal_trade_actions_allowed") is False, "reader requires an inactive non-trading migration epoch")
    _require(config.get("calendar_path") == inputs.CALENDAR and config.get("calendar_sha256") == inputs.CALENDAR_SHA256,
             "only the fixed committed SSE calendar is permitted")
    return root, dates, head, inputs._aware(now or datetime.now(timezone.utc))


def _receipt(receipt, raw, expected, kind, head, date, rows):
    _require(raw == encoded(receipt) and _sha(raw) == expected, "receipt must have the pinned canonical writer encoding")
    _require(set(receipt) == {"schema_version", "kind", "signal_date", "source_revision", "files",
        "generation_mode", "computation_completed", "inference_performed", "production_enabled",
        "forward_ledger_eligible", "formal_trade_count", "input_evidence"}, "receipt schema changed")
    _require(receipt.get("schema_version") == "dc20_forward_rehearsal_receipt_v1"
        and receipt.get("kind") == kind and receipt.get("source_revision") == head
        and receipt.get("signal_date") == date and receipt.get("generation_mode") == "REPLAY"
        and receipt.get("computation_completed") is True
        and receipt.get("inference_performed") is bool(rows)
        and receipt.get("production_enabled") is False and receipt.get("forward_ledger_eligible") is False
        and type(receipt.get("formal_trade_count")) is int and receipt["formal_trade_count"] == 0,
        "not this revision's complete non-producing REPLAY receipt")
    wanted = {"primary.json", "promotion.json"} if kind == "PROMOTION_INFERENCE" else {"profit.json"}
    _require(isinstance(receipt.get("files"), dict) and set(receipt["files"]) == wanted,
             "receipt file inventory is incomplete or unexpected")
    for value in receipt["files"].values():
        _hash(value, "receipt file")


def _evidence(root, evidence, day, head, now, env, generated):
    _require(isinstance(evidence, dict) and evidence.get("origin") in ORIGINS
        and evidence.get("forward_ledger_eligible") is False
        and evidence.get("signal_date") == day["signal_date"], "missing exact-D staging input evidence")
    source = day["source"]
    manifest_sha = _hash(source.get("input_manifest_sha256"), "input manifest")
    _same(evidence.get("manifest_sha256"), manifest_sha, "input manifest evidence")
    _hash(source.get("input_bundle_sha256"), "input canonical bundle")
    _require(source.get("input_binding_basis") == "verified_manifest_and_immutable_upstream_bytes",
             "independent upstream bundle binding required")
    collector_commit = source.get("input_collector_commit")
    _require(isinstance(collector_commit, str) and inputs.COMMIT_RE.fullmatch(collector_commit), "collector revision missing")
    collected = inputs._aware(source.get("input_collected_at_utc"))
    _require(collected <= inputs._aware(day["generated_at_utc"]) <= generated <= now, "source/computation timestamps are out of order")
    sources = {}
    for field, repo in zip(("candidate", "market"), UPSTREAMS):
        binding = source.get(field)
        _require(isinstance(binding, dict) and binding.get("source_repository") == repo
            and binding.get("binding_basis") == "verified_input_manifest"
            and binding.get("input_manifest_sha256") == manifest_sha
            and isinstance(binding.get("resolved_commit"), str)
            and inputs.COMMIT_RE.fullmatch(binding["resolved_commit"]), "upstream repository/commit/manifest binding mismatch")
        sources[repo] = binding["resolved_commit"]
    current_env = os.environ if env is None else env
    if evidence["origin"] == "HISTORICAL_PINNED_INPUT_REPLAY":
        _require(current_env.get("GITHUB_EVENT_NAME") != "schedule", "natural evidence cannot downgrade to historical REPLAY")
        _same(sorted(evidence), sorted({"origin", "manifest_sha256", "signal_date", "forward_ledger_eligible"}), "historical evidence keys")
        return None
    gate = verify_natural_evidence(root, evidence, env=env, now=now.isoformat())
    acceptance = evidence["acceptance_receipt"]
    _same([day[key] for key in ("signal_date", "exec_date", "exit_date")],
          [gate[key] for key in ("signal_date", "exec_date", "exit_date")], "natural exact dates")
    _same(acceptance.get("sources"), sources, "natural upstream commits")
    _require(collector_commit == head and inputs._aware(acceptance["started_at_utc"]) <= collected
             <= inputs._aware(acceptance["finished_at_utc"]), "natural collector revision/time differs from acceptance")
    inference_gate = evidence.get("inference_gate")
    _require(isinstance(inference_gate, dict), "natural inference completion gate missing")
    identity = acceptance["identity"]
    completed = inputs._aware(inference_gate.get("checked_at_utc"))
    _require(generated <= completed <= now, "natural inference completion time mismatch")
    dates, _, _, _ = inputs._calendar(root)
    rebuilt = bind_schedule("schedule", identity["schedule"], identity["run_created_at_utc"], completed.isoformat(), dates)
    _same(inference_gate, rebuilt, "recorded natural inference gate")
    return gate


def _primary_contract(root, primary, receipt, receipt_raw, receipt_sha, evidence, dates, head, now, env):
    _require(set(primary) == {"day", "runtime_rows", "runtime_columns", "runtime_sha256", "feature_snapshot_sha256", "receipt"}, "P0 bundle schema changed")
    day, _, _ = validate_bundle(primary)  # Pure P0 checks; P1 module is not imported.
    _require(set(day) == {"signal_date", "exec_date", "exit_date", "generated_at_utc", "generation_mode", "source", "rows"}, "P0 day schema changed")
    _promotion_rows(day["rows"])
    _same([day[key] for key in ("signal_date", "exec_date", "exit_date")],
          list(dates_for(day["signal_date"], dates)), "adjacent SSE D/T/T1")
    source = day["source"]
    _require(source.get("source_commit") == head and source.get("repository") == "njedu2023-prog/DC20",
             "P0 source checkout differs from current HEAD")
    for field in ("legacy_action_or_statistics_read", "secondary_models_loaded", "forward_ledger_eligible", "root_market_or_candidate_read"):
        _require(source.get(field) is False, f"P0 forbidden dependency/status: {field}")
    compute_receipt = primary["receipt"]
    _require(compute_receipt.get("schema_version") == "dc20_forward_promotion_compute_receipt_v1"
        and compute_receipt.get("gate_status") == "READY" and compute_receipt.get("publication_performed") is False
        and type(compute_receipt.get("selected_count")) is int
        and type(compute_receipt.get("promotion_pool_size")) is int, "P0 computation receipt invalid")
    generated = inputs._aware(day["generated_at_utc"])
    close = datetime.strptime(day["signal_date"] + "150000", "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    _require(close <= generated <= now, "P0 computation is before D close or in the future")
    _receipt(receipt, receipt_raw, receipt_sha, "PROMOTION_INFERENCE", head, day["signal_date"], day["rows"])
    _same(primary["day"], day, "P0 day")
    _same(receipt.get("input_evidence"), evidence, "P0 input evidence")
    return _evidence(root, evidence, day, head, now, env, generated)


def read_promotion(root, primary_dir, primary_receipt_sha, *, env=None, now=None):
    root, dates, head, current = _context(root, now)
    receipt, raw = _read(primary_dir, "receipt.json", primary_receipt_sha)
    files = receipt.get("files", {})
    _require(isinstance(files, dict) and set(files) == {"primary.json", "promotion.json"}, "complete P0 files required")
    primary, primary_raw = _read(primary_dir, "primary.json", files["primary.json"])
    day, day_raw = _read(primary_dir, "promotion.json", files["promotion.json"])
    _same(primary.get("day"), day, "P0 runtime/promotion artifact")
    # Both are emitted by the canonical staging writer. Requiring its encoding
    # lets the in-memory handoff be checked later without reopening disk.
    _require(primary_raw == encoded(primary) and day_raw == encoded(day), "P0 payload encoding differs from staging writer")
    evidence = copy.deepcopy(receipt.get("input_evidence"))
    gate = _primary_contract(root, primary, receipt, raw, primary_receipt_sha, evidence, dates, head, current, env)
    result = _PromotionInfo(primary=primary, receipt=receipt, receipt_sha256=primary_receipt_sha, evidence=evidence, gate=gate)
    result._anchors = (primary_receipt_sha, files["primary.json"], files["promotion.json"])
    return result


def read_profit(root, primary_info, profit_dir, profit_receipt_sha, *, env=None, now=None):
    _require(isinstance(primary_info, _PromotionInfo), "read_profit requires a fresh read_promotion result, not an unverified dict")
    from . import profit  # P1 is optional and must never be imported by P0 readers.
    root, dates, head, current = _context(root, now)
    info = copy.deepcopy(primary_info)
    p0sha, primary_sha, day_sha = info._anchors
    primary, p0receipt = info["primary"], info["receipt"]
    _require(info["receipt_sha256"] == p0sha and _sha(encoded(p0receipt)) == p0sha
        and _sha(encoded(primary)) == primary_sha and _sha(encoded(primary["day"])) == day_sha,
        "verified P0 information was altered after reading")
    _primary_contract(root, primary, p0receipt, encoded(p0receipt), p0sha, info["evidence"], dates, head, current, env)
    receipt, raw = _read(profit_dir, "receipt.json", profit_receipt_sha)
    _require(isinstance(receipt.get("files"), dict) and set(receipt["files"]) == {"profit.json"}, "complete P1 file required")
    result, _ = _read(profit_dir, "profit.json", receipt["files"]["profit.json"])
    _require(set(result) == {"schema_version", "status", "generation_mode", "research_only", "signal_date",
        "exec_date", "exit_date", "generated_at_utc", "source", "rows", "rehearsal_promotion_receipt_sha256"}, "P1 result schema changed")
    day, source = primary["day"], result.get("source")
    _require(isinstance(source, dict), "profit source missing")
    _same([result.get(key) for key in ("signal_date", "exec_date", "exit_date")],
          [day[key] for key in ("signal_date", "exec_date", "exit_date")], "P1 true dates")
    _require(result.get("schema_version") == "dc20_forward_profit_inference_v1"
        and result.get("status") == "REPLAY_RESEARCH_ONLY" and result.get("generation_mode") == "REPLAY"
        and result.get("research_only") is True and result.get("rehearsal_promotion_receipt_sha256") == p0sha,
        "P1 is not research-only REPLAY bound to its P0 receipt")
    for key, expected in {"source_commit": head, "promotion_source_sha256": profit._digest(day),
                         "runtime_sha256": primary["runtime_sha256"], "feature_snapshot_sha256": primary["feature_snapshot_sha256"],
                         "model_sha256": profit.MODEL_SHA256, "feature_columns_sha256": profit.FEATURES_SHA256,
                         "feature_count": 156, "model_status": "INTERNAL_CHALLENGER_NOT_READY",
                         "historical_feature_use": "strictly_before_D_lagged_features_not_performance_statistics"}.items():
        _same(source.get(key), expected, f"P1 {key}")
    _require(source.get("research_only") is True and source.get("calibrated_probability_output") is False
        and source.get("computation_completed") is True, "P1 research/model status changed")
    dependencies = source.get("dependencies")
    _require(isinstance(dependencies, dict), "P1 dependency hashes missing")
    for path, value in dependencies.items():
        _require(isinstance(path, str) and not path.startswith("/") and "\\" not in path
            and all(part not in {"", ".", ".."} for part in path.split("/")), "unsafe P1 dependency path")
        _hash(value, "P1 dependency")
    for path, expected in {profit.MODEL_PATH: profit.MODEL_SHA256, profit.CALENDAR_PATH: profit.CALENDAR_SHA256,
                           profit.HISTORY_PATH: profit.HISTORY_SHA256}.items():
        _same(dependencies.get(path), expected, "P1 fixed model/calendar/history")
    rows = result.get("rows")
    _require(isinstance(rows, list) and len(rows) == len(day["rows"]), "P1 must retain every real P0 member")
    ranks, scores = set(), {}
    for frozen, row in zip(day["rows"], rows):
        _require(isinstance(row, dict) and set(row) == {"ts_code", "name", "promotion_rank", "profit_rank", "profit_score"}, "P1 row schema changed")
        for key in ("ts_code", "name", "promotion_rank"):
            _same(row[key], frozen[key], f"P1 frozen {key}/physical promotion order")
        rank, score = row["profit_rank"], _number(row["profit_score"])
        _require(type(rank) is int and rank not in ranks and 0 <= score <= 1, "P1 profit rank/score invalid")
        ranks.add(rank)
        scores[row["ts_code"]] = score
    _require(ranks == set(range(1, len(rows) + 1)), "P1 ranking is not a complete permutation")
    ranked = sorted(rows, key=lambda row: row["profit_rank"])
    _require(all(a["profit_score"] >= b["profit_score"] for a, b in zip(ranked, ranked[1:])), "P1 rank must be nonincreasing by score")
    _require(source.get("inference_performed") is bool(rows) and source.get("model_loaded") is bool(rows), "P1 empty/model-loaded status mismatch")
    if rows:
        hashes = source.get("model_source_hashes")
        _require(isinstance(hashes, dict), "loaded P1 model evidence missing")
        for value in hashes.values():
            _hash(value, "loaded P1 model source")
        for key, expected in {"model_pickle_sha256": profit.MODEL_SHA256, "strict_sse_calendar_sha256": profit.CALENDAR_SHA256,
                              "full_history_ledger_sha256": profit.HISTORY_SHA256}.items():
            _same(hashes.get(key), expected, "loaded P1 frozen source")
        _hash(source.get("mixed_feature_snapshot_sha256"), "mixed feature snapshot")
        _hash(source.get("computation_source_sha256"), "profit computation source")
        prior_date = source.get("lagged_prior_max_history_exit_date")
        _require(prior_date in dates and prior_date < day["signal_date"], "P1 priors use same-day/future/unbound truth")
        proxies = source.get("row_proxy_scores")
        _require(isinstance(proxies, list) and len(proxies) == len(rows), "P1 proxy components incomplete")
        by_code = {}
        for row, proxy in zip(rows, proxies):
            _require(isinstance(proxy, dict) and set(proxy) == {"ts_code", "fill_proxy_score", "conditional_profit_score"}
                and proxy["ts_code"] == row["ts_code"], "P1 proxy identity/order changed")
            fill, conditional = _number(proxy["fill_proxy_score"]), _number(proxy["conditional_profit_score"])
            _require(0 <= fill <= 1 and 0 <= conditional <= 1 and math.isclose(fill * conditional, row["profit_score"], rel_tol=0, abs_tol=1e-15), "P1 joint score differs from its proxy components")
            by_code[row["ts_code"]] = (fill, conditional)
        expected_order = sorted(scores, key=lambda code: (-scores[code], -by_code[code][1], -by_code[code][0], code))
        _same([row["ts_code"] for row in ranked], expected_order, "P1 frozen tie ordering")
    else:
        _require(source.get("empty_event_reason") == "P0_REAL_TOPN_EMPTY", "empty P1 must bind real empty P0")
    generated = inputs._aware(result.get("generated_at_utc"))
    _require(inputs._aware(day["generated_at_utc"]) <= generated <= current, "P1 computation predates P0 or is future")
    _receipt(receipt, raw, profit_receipt_sha, "PROFIT_INFERENCE", head, day["signal_date"], rows)
    evidence = receipt.get("input_evidence")
    _require(isinstance(evidence, dict), "P1 input evidence missing")
    _same({key: value for key, value in evidence.items() if key != "inference_gate"},
          {key: value for key, value in info["evidence"].items() if key != "inference_gate"}, "P0/P1 same input evidence")
    gate = _evidence(root, evidence, day, head, current, env, generated)
    return dict(profit=result, receipt=receipt, receipt_sha256=profit_receipt_sha, evidence=copy.deepcopy(evidence), gate=gate)
