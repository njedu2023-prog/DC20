"""Offline authority-composition tests; synthetic internals are NOT real admission.

The semantic fixtures isolate already-verified upstream types and the pinned
archive adapter. Separate tests exercise the actual immutable diagnostic ZIP.
No fixture invokes HTTP or writes production/source roots.
"""
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from work.profit_1000_upgrade import nontrading_session_truth as truth
from work.profit_1000_upgrade import daily_gap_verify as daily_verify


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def typed_fixture(cls, **values):
    obj = object.__new__(cls)
    for key, value in values.items():
        object.__setattr__(obj, key, truth._freeze(value))
    return obj


@pytest.fixture
def composition(tmp_path, monkeypatch):
    """Only the upstream verifier/old-ZIP layers are unit-test isolated here."""
    market, candidates, daily_root = (tmp_path / name for name in ("market", "candidates", "daily"))
    for root in (market, candidates, daily_root): root.mkdir()
    archive, prior = tmp_path / "diagnostic.zip", tmp_path / "prior.json"
    archive.write_bytes(b"SYNTHETIC UNIT TEST ARCHIVE NOT REAL EVIDENCE")
    monkeypatch.setattr(truth.old, "DIAGNOSTIC_ZIP_SHA", truth._sha(archive))
    old_daily, events = {}, {}
    marker = ({"origin": "old_suspension_diagnostic", "path": "test/data.json", "sha256": "a" * 64},)
    for _, _, code, dates, _, _ in truth.GROUPS[:-1]:
        old_daily[(dates[0], code)] = marker
        events.update({(d, code): marker for d in dates})
    monkeypatch.setattr(truth, "_old_evidence", lambda _: (old_daily, events, ()))
    records = {}
    for api, day, code in daily_verify.PAIRS:
        fields = truth.DAILY_FIELDS if api == "daily" else truth.old.FIELDS
        rows = [] if api == "daily" else [[code, day, None, "S"]]
        records[(api, day, code)] = {"status": "DAILY_EMPTY_SOURCE_WRITTEN" if api == "daily" else "SUSPEND_EVENTS_SOURCE_WRITTEN",
            "table": {"fields": list(fields), "items": rows, "count": 0, "has_more": False},
            "source_files": [{"path": f"unit/{api}/{day}/{code}.json", "sha256": "b" * 64}]}
    # Exact market files are deliberately tiny and contain no target stock row.
    for day, code in truth.ALLOWED_PAIRS:
        put(market / "data/market/raw" / day[:4] / day / "daily.csv", b"ts_code,trade_date,open,high,low,close,pre_close,vol\n")
    candidate = typed_fixture(truth.candidate_verify.VerifiedCandidateCollection,
        root=candidates, as_of_date=truth.AS_OF_DATE, base_archive_sha256="c" * 64,
        receipt_sha256="d" * 64, frozen_manifest_sha256="e" * 64,
        base_file_bindings=[{"path": p, "sha256": sha} for p, sha in truth._inventory(market).items()])
    daily = typed_fixture(daily_verify.VerifiedDailyGapCollection, root=daily_root,
        as_of_date=truth.AS_OF_DATE, prior_label_report_sha256="pending", diagnostic_archive_sha256=truth.old.DIAGNOSTIC_ZIP_SHA,
        receipt_sha256="f" * 64, plan_sha256="1" * 64, records=records)
    rows = [{"signal_date": d, "exec_date": t, "ts_code": code,
             "label_status": "PENDING_EXIT_MISSING_DAILY", "missing_evidence_kind": "daily",
             "missing_evidence_date": days[0], "missing_evidence_code": code}
            for d, t, code, days, _, _ in truth.GROUPS]
    rows.extend({"fixture_only": i} for i in range(6753 - len(rows)))
    report = {"as_of_date": truth.AS_OF_DATE, "base_archive_sha256": candidate.base_archive_sha256,
              "candidate_collection_receipt_sha256": candidate.receipt_sha256,
              "candidate_manifest_sha256": candidate.frozen_manifest_sha256, "rows": rows}
    prior.write_bytes(truth.old._json(report))
    monkeypatch.setattr(truth, "PRIOR_LABEL_SHA", truth._sha(prior))
    object.__setattr__(daily, "prior_label_report_sha256", truth.PRIOR_LABEL_SHA)
    # Binding sentinels ensure tests can detect stale upstream state.
    token_file = candidates / "unit-bound.txt"
    token_file.write_bytes(b"upstream-verified-unit-fixture")
    sentinel_sha = truth._sha(token_file)
    def check_upstream(self):
        truth.require(truth._sha(token_file) == sentinel_sha, "UPSTREAM_SOURCE_CHANGED")
    monkeypatch.setattr(truth.candidate_verify.VerifiedCandidateCollection, "assert_unchanged", check_upstream)
    monkeypatch.setattr(daily_verify.VerifiedDailyGapCollection, "assert_unchanged", check_upstream)
    return {"verified_candidate_scope": candidate, "verified_daily_scope": daily,
            "market_root": market, "diagnostic_archive_path": archive, "prior_label_report_path": prior,
            "_records": records, "_old_daily": old_daily, "_events": events, "_sentinel": token_file,
            "_report": report}


