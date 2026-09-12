"""D-only fixture tests, including the actual frozen 6753-row feature files.

No HTTP, fitting, artifact/source authority assertion, or old return label is
used. Tiny mutated cases test only the private whitelist reader and cannot
become a public registered panel because its file SHAs remain fixed.
"""
from copy import deepcopy
import csv
import gzip
import io
import json
import os
from pathlib import Path
import shutil

import pytest

from work.profit_1000_upgrade import candidate_feature_panel as p
from work.profit_1000_upgrade import candidate_v3, research_v3 as research

REPORT_SHA, RECEIPT_SHA, MARKET_SHA = "a" * 64, "b" * 64, "c" * 64


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    root = tmp_path_factory.mktemp("frozen-d-feature-inputs").resolve()
    for binding in p.SOURCE_INPUTS.values():
        target = root / binding["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p.ROOT / binding["path"], target)
        assert p.file_sha(target) == binding["sha256"]
    marker = {"schema_version": "dc20_profit_1000_research_mirror_v3", "plan_version": "v3",
        "plan_sha256": p.PINNED["PLAN_V3.json"], "production_writes": False, **dict(p.policy_v3.CONTRACT)}
    (root / research.MARKER).write_text(json.dumps(marker))
    manifest = research.prepare_history(root)
    # These are identity-only synthetic pending labels. The panel must not
    # inspect their return/status and is NOT the final candidate contract gate.
    labels = [{"signal_date": row["signal_date"], "ts_code": row["ts_code"],
               "label_status": "PENDING_EXIT_MISSING_MINUTES", "slot_net_return": None}
              for row in manifest["rows"]]
    return root, manifest, labels


@pytest.fixture
def case(base):
    root, manifest, labels = base
    return root, deepcopy(manifest), deepcopy(labels)


def prepare(case, **updates):
    root, manifest, labels = case
    args = dict(label_report_sha256=REPORT_SHA, candidate_receipt_sha256=RECEIPT_SHA,
                market_receipt_sha256=None, label_rows=labels)
    args.update(updates)
    return p.prepare_model_panel(root, manifest, **args)


def test_actual_frozen_panel_6753_910_and_candidate_manifest_contract(case):
    original = deepcopy(case[1:])
    rows, manifest = prepare(case)
    assert len(rows) == 6753 and len(manifest["day_candidate_counts"]) == 910
    assert min(manifest["day_candidate_counts"]) == "20221111"
    assert max(manifest["day_candidate_counts"]) == "20260814"
    assert sum(manifest["day_candidate_counts"].values()) == 6753
    assert candidate_v3._manifest(manifest) == manifest["day_candidate_counts"]
    assert manifest["development_frozen_rows_sha256"] == candidate_v3.canonical_sha(rows)
    assert manifest["development_label_rows_sha256"] == candidate_v3.canonical_sha(case[2])
    assert manifest["frozen_manifest_sha256"] == candidate_v3.canonical_sha(case[1])
    assert manifest["source_sha256"] == p.SOURCE_SHA and manifest["base_archive_sha256"] == p.BASE_ARCHIVE_SHA
    assert manifest["source_overlay_contract"] == p.overlay.CONTRACT
    assert "source_policy_contract" not in manifest and "auction_source_policy_id" not in manifest
    assert manifest["market_collection_receipt_sha256"] is None
    assert manifest["training_cutoff_date"] == "20251111" and manifest["as_of_date"] == "20260911"
    assert manifest["future_holdout_start_date"] == "20260914" and manifest["cost_rate"] == .0045
    assert manifest["source_provenance_verified"] is True
    for key in ("whole_source_artifact_independently_verified_by_panel", "label_report_independently_verified_by_panel",
                "receipt_identity_independently_verified_by_panel", "natural_freeze_verified", "training_performed",
                "label_contract_verified_by_panel",
                "label_rebuild_performed", "legacy_outcome_columns_used_as_features", "feature_values_imputed",
                "forward_holdout_touched", "production_activation_allowed", "actual_execution_claimed"):
        assert manifest[key] is False
    assert case[1:] == original
    columns = set(case[1]["feature_columns"])
    expected_keys = columns | {"signal_date", "ts_code", "promotion_rank", "board_stage", "feature_as_of_date", "promotion_oof_train_end"}
    assert all(set(row) == expected_keys for row in rows)
    assert all(not set(p.MISSING_SIGNALS) & set(row) for row in rows)
    assert any(row["ret_2d"] is None for row in rows)  # Preserve missing features.
    assert all(row["promotion_oof_train_end"] < row["signal_date"] == row["feature_as_of_date"] for row in rows)
    assert {row["board_stage"] for row in rows} == {2, 3}


def test_market_receipt_explicit_binding_is_not_claimed_authority(case):
    _, manifest = prepare(case, market_receipt_sha256=MARKET_SHA)
    assert manifest["market_collection_receipt_sha256"] == MARKET_SHA
    assert manifest["receipt_identity_independently_verified_by_panel"] is False


@pytest.mark.parametrize("field", ["label_report_sha256", "candidate_receipt_sha256", "market_receipt_sha256"])
@pytest.mark.parametrize("bad", ["", "a" * 63, "A" * 64, True, 123])
def test_external_bindings_require_exact_sha(case, field, bad):
    with pytest.raises(ValueError, match="SHA_REQUIRED"): prepare(case, **{field: bad})


@pytest.mark.parametrize("field", ["label_report_sha256", "candidate_receipt_sha256"])
def test_required_external_bindings_cannot_be_none(case, field):
    with pytest.raises(ValueError, match="SHA_REQUIRED"): prepare(case, **{field: None})


def test_original_manifest_requires_exact_reconstruction(case):
    case[1]["rows"][0]["promotion_rank"] = 999
    with pytest.raises(ValueError, match="ORIGINAL_MANIFEST_DIFFERS"): prepare(case)


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate", "foreign-code", "future-date"])
def test_no_pending_candidate_can_be_deleted_or_replaced(case, kind):
    labels = case[2]
    if kind == "missing": labels.pop()
    elif kind == "extra": labels.append(dict(labels[-1], ts_code="000001.SZ"))
    elif kind == "duplicate": labels[1] = dict(labels[0])
    elif kind == "foreign-code": labels[0]["ts_code"] = "000001.SZ"
    elif kind == "future-date": labels[0]["signal_date"] = "20260914"
    with pytest.raises(ValueError, match="ALL_6753"): prepare(case)


def test_label_status_and_returns_not_used_to_select_features(case):
    rows, manifest = prepare(case)
    for i, label in enumerate(case[2]):
        label.update(label_status="UNVALIDATED_SYNTHETIC", slot_net_return=-(i + 1))
    changed_rows, changed = prepare(case)
    assert changed_rows == rows and changed["day_candidate_counts"] == manifest["day_candidate_counts"]
    assert changed["development_label_rows_sha256"] != manifest["development_label_rows_sha256"]
    assert changed["development_frozen_rows_sha256"] == manifest["development_frozen_rows_sha256"]
    assert changed["label_report_independently_verified_by_panel"] is False


@pytest.mark.parametrize("population", ["labels", "manifest"])
@pytest.mark.parametrize("future_day", ["20260914", "20261001"])
def test_future_identity_is_rejected_before_any_outcome_or_feature_hash(case, monkeypatch, population, future_day):
    class FutureValue(dict):
        def items(self):
            raise AssertionError("FUTURE_CONTENT_SERIALIZED")
    target = case[2] if population == "labels" else case[1]["rows"]
    target[0] = dict(target[0], signal_date=future_day,
                     slot_net_return=FutureValue(secret_outcome=999), features=FutureValue(secret_feature=999))
    monkeypatch.setattr(p, "canonical_sha", lambda value: pytest.fail("hash before identity gate"))
    with pytest.raises(ValueError, match="NONDEVELOPMENT_IDENTITY"):
        prepare(case)


def test_late_future_mutation_is_rejected_before_final_content_hash(case, monkeypatch):
    original_guard = p.guard
    calls = 0
    class FutureValue(dict):
        def items(self):
            raise AssertionError("FUTURE_CONTENT_SERIALIZED")
    def guard_and_mutate():
        nonlocal calls
        result = original_guard()
        calls += 1
        if calls == 2:
            case[2][0].update(signal_date="20260914", slot_net_return=FutureValue(secret=999))
        return result
    monkeypatch.setattr(p, "guard", guard_and_mutate)
    with pytest.raises(ValueError, match="NONDEVELOPMENT_IDENTITY"):
        prepare(case)


def test_label_order_preserved_in_content_digest_not_reordered_for_outcome(case):
    case[2].reverse()
    _, manifest = prepare(case)
    assert manifest["development_label_rows_sha256"] == candidate_v3.canonical_sha(case[2])


def test_old_outcome_cells_are_never_selected(base, monkeypatch):
    path = base[0] / p.SOURCE_INPUTS["ledger"]["path"]
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        actual_reader = csv.reader(stream)
        header = next(actual_reader)
        # Convert only registered selected columns into a guarded sequence;
        # indexing any legacy outcome cell will fail this test immediately.
        allowed = set(base[1]["feature_columns"]) | {"signal_date", "ts_code", "promotion_rank", "stage", "promotion_oof_train_end"}
        indices = {i for i, name in enumerate(header) if name in allowed}
        class Guarded:
            def __init__(self, row): self.values = {i: row[i] for i in indices}
            def __len__(self): return len(header)
            def __getitem__(self, index):
                assert index in indices, "LEGACY_OUTCOME_CELL_ACCESSED"
                return self.values[index]
        guarded = [Guarded(row) for row in actual_reader]
    monkeypatch.setattr(p.csv, "reader", lambda handle: iter([header, *guarded]))
    rows, counts = p._read_model_rows(path, base[1]["feature_columns"])
    assert len(rows) == 6753 and len(counts) == 910


def tiny_input(tmp_path, base, changes=None, **kw):
    columns = base[1]["feature_columns"]
    extras = ["signal_date", "ts_code", "promotion_rank", "stage", "promotion_oof_train_end"]
    row = {key: "1.0" for key in columns}
    row.update(signal_date="20221111", ts_code="600001.SH", promotion_rank="1", stage="2", promotion_oof_train_end="20221110")
    row.update(changes or {})
    buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=columns + extras)
    writer.writeheader(); writer.writerow(row)
    path = tmp_path / "tiny.gz"; path.write_bytes(gzip.compress(buffer.getvalue().encode()))
    return path, columns


