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


def test_posthoc_monthly_identity_does_not_create_trades_or_change_source():
    extra = '''
const source=[1,2].map(slot=>[{signal_date:'20260921',slot,status:'MISSING_D_PUBLICATION'}]);
const before=JSON.stringify(source);
const executableProfitExpect=(ok,msg)=>{if(!ok)throw Error(msg)};
const validatedThreeRankContract=w=>w.three_rank;
const loadPublishedDailyEntry=async()=>({wrapper:{three_rank:{rows:[
 {ts_code:'000910.SZ',name:'大亚圣象',industry:'家居用品',promotion_rank:4,stage_transition:'2→3'},
 {ts_code:'002589.SZ',name:'瑞康医药',industry:'医药商业',promotion_rank:6,stage_transition:'2→3'}]}}});
let enabled=true;
const candidateUnifiedProfitView=()=>({ready:enabled,supplement:enabled,rows:new Map([
 ['000910.SZ',{executable_profit_research_rank:1}],['002589.SZ',{executable_profit_research_rank:2}]])});
'''
    result = run('''await (async()=>{
const rows=await ledgerJoinDay('20260921',source,[{signal_date:'20260921'}]);
enabled=false;
const rejected=await ledgerJoinDay('20260921',source,[{signal_date:'20260921'}]);
return {rows,rejected,unchanged:before===JSON.stringify(source),performance:ledgerPerformance(rows),csv:ledgerCsv(rows)};
})()''', extra, names=('ledgerStatus','ledgerPerformance','ledgerCsv','ledgerJoinDay'))
    assert result['unchanged']
    assert [r['ts_code'] for r in result['rows']] == ['000910.SZ','002589.SZ']
    assert [r['promotion_rank'] for r in result['rows']] == [4,6]
    for r in result['rows']:
        assert r['status'] == 'POSTHOC_SUPPLEMENT_NOT_FORMAL'
        assert r['formal_status'] == 'MISSING_D_PUBLICATION'
        assert all(r[k] is None for k in ('entry_price','exit_price','slot_net_return','exec_date'))
    assert result['performance']['settled'] == 0
    assert result['performance']['mean'] is None
    assert all('ts_code' not in r for r in result['rejected'])
    assert '事后补算' in result['csv']


def path_view(**changes):
    value = dict(path_evidence_verified=True, stage_transition='2→3', path_days_observed=2,
                 path_data_coverage=1, path_strength_latest=.8, path_strength_delta=.3,
                 path_label_code='WEAK_TO_STRONG', path_label='弱转强', path_explanation='历史观测',
                 path_gap_slope=.02, path_first_seal_slope=-30, path_open_times_slope=-1,
                 path_seal_ratio_slope=.05, path_turnover_slope=1, path_amount_log_slope=.2)
    value.update(changes)
    return run('verifiedPathDisplay('+json.dumps(value)+')',
               extra='const PRIMARY_PATH_LABELS={WEAK_TO_STRONG:"弱转强",STABLE_STRONG:"持续强势",INSUFFICIENT:"路径数据不足"};',
               names=('verifiedPathDisplay',))


def test_path_complete_is_description_not_prediction_probability():
    result = path_view()
    assert result['status'] == 'MULTIDIMENSION_DESCRIPTION'
    assert result['label'] == '多维增强'
    assert result['change_text'] == '增强4 / 减弱0'
    assert result['delta'] == .3
    assert '50.00 → 80.00' in result['explanation']
    assert '不是预测准确率' in result['explanation']
    assert '未改变模型输入' in result['explanation']


@pytest.mark.parametrize('change', [dict(path_evidence_verified=False), dict(path_data_coverage=.99),
    dict(path_data_coverage=.35), dict(path_days_observed=1), dict(stage_transition='3→4'),
    dict(path_strength_delta=None), dict(path_strength_delta=''), dict(path_strength_latest=2),
    dict(path_strength_latest=.1,path_strength_delta=.8), dict(path_label='加速一致')])
def test_path_missing_or_incomparable_never_shows_direction(change):
    result = path_view(**change)
    assert result['delta'] is None
    assert result['label'] in ('待核验','数据不足')


@pytest.mark.parametrize('value,expected', [(None,'—'),(.2,'+20.00分 ↑'),(-.1,'-10.00分 ↓'),(0,'0.00分')])
def test_path_change_is_points_not_percentage(value, expected):
    assert run('pathChangePoints('+json.dumps(value)+')',names=('pathChangePoints',)) == expected


def test_path_zero_is_observed_not_missing_and_boundaries_are_explained():
    result = path_view(path_strength_delta=0,path_strength_latest=.65,path_label_code='STABLE_STRONG',path_label='持续强势',
                       path_gap_slope=0,path_first_seal_slope=0,path_open_times_slope=0,path_seal_ratio_slope=0)
    assert result['delta'] == 0
    assert result['label'] == '变化有限'
    assert result['evidence_count'] == 4
    assert result['change_text'] == '增强0 / 减弱0'


@pytest.mark.parametrize('key', ['path_gap_slope','path_first_seal_slope','path_open_times_slope','path_seal_ratio_slope'])
@pytest.mark.parametrize('value', [None, '', ' ', 'NaN', 'Infinity'])
def test_multidimensional_missing_never_becomes_zero_or_old_label(key, value):
    result = path_view(**{key:value})
    assert result['label'] == '数据不足'
    assert result['delta'] is None


