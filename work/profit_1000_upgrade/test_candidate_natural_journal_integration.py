"""Native TEST_ONLY daily -> original-byte journal -> next-run continuation.

Only temporary synthetic HTTP/time/state fixtures are used. Publication fields
are storage references, not a forged private publication proof or real run.
"""
from pathlib import Path
import socket

import pytest

from work.profit_1000_upgrade import candidate_natural_journal as journal
from work.profit_1000_upgrade import test_candidate_natural_daily as daily_tests

ROOT = Path(__file__).absolute().parents[2]
PINS = {
    "candidate_natural_journal.py": "2b8c18728ddbbebd42074f0d6c80ec43d8ec3bf24a5c79e022b0d50b48de9b1e",
    "candidate_natural_daily.py": "88d75a131946be1884da459b8d357942b29f18fd4d83839ea918951291d7e321",
}


def assert_pins():
    for name, expected in PINS.items():
        assert journal.sha((ROOT / "work/profit_1000_upgrade" / name).read_bytes()) == expected


def archive_restore(case, result, *, previous_sha=None):
    assert_pins()
    assert result["status"] == "TEST_ONLY" and result["publishable"] is False and result["test_only"] is True
    parent = case["hooks"]["state_parent"]
    reference = {"evidence_commit": "b"*40, "observer_run_id": 123, "evidence_manifest_sha256": "c"*64,
        "snapshot_file_sha256": result["snapshot_file_sha256"]}
    before = {b["origin_path"]: Path(b["origin_path"]).read_bytes() for b in result["publishable_file_bindings"]}
    built = journal.build_journal(result, publication_binding=reference,
        previous_manifest_sha256=previous_sha, test_state_root=parent)
    assert built["manifest"]["test_only"] is True
    assert built["manifest"]["natural_forward_admission_issued"] is built["manifest"]["source_authority_issued"] is False
    assert built["manifest"]["previous_manifest_sha256"] == previous_sha
    prefix = journal.PREFIX+result["signal_date"]+"/"+result["as_of_date"]+"/"
    raw = built["files"][prefix+"manifest.json"]
    assert journal.encoded(built["manifest"]) == raw
    bodies = {p.removeprefix(prefix): body for p, body in built["files"].items() if p != prefix+"manifest.json"}
    checked = journal.validate_journal(raw, bodies, expected_manifest_sha256=built["manifest_sha256"], test_state_root=parent)
    base = parent/result["signal_date"]/result["as_of_date"]
    old_inode = Path(result["final_collection_receipt_path"]).stat().st_ino
    # Recoverable fixture-only rename simulates the previous runner disappearing.
    base.rename(base.with_name("saved-original-"+result["as_of_date"]))
    restored = journal.restore_journal(raw, bodies, expected_manifest_sha256=built["manifest_sha256"], test_state_root=parent)
    assert restored["status"] == "ORIGINAL_BYTES_RESTORED_STORAGE_ONLY"
    assert restored["production_activation_allowed"] is False
    assert restored["endpoints"] == checked["endpoints"]
    assert Path(result["final_collection_receipt_path"]).stat().st_ino != old_inode
    assert all(Path(path).read_bytes() == body for path, body in before.items())
    for role in ("final_collection_receipt", "final_outcomes", "daily_manifest"):
        binding = restored["endpoints"][role]
        assert binding["origin_path"] == result[role+"_path"]
        assert binding["sha256"] == result[role+"_sha256"]
        assert journal.sha(Path(binding["origin_path"]).read_bytes()) == binding["sha256"]
    assert_pins()
    return built, before


def test_native_T_restore_then_T1_loss_then_terminal_next_day_zero_calls(tmp_path, monkeypatch):
    case = daily_tests.setup(tmp_path, monkeypatch)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("REAL_NETWORK_FORBIDDEN"))
    monkeypatch.setattr(daily_tests.m.natural.scorer, "predict_forward", lambda *args, **kwargs: pytest.fail("RESCORING_FORBIDDEN"))
    source = case["case"]; snapshot_before = source["snapshot"].read_bytes()
    first = daily_tests.run(case, asof=source["t"])
    first_journal, original_first = archive_restore(case, first)
    count = len(case["requests"])
    second = daily_tests.run(case, asof=source["t1"], prior=first)
    assert second["api_calls"] == 3*len(source["codes"]) and len(case["requests"])-count == second["api_calls"]
    second_journal, original_second = archive_restore(case, second, previous_sha=first_journal["manifest_sha256"])
    latest = daily_tests.latest(second)
    for name in ("candidate_slots", "promotion_slots"):
        assert len(latest[name]) == 2
        assert all(row["slot_net_return"] == pytest.approx(-.0245) for row in latest[name])
    count = len(case["requests"])
    third = daily_tests.run(case, asof="20260917", prior=second)
    assert third["api_calls"] == 0 and len(case["requests"]) == count
    archive_restore(case, third, previous_sha=second_journal["manifest_sha256"])
    after = daily_tests.latest(third)
    for code, original in latest["native_singleton_reports"].items():
        assert after["native_singleton_reports"][code]["rows"] == original["rows"]
        assert after["native_singleton_reports"][code]["source_files"] == original["source_files"]
    assert all(Path(p).read_bytes() == raw for p, raw in {**original_first, **original_second}.items())
    assert source["snapshot"].read_bytes() == snapshot_before
    assert_pins()


@pytest.mark.parametrize("size", [0, 1])
def test_native_empty_or_single_candidate_not_due_has_four_explicit_slots(tmp_path, monkeypatch, size):
    case = daily_tests.setup(tmp_path, monkeypatch, size=size)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("REAL_NETWORK_FORBIDDEN"))
    result = daily_tests.run(case, asof=case["case"]["day"])
    archive_restore(case, result)
    assert result["api_calls"] == 0 and case["requests"] == []
    report = daily_tests.latest(result)
    for name in ("candidate_slots", "promotion_slots"):
        assert len(report[name]) == 2
        for index, row in enumerate(report[name]):
            assert row["status"] == ("PENDING_T" if index < size else "MISSING_CANDIDATE")
            assert row["slot_net_return"] is None
