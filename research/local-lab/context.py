"""Evidence-led research context. No synthetic probabilities or automatic weights."""
import json, math, statistics
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def catalog(): return json.loads((ROOT/'data-catalog.json').read_text())
def iso(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('时间必须包含时区')
    return d

def review(d, cutoff):
    """Review declared evidence. Source authenticity still requires human/agent verification."""
    cut=iso(cutoff);result=[];seen=set()
    for row in d.get('observations',[]):
        key=(row['family'],row['instrument'])
        if key in seen:raise ValueError('同一包维度和标的重复')
        seen.add(key)
        if row['family'] not in {x['id'] for x in catalog()}:raise ValueError('未知研究维度')
        reasons=list(row.get('quality_issues',[]))
        if not row.get('source') or not row.get('evidence'):reasons.append('缺来源或原始证据')
        if not row.get('as_of') or not row.get('available_at'):reasons.append('行情日期/可获得时间未核验')
        else:
            if iso(row['as_of'])>iso(row['available_at']):reasons.append('数据时点晚于可获得时间')
            if iso(row['available_at'])>cut:reasons.append('超出预测截止时间')
        if iso(row['captured_at'])>cut:reasons.append('采集发生在预测截止后，仅供事后研究')
        if row.get('status')!='VERIFIED':reasons.append('未核验或不完整')
        derived={};v=row.get('values',{})
        for key2 in ('last','open','previous_close','high','low'):
            if key2 in v and (not isinstance(v[key2],(int,float)) or isinstance(v[key2],bool) or not math.isfinite(v[key2]) or v[key2]<=0):raise ValueError('非法行情数值')
        if all(k in v for k in ('last','open','previous_close')):
            derived={'gap_pct':100*(v['open']/v['previous_close']-1),'open_to_last_pct':100*(v['last']/v['open']-1),'previous_close_to_last_pct':100*(v['last']/v['previous_close']-1)}
            if 'high' in v and 'low' in v and not(v['low']<=min(v['open'],v['last'])<=max(v['open'],v['last'])<=v['high']):reasons.append('OHLC不一致')
        members=row.get('members')
        if members is not None:
            codes=[m['code'] for m in members]
            complete=len(codes)>0 and len(set(codes))==len(codes) and len(codes)==row.get('expected_members') and row.get('membership_as_of')==row.get('as_of')
            if not complete:reasons.append('成分股数量/去重/同日名单未通过')
            elif all(isinstance(m.get('return_pct'),(int,float)) and math.isfinite(m['return_pct']) for m in members):
                rr=[m['return_pct'] for m in members]
                derived.update(member_n=len(rr),positive_ratio=sum(r>0 for r in rr)/len(rr),median_return_pct=statistics.median(rr),loss5_ratio=sum(r<=-5 for r in rr)/len(rr))
            else:reasons.append('成分股收益存在缺失')
        result.append({**row,'eligible':not reasons,'exclusions':sorted(set(reasons)),'diagnostic_metrics':derived,'predictive_metrics':derived if not reasons else {}})
    return {'cutoff':cutoff,'observations':result,'eligible_n':sum(x['eligible'] for x in result),'note':'诊断计算仅解释原始数值；eligible不代表有预测增益。动态成分指数不是可投资收益。'}

def plan(report):
    out=[]
    for item in catalog():
        rows=[r for r in report['observations'] if r['family']==item['id']]
        required=item.get('required_instruments',[])
        ready=bool(rows) and all(r['eligible'] for r in rows) and all(any(r['instrument']==code and r['eligible'] for r in rows) for code in required)
        out.append({**item,'state':'OBSERVED_REQUIRES_RESEARCH' if ready else 'NEEDS_EVIDENCE','observed_n':len(rows),'eligible_n':sum(r['eligible'] for r in rows),'next_action':item['action'] if not ready else '检验与候选股的条件关系及反证，记录证据方向；不能自动加权转概率'})
    return out

def ingest(c,path,artifact,event,now):
    d=json.loads(Path(path).read_text());report=review(d,d['cutoff']);report['tasks']=plan(report)
    report['research_review']=d.get('research_review')
    c.execute('CREATE TABLE IF NOT EXISTS research_context(id INTEGER PRIMARY KEY,recorded_at TEXT NOT NULL,artifact_id INTEGER NOT NULL UNIQUE REFERENCES artifacts(id),payload TEXT NOT NULL)')
    for op in ('UPDATE','DELETE'):
        c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_context_{op} BEFORE {op} ON research_context BEGIN SELECT RAISE(ABORT,'append-only evidence'); END")
    aid=artifact(c,path)
    c.execute('INSERT OR IGNORE INTO research_context(recorded_at,artifact_id,payload) VALUES(?,?,?)',(now(),aid,json.dumps(report,ensure_ascii=False)))
    event(c,'RESEARCH_CONTEXT_REVIEW',{'artifact':aid,'eligible':report['eligible_n'],'observations':len(report['observations'])});c.commit()
    return report

def latest(c):
    if not c.execute("SELECT name FROM sqlite_master WHERE name='research_context'").fetchone():return {'observations':[],'tasks':plan({'observations':[]})}
    row=c.execute('SELECT payload FROM research_context ORDER BY id DESC LIMIT 1').fetchone()
    return json.loads(row[0]) if row else {'observations':[],'tasks':plan({'observations':[]})}
