"""Multiple reusable Pages deployments must select their own artifact.

These checks run before dependency installation, so use only the standard library.
"""
from pathlib import Path
import hashlib
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PagesArtifactIdentityTests(unittest.TestCase):
    def test_candidate_and_research_deployments_do_not_ambiguous_match(self):
        pages = (ROOT / '.github/workflows/deploy_dc20_pages.yml').read_text()
        research = (ROOT / '.github/workflows/profit_research.yml').read_text()
        inputs = pages.split('  workflow_call:', 1)[1].split('permissions:', 1)[0]
        default = re.search(r'artifact_name:\n(?:(?:        .*|\s*)\n)*?        default: (\S+)', inputs).group(1)
        second = re.search(r'^      artifact_name: (\S+)$', research, re.M).group(1)
        upload = re.search(r'uses: actions/upload-pages-artifact@[^\n]+\n(.*?)(?=\n      -)', pages, re.S).group(1)
        deploy = re.search(r'uses: actions/deploy-pages@[^\n]+\n(.*?)(?=\n      -)', pages, re.S).group(1)
        binding = "${{ inputs.artifact_name || 'github-pages' }}"
        self.assertIn('          name: ' + binding, upload)
        self.assertIn('          artifact_name: ' + binding, deploy)
        artifacts = [{'name': default, 'id': 1}, {'name': second, 'id': 2}]
        for requested, expected in [(default, 1), (second, 2)]:
            self.assertEqual([a['id'] for a in artifacts if a['name'] == requested], [expected])
        self.assertIn('expected_head: ${{ needs.accumulate.outputs.head }}', research)

    def test_frozen_deployment_pin_matches_updated_workflow(self):
        path = '.github/workflows/deploy_dc20_pages.yml'
        freeze = json.loads((ROOT / 'models/decision_model_freeze.json').read_bytes())
        self.assertEqual(freeze['pinned_files'][path], hashlib.sha256((ROOT / path).read_bytes()).hexdigest())

if __name__ == '__main__':
    unittest.main()
