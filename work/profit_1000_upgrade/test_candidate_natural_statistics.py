"""Synthetic only: fake private-proof boundary, real unchanged native outcomes.

No future market observations, API requests, fitting, publication or source
authority. Fixture host-clock fields are synthetic JSON, not live evidence.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket

import pytest

from work.profit_1000_upgrade import candidate_natural_statistics as m
from work.profit_1000_upgrade import test_candidate_natural_outcomes as old


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"


def seal(value, field):
    value.pop(field, None)
    value[field] = m.natural.scorer.canonical_sha(value)
    return encode(value)


class SyntheticPublicationProof:
    """An explicit monkeypatched TEST boundary, never the issuer's actual type."""
    def __init__(self, day, snapshot_sha):
        self.signal_date = day
        self.snapshot_file_sha256 = snapshot_sha
        self.publication_observation_sha256 = "1" * 64
        self.evidence_manifest_sha256 = "2" * 64
        self.observer_run_id = 123456
        self.evidence_commit = "3" * 40
        self.calls = 0
        self.after = None

    def assert_unchanged(self):
        self.calls += 1
        if self.after is not None:
            self.after(self.calls)


def fixture_case(tmp_path, monkeypatch, *, kind="normal", size=2):
    case = old.make_case(tmp_path, monkeypatch, kind=kind, size=size)
    frozen = json.loads(case["snapshot"].read_bytes())
    frozen["clock_mode"] = "HOST_SYSTEM_UTC"  # Synthetic envelope only.
    snapshot_raw = seal(frozen, "snapshot_sha256")
    case["snapshot"].write_bytes(snapshot_raw)
    case["snapshot_sha"] = m.sha(snapshot_raw)
    case["frozen"] = frozen
    receipt = old.run(case)
    ledger = json.loads(Path(receipt["ledger_path"]).read_bytes())
    for version in ledger["versions"]:
        version["clock_mode"] = "HOST_SYSTEM_UTC"  # Not an actual observed clock.
        seal(version, "report_sha256")
    ledger_raw = seal(ledger, "ledger_sha256")
    proof = SyntheticPublicationProof(case["day"], case["snapshot_sha"])
    monkeypatch.setattr(m, "_publication_type", lambda: SyntheticPublicationProof)
    item = {"signal_date": case["day"], "snapshot_raw": snapshot_raw,
        "expected_snapshot_sha256": case["snapshot_sha"], "ledger_raw": ledger_raw,
        "expected_ledger_sha256": m.sha(ledger_raw), "ledger_as_of_date": case["asof"],
        "publication_proof": proof, "source_collection_receipt_sha256": "4" * 64}
    calendar = Path(case["bundle"]["calendar"]["origin_path"]).read_bytes()
    return {"case": case, "item": item, "ledger": ledger, "calendar": calendar, "proof": proof}


@pytest.fixture
def case(tmp_path, monkeypatch):
    return fixture_case(tmp_path, monkeypatch)


def run(case, **options):
    return m.summarize_natural_statistics(options.pop("day_inputs", [case["item"]]),
        as_of_date=options.pop("as_of_date", "20260917"), calendar_raw=case["calendar"],
        expected_calendar_sha256=m.sha(case["calendar"]),
        clock=options.pop("clock", lambda: datetime(2026, 9, 17, 8, tzinfo=timezone.utc)), **options)


def reseal(case):
    for version in case["ledger"]["versions"]:
        seal(version, "report_sha256")
    raw = seal(case["ledger"], "ledger_sha256")
    case["item"]["ledger_raw"] = raw
    case["item"]["expected_ledger_sha256"] = m.sha(raw)


