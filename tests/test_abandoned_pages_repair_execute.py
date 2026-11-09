"""Recheck ordering: no incident cancellation can outlive its live evidence."""
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import abandoned_pages_repair as repair


class ExecuteRecheckTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        workflow = self.root / '.github/workflows/deploy_dc20_pages.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_bytes(b'name: fixture\nconcurrency:\n  group: dc20-pages\n')
        self.stack.enter_context(patch.object(repair, 'PINNED_DEPLOY_SHA256',
            hashlib.sha256(workflow.read_bytes()).hexdigest()))
        self.head = 'a' * 40
        self.env = {'REPAIR_MODE': 'cancel', 'EXPECTED_HEAD': self.head,
                    'CONFIRM_ABANDONED_CANCEL': repair.CONFIRM}
        self.evidence = json.loads((Path(__file__).resolve().parents[1] /
            repair.EVIDENCE_PATH).read_bytes())
        self.evidence['review_ready'] = True
        self.client = Mock()
        self.client.cancel_once.return_value = {
            'status': 'CANCEL_REQUEST_ACCEPTED_204', 'publication_complete': False}
        self.environment = self.stack.enter_context(patch.object(
            repair, 'environment', return_value=77))
        self.current_head = self.stack.enter_context(patch.object(
            repair.handoff, 'current_head', return_value=self.head))
        self.original = self.stack.enter_context(patch.object(
            repair, 'original_identity', return_value={'revision.json': b'original'}))
        self.live = self.stack.enter_context(patch.object(
            repair, 'no_other_work', return_value=[]))
        self.backend = self.stack.enter_context(patch.object(
            repair, 'backend_state', return_value='succeed'))
        self.public = self.stack.enter_context(patch.object(
            repair, 'preserved_public', return_value=['revision.json']))
        self.clock = self.stack.enter_context(patch.object(repair, 'datetime'))
        self.clock.now.return_value = datetime.fromisoformat('2026-11-09T22:50:00+08:00')

    def execute(self):
        return repair.execute(self.client, self.env, self.root, self.evidence)

    def rejected(self, reason):
        with self.assertRaisesRegex(ValueError, reason):
            self.execute()
        self.client.cancel_once.assert_not_called()

    def test_audit_checks_all_evidence_but_never_posts(self):
        self.env['REPAIR_MODE'] = 'audit'
        self.env.pop('CONFIRM_ABANDONED_CANCEL')
        result = self.execute()
        self.assertEqual(result['status'], 'REVIEWED_ABANDONED_BACKEND')
        self.assertFalse(result['cancel_requested'])
        self.assertNotEqual(result['status'], 'CLEAR')
        self.assertEqual(self.live.call_count, 2)
        self.assertEqual(self.backend.call_count, 2)
        self.assertEqual(self.public.call_count, 2)
        self.clock.now.assert_not_called()
        self.client.cancel_once.assert_not_called()

    def test_cancel_only_after_both_live_rechecks(self):
        def attempt():
            self.assertEqual(self.live.call_count, 2)
            self.assertEqual(self.backend.call_count, 2)
            self.assertEqual(self.public.call_count, 2)
            self.assertEqual(self.current_head.call_count, 2)
            self.assertEqual(self.clock.now.call_count, 2)
            return {'status': 'CANCEL_REQUEST_ACCEPTED_204'}
        self.client.cancel_once.side_effect = attempt
        result = self.execute()
        self.client.cancel_once.assert_called_once_with()
        self.assertTrue(result['cancel_requested'])
        self.assertFalse(result['publication_complete'])

    def test_live_job_appears_during_immutable_evidence_reads(self):
        self.live.side_effect = [[], ValueError('OTHER_LIVE_RUN_OR_JOB_88')]
        self.rejected('OTHER_LIVE_RUN_OR_JOB_88')
        self.assertEqual(self.live.call_count, 2)

    def test_live_queue_second_read_permission_error_is_not_clear(self):
        self.live.side_effect = [[], ValueError('GITHUB_HTTP_403')]
        self.rejected('GITHUB_HTTP_403')

    def test_final_real_clock_crosses_cutoff(self):
        self.clock.now.side_effect = [datetime.fromisoformat('2026-11-09T23:29:59+08:00'),
                                     datetime.fromisoformat('2026-11-09T23:30:00+08:00')]
        self.rejected('INCIDENT_CANCEL_WINDOW_CLOSED')
        self.assertEqual(self.live.call_count, 2)
        self.assertEqual(self.clock.now.call_count, 2)

    def test_main_moves_during_reads(self):
        self.current_head.side_effect = [self.head, 'b' * 40]
        self.rejected('MAIN_MOVED_BEFORE_CANCEL')

    def test_main_already_moved_before_reads(self):
        self.current_head.return_value = 'b' * 40
        self.rejected('MAIN_MOVED_BEFORE_REPAIR')
        self.original.assert_not_called()

    def test_backend_changes_between_reads(self):
        self.backend.side_effect = ['succeed', 'deployment_in_progress']
        self.rejected('BACKEND_CHANGED_RECHECK')

    def test_public_changes_between_reads(self):
        self.public.side_effect = [['revision.json'], ValueError('PUBLIC_REVISION_CHANGED')]
        self.rejected('PUBLIC_REVISION_CHANGED')

    def test_unreviewed_evidence_stops_before_api(self):
        self.evidence['review_ready'] = False
        self.rejected('REVIEW_EVIDENCE_NOT_READY')
        self.environment.assert_not_called()
        self.original.assert_not_called()

    def test_incomplete_fixed_incident_proof_stops_before_api(self):
        self.evidence['log_proofs'].pop()
        self.rejected('REVIEWED_INCIDENT_PROOF_CHANGED')
        self.environment.assert_not_called()

    def test_missing_manual_phrase_stops_before_api(self):
        self.env['CONFIRM_ABANDONED_CANCEL'] = ''
        self.rejected('MANUAL_CONFIRMATION_REQUIRED')
        self.environment.assert_not_called()

    def test_changed_pinned_deployment_fails_before_api(self):
        (self.root / '.github/workflows/deploy_dc20_pages.yml').write_bytes(b'changed')
        self.rejected('EXISTING_PAGES_LOCK_PIN_MISMATCH')
        self.environment.assert_not_called()


if __name__ == '__main__':
    unittest.main()
