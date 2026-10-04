"""Validate dated funding series. Rolling windows are not independent daily flows."""
import math
import lab

def review(d,cutoff):
    errors=[];cut=lab.stamp(cutoff)
    if d.get('unit') not in ('CNY','CNY_10K','CNY_100M'):errors.append('资金单位未核验')
    if not d.get('source') or not d.get('definition') or not d.get('date_evidence'):errors.append('缺来源/资金定义/日期依据')
    rows=d.get('daily',[]);days=[r['day'] for r in rows];sessions=d.get('expected_sessions',[])
    if len(sessions)<20 or sessions!=sorted(set(sessions)) or days!=sessions:errors.append('不足连续20交易日或存在缺日/重复/错序')
    if not d.get('as_of') or (days and days[-1]!=d['as_of']):errors.append('截至日期不一致')
    if any(not isinstance(r.get('net'),(int,float)) or isinstance(r.get('net'),bool) or not math.isfinite(r['net']) for r in rows):errors.append('缺失/非法资金值')
    try:
        if lab.stamp(d['available_at'])>cut or lab.stamp(d['captured_at'])>cut:errors.append('截止后数据')
    except (KeyError,ValueError):errors.append('缺可获得/采集时间')
    if d.get('scope')!='DAILY_SAME_DEFINITION':errors.append('滚动排行不能冒充每日资金持续性')
    metrics={}
    if not errors:
        factor={'CNY':1,'CNY_10K':1e4,'CNY_100M':1e8}[d['unit']]
        for n in (1,3,5,10,20):
            tail=rows[-n:];metrics[str(n)]={'net_cny':sum(r['net'] for r in tail)*factor,'positive_days':sum(r['net']>0 for r in tail),'observed_days':n}
    return dict(eligible=not errors,errors=errors,metrics=metrics,note='主力/全口径不得混用；资金统计是供应商算法口径，非真实机构持仓。')
