"""Safety boundaries of the read-only historical rehearsal report."""
from pathlib import Path
import socket

import pytest

from scripts import replay_daily_cleanup as replay


def test_binary64_drift_is_reported_not_called_exact():
    original = {'rows':[{'ts_code':'000001.SZ','rank':1,'value':0.05610682230565774}]}
    actual = {'rows':[{'ts_code':'000001.SZ','rank':1,'value':0.05610682230565775}]}
    result = replay.compare_frozen(actual, original)
    assert result['exact_match'] is False and result['float_differences'] == 1
    assert 0 < result['max_float_absolute_delta'] < 1e-12
    assert original['rows'][0]['value'] == 0.05610682230565774


@pytest.mark.parametrize('actual,expected', [
    ({'rank':2},{'rank':1}), ({'sha':'a'*64},{'sha':'b'*64}),
    ({'value':0.06},{'value':0.05}), ({'rank':True},{'rank':1}),
    ({'rows':[]},{'rows':[{}]}), ({'value':float('nan')},{'value':float('nan')})])
def test_tolerance_never_hides_rank_hash_shape_type_or_material_change(actual,expected):
    with pytest.raises(ValueError):
        replay.compare_frozen(actual, expected)


def test_offline_guard_blocks_connections_and_restores_normal_socket():
    original = socket.socket.connect
    with replay.offline():
        with pytest.raises(ValueError, match='NETWORK_FORBIDDEN'):
            socket.create_connection(('example.invalid',443))
        with socket.socket() as client:
            with pytest.raises(ValueError, match='NETWORK_FORBIDDEN'):
                client.connect(('127.0.0.1',1))
    assert socket.socket.connect is original


def test_report_cannot_overwrite_either_checkout(tmp_path):
    code, source = tmp_path/'code', tmp_path/'source'
    code.mkdir(); source.mkdir()
    for checkout in (code,source):
        report = checkout/'outputs'/'decision'/'candidate_profit_v1'/'day_20261106.json'
        with pytest.raises(ValueError, match='OUTSIDE_BOTH_CHECKOUTS'):
            replay.main(['--code-root',str(code),'--source-root',str(source),'--output',str(report)])
        assert not report.exists()


def test_registered_input_hash_mismatch_stops_before_any_scoring(tmp_path):
    root = tmp_path/'source'; root.mkdir()
    (root/'original.json').write_bytes(b'changed')
    runner = replay.Replay(root,root,'20261106','fault-injection')
    with pytest.raises(ValueError,match='REPLAY_INPUT_SHA_MISMATCH'):
        runner.read('original.json',expected=replay.sha(b'original'))
    assert runner.report['formal_files_written'] == 0
    assert (root/'original.json').read_bytes() == b'changed'


def test_root_normalizes_parent_components_but_rejects_symlinks(tmp_path):
    code, source = tmp_path/'code', tmp_path/'source'
    code.mkdir(); source.mkdir()
    assert replay.root_path(code/'..'/'source') == source.resolve()
    link=tmp_path/'alias'; link.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError,match='ALIASED_REPLAY_ROOT'):
        replay.root_path(link)
