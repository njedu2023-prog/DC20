"""Version admission with synthetic sources; no natural-publication claims."""
import json
from pathlib import Path
import pytest
from work.profit_1000_upgrade import candidate_snapshot_versions as v
from work.profit_1000_upgrade import candidate_natural_forward_test as fixtures


@pytest.mark.parametrize('day,count', [('20260914',10),('20260915',4),('20260916',9),('20260917',8),('20260918',9)])
def test_existing_natural_records_remain_readable_without_rewrite(day,count):
    path=Path(v.__file__).parent/'candidate_natural_forward'/f'day_{day}.json'
    raw=path.read_bytes()
    record=v.validate_snapshot(raw,v._sha(raw))
    assert record['clock_mode']=='HOST_SYSTEM_UTC'
    assert record['signal_date']==day
    assert len(v.outcome_rows(record))==count
    assert path.read_bytes()==raw


@pytest.mark.parametrize('version,missing', [('legacy',False),('eligible',False),('eligible',True)])
def test_exact_version_roundtrip_and_original_union(tmp_path,monkeypatch,version,missing):
    runner=v.natural if version=='legacy' else v.eligible
    monkeypatch.setattr(fixtures,'m',runner)
    case=fixtures.setup_case(tmp_path,monkeypatch,size=10,missing_bar=missing)
    if version=='legacy':
        # Fixture registration binds a temporary evaluation path, not production.
        monkeypatch.setattr(v,'REGISTRATION_SHA',runner.registration()[1])
    receipt=fixtures.run(case)
    path=Path(receipt['snapshot_path']);raw=path.read_bytes()
    loaded=v.validate_snapshot(raw,receipt['snapshot_file_sha256'])
    assert loaded==json.loads(raw)
    assert path.read_bytes()==raw
    originals=v.outcome_rows(loaded)
    assert len(originals)==10
    assert sorted(row['promotion_rank'] for row in originals)==list(range(1,11))
    codes={s['ts_code'] for key in ('candidate_slots','promotion_slots') for s in loaded[key]}
    assert codes <= {r['ts_code'] for r in originals}
    if missing:
        assert loaded['promotion_slots'][0]['ts_code'] not in {r['ts_code'] for r in loaded['prediction']['rows']}
        assert loaded['promotion_slots'][0]['ts_code'] in codes


@pytest.mark.parametrize('mutation', ['unknown','downgrade','hash','slot','authority'])
def test_version_reader_never_falls_back_on_invalid_v2(tmp_path,monkeypatch,mutation):
    monkeypatch.setattr(fixtures,'m',v.eligible)
    case=fixtures.setup_case(tmp_path,monkeypatch,size=10,missing_bar=True)
    receipt=fixtures.run(case)
    value=json.loads(Path(receipt['snapshot_path']).read_bytes())
    if mutation=='unknown': value['schema_version']='unknown'
    if mutation=='downgrade': value['schema_version']=v.natural.SCHEMA
    if mutation=='slot': value['candidate_slots'][0]['ts_code']=value['promotion_slots'][0]['ts_code']
    if mutation=='authority': value['production_activation_allowed']=True
    value['snapshot_sha256']=v.natural.scorer.canonical_sha({k:x for k,x in value.items() if k!='snapshot_sha256'})
    raw=v.natural.storage.encoded(value)
    with pytest.raises(ValueError):
        v.validate_snapshot(raw,'0'*64 if mutation=='hash' else v._sha(raw))