def args(fixture):
    return {k: v for k, v in fixture.items() if not k.startswith("_")}


def set_records(fixture):
    object.__setattr__(fixture["verified_daily_scope"], "records", truth._freeze(fixture["_records"]))


def rebind_market(fixture):
    object.__setattr__(fixture["verified_candidate_scope"], "base_file_bindings",
        truth._freeze([{"path": p, "sha256": sha} for p, sha in truth._inventory(fixture["market_root"]).items()]))


def test_all_exact_sessions_and_only_those_are_qualified(composition):
    proofs = truth.verify_nontrading_sessions(**args(composition))
    assert tuple((p.trade_date, p.ts_code) for p in proofs) == truth.ALLOWED_PAIRS
    assert len(proofs) == 18
    for proof in proofs:
        proof.assert_unchanged()
        value = proof.evidence()
        assert value["can_advance_holding_day"] is value["market_absence_verified"] is True
        assert value["research_only"] is True
        assert value["production_activation_allowed"] is value["actual_execution_claimed"] is False
        assert value["provider_timestamp_semantics_confirmed"] is False
        assert not {"open", "close", "price", "net_return", "gross_return", "slot_net_return"} & set(value)
        assert value["prior_label_report_sha256"] == truth.PRIOR_LABEL_SHA


def test_proof_is_deep_immutable_and_not_publicly_constructible(composition):
    proof = truth.verify_nontrading_sessions(**args(composition))[0]
    with pytest.raises(FrozenInstanceError): proof.trade_date = "20260911"
    with pytest.raises(TypeError): proof.source_files[0]["sha256"] = "0" * 64
    with pytest.raises(TypeError): proof._context.expected_files["new"] = "x"
    with pytest.raises(ValueError, match="PRIVATE_CONSTRUCTION"):
        truth.VerifiedNoTradingSession(proof.trade_date, proof.ts_code, (), proof._context)
    forged = object.__new__(truth.VerifiedNoTradingSession)
    with pytest.raises(ValueError, match="UNISSUED"):
        forged.assert_unchanged()


@pytest.mark.parametrize("bad", [True, {}, None, object()])
@pytest.mark.parametrize("key", ["verified_candidate_scope", "verified_daily_scope"])
def test_ducktyped_or_boolean_upstream_authority_rejected(composition, bad, key):
    composition[key] = bad
    with pytest.raises(ValueError, match="EXACT_"):
        truth.verify_nontrading_sessions(**args(composition))


@pytest.mark.parametrize("bad", [[], (True,), (object(),), (None, None)])
def test_unregistered_or_multiple_minute_chains_rejected(composition, bad):
    with pytest.raises(ValueError, match="ZERO_OR_ONE"):
        truth.verify_nontrading_sessions(**args(composition), verified_minute_scopes=bad)


@pytest.mark.parametrize("which", ["daily", "event", "both"])
def test_old_missing_row_or_s_event_alone_never_proves_session(composition, which):
    pair = ("20240429", "600234.SH")
    if which in ("daily", "both"): composition["_old_daily"].pop(pair)
    if which in ("event", "both"): composition["_events"].pop(pair)
    assert truth.verify_nontrading_session(*pair, **args(composition)) is None


@pytest.mark.parametrize("missing", [("daily", "20250611", "603226.SH"), ("daily", "20240430", "600083.SH"),
                                    ("suspend_d", "20240430", "600083.SH")])
def test_failed_or_unrequested_new_source_remains_pending(composition, missing):
    composition["_records"].pop(missing); set_records(composition)
    assert truth.verify_nontrading_session(missing[1], missing[2], **args(composition)) is None


@pytest.mark.parametrize("kind,timing", [("R", None), ("S", "09:30-10:00"), ("S", " "), ("S", False)])
def test_new_suspension_requires_exact_same_day_S_and_empty_timing(composition, kind, timing):
    record = composition["_records"][("suspend_d", "20240430", "600083.SH")]
    record["table"]["items"] = [["600083.SH", "20240430", timing, kind]]; set_records(composition)
    assert truth.verify_nontrading_session("20240430", "600083.SH", **args(composition)) is None


@pytest.mark.parametrize("row", [["600083.SH", "20240430", 0, 0, 0, 0, 0, 0, 0, 0],
                                  ["600083.SH", "20240430", None, None, None, None, None, 0, 0, 0],
                                  ["600083.SH", "20240430", 10, 10, 10, 10, 10, 100, 1000, 0]])