def test_actual_native_fixture_loss_all_four_groups_preserved_without_fit_or_network(case, monkeypatch):
    before = (case["item"]["snapshot_raw"], case["item"]["ledger_raw"])
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NETWORK"))
    monkeypatch.setattr(m.labels, "build_labels", lambda *a, **k: pytest.fail("NATIVE_REPLAY"))
    monkeypatch.setattr(m.natural.scorer, "predict_forward", lambda *a, **k: pytest.fail("RESCORE"))
    result = run(case)
    assert result["status"] == "SYNTHETIC_CLOCK_ONLY"
    assert set(result["groups"]) == set(m.GROUPS)
    assert result["common_complete_signal_dates"] == [case["item"]["signal_date"]]
    for group in result["groups"].values():
        assert group["filled_settled_slots"] == group["negative_filled_slots"] == 1
        assert group["filled_proxy_win_rate"] == 0
        assert group["mean_net_slot_return"] == pytest.approx(-.0245)
        assert group["synthetic_cumulative_net_return"] == pytest.approx(-.0245)
        assert group["daily_sequence"][0]["slot_net_return"] == pytest.approx(-.0245)
    assert before == (case["item"]["snapshot_raw"], case["item"]["ledger_raw"])
    assert case["proof"].calls == 2
    for name, flag in m.FLAGS.items(): assert result[name] == flag
    assert result["whole_natural_history_completeness_verified"] is False
    assert result["input_bindings"][0]["source_collection_receipt_sha256"] == "4" * 64


@pytest.mark.parametrize("kind,expected", [
    ("capacity", "NO_FILL_CAPACITY"), ("zero_auction", "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME"),
    ("suspended", "NO_FILL_SUSPENDED"), ("opening_limit", "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"),
    ("missing_auction", "PENDING_T_MISSING_CANONICAL_AUCTION"), ("missing_minutes", "PENDING_EXIT_MISSING_MINUTES"),
    ("price_conflict", "PENDING_ENTRY_PRICE_DAILY_OPEN_CONFLICT"), ("wrong_preclose", "PENDING_ENTRY_INVALID_PRE_CLOSE")])
def test_true_native_no_fill_and_pending_are_not_interchanged(tmp_path, monkeypatch, kind, expected):
    case = fixture_case(tmp_path, monkeypatch, kind=kind)
    output = run(case)
    rows = [group for group in output["groups"].values() if expected in group["status_counts"]]
    assert rows
    for group in rows:
        if expected.startswith("NO_FILL"):
            assert group["known_no_fill_slots"] == 1
            assert group["mean_net_slot_return"] == 0
            assert group["mean_net_filled_proxy_return"] is group["filled_proxy_win_rate"] is None
        else:
            assert group["pending_slots"] == 1
            assert group["mean_net_slot_return"] is group["synthetic_cumulative_net_return"] is None
            assert group["daily_sequence"][0]["slot_net_return"] is None
            assert output["common_complete_signal_dates"] == []


@pytest.mark.parametrize("size", [0, 1, 2, 10])
def test_zero_one_and_full_cohort_keep_exact_four_slots(tmp_path, monkeypatch, size):
    case = fixture_case(tmp_path, monkeypatch, size=size)
    result = run(case)
    for name, group in result["groups"].items():
        missing = int(name[-1]) > size
        assert group["missing_candidate_slots"] == int(missing)
        assert len(group["daily_sequence"]) == 1
        if missing:
            assert group["daily_sequence"][0]["slot_net_return"] is None
            assert group["synthetic_cumulative_net_return"] is None
    assert bool(result["common_complete_signal_dates"]) is (size >= 2)


def test_missing_ledger_never_no_fill_or_zero(case):
    item = case["item"]
    for key in ("ledger_raw", "expected_ledger_sha256", "ledger_as_of_date", "source_collection_receipt_sha256"):
        item[key] = None
    result = run(case)
    for group in result["groups"].values():
        assert group["missing_ledger_slots"] == 1
        assert group["terminal_slots"] == 0
        assert group["mean_net_slot_return"] is group["filled_proxy_win_rate"] is None


def test_optional_source_receipt_does_not_claim_source_authority(case):
    case["item"]["source_collection_receipt_sha256"] = None
    result = run(case)
    assert result["input_bindings"][0]["source_collection_receipt_sha256"] is None
    assert result["outcome_sources_independently_verified"] is result["source_authority_issued"] is False


def test_empty_day_universe_never_zero_return(case, monkeypatch):
    monkeypatch.setattr(m, "_publication_type", lambda: pytest.fail("NO_PROOF_NEEDED_FOR_EMPTY"))
    result = run(case, day_inputs=[])
    assert result["signal_dates"] == []
    for group in result["groups"].values():
        assert group["synthetic_cumulative_net_return"] is group["mean_net_slot_return"] is None


