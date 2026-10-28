"""Old continuation capsules keep their exact code pair and full verification."""
from copy import deepcopy

import pytest

from work.profit_1000_upgrade import candidate_natural_evidence as capture
from work.profit_1000_upgrade import candidate_natural_evidence_publication as issuer


def profile_probe(contract, day):
    # Intentionally incomplete synthetic capsule: passing the profile gate must
    # still fail inventory validation and cannot issue any publication proof.
    manifest = {"schema_version": capture.SCHEMA,
        "status": "LOCAL_UNPUBLISHED_OBSERVATION", "test_transport_injected": True,
        "capture_code_sha256": contract[0], "publication_verifier_sha256": contract[1],
        "signal_date": day, "freeze_run_id": "1", "snapshot_file_sha256": "a" * 64,
        "p0_provenance_kind": capture.CONTINUATION_KIND, "files": None,
        **capture.FLAGS}
    raw = capture.gh.json_bytes(manifest)
    return raw, manifest


@pytest.mark.parametrize("contract,day", [
    (capture.PRE_BOUNDARY_CAPTURE_CONTRACT, "20261027"),
    (capture.PRE_CONTINUATION_CAPTURE_CONTRACT, "20261028"),
    ((capture.SELF_SHA, capture.PUBLICATION_SHA), "20261029"),
])
def test_reviewed_continuation_pair_still_requires_original_inventory(contract, day):
    raw, _ = profile_probe(contract, day)
    with pytest.raises(ValueError, match="BOUNDED_EVIDENCE_INVENTORY_REQUIRED"):
        capture.verify_materials(raw, {}, expected_manifest_sha256=capture.sha(raw))


@pytest.mark.parametrize("contract,day,reason", [
    (capture.PRE_BOUNDARY_CAPTURE_CONTRACT, "20261028", "PRE_BOUNDARY_CAPTURE_DATE_CHANGED"),
    (capture.PRE_CONTINUATION_CAPTURE_CONTRACT, "20261029", "PRE_CONTINUATION_CAPTURE_DATE_CHANGED"),
    ((capture.PRE_BOUNDARY_CAPTURE_CONTRACT[0], capture.PUBLICATION_SHA), "20261027", "CAPTURE_CODE_CONTRACT_CHANGED"),
    (("0" * 64, capture.PUBLICATION_SHA), "20261027", "CAPTURE_CODE_CONTRACT_CHANGED"),
    (capture.HISTORICAL_CAPTURE_CONTRACT, "20261027", "CONTINUATION_REQUIRES_REVIEWED_CAPTURE_PROFILE"),
])
def test_unreviewed_mixed_or_future_continuation_profile_is_rejected(contract, day, reason):
    raw, _ = profile_probe(contract, day)
    with pytest.raises(ValueError, match=reason):
        capture.verify_materials(raw, {}, expected_manifest_sha256=capture.sha(raw))


def test_original_d28_observer_pair_is_readable_without_mixed_code_trees():
    _, manifest = profile_probe(capture.PRE_CONTINUATION_CAPTURE_CONTRACT, "20261028")
    original_sha, overrides = issuer._observer_profile(manifest)
    assert original_sha == "df6dc5c44116c037e46f48a3e0dc3a2ca2e2b12e95905038ca1f3a593b25cbae"
    assert overrides == {
        issuer.CAPTURE_PATH: "8d1e1522e895b0772f8122fb0d1361657a2143f7",
        issuer.OBSERVER_PATH: "b9b96bd75e3900dd9648885053355763457fdd2c"}
    local, _ = issuer.code_guard()
    original = {p: {"type": "blob", "mode": "100644",
                   "sha": overrides.get(p, issuer.gh.git_blob(raw))} for p, raw in local.items()}
    issuer.match_observer_code(original, original, local, manifest)
    for path in (issuer.CAPTURE_PATH, issuer.OBSERVER_PATH, issuer.publication.IMPORTER_PATH):
        changed = deepcopy(original)
        changed[path]["sha"] = "0" * 40
        with pytest.raises(ValueError):
            issuer.match_observer_code(changed, original, local, manifest)
    manifest["signal_date"] = "20261029"
    with pytest.raises(ValueError, match="PRE_CONTINUATION_OBSERVER_DATE_CHANGED"):
        issuer._observer_profile(manifest)
