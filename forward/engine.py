"""Read-only migration bridge, not an independent inference engine.

No legacy Python modules or model pickle are imported.  Frozen values are
verified and copied; no model is run, no ledger is opened, no latest pointer is
read.  Even an originally NATURAL source becomes REPLAY in the new epoch.

This full-package replay is NOT a production publication entry point.  A future
P0 inference/publisher must publish its promotion list independently of profit,
verification, statistics, or this adapter; a missing P1 must not block that P0.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path


class FrozenDayError(ValueError):
    """Missing or inconsistent exact-D evidence; never fall back to a date."""


def _require(condition, message):
    if not condition:
        raise FrozenDayError(message)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _safe_file(root, relative):
    _require(isinstance(relative, str) and "\\" not in relative, "unsafe source path")
    parts = relative.split("/")
    _require(all(part not in {"", ".", ".."} for part in parts), "unsafe source path")
    current = root
    for part in parts:
        current = current / part
        _require(not current.is_symlink(), "symlink source is forbidden")
    _require(current.is_file(), f"missing exact-D evidence: {relative}")
    return current


def _bytes(root, relative, expected=None):
    data = _safe_file(root, relative).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if expected is not None:
        _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected)
                 and actual == expected, f"source SHA mismatch: {relative}")
    return data, actual


def _object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(FrozenDayError("nonfinite JSON")))
    except (ValueError, UnicodeError) as exc:
        raise FrozenDayError("invalid JSON evidence") from exc
    _require(isinstance(value, dict), "JSON evidence must be an object")
    return value


def _table(data):
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
        _require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)),
                 "missing/duplicate CSV header")
        rows = list(reader)
        _require(all(None not in row and None not in row.values() for row in rows), "ragged CSV")
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise FrozenDayError("invalid CSV evidence") from exc


def _number(value, *, csv_value=False):
    _require(not isinstance(value, bool) and (isinstance(value, (int, float)) or
             (csv_value and isinstance(value, str) and bool(value.strip()))), "invalid numeric value")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise FrozenDayError("invalid numeric value") from exc
    _require(math.isfinite(number), "nonfinite numeric value")
    return number


def _int(value, *, csv_value=False):
    number = _number(value, csv_value=csv_value)
    _require(number.is_integer(), "noninteger rank/count")
    return int(number)


def _close(a, b, *, csv_a=False):
    return math.isclose(_number(a, csv_value=csv_a), _number(b), rel_tol=0, abs_tol=1e-15)


def _bundle(payload):
    fields = ("schema_version", "artifact_kind", "contract_version", "signal_date", "exec_date",
              "exit_date", "feature_as_of_date", "feature_snapshot_sha256", "promotion_pool_size",
              "top10_count", "top10_members_sha256", "models")
    row_fields = ("ts_code", "name", "industry", "stage_transition", "top10_selected", "promotion_rank",
                  "predicted_promotion_probability", "big_loss_safety_rank", "predicted_big_loss_probability",
                  "profit_rank", "predicted_profit_probability")
    core = {field: payload.get(field) for field in fields}
    core["rows"] = [{field: row.get(field) for field in row_fields} for row in payload["rows"]]
    return _digest(core)


def _projection_snapshot(payload):
    return _digest({key: value for key, value in payload.items() if key not in {"snapshot_sha256", "downloads"}})


def load_frozen_day(root, signal_date, *, generation_mode="REPLAY"):
    """Verify an exact frozen P0/P1 bundle and copy it to an unledgered day.

    NATURAL is intentionally rejected: this adapter cannot establish a new
    epoch's contemporaneous inference or P1 generation timestamp.
    """
    _require(generation_mode == "REPLAY", "migration bridge permits REPLAY only; no new forward freeze")
    _require(isinstance(signal_date, str) and re.fullmatch(r"20\d{6}", signal_date), "D must be YYYYMMDD")
    try:
        datetime.strptime(signal_date, "%Y%m%d")
        root = Path(root).resolve(strict=True)
        return _load(root, signal_date)
    except FrozenDayError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise FrozenDayError(f"incomplete or invalid frozen evidence: {exc}") from exc


def _load(root, date):
    inventory_data, inventory_sha = _bytes(Path(__file__).resolve().parent, "model_inventory.json")
    inventory = _object(inventory_data)
    assets = {item["path"]: item for item in inventory["assets"]}
    for path, item in assets.items():
        if item.get("verify_for_replay"):
            _bytes(root, path, item["sha256"])
    receipt_path = f"outputs/decision/primary_d_receipt_{date}.json"
    p0_path = f"outputs/decision/three_rank_top10_{date}.json"
    p0_csv_path = f"outputs/decision/three_rank_top10_{date}.csv"
    runtime_path = f"outputs/decision/primary_d_runtime_features_{date}.csv"
    p1_path = f"outputs/decision/executable_profit_research/projection_{date}.json"
    p1_csv_path = f"outputs/decision/executable_profit_research/projection_{date}.csv"
    receipt_data, receipt_sha = _bytes(root, receipt_path)
    receipt = _object(receipt_data)
    _require(receipt["schema_version"] == "dc20_primary_d_receipt_v1" and
             receipt["artifact_kind"] == "p0_promotion_only_d_list_receipt" and
             receipt["primary_status"] == "READY", "P0 receipt identity/status mismatch")
    for field in ("action_authorized", "action_input_consumed", "future_market_data_consumed",
                  "latest_fallback_used", "runtime_dependency_on_top10_decision"):
        _require(receipt.get(field) is False, f"P0 safety boundary: {field}")
    _require(type(receipt.get("formal_trade_count")) is int and receipt["formal_trade_count"] == 0,
             "P0 trade count boundary")
    outputs = receipt["outputs"]
    artifacts = {receipt_path: receipt_sha}
    for path_key, sha_key, expected_path in (("json_path", "json_sha256", p0_path),
            ("csv_path", "csv_sha256", p0_csv_path), ("runtime_features_path", "runtime_features_sha256", runtime_path)):
        _require(outputs[path_key] == expected_path, "P0 exact-D path mismatch")
        _, artifacts[expected_path] = _bytes(root, expected_path, outputs[sha_key])
    p0 = _object(_bytes(root, p0_path)[0])
    runtime = _table(_bytes(root, runtime_path)[0])
    p1_data, artifacts[p1_path] = _bytes(root, p1_path)
    p1 = _object(p1_data)
    _require(p0["schema_version"] == "decision_three_rank_top10_v1" and
             p0["artifact_kind"] == "d_close_independent_three_rank_top10" and
             p0["feature_as_of_date"] == date, "P0 contract identity mismatch")
    _require(p1["schema_version"] == "dc20_primary_mixed_profit_research_projection_v1" and
             p1["artifact_kind"] == "immutable_d_frozen_primary_mixed_profit_research_projection",
             "P1 projection identity mismatch")
    dates = [p0[key] for key in ("signal_date", "exec_date", "exit_date")]
    _require(dates[0] == date and all([payload[key] for key in ("signal_date", "exec_date", "exit_date")] == dates
             for payload in (receipt, p1)), "P0/P1 exact D/T/T+1 mismatch")
    calendar_path = "data/market/trade_cal_sse.csv"
    calendar_data, calendar_sha = _bytes(root, calendar_path, receipt["inputs"]["calendar"]["sha256"])
    calendar = [row for row in _table(calendar_data) if row["exchange"] == "SSE"]
    _require(len({r["cal_date"] for r in calendar}) == len(calendar), "duplicate SSE calendar date")
    sessions = sorted(row["cal_date"] for row in calendar if row["is_open"] == "1")
    _require(date in sessions and sessions[sessions.index(date):sessions.index(date)+3] == dates,
             "D/T/T+1 are not adjacent strict SSE sessions")
    rows, profits = p0["rows"], p1["rows"]
    _require(isinstance(rows, list) and isinstance(profits, list) and 0 <= len(rows) <= 10 and
             len(rows) == p0["top10_count"] == p1["candidate_count"] == len(profits), "real TopN count mismatch")
    n = len(rows)
    codes = [row["ts_code"] for row in rows]
    _require(len(set(codes)) == n and all(re.fullmatch(r"\d{6}\.(SH|SZ)", code) for code in codes), "duplicate/invalid candidate")
    members = _digest({"schema": "dc20_three_rank_member_set_v1", "signal_date": date, "members": sorted(codes)})
    for key, p1_key in (("top10_members_sha256", "top10_members_sha256"),
                        ("feature_snapshot_sha256", "source_feature_snapshot_sha256"), ("bundle_sha256", "source_bundle_sha256")):
        _require(outputs[key] == p0[key] == p1[p1_key], f"P0/P1 fingerprint mismatch: {key}")
    _require(p0["top10_members_sha256"] == members and _bundle(p0) == p0["bundle_sha256"], "P0 bundle/member digest mismatch")
    _require(_projection_snapshot(p1) == p1["snapshot_sha256"], "P1 snapshot digest mismatch")
    mode = receipt["generation_mode"]
    natural = mode == "NATURAL"
    _require(mode in {"NATURAL", "RETROSPECTIVE_RECOVERY"} and p1["generation_mode"] == mode and
             p1["status"] == ("PROSPECTIVE_RESEARCH" if natural else "RETROSPECTIVE_NON_FORWARD_RESEARCH") and
             p1["prospective"] is natural and p1["retrospective_non_forward"] is (not natural) and
             receipt["prospective"] is natural and receipt["not_forward_generated"] is (not natural), "source generation mode mismatch")
    boundaries = p1["boundaries"]
    for key in ("action_input_consumed", "actual_execution_claimed", "broker_or_order_integration_allowed",
                "estimated_probability_calibrated", "formal_probability_allowed", "formal_rank_allowed",
                "may_change_promotion_membership_or_rank", "may_create_trade_action", "official_trade_action_allowed"):
        _require(boundaries.get(key) is False, f"P1 safety boundary: {key}")
    _require(p1["research_only"] is True and boundaries.get("proxy_scores_uncalibrated") is True,
             "P1 research disclosure mismatch")
    bindings = p1["source_bindings"]
    for key, path in (("primary_receipt", receipt_path), ("runtime_features", runtime_path)):
        _require(bindings[key]["path"] == path and bindings[key]["sha256"] == artifacts[path], "P1 receipt/runtime binding mismatch")
    for key in ("json_path", "json_sha256", "csv_path", "csv_sha256", "bundle_sha256", "feature_snapshot_sha256", "top10_members_sha256"):
        _require(bindings["three_rank"][key] == outputs[key], "P1 three-rank binding mismatch")
    contract_path = "models/decision_primary_profit_research_contract.json"
    _require(bindings["contract"]["path"] == contract_path and bindings["contract"]["sha256"] ==
             p1["contract_file_sha256"] == assets[contract_path]["sha256"], "P1 contract binding mismatch")
    _require(p1["contract_id"] == bindings["contract"]["contract_id"] == "dc20_primary_profit_research_20260827_v1", "P1 contract id mismatch")
    download = p1["downloads"]
    _require(download["json_url"] == p1_path and download["csv_url"] == p1_csv_path and download["row_count"] == n,
             "P1 dated download mismatch")
    _, artifacts[p1_csv_path] = _bytes(root, p1_csv_path, download["csv_sha256"])
    for role, model in (("promotion", p0["models"]["promotion"]), ("profit", p1["model"])):
        expected = inventory["active_source_models"][role]
        _require(model["artifact_sha256"] == expected["sha256"], f"{role} model bytes changed")
    _require(p0["models"]["promotion"]["ranking_ready"] is True and p0["models"]["promotion"]["probability_ready"] is True,
             "promotion model not ready")
    _require(receipt["inputs"]["promotion_model"]["artifact_sha256"] == p0["models"]["promotion"]["artifact_sha256"],
             "P0 receipt model binding mismatch")
    _require(p1["model"]["feature_columns_sha256"] == inventory["active_source_models"]["profit"]["feature_columns_sha256"] and
             p1["model"]["calibrated_probability_output"] is False and
             p1["model"]["status"] == inventory["active_source_models"]["profit"]["release_status"], "profit feature/calibration identity changed")
    _require(len(runtime) == outputs["runtime_feature_row_count"] == p0["promotion_pool_size"] and
             n == min(10, len(runtime)) == outputs["runtime_selected_count"], "runtime real pool size mismatch")
    selected, identities, all_codes = [], [], set()
    generated = p0["generated_at_utc"]
    _require(datetime.fromisoformat(generated.replace("Z", "+00:00")).utcoffset() is not None, "generation timestamp needs timezone")
    for row in sorted(runtime, key=lambda x: x["ts_code"]):
        code = row["ts_code"]
        _require(code not in all_codes and re.fullmatch(r"\d{6}\.(SH|SZ)", code), "duplicate runtime candidate")
        all_codes.add(code)
        identity = f"{date}|{code}|{row['stage_transition']}"
        _require(row["signal_date"] == date and row["identity"] == identity and
                 row["feature_snapshot_sha256"] == p0["feature_snapshot_sha256"] and row["generated_at_utc"] == generated,
                 "runtime row date/identity/freeze mismatch")
        flag, rank = _int(row["top10_selected"], csv_value=True), _int(row["promotion_rank"], csv_value=True)
        _require(flag in {0, 1}, "runtime selected flag invalid")
        identities.append(dict(identity=identity, ts_code=code, stage_transition=row["stage_transition"], top10_selected=flag, promotion_rank=rank))
        if flag:
            selected.append(row)
    identity_sha = _digest({"schema": "dc20_primary_d_runtime_identity_v1", "signal_date": date, "rows": identities})
    _require(identity_sha == outputs["runtime_identity_sha256"] == bindings["runtime_features"]["identity_sha256"] and
             bindings["runtime_features"]["row_count"] == len(runtime) and bindings["runtime_features"]["selected_count"] == n and
             bindings["runtime_features"]["feature_snapshot_sha256"] == p0["feature_snapshot_sha256"] and
             bindings["primary_receipt"]["generation_mode"] == mode,
             "runtime identity/count binding mismatch")
    selected.sort(key=lambda row: _int(row["promotion_rank"], csv_value=True))
    _require([row["ts_code"] for row in selected] == codes, "runtime promotion order changed")
    _require({r["ts_code"] for r in profits} == set(codes), "profit membership changed")
    _require(profits == sorted(profits, key=lambda r: (-_number(r["research_joint_proxy_score"]),
             -_number(r["research_conditional_profit_score"]), -_number(r["research_fill_proxy_score"]), r["ts_code"])),
             "profit ordering changed")
    profit_by_code = {}
    for position, row in enumerate(profits, 1):
        _require(type(row["executable_profit_research_rank"]) is int and row["executable_profit_research_rank"] == position,
                 "profit rank permutation changed")
        joint, fill, conditional = [_number(row[key]) for key in ("research_joint_proxy_score", "research_fill_proxy_score", "research_conditional_profit_score")]
        _require(all(0 <= value <= 1 for value in (joint, fill, conditional)) and
                 math.isclose(joint, fill * conditional, rel_tol=0, abs_tol=1e-15), "profit proxy score identity mismatch")
        profit_by_code[row["ts_code"]] = row
    result = []
    for position, (row, rt) in enumerate(zip(rows, selected), 1):
        profit = profit_by_code[row["ts_code"]]
        _require(type(row["promotion_rank"]) is int and row["promotion_rank"] == position and
                 type(row["top10_selected"]) in (bool, int) and row["top10_selected"] == 1 and
                 type(profit["promotion_rank"]) is int and profit["promotion_rank"] == position and
                 _int(rt["promotion_rank"], csv_value=True) == position, "promotion rank changed")
        for key in ("ts_code", "name", "industry", "stage_transition"):
            _require(row[key] == rt[key] == profit[key], f"candidate identity changed: {key}")
        probability = _number(row["predicted_promotion_probability"])
        _require(0 <= probability <= 1 and _close(rt["predicted_promotion_probability"], probability, csv_a=True) and
                 _close(profit["predicted_promotion_probability"], probability) and row["stage_transition"] in {"2→3", "3→4"},
                 "promotion probability/stage changed")
        label = rt.get("path_label", "").strip() or None
        missing_path = rt.get("path_label_code") == "INSUFFICIENT" or label in {None, "路径数据不足"}
        delta_raw = rt.get("path_strength_delta", "").strip()
        delta = None if missing_path or delta_raw.lower() in {"", "nan", "null", "none"} else _number(delta_raw, csv_value=True)
        result.append(dict(ts_code=row["ts_code"], name=row["name"], industry=row["industry"],
            stage_transition=row["stage_transition"], promotion_rank=position, promotion_probability=probability,
            path_label=label, path_change_pct=delta, profit_rank=profit["executable_profit_research_rank"],
            profit_score=_number(profit["research_joint_proxy_score"])))
    return dict(signal_date=date, exec_date=dates[1], exit_date=dates[2], generated_at_utc=generated,
        generation_mode="REPLAY", rows=result, source=dict(
            repository="njedu2023-prog/DC20", inventory_source_commit=inventory["source_commit"],
            p0_compute_base_commit=receipt["inputs"]["git_head"],
            upstream_commits={key: receipt["inputs"][key]["resolved_commit"] for key in ("candidate", "market")},
            model_inventory_sha256=inventory_sha, model_hashes={key: value["sha256"] for key, value in inventory["active_source_models"].items()},
            artifact_hashes=artifacts, calendar_sha256=calendar_sha, p0_bundle_sha256=p0["bundle_sha256"],
            p1_snapshot_sha256=p1["snapshot_sha256"], members_sha256=members, feature_snapshot_sha256=p0["feature_snapshot_sha256"],
            p0_generated_at_utc=generated, original_generation_mode=mode,
            migration_bridge=True, inference_performed=False, independent_runtime=False,
            forward_ledger_eligible=False, p1_generation_timestamp_verified=False,
            upstream_market_files_replayed=False, old_statistics_imported=False,
            profit_score_semantics="uncalibrated_fill_times_conditional_proxy_not_win_probability",
            profit_model_status=p1["model"]["status"]))