@pytest.mark.parametrize("value", [None, True, {}, {"verified": True}, object()])
def test_plain_boolean_receipt_dict_or_unissued_proof_cannot_admit(case, value):
    case["item"]["publication_proof"] = value
    with pytest.raises(ValueError, match="PRIVATE_ISSUED"):
        run(case)


def test_subclass_of_exact_proof_is_rejected(case):
    class Forged(SyntheticPublicationProof): pass
    case["item"]["publication_proof"] = Forged(case["item"]["signal_date"], case["item"]["expected_snapshot_sha256"])
    with pytest.raises(ValueError, match="PRIVATE_ISSUED"):
        run(case)


@pytest.mark.parametrize("field,value", [("signal_date", "20260915"), ("snapshot_file_sha256", "0" * 64),
    ("publication_observation_sha256", "bad"), ("evidence_manifest_sha256", None),
    ("observer_run_id", "123"), ("evidence_commit", "main")])
def test_proof_identity_and_external_bindings_fail_closed(case, field, value):
    setattr(case["proof"], field, value)
    with pytest.raises(ValueError): run(case)


class Trap:
    def __bytes__(self): pytest.fail("FUTURE_BYTES")
    def __iter__(self): pytest.fail("FUTURE_ITER")
    def __len__(self): pytest.fail("FUTURE_LEN")
    def __eq__(self, other): pytest.fail("FUTURE_VALUE_COMPARE")


@pytest.mark.parametrize("field,value", [("signal_date", "20260918"), ("signal_date", "20260814"),
    ("ledger_as_of_date", "20260918"), ("ledger_as_of_date", "20260814")])
def test_all_external_dates_precede_any_ledger_access(case, field, value):
    bad = dict(case["item"])
    bad[field] = value
    bad["ledger_raw"] = Trap()
    first = dict(case["item"]); first["ledger_raw"] = Trap()
    with pytest.raises(ValueError, match="FUTURE_OR"):
        run(case, day_inputs=[first, bad])


@pytest.mark.parametrize("kind", ["duplicate_D", "missing_metadata", "extra_field", "snapshot_sha", "ledger_sha", "invalid_size"])
def test_external_scope_and_raw_bindings(case, kind):
    items = [case["item"]]
    if kind == "duplicate_D": items.append(dict(items[0]))
    elif kind == "missing_metadata": del items[0]["ledger_as_of_date"]
    elif kind == "extra_field": items[0]["legacy_ledger"] = True
    elif kind == "snapshot_sha": items[0]["snapshot_raw"] += b" "
    elif kind == "ledger_sha": items[0]["ledger_raw"] += b" "
    elif kind == "invalid_size": items[0]["ledger_raw"] = bytearray(items[0]["ledger_raw"])
    with pytest.raises(ValueError): run(case, day_inputs=items)


@pytest.mark.parametrize("field,value", [("as_of_date", "20260918"), ("signal_date", "20260915")])
def test_internal_version_dates_precede_economic_contract_hash(case, monkeypatch, field, value):
    version = case["ledger"]["versions"][0]
    version[field] = value
    reseal(case)
    original = m.outcomes._seal
    def guard(value, field):
        if field in ("ledger_sha256", "report_sha256"):
            pytest.fail("OUTCOME_SEAL_BEFORE_VERSION_IDENTITY")
        return original(value, field)
    monkeypatch.setattr(m.outcomes, "_seal", guard)
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("kind", ["fee", "native_fee", "slot_loss_omitted", "zero_pending", "source", "manifest", "legacy", "writer", "flag", "clock"])
def test_resealed_false_economic_or_source_claims_are_rejected(case, kind):
    v = case["ledger"]["versions"][0]
    code = next(iter(v["native_singleton_reports"]))
    row = v["native_singleton_reports"][code]["rows"][0]
    if kind == "fee": v["round_trip_cost_rate"] = .003
    elif kind == "native_fee": row["round_trip_cost_rate"] = .003
    elif kind == "slot_loss_omitted": v["candidate_slots"][0]["slot_net_return"] = 0
    elif kind == "zero_pending": row["label_status"] = "PENDING_EXIT_MISSING_MINUTES"
    elif kind == "source": v["native_singleton_reports"][code]["source_files"][0]["sha256"] = "0" * 64
    elif kind == "manifest": v["native_singleton_manifests"][code]["rows"][0]["promotion_rank"] = 9
    elif kind == "legacy": case["ledger"]["schema_version"] = "old_shadow_ledger"
    elif kind == "writer": v["writer_sha256"] = "0" * 64
    elif kind == "flag": v["actual_execution_claimed"] = True
    elif kind == "clock": v["clock_mode"] = "INJECTED_TEST_CLOCK_RESEARCH_ONLY"
    reseal(case)
    with pytest.raises(ValueError): run(case)