@pytest.mark.parametrize("change,reason", [({"promotion_oof_train_end": "20221111"}, "STRICTLY_BEFORE"),
    ({"promotion_oof_train_end": "20221112"}, "STRICTLY_BEFORE"), ({"promotion_oof_train_end": "20220230"}, None),
    ({"stage": "4"}, "ONLY_2"), ({"stage": "2.0"}, "ONLY_2"), ({"promotion_rank": "0"}, "INVALID_PROMOTION"),
    ({"promotion_rank": "1.0"}, "INVALID_PROMOTION"), ({"promotion_rank": "11"}, "INVALID_PROMOTION"),
    ({"d_open": "NaN"}, "NONFINITE"), ({"d_close": "Infinity"}, "NONFINITE"),
    ({"d_high": "1e400"}, "NONFINITE"), ({"ts_code": "600001.BJ"}, "INVALID_FEATURE_STOCK"),
    ({"signal_date": "20260914"}, "SCOPE"), ({"signal_date": "20221110"}, "SCOPE")])
def test_private_reader_rejects_invalid_d_feature_inputs(tmp_path, base, change, reason):
    path, columns = tiny_input(tmp_path, base, change)
    with pytest.raises(ValueError, match=reason): p._read_model_rows(path, columns)


def test_tiny_synthetic_population_cannot_be_registered_panel(tmp_path, base):
    path, columns = tiny_input(tmp_path, base)
    with pytest.raises(ValueError, match="6753_ROWS_910"): p._read_model_rows(path, columns)