def test_conflict_cannot_be_overridden_by_positive_score_or_three_positive_votes():
    result = path_view(path_open_times_slope=1)
    assert result['label'] == '多维分化'
    assert result['change_text'] == '增强3 / 减弱1'
    assert result['delta'] > 0
    assert '炸板次数增加' in result['explanation']


def test_reversed_dimensions_and_volume_not_automatic_support():
    result = path_view(path_gap_slope=-.02,path_first_seal_slope=30,path_open_times_slope=1,path_seal_ratio_slope=-.05,
                       path_turnover_slope=100,path_amount_log_slope=10)
    assert result['label'] == '多维减弱'
    assert '放量不自动等于承接强' in result['explanation']


def test_threshold_instability_abstains_and_reports_reason():
    result = path_view(path_gap_slope=.005,path_first_seal_slope=-10,path_open_times_slope=0,path_seal_ratio_slope=0)
    assert result['label'] == '方向待判'
    assert result['threshold_stable'] is False
    assert result['change_text'] == '边界待判'


def test_three_boards_use_first_last_span_not_adjacent_score_interval():
    result = path_view(stage_transition='3→4',path_days_observed=3,path_gap_slope=.01,path_open_times_slope=-.5)
    assert '开盘缺口抬升（首末差 +2.00个百分点）' in result['explanation']
    assert '炸板次数减少（首末差 -1.00次）' in result['explanation']
    assert '不代表中间一天同向' in result['explanation']
    assert '相邻两板旧规则分' in result['explanation']


@pytest.mark.parametrize('change', [dict(path_first_seal_slope=336),dict(path_open_times_slope=.3)])
def test_impossible_clock_span_or_fractional_open_count_rejected(change):
    assert path_view(**change)['status'] == 'INVALID'


def test_optional_volume_missing_does_not_claim_participation_strength():
    result = path_view(path_turnover_slope=None)
    assert result['label'] == '多维增强'
    assert '量能辅助证据不足' in result['explanation']


def test_path_display_gates_main_and_monthly_without_mutating_inputs():
    assert 'path_display: verifiedPathDisplay(runtimePath)' in HTML
    assert 'verifiedPathDisplay(path.rows.find' in HTML
    assert 'escapeHtml(row.path_display.change_text || "—")' in HTML
    assert 'path_evidence_verified: true' in HTML


@pytest.mark.parametrize('changes,code,tone', [
    ({}, 'UP', 'path-up'),
    (dict(path_gap_slope=-.02,path_first_seal_slope=30,path_open_times_slope=1,path_seal_ratio_slope=-.05), 'DOWN', 'path-down'),
    (dict(path_open_times_slope=1), 'MIXED', 'pending'),
    (dict(path_gap_slope=0,path_first_seal_slope=0,path_open_times_slope=0,path_seal_ratio_slope=0), 'LIMITED', 'path-neutral'),
    (dict(path_gap_slope=.005,path_first_seal_slope=-10,path_open_times_slope=0,path_seal_ratio_slope=0), 'BOUNDARY', 'pending'),
    (dict(path_evidence_verified=False), 'INSUFFICIENT', 'pending'),
])
def test_path_display_direction_controls_badge_without_false_certainty(changes,code,tone):
    result = path_view(**changes)
    assert result['code'] == code
    assert run('pathClass('+json.dumps(result['code'])+')', names=('pathClass',)) == tone
    if tone.startswith('path-'):
        assert '.truth-badge.'+tone in HTML


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
const PRIMARY_PATH_LABELS={WEAK_TO_STRONG:'弱转强'};
async function loadCurrentThreeRankPathEvidence(){return {rows:[1,2].map(slot=>({ts_code:'code'+slot,path_label:'弱转强',path_label_code:'WEAK_TO_STRONG',path_evidence_verified:true,stage_transition:'2→3',path_days_observed:2,path_data_coverage:1,path_strength_latest:.8,path_strength_delta:.3}))}}
async function sha256Hex(){return 'hash'}
function canonicalJson(x){return JSON.stringify(x)}
const state={candidateProfitActivation:{index:{days:[{signal_date:'20260916',path:'day',sha256:'sha'}]}}};
async function fetchPagesOnlyShaBoundJson(){return {bytes:new TextEncoder().encode(JSON.stringify(day))}}
'''
    names = ('ledgerJoinDay', 'ledgerCanonicalSource', 'executableProfitExpect', 'verifiedPathDisplay')
    joined = run("await ledgerJoinDay('20260916',sequences,navigation)", extra, names)
    assert [r['name'] for r in joined] == ['frozen1', 'frozen2']
    assert all(r['path_label'] == '数据不足' for r in joined)  # old schema cannot silently masquerade as new evidence
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
    assert 'href="?view=profit-ledger" target="_blank" rel="noopener noreferrer"' in HTML
    assert '.ledger-detail-link:visited' in HTML and 'text-decoration:none' in HTML
    assert 'toolbar ledger-titlebar' in HTML and 'title-icon" aria-hidden="true"' in HTML
    assert 'status-chip status-${status.kind}' in HTML
    assert 'class="promotion-chip"' in HTML and 'class="path-chip"' in HTML
