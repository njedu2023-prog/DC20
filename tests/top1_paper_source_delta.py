"""Check the deployed source bytes before reconstructing the previous paper-account view."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = 'models/decision_source_surface_review_20261111_top1_count.json'
REVIEW_SHA = '0302825dc8521964abfc5ce6242f7d61662546b60a5a41e28c3a54129c7754db'


@lru_cache(maxsize=1)
def _parse(raw):
    assert sha256(raw).hexdigest() == REVIEW_SHA
    doc = json.loads(raw)
    assert doc['base_commit'] == '03a04ed7cad7f7cc98c033d506eb66cf1633e908'
    assert doc['base_tree'] == 'b8a581c5c37ce7ba6828afeb9237d59ac1c1369e'
    assert doc['schema_version'] == 'dc20_reviewed_source_delta_v1'
    return doc['files']


def files():
    target = ROOT / REVIEW
    assert not target.is_symlink()
    return _parse(target.read_bytes())


@lru_cache(maxsize=1)
def _paths():
    return frozenset(files())


@lru_cache(maxsize=50)
def _restore(path, raw, review_raw):
    item = _parse(review_raw)[path]
    assert sha256(raw).hexdigest() == item['sha256'], path
    text = raw.decode('utf-8')
    end = len(text)
    for start, length, original in reversed(item['inverse']):
        assert 0 <= start <= start + length <= end
        text = text[:start] + original + text[start + length:]
        end = start
    prior = text.encode('utf-8')
    assert sha256(prior).hexdigest() == item['base_sha256'], path
    return prior


def before_count(path, raw):
    if path not in _paths():
        return raw
    return _restore(path, raw, (ROOT / REVIEW).read_bytes())