def test_unbound_modified_source_fails_before_model_rows(case, tmp_path):
    root = tmp_path / "modified"; shutil.copytree(case[0], root)
    path = root / p.SOURCE_INPUTS["ledger"]["path"]
    path.write_bytes(b"not-original")
    with pytest.raises(ValueError, match="SHA mismatch"): prepare((root, case[1], case[2]))


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_alias_feature_files_rejected(case, tmp_path, kind):
    root = tmp_path / "modified"; shutil.copytree(case[0], root)
    target = root / p.SOURCE_INPUTS["ledger"]["path"]
    target.unlink()
    original = case[0] / p.SOURCE_INPUTS["ledger"]["path"]
    if kind == "symlink": target.symlink_to(original)
    else: os.link(original, target)
    try:
        with pytest.raises(ValueError): prepare((root, case[1], case[2]))
    finally:
        target.unlink()  # Restore original fixture's link count.


def test_modified_model_row_cannot_pass_original_manifest(case, monkeypatch):
    original = p._read_model_rows
    def altered(*args):
        rows, counts = original(*args); rows[0]["d_open"] += 1
        return rows, counts
    monkeypatch.setattr(p, "_read_model_rows", altered)
    with pytest.raises(ValueError, match="MODEL_ROW_DIFFERS"): prepare(case)


def test_source_mutation_during_read_fails_closed(case, tmp_path, monkeypatch):
    root = tmp_path / "mutable"; shutil.copytree(case[0], root)
    actual = p._read_model_rows
    def change(*args):
        result = actual(*args)
        (root / p.SOURCE_INPUTS["calendar"]["path"]).write_bytes(b"changed")
        return result
    monkeypatch.setattr(p, "_read_model_rows", change)
    with pytest.raises(ValueError, match="SHA mismatch"): prepare((root, case[1], case[2]))


def test_labels_mutation_during_read_fails_closed(case, monkeypatch):
    actual = p._read_model_rows
    def change(*args):
        result = actual(*args); case[2][0]["slot_net_return"] = -1
        return result
    monkeypatch.setattr(p, "_read_model_rows", change)
    with pytest.raises(ValueError, match="CALLER_MANIFEST_OR_LABEL"): prepare(case)


def test_code_sha_change_is_blocker(case, monkeypatch):
    monkeypatch.setattr(p, "PINNED", {**p.PINNED, "research_v3.py": "0" * 64})
    with pytest.raises(ValueError, match="CODE_CHANGED"): prepare(case)


def test_no_fit_label_rebuild_output_or_network_calls(case, monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("PANEL_MUST_BE_READ_ONLY_FEATURE_EXTRACTION")
    monkeypatch.setattr(research, "write_json", forbidden)
    monkeypatch.setattr(candidate_v3, "run_candidate_v3", forbidden)
    monkeypatch.setattr(p.overlay, "build_labels", forbidden)
    before = {path.relative_to(case[0]).as_posix(): p.file_sha(path) for path in case[0].rglob("*") if path.is_file()}
    prepare(case)
    after = {path.relative_to(case[0]).as_posix(): p.file_sha(path) for path in case[0].rglob("*") if path.is_file()}
    assert after == before