def append_version(case):
    later = deepcopy(case["ledger"]["versions"][-1])
    later["as_of_date"] = "20260917"
    for report in later["native_singleton_reports"].values(): report["as_of_date"] = "20260917"
    case["ledger"]["versions"].append(later)
    case["item"]["ledger_as_of_date"] = "20260917"
    reseal(case)


def test_append_terminal_history_is_counted_once(case):
    append_version(case)
    result = run(case)
    assert result["input_bindings"][0]["ledger_version_count"] == 2
    assert result["groups"]["candidate_top1"]["filled_settled_slots"] == 1


@pytest.mark.parametrize("kind", ["reordered", "duplicate", "terminal_row", "terminal_source"])
def test_changed_append_history_rejected(case, kind):
    append_version(case)
    versions = case["ledger"]["versions"]
    if kind == "reordered": versions.reverse()
    elif kind == "duplicate": versions[1] = deepcopy(versions[0])
    else:
        native = next(iter(versions[1]["native_singleton_reports"].values()))
        if kind == "terminal_row": native["rows"][0]["reason"] = "changed"
        else: native["source_files"] = native["source_files"][:-1]
    reseal(case)
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("field", ["snapshot_raw", "ledger_raw", "expected_snapshot_sha256", "expected_ledger_sha256",
    "ledger_as_of_date", "source_collection_receipt_sha256", "signal_date"])
def test_final_long_proof_callback_cannot_mutate_caller_inputs(case, field):
    item = case["item"]
    def late(calls):
        if calls == 2:
            item[field] = item[field] + (b" " if field.endswith("_raw") else "x")
    case["proof"].after = late
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("kind", ["replace_item", "replace_proof", "proof_binding", "raw"])
def test_mutation_during_last_full_code_guard_is_not_accepted(case, monkeypatch, kind):
    original = m._guard
    calls, items = [], [case["item"]]
    def guard():
        result = original()
        calls.append(1)
        if len(calls) == 2:
            if kind == "replace_item": items[0] = dict(items[0])
            elif kind == "replace_proof": items[0]["publication_proof"] = SyntheticPublicationProof(
                items[0]["signal_date"], items[0]["expected_snapshot_sha256"])
            elif kind == "proof_binding": case["proof"].evidence_commit = "9" * 40
            else: items[0]["ledger_raw"] += b" "
        return result
    monkeypatch.setattr(m, "_guard", guard)
    with pytest.raises(ValueError, match="CHANGED"):
        run(case, day_inputs=items)


def test_proof_callback_cannot_move_asof_forward_before_ledger_access(case):
    def late(calls):
        if calls == 1:
            case["item"]["ledger_as_of_date"] = "20260918"
            case["item"]["ledger_raw"] = Trap()
    case["proof"].after = late
    with pytest.raises(ValueError, match="FUTURE_OR_INVALID_LEDGER_ASOF"):
        run(case)


@pytest.mark.parametrize("kind", ["features", "feature_time", "promotion_rank_float", "stage", "numeric_string", "bool_fill"])
def test_native_row_shape_is_exact_and_not_caller_coerced(case, kind):
    row = next(iter(case["ledger"]["versions"][0]["native_singleton_reports"].values()))["rows"][0]
    if kind == "features": row["features"]["board_stage"] = 3.0
    elif kind == "feature_time": row["feature_available_at"] = "2026-09-15T01:25:00Z"
    elif kind == "promotion_rank_float": row["promotion_rank"] = float(row["promotion_rank"])
    elif kind == "stage": row["stage_transition"] = "3→4" if row["stage_transition"] == "2→3" else "2→3"
    elif kind == "numeric_string": row["slot_net_return"] = str(row["slot_net_return"])
    else: row["proxy_fill"] = True
    reseal(case)
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("kind", ["empty_versions", "missing_source", "duplicate_source", "future_source", "wrong_code",
    "origin_path", "origin_hash", "maturity_future", "wrong_snapshot", "changed_calendar"])
