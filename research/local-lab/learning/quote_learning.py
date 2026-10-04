"""Independent, point-in-time quote-return research; never execution settlement.

Only full original candidate days with pre-cutoff numeric evidence enter training.
Partial days stay visible in ``candidates`` with their exact exclusion reasons.
"""
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import lab
from learning import features, quote_ledger, store

METHOD = 'quote_return_v1'
BASIS = 'QUOTE_RETURN'
SCOPE = 'QUOTE_RETURN_RESEARCH_ONLY'
VERSION = 'QUOTE_RETURN_DATASET_V1'


def policy():
    p = json.loads((store.ROOT / 'policy.json').read_text())
    p.update(version='QUOTE_RETURN_LEARNING_V1', method=METHOD,
             observation_status='VERIFIED_PRICE_PAIR', strict_cutoff=True, strict_information_cutoff=True,
             scenario='ALL_ORIGINAL_CANDIDATES_QUOTE_RETURN', basis=BASIS,
             scope=SCOPE, model_kind='quote_model',
             probability_target='P_QUOTE_NET_POSITIVE_NOT_EXECUTABLE_PROFIT',
             calibrates_original_probability=False, production_promotion=False,
             target_semantics='AUCTION_TO_T1_1000_QUOTE_NET_RETURN',
             promotion='DISABLED_QUOTE_RESEARCH_ONLY', training_interval_days=7, training_new_days=5,
             capital='Equal-weight quote observations; no assertion of fills or portfolio NAV',
             note='Full original candidate dates only. Frozen pre-cutoff numeric snapshots; '
                  'labels available only after archived quote evidence and verification. '
                  'Original executable probabilities and settlement counts are never changed.')
    return p


def _records(c, kind):
    """Read-only counterpart of store.get, including the immutable content digest."""
    if not c.execute("select 1 from sqlite_master where name='learning_records'").fetchone():
        return []
    out = []
    for r in c.execute('select * from learning_records where kind=? order by id', (kind,)):
        try:
            d = json.loads(r['payload'])
            out.append(dict(d, id=r['id'], key=r['key'], _recorded_at=r['at'],
                            _sha=r['sha'], _artifact_id=r['artifact_id']))
        except (TypeError, ValueError):
            continue
    return out


def _artifact(c, aid):
    r = c.execute('select * from artifacts where id=?', (aid,)).fetchone()
    if not r:
        raise ValueError('UNKNOWN_ARTIFACT')
    b = (Path(c.execute('pragma database_list').fetchone()[2]).parent / 'blobs' / r['sha256']).read_bytes()
    if hashlib.sha256(b).hexdigest() != r['sha256']:
        raise ValueError('CORRUPT_EVIDENCE')
    return json.loads(b), dict(r)


def _verify(c, record):
    d, _ = _artifact(c, record['_artifact_id'])
    payload = {k: v for k, v in record.items()
               if k not in ('id', 'key', '_recorded_at', '_sha', '_artifact_id')}
    if d != payload or store.digest(d) != record['_sha']:
        raise ValueError('RECORD_BLOB_MISMATCH')
    return d


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pool(c, p):
    forecast, _ = _artifact(c, p['artifact_id'])
    frozen = [r for r in forecast.get('predictions', []) if r.get('code') == p['code']]
    if (len(frozen) != 1 or forecast.get('signal_date') != p['signal_date'] or
            forecast.get('information_cutoff') != p['cutoff'] or
            forecast.get('entry_at') != p['entry_at'] or forecast.get('exit_at') != p['exit_at']):
        raise ValueError('FORECAST_IDENTITY_MISMATCH')
    payload = json.loads(p['payload'])
    ref = forecast.get('candidate_pool')
    if not ref or ref != payload.get('candidate_pool'):
        raise ValueError('MISSING_FULL_CANDIDATE_POOL')
    pool, meta = _artifact(c, ref['artifact_id'])
    if meta['sha256'] != ref.get('sha256'):
        raise ValueError('POOL_HASH_MISMATCH')
    codes = [x['code'] for x in pool['candidates']]
    if (pool.get('signal_date') != p['signal_date'] or len(codes) != pool.get('expected_count') or
            len(codes) != len(set(codes)) or p['code'] not in codes or
            set(codes) != {x['code'] for x in forecast['predictions']} or
            any(x.get('board_stage') not in (2, 3) for x in pool['candidates'])):
        raise ValueError('POOL_IDENTITY_MISMATCH')
    if lab.stamp(pool['captured_at']) > lab.stamp(p['cutoff']) or lab.stamp(meta['imported_at']) > lab.stamp(p['recorded_at']):
        raise ValueError('POST_CUTOFF_POOL')
    source_ref = pool.get('source_ref')
    if not source_ref:
        raise ValueError('POOL_SOURCE_MISSING')
    _, source_meta = _artifact(c, source_ref['artifact_id'])
    if source_meta['sha256'] != source_ref.get('sha256') or lab.stamp(source_meta['imported_at']) > lab.stamp(p['recorded_at']):
        raise ValueError('POOL_SOURCE_INVALID_OR_POST_CUTOFF')
    return pool


