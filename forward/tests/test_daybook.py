"""Synthetic staging-only checks: never touch production daybooks or outputs."""
import copy
from pathlib import Path

import pytest

from forward import storage
from forward.daybook import (Daybook, attach_profit_day, freeze_promotion_day,
                             new_epoch, record_day_verification)
from forward.settlement import verify_day
from .test_ledger import DATES, corporate_evidence, make_day


def epoch():
    return new_epoch("synthetic-v2", "2026-09-07T07:00:00Z")


def promotion_input(n=3, signal="20260907", generated="2026-09-07T13:00:00Z"):
    day = make_day(n, signal, generated)
    for row in day["rows"]:
        row.pop("profit_rank")
        row.pop("profit_score")
    return day


def promotion(n=3):
    return freeze_promotion_day(epoch(), promotion_input(n), open_dates=DATES,
                                now_utc="2026-09-07T13:00:01Z")


def profit_input(frozen):
    return {"signal_date": frozen["signal_date"], "exec_date": frozen["exec_date"],
            "exit_date": frozen["exit_date"], "promotion_freeze_sha256": frozen["freeze_sha256"],
            "generated_at_utc": "2026-09-07T13:02:00Z", "generation_mode": "NATURAL",
            "source": {"profit_model_sha256": "e" * 64},
            "rows": [{"ts_code": row["ts_code"], "profit_rank": len(frozen["rows"]) + 1 - row["promotion_rank"],
                      "profit_score": row["promotion_rank"] / 10} for row in frozen["rows"]]}


