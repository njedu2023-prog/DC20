"""Research-only, immutable D-day enrichment. Never edits a production input.

Historical observations remain retrospective. Public files contain derived
features and source hashes; provider bodies may be retained only in an explicit
private cache outside the repository. No fitting or model activation is done.
first_collected_revision identifies the input repository revision; the payload's
collector_code_sha256 identifies the collector file actually executed.
"""
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import urllib.request
from zoneinfo import ZoneInfo

OUT = 'outputs/decision/profit_research/enrichment'
SCHEMA = 'dc20_research_enrichment_v1'
FORMULA = 'd_minute_full_session_log_std_windows_v2'
LEGACY_FORMULA = 'd_minute_full_session_log_std_windows_v1'
SHANGHAI = ZoneInfo('Asia/Shanghai')
MINUTE_KEYS = ('minute_realized_vol', 'minute_first_30m_return', 'minute_last_30m_return')
KEYS = ('limit_last_time_minutes', *MINUTE_KEYS)
API_URL = 'https://api.tushare.pro'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MINUTE_FIELDS = ('ts_code', 'trade_time', 'open', 'close', 'high', 'low', 'vol', 'amount')
# Close-labelled one-minute bars; some sources additionally report 09:30.
EXPECTED_MINUTES = frozenset(range(571, 691)) | frozenset(range(781, 901))
MISSING_REASONS = frozenset(('NOT_COLLECTED', 'CREDENTIAL_ABSENT', 'COLLECTION_BUDGET_EXHAUSTED',
    'SOURCE_REQUEST_FAILED_OR_REJECTED', 'LAST_SEAL_MISSING_OR_INVALID', 'INCOMPLETE_MINUTE_SESSION',
    'MINUTE_DATE_MISMATCH', 'MINUTE_STOCK_MISMATCH', 'DUPLICATE_MINUTE', 'INVALID_MINUTE_TIME',
    'NON_MINUTE_TIMESTAMP', 'INVALID_MINUTE_NUMBER', 'INVALID_MINUTE_RANGE', 'INVALID_MINUTE_OHLC',
    'MINUTE_SOURCE_REJECTED'))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp(value=None):
    result = value or datetime.now(timezone.utc)
    if isinstance(result, str):
        result = datetime.fromisoformat(result.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return result.astimezone(timezone.utc)


def parse_clock_minutes(value):
    """Parse exchange clocks without turning 92501.0 into 92:50:10."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}', text):
        text = text[-8:]
    if re.fullmatch(r'\d{1,2}:\d{2}(?::\d{2})?', text):
        parts = [int(x) for x in text.split(':')]
        hour, minute, second = (*parts, 0) if len(parts) == 2 else parts
    elif re.fullmatch(r'\d{1,6}(?:\.0+)?', text):
        text = text.split('.')[0].zfill(6)
        hour, minute, second = int(text[:2]), int(text[2:4]), int(text[4:])
    else:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour * 60.0 + minute + second / 60.0


def last_seal_value(value):
    result = parse_clock_minutes(value)
    # A-share opening auction / continuous session, excluding the lunch break.
    if result is not None and (565 <= result <= 690 or 780 <= result <= 900):
        return result
    return None


def _number(value):
    if isinstance(value, bool):
        raise ValueError('INVALID_MINUTE_NUMBER')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('INVALID_MINUTE_NUMBER')
    return number


def compute_minute_features(rows, date, code):
    """Require a full, unambiguous D session before producing any features."""
    byminute = {}
    excluded = 0
    for row in rows:
        if row.get('ts_code') != code:
            raise ValueError('MINUTE_STOCK_MISMATCH')
        text = str(row.get('trade_time', row.get('time', ''))).strip()
        try:
            instant = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('INVALID_MINUTE_TIME') from None
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=SHANGHAI)
        local = instant.astimezone(SHANGHAI)
        if local.strftime('%Y%m%d') != date:
            raise ValueError('MINUTE_DATE_MISMATCH')
        if local.second or local.microsecond:
            raise ValueError('NON_MINUTE_TIMESTAMP')
        minute = local.hour * 60 + local.minute
        if minute not in EXPECTED_MINUTES and minute != 570:
            excluded += 1
            continue
        if minute in byminute:
            raise ValueError('DUPLICATE_MINUTE')
        try:
            prices = {k: _number(row[k]) for k in ('open', 'close', 'high', 'low')}
            volume, amount = _number(row['vol']), _number(row['amount'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('INVALID_MINUTE_NUMBER') from None
        if min(prices.values()) <= 0 or volume < 0 or amount < 0:
            raise ValueError('INVALID_MINUTE_RANGE')
        if prices['high'] + 1e-8 < max(prices['open'], prices['close'], prices['low']) or prices['low'] - 1e-8 > min(prices['open'], prices['close']):
            raise ValueError('INVALID_MINUTE_OHLC')
        byminute[minute] = prices
    missing = EXPECTED_MINUTES - set(byminute)
    if missing:
        raise ValueError('INCOMPLETE_MINUTE_SESSION')
    ordered = [byminute[m] for m in sorted(byminute)]
    log_returns = [math.log(b['close'] / a['close']) for a, b in zip(ordered, ordered[1:])]
    mean = math.fsum(log_returns) / len(log_returns)
    first = [byminute[m] for m in sorted(byminute) if 570 <= m <= 600]
    values = {
        'minute_realized_vol': math.sqrt(math.fsum((r - mean) ** 2 for r in log_returns) / len(log_returns)),
        'minute_first_30m_return': first[-1]['close'] / first[0]['open'] - 1,
        'minute_last_30m_return': byminute[900]['close'] / byminute[870]['close'] - 1,
    }
    quality = {'formula_version': FORMULA, 'timezone': 'Asia/Shanghai',
               'valid_minute_bars': len(byminute), 'required_minute_bars': 240,
               'excluded_outside_session_bars': excluded, 'missing_required_bars': 0,
               'first_window': '09:30<=bar_time<=10:00; first observed regular bar open to 10:00 close',
               'last_window': '15:00 close / 14:30 close - 1; exact 30-minute interval',
               'volatility_definition': 'population std of consecutive regular-session log close returns, including lunch transition; not annualized',
               'session_complete': True}
    return values, quality


def _safe_identity(date, code):
    if not re.fullmatch(r'20\d{6}', str(date)) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', str(code)):
        raise ValueError('UNSAFE_RECORD_IDENTITY')


def _binding(root, path):
    raw = path.read_bytes()
    return {'path': path.relative_to(root).as_posix(), 'sha256': sha(raw)}


def _receipt(api, date, code, source_path=None):
    if api == 'repository_csv':
        return {'api': api, 'params': {'path': source_path, 'trade_date': date, 'ts_code': code},
                'fields': ['trade_date', 'ts_code', 'last_time']}
    if api == 'limit_list_d':
        return {'api': api, 'params': {'trade_date': date}, 'fields': ['trade_date', 'ts_code', 'last_time']}
    if api == 'stk_mins':
        day = date[:4] + '-' + date[4:6] + '-' + date[6:]
        return {'api': api, 'params': {'ts_code': code, 'start_date': day + ' 09:30:00',
                'end_date': day + ' 15:00:00', 'freq': '1min'}, 'fields': list(MINUTE_FIELDS)}
    raise ValueError('INVALID_ENRICHMENT_SOURCE')


def _validate_payload(payload, date, code, current):
    allowed = {'schema_version', 'signal_date', 'ts_code', 'formula_version', 'rule_version',
        'values', 'missing_reasons', 'field_sources', 'minute_quality',
        'point_in_time_independently_verified', 'production_inputs_modified', 'collection_kind', 'field_status',
        'collector_code_sha256'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('ENRICHMENT_FIELD_NOT_ALLOWED')
    if payload.get('schema_version') != SCHEMA or payload.get('signal_date') != date or payload.get('ts_code') != code:
        raise ValueError('ENRICHMENT_IDENTITY_MISMATCH')
    # The first v1 envelopes used formula_version alone; do not rewrite them.
    rule = payload.get('formula_version')
    if rule not in (FORMULA, LEGACY_FORMULA) or payload.get('rule_version', rule) != rule:
        raise ValueError('ENRICHMENT_RULE_MISMATCH')
    if payload.get('point_in_time_independently_verified') is not False or payload.get('production_inputs_modified') is not False:
        raise ValueError('INVALID_POINT_IN_TIME_CLAIM')
    if 'collector_code_sha256' in payload and not re.fullmatch(r'[0-9a-f]{64}', str(payload['collector_code_sha256'])):
        raise ValueError('INVALID_COLLECTOR_CODE_HASH')
    values, states, reasons, sources = (payload.get(k) for k in ('values', 'field_status', 'missing_reasons', 'field_sources'))
    if any(not isinstance(x, dict) or set(x) != set(KEYS) for x in (values, states, reasons)) or not isinstance(sources, dict):
        raise ValueError('ENRICHMENT_FIELD_NOT_ALLOWED')
    available = {key for key in KEYS if values[key] is not None}
    if set(sources) != available:
        raise ValueError('ENRICHMENT_SOURCE_FIELDS_MISMATCH')
    for key in KEYS:
        value = values[key]
        if states[key] != ('AVAILABLE' if value is not None else 'MISSING'):
            raise ValueError('ENRICHMENT_STATUS_MISMATCH')
        if value is None:
            if reasons[key] not in MISSING_REASONS:
                raise ValueError('ENRICHMENT_MISSING_REASON_INVALID')
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or reasons[key] is not None:
            raise ValueError('INVALID_ENRICHMENT_VALUE')
        if key == 'limit_last_time_minutes':
            valid = 565 <= value <= 690 or 780 <= value <= 900
        elif key == 'minute_realized_vol':
            valid = value >= 0
        else:
            valid = value > -1
        if not valid:
            raise ValueError('ENRICHMENT_VALUE_OUT_OF_RANGE')
        source = sources[key]
        permitted = {'source_id', 'raw_sha256', 'source_event_date', 'observed_at_utc', 'timezone',
            'collection_kind', 'source_timestamp_before_entry', 'raw_cache_written',
            'point_in_time_independently_verified', 'source_path', 'request_receipt'}
        if not isinstance(source, dict) or set(source) - permitted:
            raise ValueError('ENRICHMENT_SOURCE_FIELDS_MISMATCH')
        if source.get('source_event_date') != date or source.get('timezone') != 'Asia/Shanghai':
            raise ValueError('ENRICHMENT_SOURCE_DATE_MISMATCH')
        instant = timestamp(source['observed_at_utc'])
        close = datetime.strptime(date + '1500', '%Y%m%d%H%M').replace(tzinfo=SHANGHAI)
        if instant > current or instant < close:
            raise ValueError('ENRICHMENT_SOURCE_TIME_INVALID')
        if not re.fullmatch(r'[0-9a-f]{64}', str(source.get('raw_sha256', ''))):
            raise ValueError('ENRICHMENT_SOURCE_HASH_INVALID')
        if source.get('point_in_time_independently_verified') is not False or type(source.get('raw_cache_written')) is not bool:
            raise ValueError('INVALID_POINT_IN_TIME_CLAIM')
        before = source.get('source_timestamp_before_entry')
        if type(before) is not bool or source.get('collection_kind') != ('BEFORE_ENTRY_COLLECTION' if before else 'HISTORICAL_BACKFILL'):
            raise ValueError('ENRICHMENT_COLLECTION_KIND_INVALID')
        source_id = source.get('source_id')
        if key in MINUTE_KEYS and source_id != 'tushare:stk_mins':
            raise ValueError('INVALID_ENRICHMENT_SOURCE')
        if key == 'limit_last_time_minutes' and source_id not in ('repository:limit_list_d', 'tushare:limit_list_d'):
            raise ValueError('INVALID_ENRICHMENT_SOURCE')
        if source_id == 'repository:limit_list_d':
            expected_path = f'data/market/raw/{date[:4]}/{date}/limit_list_d.csv'
            if source.get('source_path') != expected_path:
                raise ValueError('UNSAFE_ENRICHMENT_SOURCE_PATH')
            expected_receipt = _receipt('repository_csv', date, code, expected_path)
        else:
            if 'source_path' in source:
                raise ValueError('UNSAFE_ENRICHMENT_SOURCE_PATH')
            expected_receipt = _receipt(source_id.split(':')[1], date, code)
        # Legacy v1 records did not include request parameters. Preserve their
        # bytes and absence honestly; newly collected records always add them.
        if 'request_receipt' in source and source['request_receipt'] != expected_receipt:
            raise ValueError('ENRICHMENT_REQUEST_RECEIPT_MISMATCH')
    minute_available = available.intersection(MINUTE_KEYS)
    quality = payload.get('minute_quality')
    if minute_available:
        if minute_available != set(MINUTE_KEYS) or not isinstance(quality, dict):
            raise ValueError('PARTIAL_MINUTE_FEATURES')
        if (quality.get('formula_version') != rule or quality.get('timezone') != 'Asia/Shanghai'
                or quality.get('session_complete') is not True or quality.get('required_minute_bars') != 240
                or quality.get('valid_minute_bars') not in (240, 241) or quality.get('missing_required_bars') != 0):
            raise ValueError('MINUTE_QUALITY_CONTRACT_MISMATCH')
    elif quality is not None:
        raise ValueError('MINUTE_QUALITY_WITHOUT_FEATURES')
    kinds = {source['collection_kind'] for source in sources.values()}
    expected_kind = next(iter(kinds)) if len(kinds) == 1 else 'MIXED_SOURCE_COLLECTION' if kinds else 'NO_VALID_OBSERVATION'
    if payload.get('collection_kind') != expected_kind:
        raise ValueError('ENRICHMENT_COLLECTION_KIND_INVALID')


def read_record(root, date, code):
    """Return the most complete immutable envelope, including its public binding."""
    root = Path(root).resolve()
    _safe_identity(date, code)
    folder = root / OUT / date / code
    if not folder.exists():
        return None
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError('UNSAFE_ENRICHMENT_PATH')
    found = []
    for path in sorted(folder.glob('*.json')):
        if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}\.json', path.name):
            raise ValueError('UNSAFE_ENRICHMENT_FILE')
        envelope = json.loads(path.read_bytes())
        payload = envelope['payload']
        digest = sha(encoded(payload))
        if digest != envelope['payload_sha256'] or path.stem != digest:
            raise ValueError('ENRICHMENT_HASH_MISMATCH')
        current = timestamp()
        _validate_payload(payload, date, code, current)
        first = timestamp(envelope['first_collected_at_utc'])
        if first > current or not re.fullmatch(r'[0-9a-f]{40}', str(envelope.get('first_collected_revision', ''))):
            raise ValueError('ENRICHMENT_COLLECTION_METADATA_INVALID')
        if any(timestamp(s['observed_at_utc']) > first for s in payload['field_sources'].values()):
            raise ValueError('ENRICHMENT_COLLECTION_METADATA_INVALID')
        envelope['source_binding'] = _binding(root, path)
        found.append(envelope)
    return max(found, key=lambda e: (sum(e['payload']['values'].get(k) is not None for k in KEYS), e['first_collected_at_utc'], e['payload_sha256'])) if found else None


def _write_record(root, payload, revision, observed):
    raw_payload = encoded(payload)
    digest = sha(raw_payload)
    path = root / OUT / payload['signal_date'] / payload['ts_code'] / (digest + '.json')
    if path.exists():
        prior = json.loads(path.read_bytes())
        if prior['payload'] != payload or prior['payload_sha256'] != digest:
            raise ValueError('IMMUTABLE_ENRICHMENT_CHANGED')
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.resolve().is_relative_to(root) or path.parent.is_symlink():
        raise ValueError('UNSAFE_ENRICHMENT_PATH')
    envelope = {'payload': payload, 'payload_sha256': digest,
                'first_collected_at_utc': observed.isoformat(), 'first_collected_revision': revision}
    with path.open('xb') as stream:
        stream.write(encoded(envelope))
    return True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # This API has one fixed HTTPS endpoint. Even same-host redirects are
        # unnecessary; never forward a credential-bearing POST to another URL.
        raise ValueError('PROVIDER_REDIRECT_REJECTED')


def _api_request(api, params, fields, token, timeout):
    request = urllib.request.Request(API_URL, data=json.dumps({'api_name': api, 'token': token,
        'params': params, 'fields': ','.join(fields)}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES or token.encode() in raw:
        raise ValueError('UNSAFE_PROVIDER_RESPONSE')
    return raw


def _response_rows(raw):
    obj = json.loads(raw)
    if type(obj.get('code')) is not int or obj['code'] != 0:
        raise ValueError('PROVIDER_UNAVAILABLE')
    data = obj.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('fields'), list) or not isinstance(data.get('items'), list):
        raise ValueError('PROVIDER_SCHEMA_REJECTED')
    fields = data['fields']
    if not all(isinstance(f, str) for f in fields) or len(fields) != len(set(fields)):
        raise ValueError('PROVIDER_SCHEMA_REJECTED')
    if any(not isinstance(row, list) or len(row) != len(fields) for row in data['items']):
        raise ValueError('PROVIDER_SCHEMA_REJECTED')
    return [dict(zip(fields, row)) for row in data['items']]


def _open_private_cache_directory(folder):
    folder = Path(folder).absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open('/', flags)
    try:
        for part in folder.parts[1:]:
            if part in ('.', '..'):
                raise ValueError('UNSAFE_PRIVATE_CACHE_PATH')
            try:
                child = os.open(part, flags, dir_fd=directory)
            except FileNotFoundError:
                os.mkdir(part, mode=0o700, dir_fd=directory)
                child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        metadata = os.fstat(directory)
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError('PRIVATE_CACHE_DIRECTORY_PERMISSIONS')
        return directory
    except Exception:
        os.close(directory)
        raise


def _private_cache(raw, folder):
    if folder is None:
        return False
    directory = _open_private_cache_directory(folder)
    try:
        name = sha(raw) + '.json'
        try:
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(handle, 'rb') as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise ValueError('PRIVATE_CACHE_FILE_PERMISSIONS')
                if stream.read(MAX_RESPONSE_BYTES + 1) != raw:
                    raise ValueError('PRIVATE_CACHE_HASH_MISMATCH')
        else:
            with os.fdopen(handle, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        return True
    finally:
        os.close(directory)


def _source(source_id, raw, date, observed, exec_date, *, code, source_path=None, cached=False):
    before = False
    if exec_date and re.fullmatch(r'20\d{6}', exec_date):
        cutoff = datetime.strptime(exec_date + '0925', '%Y%m%d%H%M').replace(tzinfo=SHANGHAI)
        before = observed < cutoff
    result = {'source_id': source_id, 'raw_sha256': sha(raw), 'source_event_date': date,
              'observed_at_utc': observed.isoformat(), 'timezone': 'Asia/Shanghai',
              'collection_kind': 'BEFORE_ENTRY_COLLECTION' if before else 'HISTORICAL_BACKFILL',
              'source_timestamp_before_entry': before, 'raw_cache_written': cached,
              'point_in_time_independently_verified': False}
    result['request_receipt'] = _receipt('repository_csv' if source_id.startswith('repository:') else source_id.split(':')[1], date, code, source_path)
    if source_path:
        result['source_path'] = source_path
    return result


def _dataset(root, revision):
    from scripts.build_profit_research import build
    return build(root, revision)[0]


def collect(root, revision, *, max_calls=180, seconds=540, timeout_seconds=20,
            private_cache=None, now=None, request_pause=.35):
    root = Path(root).resolve()
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('INVALID_SOURCE_REVISION')
    if max_calls < 0 or seconds <= 0 or not 0 < timeout_seconds <= 60:
        raise ValueError('INVALID_COLLECTION_BUDGET')
    cache = Path(private_cache).absolute() if private_cache else None
    if cache is not None and cache.resolve().is_relative_to(root):
        raise ValueError('PRIVATE_CACHE_MUST_BE_OUTSIDE_REPOSITORY')
    if cache is not None:
        # Fail before making any network call if the private destination is unsafe.
        directory = _open_private_cache_directory(cache)
        os.close(directory)
    dataset = _dataset(root, revision)
    collector_code_sha256 = sha(Path(__file__).read_bytes())
    token = os.environ.get('TUSHARE_TOKEN', '').strip()
    began = time.monotonic()
    count = {'status': 'BOUNDED_ENRICHMENT_COMPLETE', 'calls': 0, 'records_seen': 0,
             'versions_written': 0, 'last_seal_recovered': 0, 'minute_records_completed': 0,
             'source_failures': 0, 'credential_present': bool(token)}
    limit_cache = {}

    def observed_now():
        return timestamp(now) if now is not None else timestamp()

    def fetch(api, params, fields):
        if not token:
            return None, None, 'CREDENTIAL_ABSENT'
        remaining = seconds - (time.monotonic() - began)
        if count['calls'] >= max_calls or remaining < 1:
            return None, None, 'COLLECTION_BUDGET_EXHAUSTED'
        if count['calls'] and request_pause:
            time.sleep(min(request_pause, max(0, remaining - .1)))
        remaining = seconds - (time.monotonic() - began)
        if remaining < 1:
            return None, None, 'COLLECTION_BUDGET_EXHAUSTED'
        count['calls'] += 1
        try:
            raw = _api_request(api, params, fields, token, min(timeout_seconds, remaining))
            if len(raw) > MAX_RESPONSE_BYTES or token.encode() in raw:
                raise ValueError('UNSAFE_PROVIDER_RESPONSE')
            rows = _response_rows(raw)
            cached = _private_cache(raw, cache)
            return rows, (raw, cached), None
        except Exception:
            # Never expose provider errors, credentials or response bodies.
            count['source_failures'] += 1
            return None, None, 'SOURCE_REQUEST_FAILED_OR_REJECTED'

    for day in reversed(dataset['days']):
        date = day['date']
        close = datetime.strptime(date + '1500', '%Y%m%d%H%M').replace(tzinfo=SHANGHAI)
        if date > dataset['as_of_date'] or observed_now() < close:
            continue
        for row in day['records']:
            code = row['ts_code']
            _safe_identity(date, code)
            count['records_seen'] += 1
            previous = read_record(root, date, code)
            if previous and previous['payload'].get('formula_version') == FORMULA and all(previous['payload']['values'].get(k) is not None for k in KEYS):
                continue
            payload = copy.deepcopy(previous['payload']) if previous else {
                'schema_version': SCHEMA, 'signal_date': date, 'ts_code': code,
                'formula_version': FORMULA, 'rule_version': FORMULA, 'values': dict.fromkeys(KEYS),
                'missing_reasons': dict.fromkeys(KEYS, 'NOT_COLLECTED'), 'field_sources': {},
                'minute_quality': None, 'point_in_time_independently_verified': False,
                'production_inputs_modified': False}
            payload['collector_code_sha256'] = collector_code_sha256
            if payload.get('formula_version') != FORMULA:
                payload.update(formula_version=FORMULA, rule_version=FORMULA, minute_quality=None)
                for key in MINUTE_KEYS:
                    payload['values'][key] = None
                    payload['missing_reasons'][key] = 'NOT_COLLECTED'
                    payload['field_sources'].pop(key, None)
            if payload['values']['limit_last_time_minutes'] is None:
                raw_path = root / 'data/market/raw' / date[:4] / date / 'limit_list_d.csv'
                valid = None
                if raw_path.is_file() and not raw_path.is_symlink() and raw_path.resolve().is_relative_to(root):
                    raw = raw_path.read_bytes()
                    if token and token.encode() in raw:
                        raise ValueError('SECRET_IN_LOCAL_SOURCE')
                    try:
                        matches = [r for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))) if r.get('ts_code') == code and r.get('trade_date') == date]
                        if len(matches) == 1:
                            valid = last_seal_value(matches[0].get('last_time'))
                    except (ValueError, UnicodeError, csv.Error):
                        pass
                    if valid is not None:
                        source = _source('repository:limit_list_d', raw, date, observed_now(), row.get('exec_date'), code=code, source_path=raw_path.relative_to(root).as_posix())
                if valid is None:
                    if date not in limit_cache:
                        limit_cache[date] = fetch('limit_list_d', {'trade_date': date}, ('trade_date', 'ts_code', 'last_time'))
                    response_rows, receipt, error = limit_cache[date]
                    if response_rows is not None:
                        matches = [r for r in response_rows if r.get('ts_code') == code and str(r.get('trade_date')) == date]
                        if len(matches) == 1:
                            valid = last_seal_value(matches[0].get('last_time'))
                        if valid is not None:
                            source = _source('tushare:limit_list_d', receipt[0], date, observed_now(), row.get('exec_date'), code=code, cached=receipt[1])
                    payload['missing_reasons']['limit_last_time_minutes'] = error or 'LAST_SEAL_MISSING_OR_INVALID'
                if valid is not None:
                    payload['values']['limit_last_time_minutes'] = valid
                    payload['missing_reasons']['limit_last_time_minutes'] = None
                    payload['field_sources']['limit_last_time_minutes'] = source
                    count['last_seal_recovered'] += 1
            if any(payload['values'][k] is None for k in MINUTE_KEYS):
                day_text = date[:4] + '-' + date[4:6] + '-' + date[6:]
                rows, receipt, error = fetch('stk_mins', {'ts_code': code, 'start_date': day_text + ' 09:30:00', 'end_date': day_text + ' 15:00:00', 'freq': '1min'}, MINUTE_FIELDS)
                if rows is not None:
                    try:
                        values, quality = compute_minute_features(rows, date, code)
                        source = _source('tushare:stk_mins', receipt[0], date, observed_now(), row.get('exec_date'), code=code, cached=receipt[1])
                        payload['minute_quality'] = quality
                        for key in MINUTE_KEYS:
                            payload['values'][key] = values[key]
                            payload['missing_reasons'][key] = None
                            payload['field_sources'][key] = source
                        count['minute_records_completed'] += 1
                    except (ValueError, TypeError, KeyError) as exc:
                        error = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[A-Z_]+', str(exc)) else 'MINUTE_SOURCE_REJECTED'
                if error:
                    for key in MINUTE_KEYS:
                        if payload['values'][key] is None:
                            payload['missing_reasons'][key] = error
            kinds = {v['collection_kind'] for v in payload['field_sources'].values()}
            payload['collection_kind'] = next(iter(kinds)) if len(kinds) == 1 else 'MIXED_SOURCE_COLLECTION' if kinds else 'NO_VALID_OBSERVATION'
            payload['field_status'] = {k: 'AVAILABLE' if payload['values'][k] is not None else 'MISSING' for k in KEYS}
            if token and token.encode() in encoded(payload):
                raise ValueError('SECRET_IN_ENRICHMENT')
            count['versions_written'] += _write_record(root, payload, revision, observed_now())
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Bounded, research-only historical D feature enrichment')
    parser.add_argument('--root', default='.')
    parser.add_argument('--revision', required=True)
    parser.add_argument('--max-calls', type=int, default=180)
    parser.add_argument('--seconds', type=float, default=540)
    parser.add_argument('--timeout-seconds', type=float, default=20)
    parser.add_argument('--private-cache')
    args = parser.parse_args()
    try:
        print(json.dumps(collect(args.root, args.revision, max_calls=args.max_calls,
              seconds=args.seconds, timeout_seconds=args.timeout_seconds, private_cache=args.private_cache), ensure_ascii=False))
    except Exception:
        # The caller gets a failing exit status without a traceback containing secrets.
        print(json.dumps({'status': 'ENRICHMENT_FAILED', 'details': 'See sanitized collection checks'}))
        raise SystemExit(1)