def _snapshot(c, p, pool, snapshots, raw_by_id, pool_by_id):
    codes = {r['code'] for r in pool['candidates']}
    matches = []
    for s in snapshots:
        if (s.get('day') == p['signal_date'] and s.get('method') == 'daily_net_return_v1'
                and s.get('state') == 'COMPLETE' and s.get('provenance') == 'PROSPECTIVE_LOCAL'):
            matches.append(s)
    errors = []
    for s in sorted(matches, key=lambda r: (r.get('cutoff', ''), r['id'])):
        try:
            _verify(c, s)
            sc = [r['code'] for r in s['rows']]
            if (s.get('feature_schema') != features.VERSION or len(sc) != len(set(sc)) or
                    set(sc) != codes or s.get('expected_count') != len(codes) or
                    s['entry_at'] != p['entry_at'] or s['exit_at'] != p['exit_at']):
                raise ValueError('SNAPSHOT_POOL_OR_CONTRACT_MISMATCH')
            cutoff = lab.stamp(p['cutoff'])
            if not (lab.stamp(s['captured_at']) <= lab.stamp(s['cutoff']) <= lab.stamp(s['_recorded_at']) <= cutoff):
                raise ValueError('POST_CUTOFF_SNAPSHOT')
            if s.get('source_available_at') and lab.stamp(s['source_available_at']) > cutoff:
                raise ValueError('POST_CUTOFF_SOURCE')
            sp = pool_by_id.get(s.get('pool_id'))
            if sp is None:
                raise ValueError('SNAPSHOT_POOL_EVIDENCE_MISSING')
            _verify(c, sp)
            if (sp.get('signal_date') != p['signal_date'] or sp.get('expected_count') != len(codes) or
                    {r['code'] for r in sp.get('candidates', [])} != codes or
                    len(sp.get('candidates', [])) != len(codes)):
                raise ValueError('SNAPSHOT_POOL_IDENTITY_MISMATCH')
            if lab.stamp(sp['_recorded_at']) > cutoff:
                raise ValueError('POST_CUTOFF_SNAPSHOT_POOL')
            for row in s['rows']:
                x = row.get('features', {})
                if (row.get('state') != 'VALID_NUMERIC_BASELINE' or set(x) != set(features.FEATURES) or
                        not any(_number(v) for v in x.values()) or
                        any(v is not None and not _number(v) for v in x.values()) or not row.get('raw_ids')):
                    raise ValueError('INCOMPLETE_NUMERIC_FEATURE_VECTOR')
            refs = set(s.get('shared_raw_ids', []) + [i for row in s['rows'] for i in row['raw_ids']])
            if not s.get('shared_raw_ids'):
                raise ValueError('SHARED_FEATURE_EVIDENCE_MISSING')
            for rid in refs:
                raw = raw_by_id.get(rid)
                if raw is None:
                    raise ValueError('FEATURE_RAW_MISSING')
                _verify(c, raw)
                if (lab.stamp(raw['captured_at']) > lab.stamp(s['cutoff']) or
                        lab.stamp(raw['_recorded_at']) > cutoff):
                    raise ValueError('POST_CUTOFF_FEATURE_RAW')
            return s, next(r for r in s['rows'] if r['code'] == p['code']), []
        except (KeyError, TypeError, ValueError, OSError) as exc:
            errors.append(str(exc) if isinstance(exc, ValueError) else 'INVALID_SNAPSHOT_EVIDENCE')
    return None, None, sorted(set(errors)) or ['NO_PRE_CUTOFF_NUMERIC_SNAPSHOT']


