import copy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import profit_research_operations as ops
from scripts.profit_research_extensions import catalogue

REV = 'a' * 40
DAY = '20260930'
CODE = '000001.SZ'
NOW = '2026-09-30T10:00:00+00:00'
START = '2026-09-30T08:00:00+00:00'


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = patch.object(ops, '_utc_now', return_value=ops._instant(NOW))
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.context = {'event_name': 'schedule', 'run_id': '1234567', 'run_attempt': '1',
            'github_head_sha': REV, 'source_revision': REV, 'run_started_at_utc': START,
            'repository': ops.REPOSITORY, 'ref_name': 'main'}
        opens = {'20260930', '20261009', '20261012', '20261013'}
        rows = ['exchange,cal_date,is_open']
        start = date(2026, 9, 30)
        for i in range(14):
            d = (start + timedelta(days=i)).strftime('%Y%m%d')
            rows.append('SSE,' + d + ',' + str(int(d in opens)))
        self.put(ops.CALENDAR, ('\n'.join(rows) + '\n').encode())
        self.make_doc()

    def put(self, path, raw):
        p = self.root / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
        return {'path': path, 'sha256': ops.sha(raw)}

    def envelope(self, path_prefix, payload, collected):
        digest = ops.sha(ops.encoded(payload))
        return self.put(path_prefix + '/' + digest + '.json', ops.encoded({
            'payload': payload, 'payload_sha256': digest, 'first_collected_at_utc': collected,
            'first_collected_revision': REV}))

    def make_doc(self, collected='2026-09-30T08:10:00+00:00', minute_at='2026-09-30T08:30:00+00:00'):
        values = {f['key']: ('观察' if f['unit'] == '文本' else 1.0) for f in catalogue()}
        for key in ops.MINUTE_KEYS + ('limit_last_time_minutes',): values[key] = None
        payload = {'schema_version': 'dc20_research_extensions_v1', 'signal_date': DAY,
            'source_generated_at_utc': '2026-09-30T08:05:00+00:00',
            'records': [{'ts_code': CODE, 'values': values}]}
        ext_binding = self.envelope('outputs/decision/profit_research/extensions/' + DAY, payload, collected)
        e_values = {k: .01 for k in ops.MINUTE_KEYS}; e_values['limit_last_time_minutes'] = 600.0
        source = {'source_event_date': DAY, 'observed_at_utc': minute_at,
            'collection_kind': 'BEFORE_ENTRY_COLLECTION', 'source_timestamp_before_entry': True,
            'raw_sha256': 'd' * 64, 'point_in_time_independently_verified': False}
        e_payload = {'schema_version': 'dc20_research_enrichment_v1', 'signal_date': DAY,
            'ts_code': CODE, 'values': e_values, 'field_sources': {k: source for k in e_values},
            'minute_quality': {'session_complete': True, 'required_minute_bars': 240,
                'missing_required_bars': 0, 'valid_minute_bars': 241}}
        e_binding = self.envelope('outputs/decision/profit_research/enrichment/' + DAY + '/' + CODE,
                                 e_payload, minute_at)
        p0_binding = self.put('outputs/decision/three_rank_top10_' + DAY + '.json',
            ops.encoded({'signal_date': DAY, 'rows': [{'ts_code': CODE}]}))
        snap_binding = self.put('work/profit_1000_upgrade/candidate_natural_forward/day_' + DAY + '.json',
            ops.encoded({'signal_date': DAY, 'D_source_evidence': {'projection': {'rows': [{'ts_code': CODE}]}}}))
        record = {'signal_date': DAY, 'exec_date': '20261009', 'ts_code': CODE,
            'feature_snapshot_present': True, 'research_extensions': {
                'values': {**values, **e_values}, 'snapshot_binding': ext_binding,
                'source_generated_at_utc': payload['source_generated_at_utc'],
                'first_collected_at_utc': collected, 'value_sources': {k: {'kind': 'RESEARCH_ENRICHMENT'} for k in e_values}},
            'research_enrichment': {**e_payload, 'snapshot_binding': e_binding},
            'outcome': {'status': 'PENDING_T', 'net_return': None}}
        self.doc = {'schema_version': 'dc20_profit_research_v1', 'source_revision': REV,
            'start_date': DAY, 'as_of_date': DAY, 'model': {'sha256': 'b' * 64},
            'days': [{'date': DAY, 'provenance': 'FORMAL_FROZEN_REFERENCE',
                'source_bindings': [p0_binding, snap_binding], 'records': [record]}]}

    def dataset(self):
        raw = ops.encoded(self.doc); digest = ops.sha(raw)
        self.put('outputs/decision/profit_research/versions/' + digest + '.json', raw)
        self.put('outputs/decision/profit_research/latest.json', ops.encoded({
            'path': 'versions/' + digest + '.json', 'sha256': digest}))

    def build(self, **kwargs):
        return ops.build_operation_receipt(self.root, self.doc, run_context=self.context, **kwargs)

    def write(self, receipt):
        self.dataset()
        return ops.write_operation_receipt(self.root, receipt)

    def test_natural_new_collection_holiday_next_session_and_pending(self):
        receipt = self.build()
        self.assertEqual(receipt['natural_evidence_status'], 'NATURAL_BEFORE_ENTRY_RECEIPT')
        self.assertEqual(receipt['natural_new_d_evidence'], [DAY])
        self.assertEqual(receipt['latest_closed_d']['exec_date'], '20261009')
        self.assertEqual(receipt['latest_closed_d']['label_counts']['pending'], 1)
        self.assertFalse(receipt['point_in_time_independently_verified'])
        self.assertEqual(receipt['training_admitted_days'], 0)
        self.assertEqual(receipt['private_storage']['status'], 'NOT_CONFIGURED')

    def test_next_schedule_cannot_promote_reused_old_data(self):
        with patch.object(ops, '_utc_now', return_value=ops._instant('2026-10-04T10:00:00+00:00')):
            self.context['run_started_at_utc'] = '2026-10-04T08:00:00+00:00'
            receipt = self.build()
        self.assertEqual(receipt['natural_evidence_status'], 'NATURAL_REUSED_NOT_NEW')
        self.assertEqual(receipt['natural_new_d_evidence'], [])
        self.assertEqual(receipt['latest_closed_d']['before_entry_records'], 1)
        self.assertEqual(receipt['latest_closed_d']['new_in_this_run_records'], 0)

    def test_push_and_manual_are_controlled_even_with_new_data(self):
        for event in ('push', 'workflow_dispatch'):
            self.context['event_name'] = event
            receipt = self.build()
            self.assertEqual(receipt['natural_evidence_status'], 'CONTROLLED_RUN_NOT_NATURAL_EVIDENCE')
            self.assertEqual(receipt['natural_new_d_evidence'], [])

    def test_explicit_clock_forces_test_only(self):
        receipt = self.build(observed_at=NOW)
        self.assertTrue(receipt['test_only'])
        self.assertEqual(receipt['natural_new_d_evidence'], [])
        self.write(receipt)
        self.assertEqual(ops.summarize_operations(self.root)['receipt_count'], 0)

    def test_upstream_success_repository_and_branch_are_required(self):
        self.context['event_name'] = 'workflow_run'
        receipt = self.build()
        self.assertIn('UPSTREAM_WORKFLOW_NOT_ACCEPTED', receipt['errors'])
        self.context['upstream'] = {'name': ops.UPSTREAM, 'conclusion': 'success',
            'head_branch': 'main', 'repository': ops.REPOSITORY}
        self.assertEqual(self.build()['natural_evidence_status'], 'NATURAL_BEFORE_ENTRY_RECEIPT')
        self.context['upstream']['conclusion'] = 'failure'
        self.assertEqual(self.build()['status'], 'ACCEPTANCE_FAILED')
        self.context['event_name'] = 'schedule'; self.context['repository'] = 'fork/elsewhere'
        self.assertEqual(self.build()['status'], 'ACCEPTANCE_FAILED')
        self.context['repository'] = ops.REPOSITORY; self.context['ref_name'] = 'dev'
        self.assertEqual(self.build()['status'], 'ACCEPTANCE_FAILED')

    def test_calendar_absent_gap_stale_and_no_next_fail_closed(self):
        calendar = self.root / ops.CALENDAR; original = calendar.read_bytes()
        for raw in (b'', b'cal_date,is_open\n20260930,1\n',
                    b'cal_date,is_open\n20260930,1\n20261002,0\n',
                    b'cal_date,is_open\n20260929,1\n'):
            calendar.write_bytes(raw)
            self.assertEqual(self.build()['status'], 'ACCEPTANCE_FAILED')
            self.assertEqual(self.build()['natural_new_d_evidence'], [])
        calendar.write_bytes(original)
        calendar.unlink()
        self.assertEqual(self.build()['status'], 'ACCEPTANCE_FAILED')

    def test_missing_latest_due_d_has_explicit_overdue_gap(self):
        with patch.object(ops, '_utc_now', return_value=ops._instant('2026-10-13T03:00:00+00:00')):
            self.context['run_started_at_utc'] = '2026-10-13T02:00:00+00:00'
            receipt = self.build()
        self.assertEqual(receipt['latest_closed_d']['signal_date'], '20261012')
        self.assertEqual(receipt['latest_due_d']['signal_date'], '20261012')
        self.assertEqual(receipt['status'], 'OVERDUE_COLLECTION_GAP')
        self.assertGreaterEqual(receipt['counts']['overdue_gap_days'], 1)

    def test_late_receipt_not_before_entry_evidence_even_if_collection_was_timely(self):
        self.make_doc(collected='2026-10-09T01:05:00+00:00', minute_at='2026-10-09T01:10:00+00:00')
        with patch.object(ops, '_utc_now', return_value=ops._instant('2026-10-09T01:30:00+00:00')):
            self.context['run_started_at_utc'] = '2026-10-09T01:00:00+00:00'
            receipt = self.build()
        self.assertEqual(receipt['latest_closed_d']['before_entry_records'], 1)
        self.assertEqual(receipt['natural_new_d_evidence'], [])

    def test_minute_before_d_close_is_rejected(self):
        self.make_doc(minute_at='2026-09-30T06:59:00+00:00')
        self.assertEqual(self.build()['latest_closed_d']['before_entry_records'], 0)

    def test_metadata_tamper_not_accepted(self):
        record = self.doc['days'][0]['records'][0]
        record['research_extensions']['source_generated_at_utc'] = '2026-09-30T08:00:00+00:00'
        self.assertIn('TIMING_EVIDENCE_UNAVAILABLE_OR_REJECTED', self.build()['latest_closed_d']['reasons'])
        self.make_doc(); record = self.doc['days'][0]['records'][0]
        record['research_enrichment']['minute_quality']['valid_minute_bars'] = 240
        self.assertEqual(self.build()['natural_new_d_evidence'], [])

    def test_wrong_first_collection_revision_not_new(self):
        record = self.doc['days'][0]['records'][0]
        binding = record['research_extensions']['snapshot_binding']
        path = self.root / binding['path']; env = json.loads(path.read_bytes())
        env['first_collected_revision'] = 'c' * 40
        binding.update(self.put(binding['path'], ops.encoded(env)))
        self.assertEqual(self.build()['latest_closed_d']['before_entry_records'], 1)
        self.assertEqual(self.build()['natural_new_d_evidence'], [])

    def test_membership_mismatch_rejected(self):
        binding = self.doc['days'][0]['source_bindings'][0]
        binding.update(self.put(binding['path'], ops.encoded({'signal_date': DAY, 'rows': [{'ts_code': '000002.SZ'}]})))
        self.assertFalse(self.build()['latest_closed_d']['frozen_membership_matched'])

    def test_incomplete_fields_and_partial_minutes_do_not_become_zero(self):
        record = self.doc['days'][0]['records'][0]
        record['research_extensions']['values']['gap_open'] = None
        receipt = self.build()
        self.assertEqual(receipt['latest_closed_d']['feature_complete_records'], 0)
        self.assertEqual(receipt['natural_new_d_evidence'], [])
        self.make_doc(); self.doc['days'][0]['records'][0]['research_enrichment']['minute_quality']['session_complete'] = False
        self.assertEqual(self.build()['latest_closed_d']['minute_complete_records'], 0)

    def test_terminal_pending_missing_are_separate(self):
        record = self.doc['days'][0]['records'][0]
        cases = [({'status': 'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY', 'net_return': .1, 'proxy_fill': 1}, 'settled'),
                 ({'status': 'NO_FILL_CAPACITY', 'net_return': 0, 'proxy_fill': 0}, 'no_fill'),
                 ({'status': 'PENDING_T1', 'net_return': None}, 'pending'),
                 ({'status': 'MISSING_COUNTERFACTUAL_LABEL', 'net_return': None}, 'missing_or_invalid')]
        for outcome, bucket in cases:
            record['outcome'] = outcome
            self.assertEqual(self.build()['latest_closed_d']['label_counts'][bucket], 1)

    def test_private_storage_configuration_not_verification_and_sanitized(self):
        receipt = self.build(private_storage_configured=True, storage_summary={
            'status': 'REPORTED_COMPLETE', 'expected_hashes': 2, 'covered_hashes': 2,
            'url': 'secret-location', 'token': 'test-secret'})
        self.assertFalse(receipt['private_storage']['independently_verified'])
        self.assertIn('PRIVATE_STORAGE_NOT_VERIFIED', receipt['notices'])
        self.assertNotIn('secret', json.dumps(receipt))

    def test_no_input_mutation_and_binding_extra_fields_not_public(self):
        self.doc['days'][0]['source_bindings'][0]['extra'] = 'DO_NOT_PUBLISH'
        before = copy.deepcopy(self.doc)
        receipt = self.build()
        self.assertEqual(self.doc, before)
        self.assertNotIn('DO_NOT_PUBLISH', json.dumps(receipt))

    def test_write_immutable_and_summary_replays_bound_dataset(self):
        receipt = self.build(); binding = self.write(receipt)
        self.assertEqual(ops.write_operation_receipt(self.root, receipt), binding)
        summary = ops.summarize_operations(self.root)
        self.assertEqual(summary['receipt_count'], 1)
        self.assertEqual(summary['natural_new_d_evidence_days'], [DAY])
        self.assertEqual(summary['natural_receipt_count'], 1)
        self.assertFalse(summary['training_allowed'])

    def test_historical_receipt_survives_calendar_extension_and_outcome_projection_update(self):
        receipt = self.build(); self.write(receipt)
        with (self.root / ops.CALENDAR).open('a') as stream: stream.write('SSE,20261014,1\n')
        self.put('outputs/decision/candidate_profit_v1/day_' + DAY + '.json', ops.encoded({'changed': True}))
        self.assertEqual(ops.summarize_operations(self.root)['natural_new_d_evidence_days'], [DAY])

    def test_hash_valid_forged_natural_receipt_rejected_by_replay(self):
        self.context['event_name'] = 'push'
        receipt = self.build(); self.dataset()
        receipt['natural_evidence_status'] = 'NATURAL_BEFORE_ENTRY_RECEIPT'
        receipt['natural_new_d_evidence'] = [DAY]
        self.write(receipt)
        summary = ops.summarize_operations(self.root)
        self.assertEqual(summary['status'], 'ACCEPTANCE_FAILED')
        self.assertEqual(summary['natural_new_d_evidence_days'], [])

    def test_receipt_extra_sensitive_metadata_is_rejected(self):
        receipt = self.build(); self.write(receipt)
        receipt['raw_provider_body'] = 'DO_NOT_PUBLISH'
        raw = ops.encoded(receipt); digest = ops.sha(raw)
        self.put(ops.OUT + '/20260930/' + digest + '.json', raw)
        summary = ops.summarize_operations(self.root)
        self.assertEqual(summary['status'], 'ACCEPTANCE_FAILED')
        self.assertNotIn('DO_NOT_PUBLISH', json.dumps(summary))

    def test_summary_tamper_or_missing_dataset_is_rejected(self):
        receipt = self.build(); binding = self.write(receipt)
        path = self.root / binding['path']; path.write_bytes(path.read_bytes() + b' ')
        self.assertEqual(ops.summarize_operations(self.root)['status'], 'ACCEPTANCE_FAILED')
        path.write_bytes(ops.encoded(receipt))
        (self.root / receipt['dataset_binding']['path']).unlink()
        self.assertEqual(ops.summarize_operations(self.root)['status'], 'ACCEPTANCE_FAILED')

    def test_controlled_and_replay_counts_have_distinct_denominators(self):
        self.context['event_name'] = 'push'; self.write(self.build())
        self.context['event_name'] = 'schedule'; self.context['run_started_at_utc'] = '2026-10-04T08:00:00+00:00'
        with patch.object(ops, '_utc_now', return_value=ops._instant('2026-10-04T10:00:00+00:00')):
            self.write(self.build())
            summary = ops.summarize_operations(self.root)
        self.assertEqual(summary['controlled_receipt_count'], 1)
        self.assertEqual(summary['replay_receipt_count'], 1)
        self.assertEqual(summary['natural_new_d_evidence_days'], [])

    def test_cli_dataset_pointer_sha_guard(self):
        self.dataset()
        self.assertEqual(ops._latest_dataset(self.root), self.doc)
        pointer = self.root / 'outputs/decision/profit_research/latest.json'
        value = json.loads(pointer.read_bytes()); value['sha256'] = 'c' * 64
        pointer.write_bytes(ops.encoded(value))
        with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'): ops._latest_dataset(self.root)

    def test_no_receipts_not_complete(self):
        summary = ops.summarize_operations(self.root)
        self.assertEqual(summary['status'], 'NOT_YET_RECORDED')
        self.assertIsNone(summary['latest_receipt'])
        self.assertEqual(summary['private_storage']['status'], 'NOT_CONFIGURED')

    def test_real_d14_selected_outcomes_remain_pending_and_unchanged(self):
        # Exact outcome payloads from native D14 research version 2d4e6719...;
        # source labels are reused, never converted to an outcome or return.
        outcomes = {
            '000823.SZ': {'actual_execution_claimed': False, 'actual_exit_date': None,
                'basis': 'FORMAL_SELECTED_PROXY', 'label_available_date': None,
                'net_return': None, 'proxy_fill': None, 'status': 'PENDING_T_NOT_DUE'},
            '600202.SH': {'actual_execution_claimed': False, 'actual_exit_date': None,
                'basis': 'FORMAL_SELECTED_PROXY', 'label_available_date': None,
                'net_return': None, 'proxy_fill': None, 'status': 'PENDING_T_NOT_DUE'},
        }
        for code, outcome in outcomes.items():
            with self.subTest(ts_code=code):
                self.doc['days'][0]['records'][0]['outcome'] = copy.deepcopy(outcome)
                before = copy.deepcopy(self.doc)
                receipt = self.build()
                self.assertEqual(receipt['latest_closed_d']['label_counts'],
                    {'settled': 0, 'no_fill': 0, 'pending': 1, 'missing_or_invalid': 0})
                self.assertEqual(self.doc, before)
                self.assertEqual(receipt['natural_evidence_status'], 'NATURAL_BEFORE_ENTRY_RECEIPT')
                self.assertEqual(receipt['training_admitted_days'], 0)

    def test_only_known_pending_states_with_null_return_count_pending(self):
        record = self.doc['days'][0]['records'][0]
        for status in ('PENDING_T', 'PENDING_T1', 'PENDING_T_NOT_DUE'):
            for value in (None, 0, .1, False, '0'):
                with self.subTest(status=status, value=value):
                    record['outcome'] = {'status': status, 'net_return': value, 'proxy_fill': 1}
                    counts = self.build()['latest_closed_d']['label_counts']
                    expected = 'pending' if value is None else 'missing_or_invalid'
                    self.assertEqual(counts[expected], 1)
                    self.assertEqual(sum(counts.values()), 1)

    def test_unknown_pending_and_genuine_missing_stay_missing(self):
        record = self.doc['days'][0]['records'][0]
        for status in ('PENDING_T1_NOT_DUE', 'PENDING_T_UNKNOWN',
                       'MISSING_COUNTERFACTUAL_LABEL', 'MISSING_T_TRUTH', '', None):
            with self.subTest(status=status):
                record['outcome'] = {'status': status, 'net_return': None, 'proxy_fill': None}
                counts = self.build()['latest_closed_d']['label_counts']
                self.assertEqual(counts,
                    {'settled': 0, 'no_fill': 0, 'pending': 0, 'missing_or_invalid': 1})

    def legacy_selected_receipt(self):
        self.doc['days'][0]['records'][0]['outcome'] = {
            'status': 'PENDING_T_NOT_DUE', 'net_return': None, 'proxy_fill': None}
        receipt = ops._build_receipt_at(self.root.resolve(), self.doc, self.context,
            ops._instant(NOW), False, None, False, label_count_contract=None)
        # Full-object digest independently generated by the original module at
        # immutable main 9c2a418f29da5f0b8e6cf26a230f58db41f3a4c2.
        self.assertEqual(ops.sha(ops.encoded(receipt)),
            '022744450e7685b551a11a96020a19ffd2c5dc41760a1ada67515121e13a2e21')
        return receipt

    def test_original_receipt_replays_exactly_and_new_contract_coexists(self):
        old = self.legacy_selected_receipt()
        old_binding = self.write(old)
        old_path = self.root / old_binding['path']
        original = old_path.read_bytes()
        summary = ops.summarize_operations(self.root)
        self.assertEqual(summary['receipt_count'], 1)
        self.assertEqual(summary['errors'], [])
        self.assertEqual(summary['latest_receipt'], old)
        self.assertNotIn('label_count_contract', old)
        self.assertEqual(old['latest_closed_d']['label_counts']['missing_or_invalid'], 1)
        before = copy.deepcopy(self.doc)
        with patch.object(ops, '_utc_now', return_value=ops._instant('2026-09-30T10:00:01+00:00')):
            new = self.build()
            self.write(new)
            summary = ops.summarize_operations(self.root)
        self.assertEqual(new['label_count_contract'], 'canonical_pending_v2')
        self.assertEqual(new['latest_closed_d']['label_counts']['pending'], 1)
        self.assertEqual(new['latest_closed_d']['label_counts']['missing_or_invalid'], 0)
        self.assertEqual(summary['latest_receipt'], new)
        self.assertEqual(summary['receipt_count'], 2)
        self.assertEqual(summary['errors'], [])
        self.assertEqual(summary['natural_receipt_count'], 2)
        self.assertEqual(summary['natural_new_d_evidence_days'], [DAY])
        self.assertEqual(old_path.read_bytes(), original)
        self.assertEqual(self.doc, before)
        self.assertFalse(summary['training_allowed'])

    def test_unknown_null_and_mixed_count_contracts_fail_full_replay(self):
        old = self.legacy_selected_receipt()
        new = self.build()
        self.write(old)  # Save its bound dataset and immutable calendar.
        variants = []
        for contract in (None, '', 'canonical_pending_v1', 'canonical_pending_v3', False, []):
            invalid = copy.deepcopy(new)
            invalid['label_count_contract'] = contract
            variants.append(invalid)
        old_with_new_contract = copy.deepcopy(old)
        old_with_new_contract['label_count_contract'] = ops.LABEL_COUNT_CONTRACT
        variants.append(old_with_new_contract)
        new_without_contract = copy.deepcopy(new)
        del new_without_contract['label_count_contract']
        variants.append(new_without_contract)
        forged_old = copy.deepcopy(old)
        forged_old['latest_closed_d']['label_counts'].update(pending=1, missing_or_invalid=0)
        variants.append(forged_old)
        for receipt in variants:
            with self.subTest(contract=receipt.get('label_count_contract'), counts=receipt['latest_closed_d']['label_counts']):
                with self.assertRaises(ValueError):
                    ops._revalidate_receipt(self.root.resolve(), receipt)
                binding = self.write(receipt)
                try:
                    summary = ops.summarize_operations(self.root)
                    self.assertEqual(summary['status'], 'ACCEPTANCE_FAILED')
                    self.assertEqual(summary['errors'], ['OPERATION_RECEIPT_REJECTED'])
                    self.assertEqual(summary['receipt_count'], 1)
                finally:
                    (self.root / binding['path']).unlink()


if __name__ == '__main__':
    unittest.main()
