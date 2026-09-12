"""Synthetic multi-round economics/provenance tests, never real cloud proof.

Only fixture market transport and miniature universe sizes are substituted.
Frozen label builders, exit mathematics, source codecs and validators run
unchanged. A private candidate fixture authority is explicitly constructed;
minute authorities are issued by the actual verifier over simulated artifacts.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import shutil
import socket
from types import SimpleNamespace

import pytest

from work.profit_1000_upgrade import candidate_market_chain as chain
from work.profit_1000_upgrade import candidate_labels_overlay as overlay
from work.profit_1000_upgrade import candidate_scope_verify as cv
from work.profit_1000_upgrade import minute_gap_collect as mc, minute_gap_verify as mv
from work.profit_1000_upgrade.test_candidate_labels_overlay import candidate
from work.profit_1000_upgrade.test_labels import binding, daily, write_csv
from work.profit_1000_upgrade.test_minute_gap_collect import FakeClock, FixedDatetime

BASE = Path(__file__).resolve().parents[2]
D, T, A, B, C = "20260630", "20260701", "20260702", "20260703", "20260706"
CODE, NO_FILL = "600000.SH", "600001.SH"
REAL_BUDGET = mc.RequestBudget


def minute_response(day, price):
    fields = list(overlay.minute_truth.FIELDS)
    rows = [{"ts_code": CODE, "trade_time": stamp, "open": price, "close": price,
             "high": price, "low": price, "vol": 100, "amount": price * 100}
            for stamp in overlay.minute_truth._minute.expected_bar_ends(day)]
    return json.dumps({"code": 0, "data": {"fields": fields,
        "items": [[r[k] for k in fields] for r in rows], "count": 0, "has_more": False}}).encode()


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, "EXPECTED_ROWS", 2)
    monkeypatch.setattr(chain, "EXPECTED_DATES", 1)
    root = tmp_path / "original"; root.mkdir()
    cal = root / "data/market/trade_cal_sse.csv"; cal.parent.mkdir(parents=True)
    shutil.copyfile(BASE / "data/market/trade_cal_sse.csv", cal)
    features = root / "fixture_feature_source.json"; features.write_text('{"synthetic_D_only_fixture":true}')
    manifest = {"schema_version": overlay.base_labels.SCHEMA, "evidence_kind": "RETROSPECTIVE_D_ONLY_RECONSTRUCTION",
        "feature_columns": ["promotion_probability"], "source_bindings": [binding(root, features)],
        "expected_candidate_codes": {D: [CODE, NO_FILL]}, "rows": [], "plan_version": "v3", **overlay.policy_v3.CONTRACT}
    for rank, code in enumerate((CODE, NO_FILL), 1):
        manifest["rows"].append({"signal_date": D, "ts_code": code, "stage": 2, "promotion_rank": rank,
            "feature_as_of_date": D, "feature_available_at": "2026-06-30T23:59:00+08:00",
            "features": {"promotion_probability": .6}, "shadow_max_price": None})
    daily(root, T, price=10)
    directory = root / f"data/market/raw/2026/{T}"
    for name in ("daily", "stk_limit"):
        path = directory / (name + '.csv')
        with path.open() as handle: rows = list(csv.DictReader(handle))
        extra = dict(rows[0], ts_code=NO_FILL)
        if name == 'daily': extra.update(open=11, high=11, low=11, close=11)
        write_csv(path, rows + [extra])
    daily(root, A, price=11, pre=10, up=11, down=9)
    daily(root, B, price=12.1, pre=11, up=12.1, down=9.9)
    daily(root, C, price=9.8, pre=12.1, up=13.31, down=8)
    candidate_root = tmp_path / 'candidate'; candidate_root.mkdir()
    candidate(candidate_root, day=T, code=CODE, price=10)
    candidate(candidate_root, day=T, code=NO_FILL, price=11, amount=22_000_000)
    _, candidate_files = cv._scan(candidate_root)
    authority = cv.VerifiedCandidateCollection(_key=cv._KEY, root=candidate_root,
        base_archive_sha256='a' * 64, receipt_sha256='b' * 64, frozen_manifest_sha256=overlay._digest(manifest),
        gap_pairs=((T, CODE), (T, NO_FILL)), successful_pairs=((T, CODE), (T, NO_FILL)),
        source_bindings=[binding(candidate_root, p) for p in candidate_root.rglob('*') if p.is_file()],
        base_file_bindings=[binding(root, p) for p in root.rglob('*') if p.is_file()],
        scope={"explicit_synthetic_unit_fixture": True}, as_of_date=chain.AS_OF_DATE,
        run_id='123456', run_commit='a' * 40, status='CANDIDATE_SOURCES_COLLECTED',
        source_only=True, label_source_eligible=False, _output_bindings=list(candidate_files.values()), _code_bindings=())
    authority.assert_unchanged()
    def build(target, scopes=(), priors=(), manifest_override=None):
        return chain.build_labels(target, manifest if manifest_override is None else manifest_override,
            as_of_date=chain.AS_OF_DATE, candidate_source_root=candidate_root,
            verified_candidate_scope=authority, verified_minute_scopes=list(scopes), prior_label_reports=list(priors))
    initial = build(root)
    assert initial['rows'][0]['label_status'] == 'PENDING_EXIT_MISSING_MINUTES'
    assert initial['rows'][0]['missing_evidence_date'] == A
    assert initial['rows'][1]['label_status'] == 'NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED'
    monkeypatch.setattr(mc, 'datetime', FixedDatetime)
    counter = 0
    def market(prior, day, price, *, fail=False):
        nonlocal counter
        counter += 1
        directory = tmp_path / f'round-{counter}'; directory.mkdir()
        prior_path = directory / 'prior.json'; prior_path.write_bytes(chain.canonical(prior))
        label_sha = mv.file_sha(prior_path)
        plan = mc.expected_plan([(day, CODE)], label_sha)
        plan_path = directory / 'registered.json'; plan_path.write_bytes(mc.json_bytes(plan))
        plan_sha = mv.file_sha(plan_path)
        monkeypatch.setenv('GITHUB_RUN_ID', str(200 + counter)); monkeypatch.setenv('GITHUB_SHA', 'c' * 40)
        monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
        clock = FakeClock()
        monkeypatch.setattr(mc, 'RequestBudget', lambda total: REAL_BUDGET(total, clock=clock, sleep=clock.sleep))
        def simulated(contract, token):
            assert token == 'explicit-synthetic-not-real'
            return b'{"code":-1,"data":null}' if fail else minute_response(day, price)
        monkeypatch.setattr(mc, 'official_call', simulated)
        output = directory / 'source'
        mc.run_collection(output, token='explicit-synthetic-not-real', plan_path=plan_path,
            expected_plan_sha256=plan_sha, expected_label_report_sha256=label_sha)
        scope = mv.verify(output, expected_run_id=str(200 + counter), expected_run_commit='c' * 40,
            expected_plan_sha256=plan_sha, expected_label_report_sha256=label_sha, registered_plan_path=plan_path)
        return scope, prior_path
    def stage(name, scopes):
        target = tmp_path / name; shutil.copytree(root, target)
        for scope in scopes:
            for item in scope.source_bindings:
                target_path = target / item['path']; target_path.parent.mkdir(parents=True, exist_ok=True)
                assert not target_path.exists()
                target_path.write_bytes((scope.root / item['path']).read_bytes())
        return target
    return SimpleNamespace(root=root, manifest=manifest, candidate_root=candidate_root, candidate=authority,
        initial=initial, build=build, market=market, stage=stage, tmp=tmp_path)


@pytest.fixture
def two_rounds(synthetic):
    s = synthetic
    first, prior0 = s.market(s.initial, A, 11)
    root1 = s.stage('stage1', [first]); report1 = s.build(root1, [first], [prior0])
    assert report1['rows'][0]['missing_evidence_date'] == B
    second, prior1 = s.market(report1, B, 12.1)
    root2 = s.stage('stage2', [first, second])
    return SimpleNamespace(s=s, first=first, second=second, priors=[prior0, prior1], root=root2, report1=report1)


def test_zero_and_one_round_return_frozen_exact_structure(two_rounds):
    x, s = two_rounds, two_rounds.s
    assert s.initial == overlay.build_labels(s.root, s.manifest, as_of_date=chain.AS_OF_DATE,
        candidate_source_root=s.candidate_root, verified_scope=s.candidate)
    expected = overlay.build_labels(x.s.tmp / 'stage1', s.manifest, as_of_date=chain.AS_OF_DATE,
        candidate_source_root=s.candidate_root, verified_scope=s.candidate, verified_market_scope=x.first,
        market_source_root=x.first.root, prior_label_report=x.priors[0])
    assert expected == x.report1
    assert 'market_source_chain_document' not in expected


def test_two_real_codec_rounds_keep_pending_and_explicit_composite(two_rounds):
    x = two_rounds; before = snapshot(x.s.tmp)
    report = x.s.build(x.root, [x.first, x.second], x.priors)
    assert snapshot(x.s.tmp) == before
    row = report['rows'][0]
    assert row['label_status'] == 'PENDING_EXIT_MISSING_MINUTES' and row['missing_evidence_date'] == C
    assert row['slot_net_return'] is None and row['net_return'] is None
    assert row['minute_source_observed_dates'] == [A, B]
    doc = report['market_source_chain_document']
    assert report['market_collection_receipt_kind'] == doc['digest_kind'] == chain.CHAIN_KIND
    assert report['market_collection_receipt_sha256'] == report['market_source_chain_sha256'] == chain.digest(doc)
    assert report['market_collection_receipt_sha256'] not in {x.first.receipt_sha256, x.second.receipt_sha256}
    assert report['market_registered_plan_sha256'] is report['market_registered_label_report_sha256'] is None
    assert [r['run_id'] for r in doc['rounds']] == [x.first.run_id, x.second.run_id]
    assert report['source_overlay_contract'] == overlay.CONTRACT
    assert doc['round_count'] == 2 and doc['single_github_collection_claimed'] is False
    assert report['files_written'] == 0 and report['training_performed'] is report['production_activation_allowed'] is False


def test_third_round_real_exit_keeps_loss_cost_and_prior_no_fill(two_rounds):
    x = two_rounds; report2 = x.s.build(x.root, [x.first, x.second], x.priors)
    third, prior2 = x.s.market(report2, C, 9.8)
    root3 = x.s.stage('stage3', [x.first, x.second, third]); before = snapshot(x.s.tmp)
    report3 = x.s.build(root3, [x.first, x.second, third], x.priors + [prior2])
    assert snapshot(x.s.tmp) == before
    row = report3['rows'][0]
    assert row['label_status'] == overlay.base_labels.SETTLED
    assert row['net_return'] == pytest.approx(-.0245)
    assert row['slot_net_return'] == row['conditional_net_return'] == row['net_return']
    assert row['actual_exit_date'] == C and row['held_limit_up_sessions'] == 2
    assert row['actual_exit_time'] == '2026-07-06T10:00:00+08:00'
    assert row['auction_source_policy_id'] == overlay.candidate_source.SOURCE_POLICY_ID
    assert row['minute_source_policy_id'] == overlay.policy_v3.MINUTE_SOURCE_POLICY_ID
    assert report3['market_source_chain_document']['round_count'] == 3
    previous, current = deepcopy(x.s.initial['rows'][1]), deepcopy(report3['rows'][1])
    previous.pop('cohort_complete'); current.pop('cohort_complete'); assert previous == current
    assert report3['cohorts_by_date'][D]['complete']


def test_partial_last_round_retains_pending_but_cannot_start_next(two_rounds):
    x = two_rounds; failed, prior = x.s.market(x.report1, B, 12.1, fail=True)
    root2 = x.s.stage('partial-final', [x.first, failed])
    out = x.s.build(root2, [x.first, failed], [x.priors[0], prior])
    assert out['rows'][0]['missing_evidence_date'] == B and out['rows'][0]['net_return'] is None
    third, next_prior = x.s.market(out, C, 9.8)
    root3 = x.s.stage('after-partial', [x.first, failed, third])
    with pytest.raises(ValueError, match='INCOMPLETE_PREVIOUS'):
        x.s.build(root3, [x.first, failed, third], [x.priors[0], prior, next_prior])


@pytest.mark.parametrize('value', [True, {}, SimpleNamespace(), [True]])
def test_private_minute_authority_type_not_duck_typed(value):
    with pytest.raises(ValueError, match='EXACT_MINUTE_AUTHORITY'): chain._minute_authority(value)


@pytest.mark.parametrize('value', [True, {}, SimpleNamespace()])
def test_private_candidate_authority_required(value):
    with pytest.raises(ValueError, match='EXACT_CANDIDATE_AUTHORITY'): chain._candidate_authority(value)


@pytest.mark.parametrize('kind', ['not_sequence', 'unequal', 'nine', 'generator'])
def test_chain_argument_limits(synthetic, kind):
    s = synthetic
    scopes, priors = [], []
    if kind == 'not_sequence': scopes = {}
    if kind == 'unequal': priors = ['path']
    if kind == 'nine': scopes, priors = [None] * 9, ['path'] * 9
    if kind == 'generator': scopes = iter([])
    with pytest.raises(ValueError, match='EXACT_BOUNDED'):
        chain.build_labels(s.root, s.manifest, as_of_date=chain.AS_OF_DATE, candidate_source_root=s.candidate_root,
            verified_candidate_scope=s.candidate, verified_minute_scopes=scopes, prior_label_reports=priors)


def test_scope_is_deep_immutable(two_rounds):
    with pytest.raises((FrozenInstanceError, AttributeError)): two_rounds.first.status = 'MINUTE_GAPS_PARTIAL'
    with pytest.raises(TypeError): two_rounds.first.source_bindings[0]['sha256'] = '0' * 64


@pytest.mark.parametrize('change', ['plan_sha', 'receipt_sha', 'prior_sha', 'kind', 'asof', 'candidate_receipt', 'base_sha', 'manifest', 'duplicate_row', 'drop_row', 'missing_kind', 'future_missing', 'wrong_code', 'source_origin', 'source_hash', 'fake_chain', 'economic_contract', 'feature', 'terminal_extra'])
def test_rehashed_second_prior_is_not_permission(two_rounds, change):
    x = two_rounds; prior = deepcopy(x.report1)
    if change == 'plan_sha': prior['market_registered_plan_sha256'] = 'd' * 64
    if change == 'receipt_sha': prior['market_collection_receipt_sha256'] = 'd' * 64
    if change == 'prior_sha': prior['market_registered_label_report_sha256'] = 'd' * 64
    if change == 'kind': prior['market_collection_receipt_kind'] = chain.CHAIN_KIND
    if change == 'asof': prior['as_of_date'] = '20260914'
    if change == 'candidate_receipt': prior['candidate_collection_receipt_sha256'] = 'd' * 64
    if change == 'base_sha': prior['base_archive_sha256'] = 'd' * 64
    if change == 'manifest': prior['candidate_manifest_sha256'] = 'd' * 64
    if change == 'duplicate_row': prior['rows'][1] = deepcopy(prior['rows'][0])
    if change == 'drop_row': prior['rows'].pop()
    if change == 'missing_kind': prior['rows'][0]['missing_evidence_kind'] = 'daily'
    if change == 'future_missing': prior['rows'][0]['missing_evidence_date'] = '20260914'
    if change == 'wrong_code': prior['rows'][0]['missing_evidence_code'] = NO_FILL
    if change == 'source_origin': prior['source_files'][0]['origin'] = 'unregistered'
    if change == 'source_hash': prior['source_files'][0]['sha256'] = 'd' * 64
    if change == 'fake_chain': prior['market_source_chain_document'] = {'round_count': 1}
    if change == 'economic_contract': prior['source_overlay_contract']['round_trip_cost_rate'] = 0
    if change == 'feature': prior['rows'][0]['features']['promotion_probability'] = .99
    if change == 'terminal_extra': prior['rows'][1]['unregistered_terminal_change'] = True
    second, prior_path = x.s.market(prior, B, 12.1)
    target = x.s.stage('bad-prior-' + change, [x.first, second])
    with pytest.raises(ValueError): x.s.build(target, [x.first, second], [x.priors[0], prior_path])


@pytest.mark.parametrize('change', ['document', 'digest', 'single_receipt', 'single_plan', 'drop_kind', 'round_run', 'round_source'])
def test_rehashed_composite_prefix_tampering(two_rounds, change):
    x = two_rounds; prior = x.s.build(x.root, [x.first, x.second], x.priors)
    if change == 'document': prior['market_source_chain_document']['round_count'] = 1
    if change == 'digest': prior['market_source_chain_sha256'] = 'd' * 64
    if change == 'single_receipt': prior['market_collection_receipt_sha256'] = x.second.receipt_sha256
    if change == 'single_plan': prior['market_registered_plan_sha256'] = x.second.plan_sha256
    if change == 'drop_kind': prior.pop('market_collection_receipt_kind')
    if change == 'round_run': prior['market_source_chain_document']['rounds'][0]['run_id'] = '99'
    if change == 'round_source': prior['market_source_chain_document']['rounds'][0]['source_bindings'].pop()
    third, path = x.s.market(prior, C, 9.8)
    target = x.s.stage('bad-composite-' + change, [x.first, x.second, third])
    with pytest.raises(ValueError): x.s.build(target, [x.first, x.second, third], x.priors + [path])


def test_retry_pair_forbidden_even_with_rehashed_prior(two_rounds):
    x = two_rounds; prior = deepcopy(x.report1); prior['rows'][0]['missing_evidence_date'] = A
    second, path = x.s.market(prior, A, 11)
    target = x.s.stage('retry', [x.first])
    with pytest.raises(ValueError, match='RETRY_FORBIDDEN'):
        x.s.build(target, [x.first, second], [x.priors[0], path])


def test_unregistered_gap_not_accepted(two_rounds):
    x = two_rounds
    second, path = x.s.market(x.report1, C, 9.8)
    target = x.s.stage('wrong-gap', [x.first, second])
    with pytest.raises(ValueError, match='EXACT_PRIOR_GAPS'):
        x.s.build(target, [x.first, second], [x.priors[0], path])


@pytest.mark.parametrize('change', ['source_data', 'source_meta', 'augmented_data', 'missing_meta', 'extra_file', 'extra_dir', 'hardlink', 'symlink', 'fifo', 'original_daily', 'prior_bytes', 'plan_bytes', 'same_scope_root'])
def test_physical_provenance_changes_close_gate(two_rounds, change):
    x = two_rounds; scope = x.second; paths = overlay.minute_truth.paths(scope.root, B, CODE)
    if change == 'source_data': paths[0].write_bytes(paths[0].read_bytes() + b' ')
    if change == 'source_meta': paths[1].write_bytes(paths[1].read_bytes() + b' ')
    augmented = overlay.minute_truth.paths(x.root, B, CODE)
    if change == 'augmented_data': augmented[0].write_bytes(augmented[0].read_bytes() + b' ')
    if change == 'missing_meta': augmented[1].unlink()
    if change == 'extra_file': (x.root / 'extra.txt').write_text('not registered')
    if change == 'extra_dir': (x.root / 'extra-directory').mkdir()
    if change == 'hardlink': os.link(augmented[0], x.s.tmp / 'outside-hardlink')
    if change == 'symlink': augmented[0].unlink(); augmented[0].symlink_to(paths[0])
    if change == 'fifo': os.mkfifo(x.root / 'named_pipe')
    if change == 'original_daily':
        p = x.root / f'data/market/raw/2026/{T}/daily.csv'; p.write_bytes(p.read_bytes() + b' ')
    if change == 'prior_bytes': x.priors[0].write_bytes(x.priors[0].read_bytes() + b' ')
    if change == 'plan_bytes': scope.registered_plan_path.write_bytes(scope.registered_plan_path.read_bytes() + b' ')
    scopes = [x.first, x.first] if change == 'same_scope_root' else [x.first, x.second]
    with pytest.raises((ValueError, OSError)):
        x.s.build(x.root, scopes, x.priors)


def test_daily_gap_is_not_swallowed_as_minute(two_rounds):
    x = two_rounds; prior = deepcopy(x.report1)
    prior['rows'][0].update(label_status='PENDING_EXIT_MISSING_DAILY', missing_evidence_kind='daily')
    second, path = x.s.market(prior, B, 12.1)
    target = x.s.stage('daily-not-minute', [x.first, second])
    with pytest.raises(ValueError, match='EXACT_PRIOR_GAPS'):
        x.s.build(target, [x.first, second], [x.priors[0], path])


def test_runtime_does_not_write_or_call_network(two_rounds, monkeypatch):
    x = two_rounds; original = Path.open
    def readonly(self, mode='r', *args, **kwargs):
        assert not any(v in mode for v in 'wax+')
        return original(self, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', readonly)
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network prohibited'))
    monkeypatch.setattr(Path, 'write_bytes', lambda *a, **k: pytest.fail('writes prohibited'))
    monkeypatch.setattr(Path, 'write_text', lambda *a, **k: pytest.fail('writes prohibited'))
    assert x.s.build(x.root, [x.first, x.second], x.priors)['files_written'] == 0


def test_no_economics_function_replacement(two_rounds):
    before = (overlay._resume, overlay.validate_label_contract, overlay.base_labels.build_labels, overlay.base_labels.resolve_exit_1000)
    x = two_rounds; x.s.build(x.root, [x.first, x.second], x.priors)
    assert before == (overlay._resume, overlay.validate_label_contract, overlay.base_labels.build_labels, overlay.base_labels.resolve_exit_1000)


def test_input_manifest_and_authorities_not_mutated(two_rounds):
    x = two_rounds; manifest = deepcopy(x.s.manifest); before = chain.canonical(manifest)
    x.s.build(x.root, [x.first, x.second], x.priors, manifest_override=manifest)
    assert chain.canonical(manifest) == before and x.first.status == x.second.status == 'MINUTE_GAPS_COLLECTED'


def test_original_base_orphan_blocks_even_a_real_new_pair(synthetic, monkeypatch):
    s = synthetic
    orphan = overlay.minute_truth.paths(s.root, A, CODE)[1]
    orphan.parent.mkdir(parents=True, exist_ok=True); orphan.write_bytes(b'{}')
    bases = tuple(s.candidate.base_file_bindings) + (binding(s.root, orphan),)
    # Explicit synthetic private authority with an originally bound orphan.
    values = {name: getattr(s.candidate, name) for name in s.candidate.__dataclass_fields__}
    values['base_file_bindings'] = bases
    authority = cv.VerifiedCandidateCollection(_key=cv._KEY, **values)
    first, prior = s.market(s.initial, A, 11)
    target = s.tmp / 'orphan-augmented'; shutil.copytree(s.root, target)
    for b in first.source_bindings:
        path = target / b['path']; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((first.root / b['path']).read_bytes())
    with pytest.raises(ValueError, match='ORPHAN'):
        chain.build_labels(target, s.manifest, as_of_date=chain.AS_OF_DATE, candidate_source_root=s.candidate_root,
            verified_candidate_scope=authority, verified_minute_scopes=[first], prior_label_reports=[prior])


def test_failed_gap_still_requires_original_exchange_calendar(two_rounds):
    x = two_rounds; saturday = '20260704'; prior = deepcopy(x.report1)
    prior['rows'][0]['missing_evidence_date'] = saturday
    failed, path = x.s.market(prior, saturday, 12.1, fail=True)
    target = x.s.stage('nontrading-calendar-gap', [x.first, failed])
    with pytest.raises(ValueError, match='STRICT_CALENDAR'):
        x.s.build(target, [x.first, failed], [x.priors[0], path])


def test_missing_first_prior_link_fields_rejected(synthetic):
    s = synthetic; prior = deepcopy(s.initial); prior.pop('market_registered_plan_sha256')
    first, path = s.market(prior, A, 11)
    target = s.stage('missing-first-fields', [first])
    with pytest.raises(ValueError, match='LINK_FIELDS'):
        s.build(target, [first], [path])


@pytest.mark.parametrize('field,value', [('EXPECTED_ROWS', 6753), ('EXPECTED_DATES', 910)])
def test_full_default_counts_not_silently_small(synthetic, monkeypatch, field, value):
    monkeypatch.setattr(chain, field, value)
    with pytest.raises(ValueError): synthetic.build(synthetic.root)


def test_code_changed_guard_closes_before_replay(synthetic, monkeypatch):
    monkeypatch.setattr(chain, '_SELF_SHA', '0' * 64)
    with pytest.raises(ValueError, match='IMPLEMENTATION_CHANGED'): synthetic.build(synthetic.root)


@pytest.mark.parametrize('target', ['original_base', 'minute_source', 'prior_report', 'candidate_source'])
def test_mutation_after_economics_still_rejected(two_rounds, monkeypatch, target):
    x = two_rounds; original = chain._replay
    def replay(*args, **kwargs):
        result = original(*args, **kwargs)
        if target == 'original_base': path = x.root / f'data/market/raw/2026/{T}/daily.csv'
        elif target == 'minute_source': path = overlay.minute_truth.paths(x.second.root, B, CODE)[0]
        elif target == 'prior_report': path = x.priors[1]
        else: path = overlay.candidate_source.source_paths(x.s.candidate_root, T, CODE)[0]
        path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(chain, '_replay', replay)
    with pytest.raises(ValueError): x.s.build(x.root, [x.first, x.second], x.priors)


class FutureOutcome(dict):
    """Tripwire: even hashing/deepcopy of out-of-universe values is forbidden."""
    def items(self):
        raise AssertionError('future outcome serialized before identity rejection')

    def __deepcopy__(self, memo):
        raise AssertionError('future outcome copied before identity rejection')


@pytest.mark.parametrize('change', ['future_codes', 'future_row', 'embargo_codes', 'invalid_code',
                                  'duplicate', 'short', 'nonstr_code', 'nonstr_day'])
def test_manifest_identity_gate_precedes_all_hash_or_copy(synthetic, change):
    s = synthetic; manifest = deepcopy(s.manifest)
    manifest['rows'][0]['features'] = FutureOutcome(net_return=999)
    if change in {'future_codes', 'embargo_codes'}:
        day = '20260914' if change == 'future_codes' else '20260817'
        manifest['expected_candidate_codes'] = {day: [CODE, NO_FILL]}
        for row in manifest['rows']: row['signal_date'] = day
    elif change == 'future_row': manifest['rows'][0]['signal_date'] = '20260914'
    elif change == 'invalid_code': manifest['rows'][0]['ts_code'] = '600000'
    elif change == 'duplicate': manifest['rows'][1] = dict(manifest['rows'][0])
    elif change == 'short': manifest['rows'].pop()
    elif change == 'nonstr_code': manifest['expected_candidate_codes'][D][0] = {'value': CODE}
    else: manifest['rows'][0]['signal_date'] = [D]
    with pytest.raises(ValueError): s.build(s.root, manifest_override=manifest)


@pytest.mark.parametrize('target', ['caller', 'snapshot'])
@pytest.mark.parametrize('change', ['feature', 'future'])
def test_after_multiround_replay_manifest_mutation_is_rejected_before_document(two_rounds, monkeypatch,
                                                                             target, change):
    x = two_rounds; original = chain._replay
    def replay(root, candidate_root, candidate, manifest, owners, priors):
        assert manifest is not x.s.manifest
        result = original(root, candidate_root, candidate, manifest, owners, priors)
        mutate = x.s.manifest if target == 'caller' else manifest
        if change == 'feature': mutate['rows'][0]['features']['promotion_probability'] = .99
        else:
            mutate['rows'][0]['signal_date'] = '20260914'
            mutate['rows'][0]['features'] = FutureOutcome(net_return=999)
        return result
    monkeypatch.setattr(chain, '_replay', replay)
    monkeypatch.setattr(chain, '_document', lambda *a: pytest.fail('changed manifest reached chain document'))
    with pytest.raises(ValueError): x.s.build(x.root, [x.first, x.second], x.priors)


@pytest.mark.parametrize('rounds', [0, 1, 2])
@pytest.mark.parametrize('change', ['feature', 'future'])
def test_manifest_mutated_by_final_code_guard_never_returns(two_rounds, monkeypatch, rounds, change):
    x = two_rounds; original = chain._guard; calls = 0
    def guard():
        nonlocal calls
        result = original(); calls += 1
        if calls == 2:
            if change == 'feature': x.s.manifest['rows'][0]['features']['promotion_probability'] = .99
            else:
                x.s.manifest['rows'][0]['signal_date'] = '20260914'
                x.s.manifest['rows'][0]['features'] = FutureOutcome(net_return=999)
        return result
    monkeypatch.setattr(chain, '_guard', guard)
    target = [x.s.root, x.s.tmp / 'stage1', x.root][rounds]
    with pytest.raises(ValueError): x.s.build(target, [x.first, x.second][:rounds], x.priors[:rounds])
    assert calls == 2


@pytest.mark.parametrize('rounds', [0, 1])
def test_compatible_single_round_path_uses_private_snapshot_and_rechecks_caller(two_rounds, monkeypatch, rounds):
    x = two_rounds; original = overlay.build_labels
    def replay(root, manifest, **kwargs):
        assert manifest is not x.s.manifest
        result = original(root, manifest, **kwargs)
        x.s.manifest['rows'][0]['features']['promotion_probability'] = .99
        return result
    monkeypatch.setattr(overlay, 'build_labels', replay)
    target = x.s.root if rounds == 0 else x.s.tmp / 'stage1'
    with pytest.raises(ValueError, match='FROZEN_MANIFEST_CHANGED'):
        x.s.build(target, [x.first][:rounds], x.priors[:rounds])


@pytest.mark.parametrize('rounds', [1, 2])
@pytest.mark.parametrize('marker', ['top', 'row', 'exit_evidence'])
@pytest.mark.parametrize('value', [None, {'source_admission': 'nontrading'}])
def test_nontrading_reports_cannot_enter_raw_minute_chain_even_rehashed(two_rounds, rounds, marker, value):
    x = two_rounds
    prior = deepcopy(x.s.initial if rounds == 1 else x.report1)
    if marker == 'top': prior['nontrading_resume'] = value
    elif marker == 'row': prior['rows'][0]['nontrading_exit_supplement'] = value
    else: prior['rows'][0]['exit_evidence'] = {'nontrading_adapter_id': value}
    day, price = (A, 11) if rounds == 1 else (B, 12.1)
    current, path = x.s.market(prior, day, price)
    scopes = [current] if rounds == 1 else [x.first, current]
    priors = [path] if rounds == 1 else [x.priors[0], path]
    target = x.s.stage(f'raw-only-{rounds}-{marker}', scopes)
    with pytest.raises(ValueError, match='NONTRADING_REPORT_FORBIDDEN_IN_RAW_MINUTE_CHAIN'):
        x.s.build(target, scopes, priors)
