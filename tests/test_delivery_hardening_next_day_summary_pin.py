"""The formal summary follows the reviewed statistics version, not new rules."""
from pathlib import Path
import hashlib
import json
from tests.test_suspension_pin_source_review import source_before_suspension_pin

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261108_next_day_summary_pin.json'
REVIEW_SHA = '4db67f1e1736348a8abc096389782d90f3c1129539c25476ee432958cf7196d0'
SOURCE = 'src/top10decision/decision/candidate_formal_shadow_summary.py'
OLD_PIN = '52f5b56da10393b44cc28fdfd3cd1628dda8f6b161a55205becd868e1a62f92c'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def review():
    raw = (ROOT / REVIEW_PATH).read_bytes()
    assert sha(raw) == REVIEW_SHA
    value = json.loads(raw)
    assert value['schema_version'] == 'dc20_next_day_summary_pin_source_review_v1'
    assert value['approved_base_commit'] == 'b0e005dd9682ef1e741aa0908a0e946b7cc05405'
    assert value['boundaries'] and all(v is False for v in value['boundaries'].values())
    assert len(value['source_changes']) == 1
    assert value['source_changes'][0]['path'] == SOURCE
    return value


def test_only_exact_statistics_pin_changed_and_baseline_is_recoverable():
    item = review()['source_changes'][0]
    raw = source_before_suspension_pin(SOURCE, (ROOT / SOURCE).read_bytes())
    assert len(raw) == item['current_bytes'] and sha(raw) == item['current_sha256']
    statistics_path = 'work/profit_1000_upgrade/candidate_natural_statistics.py'
    new_pin = sha(source_before_suspension_pin(statistics_path, (ROOT / statistics_path).read_bytes()))
    restored = raw.replace(new_pin.encode(), OLD_PIN.encode())
    assert raw.count(new_pin.encode()) == 1 and OLD_PIN.encode() not in raw
    assert len(restored) == item['baseline_bytes'] and sha(restored) == item['baseline_sha256']
    assert len(item['inverse_changes']) == 1
    change = item['inverse_changes'][0]
    assert change['baseline_start'] == change['current_start']
    assert change['current_lines'] == [f"STATISTICS_SHA = '{new_pin}'\n"]
    assert change['baseline_lines'] == [f"STATISTICS_SHA = '{OLD_PIN}'\n"]


def test_all_previous_source_reviews_models_and_trading_policy_remain_unchanged():
    for item in review()['preserved_files']:
        target = ROOT / item['path']
        assert not target.is_symlink()
        raw = source_before_suspension_pin(item['path'], target.read_bytes())
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']


def test_formal_summary_executes_full_statistics_and_evidence_guard_chain():
    from top10decision.decision import candidate_formal_shadow_summary as summary
    from work.profit_1000_upgrade import candidate_natural_settlement_workflow as settlement
    summary._guard()
    summary.publication._dependencies()
    summary.statistics._publication_type()
    settlement.code_guard(settlement.dependencies())