def _quote_index(c, raws):
    index = defaultdict(list)
    invalid = set()
    for raw in raws:
        if raw.get('api') not in ('stk_auction', 'stk_mins', 'adj_factor', 'daily'):
            continue
        try:
            data = _verify(c, raw)
            if data.get('api') == 'stk_mins' and data.get('params', {}).get('freq') != '1min':
                continue
            for row in features.records(data.get('data', {})):
                key = (data['api'], row.get('ts_code'), row.get('trade_time') or row.get('trade_date'))
                index[key].append((raw['id'], row))
        except (KeyError, TypeError, ValueError, OSError):
            # Metadata cannot prove a price, but can invalidate an apparently clean pair.
            try:
                for row in features.records(raw.get('data', {})):
                    invalid.add((raw['api'], row.get('ts_code'), row.get('trade_time') or row.get('trade_date')))
            except (TypeError, ValueError):
                invalid.add((raw.get('api'), raw.get('params', {}).get('ts_code'), None))
    return index, invalid


def _quote(c, p, ledgers, index, invalid, raw_by_id, at):
    entryday = p['entry_at'][:10].replace('-', '')
    exitday = p['exit_at'][:10].replace('-', '')
    exitstamp = p['exit_at'][:19].replace('T', ' ')
    needed = [('stk_auction', entryday, ('price', 'pre_close', 'vol')),
              ('stk_mins', exitstamp, ('close', 'vol')),
              ('adj_factor', entryday, ('adj_factor',)), ('adj_factor', exitday, ('adj_factor',)),
              ('daily', entryday, ('open', 'high', 'low', 'close', 'vol')),
              ('daily', exitday, ('open', 'high', 'low', 'close', 'vol'))]
    if lab.stamp(p['exit_at']) > lab.stamp(at):
        return None, ['FUTURE_RESULT']
    observations = []
    for api, t, fields in needed:
        if (api, p['code'], t) in invalid or (api, p['code'], None) in invalid:
            return None, ['CORRUPT_QUOTE_EVIDENCE']
        obs, err = quote_ledger.observation(index, api, p['code'], t, fields)
        if err:
            return None, [err]
        observations.append(obs)
    er, xr = observations[0][1], observations[1][1]
    if abs(observations[2][1]['adj_factor'] - observations[3][1]['adj_factor']) > 1e-10:
        return None, ['CORPORATE_ACTION_REVIEW_REQUIRED']
    for price, daily in ((er['price'], observations[4][1]), (xr['close'], observations[5][1])):
        if not daily['low'] - .011 <= price <= daily['high'] + .011:
            return None, ['PRICE_OUTSIDE_DAILY_RANGE']
    if abs(er['price'] - observations[4][1]['open']) > .011:
        return None, ['AUCTION_DAILY_OPEN_CONFLICT']
    # Bind to the earliest immutable verification of this exact original forecast.
    ledger_errors = []
    for ledger in ledgers:
        rows = [r for r in ledger.get('rows', []) if r.get('prediction_id') == p['id']]
        if len(rows) != 1 or rows[0].get('quote_status') != 'VERIFIED_PRICE_PAIR':
            continue
        q = rows[0]
        try:
            _verify(c, ledger)
            if (q.get('code') != p['code'] or q.get('day') != p['signal_date'] or
                    q.get('entry_at') != p['entry_at'] or q.get('exit_at') != p['exit_at'] or
                    q.get('prediction_cutoff') != p['cutoff'] or q.get('entry_price') != er['price'] or
                    q.get('exit_price') != xr['close'] or not _number(q.get('gross_return')) or
                    abs(q['gross_return'] - (xr['close'] / er['price'] - 1)) > 1e-12):
                raise ValueError('QUOTE_LEDGER_IDENTITY_MISMATCH')
            # Use the evidence actually referenced by this ledger, not a later identical re-fetch.
            source = []
            for api, t, fields in needed:
                candidates = [(rid, row) for rid, row in index[(api, p['code'], t)]
                              if rid in q.get('evidence_ids', []) and all(quote_ledger.finite(row.get(k)) for k in fields)]
                if not candidates:
                    raise ValueError('QUOTE_LEDGER_RAW_BINDING_MISSING')
                source.append(min(candidates, key=lambda r: r[0])[0])
            times = [p['exit_at'], ledger['_recorded_at']]
            for rid in sorted(set(source)):
                raw = raw_by_id[rid]
                times.extend([raw['captured_at'], raw['_recorded_at']])
            available = max(times, key=lab.stamp)
            if lab.stamp(available) > lab.stamp(at):
                raise ValueError('FUTURE_LABEL_AVAILABILITY')
            return dict(status='VERIFIED_PRICE_PAIR', gross=q['gross_return'], label_id=ledger['id'],
                        label_available_at=available, quote_evidence_ids=sorted(set(source)),
                        quote_status=q['quote_status'], execution_risks=q.get('execution_risks', [])), []
        except (KeyError, TypeError, ValueError, OSError) as exc:
            ledger_errors.append(str(exc) if isinstance(exc, ValueError) else 'INVALID_QUOTE_LEDGER_EVIDENCE')
    return None, sorted(set(ledger_errors)) or ['NO_IMMUTABLE_VERIFIED_QUOTE_LEDGER']


