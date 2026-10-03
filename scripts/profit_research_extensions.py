"""Versioned research-only fields from existing bound D artifacts.

No scoring, training, HTTP requests or production writes. T quotes and results
are separate observations; old missing inputs are never overwritten.
"""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path

ROOT = 'outputs/decision/profit_research'
VERSION = 'dc20_research_extensions_v1'
# Source name, human definition, unit, acceptable range.
FIELDS = [
 ('predicted_promotion_probability','D日晋级概率','比例',0,1),
 ('gap_open','D日开盘相对前收盘缺口','比例',-1,1),
 ('limit_first_time_minutes','D日首次封板时刻（午夜起分钟数）','分钟',0,1440),
 ('limit_last_time_minutes','D日最后封板时刻（午夜起分钟数）','分钟',0,1440),
 ('limit_open_times','D日炸板次数','次',0,None),
 ('limit_seal_to_amount','D日封单额与成交额比','比例',0,None),
 ('d_turnover_rate','D日换手率（0.10表示10%）','比例',0,None),
 ('d_volume_ratio','D日量比','倍',0,None),
 ('d_amount_log','ln(1+D日成交额，成交额以千元计)','自然对数',None,None),
 ('path_days_observed','连板路径观测天数','日',0,None),
 ('path_data_coverage','路径数据覆盖度','比例',0,1),
 ('path_strength_delta','路径最近两日规则分变化','分',None,None),
 ('path_gap_slope','路径开盘缺口首末差 / 观测间隔','比例/板',None,None),
 ('path_first_seal_slope','路径首封时刻首末差 / 观测间隔','分钟/板',None,None),
 ('path_open_times_slope','路径炸板次数首末差 / 观测间隔','次/板',None,None),
 ('path_seal_ratio_slope','路径封单成交额比首末差 / 观测间隔','比例/板',None,None),
 ('path_turnover_slope','路径换手率首末差 / 观测间隔','比例/板',None,None),
 ('path_amount_log_slope','路径ln(1+成交额元)首末差 / 观测间隔','自然对数/板',None,None),
 ('path_one_price_ratio','路径一字板比例','比例',0,1),
 ('market_sentiment_score','D日市场情绪规则分','分',None,None),
 ('market_up_ratio','D日市场上涨比例','比例',0,1),
 ('market_failed_limit_up_rate','D日市场炸板比例','比例',0,1),
 ('minute_realized_vol','D日分钟对数收益标准差（非年化）','比例',0,None),
 ('minute_first_30m_return','D日前30分钟涨跌','比例',-1,None),
 ('minute_last_30m_return','D日末30分钟涨跌','比例',-1,None),
]
TEXT_FIELDS = [('path_label','原路径分类标签'),('path_explanation','原路径描述')]
ENTRY_FIELDS = ('entry_price','entry_price_source','reported_auction_amount',
                'capacity_amount','capacity_reason','auction_trade_observed',
                't_opening_limit_up_observed','entry_qualification_status',
                'daily_open_cent_match','auction_request_receipt_observed')

