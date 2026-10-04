"""Build a full-pool unscored worksheet from a verified candidate manifest, never freeze/send."""
import argparse,json
from pathlib import Path
import lab,adaptive_weights as weights

def prepare(c,pool_file,calendar_file,folder,model):
    folder.mkdir(parents=True,exist_ok=False)
    def ref(p):
        aid=lab.artifact(c,p);return dict(artifact_id=aid,sha256=c.execute('SELECT sha256 FROM artifacts WHERE id=?',(aid,)).fetchone()[0])
    pool=lab.read(pool_file);cal=lab.read(calendar_file);day=pool['signal_date'];i=cal['sessions'].index(day)
    cfg=lab.read(lab.ROOT/'weight-policy-v1.json');weights.register(c,lab.ROOT/'weight-policy-v1.json')
    def iso(d,time):return f'{d[:4]}-{d[4:6]}-{d[6:]}T{time}+08:00'
    t=lab.now();d=dict(research_contract='RESEARCH_FREEZE_V1',draft_only=True,signal_date=day,entry_at=iso(cal['sessions'][i+1],'09:25:00'),exit_at=iso(cal['sessions'][i+2],'10:00:00'),information_cutoff=t,latest_data_available_at=t,calendar_evidence=cal['source'],calendar_ref=ref(calendar_file),candidate_pool=ref(pool_file),strategy=lab.STRATEGY,model=model,model_kind='subjective_uncalibrated',cost=.0045,entry_scenario='竞价高开0–3%，且实际可买入并T+1 10:00退出',entry_scenario_id='GAP_0_3',weight_version=cfg['version'],predictions=[])
    for x in pool['candidates']:
        d['predictions'].append(dict(code=x['code'],name=x['name'],p=None,probability_method='UNINFORMED',reason='待完成逐股正反证审查；本文件不是新预测',data_sources=[pool['source']],counterevidence='待逐股补充',invalidation='待逐股补充入场失效条件',execution_risk='停牌、涨停排队或跌停缺流动性单列；成交未确认',evidence_refs={},weighted_features={k:dict(status='MISSING',score=None,missing_reason='尚未完成该维度证据绑定及判断') for k in cfg['weights']}))
    p=folder/'research-draft.json';p.write_text(json.dumps(d,ensure_ascii=False,indent=2));lab.artifact(c,p);lab.event(c,'RESEARCH_WORKSHEET',dict(day=day,count=len(d['predictions']),file=str(p)));c.commit();return p
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--pool',type=Path,required=True);a.add_argument('--calendar',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--model',required=True);o=a.parse_args()
    with lab.connect() as c:print(prepare(c,o.pool,o.calendar,o.out,o.model))