def dataset(c, at=None):
    at = at or lab.now()
    snapshots = _records(c, 'snapshot')
    raws = _records(c, 'raw')
    raw_by_id = {r['id']: r for r in raws}
    pool_by_id = {r['id']: r for r in _records(c, 'pool')}
    ledgers = _records(c, 'quote_ledger')
    index, invalid = _quote_index(c, raws)
    by_day = defaultdict(list)
    for p in quote_ledger.canonical(c):
        by_day[p['signal_date']].append(p)
    candidates, rows, excluded, day_reports = [], [], [], []
    for day, predictions in sorted(by_day.items()):
        cohort = []
        expected = set()
        pool_identities = set()
        for p in predictions:
            r = dict(day=day, code=p['code'], prediction_id=p['id'], cutoff=p['cutoff'],
                     entry_at=p['entry_at'], exit_at=p['exit_at'], eligible=False,
                     quote_status='UNVERIFIED', feature_state='MISSING', reasons=[])
            payload = json.loads(p['payload'])
            bias = payload.get('metrics', {}).get('bias20_pct')
            r['frozen_descriptive_features'] = {'bias20': bias / 100} if _number(bias) else {}
            try:
                pool = _pool(c, p)
                codes = {x['code'] for x in pool['candidates']}
                expected.update(codes)
                pool_identities.add(tuple(sorted(codes)))
                s, member, errors = _snapshot(c, p, pool, snapshots, raw_by_id, pool_by_id)
                r['reasons'].extend(errors)
                if s:
                    r.update(snapshot_id=s['id'], x=member['features'], feature_state='PRE_CUTOFF_NUMERIC',
                             official_rank=payload.get('comparison_official_rank'))
            except (ValueError, OSError, KeyError, TypeError) as exc:
                r['reasons'].append(str(exc) if isinstance(exc, ValueError) else 'INVALID_FORECAST_OR_POOL_EVIDENCE')
            q, errors = _quote(c, p, ledgers, index, invalid, raw_by_id, at)
            r['reasons'].extend(errors)
            if q:
                r.update(q)
            cohort.append(r)
        observed = {r['code'] for r in cohort}
        for code in sorted(expected - observed):
            cohort.append(dict(day=day, code=code, prediction_id=None, eligible=False,
                               quote_status='UNVERIFIED', feature_state='MISSING',
                               reasons=['MISSING_ORIGINAL_FROZEN_PREDICTION']))
        day_reasons = set(reason for r in cohort for reason in r['reasons'])
        if not expected or observed != expected or len(pool_identities) != 1:
            day_reasons.add('INCOMPLETE_OR_INCONSISTENT_ORIGINAL_COHORT')
        complete = not day_reasons and bool(cohort)
        for r in cohort:
            r['eligible'] = complete
            r['cohort_state'] = 'COMPLETE' if complete else 'DESCRIPTIVE_ONLY'
            if complete:
                rows.append({k: v for k, v in r.items() if k not in ('eligible', 'reasons', 'frozen_descriptive_features', 'cohort_state')})
        candidates.extend(cohort)
        day_report = dict(day=day, state='COMPLETE' if complete else 'DESCRIPTIVE_ONLY',
                          expected_count=len(expected) if expected else None, original_predictions=len(predictions),
                          verified_quote_rows=sum(r['quote_status'] == 'VERIFIED_PRICE_PAIR' for r in cohort),
                          complete_feature_rows=sum(r['feature_state'] == 'PRE_CUTOFF_NUMERIC' for r in cohort),
                          eligible_rows=len(cohort) if complete else 0, reasons=sorted(day_reasons))
        day_reports.append(day_report)
        if not complete:
            excluded.append(dict(day=day, reason=';'.join(sorted(day_reasons)), expected_count=day_report['expected_count']))
    data = dict(version=VERSION, method=METHOD, basis=BASIS, scope=SCOPE, rows=rows,
                candidates=candidates, days=day_reports, excluded=excluded,
                selection='First local prospective forecast per D/stock; full original pool only; earliest verified pre-cutoff numeric snapshot',
                calibrates_original_probability=False, production_promotion=False)
    data['summary'] = dict(candidate_rows=len(candidates), original_predictions=sum(len(p) for p in by_day.values()),
                           verified_quote_rows=sum(r['quote_status'] == 'VERIFIED_PRICE_PAIR' for r in candidates),
                           complete_feature_rows=sum(r['feature_state'] == 'PRE_CUTOFF_NUMERIC' for r in candidates),
                           eligible_rows=len(rows), eligible_days=len({r['day'] for r in rows}),
                           descriptive_only_days=sum(d['state'] != 'COMPLETE' for d in day_reports),
                           excluded_rows=len(candidates) - len(rows),
                           missing_reasons=dict(Counter(x for r in candidates for x in set(r['reasons']))))
    data['fingerprint'] = store.digest(data)
    return data


