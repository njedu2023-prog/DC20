"""Prospective evidence contract. Missing is explicit; scores never become probabilities."""
import hashlib,json,re
from pathlib import Path
import lab,adaptive_weights as weights
VERSION='RESEARCH_FREEZE_V1'

def archived(c,ref):
    if not isinstance(ref,dict) or not isinstance(ref.get('artifact_id'),int):raise ValueError('需要归档证据ID及SHA256')
    row=c.execute('SELECT * FROM artifacts WHERE id=?',(ref['artifact_id'],)).fetchone()
    if not row or ref.get('sha256')!=row['sha256']:raise ValueError('证据引用不存在或SHA不一致')
    blob=Path(c.execute('PRAGMA database_list').fetchone()[2]).parent/'blobs'/row['sha256']
    if not blob.exists() or hashlib.sha256(blob.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('证据归档字节缺失或损坏')
    return blob

def validate(c,d):
    if d.get('research_contract')!=VERSION:raise ValueError('新预测必须使用RESEARCH_FREEZE_V1完整研究契约；旧预测不改写')
    if d.get('draft_only'):raise ValueError('研究工作表尚未审查，禁止冻结草稿')
    cut=lab.stamp(d['information_cutoff'])
    if abs(d['cost']-.0045)>1e-10 or d.get('entry_scenario_id') not in ('GAP_0_3','ANY_EXECUTABLE_AUCTION'):raise ValueError('本研究固定成本0.45%，须明确竞价情景版本')
    pool=json.loads(archived(c,d['candidate_pool']).read_text())
    archived(c,pool['source_ref'])
    codes=[x['code'] for x in pool['candidates']]
    if pool['signal_date']!=d['signal_date'] or len(codes)!=pool['expected_count'] or len(set(codes))!=len(codes):raise ValueError('同D完整候选名单数量/日期不一致')
    if set(codes)!={x['code'] for x in d['predictions']}:raise ValueError('必须覆盖完整候选池，不能只冻结Top10')
    if not pool.get('source') or lab.stamp(pool['captured_at'])>cut:raise ValueError('候选名单缺来源或晚于截止')
    if any(x.get('board_stage') not in (2,3) for x in pool['candidates']):raise ValueError('D池须为二板/三板')
    cal=json.loads(archived(c,d['calendar_ref']).read_text())
    sessions=cal['sessions']
    if not cal.get('source') or not sessions or sessions!=sorted(set(sessions)):raise ValueError('交易日历须有来源及有序去重交易日')
    archived(c,cal['source_ref'])
    i=sessions.index(d['signal_date'])
    local=__import__('datetime').timezone(__import__('datetime').timedelta(hours=8))
    if len(sessions)<=i+2 or sessions[i+1]!=lab.stamp(d['entry_at']).astimezone(local).strftime('%Y%m%d') or sessions[i+2]!=lab.stamp(d['exit_at']).astimezone(local).strftime('%Y%m%d'):raise ValueError('T/T+1必须为日历中后续两个交易日')
    if lab.stamp(cal['captured_at'])>cut:raise ValueError('日历证据晚于截止')
    weights.schema(c)
    official={}
    if d.get('formal_comparison_ref'):
        formal=json.loads(archived(c,d['formal_comparison_ref']).read_text())
        if formal.get('signal_date')!=d['signal_date'] or formal.get('status')!='FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION' or formal.get('formal_rank_allowed') is False or not formal.get('activation_id') or not formal.get('pre_cas_freeze_at_utc'):
            raise ValueError('比较名单必须为同D已激活正式冻结')
        if lab.stamp(formal['pre_cas_freeze_at_utc'])>cut:raise ValueError('官方比较名单晚于截止')
        official={r['ts_code']:r.get('candidate_rank') for r in formal['rows']}
    cfgrow=c.execute('SELECT payload FROM research_weight_versions WHERE version=?',(d['weight_version'],)).fetchone()
    if not cfgrow:raise ValueError('权重版本尚未注册')
    cfg=json.loads(cfgrow[0]);result={}
    for x in d['predictions']:
        if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',x['code']):raise ValueError('代码格式错误')
        fs=x.get('weighted_features',{})
        if set(fs)!=set(cfg['weights']):raise ValueError('六个维度必须逐项记录，缺失不能省略')
        if x.get('probability_method') not in ('SUBJECTIVE_UNCALIBRATED','UNINFORMED'):raise ValueError('尚无校准模型准入，不允许标已校准')
        if x['probability_method']=='UNINFORMED' and x['p'] not in (None,.5):raise ValueError('无信息只能未评级或50%占位')
        if x['p'] is not None and abs(x['p']*20-round(x['p']*20))>1e-8:raise ValueError('未经校准主观概率保持5个百分点粒度')
        if not x.get('counterevidence') or not x.get('invalidation') or not x.get('execution_risk'):raise ValueError('每股须有反证、失效条件及执行风险')
        refs=x.get('evidence_refs',{});used=set()
        for key,f in fs.items():
            if f.get('status')=='MISSING':
                if f.get('score') is not None or not f.get('missing_reason'):raise ValueError('缺失须空分及明确原因')
                continue
            if f.get('status')!='VERIFIED':raise ValueError('因子状态只允许VERIFIED/MISSING')
            if not f.get('rationale') or not f.get('evidence_ids'):raise ValueError('有评分必须有证据与解释')
            if lab.stamp(f['captured_at'])>cut or lab.stamp(f['available_at'])>cut:raise ValueError('未来/事后因子不可冻结')
            for eid in f['evidence_ids']:
                if eid not in refs:raise ValueError('因子证据未绑定归档')
                ref=refs[eid];archived(c,ref)
                if ref['sha256'] in used:raise ValueError('同一原始证据不同别名重复加权')
                used.add(ref['sha256'])
                if ref.get('source_role')=='DC20_RANK_OR_SCORE':raise ValueError('官方排名不能作为独立盈利预测因子')
                if not ref.get('source') or not ref.get('as_of'):raise ValueError('证据必须含来源及数据时点')
                if not lab.stamp(ref['as_of'])<=lab.stamp(ref['available_at'])<=cut or lab.stamp(ref['captured_at'])>cut:raise ValueError('证据时点不合规')
        score=weights.score(cfg,fs,d['information_cutoff'])
        result[x['code']]={'research_contract':VERSION,'comparison_policy':(__import__('benchmark').POLICY_V1 if d['entry_scenario_id']=='GAP_0_3' else __import__('benchmark').ACTIVE_POLICY),'comparison_official_rank':official.get(x['code']),'formal_comparison_ref':d.get('formal_comparison_ref'),'weight_version':cfg['version'],'feature_schema':cfg['feature_schema'],'entry_scenario_id':d['entry_scenario_id'],'candidate_pool':d['candidate_pool'],'calendar_ref':d['calendar_ref'],'weighted_review':score,'independent_rank_eligible':x['p'] is not None and x['probability_method']!='UNINFORMED'}
    return result

def validate_outcome(c,p,d):
    payload=json.loads(p['payload'])
    if payload.get('research_contract')!=VERSION:return
    if d['status']!='SETTLED':return
    review=d.get('execution_review',{})
    if review.get('basis')!=d['basis'] or not review.get('reviewer'):raise ValueError('结果必须有同口径执行证据审查')
    for leg in ('entry','exit'):
        item=review.get(leg,{})
        archived(c,item)
        if item.get('instrument')!=p['code'] or lab.stamp(item['at'])!=lab.stamp(d[leg+'_at']):raise ValueError('执行证据股票/时点不一致')
        if item.get('price')!=d[leg+'_price']:raise ValueError('执行审查价与结果不一致')
        if item.get('executable') is not True or not item.get('rationale'):raise ValueError('仅报价不足以证明可成交')
        if d['basis']=='ACTUAL' and item.get('kind')!='ORDER_FILL':raise ValueError('实际结果需要订单成交证据')
        if d['basis']=='QUOTE_PROXY' and item.get('kind')!='REVIEWED_EXECUTABLE_QUOTE':raise ValueError('行情代理需要报价及可执行证据')
    pre=review.get('previous_close')
    if not isinstance(pre,(int,float)) or isinstance(pre,bool) or pre<=0:raise ValueError('入场需前收盘价审查情景')
    gap=d['entry_price']/pre-1
    # Gap is descriptive. Executability, not a price-gap window, gates settlement.
