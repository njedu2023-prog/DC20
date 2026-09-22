"""Unit profile checks only; no Git writer, network or source authority."""
import pytest
from work.profit_1000_upgrade import candidate_natural_evidence_publication as m


@pytest.mark.parametrize("contract,expected", [
    (m.capture.LEGACY_CAPTURE_CONTRACT, m.LEGACY_OBSERVER_SHA),
    (m.capture.PRE_ELIGIBLE_CAPTURE_CONTRACT, m.PRE_ELIGIBLE_OBSERVER_SHA),
    ((m.capture.SELF_SHA, m.capture.PUBLICATION_SHA), m.OBSERVER_SHA),
])
def test_exact_capture_pair_selects_matching_observer(contract, expected):
    manifest = dict(zip(("capture_code_sha256", "publication_verifier_sha256"), contract))
    assert m._observer_profile(manifest)[0] == expected


@pytest.mark.parametrize("i,j", [(0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1)])
def test_cross_version_capture_pair_rejected(i, j):
    contracts = m.capture.capture_contracts()
    manifest = {"capture_code_sha256": contracts[i][0], "publication_verifier_sha256": contracts[j][1]}
    with pytest.raises(ValueError, match="EXACT_CAPTURE_PROFILE_REQUIRED"):
        m._observer_profile(manifest)


def test_unknown_capture_rejected():
    with pytest.raises(ValueError, match="EXACT_CAPTURE_PROFILE_REQUIRED"):
        m._observer_profile({"capture_code_sha256": "0" * 64, "publication_verifier_sha256": "0" * 64})
