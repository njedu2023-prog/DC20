"""Real frozen artifacts under isolated corruption; never creates a freeze."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import pytest
from scripts import candidate_profit_pages as m

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def case(tmp_path):
    root, site = tmp_path/'repo', tmp_path/'site'
    root.mkdir(); site.mkdir()
    index = json.loads((ROOT/(m.PREFIX+'index.json')).read_bytes())
    files = [m.CONFIG, m.PREFIX+'index.json', m.PREFIX+'summary.json']
    for entry in index['days']:
        d=entry['signal_date']
        files += [entry['path'], f'outputs/decision/three_rank_top10_{d}.json',
                  f'work/profit_1000_upgrade/candidate_natural_forward/day_{d}.json']
    for rel in files:
        dest=root/rel; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/rel,dest)
    for name in ('index.html','decision.html'):
        (site/name).write_text('verified P0 remains')
    (site/'revision.json').write_text(json.dumps({'signal_date':index['latest_signal_date'], 'head_sha':'a'*40}))
    return root, site, index


def test_healthy_delivery_preserves_every_formal_byte(case):
    root, site, index = case
    result=m.materialize_delivery(root,site)
    assert result['summary_ready'] is True
    for rel in [m.CONFIG,m.PREFIX+'index.json',m.PREFIX+'summary.json']+[e['path'] for e in index['days']]:
        assert (site/rel).read_bytes()==(root/rel).read_bytes()
    assert m.verify_public(site,lambda rel:(site/rel).read_bytes())['status']==index['status']


@pytest.mark.parametrize('fault',['old_day','summary','old_p0','old_snapshot','current_day','index','activation'])
def test_corruption_is_explicit_and_preserves_verified_p0(case,fault):
    root, site, index=case
    old,current=index['days'][0], index['days'][-1]
    paths={'old_day':old['path'],'summary':m.PREFIX+'summary.json',
        'old_p0':f"outputs/decision/three_rank_top10_{old['signal_date']}.json",
        'old_snapshot':f"work/profit_1000_upgrade/candidate_natural_forward/day_{old['signal_date']}.json",
        'current_day':current['path'],'index':m.PREFIX+'index.json','activation':m.CONFIG}
    path=root/paths[fault]; original=path.read_bytes(); path.write_bytes(b'{"corrupted":true}')
    # Snapshot damage is caught only during the explicitly isolated path; the
    # public view's ordinary consistency pass is not a new proof issuer.
    if fault=='old_snapshot': (root/(m.PREFIX+'summary.json')).write_bytes(b'{}')
    protected={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    result=m.materialize_delivery(root,site)
    assert result['summary_ready'] is False
    revision=json.loads((site/'revision.json').read_bytes())
    delivery=revision['candidate_profit_delivery']
    assert delivery['new_freeze_created'] is False
    assert 'candidate_profit_summary_url' not in revision
    assert not (site/(m.PREFIX+'summary.json')).exists()
    assert (site/'index.html').read_text()=='verified P0 remains'
    if fault in ('old_day','summary','old_p0','old_snapshot'):
        assert delivery['current_ranking_status']=='READY', result
        assert (site/current['path']).read_bytes()==(root/current['path']).read_bytes()
    else:
        assert delivery['current_ranking_status']=='UNAVAILABLE'
        assert not (site/current['path']).exists()
    m.verify_public(site,lambda rel:(site/rel).read_bytes())
    assert protected=={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_exact_public_bytes_still_required_when_history_unavailable(case):
    root,site,index=case
    (root/(m.PREFIX+'summary.json')).write_bytes(b'{}')
    m.materialize_delivery(root,site)
    latest=index['days'][-1]['path']
    with pytest.raises(ValueError,match='converged'):
        m.verify_public(site,lambda rel:b'old' if rel==latest else (site/rel).read_bytes())


def test_frontend_blocks_legacy_fallback_for_untrusted_activation():
    path=ROOT/'tests/test_candidate_profit_activation_frontend.py'
    spec=importlib.util.spec_from_file_location('isolation_js_fixture',path)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    if not fixture.NODE: pytest.skip('Node required')
    result=fixture.run('''
revision.candidate_profit_delivery={status:'UNAVAILABLE'};
await refreshCandidateProfitActivation();
console.log(JSON.stringify({status:state.candidateProfitActivation.status,applies:candidateProfitApplies('20260914'),message:state.candidateProfitActivation.message,calls}));
''')
    assert result['status']=='invalid' and result['applies'] is True
    assert result['calls']==[] and '晋级名单' in result['message']


def test_frontend_ready_day_with_unavailable_summary():
    path=ROOT/'tests/test_candidate_profit_activation_frontend.py'
    spec=importlib.util.spec_from_file_location('isolation_js_fixture',path)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    if not fixture.NODE: pytest.skip('Node required')
    result=fixture.run('''
revision.candidate_profit_delivery={status:'PARTIAL',summary_status:'UNAVAILABLE'};
await refreshCandidateProfitActivation();
const result=await loadCandidateProfitDay(input.wrapper);
await refreshCandidateProfitSummary();
console.log(JSON.stringify({day:result.status,summary:state.candidateProfitSummaryLoad.status,message:state.candidateProfitSummaryLoad.message,calls}));
''')
    assert result['day']=='ready' and result['summary']=='invalid'
    assert '当日名单' in result['message']
    assert m.PREFIX+'summary.json' not in result['calls']


def test_same_staging_recovers_after_historical_summary_is_restored(case):
    root,site,index=case
    path=root/(m.PREFIX+'summary.json'); original=path.read_bytes()
    path.write_bytes(b'{}')
    assert m.materialize_delivery(root,site)['summary_ready'] is False
    path.write_bytes(original)
    assert m.materialize_delivery(root,site)['summary_ready'] is True
    revision=json.loads((site/'revision.json').read_bytes())
    assert 'candidate_profit_delivery' not in revision
    assert m.verify_public(site,lambda rel:(site/rel).read_bytes())['status']==index['status']
