"""Exact earlier code tuples remain readable; mixed/future tuples fail closed."""
from copy import deepcopy
import pytest
from work.profit_1000_upgrade import candidate_natural_forward as natural
from work.profit_1000_upgrade import candidate_eligible_forward as eligible
from work.profit_1000_upgrade import candidate_natural_outcomes as outcomes
from work.profit_1000_upgrade import candidate_natural_outcome_collect as collector
from work.profit_1000_upgrade import candidate_natural_publication as publication
from work.profit_1000_upgrade import candidate_natural_evidence as capture
from work.profit_1000_upgrade import candidate_natural_evidence_publication as observer


@pytest.mark.parametrize('runner', [natural, eligible])
def test_prior_snapshot_and_outcome_exact_tuples_remain_readable(runner):
    frozen = {**deepcopy(runner.PRE_BOUNDARY_SNAPSHOT_PROFILE), 'schema_version':runner.SCHEMA, 'signal_date':'20261027'}
    assert runner.snapshot_profile(frozen) == runner.PRE_BOUNDARY_SNAPSHOT_PROFILE
    writer, dependencies = outcomes.PRE_BOUNDARY_REPORT_PROFILE
    report = {'writer_sha256':writer, 'dependencies':dependencies}
    assert outcomes.validate_report_profile(report, frozen) == (writer, dependencies)
    assert writer in collector.accepted_outcome_writers(frozen)
    for mutation in ['date', 'runner', 'dependency']:
        bad = deepcopy(frozen)
        if mutation == 'date': bad['signal_date'] = '20261028'
        elif mutation == 'runner': bad['runner_sha256'] = runner.SELF_SHA
        else: bad['dependency_sha256']['work/profit_1000_upgrade/candidate_d_source_adapter.py'] = '0'*64
        with pytest.raises(ValueError): runner.snapshot_profile(bad)
        with pytest.raises(ValueError): outcomes.validate_report_profile(report,bad)


def test_prior_publication_requires_complete_old_tree_and_date():
    local,_ = publication.code_guard()
    entries = {b['path']:{'type':'blob','mode':'100644','sha':b['git_blob_sha1']} for b in publication.PRE_BOUNDARY_CODE_BINDINGS}
    result = publication.match_publication_code(entries, entries, local, day='20261027')
    assert result == sorted(publication.PRE_BOUNDARY_CODE_BINDINGS,key=lambda x:x['path'])
    for day in ['20261028', None]:
        with pytest.raises(ValueError):publication.match_publication_code(entries,entries,local,day=day)
    changed = deepcopy(entries)
    changed['scripts/publish_primary_three_rank.py']['sha'] = publication.gh.git_blob(local['scripts/publish_primary_three_rank.py'])
    with pytest.raises(ValueError):publication.match_publication_code(changed,changed,local,day='20261027')


def test_prior_capture_and_observer_profile_is_date_bounded():
    contract = capture.PRE_BOUNDARY_CAPTURE_CONTRACT
    assert contract in capture.capture_contracts()
    manifest = {'capture_code_sha256':contract[0], 'publication_verifier_sha256':contract[1], 'signal_date':'20261027'}
    assert observer._observer_profile(manifest) == observer.PRE_BOUNDARY_OBSERVER_PROFILE
    manifest['signal_date'] = '20261028'
    with pytest.raises(ValueError):observer._observer_profile(manifest)
    manifest.update(signal_date='20261027',publication_verifier_sha256=capture.PUBLICATION_SHA)
    with pytest.raises(ValueError):observer._observer_profile(manifest)


@pytest.mark.parametrize('runner', [natural, eligible])
def test_prior_daily_manifest_requires_exact_writer_dependencies_and_old_snapshot(runner):
    from work.profit_1000_upgrade import candidate_natural_daily as daily
    from work.profit_1000_upgrade import candidate_natural_settlement_workflow as settlement
    frozen = {**deepcopy(runner.PRE_BOUNDARY_SNAPSHOT_PROFILE),
              'schema_version': runner.SCHEMA, 'signal_date': '20261027'}
    modules = {'daily': daily, 'outcome_collect': collector}
    writer, dependencies = settlement.PRE_BOUNDARY_DAILY_PROFILE
    manifest = {'daily_module_sha256': writer, 'dependencies': deepcopy(dependencies)}
    assert settlement.daily_metadata_profile(manifest, frozen, modules)
    for mutation in ['future_date', 'unknown_snapshot_writer', 'changed_snapshot_dependency']:
        bad = deepcopy(frozen)
        if mutation == 'future_date': bad['signal_date'] = '20261028'
        elif mutation == 'unknown_snapshot_writer': bad['runner_sha256'] = '0' * 64
        else: bad['dependency_sha256']['work/profit_1000_upgrade/candidate_d_source_adapter.py'] = '0' * 64
        assert not settlement.daily_metadata_profile(manifest, bad, modules)
    for mutation in ['unknown_writer', 'old_writer_current_dependencies',
                     'current_writer_old_dependencies', 'changed_dependency', 'extra_dependency']:
        bad = deepcopy(manifest)
        if mutation == 'unknown_writer': bad['daily_module_sha256'] = '0' * 64
        elif mutation == 'old_writer_current_dependencies': bad['dependencies'] = deepcopy(daily.PINS)
        elif mutation == 'current_writer_old_dependencies':
            bad['daily_module_sha256'] = settlement.PINS['candidate_natural_daily']
        elif mutation == 'changed_dependency':
            bad['dependencies']['scripts/diagnose_core_supervisor.py'] = '0' * 64
        else: bad['dependencies']['unexpected.py'] = '0' * 64
        assert not settlement.daily_metadata_profile(bad, frozen, modules)
    current = {'daily_module_sha256': settlement.PINS['candidate_natural_daily'],
               'dependencies': deepcopy(daily.PINS)}
    assert settlement.daily_metadata_profile(current, frozen, modules)