def run(c, day):
    """Refresh evidence/readiness every run; fit only at the existing weekly cadence."""
    from learning import trainer
    p = policy()
    store.put(c, 'quote_policy', p['version'], p)
    quote_ledger.run(c)
    data = dataset(c)
    did = store.put(c, 'quote_dataset', data['fingerprint'], data)
    days = sorted({r['day'] for r in data['rows']})
    cycles = _records(c, 'quote_training_cycle')
    last = cycles[-1] if cycles else None
    new_days = sorted(set(days) - set(last['days'] if last else []))
    due = last is None or (last['fingerprint'] != data['fingerprint'] and
            (datetime.strptime(day, '%Y%m%d') - datetime.strptime(last['day'], '%Y%m%d')).days >= 7 and len(new_days) >= 5)
    if due:
        code_sha = store.digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                 hashlib.sha256(Path(trainer.__file__).read_bytes()).hexdigest()])
        key = store.digest([p, data['fingerprint'], code_sha])
        existing = [m for m in _records(c, 'quote_model') if m['key'] == key]
        if existing:
            model = existing[-1]
        else:
            model = trainer.fit_dataset(data, p)
            model.update(method=METHOD, scope=SCOPE, model_kind='quote_model', created_at=lab.now(),
                         policy_sha=store.digest(p), dataset_id=did, code_sha=code_sha,
                         source_ids=sorted({r['snapshot_id'] for r in data['rows']} | {r['label_id'] for r in data['rows']}),
                         calibrates_original_probability=False, production_promotion=False, promotion='DISABLED_QUOTE_RESEARCH_ONLY')
            model['id'] = store.put(c, 'quote_model', key, model)
        cycle = dict(day=day, days=days, fingerprint=data['fingerprint'], dataset_id=did, model_id=model['id'])
        store.put(c, 'quote_training_cycle', store.digest(cycle), cycle)
        training = dict(state=model['state'], model_id=model['id'], attempted=True, new_eligible_days=len(new_days))
    else:
        training = dict(state='WAITING_NEW_DATA_OR_WEEKLY_BUDGET', model_id=last['model_id'], attempted=False,
                        new_eligible_days=len(new_days), interval_days=7, required_new_days=5)
    report = dict(day=day, method=METHOD, basis=BASIS, scope=SCOPE, dataset_id=did,
                  dataset_fingerprint=data['fingerprint'], summary=data['summary'], days=data['days'],
                  excluded=data['excluded'], training=training, production_promotion=False,
                  calibrates_original_probability=False,
                  note='报价学习与真实成交结算独立；完整日期才训练，部分日期保留缺失原因。')
    report['record_id'] = store.put(c, 'quote_learning_report', store.digest(report), report)
    return report


