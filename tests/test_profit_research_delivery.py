import copy
import hashlib
import json
from pathlib import Path
import unittest
from scripts import profit_research_delivery as delivery
from scripts import profit_research_operations as operations


class DeliveryTests(unittest.TestCase):
    def test_writer_barrier_excludes_own_publisher_but_waits_for_settlement(self):
        runs = [dict(id=1, path='.github/workflows/research_candidate_natural_settlement.yml', status='in_progress'),
                dict(id=2, path='.github/workflows/publish_candidate_profit.yml', status='in_progress'),
                dict(id=3, path='.github/workflows/run_primary_d_daily.yml', status='completed')]
        self.assertEqual(delivery.blocking_runs(runs), [1])

    def dataset(self):
        raw = json.dumps({'source_revision': 'a'*40, 'as_of_date': '20261009',
                          'days': [{'date': '20261009'}]}).encode()
        digest = hashlib.sha256(raw).hexdigest()
        pointer = dict(path='versions/'+digest+'.json', sha256=digest,
                       source_revision='a'*40, as_of_date='20261009')
        return pointer, raw

    def test_exact_public_dataset_and_stale_or_corrupt_fail_closed(self):
        pointer, raw = self.dataset()
        delivery.validate_dataset(pointer, raw, '20261009', 'a'*40)
        for day, source, data in [('20261012', 'a'*40, raw),
                                  ('20261009', 'b'*40, raw), ('20261009', 'a'*40, raw+b' ')]:
            with self.assertRaises(ValueError):
                delivery.validate_dataset(pointer, data, day, source)
        pointer['path'] = '../private.json'
        with self.assertRaises(ValueError):
            delivery.validate_dataset(pointer, raw, '20261009', 'a'*40)

    def test_reusable_caller_keeps_real_upstream_and_manual_is_not_natural(self):
        observed = operations._instant('2026-10-09T14:00:00Z')
        raw = dict(event_name='workflow_run', repository=operations.REPOSITORY, ref_name='main',
                   run_id='123', run_attempt='1', github_head_sha='a'*40, source_revision='a'*40,
                   run_started_at_utc='2026-10-09T13:50:00Z', caller_workflow=operations.UPSTREAM,
                   caller_workflow_ref=operations.REPOSITORY+'/.github/workflows/publish_candidate_profit.yml@refs/heads/main',
                   upstream=dict(name='DC20 · Preserve natural candidate publication (research)',
                                 conclusion='success', head_branch='main', repository=operations.REPOSITORY))
        context, errors = operations._context(raw, observed, False)
        self.assertEqual(errors, [])
        self.assertEqual(context['upstream'], raw['upstream'])
        # The same context is replayable without inventing a completed publisher.
        self.assertEqual(operations._context(context, observed, False), (context, []))
        bad = copy.deepcopy(raw); bad['caller_workflow_ref'] += '-wrong'
        self.assertIn('UPSTREAM_WORKFLOW_NOT_ACCEPTED', operations._context(bad, observed, False)[1])
        raw['event_name'] = 'workflow_dispatch'
        self.assertEqual(operations._context(raw, observed, False)[0]['trigger_class'], 'CONTROLLED_TRIGGER')

    def test_main_job_waits_for_research_and_no_fourth_hop(self):
        research = Path('.github/workflows/profit_research.yml').read_text()
        publisher = Path('.github/workflows/publish_candidate_profit.yml').read_text()
        self.assertNotIn('  workflow_run:', research)
        self.assertIn('  workflow_call:', research)
        self.assertIn('uses: ./.github/workflows/profit_research.yml', publisher)
        self.assertIn("inputs.dry_run == false", publisher.split('  research:', 1)[1])
        self.assertIn('needs: [accumulate, deploy]', research)
        self.assertIn('profit_research_delivery.py verify', research)


if __name__ == '__main__':
    unittest.main()
