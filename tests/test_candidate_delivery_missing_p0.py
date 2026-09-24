"""Isolated fault injection. No real credentials, HTTP or dispatches."""
from copy import deepcopy
from datetime import datetime
import json
import pytest
from scripts import candidate_delivery_watchdog as m
from test_candidate_delivery_watchdog import Fake, NOW, raw, run


class Missing(Fake):
    def __init__(self):
        super().__init__()
        del self.files['outputs/decision/primary_d_receipt_20260922.json']
        del self.files['outputs/decision/three_rank_top10_20260922.json']
        self.sources = {'pred_commit': 'b'*40, 'market_commit': 'c'*40,
                        'source_ready_at': '2026-09-22T13:45:00+00:00'}
    def p0_sources(self, day, now):
        assert day == '20260922'
        return self.sources
    def request(self, path, body=None):
        if path == '/actions/workflows/'+m.P0+'/runs?per_page=50':
            return {'workflow_runs': self.busy}
        return super().request(path, body)


def test_missing_p0_dispatches_existing_pinned_entry_without_writes():
    f = Missing(); before = deepcopy(f.files)
    plan = m.inspect(f, NOW)
    assert plan['status'] == 'NEEDS_P0'
    assert plan['recovery_kind'] == 'CONTROLLED_DAILY_NOT_SCHEDULE_PROOF'
    assert m.act(f, plan, NOW)['status'] == 'DRY_RUN_WOULD_DISPATCH'
    assert not f.posts
    assert m.act(f, plan, NOW, execute=True)['status'] == 'DISPATCHED_NOT_COMPLETED'
    assert f.posts == [('/actions/workflows/'+m.P0+'/dispatches', {'ref': 'main', 'inputs': {
        'trade_date': '20260922', 'generation_mode': 'NATURAL', 'dry_run': False,
        'confirm_daily_generation': True, 'confirm_recovery': False,
        'pred_commit': 'b'*40, 'market_commit': 'c'*40}})]
    assert f.files == before


@pytest.mark.parametrize('suffix', ['three_rank_top10_20260922.csv', 'primary_d_runtime_features_20260922.csv'])
def test_partial_bundle_never_overwritten(suffix):
    f = Missing(); f.files['outputs/decision/'+suffix] = b'existing'
    with pytest.raises(ValueError, match='INCOMPLETE_P0'): m.inspect(f, NOW)
    assert not f.posts


@pytest.mark.parametrize('stamp', ['2026-09-22T23:30:00+08:00', '2026-09-23T00:01:00+08:00'])
def test_existing_controlled_window_not_extended(stamp):
    f = Missing()
    with pytest.raises(ValueError, match='OUTSIDE_CONTROLLED_WINDOW'):
        m.inspect(f, datetime.fromisoformat(stamp))
    assert not f.posts


def test_clock_rechecked_before_dispatch():
    f = Missing(); plan = m.inspect(f, NOW)
    with pytest.raises(ValueError, match='OUTSIDE_CONTROLLED_WINDOW'):
        m.act(f, plan, datetime.fromisoformat('2026-09-22T23:30:00+08:00'), execute=True)
    assert not f.posts


def test_old_failed_p0_does_not_exhaust_new_ready_sources():
    f = Missing()
    f.busy = [{**run(m.P0), 'event': 'schedule', 'conclusion': 'failure'} for _ in range(3)]
    assert m.act(f, m.inspect(f, NOW), NOW, execute=True)['status'] == 'DISPATCHED_NOT_COMPLETED'


def test_new_controlled_failures_are_bounded():
    f = Missing()
    f.busy = [{**run(m.P0), 'conclusion': 'failure', 'created_at': '2026-09-22T13:50:00Z',
               'display_title': 'DC20 controlled daily NATURAL | D=20260922'} for _ in range(3)]
    with pytest.raises(ValueError, match='RETRY_BUDGET'): m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert not f.posts


def test_p0_in_progress_never_duplicated():
    f = Missing(); f.busy = [{**run(m.P0), 'status': 'in_progress', 'conclusion': None}]
    assert m.act(f, m.inspect(f, NOW), NOW, execute=True)['status'] == 'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'
    assert not f.posts


def test_after_midnight_generating_run_can_recover_downstream():
    f = Fake(); original = f.request
    def request(path, body=None):
        result = original(path, body)
        if path.startswith('/actions/workflows/'+m.P0+'/runs?status=success'):
            result['workflow_runs'][0]['created_at'] = '2026-09-22T17:51:00Z'
        return result
    f.request = request
    plan = m.inspect(f, datetime.fromisoformat('2026-09-23T02:00:00+08:00'))
    assert plan['workflow'] == m.NATURAL and plan['inputs']['p0_run_id'] == '7'