def shadow(c, model_id, snapshot_id):
    """A new prospective quote forecast; never modifies an original prediction."""
    from learning import trainer
    models = {m['id']: m for m in _records(c, 'quote_model')}
    snapshots = {s['id']: s for s in _records(c, 'snapshot')}
    if model_id not in models or snapshot_id not in snapshots:
        raise ValueError('QUOTE_MODEL_OR_SNAPSHOT_MISSING')
    m, s = models[model_id], snapshots[snapshot_id]
    _verify(c, m)
    _verify(c, s)
    if (m.get('method') != METHOD or m.get('state') != 'TRAINED_RESEARCH_ONLY' or
            m.get('basis') != BASIS or s.get('state') != 'COMPLETE' or
            s.get('provenance') != 'PROSPECTIVE_LOCAL' or s.get('method') != 'daily_net_return_v1'):
        raise ValueError('QUOTE_SHADOW_NOT_READY')
    if not (max(lab.stamp(m['created_at']), lab.stamp(m['_recorded_at'])) < lab.stamp(s['cutoff']) <= lab.stamp(s['_recorded_at']) <
            lab.stamp(s['entry_at']) and lab.stamp(lab.now()) < lab.stamp(s['entry_at'])):
        raise ValueError('QUOTE_SHADOW_NOT_PRE_ENTRY')
    last_available = m['bundle'].get('last_fit_available_at')
    if (not last_available or lab.stamp(last_available) >= lab.stamp(s['cutoff']) or
            s['day'] <= max(m['test_days'])):
        raise ValueError('QUOTE_SHADOW_NOT_UNSEEN')
    # Validate the same complete numeric evidence contract against this new cutoff.
    pools = {r['id']: r for r in _records(c, 'pool')}
    pool = pools.get(s.get('pool_id'))
    if pool is None or not s.get('rows'):
        raise ValueError('QUOTE_SHADOW_POOL_MISSING')
    raw_by_id = {r['id']: r for r in _records(c, 'raw')}
    prospective = dict(signal_date=s['day'], code=s['rows'][0]['code'], cutoff=s['_recorded_at'],
                       entry_at=s['entry_at'], exit_at=s['exit_at'])
    bound, _, errors = _snapshot(c, prospective, pool, [s], raw_by_id, pools)
    if bound is None:
        raise ValueError('QUOTE_SHADOW_EVIDENCE:' + ';'.join(errors))
    if any(lab.stamp(raw_by_id[rid]['_recorded_at']) > lab.stamp(s['cutoff']) for rid in
           s.get('shared_raw_ids', []) + [i for row in s['rows'] for i in row['raw_ids']]):
        raise ValueError('QUOTE_SHADOW_POST_CUTOFF_RAW')
    key = store.digest([model_id, snapshot_id])
    existing = [r for r in _records(c, 'quote_shadow') if r['key'] == key]
    if existing:
        return existing[-1]['id']
    rows = [dict(day=s['day'], code=r['code'], x=r['features']) for r in s['rows']]
    if m.get('research_proposal_id'):
        from learning import research_agent
        rows = research_agent.shadow_rows(c, m, s)
    predictions = trainer.predict(rows, m['bundle'])
    for row in predictions:
        row.update(status='QUOTE_SHADOW_UNVALIDATED', scope=SCOPE,
                   probability_target='P_QUOTE_NET_POSITIVE_NOT_EXECUTABLE_PROFIT')
    report = dict(model_id=model_id, snapshot_id=snapshot_id, method=METHOD, basis=BASIS,
                  at=lab.now(), cutoff=s['cutoff'], predictions=predictions, scope=SCOPE,
                  state='QUOTE_SHADOW_UNVALIDATED', calibrates_original_probability=False,
                  production_promotion=False)
    return store.put(c, 'quote_shadow', key, report)


def latest(c):
    """Read the last saved readiness report without refreshing or writing evidence."""
    reports = _records(c, 'quote_learning_report')
    if not reports:
        return dict(state='NOT_RUN', method=METHOD, basis=BASIS, scope=SCOPE,
                    production_promotion=False, calibrates_original_probability=False)
    last = reports[-1]
    try:
        body = _verify(c, last)
    except (ValueError, KeyError, TypeError, OSError):
        return dict(state='EVIDENCE_INVALID', method=METHOD, basis=BASIS, scope=SCOPE,
                    report_id=last['id'], reason='QUOTE_LEARNING_REPORT_INTEGRITY_FAILED',
                    production_promotion=False, calibrates_original_probability=False)
    return dict(body, record_id=last['id'], recorded_at=last['_recorded_at'])
