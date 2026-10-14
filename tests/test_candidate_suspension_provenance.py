"""Suspension provenance adds source evidence without inventing daily prices."""
from copy import deepcopy
import pytest
from work.profit_1000_upgrade.test_candidate_d_source_adapter import (
    a, make_case, run, write, json_bytes, update_receipt,
)


def suspension_case(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch, missing_bar=True)
    dates = case['receipt']['inputs']['calendar']['runtime_context_dates']
    interval = {'ts_code': case['runtime'][0]['ts_code'], 'start_date': dates[3],
        'resume_date': dates[4], 'previous_trading_date': dates[2], 'skipped_sessions': [dates[3]]}
    payload = {'schema_version': 'dc20_confirmed_suspensions_v1', 'suspensions': [{
        **{k: interval[k] for k in ('ts_code', 'start_date', 'resume_date')},
        'sources': [{'url': 'https://static.cninfo.com.cn/synthetic.pdf',
            'published_date': dates[3], 'pdf_sha256': 'a' * 64}]}]}
    body = json_bytes(payload)
    entry = {'path': a.SUSPENSION_PATH, 'table': 'confirmed_suspensions',
        'sha256': write(case['root'] / a.SUSPENSION_PATH, body),
        'git_blob_sha1': a._git_blob(body), 'git_mode': '100644', 'used_intervals': [interval]}
    case['receipt']['inputs']['runtime_consumed_market_files'].append(entry)
    update_receipt(case)
    return case, entry, payload


def test_registered_disclosure_is_bound_without_changing_missing_daily_state(tmp_path, monkeypatch):
    case, entry, payload = suspension_case(tmp_path, monkeypatch)
    result = run(case)
    assert result['status'] == 'BLOCKED_DAILY_FEATURE_HISTORY'
    assert result['registered_daily_file_count'] == 21
    assert result['projection']['diagnostics'][0]['observed_bar_count'] == 20
    bindings = [x for x in result['source_file_bindings'] if x['receipt_path'] == a.SUSPENSION_PATH]
    assert len(bindings) == 1 and bindings[0]['sha256'] == entry['sha256']


@pytest.mark.parametrize('mutation', ['blob', 'mode', 'duplicate', 'unknown_path', 'interval', 'future', 'missing_source', 'hash'])
def test_suspension_provenance_never_becomes_unbound_or_future(tmp_path, monkeypatch, mutation):
    case, entry, payload = suspension_case(tmp_path, monkeypatch)
    if mutation == 'blob': entry['git_blob_sha1'] = '0' * 40
    elif mutation == 'mode': entry['git_mode'] = '120000'
    elif mutation == 'duplicate': case['receipt']['inputs']['runtime_consumed_market_files'].append(deepcopy(entry))
    elif mutation == 'unknown_path': entry['path'] = 'data/market/latest/suspensions.json'
    elif mutation == 'interval': entry['used_intervals'][0]['skipped_sessions'] = []
    elif mutation in ('future', 'hash'):
        source = payload['suspensions'][0]['sources'][0]
        source['published_date' if mutation == 'future' else 'pdf_sha256'] = '20990101' if mutation == 'future' else 'bad'
        body = json_bytes(payload)
        entry['sha256'] = write(case['root'] / a.SUSPENSION_PATH, body)
        entry['git_blob_sha1'] = a._git_blob(body)
    else: (case['root'] / a.SUSPENSION_PATH).unlink()
    update_receipt(case)
    with pytest.raises(ValueError): run(case)
