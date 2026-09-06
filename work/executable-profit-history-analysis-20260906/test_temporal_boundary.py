"""Repeatable synthetic temporal audit of the byte-pinned execution-v2 code.

450 synthetic weekdays, five candidates per D and five evaluation folds. These
are test dates, not evidence about SSE sessions or actual investment returns.
Only merge/readiness/training-input selection runs: no estimator is fitted, no
market endpoint is used, and no source, production artifact or result is written.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True

import numpy as np
import pandas as pd


SOURCE = (
    Path(__file__).resolve().parent.parent
    / "executable-profit-execution-v2-20260906/train_candidate.py"
)
SOURCE_SHA256 = "50e01cbcd7da0aea7ec3329b0f980e58fd17d5a350e251452ba2dc26e6eaaaa8"


def load_fixed_training_module():
    """Check the reviewed bytes before importing the actual implementation."""
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise AssertionError("FROZEN_TRAINING_SCRIPT_SHA_CHANGED")
    spec = importlib.util.spec_from_file_location(
        "dc20_readonly_temporal_boundary_execution_v2_50e01cbc", SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_frame(training):
    """Every label matures two test sessions after D; never a real training set."""
    dates = pd.bdate_range("2022-01-03", periods=460).strftime("%Y%m%d").tolist()
    rng = np.random.default_rng(20260906)
    frozen, labels = [], []
    for index, date in enumerate(dates[:450]):
        for rank in range(1, 6):
            row = {
                "signal_date": date,
                "ts_code": f"{rank:06d}.SZ",
                "exec_date": dates[index + 1],
                "scheduled_exit_date": dates[index + 2],
                "promotion_rank": rank,
            }
            row.update(zip(training.FEATURES, rng.normal(size=48).tolist()))
            frozen.append(row)
            net = float(.01 * np.tanh(row[training.FEATURES[0]]) - .003)
            labels.append({
                **{key: row[key] for key in training.KEY + ["exec_date", "scheduled_exit_date"]},
                "label_status": "SETTLED_OPEN_PROXY",
                "proxy_fill": 1.,
                "slot_net_return": net,
                "slot_net_return_stress": net - .0045,
                "conditional_net_return": net,
                "label_available_date": dates[index + 2],
                "actual_exit_date": dates[index + 2],
                "blocked_exit_sessions": 0,
            })
    return training.merge_labels(pd.DataFrame(frozen), pd.DataFrame(labels)), dates


class TemporalBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.training = load_fixed_training_module()
        cls.frame, cls.dates = synthetic_frame(cls.training)
        cls.plan = {
            "hgb_parameters": dict(cls.training.PARAMETERS),
            "training": dict(cls.training.TRAINING_CONTRACT),
        }

    def setUp(self):
        # A future refactor must not make readiness or selection silently fit.
        self.fit_guard = mock.patch.object(
            self.training, "fit_heads", side_effect=AssertionError("MODEL_FIT_FORBIDDEN")
        )
        self.estimator_guard = mock.patch.object(
            self.training, "HistGradientBoostingRegressor",
            side_effect=AssertionError("ESTIMATOR_CONSTRUCTION_FORBIDDEN"),
        )
        self.fit = self.fit_guard.start()
        self.estimator = self.estimator_guard.start()
        self.addCleanup(self.fit_guard.stop)
        self.addCleanup(self.estimator_guard.stop)
        self.readiness = self.training.assess_readiness(self.frame, self.plan)
        self.assertTrue(self.readiness["ready"])
        self.assertEqual(len(self.readiness["folds"]), 5)
        self.assertEqual(self.readiness["models_fit"], 0)

    def tearDown(self):
        self.fit.assert_not_called()
        self.estimator.assert_not_called()

    def assert_training_inputs_unchanged(self, *, poison_labels, poison_features):
        for fold in self.readiness["folds"]:
            cutoff = fold["first_evaluation_D"]
            with self.subTest(fold=fold["fold"], cutoff=cutoff):
                before = self.training.training_at(self.frame, cutoff)
                poisoned = self.frame.copy(deep=True)
                # Include labels maturing exactly at cutoff, not only later D.
                future = poisoned.label_available_date.ge(cutoff)
                self.assertTrue(future.any())
                self.assertTrue(poisoned.label_available_date.eq(cutoff).any())
                if poison_labels:
                    poisoned.loc[future, "slot_net_return"] = 8.
                    poisoned.loc[future, "slot_net_return_stress"] = 8. - .0045
                    poisoned.loc[future, "conditional_net_return"] = 8.
                if poison_features:
                    poisoned.loc[future, self.training.FEATURES] = 1000.
                after = self.training.training_at(poisoned, cutoff)
                pd.testing.assert_frame_equal(before, after, check_exact=True)
                self.assertLess(before.label_available_date.max(), cutoff)

    def test_original_450D_readiness_and_each_fold_strict_maturity(self):
        self.assertEqual(len(self.frame), 2250)
        self.assertEqual(self.readiness["complete_signal_dates"], 450)
        self.assertEqual(len(self.readiness["evaluation_D_dates"]), 180)
        self.assertEqual(len(self.training.FEATURES), 48)
        self.assertEqual(self.readiness["fixed_training_contract"]["min_train_complete_dates"], 252)
        self.assertEqual(self.readiness["fixed_training_contract"]["min_train_rows"], 1000)
        for fold in self.readiness["folds"]:
            with self.subTest(fold=fold["fold"]):
                first, last = fold["first_evaluation_D"], fold["last_evaluation_D"]
                train = self.training.training_at(self.frame, first)
                target_dates = {
                    date for date in self.readiness["evaluation_D_dates"]
                    if first <= date <= last
                }
                self.assertGreaterEqual(train.signal_date.nunique(), 252)
                self.assertGreaterEqual(len(train), 1000)
                self.assertLess(train.label_available_date.max(), first)
                self.assertTrue(set(train.signal_date).isdisjoint(target_dates))

    def test_future_label_poison_cannot_change_any_fold_training_input(self):
        self.assert_training_inputs_unchanged(poison_labels=True, poison_features=False)

    def test_future_48_feature_poison_cannot_change_any_fold_training_input(self):
        self.assert_training_inputs_unchanged(poison_labels=False, poison_features=True)

    def test_combined_future_poison_cannot_change_any_fold_training_input(self):
        self.assert_training_inputs_unchanged(poison_labels=True, poison_features=True)

    def test_one_candidate_maturing_at_cutoff_excludes_entire_D_until_next_day(self):
        for fold in self.readiness["folds"]:
            cutoff = fold["first_evaluation_D"]
            with self.subTest(fold=fold["fold"], cutoff=cutoff):
                frame = self.frame.copy(deep=True)
                earliest_D = frame.signal_date.min()
                self.assertIn(earliest_D, self.training.complete_dates(frame, cutoff))
                index = frame.index[frame.signal_date.eq(earliest_D)][0]
                frame.loc[index, "actual_exit_date"] = cutoff
                frame.loc[index, "label_available_date"] = cutoff
                frame.loc[index, "blocked_exit_sessions"] = self.dates.index(cutoff) - 2
                self.assertNotIn(earliest_D, self.training.complete_dates(frame, cutoff))
                self.assertNotIn(earliest_D, set(self.training.training_at(frame, cutoff).signal_date))
                next_date = self.dates[self.dates.index(cutoff) + 1]
                admitted = self.training.training_at(frame, next_date)
                self.assertEqual(int(admitted.signal_date.eq(earliest_D).sum()), 5)
                self.assertLess(admitted.label_available_date.max(), next_date)


if __name__ == "__main__":
    unittest.main()
