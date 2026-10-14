"""Original frozen v1/v2 bytes remain readable; mixed provenance never does."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from work.profit_1000_upgrade import candidate_snapshot_versions as profiles

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SHA256 = {
    '20260914': '3bcea6f46702fc9b3c3d7a05af72b59ce58ff84636be1bb3ed49059ea30e6e6b',
    '20261013': '6bdcf8e971b1b6cc6714b2d3b9d2cea0e30ccab0b9f61d04ff4f304a8ab1f03c',
}


def original(day):
    return (ROOT / 'work/profit_1000_upgrade/candidate_natural_forward' / f'day_{day}.json').read_bytes()


@pytest.mark.parametrize('day', ['20260914', '20261013'])
def test_original_snapshot_bytes_and_old_profile_remain_unchanged(day):
    raw = original(day)
    before = hashlib.sha256(raw).hexdigest()
    assert before == ORIGINAL_SHA256[day]
    value = profiles.validate_snapshot(raw, before)
    runner = profiles.natural if day == '20260914' else profiles.eligible
    assert {key: value[key] for key in runner.HISTORICAL_SNAPSHOT_PROFILE} == runner.HISTORICAL_SNAPSHOT_PROFILE
    assert original(day) == raw


@pytest.mark.parametrize('day', ['20260914', '20261013'])
@pytest.mark.parametrize('field', ['runner_sha256', 'registration_sha256', 'dependency_sha256', 'unknown_runner', 'future_old'])
def test_complete_profile_cannot_be_mixed_or_redated(day, field):
    raw = original(day)
    value = json.loads(raw)
    runner = profiles.natural if day == '20260914' else profiles.eligible
    _, plan_sha, _ = runner.registration()
    current = {'runner_sha256': runner.SELF_SHA, 'registration_sha256': plan_sha, 'dependency_sha256': runner.PINS}
    if field in current:
        value[field] = deepcopy(current[field])
    elif field == 'future_old':
        value['signal_date'] = '20261014'
    else:
        value['runner_sha256'] = 'f' * 64
    value['snapshot_sha256'] = runner.scorer.canonical_sha({k: v for k, v in value.items() if k != 'snapshot_sha256'})
    mutated = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    with pytest.raises(ValueError, match='PROVENANCE_PROFILE'):
        profiles.validate_snapshot(mutated, hashlib.sha256(mutated).hexdigest())


@pytest.mark.parametrize('day', ['20260914', '20261013'])
def test_history_profile_does_not_exempt_current_dependency_guard(day, monkeypatch):
    raw = original(day)
    source = profiles.natural.source
    original_read = source._read
    def changed(path):
        body, identity = original_read(path)
        if Path(path) == source.CODE_ROOT / 'scripts/publish_primary_three_rank.py':
            body += b'\n# synthetic unreviewed drift\n'
        return body, identity
    monkeypatch.setattr(source, '_read', changed)
    with pytest.raises(ValueError, match='FROZEN_CODE_SHA_CHANGED'):
        profiles.validate_snapshot(raw, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('day', ['20260914', '20261013'])
def test_original_external_bytes_hash_is_still_required(day):
    raw = original(day)
    with pytest.raises(ValueError, match='EXTERNAL_FROZEN_SNAPSHOT_SHA_MISMATCH'):
        profiles.validate_snapshot(raw + b' ', hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('day', ['20260914', '20261013'])
def test_real_old_snapshot_continues_with_current_writer_without_price_imputation(day, tmp_path):
    from datetime import datetime, timezone
    from work.profit_1000_upgrade import candidate_natural_outcome_collect as collector
    raw = original(day)
    snapshot = ROOT / 'work/profit_1000_upgrade/candidate_natural_forward' / f'day_{day}.json'
    expected = hashlib.sha256(raw).hexdigest()
    calendar = ROOT / collector.labels.settlement.CALENDAR_PATH
    now = datetime.strptime(day, '%Y%m%d').replace(hour=23, tzinfo=timezone.utc)
    def no_request(contract):
        pytest.fail('No quote request is due on frozen signal day')
    collection = collector.collect_natural_outcome_sources(snapshot,
        tmp_path.resolve() / 'candidate_natural_outcome_sources',
        expected_snapshot_sha256=expected, calendar_path=calendar,
        expected_calendar_sha256=collector.labels.settlement.CALENDAR_SHA256,
        as_of_date=day, transport=no_request, clock=lambda: now,
        monotonic=lambda: 0.0, sleep=lambda _: None)
    assert collection['api_calls'] == 0
    receipt = collector.outcomes.evaluate_natural_outcomes(snapshot,
        tmp_path.resolve() / 'candidate_natural_outcomes',
        expected_snapshot_sha256=expected, as_of_date=day,
        source_bundle=collection['source_bundle'], clock=lambda: now)
    report = json.loads(Path(receipt['ledger_path']).read_bytes())['versions'][-1]
    assert report['registration_sha256'] == json.loads(raw)['registration_sha256']
    assert report['writer_sha256'] == collector.outcomes.SELF_SHA
    assert report['dependencies'] == collector.outcomes.PINS
    assert all(row['rows'][0]['slot_net_return'] is None for row in report['native_singleton_reports'].values())
    assert original(day) == raw
