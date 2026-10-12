"""Multiple reusable Pages deployments must select their own artifact."""
from pathlib import Path
import hashlib
import json
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]

class PagesArtifactIdentityTests(unittest.TestCase):
    def test_candidate_and_research_deployments_do_not_ambiguous_match(self):
        pages = yaml.load((ROOT / '.github/workflows/deploy_dc20_pages.yml').read_text(), Loader=yaml.BaseLoader)
        research = yaml.load((ROOT / '.github/workflows/profit_research.yml').read_text(), Loader=yaml.BaseLoader)
        default = pages['on']['workflow_call']['inputs']['artifact_name']['default']
        second = research['jobs']['deploy']['with']['artifact_name']
        steps = pages['jobs']['deploy']['steps']
        upload = next(s for s in steps if s.get('uses', '').startswith('actions/upload-pages-artifact@'))
        deploy = next(s for s in steps if s.get('uses', '').startswith('actions/deploy-pages@'))
        binding = "${{ inputs.artifact_name || 'github-pages' }}"
        self.assertEqual(upload['with']['name'], binding)
        self.assertEqual(deploy['with']['artifact_name'], binding)
        artifacts = [{'name': default, 'id': 1}, {'name': second, 'id': 2}]
        for requested, expected in [(default, 1), (second, 2)]:
            self.assertEqual([a['id'] for a in artifacts if a['name'] == requested], [expected])
        self.assertEqual(research['jobs']['deploy']['with']['expected_head'], '${{ needs.accumulate.outputs.head }}')

    def test_frozen_deployment_pin_matches_updated_workflow(self):
        path = '.github/workflows/deploy_dc20_pages.yml'
        freeze = json.loads((ROOT / 'models/decision_model_freeze.json').read_bytes())
        self.assertEqual(freeze['pinned_files'][path], hashlib.sha256((ROOT / path).read_bytes()).hexdigest())

if __name__ == '__main__':
    unittest.main()
