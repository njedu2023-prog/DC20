"""Read-only monthly ledger: frozen joins, independent denominators and export."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'decision.html').read_text()
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='Node required')


def run(expression, extra='', names=('ledgerStatus', 'ledgerPerformance', 'ledgerFilter', 'ledgerCsv')):
    source = '\n'.join(re.search(rf'^    (?:async )?function {name}\(.*?^    }}', HTML, re.M | re.S).group() for name in names)
    result = subprocess.run([NODE, '-'], input=source+'\n'+extra+'\n(async()=>{console.log(JSON.stringify('+expression+'))})().catch(e=>{console.error(e);process.exit(1)})',
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def row(status='SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY', net=.1, **kw):
    return dict(signal_date='20260930', slot=1, ts_code='000001.SZ', name='甲', status=status, slot_net_return=net, **kw)


def test_performance_excludes_unknown_and_no_fill_from_trade_denominator():
    rows = [row(), row(net=-.05), row('NO_FILL_CAPACITY', 0), row('PENDING_T', None),
            row('PENDING_LIMIT_UP_HOLD', None), row('MISSING_OUTCOME_LEDGER', None)]
    p = run('ledgerPerformance('+json.dumps(rows)+')')
    assert p == dict(count=6, settled=2, wins=1, noFill=1, holding=1, pending=1, missing=1, winRate=.5, mean=.025)


def test_empty_and_unsettled_are_null_not_zero():
    for rows in ([], [row('PENDING_T1', None)], [row('NO_FILL_CAPACITY', 0)]):
        p = run('ledgerPerformance('+json.dumps(rows)+')')
        assert p['winRate'] is None and p['mean'] is None


def test_month_uses_signal_date_not_cross_month_exit():
    rows = [row(actual_exit_date='20261008'), dict(row(), signal_date='20261001')]
    assert len(run('ledgerFilter('+json.dumps(rows)+',"202609","","","")')) == 1


def test_filters_and_repeated_stock_keep_both_slots():
    rows = [row(), dict(row(), slot=2)]
    assert len(run('ledgerFilter('+json.dumps(rows)+',"202609","","","000001")')) == 2
    assert run('ledgerFilter('+json.dumps(rows)+',"202609","2","settled","甲")')[0]['slot'] == 2
    assert run('ledgerFilter('+json.dumps(rows)+',"202609","","pending","")') == []


@pytest.mark.parametrize('name', ['=SUM(A1)', '+cmd', '-cmd', '@cmd', '\tcmd'])
def test_csv_safe_text_and_no_fill_not_zero_return(name):
    rows = [dict(row('NO_FILL_CAPACITY', 0), name=name), row(net=-.12)]
    csv = run('ledgerCsv('+json.dumps(rows)+')')
    assert csv.startswith('\ufeff') and "'"+name in csv
    assert '"-0.12"' in csv
    assert '"0"' not in csv.split('\r\n')[1]


def test_join_requires_identity_and_uses_frozen_not_latest_features():
    extra = '''
const day={snapshot_file_sha256:'snap',exec_date:'20260917',exit_date:'20260918',candidate_slots:[1,2].map(slot=>({ts_code:'code'+slot,candidate_rank:slot,candidate_score:0,promotion_rank:3-slot})),rows:[1,2].map(slot=>({ts_code:'code'+slot,name:'frozen'+slot,industry:'industry'+slot}))};
const sequences=[1,2].map(slot=>[{signal_date:'20260916',slot,ts_code:'code'+slot,candidate_rank:slot,candidate_score:0,promotion_rank:3-slot,exec_date:'20260917',exit_date:'20260918',formal_projection_sha256:'hash',snapshot_file_sha256:'snap',status:'PENDING_T1'}]);
const navigation=[{signal_date:'20260916'}];
async function loadPublishedDailyEntry(){return {wrapper:{three_rank:{rows:[1,2].map(slot=>({ts_code:'code'+slot,stage_transition:'2→3'}))}},runtimeIndex:{}}}
async function loadCandidateProfitDay(){return {projection:day}}
async function loadCurrentThreeRankPathEvidence(){return {rows:[1,2].map(slot=>({ts_code:'code'+slot,path_label:'冻结路径'}))}}
async function sha256Hex(){return 'hash'}
function canonicalJson(x){return JSON.stringify(x)}
const state={candidateProfitActivation:{index:{days:[{signal_date:'20260916',path:'day',sha256:'sha'}]}}};
async function fetchPagesOnlyShaBoundJson(){return {bytes:new TextEncoder().encode(JSON.stringify(day))}}
'''
    names = ('ledgerJoinDay', 'ledgerCanonicalSource', 'executableProfitExpect')
    joined = run("await ledgerJoinDay('20260916',sequences,navigation)", extra, names)
    assert [r['name'] for r in joined] == ['frozen1', 'frozen2']
    assert all(r['path_label'] == '冻结路径' for r in joined)
    rejected = run("await ledgerJoinDay('20260916',sequences,navigation).then(()=>false,()=>true)", extra+"sequences[0][0].ts_code='wrong';", names)
    assert rejected


@pytest.mark.parametrize('score', [0.0, -1e-7, 1e-6, 1e-12, .3333333333333333])
def test_ledger_canonical_preserves_python_numbers_and_unicode(score):
    payload = dict(name='中文', score=score, rows=[None, True, dict(text='换行\n引号"')])
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    result = run('ledgerCanonicalSource(new TextEncoder().encode('+json.dumps(raw)+'))', names=('ledgerCanonicalSource','executableProfitExpect'))
    assert result == json.dumps(payload, sort_keys=True, separators=(',',':'))


def test_route_does_not_bootstrap_homepage_and_month_load_is_bounded():
    assert 'if (new URLSearchParams(location.search).get("view") === "profit-ledger")' in HTML
    assert '} else { initialize(false).catch(showError); }' in HTML
    block = re.search(r'^    async function initializeProfitLedger\(.*?^    }', HTML, re.M | re.S).group()
    assert 'offset+=4' in block and 'version!==serial' in block
    assert 'ledgerCsv(currentRows)' in block  # export is full month, not filtered subset
    assert 'refreshPublicObservationStatistics' not in block
    assert '"代码","名称","行业","晋级","连板路径","晋级排序"' in block
    assert 'historicalResearch' not in block


def test_entire_browser_script_has_valid_syntax():
    script = HTML.split('<script>')[1].split('</script>')[0]
    subprocess.run([NODE,'-'],input='new Function('+json.dumps(script)+');',text=True,check=True)


def test_monthly_presentation_matches_compact_homepage_without_changing_data():
    assert '<div class="ledger-heading-group"><h2 id="compactLedgerTitle">' in HTML
    assert 'class="ledger-detail-link" href="?view=profit-ledger"' in HTML
    assert '.ledger-detail-link:visited' in HTML and 'text-decoration:none' in HTML
    assert 'toolbar ledger-titlebar' in HTML and 'title-icon" aria-hidden="true"' in HTML
    assert 'status-chip status-${status.kind}' in HTML
    assert 'class="promotion-chip"' in HTML and 'class="path-chip"' in HTML