def test_any_daily_row_including_zero_null_or_trade_is_not_empty(composition, row):
    composition["_records"][("daily", "20240430", "600083.SH")]["table"]["items"] = [row]
    set_records(composition)
    assert truth.verify_nontrading_session("20240430", "600083.SH", **args(composition)) is None


@pytest.mark.parametrize("key,value", [("count", True), ("count", 1), ("has_more", 0), ("has_more", True),
                                      ("items", None), ("fields", list(reversed(truth.DAILY_FIELDS)))])
def test_incomplete_or_malformed_empty_daily_cannot_mint(composition, key, value):
    composition["_records"][("daily", "20240430", "600083.SH")]["table"][key] = value
    set_records(composition)
    with pytest.raises(ValueError, match="COMPLETE_TABLE"):
        truth.verify_nontrading_sessions(**args(composition))


@pytest.mark.parametrize("suffix", ["10,10,10,10,10,100", "0,0,0,0,0,0", ",,,,,"])
def test_existing_market_row_never_treated_as_empty(composition, suffix):
    path = composition["market_root"] / "data/market/raw/2024/20240430/daily.csv"
    path.write_text("ts_code,trade_date,open,high,low,close,pre_close,vol\n600083.SH,20240430," + suffix + "\n")
    rebind_market(composition)
    assert truth.verify_nontrading_session("20240430", "600083.SH", **args(composition)) is None


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("index", [0, 1])
def test_any_original_minute_presence_even_orphan_or_invalid_blocks(composition, legacy, index):
    pair = ("20240430", "600083.SH")
    paths = (truth.legacy_minutes.minute_paths if legacy else truth.minute_truth.paths)(composition["market_root"], *pair)
    put(paths[index], b"invalid existing minute is not absence")
    rebind_market(composition)
    assert truth.verify_nontrading_session(*pair, **args(composition)) is None


def test_unknown_inventory_file_is_rejected_not_ignored(composition):
    put(composition["market_root"] / "extra.json", b"{}")
    with pytest.raises(ValueError, match="EXACT_VERIFIED_UNION"):
        truth.verify_nontrading_sessions(**args(composition))


@pytest.mark.parametrize("change", ["market", "missing_minute", "archive", "prior", "upstream"])
def test_after_admission_changes_including_missing_status_invalidate_proof(composition, change):
    proof = truth.verify_nontrading_sessions(**args(composition))[0]
    if change == "market":
        path = composition["market_root"] / "data/market/raw/2024/20240429/daily.csv"
        path.write_bytes(path.read_bytes() + b"600234.SH,20240429,1,1,1,1,1,1\n")
    elif change == "missing_minute":
        put(truth.minute_truth.paths(composition["market_root"], "20240429", "600234.SH")[0], b"{}")
    else:
        path = composition[{"archive": "diagnostic_archive_path", "prior": "prior_label_report_path", "upstream": "_sentinel"}[change]]
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError): proof.assert_unchanged()


def test_root_alias_empty_fake_root_and_extra_directory_rejected(composition, tmp_path):
    original = composition["market_root"]
    alias = tmp_path / "alias"; alias.symlink_to(original)
    composition["market_root"] = alias
    with pytest.raises(ValueError, match="UNALIASED"):
        truth.verify_nontrading_sessions(**args(composition))
    alias.unlink(); alias.mkdir(); composition["market_root"] = alias
    with pytest.raises(ValueError, match="EXACT_VERIFIED_UNION"):
        truth.verify_nontrading_sessions(**args(composition))
    composition["market_root"] = original
    (original / "empty").mkdir()
    with pytest.raises(ValueError, match="EMPTY_MARKET_DIRECTORY"):
        truth.verify_nontrading_sessions(**args(composition))


def test_factory_has_no_filesystem_writes(composition):
    root = composition["market_root"].parent
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    truth.verify_nontrading_sessions(**args(composition))
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("day,code", [("20240430", "600234.SH"), ("20260914", "600083.SH"),
                                     ("20260725", "002036.SZ"), ("20240430", "600083.BJ")])
def test_session_scope_never_extended_from_prior_S_or_missing_dates(day, code):
    with pytest.raises(ValueError): truth.verify_nontrading_session(day, code)


def test_frozen_old_diagnostic_archive_full_validation_and_exact_days():
    path = Path("/Users/moclh/Documents/ChatGPT/DC20/upgrade-evidence-20260912.bE2NIC/suspension-34672430723.zip")
    if not path.exists(): pytest.skip("fixed old diagnostic is not present; never download")
    daily, events, bindings = truth._old_evidence(path)
    assert len(daily) == 5 and len(events) == 17 and len(bindings) == 20
    assert ("20240430", "600234.SH") not in events
    assert ("20260725", "002036.SZ") not in events
    assert all(b["origin"] == "old_suspension_diagnostic" for b in bindings)