def test_native_receipts_dates_and_source_inventory_are_bound(case, kind):
    version = case["ledger"]["versions"][0]
    if kind == "empty_versions": case["ledger"]["versions"] = []
    elif kind == "missing_source": version["input_bindings"].pop()
    elif kind == "duplicate_source": version["input_bindings"].append(deepcopy(version["input_bindings"][0]))
    elif kind == "future_source": version["input_bindings"][0]["path"] = "data/market/raw/2026/20260918/daily.csv"
    elif kind == "wrong_code": version["input_bindings"][0]["ts_code"] = "600999.SH"
    elif kind == "origin_path": version["origin_bindings"][0]["origin_path"] = "../unbound"
    elif kind == "origin_hash": version["origin_bindings"][0]["sha256"] = "0" * 64
    elif kind == "maturity_future": next(iter(version["native_singleton_reports"].values()))["rows"][0]["label_available_date"] = "20260918"
    elif kind == "wrong_snapshot": version["snapshot_file_sha256"] = "0" * 64
    else: version["calendar_sha256"] = "0" * 64
    reseal(case)
    with pytest.raises(ValueError): run(case)


def seq(day, status, net):
    return {"signal_date": day, "ts_code": "600001.SH", "ledger_as_of_date": "20260917",
        "status": status, "proxy_fill": 1 if status == m.labels.SETTLED else None,
        "slot_net_return": net, "label_available_date": None}


def test_metric_denominators_compounding_loss_zero_pending_and_no_fill():
    sequence = [seq("20260914", m.labels.SETTLED, .10), seq("20260915", m.labels.SETTLED, -.20),
        seq("20260916", m.labels.SETTLED, 0.0), seq("20260917", "NO_FILL_CAPACITY", 0),
        seq("20260918", "PENDING_EXIT_MISSING_MINUTES", None),
        seq("20260921", "MISSING_OUTCOME_LEDGER", None), seq("20260922", "MISSING_CANDIDATE", None)]
    out = m._metrics(sequence)
    assert out["filled_proxy_win_rate"] == pytest.approx(1/3)
    assert out["mean_net_slot_return"] == pytest.approx(-.1/4)
    assert out["mean_net_filled_proxy_return"] == pytest.approx(-.1/3)
    assert out["synthetic_cumulative_net_return"] == pytest.approx(1.1 * .8 - 1)
    assert len(out["daily_sequence"]) == 7 and len(out["complete_daily_sequence"]) == 4
    assert out["negative_filled_slots"] == out["zero_filled_slots"] == out["positive_filled_slots"] == 1
    assert out["pending_slots"] == out["missing_ledger_slots"] == out["missing_candidate_slots"] == 1


def test_extreme_fee_adjusted_loss_is_not_clipped_into_fake_nav():
    out = m._metrics([seq("20260914", m.labels.SETTLED, -1.0045)])
    assert out["synthetic_cumulative_net_return"] == -1.0045


def test_publication_placeholder_is_fail_closed(monkeypatch):
    monkeypatch.setattr(m, "PUBLICATION_SHA", "0" * 64)
    with pytest.raises(ValueError, match="ISSUER_NOT_REGISTERED"):
        m._publication_type()


def test_real_fixed_issuer_class_cannot_be_publicly_constructed():
    cls = m._publication_type()
    assert cls.__name__ == "VerifiedResearchPublication"
    with pytest.raises(ValueError, match="PRIVATE_PUBLICATION_ISSUER"):
        cls()


def test_asof_not_complete_rejected_before_proof_or_source_value(case):
    with pytest.raises(ValueError, match="ASOF_SESSION_NOT_COMPLETED"):
        run(case, clock=lambda: datetime(2026, 9, 17, 6, tzinfo=timezone.utc))


def test_statistics_module_has_no_write_network_training_or_ledger_discovery_api():
    import ast
    tree = ast.parse(Path(m.__file__).read_text())
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls.intersection({"write_bytes", "write_text", "mkdir", "unlink", "fit", "predict_forward", "rglob", "glob", "urlopen"})