class Sources(m.GitHub):
    def __init__(self):
        super().__init__('fake-never-sent')
        self.reads = []
        self.pred = b'trade_date,generated_at_utc\n20260922,2026-09-22T12:00:00Z\n'
        self.meta = {'resolved_trade_date': '20260922', 'generated_at_bj': '2026-09-22 20:00:00'}
        self.names = ['daily.csv', 'daily_basic.csv', 'stk_limit.csv', 'stock_basic.csv', 'limit_list_d.csv', '_meta.json']
        self.commit_time = '2026-09-22T12:05:00Z'
    def request(self, path, body=None, *, repository=m.REPO):
        assert body is None
        assert repository in m.UPSTREAMS
        self.reads.append((repository, path))
        if path == '/commits/main':
            return {'sha': ('b' if repository == m.UPSTREAMS[0] else 'c')*40,
                    'commit': {'committer': {'date': self.commit_time}}}
        assert path == '/contents/data/raw/2026/20260922/?ref='+'c'*40
        return [{'name': name, 'type': 'file', 'size': 10} for name in self.names]
    def file(self, path, head, *, repository=m.REPO):
        self.reads.append((repository, path, head))
        assert head == ('b' if repository == m.UPSTREAMS[0] else 'c')*40
        if repository == m.UPSTREAMS[0]:
            assert path == 'outputs/decisio/pred_decisio_20260922.csv'
            return self.pred
        assert path == 'data/raw/2026/20260922/_meta.json'
        return raw(self.meta)


def test_source_preflight_binds_both_exact_heads_and_dates():
    f = Sources(); sources = f.p0_sources('20260922', NOW)
    assert sources['pred_commit'] == 'b'*40 and sources['market_commit'] == 'c'*40
    assert all('latest' not in str(item) for item in f.reads)


@pytest.mark.parametrize('change,reason', [
    ('missing_pred', 'PRED_NOT_READY'), ('old_pred', 'PRED_DATE_MISMATCH'),
    ('missing_market', 'MARKET_FILES_MISSING'), ('old_market', 'MARKET_DATE_MISMATCH'),
    ('future_commit', 'COMMIT_FROM_FUTURE'), ('future_pred', 'SOURCE_TIME_INVALID'),
    ('before_close', 'SOURCE_TIME_INVALID')])
def test_unready_sources_rejected_without_dispatch(change, reason):
    f = Sources()
    if change == 'missing_pred': f.pred = None
    if change == 'old_pred': f.pred = f.pred.replace(b'20260922', b'20260921')
    if change == 'missing_market': f.names.remove('daily_basic.csv')
    if change == 'old_market': f.meta['resolved_trade_date'] = '20260921'
    if change == 'future_commit': f.commit_time = '2026-09-22T15:00:00Z'
    if change == 'future_pred': f.pred = f.pred.replace(b'T12:00:', b'T15:00:')
    if change == 'before_close': f.meta['generated_at_bj'] = '2026-09-22 15:00:00'
    with pytest.raises(ValueError, match=reason): f.p0_sources('20260922', NOW)


def test_real_client_write_allowlist_still_rejects_upstream_mutation():
    client = m.GitHub('fake-never-sent')
    with pytest.raises(ValueError, match='DISPATCH_TARGET_REJECTED'):
        client.request('/actions/workflows/'+m.P0+'/dispatches', {'ref': 'main'}, repository=m.UPSTREAMS[0])


def test_existing_entry_contract_and_schedule_remain_unchanged():
    from pathlib import Path
    import yaml
    text = (Path(__file__).resolve().parents[1]/'.github/workflows'/m.P0).read_text()
    workflow = yaml.safe_load(text)
    plan = m.inspect(Missing(), NOW)
    assert set(plan['inputs']) == set(workflow[True]['workflow_dispatch']['inputs'])
    assert workflow[True]['schedule'] == [{'cron': f'15 {hour} * * {day}'}
                                        for day in range(1, 6) for hour in (13, 14, 15, 16)]
    assert 'not time(15, 0) < bj.time() < time(23, 30)' in text
    assert 'controlled daily P0 requires two immutable upstream commits' in text
    assert 'partial immutable P0 D bundle exists' in text