def add_minute_scope(fixture, monkeypatch, *, pair=("20250610", "603226.SH")):
    from work.profit_1000_upgrade.test_minute_truth import rows_for, raw_response
    root = fixture["market_root"].parent / "minute_collection"
    root.mkdir()
    bodies = truth.minute_truth.source_bytes(raw_response(rows_for(*pair)), *pair,
        request_params=truth.minute_truth.request_parameters(*pair), fetched_at_utc="2026-09-12T07:00:00+00:00")
    for base in (root, fixture["market_root"]):
        for path, raw in zip(truth.minute_truth.paths(base, *pair), bodies): put(path, raw)
    bindings = truth.minute_truth.load(root, *pair)["source_files"]
    fixture["_report"]["rows"][-1] = {"label_status": "PENDING_EXIT_MISSING_MINUTES", "ts_code": pair[1],
        "missing_evidence_code": pair[1], "missing_evidence_date": pair[0],
        "missing_evidence_kind": "research_exit_1000_1m_0931"}
    fixture["prior_label_report_path"].write_bytes(truth.old._json(fixture["_report"]))
    monkeypatch.setattr(truth, "PRIOR_LABEL_SHA", truth._sha(fixture["prior_label_report_path"]))
    object.__setattr__(fixture["verified_daily_scope"], "prior_label_report_sha256", truth.PRIOR_LABEL_SHA)
    scope = typed_fixture(truth.minute_verify.VerifiedMinuteCollection, root=root, as_of_date=truth.AS_OF_DATE,
        label_report_sha256=truth.PRIOR_LABEL_SHA, gap_pairs=(pair,), successful_pairs=(pair,), source_bindings=bindings)
    expected = truth._inventory(root)
    def check(self): truth.require(truth._inventory(self.root) == expected, "MINUTE_UPSTREAM_CHANGED")
    monkeypatch.setattr(truth.minute_verify.VerifiedMinuteCollection, "assert_unchanged", check)
    fixture["verified_minute_scopes"] = (scope,)
    return scope


def test_verified_minute_union_is_valid_inventory_but_blocks_nontrading_at_that_pair(composition, monkeypatch):
    scope = add_minute_scope(composition, monkeypatch)
    result = truth.verify_nontrading_sessions(**args(composition))
    assert len(result) == 17
    assert ("20250610", "603226.SH") not in {(p.trade_date, p.ts_code) for p in result}
    result[0].assert_unchanged()
    path = truth.minute_truth.paths(scope.root, "20250610", "603226.SH")[0]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="MINUTE_UPSTREAM_CHANGED"): result[0].assert_unchanged()


def test_minute_plan_must_equal_every_exact_prior_pending_pair(composition, monkeypatch):
    scope = add_minute_scope(composition, monkeypatch)
    object.__setattr__(scope, "gap_pairs", (("20250611", "603226.SH"),))
    with pytest.raises(ValueError, match="EXACT_PRIOR_MISSING_SET"):
        truth.verify_nontrading_sessions(**args(composition))


def test_minute_union_cannot_silently_overwrite_any_base_file(composition, monkeypatch):
    add_minute_scope(composition, monkeypatch)
    rebind_market(composition)
    with pytest.raises(ValueError, match="CANNOT_OVERWRITE_BASE"):
        truth.verify_nontrading_sessions(**args(composition))


def test_augmented_minute_pair_must_equal_original_verified_bytes(composition, monkeypatch):
    add_minute_scope(composition, monkeypatch)
    path = truth.minute_truth.paths(composition["market_root"], "20250610", "603226.SH")[1]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="EXACT_VERIFIED_UNION"):
        truth.verify_nontrading_sessions(**args(composition))


@pytest.mark.parametrize("field,value", [("as_of_date", "20260914"), ("prior_label_report_sha256", "0" * 64),
                                        ("diagnostic_archive_sha256", "0" * 64)])
def test_wrong_daily_authority_identity_does_not_qualify(composition, field, value):
    object.__setattr__(composition["verified_daily_scope"], field, value)
    with pytest.raises(ValueError, match="IDENTITY_CONFLICT"):
        truth.verify_nontrading_sessions(**args(composition))


def test_unpinned_diagnostic_is_not_accepted(tmp_path):
    path = tmp_path / "other.zip"; path.write_bytes(b"not original archive")
    with pytest.raises(ValueError, match="NOT_PINNED"): truth._old_evidence(path)


def test_pin_change_is_explicit_not_pending_or_price(monkeypatch):
    monkeypatch.setitem(truth.PINNED, "work/profit_1000_upgrade/suspension_truth.py", "0" * 64)
    with pytest.raises(ValueError, match="DEPENDENCY_CHANGED"): truth._guard()
