import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.stage_current_profit_research import stage


class StageResearchTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'source'
        (source / 'versions').mkdir(parents=True)
        raw = b'{"days": []}\n'
        digest = hashlib.sha256(raw).hexdigest()
        relative = f'versions/{digest}.json'
        (source / relative).write_bytes(raw)
        (source / 'latest.json').write_text(json.dumps({'path': relative, 'sha256': digest}))
        (source / 'index.html').write_text('research')
        (source / 'versions' / ('0' * 64 + '.json')).write_bytes(b'obsolete')
        return source, relative

    def test_only_current_version_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, relative = self.fixture(root)
            before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
            stage(source, root / 'site')
            self.assertEqual(len(list((root / 'site/versions').iterdir())), 1)
            for name in ['latest.json', relative, 'index.html']:
                self.assertEqual((root / 'site' / name).read_bytes(), before[Path(name)])
            self.assertEqual(before, {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()})

    def test_corrupt_hash_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, relative = self.fixture(root)
            (source / relative).write_text('changed')
            with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'):
                stage(source, root / 'site')
            self.assertFalse((root / 'site').exists())

    def test_symlink_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, relative = self.fixture(root)
            (source / 'escape').symlink_to(root)
            with self.assertRaisesRegex(ValueError, 'SYMLINK'):
                stage(source, root / 'site')
            self.assertFalse((root / 'site').exists())


if __name__ == '__main__':
    unittest.main()
