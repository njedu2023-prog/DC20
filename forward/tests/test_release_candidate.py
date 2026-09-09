"""Publication handoff tests; trust parsing is covered separately by readers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from forward import release_candidate as release
from forward.storage import encoded

REV = "a" * 40
P0 = "b" * 64
P1 = "c" * 64
NOW = "2026-09-07T13:20:00Z"


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "forward").mkdir(parents=True)
    (root / "forward/config.json").write_bytes(encoded({
        "production_enabled": False, "activated_at_utc": None,
        "start_signal_date": None, "legacy_statistics_import_allowed": False,
        "formal_trade_actions_allowed": False, "phase": "MIGRATION_ACCEPTANCE"}))
    primary_dir, profit_dir = tmp_path / "primary", tmp_path / "profit"
    primary_dir.mkdir()
    profit_dir.mkdir()
    evidence = {"origin": "HISTORICAL_PINNED_INPUT_REPLAY", "manifest_sha256": "d" * 64}
    rows = [{"ts_code": f"60000{i}.SH", "name": f"公司{i}", "industry": "行业",
             "promotion_rank": i, "promotion_probability": 0.8 - i / 10,
             "stage_transition": "2→3", "path_label": None, "path_change_pct": None}
            for i in range(1, 5)]
    day = {"signal_date": "20260907", "exec_date": "20260908", "exit_date": "20260909",
           "generated_at_utc": NOW, "generation_mode": "REPLAY", "rows": rows,
           "source": {"model_sha256": "e" * 64, "members_sha256": "f" * 64}}
    primary = {"primary": {"day": day}, "receipt": {"source_revision": REV},
               "receipt_sha256": P0, "evidence": evidence, "gate": None}
    profit = {"profit": {**{key: day[key] for key in release.DATE_FIELDS},
              "generated_at_utc": NOW, "source": {"model_sha256": "1" * 64},
              "rows": [{"ts_code": row["ts_code"], "name": row["name"],
                        "promotion_rank": row["promotion_rank"], "profit_rank": 5 - row["promotion_rank"],
                        "profit_score": row["promotion_rank"] / 10} for row in rows]},
              "receipt": {"source_revision": REV}, "receipt_sha256": P1,
              "evidence": evidence, "gate": None}
    monkeypatch.setattr(release, "read_promotion", lambda *a, **k: deepcopy(primary))
    monkeypatch.setattr(release, "read_profit", lambda *a, **k: deepcopy(profit))
    monkeypatch.setattr(release.rehearsal, "_head", lambda _: REV)
    monkeypatch.setattr(release.rehearsal, "_state", lambda _: (REV, "", {}))
    monkeypatch.setattr(release.rehearsal, "_utc", lambda: NOW)
    return {"root": root, "primary_dir": primary_dir, "profit_dir": profit_dir,
            "primary": primary, "profit": profit, "output": tmp_path / "candidate"}


def build(c, kind="promotion"):
    return release.build_candidate(c["root"], c["primary_dir"], P0, c["output"],
        kind=kind, profit_directory=c["profit_dir"] if kind == "profit" else None,
        profit_receipt_sha=P1 if kind == "profit" else None, env={})


def test_promotion_handoff_never_reads_profit_or_records_slots(case, monkeypatch):
    monkeypatch.setattr(release, "read_profit", lambda *a, **k: pytest.fail("P0 read P1"))
    receipt = build(case)
    value = json.loads((case["output"] / "promotion.json").read_bytes())
    assert value["rows"] == case["primary"]["primary"]["day"]["rows"]
    assert [row["slot"] for row in value["promotion_top3"]] == ["Top1", "Top2", "Top3"]
    assert all(row["recording_status"] == release.NOT_RECORDED for row in value["promotion_top3"])
    assert "shadow_top2" not in value and not any("profit_rank" in row for row in value["rows"])
    assert receipt["files"]["promotion.json"] == hashlib.sha256((case["output"] / "promotion.json").read_bytes()).hexdigest()
    assert not value["ledger_written"] and not value["publication_verified"]
    assert receipt["new_forward_days"] == 0 and not receipt["forward_ledger_eligible"]
    assert not (case["output"] / "ledger.json").exists()


def test_profit_handoff_includes_automatic_top2_names_dates_and_keeps_promotion(case):
    before = deepcopy(case["primary"])
    build(case, "profit")
    value = json.loads((case["output"] / "profit.json").read_bytes())
    assert [row["profit_rank"] for row in value["rows"]] == [1, 2, 3, 4]
    assert [row["promotion_rank"] for row in value["rows"]] == [4, 3, 2, 1]
    assert [row["name"] for row in value["shadow_top2"]] == ["公司4", "公司3"]
    assert all(row["signal_date"] == "20260907" and row["exec_date"] == "20260908"
               and row["exit_date"] == "20260909" and not row["ledger_recorded"] for row in value["shadow_top2"])
    assert case["primary"] == before
    assert value["rows"][0]["path_label"] is None
    assert value["epoch_activated_at_utc"] is None and value["formal_trade_count"] == 0
    assert value["generation_mode"] == "REPLAY"


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("kind", ["promotion", "profit"])
def test_no_slots_or_candidates_are_padded(case, n, kind):
    case["primary"]["primary"]["day"]["rows"] = case["primary"]["primary"]["day"]["rows"][:n]
    case["profit"]["profit"]["rows"] = case["profit"]["profit"]["rows"][:n]
    for row in case["profit"]["profit"]["rows"]:
        row["profit_rank"] = n + 1 - row["promotion_rank"]
    build(case, kind)
    value = json.loads((case["output"] / f"{kind}.json").read_bytes())
    assert value["candidate_count"] == n and len(value["promotion_top3"]) == min(3, n)
    if kind == "profit":
        assert len(value["shadow_top2"]) == min(2, n)


def test_conflict_never_overwrites_and_failed_profit_does_not_remove_p0(case, monkeypatch):
    build(case)
    original = {p.name: p.read_bytes() for p in case["output"].glob("*.json")}
    with pytest.raises(ValueError):
        build(case)
    previous_output = case["output"]
    case["output"] = previous_output.parent / "p1-candidate"
    monkeypatch.setattr(release, "read_profit", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad P1")))
    with pytest.raises(ValueError):
        build(case, "profit")
    assert {p.name: p.read_bytes() for p in previous_output.glob("*.json")} == original
    assert not case["output"].exists()


def test_crossing_window_during_write_has_no_success_receipt(case, monkeypatch):
    case["primary"]["evidence"]["origin"] = "NATURAL_SCHEDULE_STAGING"
    expired = [False]
    def gate(*a, **k):
        if expired[0]:
            raise ValueError("original window expired")
        return {"checked_at_utc": NOW}
    monkeypatch.setattr(release.rehearsal, "verify_natural_evidence", gate)
    original = release.compare_and_swap
    def delayed(path, *args):
        result = original(path, *args)
        expired[0] = True
        return result
    monkeypatch.setattr(release, "compare_and_swap", delayed)
    with pytest.raises(ValueError):
        build(case)
    assert (case["output"] / "promotion.json").exists()
    assert not (case["output"] / "receipt.json").exists()


def test_source_mutation_invalid_scope_or_activated_config_blocks(case, monkeypatch):
    case["output"] = case["primary_dir"] / "nested"
    with pytest.raises(ValueError):
        build(case)
    case["output"] = case["root"] / "outputs"
    with pytest.raises(ValueError):
        build(case)
    case["output"] = case["root"].parent / "new-candidate"
    monkeypatch.setattr(release.rehearsal, "_unchanged", lambda *a: (_ for _ in ()).throw(ValueError("source mutation")))
    with pytest.raises(ValueError):
        build(case)
    assert not case["output"].exists()
