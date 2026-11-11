from copy import deepcopy
import pytest
from tests.test_candidate_suspension_provenance import suspension_case, run, update_receipt


def test_identical_references_preserve_projection_and_conflicts_fail(tmp_path, monkeypatch):
    case, entry, _ = suspension_case(tmp_path, monkeypatch)
    expected = run(case)['projection']
    repeated = deepcopy(entry['used_intervals'][0])
    entry['used_intervals'].append(repeated)
    update_receipt(case)
    actual = run(case)['projection']
    assert actual['diagnostics'] == expected['diagnostics']
    repeated['previous_trading_date'] = repeated['start_date']
    update_receipt(case)
    with pytest.raises(ValueError, match='CONFLICTING_SUSPENSION_INTERVAL_REFERENCE'):
        run(case)
