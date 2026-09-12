"""Offline synthetic minute admission; monkeypatched gates grant no real truth."""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from work.profit_1000_upgrade import candidate_labels_overlay as overlay, labels_v3, minute_truth, policy_v3
from work.profit_1000_upgrade.test_candidate_labels_overlay import authority_for, candidate
from work.profit_1000_upgrade.test_labels import CODE, D, T, T1, NEXT, case, binding, daily, write_csv
from work.profit_1000_upgrade.test_labels_v2 import research_minutes
from work.profit_1000_upgrade.test_labels_v3 import canonical


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def market_case(case, tmp_path_factory, monkeypatch):
    original, manifest = case
    manifest["rows"][0]["shadow_max_price"] = None
    manifest.update(policy_v3.CONTRACT)
    manifest["plan_version"] = "v3"
    candidate_root = tmp_path_factory.mktemp("market-candidate")
    candidate(candidate_root)
    authority = authority_for((original, manifest, candidate_root), monkeypatch)
    prior = overlay.build_labels(original, manifest, as_of_date=T1, candidate_source_root=candidate_root, verified_scope=authority)
    assert prior["rows"][0]["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    prior_path = tmp_path_factory.mktemp("prior-report") / "labels.json"
    prior_path.write_text(json.dumps(prior))
    market_root = tmp_path_factory.mktemp("minute-only")
    paths = research_minutes(market_root)
    augmented = tmp_path_factory.mktemp("augmented-base")
    shutil.copytree(original, augmented, dirs_exist_ok=True)
    for path in paths:
        target = augmented / path.relative_to(market_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    market = SimpleNamespace(root=market_root, as_of_date=T1, gap_pairs=((T1, CODE),),
        successful_pairs=((T1, CODE),), source_bindings=tuple(binding(market_root, p) for p in paths),
        receipt_sha256="c" * 64, plan_sha256="d" * 64, label_report_sha256=digest(prior_path), checks=0)
    def unchanged():
        market.checks += 1
        for item in market.source_bindings:
            labels_v3._binding(market_root, dict(item))
    market.assert_unchanged = unchanged
    def gate(value):
        assert value is market, "wrong synthetic authority"
        value.assert_unchanged()
        return value
    monkeypatch.setattr(overlay, "_market_authority", gate)
    return SimpleNamespace(original=original, manifest=manifest, candidate_root=candidate_root,
        authority=authority, prior=prior, prior_path=prior_path, market_root=market_root,
        market=market, augmented=augmented)


def run(ctx, **extra):
    args = dict(as_of_date=T1, candidate_source_root=ctx.candidate_root, verified_scope=ctx.authority,
        verified_market_scope=ctx.market, market_source_root=ctx.market_root, prior_label_report=ctx.prior_path)
    args.update(extra)
    return overlay.build_labels(ctx.augmented, ctx.manifest, **args)


def revise_prior(ctx, mutate):
    value = json.loads(ctx.prior_path.read_bytes())
    mutate(value)
    ctx.prior_path.write_text(json.dumps(value))
    ctx.market.label_report_sha256 = digest(ctx.prior_path)


def test_minute_authority_is_exact_type_not_boolean_or_mapping():
    for value in (True, {}, {"accepted": True}, SimpleNamespace()):
        with pytest.raises(ValueError, match="VERIFIED_MINUTE_COLLECTION_REQUIRED"):
            overlay._market_authority(value)


def test_admitted_candidate_and_minute_origins_negative_cost_once_no_writes(market_case):
    ctx = market_case
    before = {str(root): inventory(root) for root in (ctx.original, ctx.augmented, ctx.market_root, ctx.candidate_root)}
    out = run(ctx)
    row = out["rows"][0]
    assert row["label_status"] == labels_v3.SETTLED
    assert row["net_return"] == pytest.approx(-.0245)
    assert row["slot_net_return"] == row["conditional_net_return"] == row["net_return"]
    assert row["entry_price_evidence"]["source_origin"] == "candidate"
    assert row["entry_price_evidence"]["daily_source_origin"] == "base"
    assert out["source_overlay_contract"] == overlay.CONTRACT
    assert {b["origin"] for b in out["source_files"]} == {"base", "candidate", "minute_overlay"}
    actual = {b["path"]: b["sha256"] for b in out["source_files"] if b["origin"] == "minute_overlay"}
    assert actual == {b["path"]: b["sha256"] for b in ctx.market.source_bindings}
    assert out["market_collection_receipt_sha256"] == ctx.market.receipt_sha256
    assert out["market_registered_plan_sha256"] == ctx.market.plan_sha256
    assert out["market_registered_label_report_sha256"] == digest(ctx.prior_path)
    # The reused synthetic candidate helper replaces its entry permission gate;
    # its counter records the two end checks (prior build and augmented build).
    assert ctx.market.checks >= 2 and ctx.authority.checks >= 2
    assert out["files_written"] == 0
    assert not out["training_performed"] and not out["production_activation_allowed"]
    assert not out["actual_execution_claimed"] and not out["actual_capacity_verified"]
    assert before == {str(root): inventory(root) for root in (ctx.original, ctx.augmented, ctx.market_root, ctx.candidate_root)}


def test_frozen_builder_consumes_minute_without_changing_old_auction_identity(market_case, monkeypatch):
    ctx = market_case
    # Rebuild the synthetic original authority/prior after adding a canonical
    # base source; the separately admitted minute bytes stay outside the base.
    canonical(ctx.original)
    ctx.authority = authority_for((ctx.original, ctx.manifest, ctx.candidate_root), monkeypatch,
                                  gaps=(), successes=())
    prior = overlay.build_labels(ctx.original, ctx.manifest, as_of_date=T1,
        candidate_source_root=ctx.candidate_root, verified_scope=ctx.authority)
    ctx.prior_path.write_text(json.dumps(prior)); ctx.market.label_report_sha256 = digest(ctx.prior_path)
    shutil.copytree(ctx.original, ctx.augmented, dirs_exist_ok=True)
    out = run(ctx)
    assert out["rows"][0]["auction_source_policy_id"] == policy_v3.AUCTION_SOURCE_POLICY_ID
    assert out["rows"][0]["label_status"] == labels_v3.SETTLED
    assert out["rows"][0]["net_return"] == pytest.approx(-.0245)
    assert {b["origin"] for b in out["source_files"]} == {"base", "minute_overlay"}
    assert "source_overlay_policy_id" not in out["rows"][0]


@pytest.mark.parametrize("omitted", ["verified_market_scope", "market_source_root", "prior_label_report"])
def test_partial_optional_arguments_rejected(market_case, omitted):
    with pytest.raises(ValueError, match="COMPLETE_MINUTE_ADMISSION_ARGUMENTS_REQUIRED"):
        run(market_case, **{omitted: None})


def test_no_market_admission_does_not_accept_extra_minutes(market_case):
    with pytest.raises(ValueError, match="BASE_EXTRA_OR_MISSING_FILES"):
        run(market_case, verified_market_scope=None, market_source_root=None, prior_label_report=None)


def test_no_market_keeps_explicit_none_receipts(market_case):
    ctx = market_case
    out = overlay.build_labels(ctx.original, ctx.manifest, as_of_date=T1,
        candidate_source_root=ctx.candidate_root, verified_scope=ctx.authority)
    assert out["market_collection_receipt_sha256"] is None
    assert out["market_registered_plan_sha256"] is None
    assert out["market_registered_label_report_sha256"] is None
    assert out["rows"][0]["slot_net_return"] is None


@pytest.mark.parametrize("field", ["candidate_collection_receipt_sha256", "base_archive_sha256", "candidate_manifest_sha256",
    "as_of_date", "source_overlay_contract", "entry_policy_id", "label_policy_id", "round_trip_cost_rate",
    "feature_evidence_kind", "feature_columns", "training_performed", "production_activation_allowed", "files_written"])
def test_prior_metadata_cannot_rebaseline(market_case, field):
    revise_prior(market_case, lambda value: value.__setitem__(field, "changed"))
    with pytest.raises(ValueError, match="PRIOR_LABEL_CONTRACT_OR_AUTHORITY_CHANGED"):
        run(market_case)


@pytest.mark.parametrize("change", ["drop", "duplicate", "wrong_code", "wrong_signal"])
def test_prior_all_candidate_identities_required(market_case, change):
    def mutate(report):
        if change == "drop": report["rows"] = []
        elif change == "duplicate": report["rows"].append(deepcopy(report["rows"][0]))
        else: report["rows"][0]["ts_code" if change == "wrong_code" else "signal_date"] = "000001.SZ" if change == "wrong_code" else "20260909"
    revise_prior(market_case, mutate)
    with pytest.raises(ValueError, match="PRIOR_FULL_CANDIDATE_IDENTITIES_CHANGED"):
        run(market_case)


@pytest.mark.parametrize("field,value", [("promotion_rank", 2), ("stage_transition", "3→4"),
    ("feature_as_of_date", "20260909"), ("feature_available_at", "2026-09-10T22:59:00+08:00"),
    ("features", {"promotion_probability": .5, "path_change": -.1}), ("shadow_max_price", 10)])
def test_prior_frozen_feature_rank_identity_cannot_change(market_case, field, value):
    revise_prior(market_case, lambda r: r["rows"][0].__setitem__(field, value))
    with pytest.raises(ValueError):
        run(market_case)


@pytest.mark.parametrize("field,value", [("missing_evidence_kind", "daily"), ("missing_evidence_code", "000001.SZ"),
    ("missing_evidence_date", T), ("missing_evidence_date", NEXT)])
def test_prior_missing_pair_must_be_exact_exit_evidence(market_case, field, value):
    revise_prior(market_case, lambda r: r["rows"][0].__setitem__(field, value))
    with pytest.raises(ValueError, match="PRIOR_MISSING_MINUTE"):
        run(market_case)


@pytest.mark.parametrize("pairs", [(), ((T1, CODE), (T1, CODE)), ((T1, "000001.SZ"),), ((T1, CODE), (NEXT, CODE))])
def test_registration_cannot_omit_or_expand_prior_missing_pairs(market_case, pairs):
    market_case.market.gap_pairs = pairs
    with pytest.raises(ValueError, match="REGISTERED_MINUTE_PAIRS_NOT_EXACT_PRIOR_GAPS"):
        run(market_case)


def test_prior_content_changed_even_with_unchanged_metadata_rejected(market_case):
    market_case.prior_path.write_bytes(market_case.prior_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="REGISTERED_PRIOR_LABEL_SHA_CHANGED"):
        run(market_case)


@pytest.mark.parametrize("value", [True, "not-sha", None])
def test_minute_authority_digest_is_not_an_arbitrary_permission(market_case, value):
    market_case.market.receipt_sha256 = value
    with pytest.raises(ValueError, match="MINUTE_AUTHORITY_SHA_REQUIRED"):
        run(market_case)


def test_second_implicit_market_chain_rejected(market_case):
    revise_prior(market_case, lambda r: r.__setitem__("market_collection_receipt_sha256", "a" * 64))
    with pytest.raises(ValueError, match="MULTIPLE_MINUTE_ADMISSION_CHAIN_NOT_REGISTERED"):
        run(market_case)


@pytest.mark.parametrize("part", [0, 1])
def test_registered_pair_cannot_overwrite_even_one_old_orphan(market_case, part):
    ctx = market_case
    item = ctx.market.source_bindings[part]
    ctx.authority.base_file_bindings += (dict(item),)
    with pytest.raises(ValueError, match="MINUTE_OVERLAY_CANNOT_REPLACE_EXISTING_OR_ORPHAN_SOURCE"):
        run(ctx)


@pytest.mark.parametrize("change", ["missing_copy", "changed_copy", "extra_copy", "missing_binding", "duplicate_binding"])
def test_augmented_inventory_is_exact_source_pair_union(market_case, change):
    ctx = market_case
    path = minute_truth.paths(ctx.augmented, T1, CODE)[0]
    if change == "missing_copy": path.unlink()
    elif change == "changed_copy": path.write_bytes(path.read_bytes() + b" ")
    elif change == "extra_copy": (ctx.augmented / "unregistered.txt").write_text("extra")
    elif change == "missing_binding": ctx.market.source_bindings = ctx.market.source_bindings[:1]
    else: ctx.market.source_bindings += ctx.market.source_bindings[:1]
    with pytest.raises(ValueError):
        run(ctx)


def test_rejected_or_unattempted_minute_stays_pending_not_zero(market_case):
    ctx = market_case
    for path in minute_truth.paths(ctx.augmented, T1, CODE): path.unlink()
    ctx.market.successful_pairs = (); ctx.market.source_bindings = ()
    out = run(ctx)
    assert out["rows"][0]["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    assert out["rows"][0]["slot_net_return"] is out["rows"][0]["net_return"] is None
    assert not any(b["origin"] == "minute_overlay" for b in out["source_files"])
    assert out["market_collection_receipt_sha256"] == ctx.market.receipt_sha256


def test_unqualified_semantics_do_not_reach_frozen_builder(market_case, monkeypatch):
    real = overlay.minute_truth.load
    def changed(*args):
        value = real(*args)
        if value is not None: value["provider_timestamp_semantics_confirmed"] = True
        return value
    monkeypatch.setattr(overlay.minute_truth, "load", changed)
    with pytest.raises(ValueError, match="UNQUALIFIED_REGISTERED_MINUTE_SOURCE"):
        run(market_case)


def test_market_root_must_be_exact_authority_root(market_case):
    with pytest.raises(ValueError, match="MINUTE_SOURCE_ROOT_MISMATCH"):
        run(market_case, market_source_root=market_case.original)


def test_prior_symlink_rejected(market_case, tmp_path_factory):
    link = tmp_path_factory.mktemp("prior-alias") / "alias.json"
    link.symlink_to(market_case.prior_path)
    with pytest.raises(ValueError, match="UNALIASED_PRIOR_LABEL_FILE_REQUIRED"):
        run(market_case, prior_label_report=link)


def test_source_mutation_during_replay_does_not_escape_end_gate(market_case, monkeypatch):
    ctx = market_case
    real = overlay.base_labels.build_labels
    def mutate(*args, **kwargs):
        out = real(*args, **kwargs)
        path = minute_truth.paths(ctx.market_root, T1, CODE)[0]
        path.write_bytes(path.read_bytes() + b" ")
        return out
    monkeypatch.setattr(overlay.base_labels, "build_labels", mutate)
    with pytest.raises(ValueError, match="source SHA mismatch"):
        run(ctx)


def test_prior_mutation_during_replay_does_not_escape_end_gate(market_case, monkeypatch):
    ctx = market_case
    real = overlay.base_labels.build_labels
    def mutate(*args, **kwargs):
        out = real(*args, **kwargs)
        ctx.prior_path.write_bytes(ctx.prior_path.read_bytes() + b" ")
        return out
    monkeypatch.setattr(overlay.base_labels, "build_labels", mutate)
    with pytest.raises(ValueError, match="REGISTERED_PRIOR_LABEL_SHA_CHANGED"):
        run(ctx)


def test_limit_hold_may_expose_next_missing_day_without_fabricating_return(market_case, monkeypatch):
    ctx = market_case
    daily(ctx.original, T1, price=11)
    daily(ctx.original, NEXT, price=10.5)
    ctx.authority = authority_for((ctx.original, ctx.manifest, ctx.candidate_root), monkeypatch, asof=NEXT)
    prior = overlay.build_labels(ctx.original, ctx.manifest, as_of_date=NEXT,
        candidate_source_root=ctx.candidate_root, verified_scope=ctx.authority)
    assert prior["rows"][0]["missing_evidence_date"] == T1
    ctx.prior_path.write_text(json.dumps(prior)); ctx.market.label_report_sha256 = digest(ctx.prior_path)
    ctx.market.as_of_date = NEXT
    paths = research_minutes(ctx.market_root, price=11)
    ctx.market.source_bindings = tuple(binding(ctx.market_root, p) for p in paths)
    shutil.copytree(ctx.original, ctx.augmented, dirs_exist_ok=True)
    for path in paths: shutil.copyfile(path, ctx.augmented / path.relative_to(ctx.market_root))
    out = run(ctx, as_of_date=NEXT)
    row = out["rows"][0]
    assert row["label_status"] == "PENDING_EXIT_MISSING_MINUTES"
    assert row["missing_evidence_date"] == NEXT
    assert row["minute_source_observed_dates"] == [T1]
    assert row["net_return"] is row["slot_net_return"] is None
    assert not row["cohort_complete"]


@pytest.mark.parametrize("which", ["data", "meta"])
def test_loaded_source_origin_is_not_inferred_from_same_minute_schema(market_case, which):
    ctx = market_case
    revise_prior(ctx, lambda r: r["source_files"].append({"origin": "base", **dict(ctx.market.source_bindings[which == "meta"])}))
    with pytest.raises(ValueError, match="PRIOR_SOURCE_NOT_IN_ORIGINAL_AUTHORITIES"):
        run(ctx)


def test_prior_pending_cannot_claim_zero_return(market_case):
    revise_prior(market_case, lambda r: r["rows"][0].__setitem__("slot_net_return", 0.))
    with pytest.raises(ValueError, match="OVERLAY_PENDING_CANNOT_BECOME_ZERO_OR_TERMINAL"):
        run(market_case)


def test_forged_success_outside_registered_gap_rejected(market_case):
    market_case.market.successful_pairs = ((NEXT, CODE),)
    with pytest.raises(ValueError, match="REGISTERED_MINUTE_PAIRS_NOT_EXACT_PRIOR_GAPS"):
        run(market_case)


@pytest.fixture
def partly_settled_case(market_case, monkeypatch):
    ctx = market_case
    other = "000001.SZ"
    second = deepcopy(ctx.manifest["rows"][0]); second.update(ts_code=other, promotion_rank=2)
    ctx.manifest["rows"].append(second); ctx.manifest["expected_candidate_codes"][D].append(other)
    for day in (T, T1):
        for name in ("daily", "stk_limit"):
            path = ctx.original / f"data/market/raw/2026/{day}/{name}.csv"
            with path.open() as handle: rows = list(csv.DictReader(handle))
            rows.append({**rows[0], "ts_code": other}); write_csv(path, rows)
    research_minutes(ctx.original)  # First candidate was already settled.
    candidate(ctx.candidate_root, code=other)
    pairs = tuple(sorted(((T, CODE), (T, other))))
    ctx.authority = authority_for((ctx.original, ctx.manifest, ctx.candidate_root), monkeypatch,
        gaps=pairs, successes=pairs)
    prior = overlay.build_labels(ctx.original, ctx.manifest, as_of_date=T1,
        candidate_source_root=ctx.candidate_root, verified_scope=ctx.authority)
    assert [r["label_status"] for r in prior["rows"]] == [labels_v3.SETTLED, "PENDING_EXIT_MISSING_MINUTES"]
    ctx.prior = prior; ctx.prior_path.write_text(json.dumps(prior))
    ctx.market.label_report_sha256 = digest(ctx.prior_path)
    for path in minute_truth.paths(ctx.market_root, T1, CODE): path.unlink()
    new_paths = research_minutes(ctx.market_root, code=other)
    ctx.market.source_bindings = tuple(binding(ctx.market_root, p) for p in new_paths)
    ctx.market.gap_pairs = ctx.market.successful_pairs = ((T1, other),)
    shutil.copytree(ctx.original, ctx.augmented, dirs_exist_ok=True)
    for path in new_paths: shutil.copyfile(path, ctx.augmented / path.relative_to(ctx.market_root))
    return ctx


def test_old_terminal_negative_row_kept_identical_except_cohort_completeness(partly_settled_case):
    ctx = partly_settled_case
    out = run(ctx)
    old, current = ctx.prior["rows"][0], out["rows"][0]
    assert old["cohort_complete"] is False and current["cohort_complete"] is True
    assert {k: v for k, v in old.items() if k != "cohort_complete"} == {
        k: v for k, v in current.items() if k != "cohort_complete"}
    assert current["net_return"] == pytest.approx(-.0245)
    sources = {b["path"]: b["origin"] for b in out["source_files"]}
    for p in minute_truth.paths(ctx.augmented, T1, CODE):
        assert sources[p.relative_to(ctx.augmented).as_posix()] == "base"
    for b in ctx.market.source_bindings: assert sources[b["path"]] == "minute_overlay"


def test_terminal_guard_rejects_changed_even_internally_consistent_profit(partly_settled_case, monkeypatch):
    ctx = partly_settled_case
    real = overlay._resume
    def wrong(*args, **kwargs):
        row = real(*args, **kwargs)
        if row["ts_code"] == CODE:
            for k in ("net_return", "conditional_net_return", "slot_net_return"): row[k] += .01
            row["exit_evidence"]["gross_return"] += .01
        return row
    monkeypatch.setattr(overlay, "_resume", wrong)
    with pytest.raises(ValueError, match="PRIOR_TERMINAL_ECONOMICS_OR_IDENTITY_CHANGED"):
        run(ctx)


@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_prior_source_binding_list_is_mandatory(market_case, value):
    revise_prior(market_case, lambda r: r.__setitem__("source_files", value))
    with pytest.raises(ValueError, match="PRIOR_SOURCE_BINDINGS_REQUIRED"):
        run(market_case)
