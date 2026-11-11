"""Exact predecessor compatibility and new execution profiles, never new evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from work.profit_1000_upgrade import candidate_natural_forward as natural
from work.profit_1000_upgrade import candidate_eligible_forward as eligible
from work.profit_1000_upgrade import candidate_natural_publication as publication
from work.profit_1000_upgrade import candidate_natural_p0_continuation as continuation
from work.profit_1000_upgrade import candidate_natural_evidence as capture
from work.profit_1000_upgrade import candidate_natural_evidence_publication as observation
from work.profit_1000_upgrade import candidate_natural_outcomes as outcomes
from work.profit_1000_upgrade import candidate_natural_outcome_collect as collector
from scripts import publish_primary_three_rank as publisher

gh = publication.gh
ROOT = Path(__file__).resolve().parents[1]


def tree(profile):
    return {p: {"type": "blob", "mode": "100644", "sha": value} for p, value in profile.items()}


@pytest.mark.parametrize("runner", [natural, eligible])
def test_exact_old_snapshot_tuple_is_readable_through_original_day(runner):
    frozen = {**deepcopy(runner.PRE_RELIABILITY_SNAPSHOT_PROFILE), "signal_date": "20261110"}
    assert runner.snapshot_profile(frozen) == runner.PRE_RELIABILITY_SNAPSHOT_PROFILE
    frozen["signal_date"] = "20261111"
    with pytest.raises(ValueError, match="UNREGISTERED_SNAPSHOT"):
        runner.snapshot_profile(frozen)
    frozen["signal_date"] = "20261110"
    frozen["dependency_sha256"] = dict(runner.PINS)
    with pytest.raises(ValueError, match="UNREGISTERED_SNAPSHOT"):
        runner.snapshot_profile(frozen)


def test_current_source_guards_bind_registry_and_preflight():
    files, _ = publication.code_guard()
    assert publication.RELIABILITY_ONLY_CODE_FILES <= set(files)
    observation.code_guard()
    assert "src/top10decision/decision/suspension_evidence_registry.py" in gh.adapter.CODE_PINS
    assert "scripts/candidate_runtime_preflight.py" in publication.FIXED_FILES


def test_exact_predecessor_publication_does_not_require_future_reader_modules():
    bindings = publication.PRE_RELIABILITY_CODE_BINDINGS
    original = tree({b["path"]: b["git_blob_sha1"] for b in bindings})
    files, _ = publication.code_guard()
    assert publication.match_publication_code(original, deepcopy(original), files, day="20261110") == sorted(bindings, key=lambda b: b["path"])
    with pytest.raises(ValueError, match="DATE_OR_SET"):
        publication.match_publication_code(original, original, files, day="20261111")
    with pytest.raises(ValueError, match="DATE_OR_SET"):
        publication.match_publication_code(original, original, {**files, "unreviewed.py": b"new"}, day="20261110")
    changed = deepcopy(original)
    changed[publication.IMPORTER_PATH]["sha"] = "f" * 40
    with pytest.raises(ValueError, match="BYTES_CHANGED"):
        publication.match_publication_code(original, changed, files, day="20261110")


def test_predecessor_capture_and_observer_are_a_complete_pair():
    manifest = {"capture_code_sha256": capture.PRE_RELIABILITY_CAPTURE_CONTRACT[0],
                "publication_verifier_sha256": capture.PRE_RELIABILITY_CAPTURE_CONTRACT[1],
                "signal_date": "20261110"}
    assert capture.PRE_RELIABILITY_CAPTURE_CONTRACT in capture.capture_contracts()
    assert observation._observer_profile(manifest) == observation.PRE_RELIABILITY_OBSERVER_PROFILE
    manifest["signal_date"] = "20261111"
    with pytest.raises(ValueError, match="PRE_RELIABILITY_OBSERVER_DATE"):
        observation._observer_profile(manifest)


def test_new_p0_profile_binds_all_current_execution_files():
    profile = continuation.TOP1_START_DATE_P0_CODE_PROFILE
    original = continuation.RELIABILITY_P0_CODE_PROFILE
    assert set(profile) == set(original)
    assert {p for p in original if profile[p] != original[p]} == {continuation.MANIFEST_PATH}
    assert "data/market/confirmed_suspensions.json" not in profile
    assert not any(p.startswith("data/market/suspension") for p in profile)
    for path, expected in profile.items():
        assert gh.git_blob((ROOT / path).read_bytes()) == expected, path
    bindings = continuation.RELIABILITY_RUNTIME_CODE_BINDINGS
    assert [b["path"] for b in bindings] == list(publisher.PRIMARY_RUNTIME_CODE_PATHS)
    for binding in bindings:
        raw = (ROOT / binding["path"]).read_bytes()
        assert binding == {"path": binding["path"], "sha256": hashlib.sha256(raw).hexdigest(),
                           "git_blob_sha1": gh.git_blob(raw), "git_mode": "100644"}


@pytest.mark.parametrize("profile", continuation.RELIABILITY_PROFILES, ids=["original", "top1-paper", "top1-count", "top1-rate", "top1-month", "top1-fill-note", "top1-start-date"])
def test_new_p0_profile_admits_only_new_date_and_three_unchanged_trees(profile):
    day = "20261112" if profile in (continuation.TOP1_RATE_P0_CODE_PROFILE, continuation.TOP1_MONTH_P0_CODE_PROFILE, continuation.TOP1_FILL_NOTE_P0_CODE_PROFILE, continuation.TOP1_START_DATE_P0_CODE_PROFILE) else "20261111"
    trees = [tree(profile) for _ in range(3)]
    if profile in (continuation.TOP1_RATE_P0_CODE_PROFILE, continuation.TOP1_MONTH_P0_CODE_PROFILE, continuation.TOP1_FILL_NOTE_P0_CODE_PROFILE, continuation.TOP1_START_DATE_P0_CODE_PROFILE):
        with pytest.raises(ValueError, match="ACTIVATION_DATE"):
            continuation.validate_code_profiles(gh, None, *trees, {"signal_date": "20261111"})
    result, transition = continuation.validate_code_profiles(gh, None, *trees, {"signal_date": day})
    assert result is profile and transition is None
    with pytest.raises(ValueError, match="ACTIVATION_DATE"):
        continuation.validate_code_profiles(gh, None, *trees, {"signal_date": "20261110"})
    for index in range(3):
        broken = deepcopy(trees)
        broken[index]["src/top10decision/decision/suspension_evidence_registry.py"]["sha"] = "f" * 40
        with pytest.raises(ValueError, match="CODE_PROFILE_REQUIRED"):
            continuation.validate_code_profiles(gh, None, *broken, {"signal_date": day})
        for other in continuation.RELIABILITY_PROFILES:
            if other is profile:
                continue
            mixed = deepcopy(trees)
            mixed[index][continuation.MANIFEST_PATH]["sha"] = other[continuation.MANIFEST_PATH]
            with pytest.raises(ValueError, match="CODE_PROFILE_REQUIRED"):
                continuation.validate_code_profiles(gh, None, *mixed, {"signal_date": "20261112"})


@pytest.mark.parametrize("runner", [natural, eligible])
def test_predecessor_outcome_writer_uses_exact_original_snapshot_profile(runner):
    frozen = {"schema_version": runner.SCHEMA, "signal_date": "20261110",
              **deepcopy(runner.PRE_RELIABILITY_SNAPSHOT_PROFILE)}
    old_writer, old_pins = outcomes.PRE_RELIABILITY_REPORT_PROFILE
    report = {"writer_sha256": old_writer, "dependencies": deepcopy(old_pins)}
    assert outcomes.validate_report_profile(report, frozen) == (old_writer, old_pins)
    assert old_writer in collector.accepted_outcome_writers(frozen)
    frozen["signal_date"] = "20261111"
    assert old_writer not in collector.accepted_outcome_writers(frozen)
    with pytest.raises(ValueError, match="UNREGISTERED_OUTCOME"):
        outcomes.validate_report_profile(report, frozen)
    frozen["signal_date"] = "20261110"
    report["dependencies"] = dict(outcomes.PINS)
    with pytest.raises(ValueError, match="UNREGISTERED_OUTCOME"):
        outcomes.validate_report_profile(report, frozen)


@pytest.mark.parametrize("day", ["20260914", "20261009", "20261015", "20261028", "20261106", "20261109", "20261110"])
def test_real_historical_observer_context_preserves_its_original_writer(day):
    folder = ROOT / "work/profit_1000_upgrade/candidate_natural_evidence" / day
    raw = (folder / "context.json").read_bytes()
    context = json.loads(raw)
    manifest = json.loads((folder / "manifest.json").read_bytes())
    observed_raw = (folder / manifest["native_observation"]["path"]).read_bytes()
    assert gh.sha256(observed_raw) == manifest["native_observation"]["sha256"]
    observed = json.loads(observed_raw)
    run = {"id": context["observer_run_id"], "head_sha": context["code_head_sha"]}
    observation._context(context, run, manifest, observed)
    changed = {**context, "writer_sha256": observation.WRITER_SHA}
    with pytest.raises(ValueError, match="EXACT_OBSERVER_CONTEXT"):
        observation._context(changed, run, manifest, observed)
    assert (folder / "context.json").read_bytes() == raw


def test_original_d11_observation_cannot_admit_new_days_or_mixed_source_trees():
    folder = ROOT / "work/profit_1000_upgrade/candidate_natural_evidence/20261111"
    manifest = json.loads((folder / "manifest.json").read_bytes())
    observed = json.loads((folder / manifest["native_observation"]["path"]).read_bytes())
    original = tree({b["path"]: b["git_blob_sha1"] for b in observed["verified_code_bindings"]})
    files, _ = publication.code_guard()
    assert publication.match_publication_code(original, original, files, day="20261111") == observed["verified_code_bindings"]
    with pytest.raises(ValueError, match="D11_PUBLICATION_DATE"):
        publication.match_publication_code(original, original, files, day="20261112")
    changed = deepcopy(original)
    changed[publication.COORDINATOR_PATH]["sha"] = "f" * 40
    with pytest.raises(ValueError, match="D11_PUBLICATION_CODE_CHANGED"):
        publication.match_publication_code(original, changed, files, day="20261111")
    with pytest.raises(ValueError, match="D11_OBSERVER_DATE"):
        observation._observer_profile({**manifest, "signal_date": "20261112"})
