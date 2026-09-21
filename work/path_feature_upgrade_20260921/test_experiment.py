import hashlib
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("path_experiment", Path(__file__).with_name("experiment.py"))
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def test_blob_binding_rejects_changed_file(tmp_path):
    p = tmp_path / "source"
    p.write_bytes(b"abc")
    sha = hashlib.sha1(b"blob 3\0abc").hexdigest()
    e.verify_blob(p, sha)
    p.write_bytes(b"abd")
    with pytest.raises(ValueError, match="MISMATCH"):
        e.verify_blob(p, sha)


def test_join_is_d_and_code_not_code_only():
    frame = pd.DataFrame({"signal_date": ["20260101", "20260102"], "ts_code": ["000001.SZ"]*2})
    path = frame.iloc[:1].copy()
    for key in e.PATH + ["label_" + x for x in e.LABELS]:
        path[key] = .25
    result = e.attach(frame, path)
    assert len(result) == 2
    assert result.path_strength_delta.iloc[0] == .25
    assert np.isnan(result.path_strength_delta.iloc[1])


def test_duplicate_path_rejected():
    frame = pd.DataFrame({"signal_date": ["20260101"], "ts_code": ["000001.SZ"]})
    with pytest.raises(ValueError, match="DUPLICATE"):
        e.attach(frame, pd.concat([frame, frame]))


def test_rank_ties_deterministic_and_negative_scores_not_skipped():
    frame = pd.DataFrame({"signal_date": ["20260101"]*2, "ts_code": ["000002.SZ", "000001.SZ"]})
    result = e.ranked(frame, [-.1, -.1])
    assert result.ts_code.tolist() == ["000001.SZ", "000002.SZ"]
    assert result["rank"].tolist() == [1, 2]


def test_no_fill_does_not_inflate_filled_win_rate():
    frame = pd.DataFrame({"signal_date": ["20260101", "20260102", "20260101", "20260102"],
                          "rank": [1, 1, 2, 2], "proxy_fill": [0, 1, 1, 1],
                          "slot_net_return": [0, -.1, .1, .2]})
    result = e.metrics(frame, "profit")
    assert result["top1"]["filled"] == 1
    assert result["top1"]["win_rate"] == 0
    assert result["top1"]["mean_slot_net"] == -.05
    assert result["top1"]["mean_filled_net"] == -.1


def test_paired_interval_identical_models_is_zero():
    frame = pd.DataFrame({"signal_date": ["20260101", "20260102"]*2,
                          "rank": [1, 1, 2, 2], "slot_net_return": [.1, -.1, .2, -.2]})
    result = e.paired_difference(frame, frame, "profit")
    assert result["top1"]["block_bootstrap_95_interval"] == [0., 0.]


def test_path_loader_ignores_old_targets_and_rejects_low_coverage(tmp_path):
    frame = pd.DataFrame({"signal_date": ["20260101", "20260102"], "ts_code": ["000001.SZ"]*2,
                          "path_label_code": ["WEAK_TO_STRONG"]*2,
                          "path_data_coverage": [1., .35], "path_days_observed": [2, 2],
                          "net_return": [1000., -1000.], "t_close": [9999., 9999.]})
    for key in e.PATH:
        frame[key] = .1
    path = tmp_path / "history.csv"
    frame.to_csv(path, index=False)
    raw = path.read_bytes()
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    result, audit = e.path_panel(tmp_path, {"history_blobs": {"history.csv": sha}})
    assert "net_return" not in result and "t_close" not in result
    assert audit["usable_keys"] == 1
    assert result[e.PATH].iloc[1].isna().all()


def test_conflicting_path_observations_excluded(tmp_path):
    frame = pd.DataFrame({"signal_date": ["20260101"]*2, "ts_code": ["000001.SZ"]*2,
                          "path_label_code": ["WEAK_TO_STRONG"]*2,
                          "path_data_coverage": [1., 1.], "path_days_observed": [2, 2]})
    for key in e.PATH:
        frame[key] = [.1, .2]
    p = tmp_path / "history.csv"
    frame.to_csv(p, index=False)
    raw = p.read_bytes()
    sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    result, audit = e.path_panel(tmp_path, {"history_blobs": {"history.csv": sha}})
    assert result.empty
    assert audit["conflicting_keys_excluded"] == 1
