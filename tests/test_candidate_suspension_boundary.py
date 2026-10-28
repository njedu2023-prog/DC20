"""Boundary witnesses remain separate from the 21 feature daily tables."""
from copy import deepcopy
import csv
import io

import pytest

from work.profit_1000_upgrade.test_candidate_d_source_adapter import (
    a, make_case, run, write, json_bytes, csv_bytes, update_receipt,
)


def boundary_case(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    inputs = case['receipt']['inputs']
    dates = inputs['calendar']['runtime_context_dates']
    code = case['runtime'][0]['ts_code']
    previous, start, resume = '20260814', dates[0], dates[1]
    files = []
    for d in [previous, start, resume]:
        path = f'data/market/raw/{d[:4]}/{d}/daily.csv'
        if d == previous:
            rows = [{'trade_date': d, 'ts_code': code, 'close': '10', 'pre_close': '9', 'vol': '100'}]
        else:
            rows = list(csv.DictReader(io.StringIO((case['root']/path).read_text())))
            if d == start:
                rows = [r for r in rows if r['ts_code'] != code]
            else:
                next(r for r in rows if r['ts_code'] == code)['pre_close'] = '10'
        body = csv_bytes(rows, rows[0])
        sha = write(case['root']/path, body)
        record = {'path': path, 'sha256': sha, 'git_blob_sha1': a._git_blob(body),
                  'git_mode': '100644', 'trade_date': d, 'table': 'daily'}
        for collection in [inputs['runtime_consumed_market_files'], inputs['committed_history_context']['files']]:
            for item in collection:
                if item.get('path') == path:
                    item.update(record)
        files.append(record)
    interval = {'ts_code': code, 'start_date': start, 'resume_date': resume,
                'previous_trading_date': previous, 'skipped_sessions': [start]}
    payload = {'schema_version': 'dc20_confirmed_suspensions_v1', 'suspensions': [{
        'ts_code': code, 'start_date': start, 'resume_date': resume,
        'sources': [{'url': 'https://example.invalid/fixture.pdf', 'published_date': start, 'pdf_sha256': 'a'*64}]}]}
    body = json_bytes(payload)
    disclosure = {'path': a.SUSPENSION_PATH, 'sha256': write(case['root']/a.SUSPENSION_PATH, body),
                  'git_blob_sha1': a._git_blob(body), 'git_mode': '100644',
                  'table': 'confirmed_suspensions', 'used_intervals': [interval]}
    boundary = {'schema_version': a.BOUNDARY_SCHEMA, 'table': a.BOUNDARY_TABLE,
        'start_date': start, 'resume_date': resume, 'as_of_date': resume, 'git_head': 'b'*40,
        'feature_window_extended': False, 'files': files,
        'calendar': {'path': a.CALENDAR_PATH, 'sha256': inputs['calendar']['sha256'],
                     'git_blob_sha1': a._git_blob((case['root']/a.CALENDAR_PATH).read_bytes()), 'git_mode': '100644'}}
    inputs['committed_history_context']['git_head'] = 'b'*40
    inputs['runtime_consumed_market_files'].extend([disclosure, boundary])
    update_receipt(case)
    return case, boundary, disclosure, payload


def test_boundary_witness_does_not_add_feature_bar_or_authority(tmp_path, monkeypatch):
    case, boundary, _, _ = boundary_case(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in case['root'].rglob('*') if p.is_file()}
    result = run(case)
    assert result['registered_daily_file_count'] == 21
    assert result['status'] == 'BLOCKED_DAILY_FEATURE_HISTORY'
    assert result['projection']['diagnostics'][0]['observed_bar_count'] == 20
    assert result['git_membership_verified'] is False
    assert result['files_written'] == result['network_calls_performed'] == 0
    assert any(x['receipt_path'] == boundary['files'][0]['path'] for x in result['source_file_bindings'])
    assert before == {p: p.read_bytes() for p in case['root'].rglob('*') if p.is_file()}


@pytest.mark.parametrize('mutation', [
    'missing_file', 'raw_hash', 'blob', 'mode', 'head', 'future_file', 'future_asof',
    'future_disclosure', 'extra_file', 'missing_interval', 'duplicate', 'missing_boundary',
    'traded', 'price', 'missing_price_witness', 'wrong_file_date', 'unbound_calendar',
    'missing_session', 'feature_window', 'unknown_schema',
])
def test_boundary_receipt_rejects_unbound_or_false_evidence(tmp_path, monkeypatch, mutation):
    case, boundary, disclosure, payload = boundary_case(tmp_path, monkeypatch)
    files = boundary['files']
    first = case['root']/files[0]['path']
    if mutation == 'missing_file': first.unlink()
    elif mutation == 'raw_hash': first.write_bytes(first.read_bytes()+b'\n')
    elif mutation == 'blob': files[0]['git_blob_sha1'] = '0'*40
    elif mutation == 'mode': files[0]['git_mode'] = '120000'
    elif mutation == 'head': boundary['git_head'] = 'c'*40
    elif mutation == 'future_file': files[0]['trade_date'] = '20990101'
    elif mutation == 'future_asof': boundary['as_of_date'] = '20990101'
    elif mutation == 'extra_file': files.append(deepcopy(files[-1]))
    elif mutation == 'missing_interval': disclosure['used_intervals'] = []
    elif mutation == 'duplicate': case['receipt']['inputs']['runtime_consumed_market_files'].append(deepcopy(boundary))
    elif mutation == 'missing_boundary': case['receipt']['inputs']['runtime_consumed_market_files'].remove(boundary)
    elif mutation == 'unbound_calendar': boundary['calendar']['git_blob_sha1'] = '0'*40
    elif mutation == 'missing_session': del files[1]
    elif mutation == 'feature_window': boundary['feature_window_extended'] = True
    elif mutation == 'unknown_schema': boundary['schema_version'] = 'unknown'
    elif mutation == 'future_disclosure':
        payload['suspensions'][0]['sources'][0]['published_date'] = case['day']
        body = json_bytes(payload)
        disclosure.update(sha256=write(case['root']/a.SUSPENSION_PATH, body), git_blob_sha1=a._git_blob(body))
    else:
        target = files[1] if mutation == 'traded' else files[0]
        path = case['root']/target['path']
        rows = list(csv.DictReader(io.StringIO(path.read_text())))
        if mutation == 'traded':
            rows.append({**rows[0], 'ts_code': case['runtime'][0]['ts_code']})
        elif mutation == 'price': rows[0]['close'] = '9'
        elif mutation == 'missing_price_witness': rows[0]['ts_code'] = '600999.SH'
        elif mutation == 'wrong_file_date': rows[0]['trade_date'] = case['day']
        body = csv_bytes(rows, rows[0])
        target.update(sha256=write(path, body), git_blob_sha1=a._git_blob(body))
        for collection in [case['receipt']['inputs']['runtime_consumed_market_files'], case['receipt']['inputs']['committed_history_context']['files']]:
            for item in collection:
                if item.get('path') == target['path']: item.update(target)
    update_receipt(case)
    with pytest.raises(ValueError):
        run(case)