def encoded(x):
    return (json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()

def sha(raw): return hashlib.sha256(raw).hexdigest()

def bound(root, binding):
    p=Path(binding['path'])
    target=root/p
    if p.is_absolute() or '..' in p.parts or not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise ValueError('UNSAFE_EXTENSION_SOURCE')
    raw=target.read_bytes()
    if sha(raw)!=binding['sha256']: raise ValueError('EXTENSION_SOURCE_HASH_MISMATCH')
    return raw

def numeric(value, lo=None, hi=None):
    if value is None or str(value).strip() in ('','None','nan','NaN'):
        return None,'SOURCE_FIELD_MISSING'
    try: v=float(value)
    except (ValueError,TypeError): return None,'INVALID_NUMERIC'
    if not math.isfinite(v) or (lo is not None and v<lo) or (hi is not None and v>hi):
        return None,'OUT_OF_RANGE'
    return v,None

def catalogue():
    return [dict(key=k,definition=label,unit=unit,phase='D',old_model_uses=False,
                 old_model_note='独立扩展；不回填旧盈利模型输入')
            for k,label,unit,_,_ in FIELDS]+[
        dict(key=k,definition=label,unit='文本',phase='D',old_model_uses=False,
             old_model_note='原网页路径与旧训练路径类别不可直接等同') for k,label in TEXT_FIELDS]

ENRICHMENT_FIELDS = ('limit_last_time_minutes','minute_realized_vol',
                     'minute_first_30m_return','minute_last_30m_return')

def attach_enrichment(root, row, bindings):
    """Overlay explicitly versioned research values, never original model inputs."""
    from scripts.profit_research_enrichment import read_record
    envelope = read_record(root, row['signal_date'], row['ts_code'])
    if envelope is None:
        return
    payload = envelope['payload']
    binding = envelope['source_binding']
    row['research_enrichment'] = {**payload,
        'first_collected_at_utc': envelope['first_collected_at_utc'],
        'snapshot_binding': binding}
    ext = row['research_extensions']
    # Keep both views so backfills cannot silently rewrite the frozen evidence.
    ext['original_values'] = {k:ext['values'][k] for k in ENRICHMENT_FIELDS}
    ext['value_sources'] = {}
    ext['effective_values_timestamp_before_entry'] = bool(ext['source_timestamp_before_entry'])
    for key in ENRICHMENT_FIELDS:
        value = payload.get('values', {}).get(key)
        if value is not None:
            field_source=payload.get('field_sources',{}).get(key,{})
            ext['values'][key] = value
            ext['missing_reasons'][key] = None
            ext['value_sources'][key] = {
                'kind': 'RESEARCH_ENRICHMENT', 'snapshot_binding': binding,
                'collection_kind': field_source.get('collection_kind',payload.get('collection_kind')),
                'observed_at_utc':field_source.get('observed_at_utc'),
                'rule_version': payload.get('rule_version'),
                'point_in_time_independently_verified': False}
            ext['effective_values_timestamp_before_entry'] = ext['effective_values_timestamp_before_entry'] and field_source.get('source_timestamp_before_entry') is True
        elif ext['values'][key] is None:
            ext['missing_reasons'][key] = payload.get('missing_reasons', {}).get(key) or 'ENRICHMENT_NOT_AVAILABLE'
    bindings.append(binding)
    for source in payload.get('field_sources', {}).values():
        if isinstance(source, dict) and source.get('source_path') and source.get('raw_sha256'):
            bindings.append({'path':source['source_path'], 'sha256':source['raw_sha256']})

def enrichment_quality(doc):
    rows=[r for d in doc['days'] for r in d['records']]
    additions=[r['research_enrichment'] for r in rows if 'research_enrichment' in r]
    minutes=ENRICHMENT_FIELDS[1:]
    stamps=[e.get('first_collected_at_utc') for e in additions if e.get('first_collected_at_utc')]
    fields=[k for k,*_ in FIELDS+TEXT_FIELDS]
    return {
        'enrichment': {
            'records_enriched':sum(any(v is not None for v in e.get('values',{}).values()) for e in additions),
            'records_attempted':len(additions),
            'minute_complete_records':sum(all(e.get('values',{}).get(k) is not None for k in minutes) for e in additions),
            'last_seal_recovered_records':sum(r.get('research_enrichment',{}).get('values',{}).get('limit_last_time_minutes') is not None and r['research_extensions'].get('original_values',{}).get('limit_last_time_minutes') is None for r in rows),
            'historical_backfill_records':sum(e.get('collection_kind')=='HISTORICAL_BACKFILL' or any(s.get('collection_kind')=='HISTORICAL_BACKFILL' for s in e.get('field_sources',{}).values()) for e in additions),
            'before_entry_records':sum(e.get('collection_kind')=='BEFORE_ENTRY_COLLECTION' for e in additions),
            'independently_verified_records':0,
            'latest_collected_at_utc':max(stamps) if stamps else None,
            'collection_status':('COMPLETE' if rows and len(additions)==len(rows) and all(all(e.get('values',{}).get(k) is not None for k in ENRICHMENT_FIELDS) for e in additions) else 'PARTIAL' if additions else 'NOT_COLLECTED'),
        },
        'readiness': {
            'feature_complete_days':sum(bool(d['records']) and all(all(r['research_extensions']['values'].get(k) is not None for k in fields) for r in d['records']) for d in doc['days']),
            'label_complete_days':sum(bool(d['mature_training_day']) for d in doc['days']),
            'training_admitted_days':0, 'status':'NOT_ADMITTED',
            'reason':'时点核验、时间切分与样本独立性尚未完成；完整性计数不是训练准入',
        },
    }

def day_extension(root, day, revision, now):
    date=day['date']; rows=day['records']
    values={r['ts_code']:{k:None for k,*_ in FIELDS+TEXT_FIELDS} for r in rows}
    reasons={code:{k:'BOUND_D_RUNTIME_UNAVAILABLE' for k in values[code]} for code in values}
    bindings=[]; generated=None; source_status='MISSING'; available_before_entry=False
    if rows and day['provenance']=='FORMAL_FROZEN_REFERENCE':
        try:
            snapshot_binding=next(b for b in day['source_bindings'] if b['path'].startswith('work/profit_1000_upgrade/candidate_natural_forward/'))
            snapshot=json.loads(bound(root,snapshot_binding))
            declarations=snapshot['D_source_evidence']['projection']['four_file_declarations']
            runtime_binding=declarations['runtime_features']; receipt_binding=declarations['receipt']
            if runtime_binding['path']!=f'outputs/decision/primary_d_runtime_features_{date}.csv' or receipt_binding['path']!=f'outputs/decision/primary_d_receipt_{date}.json':
                raise ValueError('D_RUNTIME_PATH_MISMATCH')
            receipt=json.loads(bound(root,receipt_binding))
            if receipt['signal_date']!=date: raise ValueError('RECEIPT_DATE_MISMATCH')
            table=list(csv.DictReader(io.StringIO(bound(root,runtime_binding).decode('utf-8-sig'))))
            selected=[r for r in table if r.get('ts_code') in values]
            if len(selected)!=len(values) or len({r['ts_code'] for r in selected})!=len(values):
                raise ValueError('D_RUNTIME_MEMBERSHIP_MISMATCH')
            bindings=[snapshot_binding,runtime_binding,receipt_binding]
            for r in selected:
                if r.get('signal_date')!=date: raise ValueError('D_RUNTIME_DATE_MISMATCH')
                stamp=datetime.fromisoformat(r['generated_at_utc'].replace('Z','+00:00'))
                # Date identity is checked in Asia/Shanghai, never inferred from run time.
                from zoneinfo import ZoneInfo
                local_stamp=stamp.astimezone(ZoneInfo('Asia/Shanghai'))
                if local_stamp.strftime('%Y%m%d')<date:
                    raise ValueError('D_RUNTIME_GENERATED_DATE_MISMATCH')
                if stamp>datetime.fromisoformat(now.replace('Z','+00:00')):
                    raise ValueError('FUTURE_RUNTIME_TIMESTAMP')
                generated=r['generated_at_utc']; code=r['ts_code']
                for k,_,_,lo,hi in FIELDS:
                    values[code][k],reasons[code][k]=numeric(r.get(k),lo,hi)
                for k,_ in TEXT_FIELDS:
                    text=r.get(k)
                    values[code][k]=text if isinstance(text,str) and 0<len(text)<=2000 else None
                    reasons[code][k]=None if values[code][k] is not None else 'SOURCE_FIELD_MISSING'
            generated=max(r['generated_at_utc'] for r in selected)
            stamps=[datetime.fromisoformat(r['generated_at_utc'].replace('Z','+00:00')) for r in selected]
            entry_dates={r.get('exec_date') for r in rows}
            if len(entry_dates)==1 and None not in entry_dates:
                cutoff=datetime.strptime(next(iter(entry_dates))+'0925','%Y%m%d%H%M').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                available_before_entry=all(stamp<cutoff for stamp in stamps)
            source_status='HASH_BOUND_D_ARTIFACT' if all(stamp.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')==date for stamp in stamps) else 'HASH_BOUND_LATE_GENERATED_D_ARTIFACT'
        except (OSError,ValueError,KeyError,TypeError,StopIteration):
            source_status='SOURCE_UNAVAILABLE_OR_REJECTED'
            values={code:{k:None for k in fields} for code,fields in values.items()}
            reasons={code:{k:'BOUND_SOURCE_UNAVAILABLE_OR_REJECTED' for k in fields} for code,fields in reasons.items()}
            bindings=[]; generated=None; available_before_entry=False
    # p0 probabilities are persisted in the original frozen list; if the extended
    # runtime is unavailable they still remain visible with their own source binding.
    try:
        p0binding=next(b for b in day['source_bindings'] if b['path']==f'outputs/decision/three_rank_top10_{date}.json')
        p0=json.loads(bound(root,p0binding))
        if p0.get('signal_date')!=date: raise ValueError('P0_DATE_MISMATCH')
        if day['provenance']=='FORMAL_FROZEN_REFERENCE':
            for r in p0['rows']:
                if r['ts_code'] in values:
                    v,error=numeric(r.get('predicted_promotion_probability'),0,1)
                    if v is not None:
                        values[r['ts_code']]['predicted_promotion_probability']=v
                        reasons[r['ts_code']]['predicted_promotion_probability']=None
            if p0binding not in bindings: bindings.append(p0binding)
    except (OSError,ValueError,KeyError,TypeError,StopIteration): pass
    return {'schema_version':VERSION,'signal_date':date,'source_status':source_status,
            'source_generated_at_utc':generated,'point_in_time_independently_verified':False,
            'source_timestamp_before_entry':available_before_entry,
            'historical_backfill_claimed_forward':False,'source_bindings':bindings,
            'records':[{'ts_code':r['ts_code'],'values':values[r['ts_code']],
                        'missing_reasons':reasons[r['ts_code']]} for r in rows]}

def enrich(root, doc, revision, now=None):
    root=Path(root); now=now or datetime.now(timezone.utc).isoformat()
    cat=catalogue(); extension_bindings=[]; complete_days=0
    for day in doc['days']:
        payload=day_extension(root,day,revision,now)
        digest=sha(encoded(payload))
        path=f"{ROOT}/extensions/{day['date']}/{digest}.json"
        target=root/path
        if target.exists():
            raw=target.read_bytes(); envelope=json.loads(raw)
            if envelope['payload']!=payload or envelope['payload_sha256']!=digest:
                raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
        else:
            envelope={'payload':payload,'payload_sha256':digest,'first_collected_at_utc':now,
                      'first_collected_revision':revision}
            raw=encoded(envelope); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f:f.write(raw)
        binding={'path':path,'sha256':sha(raw)}
        extension_bindings.extend(payload['source_bindings']); extension_bindings.append(binding)
        bycode={r['ts_code']:r for r in payload['records']}
        for r in day['records']:
            r['research_extensions']={**bycode[r['ts_code']],'schema_version':VERSION,
                'source_status':payload['source_status'],'source_generated_at_utc':payload['source_generated_at_utc'],
                'source_timestamp_before_entry':payload['source_timestamp_before_entry'],
                'first_collected_at_utc':envelope['first_collected_at_utc'],
                'point_in_time_independently_verified':False,'snapshot_binding':binding}
            # Independent enrichment is a separate immutable evidence layer.
            # Copy mappings so the bound payload and existing envelope stay unchanged.
            r['research_extensions']['values']=dict(r['research_extensions']['values'])
            r['research_extensions']['missing_reasons']=dict(r['research_extensions']['missing_reasons'])
            attach_enrichment(root,r,extension_bindings)
            # Entry and future results are never merged into D feature values.
            lp=root/ROOT/'labels'/day['date']/(r['ts_code']+'.json')
            r['entry_observation']={'status':'NOT_CAPTURED_IN_EXISTING_LABEL','values':{},'phase':'T'}
            if lp.exists():
                lab=json.loads(lp.read_bytes())
                # The base builder already validates eligible labels against snapshot,
                # model and policies. Only that validated file may supply extra data.
                if r['outcome'].get('source_binding',{}).get('sha256')==sha(lp.read_bytes()):
                    entry=lab.get('entry_observation',{})
                    entry={k:entry[k] for k in ENTRY_FIELDS if isinstance(entry,dict) and k in entry and isinstance(entry[k],(str,int,float,bool)) and (not isinstance(entry[k],float) or math.isfinite(entry[k]))}
                    r['entry_observation']={'status':'CAPTURED' if entry else 'NOT_CAPTURED_IN_EXISTING_LABEL',
                        'values':entry,'phase':'T','source_binding':r['outcome']['source_binding']}
        required=('predicted_promotion_probability','gap_open','limit_first_time_minutes','limit_open_times','limit_seal_to_amount')
        if day['records'] and all(all(r['research_extensions']['values'][k] is not None for k in required) for r in day['records']):
            complete_days+=1
    records=[r for d in doc['days'] for r in d['records']]
    quality=[]
    for item in cat:
        vals=[r['research_extensions']['values'][item['key']] for r in records]
        known=[v for v in vals if v is not None]; dates=[r['signal_date'] for r in records if r['research_extensions']['values'][item['key']] is not None]
        quality.append({**item,'records':len(records),'known':len(known),'coverage':len(known)/len(records) if records else None,
            'first_available_signal_date':min(dates) if dates else None,'constant_observed':bool(known) and len(set(known))==1,
            'missing':len(vals)-len(known),'invalid':sum(r['research_extensions']['missing_reasons'][item['key']] in ('INVALID_NUMERIC','OUT_OF_RANGE') for r in records)})
    m=doc['model']
    disabled=set(('promotion_probability','path_change'))
    doc['research_data_quality']={'schema_version':VERSION,'fields':quality,
        'complete_core_extension_days':complete_days,'required_core_fields':list(required),
        'mature_extended_training_days':sum(bool(d['records']) and d['mature_training_day'] and all(r['research_extensions']['source_timestamp_before_entry'] and all(r['research_extensions']['values'][k] is not None for k in required) for r in d['records']) for d in doc['days']),
        'model_fields':[{'key':k,'currently_informative':k not in disabled and not k.startswith('path_'),
                         'reason':'历史缺失，旧模型保持原输入契约' if k in disabled or k.startswith('path_') else '原模型输入；有效性需独立评估'}
                        for k in m.get('features',[])],
        'training_allowed':False,'production_model_changed':False,
        'time_policy':'D特征与T竞价、后续结果分开保存；事后采集时间不冒充盘前可用时间'}
    doc['research_data_quality'].update(enrichment_quality(doc))
    doc['extension_source_bindings']=list({b['path']+':'+b['sha256']:b for b in extension_bindings}.values())
    return doc
