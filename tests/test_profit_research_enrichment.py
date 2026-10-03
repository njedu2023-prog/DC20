import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import profit_research_enrichment as e


CODE = '600448.SH'
DATE = '20260918'
REV = 'a' * 40


def bars(code=CODE, date=DATE):
    result = []
    for minute in sorted(e.EXPECTED_MINUTES | {570}):
        price = 10 + (minute - 570) / 1000
        result.append({'ts_code': code,
            'trade_time': f'{date[:4]}-{date[4:6]}-{date[6:]} {minute//60:02d}:{minute%60:02d}:00',
            'open': price, 'close': price + .01, 'high': price + .02,
            'low': price - .01, 'vol': 100, 'amount': 1000})
    return result


def response(rows):
    fields = list(rows[0]) if rows else list(e.MINUTE_FIELDS)
    return json.dumps({'code': 0, 'data': {'fields': fields,
        'items': [[r[k] for k in fields] for r in rows]}}).encode()


class ClockAndMinuteTests(unittest.TestCase):
    def test_clock_integer_float_and_strings(self):
        for value in (92501, 92501.0, '92501', '92501.0', '09:25:01', '2026-09-18 09:25:01'):
            self.assertAlmostEqual(e.last_seal_value(value), 565 + 1/60)
        for value in (None, float('nan'), '92501.2', '92:50:10', '12:00:00', '', True):
            self.assertIsNone(e.last_seal_value(value))

    def test_full_session_numeric_definition(self):
        rows = bars()
        values, quality = e.compute_minute_features(rows, DATE, CODE)
        self.assertTrue(quality['session_complete'])
        self.assertEqual(quality['valid_minute_bars'], 241)
        self.assertEqual(quality['timezone'], 'Asia/Shanghai')
        self.assertAlmostEqual(values['minute_first_30m_return'], 10.04/10 - 1)
        self.assertAlmostEqual(values['minute_last_30m_return'], 10.34/10.31 - 1)
        self.assertGreater(values['minute_realized_vol'], 0)

    def test_exact_240_close_labelled_bars_allowed(self):
        values, quality = e.compute_minute_features(bars()[1:], DATE, CODE)
        self.assertEqual(quality['valid_minute_bars'], 240)
        self.assertEqual(len(values), 3)

    def test_partial_duplicates_and_wrong_identity_rejected(self):
        rows = bars()
        with self.assertRaisesRegex(ValueError, 'INCOMPLETE_MINUTE_SESSION'):
            e.compute_minute_features(rows[:-1], DATE, CODE)
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_MINUTE'):
            e.compute_minute_features(rows + [rows[0]], DATE, CODE)
        with self.assertRaisesRegex(ValueError, 'DATE_MISMATCH'):
            e.compute_minute_features(rows, '20260917', CODE)
        with self.assertRaisesRegex(ValueError, 'STOCK_MISMATCH'):
            e.compute_minute_features(rows, DATE, '000001.SZ')

    def test_invalid_ohlc_and_pre_session_excluded(self):
        rows = bars()
        extra = {**rows[0], 'trade_time': '2026-09-18 09:25:00', 'close': 99}
        original, _ = e.compute_minute_features(rows, DATE, CODE)
        values, quality = e.compute_minute_features([extra] + rows, DATE, CODE)
        self.assertEqual(original, values)
        self.assertEqual(quality['excluded_outside_session_bars'], 1)
        rows[0]['close'] = 100
        with self.assertRaisesRegex(ValueError, 'INVALID_MINUTE_OHLC'):
            e.compute_minute_features(rows, DATE, CODE)

    def test_minute_nonfinite_rejected(self):
        rows = bars()
        rows[0]['open'] = float('inf')
        with self.assertRaisesRegex(ValueError, 'INVALID_MINUTE_NUMBER'):
            e.compute_minute_features(rows, DATE, CODE)


class EnrichmentCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = '2026-09-25T03:00:00+00:00'
        real_timestamp = e.timestamp
        self.clock = patch.object(e, 'timestamp', side_effect=lambda value=None:
            real_timestamp(value or '2026-10-01T00:00:00+00:00'))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.doc = {'as_of_date': '20260930', 'days': [{'date': DATE,
            'records': [{'ts_code': CODE, 'exec_date': '20260921'}]}]}
        self.dataset = patch.object(e, '_dataset', side_effect=lambda *args: copy.deepcopy(self.doc))
        self.dataset.start()
        self.addCleanup(self.dataset.stop)
        self.env = patch.dict('os.environ', {'TUSHARE_TOKEN': 'FAKE_TEST_TOKEN_DO_NOT_PUBLISH'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.raw = self.root / 'data/market/raw/2026/20260918/limit_list_d.csv'
        self.raw.parent.mkdir(parents=True)
        self.raw.write_text('trade_date,ts_code,last_time\n20260918,600448.SH,92501.0\n')

    def run_collection(self, **kwargs):
        return e.collect(self.root, REV, now=self.now, request_pause=0, **kwargs)

    def test_raw_repair_minute_collection_and_idempotence(self):
        with patch.object(e, '_api_request', return_value=response(bars())) as request:
            first = self.run_collection()
            envelope = e.read_record(self.root, DATE, CODE)
            public = json.dumps(envelope)
            second = self.run_collection()
        self.assertEqual(first['calls'], 1)
        self.assertEqual(first['last_seal_recovered'], 1)
        self.assertEqual(first['minute_records_completed'], 1)
        self.assertEqual(second['calls'], 0)
        self.assertEqual(second['versions_written'], 0)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(envelope['payload']['point_in_time_independently_verified'])
        self.assertEqual(envelope['payload']['collection_kind'], 'HISTORICAL_BACKFILL')
        self.assertNotIn('FAKE_TEST_TOKEN', public)
        self.assertNotIn('"trade_time":', public)
        self.assertIn('source_binding', envelope)
        self.assertEqual(envelope['payload']['collector_code_sha256'], e.sha(Path(e.__file__).read_bytes()))

    def test_no_credential_still_recovers_existing_raw(self):
        with patch.dict('os.environ', {'TUSHARE_TOKEN': ''}), patch.object(e, '_api_request') as request:
            result = self.run_collection()
        payload = e.read_record(self.root, DATE, CODE)['payload']
        self.assertEqual(result['calls'], 0)
        self.assertEqual(result['last_seal_recovered'], 1)
        self.assertEqual(payload['missing_reasons']['minute_realized_vol'], 'CREDENTIAL_ABSENT')
        self.assertIsNone(payload['values']['minute_realized_vol'])
        request.assert_not_called()

    def test_one_limit_list_request_per_day_and_per_stock_minutes(self):
        self.raw.unlink()
        self.doc['days'][0]['records'].append({'ts_code': '000001.SZ', 'exec_date': '20260921'})
        def call(api, params, *args):
            if api == 'limit_list_d':
                return response([{'trade_date': DATE, 'ts_code': code, 'last_time': 92501} for code in (CODE, '000001.SZ')])
            return response(bars(params['ts_code']))
        with patch.object(e, '_api_request', side_effect=call) as request:
            result = self.run_collection()
        self.assertEqual(result['calls'], 3)
        self.assertEqual(sum(c.args[0] == 'limit_list_d' for c in request.call_args_list), 1)

    def test_budget_retains_missing_and_later_retry_preserves_old(self):
        with patch.object(e, '_api_request') as request:
            first = self.run_collection(max_calls=0)
        request.assert_not_called()
        self.assertEqual(first['versions_written'], 1)
        previous = e.read_record(self.root, DATE, CODE)
        path = self.root / previous['source_binding']['path']
        original = path.read_bytes()
        self.now = '2026-09-25T04:00:00+00:00'
        with patch.object(e, '_api_request', return_value=response(bars())):
            self.run_collection()
        current = e.read_record(self.root, DATE, CODE)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(current['payload']['field_sources']['limit_last_time_minutes'], previous['payload']['field_sources']['limit_last_time_minutes'])
        self.assertIsNotNone(current['payload']['values']['minute_realized_vol'])

    def test_partial_session_not_a_zero_feature(self):
        with patch.object(e, '_api_request', return_value=response(bars()[:5])):
            self.run_collection()
        payload = e.read_record(self.root, DATE, CODE)['payload']
        for key in e.MINUTE_KEYS:
            self.assertIsNone(payload['values'][key])
            self.assertEqual(payload['missing_reasons'][key], 'INCOMPLETE_MINUTE_SESSION')

    def test_before_entry_is_still_not_independent_pit_verification(self):
        self.now = '2026-09-18T09:00:00+00:00'
        with patch.object(e, '_api_request', return_value=response(bars())):
            self.run_collection()
        payload = e.read_record(self.root, DATE, CODE)['payload']
        self.assertEqual(payload['collection_kind'], 'BEFORE_ENTRY_COLLECTION')
        self.assertFalse(payload['point_in_time_independently_verified'])

    def test_unclosed_day_not_collected(self):
        self.now = '2026-09-18T06:59:00+00:00'
        with patch.object(e, '_api_request') as request:
            result = self.run_collection()
        request.assert_not_called()
        self.assertEqual(result['records_seen'], 0)
        self.assertIsNone(e.read_record(self.root, DATE, CODE))

    def test_provider_error_and_token_response_are_not_published(self):
        token = 'FAKE_TEST_TOKEN_DO_NOT_PUBLISH'
        with patch.object(e, '_api_request', return_value=('failure ' + token).encode()):
            self.run_collection()
        payload = e.read_record(self.root, DATE, CODE)['payload']
        self.assertEqual(payload['missing_reasons']['minute_realized_vol'], 'SOURCE_REQUEST_FAILED_OR_REJECTED')
        self.assertNotIn(token, json.dumps(payload))

    def test_private_cache_separate_from_public(self):
        with tempfile.TemporaryDirectory() as cache:
            with patch.object(e, '_api_request', return_value=response(bars())):
                self.run_collection(private_cache=Path(cache).resolve())
            files = list(Path(cache).glob('*.json'))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertNotIn('"trade_time":', json.dumps(e.read_record(self.root, DATE, CODE)))
        with self.assertRaisesRegex(ValueError, 'PRIVATE_CACHE_MUST_BE_OUTSIDE'):
            self.run_collection(private_cache=self.root / 'private')

    def test_tampered_immutable_record_rejected(self):
        self.run_collection(max_calls=0)
        envelope = e.read_record(self.root, DATE, CODE)
        path = self.root / envelope['source_binding']['path']
        obj = json.loads(path.read_bytes())
        obj['payload']['values']['limit_last_time_minutes'] = 999
        path.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'):
            e.read_record(self.root, DATE, CODE)

    def test_repeat_missing_does_not_change_first_collection(self):
        self.run_collection(max_calls=0)
        original = e.read_record(self.root, DATE, CODE)
        self.now = '2026-09-26T03:00:00+00:00'
        result = self.run_collection(max_calls=0)
        self.assertEqual(result['versions_written'], 0)
        self.assertEqual(original, e.read_record(self.root, DATE, CODE))

    def test_request_receipts_are_exact_and_have_no_token(self):
        with patch.object(e, '_api_request', return_value=response(bars())):
            self.run_collection()
        payload = e.read_record(self.root, DATE, CODE)['payload']
        for key, source in payload['field_sources'].items():
            receipt = source['request_receipt']
            self.assertEqual(set(receipt), {'api', 'params', 'fields'})
            self.assertNotIn('token', json.dumps(receipt))
            if key in e.MINUTE_KEYS:
                self.assertEqual(receipt['api'], 'stk_mins')
                self.assertEqual(receipt['params']['end_date'], '2026-09-18 15:00:00')

    def test_semantically_invalid_but_hash_valid_records_rejected(self):
        with patch.object(e, '_api_request', return_value=response(bars())):
            self.run_collection()
        original = e.read_record(self.root, DATE, CODE)
        original.pop('source_binding')
        mutations = [
            lambda p: p.update(collector_code_sha256='not-a-hash'),
            lambda p: p.update(rule_version='invented'),
            lambda p: p['values'].update(extra_field=1),
            lambda p: p['values'].update(minute_last_30m_return=-2),
            lambda p: p['values'].update(limit_last_time_minutes=725),
            lambda p: p['field_sources']['minute_realized_vol'].update(source_event_date='20260917'),
            lambda p: p['field_sources']['minute_realized_vol'].update(observed_at_utc='2099-01-01T00:00:00Z'),
            lambda p: p['field_sources']['limit_last_time_minutes'].update(source_path='../../private/token'),
            lambda p: p['field_sources']['minute_realized_vol']['request_receipt']['params'].update(freq='5min'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                corrupt = copy.deepcopy(original)
                mutate(corrupt['payload'])
                digest = e.sha(e.encoded(corrupt['payload']))
                corrupt['payload_sha256'] = digest
                path = self.root / e.OUT / DATE / CODE / (digest + '.json')
                path.write_bytes(e.encoded(corrupt))
                try:
                    with self.assertRaises(ValueError):
                        e.read_record(self.root, DATE, CODE)
                finally:
                    path.unlink()

    def test_private_cache_bad_mode_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            cache = base / 'cache'
            cache.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, 'DIRECTORY_PERMISSIONS'):
                e._private_cache(b'private-body', cache)
            self.assertEqual(cache.stat().st_mode & 0o777, 0o755)
            cache.chmod(0o700)
            link = base / 'link'
            link.symlink_to(cache, target_is_directory=True)
            with self.assertRaises(OSError):
                e._private_cache(b'private-body', link / 'nested')
            self.assertFalse((cache / 'nested').exists())
            target = base / 'outside.json'
            target.write_bytes(b'outside')
            (cache / (e.sha(b'private-body') + '.json')).symlink_to(target)
            with self.assertRaises(OSError):
                e._private_cache(b'private-body', cache)
            self.assertEqual(target.read_bytes(), b'outside')

    def test_private_cache_rejects_wrong_owner_and_loose_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary).resolve()
            with patch.object(e.os, 'geteuid', return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(ValueError, 'DIRECTORY_PERMISSIONS'):
                    e._private_cache(b'private-body', cache)
            e._private_cache(b'private-body', cache)
            path = cache / (e.sha(b'private-body') + '.json')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'FILE_PERMISSIONS'):
                e._private_cache(b'private-body', cache)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_redirects_never_forward_credential_request(self):
        handler = e._NoRedirect()
        for url in ('https://evil.example/steal', 'https://api.tushare.pro/moved'):
            with self.assertRaisesRegex(ValueError, 'REDIRECT_REJECTED'):
                handler.redirect_request(None, None, 307, '', {}, url)
        with patch.object(e.urllib.request, 'build_opener') as create:
            create.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"code":0}'
            e._api_request('stk_mins', {}, e.MINUTE_FIELDS, 'secret-token', 10)
            self.assertIsInstance(create.call_args.args[0], e._NoRedirect)


if __name__ == '__main__':
    unittest.main()
