"""Research-only runtime acceptance receipts; no data provider or model writes.

A scheduled replay is not evidence of newly collected forward data. A natural
receipt requires the frozen D snapshot and minute observations to have first
been collected in this run's real UTC window before the calendar-derived T
09:25 deadline. This is an operational receipt, not independent PIT admission.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

SCHEMA = 'dc20_research_operation_v1'
OUT = 'outputs/decision/profit_research/operations/receipts'
CALENDAR_OUT = 'outputs/decision/profit_research/operations/calendars'
CALENDAR = 'data/market/trade_cal_sse.csv'
SHANGHAI = ZoneInfo('Asia/Shanghai')
NATURAL_EVENTS = {'schedule', 'workflow_run'}
CONTROLLED_EVENTS = {'push', 'workflow_dispatch'}
MINUTE_KEYS = ('minute_realized_vol', 'minute_first_30m_return', 'minute_last_30m_return')
MAX_RECEIPTS = 5000
REPOSITORY = 'njedu2023-prog/DC20'
UPSTREAM = 'DC20 · Publish active candidate profit'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _utc_now():
    return datetime.now(timezone.utc)


def _instant(value):
    if not isinstance(value, str):
        raise ValueError('TIMESTAMP_REQUIRED')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return stamp.astimezone(timezone.utc)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'20\d{6}', value):
        raise ValueError('INVALID_CALENDAR_DATE')
    return datetime.strptime(value, '%Y%m%d').date()


def _time_on(date, clock):
    return datetime.strptime(date + clock, '%Y%m%d%H%M').replace(tzinfo=SHANGHAI)


def _safe_path(root, relative):
    rel = Path(relative)
    path = root / rel
    if rel.is_absolute() or '..' in rel.parts or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('UNSAFE_RESEARCH_OPERATION_PATH')
    return path


def _bound(root, binding):
    if not isinstance(binding, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('sha256', ''))):
        raise ValueError('INVALID_SOURCE_BINDING')
    raw = _safe_path(root, binding['path']).read_bytes()
    if sha(raw) != binding['sha256']:
        raise ValueError('SOURCE_BINDING_HASH_MISMATCH')
    return json.loads(raw)


def _public_binding(binding):
    return {'path': binding['path'], 'sha256': binding['sha256']}


def _calendar(root, observed, snapshot_binding=None):
    if snapshot_binding is not None:
        if not re.fullmatch(CALENDAR_OUT + r'/[0-9a-f]{64}\.json', snapshot_binding.get('path', '')):
            raise ValueError('INVALID_CALENDAR_SNAPSHOT_PATH')
        snapshot = _bound(root, snapshot_binding)
        if snapshot.get('schema_version') != 'dc20_operation_calendar_v1':
            raise ValueError('INVALID_CALENDAR_SNAPSHOT')
        rows = [{'cal_date': row[0], 'is_open': str(row[1])} for row in snapshot['calendar_entries']]
        source_binding = _public_binding(snapshot['source_binding'])
    else:
        path = _safe_path(root, CALENDAR)
        raw = path.read_bytes()
        rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
        source_binding = {'path': CALENDAR, 'sha256': sha(raw)}
    entries = {}
    for row in rows:
        if row.get('exchange', 'SSE') not in ('SSE', ''):
            continue
        date = row.get('cal_date', row.get('trade_date'))
        _date(date)
        if date in entries or str(row.get('is_open')) not in ('0', '1'):
            raise ValueError('INVALID_OR_DUPLICATE_CALENDAR_ROW')
        entries[date] = str(row['is_open']) == '1'
    if not entries:
        raise ValueError('CALENDAR_EMPTY')
    dates = sorted(entries)
    first, last = _date(dates[0]), _date(dates[-1])
    if len(entries) != (last - first).days + 1:
        raise ValueError('CALENDAR_RANGE_GAP')
    today = observed.astimezone(SHANGHAI).strftime('%Y%m%d')
    if today not in entries:
        raise ValueError('CALENDAR_OUT_OF_DATE')
    opens = [date for date in dates if entries[date]]
    closed = [date for date in opens if _time_on(date, '1500') <= observed]
    if not closed:
        raise ValueError('NO_CLOSED_TRADING_DAY')
    latest = closed[-1]
    nexts = {date: opens[i + 1] for i, date in enumerate(opens[:-1])}
    if latest not in nexts:
        raise ValueError('CALENDAR_NEXT_SESSION_UNKNOWN')
    snapshot = {'schema_version': 'dc20_operation_calendar_v1', 'source_binding': source_binding,
                'calendar_entries': [[date, int(entries[date])] for date in dates]}
    digest = sha(encoded(snapshot))
    binding = {'path': f'{CALENDAR_OUT}/{digest}.json', 'sha256': digest}
    if snapshot_binding is not None and binding != _public_binding(snapshot_binding):
        raise ValueError('NONCANONICAL_CALENDAR_SNAPSHOT')
    return {'binding': binding, 'source_binding': source_binding, '_snapshot': snapshot,
            'coverage_start': dates[0], 'coverage_end': dates[-1],
            'opens': opens, 'closed': closed, 'nexts': nexts, 'latest': latest}


def _context(raw, observed, test_only):
    event = raw.get('event_name')
    context = {
        'event_name': event if event in NATURAL_EVENTS | CONTROLLED_EVENTS else 'unattested',
        'run_id': None, 'run_attempt': None, 'github_head_sha': None,
        'source_revision': None, 'run_started_at_utc': None,
        'repository': None, 'ref_name': None,
    }
    errors = []
    if raw.get('repository') == REPOSITORY:
        context['repository'] = REPOSITORY
    else:
        errors.append('UNEXPECTED_REPOSITORY')
    if raw.get('ref_name') == 'main':
        context['ref_name'] = 'main'
    else:
        errors.append('UNEXPECTED_BRANCH')
    if event == 'workflow_run':
        upstream = raw.get('upstream', {})
        allowed = {'name': UPSTREAM, 'conclusion': 'success', 'head_branch': 'main', 'repository': REPOSITORY}
        if isinstance(upstream, dict) and all(upstream.get(k) == v for k, v in allowed.items()):
            context['upstream'] = allowed
        else:
            context['upstream'] = None
            errors.append('UPSTREAM_WORKFLOW_NOT_ACCEPTED')
    for key in ('run_id', 'run_attempt'):
        value = str(raw.get(key, ''))
        if re.fullmatch(r'[1-9]\d{0,19}', value):
            context[key] = value
        else:
            errors.append('MISSING_OR_INVALID_' + key.upper())
    for key in ('github_head_sha', 'source_revision'):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value):
            context[key] = value
        else:
            errors.append('MISSING_OR_INVALID_' + key.upper())
    try:
        start = _instant(raw.get('run_started_at_utc'))
        if start > observed or observed - start > timedelta(hours=24):
            raise ValueError('INVALID_RUN_TIME_WINDOW')
        context['run_started_at_utc'] = start.isoformat()
    except (ValueError, TypeError):
        errors.append('MISSING_OR_INVALID_RUN_TIME_WINDOW')
    if event not in NATURAL_EVENTS | CONTROLLED_EVENTS:
        errors.append('UNATTESTED_EVENT')
    context['trigger_class'] = ('TEST_ONLY' if test_only else
        'NATURAL_TRIGGER' if event in NATURAL_EVENTS else
        'CONTROLLED_TRIGGER' if event in CONTROLLED_EVENTS else 'UNATTESTED')
    return context, errors


def _storage(configured, summary):
    # Configured storage and counts supplied by a caller are not independent
    # verification that provider originals are durably and lawfully archived.
    result = {'status': 'CONFIGURED_UNVERIFIED' if configured else 'NOT_CONFIGURED',
              'independently_verified': False, 'expected_hashes': None, 'covered_hashes': None}
    if isinstance(summary, dict):
        for key in ('expected_hashes', 'covered_hashes'):
            value = summary.get(key)
            if type(value) is int and value >= 0:
                result[key] = value
        if summary.get('status') in {'NOT_CONFIGURED', 'CONFIGURED_UNVERIFIED', 'PARTIAL', 'REPORTED_COMPLETE'}:
            result['reported_status'] = summary['status']
    return result


def _frozen_membership(root, day, records):
    date = day['date']
    bindings = day.get('source_bindings', [])
    p0_binding = next(b for b in bindings if b.get('path') == f'outputs/decision/three_rank_top10_{date}.json')
    snapshot_binding = next(b for b in bindings if b.get('path') == f'work/profit_1000_upgrade/candidate_natural_forward/day_{date}.json')
    # Verify the immutable P0 and candidate snapshot, not the public outcome
    # projection which legitimately changes as T/T+1 labels become available.
    permitted = {p0_binding['path'], snapshot_binding['path'],
                 f'outputs/decision/candidate_profit_v1/day_{date}.json'}
    if any(b.get('path') not in permitted for b in bindings):
        raise ValueError('UNEXPECTED_FROZEN_BINDING_PATH')
    sources = {b['path']: _bound(root, b) for b in (p0_binding, snapshot_binding)}
    p0, snapshot = sources[p0_binding['path']], sources[snapshot_binding['path']]
    if p0.get('signal_date') != date or snapshot.get('signal_date', date) != date:
        raise ValueError('FROZEN_SOURCE_DATE_MISMATCH')
    groups = ([r.get('ts_code') for r in records],
              [r.get('ts_code') for r in p0['rows']],
              [r.get('ts_code') for r in snapshot['D_source_evidence']['projection']['rows']])
    if not groups[0] or len(groups[0]) > 10:
        raise ValueError('INVALID_FROZEN_CANDIDATE_COUNT')
    if any(any(not isinstance(c, str) or not c for c in group) or len(group) != len(set(group)) for group in groups):
        raise ValueError('DUPLICATE_OR_INVALID_FROZEN_MEMBER')
    if any(set(group) != set(groups[0]) for group in groups[1:]):
        raise ValueError('FROZEN_MEMBERSHIP_MISMATCH')
    return [_public_binding(p0_binding), _public_binding(snapshot_binding)]


def _valid_timestamp(value, cutoff, observed):
    try:
        stamp = _instant(value)
        return stamp if stamp < cutoff and stamp <= observed else None
    except (ValueError, TypeError):
        return None


def _record_timing(root, record, fields, cutoff, observed, run_start, source_revision):
    ext = record.get('research_extensions', {})
    enriched = record.get('research_enrichment', {})
    bindings = []
    try:
        ext_binding = ext['snapshot_binding']
        if not re.fullmatch(r'outputs/decision/profit_research/extensions/' + record['signal_date'] + r'/[0-9a-f]{64}\.json', ext_binding.get('path', '')):
            raise ValueError('UNEXPECTED_EXTENSION_BINDING_PATH')
        original = _bound(root, ext_binding)
        original_payload = original['payload']
        if (original_payload.get('schema_version') != 'dc20_research_extensions_v1'
                or original_payload.get('signal_date') != record['signal_date']
                or original_payload.get('source_generated_at_utc') != ext.get('source_generated_at_utc')
                or sha(encoded(original_payload)) != original.get('payload_sha256')
                or Path(ext_binding['path']).stem != original.get('payload_sha256')):
            raise ValueError('EXTENSION_METADATA_MISMATCH')
        if original['first_collected_at_utc'] != ext['first_collected_at_utc']:
            raise ValueError('EXTENSION_COLLECTION_TIME_MISMATCH')
        bound_rows = {r['ts_code']: r for r in original['payload']['records']}
        frozen_values = bound_rows[record['ts_code']]['values']
        bindings.append(_public_binding(ext_binding))
        enrichment_payload = None
        if enriched:
            e_binding = enriched['snapshot_binding']
            expected = (r'outputs/decision/profit_research/enrichment/' + record['signal_date']
                        + '/' + re.escape(record['ts_code']) + r'/[0-9a-f]{64}\.json')
            if not re.fullmatch(expected, e_binding.get('path', '')):
                raise ValueError('UNEXPECTED_ENRICHMENT_BINDING_PATH')
            envelope = _bound(root, e_binding)
            enrichment_payload = envelope['payload']
            if (enrichment_payload.get('signal_date') != record['signal_date']
                    or enrichment_payload.get('ts_code') != record['ts_code']
                    or enrichment_payload.get('schema_version') != 'dc20_research_enrichment_v1'
                    or enrichment_payload.get('minute_quality') != enriched.get('minute_quality')
                    or sha(encoded(enrichment_payload)) != envelope.get('payload_sha256')
                    or Path(e_binding['path']).stem != envelope.get('payload_sha256')):
                raise ValueError('ENRICHMENT_IDENTITY_MISMATCH')
            bindings.append(_public_binding(e_binding))
        collected = _valid_timestamp(ext.get('first_collected_at_utc'), cutoff, observed)
        generated = _valid_timestamp(ext.get('source_generated_at_utc'), cutoff, observed)
        close = _time_on(record['signal_date'], '1500')
        all_before = bool(collected and generated and collected >= close and generated >= close)
        # First research snapshot is required to belong to this run, preventing
        # an old D from becoming "new" merely because schedule reread it later.
        all_new = bool(all_before and run_start and collected >= run_start
                       and original.get('first_collected_revision') == source_revision)
        for key in fields:
            value = ext.get('values', {}).get(key)
            if value is None:
                all_before = all_new = False
                continue
            if key in ext.get('value_sources', {}):
                source = enriched.get('field_sources', {}).get(key, {})
                if not enrichment_payload or enrichment_payload.get('values', {}).get(key) != value or enrichment_payload.get('field_sources', {}).get(key) != source:
                    raise ValueError('ENRICHMENT_VALUE_OR_SOURCE_MISMATCH')
                stamp = _valid_timestamp(source.get('observed_at_utc'), cutoff, observed)
                event_date = source.get('source_event_date')
                before = bool(stamp and stamp >= close and event_date == record['signal_date']
                              and _time_on(event_date, '1500') < cutoff
                              and source.get('source_timestamp_before_entry') is True
                              and source.get('collection_kind') == 'BEFORE_ENTRY_COLLECTION')
                all_before = all_before and before
                all_new = bool(all_new and before and stamp >= run_start
                               and envelope.get('first_collected_revision') == source_revision)
            elif frozen_values.get(key) != value:
                raise ValueError('EXTENSION_BOUND_VALUE_MISMATCH')
        return all_before, all_new, bindings, None
    except (OSError, ValueError, TypeError, KeyError, StopIteration):
        return False, False, bindings, 'TIMING_EVIDENCE_UNAVAILABLE_OR_REJECTED'


def _audit_day(root, date, next_date, day, observed, run_start, source_revision):
    from scripts.profit_research_extensions import catalogue
    from scripts.profit_research_comparison import SETTLED, NO_FILL
    fields = [field['key'] for field in catalogue()]
    cutoff = _time_on(next_date, '0925')
    result = {'signal_date': date, 'exec_date': next_date,
        'collection_deadline_utc': cutoff.astimezone(timezone.utc).isoformat(),
        'deadline_passed': observed >= cutoff, 'candidate_records': 0,
        'frozen_membership_matched': False, 'frozen_feature_records': 0,
        'feature_field_count': len(fields), 'feature_complete_records': 0,
        'minute_complete_records': 0, 'before_entry_records': 0,
        'new_in_this_run_records': 0,
        'label_counts': {'settled': 0, 'no_fill': 0, 'pending': 0, 'missing_or_invalid': 0},
        'status': 'MISSING_FROZEN_D', 'reasons': [], 'source_bindings': []}
    if day is None:
        result['reasons'] = ['EXPECTED_TRADING_D_NOT_IN_DATASET']
        return result
    records = day.get('records', [])
    result['candidate_records'] = len(records)
    try:
        if day.get('provenance') != 'FORMAL_FROZEN_REFERENCE':
            raise ValueError('NO_FORMAL_FROZEN_DAY')
        result['source_bindings'] = _frozen_membership(root, day, records)
        result['frozen_membership_matched'] = True
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        result['reasons'].append('FROZEN_BINDING_OR_MEMBERSHIP_REJECTED')
    for record in records:
        result['frozen_feature_records'] += record.get('feature_snapshot_present') is True
        ext = record.get('research_extensions', {})
        values = ext.get('values', {})
        complete = all((isinstance(values.get(k), str) and bool(values[k].strip())) if k in ('path_label', 'path_explanation')
                       else (type(values.get(k)) in (int, float) and math.isfinite(values[k])) for k in fields)
        result['feature_complete_records'] += complete
        enrichment = record.get('research_enrichment', {})
        quality = enrichment.get('minute_quality') or {}
        minutes = (all(ext.get('values', {}).get(k) is not None for k in MINUTE_KEYS)
                   and quality.get('session_complete') is True
                   and quality.get('required_minute_bars') == 240
                   and quality.get('missing_required_bars') == 0
                   and quality.get('valid_minute_bars') in (240, 241))
        result['minute_complete_records'] += minutes
        before, newly, bindings, issue = _record_timing(root, record, fields, cutoff, observed, run_start, source_revision)
        result['before_entry_records'] += bool(before)
        result['new_in_this_run_records'] += bool(newly and minutes)
        result['source_bindings'].extend(bindings)
        if issue:
            result['reasons'].append(issue)
        if record.get('exec_date') != next_date:
            result['reasons'].append('EXEC_DATE_DISAGREES_WITH_EXCHANGE_CALENDAR')
        outcome = record.get('outcome', {})
        status = outcome.get('status', '')
        value, fill = outcome.get('net_return'), outcome.get('proxy_fill')
        if status == SETTLED and type(value) in (int, float) and math.isfinite(value) and type(fill) is int and fill == 1:
            bucket = 'settled'
        elif status in NO_FILL and value == 0 and type(fill) is int and fill == 0:
            bucket = 'no_fill'
        elif status in ('PENDING_T', 'PENDING_T1') and value is None:
            bucket = 'pending'
        else:
            bucket = 'missing_or_invalid'
        result['label_counts'][bucket] += 1
    total = result['candidate_records']
    collected = (bool(total) and result['frozen_membership_matched']
                 and all(result[k] == total for k in ('frozen_feature_records', 'feature_complete_records', 'minute_complete_records'))
                 and not result['reasons'])
    if not collected:
        result['status'] = 'OVERDUE_COLLECTION_GAP' if result['deadline_passed'] else 'COLLECTION_WINDOW_OPEN_WITH_GAPS'
    elif result['before_entry_records'] != total:
        result['status'] = 'COMPLETE_WITH_LATE_OR_UNVERIFIED_COLLECTION'
    else:
        result['status'] = 'COMPLETE_BEFORE_ENTRY'
    result['collection_complete'] = collected
    result['new_natural_eligible'] = bool(collected and observed < cutoff and result['before_entry_records'] == total
                                        and result['new_in_this_run_records'] == total)
    result['reasons'] = sorted(set(result['reasons']))
    result['source_bindings'] = list({b['path'] + ':' + b['sha256']: b for b in result['source_bindings']}.values())
    return result


def build_operation_receipt(root, doc, *, run_context, observed_at=None,
                            private_storage_configured=False, storage_summary=None, test_only=False):
    """Audit at actual UTC time; any supplied clock is forced to TEST_ONLY."""
    root = Path(root).resolve()
    observed = _instant(observed_at) if observed_at is not None else _utc_now()
    test_only = bool(test_only or observed_at is not None)
    return _build_receipt_at(root, doc, run_context, observed, private_storage_configured,
                             storage_summary, test_only)


def _build_receipt_at(root, doc, run_context, observed, private_storage_configured, storage_summary, test_only,
                      calendar_binding=None):
    """Internal deterministic replay; public recording obtains a real clock."""
    context, errors = _context(run_context, observed, test_only)
    if doc.get('source_revision') != context['source_revision']:
        errors.append('DATASET_SOURCE_REVISION_MISMATCH')
    dataset_sha = sha(encoded(doc))
    receipt = {'schema_version': SCHEMA, 'observed_at_utc': observed.isoformat(),
        'dataset_sha256': dataset_sha,
        'dataset_binding': {'path': f'outputs/decision/profit_research/versions/{dataset_sha}.json', 'sha256': dataset_sha},
        'run_context': context, 'test_only': test_only, 'source_revision': doc.get('source_revision'),
        'data_as_of_date': doc.get('as_of_date'), 'model_sha256': doc.get('model', {}).get('sha256'),
        'training_admitted_days': 0, 'training_allowed': False,
        'production_model_changed': False, 'point_in_time_independently_verified': False,
        'private_storage': _storage(private_storage_configured, storage_summary),
        'calendar': None, 'latest_closed_d': None, 'latest_due_d': None,
        'days': [], 'natural_new_d_evidence': [], 'errors': errors,
        'status': 'ACCEPTANCE_FAILED', 'natural_evidence_status': 'NO_NATURAL_NEW_D_EVIDENCE'}
    try:
        cal = _calendar(root, observed, calendar_binding)
        receipt['calendar'] = {k: cal[k] for k in ('binding', 'source_binding', 'coverage_start', 'coverage_end')}
        start = doc.get('start_date') or min((d['date'] for d in doc.get('days', [])), default=cal['latest'])
        _date(start)
        grouped = {}
        for day in doc.get('days', []):
            if day['date'] not in cal['opens']:
                raise ValueError('DATASET_DAY_NOT_IN_EXCHANGE_CALENDAR')
            if day['date'] in grouped:
                raise ValueError('DUPLICATE_DATASET_DAY')
            grouped[day['date']] = day
        run_start = _instant(context['run_started_at_utc']) if context['run_started_at_utc'] else None
        expected = [date for date in cal['closed'] if date >= start]
        if not expected:
            raise ValueError('NO_EXPECTED_D_WITHIN_RESEARCH_WINDOW')
        receipt['days'] = [_audit_day(root, date, cal['nexts'][date], grouped.get(date), observed, run_start, context['source_revision'])
                           for date in expected]
        receipt['latest_closed_d'] = receipt['days'][-1]
        due = [day for day in receipt['days'] if day['deadline_passed']]
        receipt['latest_due_d'] = due[-1] if due else None
        if context['trigger_class'] == 'NATURAL_TRIGGER' and not errors:
            latest = receipt['latest_closed_d']
            if latest.get('new_natural_eligible'):
                receipt['natural_new_d_evidence'] = [latest['signal_date']]
                receipt['natural_evidence_status'] = 'NATURAL_BEFORE_ENTRY_RECEIPT'
            elif latest.get('collection_complete'):
                receipt['natural_evidence_status'] = 'NATURAL_REUSED_NOT_NEW'
            else:
                receipt['natural_evidence_status'] = 'NATURAL_COLLECTION_INCOMPLETE'
        elif context['trigger_class'] == 'CONTROLLED_TRIGGER':
            receipt['natural_evidence_status'] = 'CONTROLLED_RUN_NOT_NATURAL_EVIDENCE'
        latest = receipt['latest_closed_d']
        overdue = sum(day['status'] in ('OVERDUE_COLLECTION_GAP', 'MISSING_FROZEN_D') and day['deadline_passed'] for day in receipt['days'])
        receipt['counts'] = {'expected_closed_days': len(expected), 'overdue_gap_days': overdue,
            'late_or_unverified_complete_days': sum(day['status'] == 'COMPLETE_WITH_LATE_OR_UNVERIFIED_COLLECTION' for day in receipt['days']),
            'complete_before_entry_days': sum(day['status'] == 'COMPLETE_BEFORE_ENTRY' for day in receipt['days'])}
        receipt['status'] = ('ACCEPTANCE_FAILED' if errors else 'OVERDUE_COLLECTION_GAP' if overdue
                             else 'COLLECTION_INCOMPLETE' if not latest.get('collection_complete')
                             else 'COMPLETE_WITH_WARNINGS')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Error text is reduced to a closed internal code, never source bodies.
        code = str(exc)
        receipt['errors'].append(code if re.fullmatch(r'[A-Z_]+', code) else 'CALENDAR_OR_ACCEPTANCE_INPUT_REJECTED')
    receipt['notices'] = []
    if not private_storage_configured:
        receipt['notices'].append('PRIVATE_STORAGE_NOT_CONFIGURED')
    else:
        receipt['notices'].append('PRIVATE_STORAGE_NOT_VERIFIED')
    if not receipt['natural_new_d_evidence']:
        receipt['notices'].append('NO_NATURAL_NEW_D_EVIDENCE_IN_THIS_RUN')
    receipt['notices'].append('OPERATIONAL_RECEIPT_IS_NOT_INDEPENDENT_PIT_OR_TRAINING_ADMISSION')
    return receipt


def write_operation_receipt(root, receipt):
    root = Path(root).resolve()
    if receipt.get('schema_version') != SCHEMA:
        raise ValueError('INVALID_OPERATION_SCHEMA')
    doc = _bound(root, receipt['dataset_binding'])
    if sha(encoded(doc)) != receipt.get('dataset_sha256'):
        raise ValueError('OPERATION_DATASET_IDENTITY_MISMATCH')
    if receipt.get('calendar') is not None:
        cal = _calendar(root, _instant(receipt['observed_at_utc']))
        if cal['binding'] != receipt['calendar']['binding']:
            raise ValueError('CALENDAR_CHANGED_DURING_ACCEPTANCE')
        calendar_path = _safe_path(root, cal['binding']['path'])
        calendar_raw = encoded(cal['_snapshot'])
        if calendar_path.exists():
            if calendar_path.read_bytes() != calendar_raw:
                raise ValueError('IMMUTABLE_CALENDAR_SNAPSHOT_CHANGED')
        else:
            calendar_path.parent.mkdir(parents=True, exist_ok=True)
            _safe_path(root, cal['binding']['path'])
            with calendar_path.open('xb') as stream:
                stream.write(calendar_raw)
    raw = encoded(receipt)
    digest = sha(raw)
    day = _instant(receipt['observed_at_utc']).strftime('%Y%m%d')
    relative = f'{OUT}/{day}/{digest}.json'
    path = _safe_path(root, relative)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('IMMUTABLE_OPERATION_RECEIPT_CHANGED')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _safe_path(root, relative)
        with path.open('xb') as stream:
            stream.write(raw)
    return {'path': relative, 'sha256': digest}


def _revalidate_receipt(root, receipt):
    binding = receipt.get('dataset_binding', {})
    if not re.fullmatch(r'outputs/decision/profit_research/versions/[0-9a-f]{64}\.json', binding.get('path', '')):
        raise ValueError('INVALID_OPERATION_DATASET_PATH')
    doc = _bound(root, binding)
    if sha(encoded(doc)) != receipt.get('dataset_sha256') or Path(binding['path']).stem != receipt.get('dataset_sha256'):
        raise ValueError('OPERATION_DATASET_IDENTITY_MISMATCH')
    storage = receipt.get('private_storage', {})
    storage_input = {k: storage[k] for k in ('expected_hashes', 'covered_hashes') if k in storage}
    if storage.get('reported_status') is not None:
        storage_input['status'] = storage['reported_status']
    replay = _build_receipt_at(root, doc, receipt['run_context'], _instant(receipt['observed_at_utc']),
        storage.get('status') == 'CONFIGURED_UNVERIFIED', storage_input, receipt['test_only'],
        (receipt.get('calendar') or {}).get('binding'))
    # The whole canonical object must match: a freshly hashed false natural
    # flag, injected metadata, a zeroed gap count or a changed clock is rejected.
    if replay != receipt:
        raise ValueError('OPERATION_RECEIPT_REPLAY_MISMATCH')
    return replay


def summarize_operations(root, doc=None, now=None):
    """Read receipts only; safe for build integration without recursive builds."""
    root = Path(root).resolve()
    observed = _instant(now) if now is not None else _utc_now()
    folder = _safe_path(root, OUT)
    summary = {'schema_version': SCHEMA, 'receipt_count': 0, 'natural_receipt_count': 0,
        'controlled_receipt_count': 0, 'replay_receipt_count': 0,
        'natural_new_d_evidence_days': [], 'latest_receipt': None, 'source_bindings': [],
        'training_admitted_days': 0, 'training_allowed': False,
        'status': 'NOT_YET_RECORDED', 'private_storage': {'status': 'NOT_CONFIGURED', 'independently_verified': False},
        'notices': ['NO_NATURAL_NEW_D_EVIDENCE'], 'errors': []}
    if not folder.exists():
        return summary
    paths = sorted(folder.glob('*/*.json'))
    if len(paths) > MAX_RECEIPTS:
        summary['errors'].append('RECEIPT_SCAN_LIMIT_EXCEEDED')
        summary['status'] = 'ACCEPTANCE_FAILED'
        return summary
    receipts = []
    for path in paths:
        try:
            _safe_path(root, path.relative_to(root).as_posix())
            raw = path.read_bytes()
            if not re.fullmatch(r'[0-9a-f]{64}\.json', path.name) or sha(raw) != path.stem:
                raise ValueError('OPERATION_RECEIPT_HASH_MISMATCH')
            receipt = json.loads(raw)
            if receipt.get('schema_version') != SCHEMA or _instant(receipt['observed_at_utc']) > observed:
                raise ValueError('OPERATION_RECEIPT_SCHEMA_OR_TIME_REJECTED')
            if receipt.get('training_allowed') is not False or receipt.get('training_admitted_days') != 0:
                raise ValueError('OPERATION_RECEIPT_CANNOT_ADMIT_TRAINING')
            if receipt.get('test_only') is True:
                continue
            receipt = _revalidate_receipt(root, receipt)
            receipts.append(receipt)
            summary['source_bindings'].append({'path': path.relative_to(root).as_posix(), 'sha256': sha(raw)})
        except (OSError, ValueError, KeyError, TypeError):
            summary['errors'].append('OPERATION_RECEIPT_REJECTED')
    receipts.sort(key=lambda receipt: receipt['observed_at_utc'])
    if receipts:
        latest = receipts[-1]
        summary['receipt_count'] = len(receipts)
        summary['latest_receipt'] = latest
        summary['private_storage'] = latest['private_storage']
        summary['status'] = latest['status']
        natural = [r for r in receipts if r.get('natural_evidence_status') == 'NATURAL_BEFORE_ENTRY_RECEIPT'
                   and r.get('run_context', {}).get('trigger_class') == 'NATURAL_TRIGGER'
                   and not r.get('errors')]
        summary['natural_receipt_count'] = len(natural)
        summary['controlled_receipt_count'] = sum(r['run_context']['trigger_class'] == 'CONTROLLED_TRIGGER' and not r['errors'] for r in receipts)
        summary['replay_receipt_count'] = sum(r['natural_evidence_status'] == 'NATURAL_REUSED_NOT_NEW' and not r['errors'] for r in receipts)
        summary['natural_new_d_evidence_days'] = sorted({date for r in natural for date in r['natural_new_d_evidence']})
        summary['notices'] = list(latest.get('notices', []))
        if summary['natural_new_d_evidence_days']:
            summary['notices'] = [n for n in summary['notices'] if not n.startswith('NO_NATURAL_NEW_D_EVIDENCE')]
        else:
            summary['notices'].append('NO_NATURAL_NEW_D_EVIDENCE')
    if summary['errors']:
        summary['status'] = 'ACCEPTANCE_FAILED'
        summary['errors'] = sorted(set(summary['errors']))
    summary['notices'] = sorted(set(summary['notices']))
    return summary


def _latest_dataset(root):
    root = Path(root).resolve()
    base = root / 'outputs/decision/profit_research'
    pointer = json.loads(_safe_path(root, 'outputs/decision/profit_research/latest.json').read_bytes())
    path = pointer.get('path')
    if not isinstance(path, str) or not re.fullmatch(r'versions/[0-9a-f]{64}\.json', path):
        raise ValueError('INVALID_RESEARCH_DATASET_POINTER')
    raw = _safe_path(root, (base / path).relative_to(root).as_posix()).read_bytes()
    if sha(raw) != pointer.get('sha256') or Path(path).stem != pointer.get('sha256'):
        raise ValueError('RESEARCH_DATASET_HASH_MISMATCH')
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description='Record research-only UTC runtime acceptance')
    parser.add_argument('--root', default='.')
    parser.add_argument('--record', action='store_true', required=True)
    parser.add_argument('--event-name', default=os.environ.get('GITHUB_EVENT_NAME'))
    parser.add_argument('--run-id', default=os.environ.get('GITHUB_RUN_ID'))
    parser.add_argument('--run-attempt', default=os.environ.get('GITHUB_RUN_ATTEMPT'))
    parser.add_argument('--github-head-sha', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--source-revision', default=os.environ.get('RESEARCH_BASE'))
    parser.add_argument('--run-started-at-utc', default=os.environ.get('RESEARCH_RUN_STARTED_AT_UTC'))
    parser.add_argument('--private-storage-configured', action='store_true')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        doc = _latest_dataset(root)
        context = {key: getattr(args, key) for key in ('event_name', 'run_id', 'run_attempt',
                   'github_head_sha', 'source_revision', 'run_started_at_utc')}
        context.update(repository=os.environ.get('GITHUB_REPOSITORY'), ref_name=os.environ.get('GITHUB_REF_NAME'))
        if context['event_name'] == 'workflow_run':
            event_path = os.environ.get('GITHUB_EVENT_PATH')
            if event_path:
                event = json.loads(Path(event_path).read_bytes())
                upstream = event.get('workflow_run', {})
                context['upstream'] = {k: upstream.get(k) for k in ('name', 'conclusion', 'head_branch')}
                context['upstream']['repository'] = upstream.get('repository', {}).get('full_name')
        receipt = build_operation_receipt(root, doc, run_context=context,
                                         private_storage_configured=args.private_storage_configured)
        binding = write_operation_receipt(root, receipt)
        print(json.dumps({'status': receipt['status'], 'binding': binding,
            'natural_evidence_status': receipt['natural_evidence_status'], 'counts': receipt.get('counts', {}),
            'errors': receipt['errors']}, ensure_ascii=False))
        return 1 if receipt['status'] == 'ACCEPTANCE_FAILED' else 0
    except Exception:
        print(json.dumps({'status': 'ACCEPTANCE_FAILED', 'errors': ['OPERATION_RECORD_FAILED']}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
