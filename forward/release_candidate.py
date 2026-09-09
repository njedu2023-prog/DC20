"""Build hash-bound, unpublished release candidates from completed inference.

This is the read-only handoff boundary, not a Git/Pages writer or admission
command. It never changes REPLAY to NATURAL, imports an old ledger, activates
an epoch, or records a Shadow. Slots are an explicit preview of future
admission; only the future prospective daybook writer can record them.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from . import bundle_rehearsal as rehearsal
from .release_evidence import read_promotion, read_profit
from .storage import compare_and_swap, encoded

DATE_FIELDS = ("signal_date", "exec_date", "exit_date")
NOT_RECORDED = "AWAITING_NEW_EPOCH_NOT_RECORDED"


def _slots(day, rows, rank_field, limit):
    return [{**{key: day[key] for key in DATE_FIELDS},
             "slot": f"Top{row[rank_field]}", "ts_code": row["ts_code"],
             "name": row["name"], "promotion_rank": row["promotion_rank"],
             **({"profit_rank": row["profit_rank"]} if rank_field == "profit_rank" else {}),
             "recording_status": NOT_RECORDED, "ledger_recorded": False}
            for row in rows if row[rank_field] <= limit]


def _separate(output, inputs):
    for directory in inputs:
        directory = Path(directory).resolve(strict=True)
        if output == directory or output in directory.parents or directory in output.parents:
            raise ValueError("candidate output must be separate from every input artifact")


def _check_current(root, evidence, revision, env):
    if rehearsal._head(root) != revision:
        raise ValueError("candidate source HEAD changed")
    if evidence["origin"] == "NATURAL_SCHEDULE_STAGING":
        return rehearsal.verify_natural_evidence(root, evidence, env=env, now=rehearsal._utc())
    return None


def build_candidate(root, primary_directory, primary_receipt_sha, output, *,
                    kind="promotion", profit_directory=None, profit_receipt_sha=None,
                    env=None):
    """Write one fresh candidate artifact; P0 never reads or waits for P1.

    Hash pins are supplied through the producing jobs' outputs. A self hash,
    an old result file, or a modified source revision is not an admission.
    The successful receipt is written last, after a second natural-slot check.
    """
    if kind not in {"promotion", "profit"}:
        raise ValueError("candidate kind must be promotion or profit")
    if kind == "promotion" and (profit_directory is not None or profit_receipt_sha is not None):
        raise ValueError("P0 release candidate must not depend on P1")
    if kind == "profit" and (profit_directory is None or profit_receipt_sha is None):
        raise ValueError("P1 release candidate requires its completed externally pinned artifact")
    root, output = rehearsal._migration(root, output)
    _separate(output, [primary_directory] + ([profit_directory] if kind == "profit" else []))
    current_env = os.environ if env is None else env
    before = rehearsal._state(root)
    primary_info = read_promotion(root, Path(primary_directory), primary_receipt_sha, env=current_env)
    primary = primary_info["primary"]
    day = primary["day"]
    selected_info = primary_info
    if kind == "profit":
        selected_info = read_profit(root, primary_info, Path(profit_directory), profit_receipt_sha, env=current_env)
    evidence = copy.deepcopy(selected_info["evidence"])
    revision = primary_info["receipt"]["source_revision"]
    gate = _check_current(root, evidence, revision, current_env)
    rows = copy.deepcopy(day["rows"] if kind == "promotion" else selected_info["profit"]["rows"])
    rank_field = "promotion_rank" if kind == "promotion" else "profit_rank"
    rows.sort(key=lambda row: row[rank_field])
    # P1 carries frozen promotion identity and path for one self-contained
    # presentation, without changing its independent profit rank or score.
    if kind == "profit":
        members = {row["ts_code"]: row for row in day["rows"]}
        rows = [{**copy.deepcopy(members[row["ts_code"]]), **row} for row in rows]
    generated = day["generated_at_utc"] if kind == "promotion" else selected_info["profit"]["generated_at_utc"]
    candidate = {
        "schema_version": f"dc20_forward_release_candidate_{kind}_v1",
        "kind": kind, "status": "CANDIDATE_NOT_PUBLISHED",
        **{key: day[key] for key in DATE_FIELDS},
        "source_revision": revision, "generation_mode": "REPLAY",
        "inference_generated_at_utc": generated,
        "assembled_at_utc": rehearsal._utc(),
        "input_origin": evidence["origin"], "input_manifest_sha256": evidence["manifest_sha256"],
        "inference_receipt_sha256": selected_info["receipt_sha256"],
        "promotion_receipt_sha256": primary_info["receipt_sha256"],
        "promotion_model_sha256": day["source"]["model_sha256"],
        "member_set_sha256": day["source"]["members_sha256"],
        "candidate_count": len(rows), "rows": rows,
        "promotion_top3": _slots(day, day["rows"], "promotion_rank", 3),
        "publication_verified": False, "production_enabled": False,
        "forward_ledger_eligible": False, "ledger_written": False,
        "epoch_activated_at_utc": None, "new_forward_days": 0,
        "formal_trade_count": 0,
        "notice": "Inference evidence and slot preview only; not published, activated, or recorded in a forward ledger.",
    }
    if kind == "profit":
        candidate.update(profit_model_sha256=selected_info["profit"]["source"]["model_sha256"],
                         score_semantics="relative_research_score_not_probability_or_expected_return",
                         shadow_top2=_slots(day, rows, "profit_rank", 2))
    rehearsal._unchanged(root, before)
    filename = kind + ".json"
    content_sha = compare_and_swap(output / filename, candidate, None)
    # Data files alone are incomplete. A slow write crossing the original
    # deadline must not leave a success receipt or an apparent admission.
    gate = _check_current(root, evidence, revision, current_env)
    receipt = {
        "schema_version": "dc20_forward_release_candidate_receipt_v1", "kind": kind,
        "status": "CANDIDATE_COMPLETE_NOT_PUBLISHED", "source_revision": revision,
        **{key: day[key] for key in DATE_FIELDS}, "files": {filename: content_sha},
        "input_evidence": evidence, "assembly_gate": gate,
        "generation_mode": "REPLAY", "candidate_complete": True,
        "production_enabled": False, "publication_verified": False,
        "forward_ledger_eligible": False, "ledger_written": False,
        "formal_trade_count": 0, "new_forward_days": 0,
        "completed_at_utc": gate["checked_at_utc"] if gate is not None else rehearsal._utc(),
    }
    compare_and_swap(output / "receipt.json", receipt, None)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="kind", required=True)
    for kind in ("promotion", "profit"):
        command = sub.add_parser(kind)
        command.add_argument("--primary", type=Path, required=True)
        command.add_argument("--primary-receipt-sha256", required=True)
        command.add_argument("--output", type=Path, required=True)
        if kind == "profit":
            command.add_argument("--profit", type=Path, required=True)
            command.add_argument("--profit-receipt-sha256", required=True)
    args = parser.parse_args(argv)
    receipt = build_candidate(args.root, args.primary, args.primary_receipt_sha256,
        args.output, kind=args.kind, profit_directory=getattr(args, "profit", None),
        profit_receipt_sha=getattr(args, "profit_receipt_sha256", None))
    rehearsal.emit_job_outputs({"receipt_sha256": hashlib.sha256(encoded(receipt)).hexdigest()})
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
