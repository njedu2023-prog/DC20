import copy
import json
import sys
from types import SimpleNamespace

import pytest

from forward import rehearsal
from forward.storage import encoded


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "forward").mkdir(parents=True)
    config = {"production_enabled": False, "activated_at_utc": None,
              "legacy_statistics_import_allowed": False,
              "formal_trade_actions_allowed": False}
    (root / "forward/config.json").write_bytes(encoded(config))
    day = {"signal_date": "20260908", "rows": [{"ts_code": "000001.SZ"}]}
    bundle = {"day": day, "runtime_rows": []}
    monkeypatch.setattr(rehearsal, "_head", lambda root: "a" * 40)
    monkeypatch.setitem(sys.modules, "forward.promotion", SimpleNamespace(
        compute_promotion_bundle=lambda *a, **kw: copy.deepcopy(bundle)))
    return root, tmp_path / "p0", tmp_path / "p1", config


def test_promotion_does_not_import_profit_or_daybook(setup, monkeypatch):
    root, p0, _, _ = setup
    class Unavailable:
        def __getattr__(self, name):
            raise AssertionError("auxiliary layer must not block promotion")
    monkeypatch.setitem(sys.modules, "forward.profit", Unavailable())
    monkeypatch.setitem(sys.modules, "forward.daybook", Unavailable())
    receipt = rehearsal.compute_promotion(root, p0, "20260908")
    bundle, reread = rehearsal.read_primary(p0)
    assert receipt == reread
    assert bundle["day"]["signal_date"] == "20260908"
    assert receipt["inference_performed"] is True
    assert receipt["forward_ledger_eligible"] is False


def test_profit_failure_cannot_erase_promotion(setup, monkeypatch):
    root, p0, p1, _ = setup
    rehearsal.compute_promotion(root, p0, "20260908")
    before = {p.name: p.read_bytes() for p in p0.iterdir()}
    def fail(*a):
        raise ValueError("profit model unavailable")
    monkeypatch.setitem(sys.modules, "forward.profit", SimpleNamespace(infer_profit=fail))
    with pytest.raises(ValueError, match="profit model"):
        rehearsal.compute_profit(root, p0, p1)
    assert {p.name: p.read_bytes() for p in p0.iterdir()} == before
    assert not p1.exists()


def test_profit_receipt_independent_and_bound(setup, monkeypatch):
    root, p0, p1, _ = setup
    rehearsal.compute_promotion(root, p0, "20260908")
    monkeypatch.setitem(sys.modules, "forward.profit", SimpleNamespace(
        infer_profit=lambda *a: {"rows": [], "generation_mode": "REPLAY"}))
    receipt = rehearsal.compute_profit(root, p0, p1)
    assert set(receipt["files"]) == {"profit.json"}
    assert len(json.loads((p1 / "profit.json").read_text())["rehearsal_promotion_receipt_sha256"]) == 64


