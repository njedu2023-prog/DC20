"""Synthetic schema fixtures; no inference, weights, or historical result read."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from forward import inputs, profit, release_evidence as reader
from forward.storage import encoded
from forward.trigger import bind_schedule

ROOT = Path(__file__).resolve().parents[2]
D, T, T1 = "20260907", "20260908", "20260909"
NOW = "2026-09-07T13:25:00Z"
P0TIME, P1TIME = "2026-09-07T13:19:00Z", "2026-09-07T13:20:00Z"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def bind(primary):
    day = primary["day"]
    primary["runtime_sha256"] = profit._digest({"schema_version": "dc20_forward_promotion_runtime_v1",
        "signal_date": day["signal_date"], "columns": primary["runtime_columns"], "rows": primary["runtime_rows"]})
    source = day["source"]
    source.update(runtime_sha256=primary["runtime_sha256"], feature_snapshot_sha256=primary["feature_snapshot_sha256"],
        members_sha256=profit._digest({"schema": "dc20_three_rank_member_set_v1", "signal_date": day["signal_date"],
                                      "members": sorted(row["ts_code"] for row in day["rows"])}))
    primary["receipt"] = dict(schema_version="dc20_forward_promotion_compute_receipt_v1", signal_date=day["signal_date"],
        selected_count=len(day["rows"]), promotion_pool_size=len(primary["runtime_rows"]), runtime_sha256=primary["runtime_sha256"],
        day_sha256=profit._digest(day), computation_performed=True, inference_performed=bool(primary["runtime_rows"]),
        training_performed=False, gate_status="READY", publication_performed=False)


def make_case(tmp_path, n=2, natural=False):
    dates, _, head, _ = inputs._calendar(ROOT)
    primary_dir, profit_dir = tmp_path / "p0", tmp_path / "p1"
    primary_dir.mkdir(); profit_dir.mkdir()
    source = dict(schema_version="dc20_forward_promotion_source_v1", repository="njedu2023-prog/DC20", source_commit=head,
        model_sha256="b7837d7001917a9c7bcc8814a09b45c6460f36a1adf6a7b5dcc024b4adc5f79c",
        computation_performed=True, inference_performed=bool(n), training_performed=False, legacy_ranking_read=False,
        production_enabled=False, legacy_action_or_statistics_read=False, secondary_models_loaded=False,
        forward_ledger_eligible=False, root_market_or_candidate_read=False, input_manifest_sha256="a" * 64,
        input_bundle_sha256="b" * 64, input_binding_basis="verified_manifest_and_immutable_upstream_bytes",
        input_collector_commit=head if natural else "e" * 40, input_collected_at_utc="2026-09-07T13:18:00Z")
    for key, repo, commit in [("candidate", inputs.PRED_REPO, "b" * 40), ("market", inputs.MARKET_REPO, "c" * 40)]:
        source[key] = dict(source_repository=repo, resolved_commit=commit, binding_basis="verified_input_manifest", input_manifest_sha256="a" * 64)
    rows = [dict(ts_code=f"60000{i}.SH", name=f"公司{i}", industry="测试行业", stage_transition="2→3",
                 promotion_rank=i + 1, promotion_probability=.9 - .05 * i, path_label=None, path_change_pct=None) for i in range(n)]
    day = dict(signal_date=D, exec_date=T, exit_date=T1, generated_at_utc=P0TIME, generation_mode="REPLAY", source=source, rows=rows)
    runtime = [dict(signal_date=D, ts_code=row["ts_code"], name=row["name"], industry=row["industry"],
        stage_transition="2→3", identity=f"{D}|{row['ts_code']}|2→3", top10_selected=1,
        promotion_rank=row["promotion_rank"], predicted_promotion_probability=row["promotion_probability"],
        generated_at_utc=P0TIME, feature_snapshot_sha256="f" * 64) for row in rows]
    columns = list(runtime[0]) if runtime else ["signal_date", "ts_code"]
    primary = dict(day=day, runtime_rows=runtime, runtime_columns=columns, feature_snapshot_sha256="f" * 64)
    bind(primary)
    evidence = dict(origin="HISTORICAL_PINNED_INPUT_REPLAY", manifest_sha256="a" * 64, signal_date=D, forward_ledger_eligible=False)
    env = {}
    if natural:
        schedule, created = "15 13 * * 1", "2026-09-07T13:16:00Z"
        event = tmp_path / "event.json"
        event.write_bytes(encoded({"schedule": schedule}))
        identity = dict(source_revision=head, run_id=101, run_attempt=1, event_name="schedule",
                        workflow_path=".github/workflows/accept_forward_inputs.yml", schedule=schedule, run_created_at_utc=created)
        accepted = dict(schema_version="dc20_forward_input_acceptance_v1", status="INPUTS_VALIDATED_NOT_PRODUCTION",
            read_only=True, production_activated=False, inference_performed=False, ledger_written=False, published_list=False,
            identity=identity, gate=bind_schedule("schedule", schedule, created, "2026-09-07T13:18:30Z", dates),
            started_at_utc="2026-09-07T13:17:00Z", finished_at_utc="2026-09-07T13:18:30Z", bundle_sha256="a" * 64,
            sources={inputs.PRED_REPO: "b" * 40, inputs.MARKET_REPO: "c" * 40})
        evidence.update(origin="NATURAL_SCHEDULE_STAGING", acceptance_receipt=accepted,
                        acceptance_receipt_sha256=sha(encoded(accepted)),
                        inference_gate=bind_schedule("schedule", schedule, created, "2026-09-07T13:21:00Z", dates))
        env = dict(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="schedule", GITHUB_REPOSITORY="njedu2023-prog/DC20",
            GITHUB_REF="refs/heads/main", GITHUB_WORKFLOW_REF="njedu2023-prog/DC20/.github/workflows/accept_forward_inputs.yml@refs/heads/main",
            GITHUB_SHA=head, GITHUB_WORKFLOW_SHA=head, GITHUB_RUN_ID="101", GITHUB_RUN_ATTEMPT="1", GITHUB_EVENT_PATH=str(event))
    p0receipt = dict(schema_version="dc20_forward_rehearsal_receipt_v1", kind="PROMOTION_INFERENCE", signal_date=D,
        source_revision=head, generation_mode="REPLAY", computation_completed=True, inference_performed=bool(n),
        production_enabled=False, forward_ledger_eligible=False, formal_trade_count=0, input_evidence=evidence, files={})
    case = dict(primary=primary, p0receipt=p0receipt, p0dir=primary_dir, p1dir=profit_dir, env=env, dates=dates)
    write_p0(case)
    p1source = dict(source_commit=head, promotion_source_sha256=profit._digest(day), runtime_sha256=primary["runtime_sha256"],
        feature_snapshot_sha256=primary["feature_snapshot_sha256"], model_sha256=profit.MODEL_SHA256,
        feature_columns_sha256=profit.FEATURES_SHA256, feature_count=156, model_status="INTERNAL_CHALLENGER_NOT_READY",
        historical_feature_use="strictly_before_D_lagged_features_not_performance_statistics", research_only=True,
        calibrated_probability_output=False, computation_completed=True, inference_performed=bool(n), model_loaded=bool(n),
        dependencies={profit.MODEL_PATH: profit.MODEL_SHA256, profit.CALENDAR_PATH: profit.CALENDAR_SHA256, profit.HISTORY_PATH: profit.HISTORY_SHA256})
    proxy = [dict(ts_code=row["ts_code"], fill_proxy_score=.8, conditional_profit_score=.2 + i * .05) for i, row in enumerate(rows)]
    prows = [dict(ts_code=row["ts_code"], name=row["name"], promotion_rank=row["promotion_rank"], profit_rank=n-i,
                  profit_score=proxy[i]["fill_proxy_score"] * proxy[i]["conditional_profit_score"]) for i, row in enumerate(rows)]
    if n:
        p1source.update(model_source_hashes={"model_pickle_sha256": profit.MODEL_SHA256,
            "strict_sse_calendar_sha256": profit.CALENDAR_SHA256, "full_history_ledger_sha256": profit.HISTORY_SHA256},
            mixed_feature_snapshot_sha256="d" * 64, computation_source_sha256="e" * 64,
            lagged_prior_max_history_exit_date="20260818", row_proxy_scores=proxy)
    else:
        p1source["empty_event_reason"] = "P0_REAL_TOPN_EMPTY"
    result = dict(schema_version="dc20_forward_profit_inference_v1", status="REPLAY_RESEARCH_ONLY", generation_mode="REPLAY",
        research_only=True, signal_date=D, exec_date=T, exit_date=T1, generated_at_utc=P1TIME, source=p1source, rows=prows,
        rehearsal_promotion_receipt_sha256=case["p0sha"])
    p1receipt = deepcopy(p0receipt)
    p1receipt["kind"] = "PROFIT_INFERENCE"
    if natural:
        p1receipt["input_evidence"]["inference_gate"] = bind_schedule("schedule", "15 13 * * 1", "2026-09-07T13:16:00Z", "2026-09-07T13:22:00Z", dates)
    case.update(profit=result, p1receipt=p1receipt)
    write_p1(case)
    return case


def write_p0(c):
    for name, value in (("primary.json", c["primary"]), ("promotion.json", c["primary"]["day"])):
        raw = encoded(value); (c["p0dir"] / name).write_bytes(raw); c["p0receipt"]["files"][name] = sha(raw)
    raw = encoded(c["p0receipt"]); (c["p0dir"] / "receipt.json").write_bytes(raw); c["p0sha"] = sha(raw)


def write_p1(c):
    raw = encoded(c["profit"]); (c["p1dir"] / "profit.json").write_bytes(raw)
    c["p1receipt"]["files"] = {"profit.json": sha(raw)}
    raw = encoded(c["p1receipt"]); (c["p1dir"] / "receipt.json").write_bytes(raw); c["p1sha"] = sha(raw)


def read_p0(c, **kwargs):
    return reader.read_promotion(ROOT, c["p0dir"], c["p0sha"], env=c["env"], now=kwargs.get("now", NOW))


def read_p1(c, info=None, **kwargs):
    return reader.read_profit(ROOT, read_p0(c) if info is None else info, c["p1dir"], c["p1sha"], env=c["env"], now=kwargs.get("now", NOW))


@pytest.mark.parametrize("n", [0, 1, 2, 6, 10])
def test_valid_real_size_contracts_preserve_rows_and_never_admit(tmp_path, n):
    c = make_case(tmp_path, n=n)
    p0, p1 = read_p0(c), read_p1(c)
    assert p0["gate"] is p1["gate"] is None
    assert p0["primary"] == c["primary"] and p1["profit"] == c["profit"]
    assert len(p1["profit"]["rows"]) == n
    assert p0["receipt"]["forward_ledger_eligible"] is p1["receipt"]["forward_ledger_eligible"] is False
    assert json.loads(json.dumps(p0))["primary"] == c["primary"]


def test_natural_same_run_original_slot_is_still_replay(tmp_path):
    c = make_case(tmp_path, natural=True)
    p0, p1 = read_p0(c), read_p1(c)
    assert p0["gate"]["status"] == p1["gate"]["status"] == "ELIGIBLE"
    assert p1["profit"]["generation_mode"] == "REPLAY"
    c["env"]["GITHUB_RUN_ID"] = "102"
    with pytest.raises(ValueError): read_p0(c)
    c["env"]["GITHUB_RUN_ID"] = "101"
    with pytest.raises(ValueError): read_p0(c, now="2026-09-08T01:20:00Z")


def test_natural_environment_cannot_accept_historical_downgrade(tmp_path):
    c = make_case(tmp_path)
    c["env"]["GITHUB_EVENT_NAME"] = "schedule"
    with pytest.raises(ValueError): read_p0(c)


@pytest.mark.parametrize("field", ["source_commit", "model", "runtime", "member", "rank", "name", "date", "path", "gate", "natural", "trade"])
def test_rehashed_primary_semantic_mutations_rejected(tmp_path, field):
    c = make_case(tmp_path)
    b, r = c["primary"], c["p0receipt"]
    if field == "source_commit": b["day"]["source"]["source_commit"] = "d" * 40
    elif field == "model": b["day"]["source"]["model_sha256"] = "d" * 64
    elif field == "runtime": b["runtime_rows"][0]["generated_at_utc"] = P1TIME
    elif field == "member": b["day"]["rows"][0]["ts_code"] = "600999.SH"
    elif field == "rank": b["day"]["rows"].reverse()
    elif field == "name": b["day"]["rows"][0]["name"] = "changed"
    elif field == "date": b["day"]["exit_date"] = "20260910"
    elif field == "path": b["day"]["rows"][0]["path_change_pct"] = True
    elif field == "gate": r["forward_ledger_eligible"] = True
    elif field == "natural": b["day"]["generation_mode"] = "NATURAL"
    elif field == "trade": r["formal_trade_count"] = 1
    bind(b); write_p0(c)
    with pytest.raises(ValueError): read_p0(c)


@pytest.mark.parametrize("field", ["p0_receipt", "day_hash", "runtime_hash", "feature_hash", "model", "date", "name", "promotion_rank", "profit_rank", "order", "score", "proxy", "future_prior", "future_time", "loaded", "extra_orders", "natural"])
def test_rehashed_profit_cannot_change_immutable_contract(tmp_path, field):
    c = make_case(tmp_path); p = c["profit"]; source = p["source"]
    if field == "p0_receipt": p["rehearsal_promotion_receipt_sha256"] = "e" * 64
    elif field == "day_hash": source["promotion_source_sha256"] = "e" * 64
    elif field == "runtime_hash": source["runtime_sha256"] = "e" * 64
    elif field == "feature_hash": source["feature_snapshot_sha256"] = "e" * 64
    elif field == "model": source["model_sha256"] = "e" * 64
    elif field == "date": p["exec_date"] = D
    elif field == "name": p["rows"][0]["name"] = "changed"
    elif field == "promotion_rank": p["rows"][0]["promotion_rank"] = 2
    elif field == "profit_rank": p["rows"][0]["profit_rank"] = 1
    elif field == "order": p["rows"].reverse()
    elif field == "score": p["rows"][0]["profit_score"] = .99
    elif field == "proxy": source["row_proxy_scores"][0]["fill_proxy_score"] = .9
    elif field == "future_prior": source["lagged_prior_max_history_exit_date"] = D
    elif field == "future_time": p["generated_at_utc"] = "2026-09-07T14:00:00Z"
    elif field == "loaded": source["model_loaded"] = False
    elif field == "extra_orders": p["orders"] = []
    elif field == "natural": p["generation_mode"] = "NATURAL"
    write_p1(c)
    with pytest.raises(ValueError): read_p1(c)


def test_tied_scores_preserve_existing_conditional_fill_then_code_rule(tmp_path):
    c = make_case(tmp_path)
    for row, proxy in zip(c["profit"]["rows"], c["profit"]["source"]["row_proxy_scores"]):
        row["profit_score"] = .2
        proxy["fill_proxy_score"] = .5
        proxy["conditional_profit_score"] = .4
    c["profit"]["rows"][0]["profit_rank"], c["profit"]["rows"][1]["profit_rank"] = 1, 2
    write_p1(c); assert read_p1(c)["profit"]["rows"] == c["profit"]["rows"]
    c["profit"]["rows"][0]["profit_rank"], c["profit"]["rows"][1]["profit_rank"] = 2, 1
    write_p1(c)
    with pytest.raises(ValueError): read_p1(c)


def test_external_digest_and_canonical_receipt_required(tmp_path):
    c = make_case(tmp_path)
    for expected in [None, "", "0" * 64]:
        with pytest.raises(ValueError): reader.read_promotion(ROOT, c["p0dir"], expected, env={}, now=NOW)
    raw = json.dumps(c["p0receipt"], indent=2).encode(); (c["p0dir"] / "receipt.json").write_bytes(raw); c["p0sha"] = sha(raw)
    with pytest.raises(ValueError): read_p0(c)


def test_pinned_input_is_read_once_no_network_or_old_data_content(tmp_path, monkeypatch):
    c = make_case(tmp_path)
    originals = reader._regular_bytes, io.open
    seen = []
    def one(fd, relative, limit):
        seen.append(relative)
        return originals[0](fd, relative, limit)
    def limited(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file)).resolve()
            if path.is_relative_to(ROOT):
                assert path.relative_to(ROOT).as_posix() in {inputs.CALENDAR, "forward/config.json"}
                assert not any(flag in mode for flag in "wax+")
        return originals[1](file, mode, *args, **kwargs)
    monkeypatch.setattr(reader, "_regular_bytes", one)
    monkeypatch.setattr(io, "open", limited)
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network"))
    monkeypatch.setattr(profit, "_dependencies", lambda *a: pytest.fail("ML import"))
    info = read_p0(c); read_p1(c, info)
    assert seen == ["receipt.json", "primary.json", "promotion.json", "receipt.json", "profit.json"]


def test_primary_info_public_mutation_or_plain_dict_cannot_replace_verified_anchor(tmp_path):
    c = make_case(tmp_path); info = read_p0(c)
    with pytest.raises(ValueError): read_p1(c, dict(info))
    info["primary"]["runtime_rows"][0]["name"] = "tampered"
    with pytest.raises(ValueError): read_p1(c, info)


def test_p1_failure_keeps_all_primary_evidence_bytes(tmp_path):
    c = make_case(tmp_path)
    before = {path.name: path.read_bytes() for path in c["p0dir"].iterdir()}
    (c["p1dir"] / "profit.json").write_bytes(b"broken")
    with pytest.raises(ValueError): read_p1(c)
    assert {path.name: path.read_bytes() for path in c["p0dir"].iterdir()} == before


def test_p0_reader_cold_process_does_not_import_optional_profit_module(tmp_path):
    c = make_case(tmp_path)
    program = (
        "import sys; sys.modules['forward.profit']=None; "
        "from forward.release_evidence import read_promotion; "
        "v=read_promotion(sys.argv[1],sys.argv[2],sys.argv[3],env={},now=sys.argv[4]); "
        "assert len(v['primary']['day']['rows'])==2; "
        "assert sys.modules['forward.profit'] is None; print('P0_INDEPENDENT_OK')"
    )
    result = subprocess.run([sys.executable, "-c", program, str(ROOT), str(c["p0dir"]), c["p0sha"], NOW],
        cwd=ROOT, capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.stdout.strip() == "P0_INDEPENDENT_OK"


def test_profit_validation_wrapper_preserves_original_exception_type(tmp_path):
    c = make_case(tmp_path)
    c["primary"]["day"]["rows"][0]["promotion_rank"] = 999
    with pytest.raises(profit.ProfitInferenceError, match="contiguous"):
        profit._validate_bundle(c["primary"])
