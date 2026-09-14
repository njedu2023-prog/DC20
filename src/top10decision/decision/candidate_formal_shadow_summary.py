"""Version-isolated, fully enumerated projection of the natural Shadow ledger.

No price collection, fitting, score changes, broker claims, or research gate
changes occur here. Every admitted D is revalidated by the existing private
publication-proof and native-outcome validators. Missing calendar D and absent
slots remain records, never synthetic no-fills. Publication/CAS is the caller's
responsibility; this is an immutable read-only computation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re

from top10decision.decision import candidate_profit_publication as publication
from work.profit_1000_upgrade import candidate_natural_statistics as statistics

SCHEMA = "dc20_candidate_formal_shadow_summary_v1"
DAY_SCHEMA = "dc20_candidate_formal_profit_day_v1"
ACTIVATION_ID = "dc20_profit_ridge_999666_auction_exit1000_v1"
MODEL_SHA = "999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589"
EFFECTIVE_D = "20260914"
STATISTICS_SHA = "31e64f9f37cb4043c4955064c96421728d4dde9376c9c5a5a608082947e33995"
GROUPS = ("candidate_top1", "candidate_top2")
FLAGS = {
    "actual_execution_claimed": False,
    "actual_capacity_verified": False,
    "capital_nav_claimed": False,
    "actual_account_drawdown_claimed": False,
    "profitability_improvement_proven": False,
    "old_model_results_consumed": False,
    "missing_or_pending_is_zero": False,
    "fees_subtracted_again": False,
    "scores_recalculated": False,
    "research_registration_changed": False,
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return statistics.natural.scorer.canonical_sha(value)


def _guard():
    path = Path(statistics.__file__).absolute()
    require(path.name == "candidate_natural_statistics.py"
        and sha(statistics.natural._read(path)[0]) == STATISTICS_SHA,
        "FROZEN_NATURAL_STATISTICS_CHANGED")
    return statistics._guard()


def _activation(config):
    publication.validate_activation(config)
    require(type(config) is dict and type(config.get("enabled")) is bool,
        "EXPLICIT_ACTIVATION_STATE_REQUIRED")
    for key, value in {"activation_id": ACTIVATION_ID,
            "model_canonical_sha256": MODEL_SHA,
            "effective_from_signal_date": EFFECTIVE_D}.items():
        require(config.get(key) == value, "ACTIVATION_IDENTITY_CHANGED:" + key)
    return {key: config[key] for key in
        ("activation_id", "model_canonical_sha256", "effective_from_signal_date")}


def _projections(projections, items, identity):
    require(type(projections) in (list, tuple) and len(projections) <= 4096,
        "BOUNDED_PROJECTION_LIST_REQUIRED")
    by_day = {}
    for projection in projections:
        require(type(projection) is dict and projection.get("schema_version") == DAY_SCHEMA,
            "EXACT_FORMAL_DAY_SCHEMA_REQUIRED")
        day = projection.get("signal_date")
        require(day in items and day not in by_day, "UNBOUND_OR_DUPLICATE_FORMAL_DAY")
        for key, value in identity.items():
            require(projection.get(key) == value, "PROJECTION_MODEL_VERSION_CHANGED")
        item = items[day]
        frozen = statistics.outcomes._snapshot(item["snapshot_raw"], item["expected_snapshot_sha256"])
        require(projection.get("snapshot_file_sha256") == item["expected_snapshot_sha256"],
            "PROJECTION_SNAPSHOT_CHANGED")
        for key in ("signal_date", "exec_date", "exit_date", "candidate_slots"):
            statistics._same(projection.get(key), frozen[key], "FROZEN_PROJECTION_CHANGED:" + key)
        rows = [{key: row[key] for key in
            ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")}
            for row in frozen["prediction"]["rows"]]
        supplied = projection.get("rows")
        require(type(supplied) is list and all(type(row) is dict for row in supplied),
            "FORMAL_PROJECTION_ROWS_REQUIRED")
        compact = [{key: row.get(key) for key in
            ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")} for row in supplied]
        statistics._same(sorted(compact, key=lambda row: row["candidate_rank"]),
            sorted(rows, key=lambda row: row["candidate_rank"]), "FROZEN_RANK_OR_SCORE_CHANGED")
        by_day[day] = (projection, frozen)
    require(set(by_day) == set(items), "PROJECTIONS_MUST_COVER_ALL_ADMITTED_DAYS")
    return by_day


def _missing_day(day, rank):
    return {"signal_date": day, "slot": rank, "ts_code": None,
        "candidate_rank": None, "candidate_score": None, "promotion_rank": None,
        "exec_date": None, "exit_date": None, "snapshot_file_sha256": None,
        "formal_projection_sha256": None,
        "status": "MISSING_D_PUBLICATION", "proxy_fill": None,
        "slot_net_return": None, "label_available_date": None,
        "ledger_as_of_date": None, "entry_price": None, "entry_price_source": None,
        "exit_price": None, "actual_exit_date": None,
        "actual_exit_time": None, "held_limit_up_sessions": None,
        "terminal_economic_source_sha256": None}


def _native_row(item, code):
    if item["ledger_raw"] is None or code is None:
        return None
    # All ledger seals, point-in-time identities, terminal histories, and native
    # contracts have already been checked by summarize_natural_statistics.
    ledger = statistics.natural._json(item["ledger_raw"])
    native = ledger["versions"][-1]["native_singleton_reports"][code]
    return native


def _row(item, frozen, raw_row, rank, asof, projection_sha256=None):
    slot = frozen["candidate_slots"][rank - 1]
    row = {**_missing_day(frozen["signal_date"], rank), **raw_row,
        **{key: slot[key] for key in
            ("candidate_rank", "candidate_score", "promotion_rank")},
        "exec_date": frozen["exec_date"], "exit_date": frozen["exit_date"],
        "snapshot_file_sha256": item["expected_snapshot_sha256"],
        "formal_projection_sha256": projection_sha256}
    # The absence of an outcome journal before the buy day is normal waiting,
    # not missing T-price evidence or a zero-return observation.
    if row["status"] == "MISSING_OUTCOME_LEDGER" and asof < frozen["exec_date"]:
        row["status"] = "PENDING_T_NOT_DUE"
    native = _native_row(item, slot["ts_code"])
    if native is not None:
        original = native["rows"][0]
        for key in ("entry_price", "entry_price_source", "actual_exit_date",
                "actual_exit_time", "held_limit_up_sessions"):
            row[key] = original.get(key)
        row["exit_price"] = (original.get("exit_evidence") or {}).get("exit_price")
        if row["status"] in statistics.outcomes.TERMINAL:
            row["terminal_economic_source_sha256"] = canonical({
                "rows": native["rows"], "source_files": native["source_files"]})
    return row


def _metrics(sequence, *, coverage_complete):
    result = statistics._metrics(sequence)
    terminal = [row for row in sequence if row["status"] in statistics.outcomes.TERMINAL]
    peak = equity = 1.0
    drawdown = 0.0
    for row in terminal:
        equity *= 1 + row["slot_net_return"]
        require(math.isfinite(equity), "INVALID_SYNTHETIC_EQUITY")
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    counts = Counter(row["status"] for row in sequence)
    result.update({
        "expected_selection_days": len(sequence),
        "verified_selection_days": sum(row["snapshot_file_sha256"] is not None for row in sequence),
        "missing_publication_slots": counts["MISSING_D_PUBLICATION"],
        "not_due_slots": sum(count for status, count in counts.items()
            if status in {"PENDING_T", "PENDING_T_NOT_DUE", "PENDING_T1"}),
        "complete_day_count": len(terminal),
        "cumulative_available": coverage_complete and bool(terminal),
        "synthetic_max_drawdown": drawdown if coverage_complete and terminal else None,
        "synthetic_drawdown_interpretation": "SETTLED_SLOT_PRODUCT_ONLY_NOT_CAPITAL_ACCOUNT_DRAWDOWN",
        "entire_window_settled": bool(sequence) and len(terminal) == len(sequence),
    })
    if not coverage_complete:
        # Explicitly enumerating omitted D makes the omission visible, but it
        # must not turn a selected subset into a whole-window cumulative claim.
        result["synthetic_cumulative_net_return"] = None
    return result


def _previous(previous, result):
    if previous is None:
        return
    require(type(previous) is dict and previous.get("schema_version") == SCHEMA,
        "EXACT_PRIOR_SUMMARY_REQUIRED")
    require(previous.get("summary_sha256") == canonical({key: value for key, value in previous.items()
        if key != "summary_sha256"}), "PRIOR_SUMMARY_SEAL_CHANGED")
    for key in ("activation_id", "model_canonical_sha256", "effective_from_signal_date", "test_only"):
        require(previous.get(key) == result[key], "PRIOR_MODEL_VERSION_OR_MODE_CHANGED")
    require(previous.get("as_of_date") <= result["as_of_date"], "SUMMARY_ASOF_CANNOT_GO_BACKWARD")
    for group in GROUPS:
        fresh = {row["signal_date"]: row for row in result["groups"][group]["daily_sequence"]}
        for old in previous["groups"][group]["daily_sequence"]:
            row = fresh.get(old["signal_date"])
            require(row is not None, "PREVIOUS_D_CANNOT_DISAPPEAR")
            if old["snapshot_file_sha256"] is not None:
                for key in ("snapshot_file_sha256", "formal_projection_sha256", "ts_code", "candidate_rank", "candidate_score",
                        "promotion_rank", "exec_date", "exit_date"):
                    statistics._same(row[key], old[key], "FROZEN_SAME_D_CHANGED:" + key)
            if old["status"] in statistics.outcomes.TERMINAL:
                for key in ("status", "proxy_fill", "slot_net_return", "label_available_date",
                        "entry_price", "entry_price_source", "exit_price", "actual_exit_date",
                        "actual_exit_time", "held_limit_up_sessions",
                        "terminal_economic_source_sha256"):
                    statistics._same(row[key], old[key], "TERMINAL_SHADOW_HISTORY_CHANGED:" + key)


def build_summary(activation_config, projections, day_inputs, *, as_of_date,
        calendar_raw, expected_calendar_sha256, previous_summary=None,
        cached_terminal_days=None, clock=None):
    """Enumerate every activated trading D; project two independently verified slots.

    ``projections`` is the complete list of formal D projections, not the bounded
    settlement worker's selected batch. ``day_inputs`` uses the exact existing
    natural-statistics contract for every non-cached projection. Cached days
    may only copy previously published, closed economic rows (or truly frozen
    absent candidate slots); no pending result or new D may use that path.
    The caller binds all lists and the previous summary to the complete current
    main Git tree and verifies the original public day, snapshot and P0 bytes.
    A cache is existing published ledger continuity, not newly issued proof.
    Missing D
    are explicitly recorded by comparing this list with the pinned calendar.
    A supplied test clock can never produce a natural-mode summary.
    """
    code_state = _guard()
    identity = _activation(activation_config)
    config_seal = canonical(activation_config)
    projection_seal = canonical(projections)
    prior_seal = canonical(previous_summary) if previous_summary is not None else None
    asof = statistics.natural.scorer._date(as_of_date, "as_of_date")
    dates = statistics._calendar(calendar_raw, expected_calendar_sha256)
    require(asof in dates and EFFECTIVE_D in dates, "REGISTERED_TRADING_D_REQUIRED")
    expected_days = [day for day in dates if EFFECTIVE_D <= day <= asof]
    require(type(day_inputs) in (list, tuple), "EXACT_DAY_INPUT_LIST_REQUIRED")
    # Date/type/order validation takes place before reading any ledger content.
    supplied_days = statistics._identities(day_inputs, asof, dates)
    require(all(day in expected_days for day in supplied_days), "OLD_MODEL_OR_FUTURE_D_FORBIDDEN")
    cached = [] if cached_terminal_days is None else cached_terminal_days
    require(type(cached) in (list, tuple) and list(cached) == sorted(set(cached))
        and all(day in expected_days for day in cached) and not set(cached).intersection(supplied_days),
        "EXACT_NONOVERLAPPING_CACHED_DAYS_REQUIRED")
    cached_seal = canonical(cached)
    require(type(projections) in (list, tuple) and len(projections) <= 4096
        and all(type(day) is dict for day in projections), "BOUNDED_PROJECTION_LIST_REQUIRED")
    projection_days = [day.get("signal_date") for day in projections]
    require(len(projection_days) == len(set(projection_days))
        and set(projection_days) == set(supplied_days) | set(cached), "EXACT_FRESH_PLUS_CACHED_PROJECTIONS_REQUIRED")
    all_projections = {day["signal_date"]: day for day in projections}
    cached_rows, cached_bindings = {}, {}
    if cached:
        require(type(previous_summary) is dict, "CACHED_DAYS_REQUIRE_BOUND_PREVIOUS_SUMMARY")
        prior_config = {**activation_config, "enabled": previous_summary.get("enabled")}
        validate_summary(previous_summary, prior_config, allow_test_only=clock is not None,
            calendar_raw=calendar_raw)
        previous_groups = {name: {row["signal_date"]: row for row in
            previous_summary["groups"][name]["daily_sequence"]} for name in GROUPS}
        previous_bindings = {row["signal_date"]: row for row in previous_summary["input_bindings"]}
        for day in cached:
            require(day in previous_bindings, "CACHED_DAY_WAS_NOT_PUBLISHED")
            rows = [previous_groups[name].get(day) for name in GROUPS]
            require(all(type(row) is dict and (row["status"] in statistics.outcomes.TERMINAL
                or row["status"] == "MISSING_CANDIDATE") for row in rows),
                "PENDING_OR_MISSING_EVIDENCE_CANNOT_BE_CACHED")
            projection = all_projections[day]
            digest = canonical(projection)
            require(projection.get("schema_version") == DAY_SCHEMA
                and all(projection.get(key) == value for key, value in identity.items())
                and all(row["formal_projection_sha256"] == digest
                    and row["snapshot_file_sha256"] == projection.get("snapshot_file_sha256")
                    for row in rows), "CACHED_FORMAL_DAY_OR_SOURCE_CHANGED")
            cached_rows[day] = deepcopy(rows)
            cached_bindings[day] = deepcopy(previous_bindings[day])
    report = statistics.summarize_natural_statistics(day_inputs, as_of_date=asof,
        calendar_raw=calendar_raw, expected_calendar_sha256=expected_calendar_sha256, clock=clock)
    require(report["signal_dates"] == list(supplied_days), "NATURAL_DAY_UNIVERSE_CHANGED")
    items = {item["signal_date"]: item for item in day_inputs}
    by_day = _projections([all_projections[day] for day in supplied_days], items, identity)
    published_days = sorted(set(supplied_days) | set(cached))
    missing = [day for day in expected_days if day not in all_projections]
    complete = not missing
    groups = {}
    for rank, group in enumerate(GROUPS, 1):
        supplied = {row["signal_date"]: row for row in report["groups"][group]["daily_sequence"]}
        sequence = [cached_rows[day][rank - 1] if day in cached_rows else
            _missing_day(day, rank) if day not in by_day else
            _row(items[day], by_day[day][1], supplied[day], rank, asof,
                canonical(all_projections[day])) for day in expected_days]
        groups[group] = _metrics(sequence, coverage_complete=complete)
    all_bindings = {row["signal_date"]: deepcopy(row) for row in report["input_bindings"]}
    all_bindings.update(cached_bindings)
    input_bindings = [all_bindings[day] for day in published_days]
    result = {"schema_version": SCHEMA, **identity, "model_version": identity["activation_id"],
        "enabled": activation_config["enabled"], "publication_paused": not activation_config["enabled"],
        "activation_signal_date": EFFECTIVE_D, "as_of_date": asof,
        "status": "SYNTHETIC_CLOCK_ONLY" if clock is not None else "FORMAL_PUBLICATION_PAUSED" if not activation_config["enabled"] else
            ("AWAITING_FIRST_ACTIVATED_D" if not expected_days else
            "FULL_ENUMERATED_WITH_MISSING_D" if missing else "FULL_ENUMERATED_SHADOW_STATISTICS"),
        "test_only": clock is not None, "coverage_complete": complete,
        "universe_scope": "EVERY_PINNED_CALENDAR_D_FROM_ACTIVATION_THROUGH_ASOF",
        "signal_dates": expected_days, "published_signal_dates": published_days,
        "missing_signal_dates": missing, "expected_day_count": len(expected_days),
        "published_day_count": len(published_days), "expected_slot_count": 2 * len(expected_days),
        "groups": groups, "round_trip_cost_rate": .0045, "shadow_notional_cny": 100000,
        "entry_policy_id": "research_canonical_price_capacity_split_no_cap_v3",
        "exit_policy_id": "dc20_exit_1000_limit_hold_20260912_v1",
        "evidence_kind": "VERIFIED_PREBUY_SELECTION_WITH_POSTHOC_PRICE_PROXY_OUTCOMES_NOT_BROKER_FILLS",
        "cumulative_interpretation": "PRODUCT_OF_TERMINAL_SLOT_RETURNS_ONLY_NOT_ACCOUNT_NAV_OVERLAPPING_HOLDS_IGNORED",
        "win_rate_denominator": "FILLED_SETTLED_SLOTS_ONLY_ZERO_NOT_WIN",
        "calendar_sha256": expected_calendar_sha256, "activation_config_sha256": config_seal,
        "verified_native_inputs_sha256": canonical(input_bindings),
        "input_bindings": input_bindings, **FLAGS}
    _previous(previous_summary, result)
    require(config_seal == canonical(activation_config) and projection_seal == canonical(projections)
        and prior_seal == (canonical(previous_summary) if previous_summary is not None else None)
        and cached_seal == canonical(cached),
        "SUMMARY_INPUT_CHANGED_DURING_VALIDATION")
    require(_guard() == code_state, "SUMMARY_DEPENDENCY_CHANGED")
    result["summary_sha256"] = canonical(result)
    return result


def _validation_calendar():
    return statistics.natural._read(statistics.ROOT / statistics.labels.settlement.CALENDAR_PATH)[0]


def validate_summary(summary, activation_config, *, allow_test_only=False, calendar_raw=None):
    """Validate cached presentation bytes/contracts; do not issue source proof.

    A publisher must separately bind the supplied object to the original Git
    blob SHA. This validator cannot upgrade a dictionary into a live private
    publication proof. It recomputes all metrics from the enumerated two-slot
    sequences and rejects hidden/duplicated days or legacy-model aggregates.
    """
    identity = _activation(activation_config)
    require(type(summary) is dict and summary.get("schema_version") == SCHEMA,
        "EXACT_FORMAL_SUMMARY_REQUIRED")
    require(summary.get("summary_sha256") == canonical({key: value for key, value in summary.items()
        if key != "summary_sha256"}), "FORMAL_SUMMARY_SEAL_CHANGED")
    for key, value in identity.items():
        require(summary.get(key) == value, "SUMMARY_MODEL_VERSION_CHANGED")
    require(summary.get("model_version") == ACTIVATION_ID
        and summary.get("activation_signal_date") == EFFECTIVE_D,
        "SUMMARY_ACTIVATION_IDENTITY_CHANGED")
    require(summary.get("enabled") is activation_config["enabled"]
        and summary.get("publication_paused") is (not activation_config["enabled"]),
        "SUMMARY_PUBLICATION_STATE_CHANGED")
    require(type(summary.get("test_only")) is bool
        and (allow_test_only or summary["test_only"] is False), "SYNTHETIC_SUMMARY_CANNOT_BE_PUBLISHED")
    for key, value in FLAGS.items():
        require(summary.get(key) is value, "SUMMARY_EXECUTION_BOUNDARY_CHANGED:" + key)
    for key, value in {
        "entry_policy_id": "research_canonical_price_capacity_split_no_cap_v3",
        "exit_policy_id": "dc20_exit_1000_limit_hold_20260912_v1",
        "evidence_kind": "VERIFIED_PREBUY_SELECTION_WITH_POSTHOC_PRICE_PROXY_OUTCOMES_NOT_BROKER_FILLS",
        "universe_scope": "EVERY_PINNED_CALENDAR_D_FROM_ACTIVATION_THROUGH_ASOF",
        "cumulative_interpretation": "PRODUCT_OF_TERMINAL_SLOT_RETURNS_ONLY_NOT_ACCOUNT_NAV_OVERLAPPING_HOLDS_IGNORED",
        "win_rate_denominator": "FILLED_SETTLED_SLOTS_ONLY_ZERO_NOT_WIN",
        "round_trip_cost_rate": .0045, "shadow_notional_cny": 100000,
        "activation_config_sha256": canonical(activation_config),
    }.items():
        statistics._same(summary.get(key), value, "SUMMARY_POLICY_CHANGED:" + key)
    asof = statistics.natural.scorer._date(summary.get("as_of_date"), "summary_asof")
    calendar = _validation_calendar() if calendar_raw is None else calendar_raw
    dates = statistics._calendar(calendar, summary.get("calendar_sha256"))
    expected = [day for day in dates if EFFECTIVE_D <= day <= asof]
    require(asof in dates and summary.get("signal_dates") == expected,
        "SUMMARY_CALENDAR_D_UNIVERSE_CHANGED")
    published = summary.get("published_signal_dates")
    require(type(published) is list and published == sorted(set(published))
        and all(day in expected for day in published), "SUMMARY_PUBLISHED_DAY_UNIVERSE_CHANGED")
    missing = [day for day in expected if day not in published]
    complete = not missing
    require(summary.get("missing_signal_dates") == missing
        and summary.get("coverage_complete") is complete, "SUMMARY_COVERAGE_CLAIM_CHANGED")
    for key, number in {"expected_day_count": len(expected), "published_day_count": len(published),
            "expected_slot_count": 2 * len(expected)}.items():
        require(type(summary.get(key)) is int and summary[key] == number, "SUMMARY_COUNT_CHANGED:" + key)
    status = "SYNTHETIC_CLOCK_ONLY" if summary["test_only"] else "FORMAL_PUBLICATION_PAUSED" if not summary["enabled"] else (
        "AWAITING_FIRST_ACTIVATED_D" if not expected else
        "FULL_ENUMERATED_WITH_MISSING_D" if missing else "FULL_ENUMERATED_SHADOW_STATISTICS")
    require(summary.get("status") == status, "SUMMARY_STATUS_CHANGED")
    groups = summary.get("groups")
    require(type(groups) is dict and set(groups) == set(GROUPS), "EXACT_TWO_SUMMARY_GROUPS_REQUIRED")
    bindings = summary.get("input_bindings")
    require(type(bindings) is list and [row.get("signal_date") for row in bindings
        if type(row) is dict] == published, "SUMMARY_BINDING_D_UNIVERSE_CHANGED")
    require(summary.get("verified_native_inputs_sha256") == canonical(bindings),
        "SUMMARY_NATIVE_BINDINGS_SEAL_CHANGED")
    by_day = {row["signal_date"]: row for row in bindings}
    for rank, name in enumerate(GROUPS, 1):
        group = groups[name]
        require(type(group) is dict and type(group.get("daily_sequence")) is list,
            "SUMMARY_DAILY_SEQUENCE_REQUIRED")
        rows = group["daily_sequence"]
        require([row.get("signal_date") for row in rows if type(row) is dict] == expected,
            "SUMMARY_SLOT_SEQUENCE_D_CHANGED")
        for row in rows:
            require(set(row) == set(_missing_day(row["signal_date"], rank))
                and type(row["slot"]) is int and row["slot"] == rank,
                "EXACT_SHADOW_SLOT_ROW_REQUIRED")
            day = row["signal_date"]
            if day not in published:
                statistics._same(row, _missing_day(day, rank), "MISSING_D_MUST_NOT_CLAIM_ECONOMICS")
                continue
            digest = row["snapshot_file_sha256"]
            statistics.natural.scorer._sha(digest)
            statistics.natural.scorer._sha(row["formal_projection_sha256"])
            require(by_day[day].get("snapshot_file_sha256") == digest,
                "SUMMARY_SNAPSHOT_BINDING_CHANGED")
            require(dates[dates.index(day) + 1:dates.index(day) + 3] == [row["exec_date"], row["exit_date"]],
                "SUMMARY_D_T_T1_CHANGED")
            if row["ts_code"] is None:
                require(row["status"] == "MISSING_CANDIDATE"
                    and row["candidate_rank"] is row["candidate_score"] is row["promotion_rank"] is None,
                    "MISSING_CANDIDATE_CANNOT_CLAIM_RANK")
            else:
                require(type(row["ts_code"]) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", row["ts_code"])
                    and type(row["candidate_rank"]) is int and row["candidate_rank"] == rank
                    and type(row["promotion_rank"]) is int and row["promotion_rank"] > 0,
                    "SUMMARY_CANDIDATE_IDENTITY_CHANGED")
                statistics.natural.scorer._number(row["candidate_score"], "candidate_score")
            require(type(row["status"]) is str and (row["proxy_fill"] is None
                or (type(row["proxy_fill"]) is int and row["proxy_fill"] in (0, 1))),
                "SUMMARY_NATIVE_STATUS_REQUIRED")
            if row["status"] in statistics.outcomes.TERMINAL:
                statistics.natural.scorer._number(row["slot_net_return"], "slot_net_return")
                statistics.natural.scorer._sha(row["terminal_economic_source_sha256"])
                if row["status"] != statistics.labels.SETTLED:
                    require(row["slot_net_return"] == 0 and row["proxy_fill"] == 0,
                        "ONLY_KNOWN_NO_FILL_CAN_BE_ZERO_SLOT")
                else:
                    require(row["proxy_fill"] == 1, "SETTLED_PRICE_PROXY_REQUIRED")
            else:
                require((row["status"].startswith("PENDING_")
                    or row["status"] in {"MISSING_CANDIDATE", "MISSING_OUTCOME_LEDGER"})
                    and row["slot_net_return"] is None and row["terminal_economic_source_sha256"] is None,
                    "UNKNOWN_OR_PENDING_CANNOT_BE_SETTLED")
        statistics._same(group, _metrics(rows, coverage_complete=complete), "SUMMARY_METRICS_CHANGED")
    return deepcopy(summary)