def test_promotion_freezes_without_any_profit_fields_and_records_top3():
    day = promotion_input(4)
    before = copy.deepcopy(day)
    frozen = freeze_promotion_day(epoch(), day, open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    assert day == before
    assert [slot["ts_code"] for slot in frozen["promotion_top3"]] == [row["ts_code"] for row in day["rows"][:3]]
    assert [slot["slot"] for slot in frozen["promotion_top3"]] == ["Top1", "Top2", "Top3"]
    assert all("profit_rank" not in row for row in frozen["rows"])
    assert frozen["promotion_top3"][0]["exec_date"] == "20260908"


@pytest.mark.parametrize("n", [0, 1, 2, 3, 6, 10])
def test_real_n_slots_never_padded(n):
    frozen = promotion(n)
    assert len(frozen["rows"]) == n
    assert len(frozen["promotion_top3"]) == min(n, 3)
    result = attach_profit_day(epoch(), frozen, profit_input(frozen), now_utc="2026-09-07T13:02:01Z")
    assert len(result["rows"]) == n
    assert len(result["profit_top2"]) == min(n, 2)
    assert result["status"] == "READY"


def test_profit_attachment_preserves_exact_promotion_and_freezes_its_top2():
    frozen = promotion()
    before = copy.deepcopy(frozen)
    result = attach_profit_day(epoch(), frozen, profit_input(frozen), now_utc="2026-09-07T13:02:01Z")
    assert frozen == before
    assert result["promotion_freeze_sha256"] == frozen["freeze_sha256"]
    assert [row["ts_code"] for row in result["rows"]] == ["600003.SH", "600002.SH", "600001.SH"]
    assert [slot["name"] for slot in result["profit_top2"]] == ["公司3", "公司2"]
    assert [row["promotion_rank"] for row in result["rows"]] == [3, 2, 1]


def test_exact_promotion_and_profit_duplicates_are_noop_even_after_cutoff():
    original = promotion()
    duplicate = freeze_promotion_day(epoch(), promotion_input(), open_dates=DATES,
                                      now_utc="2026-09-10T13:00:00Z", existing=original)
    assert duplicate == original and duplicate is not original
    supplied = profit_input(original)
    attached = attach_profit_day(epoch(), original, supplied, now_utc="2026-09-07T13:02:01Z")
    assert attach_profit_day(epoch(), original, supplied, now_utc="2026-09-10T13:00:00Z", existing=attached) == attached


@pytest.mark.parametrize("mutation", ["rank", "score", "name", "member", "sha", "date", "partial", "boolrank"])
def test_profit_bad_attachment_cannot_change_promotion(mutation):
    frozen = promotion()
    before = copy.deepcopy(frozen)
    supplied = profit_input(frozen)
    if mutation == "rank":
        supplied["rows"][0]["profit_rank"] = 2
    elif mutation == "score":
        supplied["rows"][0]["profit_score"] = float("nan")
    elif mutation == "name":
        supplied["rows"][0]["name"] = "other"
    elif mutation == "member":
        supplied["rows"][0]["ts_code"] = "600999.SH"
    elif mutation == "sha":
        supplied["promotion_freeze_sha256"] = "0" * 64
    elif mutation == "date":
        supplied["exec_date"] = "20260909"
    elif mutation == "partial":
        supplied["rows"].pop()
    else:
        supplied["rows"][0]["profit_rank"] = True
    with pytest.raises(ValueError):
        attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")
    assert frozen == before


def test_changed_frozen_payloads_conflict_instead_of_overwrite():
    frozen = promotion()
    changed = promotion_input()
    changed["rows"][0]["path_label"] = "changed"
    with pytest.raises(ValueError, match="conflict"):
        freeze_promotion_day(epoch(), changed, open_dates=DATES,
                             now_utc="2026-09-07T13:01:00Z", existing=frozen)
    supplied = profit_input(frozen)
    attached = attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")
    supplied["rows"][0]["profit_score"] += .01
    with pytest.raises(ValueError, match="conflict"):
        attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:03:00Z", existing=attached)


@pytest.mark.parametrize("mutation", ["inactive", "replay", "preactivation", "late", "naive", "weekend", "future_source", "profit_dependency", "profit_row"])
def test_forward_promotion_gates(mutation):
    active = epoch()
    day = promotion_input()
    now = "2026-09-07T13:00:01Z"
    if mutation == "inactive":
        active = new_epoch("inactive")
    elif mutation == "replay":
        day["generation_mode"] = "REPLAY"
    elif mutation == "preactivation":
        active = new_epoch("later", "2026-09-07T13:01:00Z")
    elif mutation == "late":
        now = "2026-09-08T01:25:00Z"
    elif mutation == "naive":
        now = "2026-09-07T13:00:01"
    elif mutation == "weekend":
        day["exec_date"] = "20260906"
    elif mutation == "future_source":
        day["source"]["snapshot_created_at_utc"] = "2026-09-07T13:00:01Z"
    elif mutation == "profit_dependency":
        day["source"]["profit_sha256"] = "a" * 64
    else:
        day["rows"][0]["profit_rank"] = 1
    with pytest.raises(ValueError):
        freeze_promotion_day(active, day, open_dates=DATES, now_utc=now)


def test_profit_also_has_independent_prospective_cutoff():
    frozen = promotion()
    with pytest.raises(ValueError, match="late first admission"):
        attach_profit_day(epoch(), frozen, profit_input(frozen), now_utc="2026-09-08T01:25:00Z")
    supplied = profit_input(frozen)
    supplied["generation_mode"] = "REPLAY"
    with pytest.raises(ValueError, match="NATURAL"):
        attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")


def test_truth_records_without_any_profit_and_preserves_settled_values():
    frozen = promotion(1)
    code = frozen["rows"][0]["ts_code"]
    market = {
        "20260908": [{"ts_code": code, "trade_date": "20260908", "open": 10,
                       "high": 11, "low": 10, "close": 11, "vol": 100,
                       "up_limit": 11, "down_limit": 9}],
        "20260909": [{"ts_code": code, "trade_date": "20260909", "open": 10.8,
                       "high": 11, "low": 10, "close": 10.5, "vol": 100,
                       "up_limit": 12.1, "down_limit": 9.9}],
    }
    first = verify_day(frozen, market, DATES, as_of_date="20260908")
    sidecar = record_day_verification(frozen, first)
    assert sidecar["verifications"][-1]["rows"][0]["t_status"] == "PROMOTED"
    final = verify_day(frozen, market, DATES, as_of_date="20260909",
                       corporate_action_evidence={code: corporate_evidence(frozen, code)})
    sidecar = record_day_verification(frozen, final, existing=sidecar)
    settled = copy.deepcopy(sidecar["verifications"][-1]["rows"])
    missing = verify_day(frozen, {}, DATES, as_of_date="20260910")
    updated = record_day_verification(frozen, missing, existing=sidecar)
    assert updated["verifications"][-1]["rows"] == settled
    assert record_day_verification(frozen, missing, existing=updated) == updated
    assert len(sidecar["verifications"]) == 2


def test_truth_wrong_freeze_and_prior_corruption_fail_only_truth():
    frozen = promotion(1)
    verification = verify_day(frozen, {}, DATES, as_of_date="20260907")
    sidecar = record_day_verification(frozen, verification)
    verification["freeze_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        record_day_verification(frozen, verification, existing=sidecar)
    verification["freeze_sha256"] = frozen["freeze_sha256"]
    sidecar["verifications"][0]["as_of_date"] = "20260908"
    with pytest.raises(ValueError, match="modified"):
        record_day_verification(frozen, verification, existing=sidecar)
    assert freeze_promotion_day(epoch(), promotion_input(1), open_dates=DATES,
                                now_utc="2026-09-07T13:00:01Z", existing=frozen) == frozen


def test_p0_reads_only_epoch_and_target_d_not_profit_truth_latest_or_history(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    book = Daybook(root, epoch())
    # Corrupt auxiliary, old-P0 and global pointer artifacts must not be consulted.
    for relative in ("profit/20260907.json", "truth/20260907.json", "promotion/20260904.json", "latest.json"):
        storage.compare_and_swap(root / relative, {"deliberately_invalid": True}, None)
    original_read = storage.read_state
    allowed = {root / "epoch.json", root / "promotion/20260907.json"}
    reads = []
    def guarded_read(path):
        path = Path(path)
        assert path in allowed, f"P0 illegally read dependency {path}"
        reads.append(path)
        return original_read(path)
    monkeypatch.setattr(storage, "read_state", guarded_read)
    frozen = book.freeze_promotion(promotion_input(), open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    assert frozen["rows"]
    assert reads and set(reads) <= allowed
    assert book.freeze_promotion(promotion_input(), open_dates=DATES, now_utc="2026-09-07T13:01:00Z") == frozen


def test_persistence_profit_missing_is_none_not_ready_zero_and_p0_file_unchanged(tmp_path):
    root = tmp_path.resolve()
    book = Daybook(root, epoch())
    frozen = book.freeze_promotion(promotion_input(0), open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    assert book.read_profit("20260907") is None
    before = (root / "promotion/20260907.json").read_bytes()
    attached = book.attach_profit(profit_input(frozen), now_utc="2026-09-07T13:02:01Z")
    assert attached["status"] == "READY" and attached["profit_top2"] == []
    assert book.read_profit("20260907") == attached
    assert (root / "promotion/20260907.json").read_bytes() == before
    assert not (root / "latest.json").exists()


def test_auxiliary_failure_does_not_block_next_d(tmp_path):
    root = tmp_path.resolve()
    book = Daybook(root, epoch())
    book.freeze_promotion(promotion_input(), open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    storage.compare_and_swap(root / "profit/20260907.json", {"corrupted": True}, None)
    storage.compare_and_swap(root / "truth/20260907.json", {"corrupted": True}, None)
    tomorrow = promotion_input(signal="20260908", generated="2026-09-08T13:00:00Z")
    result = book.freeze_promotion(tomorrow, open_dates=DATES, now_utc="2026-09-08T13:00:01Z")
    assert result["signal_date"] == "20260908"


def test_persisted_truth_needs_no_profit_file(tmp_path):
    root = tmp_path.resolve()
    book = Daybook(root, epoch())
    frozen = book.freeze_promotion(promotion_input(), open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    truth = verify_day(frozen, {}, DATES, as_of_date="20260907")
    result = book.record_verification("20260907", truth)
    assert result["freeze_sha256"] == frozen["freeze_sha256"]
    assert not (root / "profit").exists()


def test_immutable_epoch_inactive_and_wrong_epoch_rejected(tmp_path):
    root = tmp_path.resolve()
    book = Daybook(root, new_epoch("inactive"))
    with pytest.raises(ValueError, match="inactive"):
        book.freeze_promotion(promotion_input(), open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    with pytest.raises(ValueError, match="epoch differs"):
        Daybook(root, epoch())
    assert not (root / "promotion").exists()
    with pytest.raises(ValueError, match="different epoch"):
        attach_profit_day(new_epoch("different", "2026-09-07T07:00:00Z"), promotion(),
                          profit_input(promotion()), now_utc="2026-09-07T13:02:01Z")


def test_local_cas_conflict_never_overwrites_and_symlinks_rejected(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    book = Daybook(root / "store", epoch())
    original_cas = storage.compare_and_swap
    def race(path, value, expected):
        original_cas(path, {"concurrent_owner": True}, expected)
        return original_cas(path, value, expected)
    monkeypatch.setattr(storage, "compare_and_swap", race)
    with pytest.raises(ValueError, match="CAS conflict"):
        book.freeze_promotion(promotion_input(), open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    assert storage.read_state(root / "store/promotion/20260907.json")[0] == {"concurrent_owner": True}
    monkeypatch.setattr(storage, "compare_and_swap", original_cas)
    (root / "link").symlink_to(root / "store", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        Daybook(root / "link", epoch())


def test_cannot_create_profit_or_truth_without_frozen_promotion(tmp_path):
    book = Daybook(tmp_path.resolve(), epoch())
    supplied = profit_input(promotion())
    with pytest.raises(ValueError, match="unfrozen"):
        book.attach_profit(supplied, now_utc="2026-09-07T13:02:01Z")
    with pytest.raises(ValueError, match="unfrozen"):
        book.record_verification("20260907", {})


@pytest.mark.parametrize("mutation", ["top3", "admitted", "sha", "epoch"])
def test_tampered_frozen_promotion_rejected_at_attachments(mutation):
    frozen = promotion()
    supplied = profit_input(frozen)
    if mutation == "top3":
        frozen["promotion_top3"][0]["name"] = "wrong"
    elif mutation == "admitted":
        frozen["admitted_at_utc"] = "2026-09-08T01:25:00Z"
    elif mutation == "sha":
        frozen["freeze_sha256"] = "0" * 64
    else:
        frozen["epoch_id"] = "wrong"
    with pytest.raises(ValueError):
        attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")


def test_profit_score_order_is_required_and_equal_scores_preserve_adapter_rank():
    frozen = promotion()
    supplied = profit_input(frozen)
    supplied["rows"][0]["profit_score"] = 1.0
    with pytest.raises(ValueError, match="descend"):
        attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")
    for row in supplied["rows"]:
        row["profit_score"] = 0.5
    # A legitimate conditional/fill tie-break need not equal stock-code order.
    tied = attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")
    assert [row["ts_code"] for row in tied["rows"]] == ["600003.SH", "600002.SH", "600001.SH"]
    for rank, row in enumerate(supplied["rows"], 1):
        row["profit_rank"] = rank
    attached = attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")
    assert [row["ts_code"] for row in attached["rows"]] == ["600001.SH", "600002.SH", "600003.SH"]


@pytest.mark.parametrize("field,value", [("admitted_at_utc", "2026-09-07T13:00:02Z"),
                                          ("ignored_extra", "not allowed")])
def test_storage_record_hash_covers_admission_and_unknown_fields(field, value):
    frozen = promotion()
    supplied = profit_input(frozen)
    frozen[field] = value
    with pytest.raises(ValueError):
        attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")


def test_profit_admission_and_truth_extra_fields_are_integrity_checked():
    frozen = promotion()
    supplied = profit_input(frozen)
    attached = attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:01Z")
    attached["admitted_at_utc"] = "2026-09-07T13:02:02Z"
    with pytest.raises(ValueError, match="modified"):
        attach_profit_day(epoch(), frozen, supplied, now_utc="2026-09-07T13:02:03Z", existing=attached)
    verification = verify_day(frozen, {}, DATES, as_of_date="20260907")
    sidecar = record_day_verification(frozen, verification)
    sidecar["admitted_at_utc"] = "unexpected"
    with pytest.raises(ValueError, match="unexpected"):
        record_day_verification(frozen, verification, existing=sidecar)
