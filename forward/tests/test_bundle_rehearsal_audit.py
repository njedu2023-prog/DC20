"""Independent regression audit for the staged input -> P0 -> P1 boundary."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat

import pytest

from forward import bundle_rehearsal as cli, promotion
from forward.storage import encoded
from forward.tests.test_bundle_rehearsal import case, compute, NOW, REV

REAL_STATE = cli._state
EXPIRED = "2026-09-08T01:20:00Z"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def p1(c, **kwargs):
    return cli.compute_profit(c["root"], c["output"], c["profit_output"],
        primary_receipt_sha=digest(c["output"] / "receipt.json"), env=c["env"], **kwargs)


@pytest.fixture
def natural(case, monkeypatch):
    """Use complete date/source fields independently of the orchestration mock."""
    def model(*args, **kwargs):
        return {"day": {"signal_date": "20260907", "exec_date": "20260908", "exit_date": "20260909",
            "rows": [], "source": {"input_manifest_sha256": case["digest"], "source_commit": REV,
                "candidate": {"resolved_commit": "b" * 40}, "market": {"resolved_commit": "c" * 40}}},
            "runtime_rows": []}
    monkeypatch.setattr(promotion, "compute_promotion_from_inputs", model)
    return case


def test_p0_cas_crossing_deadline_leaves_no_success_receipt(natural, monkeypatch):
    current = [NOW]
    monkeypatch.setattr(cli, "_utc", lambda: current[0])
    original = cli.compare_and_swap
    def advance(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if Path(path).name == "promotion.json":
            current[0] = EXPIRED
        return result
    monkeypatch.setattr(cli, "compare_and_swap", advance)
    with pytest.raises(ValueError):
        compute(natural, natural=True)
    assert (natural["output"] / "primary.json").is_file()
    assert (natural["output"] / "promotion.json").is_file()
    assert not (natural["output"] / "receipt.json").exists()


def test_p1_cas_crossing_deadline_does_not_damage_p0(natural, monkeypatch):
    compute(natural, natural=True)
    primary = {p.name: p.read_bytes() for p in natural["output"].glob("*.json")}
    current = [NOW]
    monkeypatch.setattr(cli, "_utc", lambda: current[0])
    original = cli.compare_and_swap
    def advance(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if Path(path).name == "profit.json":
            current[0] = EXPIRED
        return result
    monkeypatch.setattr(cli, "compare_and_swap", advance)
    with pytest.raises(ValueError):
        p1(natural)
    assert (natural["profit_output"] / "profit.json").is_file()
    assert not (natural["profit_output"] / "receipt.json").exists()
    assert {p.name: p.read_bytes() for p in natural["output"].glob("*.json")} == primary


def test_primary_receipt_external_pin_required_and_protects_embedded_evidence(natural):
    compute(natural, natural=True)
    pin = digest(natural["output"] / "receipt.json")
    receipt_path = natural["output"] / "receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    evidence = receipt["input_evidence"]
    evidence["acceptance_receipt"]["sources"][cli.UPSTREAMS[0]] = "d" * 40
    evidence["acceptance_receipt_sha256"] = hashlib.sha256(encoded(evidence["acceptance_receipt"])).hexdigest()
    receipt_path.write_bytes(encoded(receipt))
    for expected in [None, "", "bad", pin]:
        with pytest.raises(ValueError):
            cli.compute_profit(natural["root"], natural["output"], natural["profit_output"],
                primary_receipt_sha=expected, env=natural["env"])
    assert not natural["profit_output"].exists()


def test_p1_uses_the_same_primary_bytes_it_hash_verified(natural, monkeypatch):
    compute(natural, natural=True)
    target = natural["output"] / "primary.json"
    original = Path.read_bytes
    reads = []
    def once(path):
        if path == target:
            reads.append(path)
            if len(reads) > 1:
                raise AssertionError("P0 primary payload reopened after hash verification")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", once)
    result = p1(natural)
    assert result["kind"] == "PROFIT_INFERENCE"
    assert reads == [target]


@pytest.mark.parametrize("kind", ["candidate", "market", "exec_date", "exit_date"])
def test_even_repinned_p0_must_match_natural_source_and_full_dates(natural, kind):
    compute(natural, natural=True)
    primary_path = natural["output"] / "primary.json"
    primary = json.loads(primary_path.read_bytes())
    if kind in {"candidate", "market"}:
        primary["day"]["source"][kind]["resolved_commit"] = "d" * 40
    else:
        primary["day"][kind] = "20260910"
    primary_path.write_bytes(encoded(primary))
    promotion_path = natural["output"] / "promotion.json"
    promotion_path.write_bytes(encoded(primary["day"]))
    receipt_path = natural["output"] / "receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["files"].update({"primary.json": digest(primary_path), "promotion.json": digest(promotion_path)})
    receipt_path.write_bytes(encoded(receipt))
    with pytest.raises(ValueError):
        p1(natural)
    assert not natural["profit_output"].exists()


def test_natural_acceptance_start_cannot_predate_current_run_creation(natural):
    receipt = deepcopy(natural["receipt"])
    receipt["started_at_utc"] = "2026-09-07T13:15:30Z"  # created_at is 13:16.
    raw = encoded(receipt)
    (natural["acceptance"] / "receipt.json").write_bytes(raw)
    natural["receipt_sha"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        compute(natural, natural=True)
    assert not natural["output"].exists()


def test_legacy_symlinks_are_metadata_only_not_new_input_dependencies(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.csv").write_text("must never be inspected")
    (root / "outputs").symlink_to(external, target_is_directory=True)
    (root / "work").symlink_to(tmp_path / "does-not-exist", target_is_directory=True)
    (root / "data").mkdir()
    (root / "data/link").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(cli, "_head", lambda _: REV)
    monkeypatch.setattr(cli.subprocess, "check_output", lambda *a, **k: "")
    monkeypatch.setattr(Path, "read_bytes", lambda *a, **k: (_ for _ in ()).throw(AssertionError("old content read")))
    before = REAL_STATE(root)
    layout = before[2]
    assert stat.S_ISLNK(layout["outputs"][0]) and stat.S_ISLNK(layout["work"][0])
    assert stat.S_ISLNK(layout["data/link"][0])
    assert not any("secret.csv" in key for key in layout)
    assert REAL_STATE(root) == before
    (root / "work").unlink()
    (root / "work").symlink_to(external, target_is_directory=True)
    assert REAL_STATE(root) != before


@pytest.mark.parametrize("values", [
    {"status": "x" * 65}, {"receipt_sha256": "x" * 64},
    {"manifest_sha256": "f" * 63}, {"receipt_sha256": ""},
    {"manifest_sha256": ""}, {"bundle_sha256": "bad"},
])
def test_job_outputs_are_length_and_digest_bounded(tmp_path, values):
    output = tmp_path / "job-output"
    output.touch()
    with pytest.raises(ValueError):
        cli.emit_job_outputs(values, {"GITHUB_ACTIONS": "true", "GITHUB_OUTPUT": str(output)})
    assert output.read_bytes() == b""
