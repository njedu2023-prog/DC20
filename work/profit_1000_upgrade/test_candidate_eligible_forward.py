"""Versioned freeze integration with synthetic bound sources and fixed model.

Fixture injection is explicit; passing never proves natural publication.
"""
from datetime import datetime
import json
from pathlib import Path
import pytest
from work.profit_1000_upgrade import candidate_eligible_forward as m
from work.profit_1000_upgrade import candidate_natural_forward_test as fixtures


def setup(tmp_path,monkeypatch,**options):
    monkeypatch.setattr(fixtures,"m",m)
    return fixtures.setup_case(tmp_path,monkeypatch,**options)


@pytest.mark.parametrize("size",[0,1,2,3,10,13])
@pytest.mark.parametrize("missing",[False,True])
def test_freeze_retains_original_promotion_and_scores_only_eligible(tmp_path,monkeypatch,size,missing):
    case=setup(tmp_path,monkeypatch,size=size,missing_bar=missing)
    receipt=fixtures.run(case)
    frozen=fixtures.record(receipt)
    p=frozen["prediction"]
    assert p["original_candidate_count"] == min(10,size)
    assert p["eligible_candidate_count"] == min(10,size)-(1 if size and missing else 0)
    assert [r["promotion_rank"] for r in p["promotion_rows"]] == list(range(1,min(10,size)+1))
    assert len(frozen["promotion_top3"]) == 3
    if size>=3:
        assert [r["promotion_rank"] for r in frozen["promotion_top3"]] == [1,2,3]
    if size>=3 and missing:
        assert all(s["status"]=="PENDING_T" for s in frozen["candidate_slots"])
        assert frozen["promotion_slots"][0]["ts_code"] == p["promotion_rows"][0]["ts_code"]
        assert frozen["promotion_slots"][0]["candidate_score"] is None
        assert all(s["ts_code"]!=p["promotion_rows"][0]["ts_code"] for s in frozen["candidate_slots"])
    assert frozen["schema_version"] == m.SCHEMA
    assert frozen["clock_mode"] == "INJECTED_TEST_CLOCK_RESEARCH_ONLY"
    assert frozen["model_retrained"] is False
    assert frozen["formal_ledger_written"] is False
    assert m._sealed(frozen)


def test_idempotent_retry_preserves_exact_original_bytes_and_timestamp(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch,size=10,missing_bar=True)
    first=fixtures.run(case)
    path=Path(first["snapshot_path"]); original=path.read_bytes()
    retry=fixtures.run(case,expected_existing_snapshot_sha256=first["snapshot_file_sha256"],
        clock=lambda:datetime.fromisoformat("2026-09-16T08:00:00+00:00"))
    assert retry["new_snapshot_written"] is False
    assert path.read_bytes()==original


def test_late_first_freeze_remains_forbidden(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch,size=10,missing_bar=True)
    with pytest.raises(ValueError,match="LATE_FIRST_FREEZE_FORBIDDEN"):
        fixtures.run(case,clock=lambda:datetime.fromisoformat("2026-09-15T01:25:00+00:00"))
    assert not case["output"].exists()


def test_source_hash_mismatch_cannot_be_excluded_as_stock_deficiency(tmp_path,monkeypatch):
    case=setup(tmp_path,monkeypatch,size=10,missing_bar=True)
    p=case["root"]/m.source._p0_paths(case["day"])["runtime_features"]
    p.write_bytes(p.read_bytes()+b"\n")
    with pytest.raises(ValueError,match="SHA_MISMATCH"):
        fixtures.run(case)
    assert not case["output"].exists()


@pytest.mark.parametrize("field",["candidate_rank","promotion_rank","candidate_score"])
def test_prediction_tampering_is_rejected(tmp_path,monkeypatch,field):
    case=setup(tmp_path,monkeypatch,size=10,missing_bar=True)
    frozen=fixtures.record(fixtures.run(case))
    p=frozen["prediction"]
    if field=="candidate_score": p["rows"][0][field]=float("nan")
    else: p["rows"][0][field]=99
    with pytest.raises(ValueError):
        m.eligible.validate_prediction(p,frozen["D_source_evidence"]["projection"],expected_model_sha256=m.MODEL_SHA)
