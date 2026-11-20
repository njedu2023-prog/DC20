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


@lru_cache(maxsize=8)
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


@lru_cache(maxsize=8)
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
    'a49f49019d61f12c902617e17822f4245d1b7142f66afbf489172dac8fe93ccf',
    'd078093686c87cfb5ce60caa145ddd74af2ceedb',
    '48ac969b04fea90040748e745657b70597f29256',
)


LATE_RESEARCH_UI = (
    'models/decision_source_surface_review_20261120_late_research_ui.json',
    '5e3453f90ad11453bc6d6984b7b4fb4f3bafb4f27906f10940376a66972a13b2',
    'cdb4492dc9b5d1a8d19622e810ecb7e9bb24ef7d',
    'a2e99e81bee2305795c6e1cde65569fc87bfc471',
)


def late_files():
    return _parse(_read(LATE_RESEARCH_UI), LATE_RESEARCH_UI)


LIST_DETAIL_UI = (
    'models/decision_source_surface_review_20261120_list_detail_ui.json',
    '6b38fd22509ff8e7e2f3de45c157fd91dbf226a9fd69024762aed16bc83d8b80',
    '5632192b9731983b7004c1b731169631929208dd',
    '41702eb10bf88f8e92a1c1d2f5eab82e93e3987a',
)


def detail_files():
    return _parse(_read(LIST_DETAIL_UI), LIST_DETAIL_UI)


def before_detail(path, raw):
    if path not in _paths(LIST_DETAIL_UI):
        return raw
    return _restore(path, raw, _read(LIST_DETAIL_UI), LIST_DETAIL_UI)


def before_late(path, raw):
    raw = before_detail(path, raw)
    if path not in _paths(LATE_RESEARCH_UI):
        return raw
    return _restore(path, raw, _read(LATE_RESEARCH_UI), LATE_RESEARCH_UI)


def before_rate(path, raw):
    raw = before_late(path, raw)
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
