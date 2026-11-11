"""Verify each released source before reconstructing the preceding paper-account views."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNT = (
    'models/decision_source_surface_review_20261111_top1_count.json',
    '0302825dc8521964abfc5ce6242f7d61662546b60a5a41e28c3a54129c7754db',
    '03a04ed7cad7f7cc98c033d506eb66cf1633e908',
    'b8a581c5c37ce7ba6828afeb9237d59ac1c1369e',
)
SUSPENSION_REFERENCES = (
    'models/decision_source_surface_review_20261111_suspension_references.json',
    '33dcb86aa0492afac33066891cc7d3567d747c2474d42a521d7c0def67c6c199',
    '4dd9659925f9437bca6eb5094cc18a331d4c5bc6',
    None,
)
RATE = (
    'models/decision_source_surface_review_20261111_top1_rate.json',
    'ce1f457c250e7f087a5bf41b0b83992c94f9e101b6e67e339b1a891311ce6155',
    '79d13a671c64580c246cd8ffdf387efcb0587c70',
    '99c03deaf96080620cad86b222781216a7fccd16',
)


def _read(release):
    target = ROOT / release[0]
    assert not target.is_symlink()
    return target.read_bytes()


@lru_cache(maxsize=4)
def _parse(raw, release):
    _, expected_sha, base_commit, base_tree = release
    assert sha256(raw).hexdigest() == expected_sha
    doc = json.loads(raw)
    assert doc['base_commit'] == base_commit and doc.get('base_tree') == base_tree
    assert doc['schema_version'] == 'dc20_reviewed_source_delta_v1'
    return doc['files']


def files():
    return _parse(_read(COUNT), COUNT)


def rate_files():
    return _parse(_read(RATE), RATE)


@lru_cache(maxsize=3)
def _paths(release):
    return frozenset(_parse(_read(release), release))


@lru_cache(maxsize=100)
def _restore(path, raw, review_raw, release):
    item = _parse(review_raw, release)[path]
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


MONTH = (
    'models/decision_source_surface_review_20261112_monthly_trades.json',
    '18f0d15c3cf7a41f73d1af335e0a3eb2052782a684b8d91baca0b333655e4455',
    'd078093686c87cfb5ce60caa145ddd74af2ceedb',
    '48ac969b04fea90040748e745657b70597f29256',
)


def before_rate(path, raw):
    if path in _paths(MONTH):
        raw = _restore(path, raw, _read(MONTH), MONTH)
    if path not in _paths(RATE):
        return raw
    return _restore(path, raw, _read(RATE), RATE)


def before_count(path, raw):
    raw = before_rate(path, raw)
    if path in _paths(SUSPENSION_REFERENCES):
        raw = _restore(path, raw, _read(SUSPENSION_REFERENCES), SUSPENSION_REFERENCES)
    if path not in _paths(COUNT):
        return raw
    return _restore(path, raw, _read(COUNT), COUNT)
