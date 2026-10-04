"""Synthetic fixtures only; never used by production forecasts."""
import json
from pathlib import Path
import lab,adaptive_weights as weights

def attach(c,root,d):
    def ref(name,value):
        p=Path(root)/name;p.write_text(json.dumps(value));aid=lab.artifact(c,p)
        return dict(artifact_id=aid,sha256=c.execute('SELECT sha256 FROM artifacts WHERE id=?',(aid,)).fetchone()[0])
    cfg=json.loads((weights.ROOT/'weight-policy-v1.json').read_text());cfg['effective_at']='2020-01-01T00:00:00Z'
    p=Path(root)/'weights.json';p.write_text(json.dumps(cfg));weights.register(c,p)
    at='2026-09-24T14:00:00+08:00'
    pool_source=ref('pool-source.json',{'test':'candidate source'})
    pool=ref('pool.json',dict(source_ref=pool_source,signal_date=d['signal_date'],expected_count=len(d['predictions']),source='synthetic test',captured_at=at,candidates=[dict(code=x['code'],board_stage=2) for x in d['predictions']]))
    raw=ref('calendar-source.json',{'test':'synthetic only'})
    cal=ref('calendar.json',dict(source='synthetic calendar',captured_at=at,source_ref=raw,sessions=['20260924','20260928','20260929']))
    d.update(research_contract='RESEARCH_FREEZE_V1',weight_version=cfg['version'],candidate_pool=pool,calendar_ref=cal,entry_scenario_id='GAP_0_3')
    for x in d['predictions']:
        x.update(probability_method='SUBJECTIVE_UNCALIBRATED',counterevidence='synthetic counterevidence',invalidation='synthetic invalidation',execution_risk='synthetic only',evidence_refs={},weighted_features={k:dict(status='MISSING',score=None,missing_reason='synthetic missing') for k in cfg['weights']})
    return d