@pytest.mark.parametrize("filename", ["primary.json", "promotion.json"])
def test_mutated_input_rejected(setup, filename):
    root, p0, p1, _ = setup
    rehearsal.compute_promotion(root, p0, "20260908")
    (p0 / filename).write_bytes(b"{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        rehearsal.compute_profit(root, p0, p1)


def test_missing_receipt_is_not_complete(setup):
    root, p0, _, _ = setup
    rehearsal.compute_promotion(root, p0, "20260908")
    (p0 / "receipt.json").unlink()
    with pytest.raises(FileNotFoundError):
        rehearsal.read_primary(p0)


def test_cross_revision_profit_rejected(setup, monkeypatch):
    root, p0, p1, _ = setup
    rehearsal.compute_promotion(root, p0, "20260908")
    monkeypatch.setattr(rehearsal, "_head", lambda root: "b" * 40)
    with pytest.raises(ValueError, match="source revision"):
        rehearsal.compute_profit(root, p0, p1)


def test_inference_head_change_is_not_published(setup, monkeypatch):
    root, p0, _, _ = setup
    counter = iter(["a" * 40, "b" * 40])
    monkeypatch.setattr(rehearsal, "_head", lambda root: next(counter))
    with pytest.raises(ValueError, match="changed HEAD"):
        rehearsal.compute_promotion(root, p0, "20260908")
    assert not p0.exists()


def test_untracked_output_mutation_caught_before_receipt(setup, monkeypatch):
    root, p0, _, _ = setup
    def mutating(*a, **kw):
        (root / "outputs").mkdir()
        (root / "outputs/untracked.json").write_bytes(b"{}")
        return {"day": {"signal_date": "20260908", "rows": []}}
    monkeypatch.setitem(sys.modules, "forward.promotion", SimpleNamespace(compute_promotion_bundle=mutating))
    with pytest.raises(ValueError, match="protected repository"):
        rehearsal.compute_promotion(root, p0, "20260908")
    assert not p0.exists()


@pytest.mark.parametrize("key,value", [("production_enabled", True),
    ("activated_at_utc", "2026-09-09T00:00:00Z"),
    ("legacy_statistics_import_allowed", True), ("formal_trade_actions_allowed", True)])
def test_activated_or_unsafe_configuration_rejected(setup, key, value):
    root, p0, _, config = setup
    config[key] = value
    (root / "forward/config.json").write_bytes(encoded(config))
    with pytest.raises(ValueError, match="inactive"):
        rehearsal.compute_promotion(root, p0, "20260908")


def test_no_source_overwrite_or_existing_destination(setup):
    root, p0, _, _ = setup
    with pytest.raises(ValueError, match="outside"):
        rehearsal.compute_promotion(root, root / "outputs/decision", "20260908")
    rehearsal.compute_promotion(root, p0, "20260908")
    with pytest.raises(ValueError, match="fresh empty"):
        rehearsal.compute_promotion(root, p0, "20260908")


def test_symlink_receipt_rejected(setup, tmp_path):
    root, p0, _, _ = setup
    rehearsal.compute_promotion(root, p0, "20260908")
    receipt = (p0 / "receipt.json").read_bytes()
    (tmp_path / "elsewhere.json").write_bytes(receipt)
    (p0 / "receipt.json").unlink()
    (p0 / "receipt.json").symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(ValueError, match="symlink"):
        rehearsal.read_primary(p0)


@pytest.mark.parametrize("mutation", [None, "rank", "score", "missing_path", "membership"])
def test_post_compute_comparison_only_accepts_equivalence(setup, monkeypatch, mutation):
    root, p0, p1, _ = setup
    row = {"ts_code": "000001.SZ", "name": "company", "industry": "sector",
           "stage_transition": "2→3", "promotion_rank": 1,
           "promotion_probability": .5, "path_label": "持续强势", "path_change_pct": 0.0}
    day = {"signal_date": "20260908", "exec_date": "20260909", "exit_date": "20260910", "rows": [row]}
    old = copy.deepcopy(day)
    old["rows"][0].update(profit_rank=1, profit_score=.3)
    actual = copy.deepcopy(day)
    profit_row = {"ts_code": "000001.SZ", "profit_rank": 1, "profit_score": .3}
    if mutation == "rank":
        profit_row["profit_rank"] = 2
    elif mutation == "score":
        profit_row["profit_score"] = .4
    elif mutation == "missing_path":
        actual["rows"][0]["path_change_pct"] = None
    elif mutation == "membership":
        profit_row["ts_code"] = "000002.SZ"
    monkeypatch.setitem(sys.modules, "forward.promotion", SimpleNamespace(
        compute_promotion_bundle=lambda *a, **kw: {"day": actual}))
    monkeypatch.setitem(sys.modules, "forward.profit", SimpleNamespace(
        infer_profit=lambda *a: {"rows": [profit_row]}))
    monkeypatch.setitem(sys.modules, "forward.engine", SimpleNamespace(
        load_frozen_day=lambda *a, **kw: old))
    rehearsal.compute_promotion(root, p0, "20260908")
    rehearsal.compute_profit(root, p0, p1)
    if mutation is None:
        result = rehearsal.compare_replay(root, p0, p1)
        assert result["status"] == "RECOMPUTED_REPLAY_MATCH"
        assert result["forward_ledger_days_added"] == 0
    else:
        with pytest.raises(ValueError):
            rehearsal.compare_replay(root, p0, p1)
