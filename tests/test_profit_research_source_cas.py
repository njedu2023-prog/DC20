"""Bind a research Pages handoff to its actual successful source CAS."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from scripts import research_pages_handoff as handoff

HEAD, SOURCE, CAS = 'a' * 40, 'b' * 40, 'c' * 40
PREFIX = 'outputs/decision/profit_research/'
POINTER = PREFIX + 'latest.json'
START, END = '2026-11-09T13:41:11Z', '2026-11-09T13:45:20Z'


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def commit(sha=CAS, parent=SOURCE, at='2026-11-09T13:44:57Z'):
    return {'sha': sha, 'parents': [{'sha': parent}],
            'commit': {'committer': {'date': at}}}


class ReadOnlyAPI:
    def __init__(self, root, version):
        self.calls = []
        self.pages = [[commit()]]
        self.metadata = {}
        for path in (POINTER, version):
            raw = (root / path).read_bytes()
            self.metadata[path] = {'type': 'file', 'path': path,
                                   'sha': git_blob(raw), 'size': len(raw)}

    def request(self, route, body=None):
        assert body is None, 'Source provenance checks must remain read-only'
        self.calls.append(route)
        parsed = urlsplit(route)
        query = parse_qs(parsed.query)
        if parsed.path == '/commits':
            assert query['sha'] == [HEAD]
            assert query['path'] == [POINTER]
            assert query['per_page'] == ['100']
            assert 'since' in query and 'until' in query
            return copy.deepcopy(self.pages[int(query['page'][0]) - 1])
        assert parsed.path.startswith('/contents/')
        assert query['ref'][0] in {item['sha'] for page in self.pages for item in page}
        return copy.deepcopy(self.metadata[parsed.path.removeprefix('/contents/')])


class SourceCasTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        data = (json.dumps({'source_revision': SOURCE, 'as_of_date': '20261109',
                            'days': [{'date': '20261109', 'provenance': 'FORMAL_FROZEN_REFERENCE'}]},
                           sort_keys=True) + '\n').encode()
        fingerprint = hashlib.sha256(data).hexdigest()
        self.version = PREFIX + 'versions/' + fingerprint + '.json'
        (self.root / self.version).parent.mkdir(parents=True)
        (self.root / self.version).write_bytes(data)
        (self.root / POINTER).write_text(json.dumps({
            'path': 'versions/' + fingerprint + '.json', 'sha256': fingerprint,
            'source_revision': SOURCE, 'as_of_date': '20261109'}) + '\n')
        self.run = {'id': 42626848568, '_research_job': {
            'id': 129195600302, 'run_id': 42626848568,
            'status': 'completed', 'conclusion': 'success',
            'started_at': START, 'completed_at': END,
            'steps': [{'name': 'Write only research evidence through GitHub API',
                       'status': 'completed', 'conclusion': 'success',
                       'started_at': '2026-11-09T13:44:56Z',
                       'completed_at': '2026-11-09T13:45:18Z'}]}}
        self.api = ReadOnlyAPI(self.root, self.version)

    def invoke(self):
        return handoff.source_cas(self.api, self.root, HEAD, SOURCE, self.run)

    def rejected(self):
        with self.assertRaisesRegex(ValueError, 'RESEARCH_SOURCE_CAS_'):
            self.invoke()

    def test_real_source_cas_can_precede_later_code_only_head(self):
        self.assertNotEqual(HEAD, CAS)
        self.assertEqual(self.invoke(), CAS)
        paths = [urlsplit(route).path for route in self.api.calls]
        self.assertIn('/contents/' + POINTER, paths)
        self.assertIn('/contents/' + self.version, paths)

    def test_old_successful_run_without_its_own_cas_cannot_claim_current_dataset(self):
        self.api.pages = [[]]
        self.rejected()

    def test_parent_or_production_interval_mismatch_rejects_cas(self):
        for candidate in [commit(parent='d' * 40),
                          commit(at='2026-11-09T13:41:10Z'),
                          commit(at='2026-11-09T13:45:21Z'),
                          {**commit(), 'parents': [{'sha': SOURCE}, {'sha': 'd' * 40}]}]:
            with self.subTest(candidate=candidate):
                self.api.pages = [[candidate]]
                self.rejected()

    def test_both_original_git_blobs_and_sizes_must_match_current_bytes(self):
        for which, field in [('pointer', 'sha'), ('version', 'sha'),
                             ('pointer', 'size'), ('version', 'size')]:
            with self.subTest(which=which, field=field):
                self.api = ReadOnlyAPI(self.root, self.version)
                entry = self.api.metadata[POINTER if which == 'pointer' else self.version]
                entry[field] = 'e' * 40 if field == 'sha' else entry[field] + 1
                self.rejected()

    def test_two_plausible_source_commits_are_ambiguous(self):
        self.api.pages = [[commit(), commit(sha='d' * 40)]]
        self.rejected()

    def test_duplicate_commit_across_pages_is_not_unique_provenance(self):
        page = [commit()] + [commit(sha=f'{i:040x}', parent='e' * 40) for i in range(1, 100)]
        self.api.pages = [page, [commit()]]
        self.rejected()
        self.assertTrue(any(parse_qs(urlsplit(route).query).get('page') == ['2']
                            for route in self.api.calls))

    def test_ten_full_pages_are_incomplete_even_if_a_match_was_seen(self):
        pages = [[commit(sha=f'{page * 100 + row + 1:040x}', parent='e' * 40)
                  for row in range(100)] for page in range(10)]
        pages[0][0] = commit()
        self.api.pages = pages
        self.rejected()
        self.assertEqual(len([route for route in self.api.calls if urlsplit(route).path == '/commits']), 10)

    def test_naive_job_times_cannot_authorize_cas(self):
        for field in ('started_at', 'completed_at'):
            with self.subTest(field=field):
                self.run['_research_job']['started_at'] = START
                self.run['_research_job']['completed_at'] = END
                self.run['_research_job'][field] = '2026-11-09T13:44:00'
                self.rejected()

    def test_api_failure_is_not_a_valid_or_empty_source_history(self):
        def unavailable(*args, **kwargs):
            raise ValueError('GITHUB_HTTP_403')
        self.api.request = unavailable
        with self.assertRaisesRegex(ValueError, 'GITHUB_HTTP_403'):
            self.invoke()


if __name__ == '__main__':
    unittest.main()
