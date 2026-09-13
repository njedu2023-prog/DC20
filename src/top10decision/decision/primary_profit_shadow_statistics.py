"""Rebuild pre-10:00-policy public statistics from their validated raw inputs.

The settlement engine remains unchanged. Historical projections retain their
original format and unrounded accounting; policy-era projections pass through.
No archived summary is an input to this adapter.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any

from top10decision.decision import executable_profit_shadow_settlement as settlement


_INPUT_PATH = re.compile(
    r"data/decision_executable_profit/forward/(?:selections/shadow_|"
    r"verifications/t_verification_|settlements/settlement_)20\d{6}\.json"
)


def _bound_inputs(root: Path, summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payloads = {}
    for binding in summary["input_files"]:
        relative = binding["path"]
        settlement._expect(_INPUT_PATH.fullmatch(relative) is not None and relative not in payloads,
                           "historical statistics input path is foreign or duplicated")
        path = settlement._safe_existing_file(root, Path(relative), label="historical statistics input")
        raw = path.read_bytes()
        settlement._expect(settlement._sha256_bytes(raw) == binding["sha256"],
                           "historical statistics input changed after validation")
        payloads[relative] = json.loads(raw)
    return payloads


def _historical_records(payloads: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    prefix = settlement.SELECTION_ROOT.as_posix() + "/"
    for relative, selection in sorted(payloads.items()):
        if not relative.startswith(prefix):
            continue
        day = selection["signal_date"]
        verification = payloads.get((settlement.VERIFICATION_ROOT / f"t_verification_{day}.json").as_posix())
        settled = payloads.get((settlement.SETTLEMENT_ROOT / f"settlement_{day}.json").as_posix())
        verification_rows = {int(row["shadow_slot"]): row for row in verification["rows"]} if verification else {}
        settlement_rows = {int(row["shadow_slot"]): row for row in settled["rows"]} if settled else {}
        for frozen in settlement._selected_rows(selection):
            slot = int(frozen["shadow_slot"])
            verified = verification_rows.get(slot)
            terminal_row = settlement_rows.get(slot)
            proxy_fill = verified.get("proxy_fill") if verified is not None else None
            records.append({
                "signal_date": day,
                "shadow_slot": slot,
                "stage_transition": str(frozen["stage_transition"]),
                "t_validated": verified is not None,
                "proxy_fill": proxy_fill,
                "terminal": bool(terminal_row is not None or proxy_fill == 0),
                "net_return_after_cost": terminal_row.get("net_return_after_cost") if terminal_row is not None else None,
                "stress_net_return": terminal_row.get("stress_net_return") if terminal_row is not None else None,
                "strategy_slot_return": terminal_row.get("strategy_slot_return") if terminal_row is not None else 0.0 if proxy_fill == 0 else None,
                "delayed_trading_days": terminal_row.get("delayed_trading_days") if terminal_row is not None else None,
                "blocked_exit_sessions": terminal_row.get("blocked_exit_sessions") if terminal_row is not None else 0,
            })
    return records


def build_public_statistics(repo_root: Path, *, as_of_date: str) -> dict[str, Any]:
    # The original builder validates the calendar, selections, truth bindings,
    # dated exit policy and complete input scope before compatibility work.
    summary = settlement.build_public_statistics(repo_root, as_of_date=as_of_date)
    if summary["as_of_date"] >= settlement.EXIT_POLICY_EFFECTIVE_DATE:
        return summary

    root = repo_root.resolve(strict=True)
    payloads = _bound_inputs(root, summary)
    records = _historical_records(payloads)
    historical = copy.deepcopy(summary)
    historical["cohorts"] = {
        "all_selected_slots": settlement._cohort_metrics(records),
        "shadow_slot_1": settlement._cohort_metrics([row for row in records if row["shadow_slot"] == 1]),
        "shadow_slot_2": settlement._cohort_metrics([row for row in records if row["shadow_slot"] == 2]),
        "stage_2_to_3": settlement._cohort_metrics([row for row in records if row["stage_transition"] == "2→3"]),
        "stage_3_to_4": settlement._cohort_metrics([row for row in records if row["stage_transition"] == "3→4"]),
    }
    historical["pending_definitions"]["pending_exit_slots"] = (
        "proxy-filled slots without a resolved first tradable open from scheduled T+1 onward"
    )
    historical["snapshot_sha256"] = settlement._payload_snapshot(historical)
    for binding in summary["input_files"]:
        path = settlement._safe_existing_file(root, Path(binding["path"]), label="historical statistics input")
        settlement._expect(settlement._sha256(path) == binding["sha256"],
                           "historical statistics input changed during reconstruction")
    settlement.validate_statistics(historical, require_public_cumulative=True)
    return historical
