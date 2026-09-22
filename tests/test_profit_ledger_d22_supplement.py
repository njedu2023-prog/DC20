from tests.test_profit_ledger_monthly_frontend import run


def test_d22_missing_slots_show_identity_without_fabricating_trades():
    extra = '''
const source=[1,2].map(slot=>[{signal_date:'20260922',slot,status:'MISSING_D_PUBLICATION'}]);
const before=JSON.stringify(source);
const executableProfitExpect=(ok,msg)=>{if(!ok)throw Error(msg)};
const validatedThreeRankContract=w=>w.three_rank;
const loadPublishedDailyEntry=async()=>({wrapper:{three_rank:{rows:[
 {ts_code:'000910.SZ',name:'大亚圣象',industry:'家居用品',promotion_rank:6,stage_transition:'3→4'},
 {ts_code:'002303.SZ',name:'美盈森',industry:'包装印刷',promotion_rank:7,stage_transition:'2→3'}]}}});
const candidateUnifiedProfitView=()=>({ready:true,supplement:true,rows:new Map([
 ['000910.SZ',{executable_profit_research_rank:1}],['002303.SZ',{executable_profit_research_rank:2}]])});
'''
    result = run('''await (async()=>{
const rows=await ledgerJoinDay('20260922',source,[{signal_date:'20260922'}]);
return {rows,unchanged:before===JSON.stringify(source),performance:ledgerPerformance(rows)};
})()''', extra, names=('ledgerStatus','ledgerPerformance','ledgerJoinDay'))
    assert result['unchanged']
    assert [r['ts_code'] for r in result['rows']] == ['000910.SZ', '002303.SZ']
    assert [r['promotion_rank'] for r in result['rows']] == [6, 7]
    for r in result['rows']:
        assert r['status'] == 'POSTHOC_SUPPLEMENT_NOT_FORMAL'
        assert r['formal_status'] == 'MISSING_D_PUBLICATION'
        assert '2026-09-23本地补算' in r['supplement_note']
        assert all(r[k] is None for k in ('entry_price','exit_price','slot_net_return','exec_date'))
    assert result['performance']['settled'] == 0
    assert result['performance']['mean'] is None
