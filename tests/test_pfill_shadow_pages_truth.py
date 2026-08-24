from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.decision_pages_truth import (  # noqa: E402
    THREE_RANK_CSV_FIELDS,
    DecisionPagesTruthError,
    _validate_three_rank_contract_payload,
    _validate_three_rank_downloads,
)
from top10decision.decision.three_rank import (  # noqa: E402
    THREE_RANK_CONTRACT_VERSION,
    build_three_rank_contract,
    materialize_three_rank_artifacts,
)


SIGNAL_DATE = "20260820"
EXEC_DATE = "20260821"
EXIT_DATE = "20260824"


def _ready_row(code: str, rank: int, count: int = 3) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": code,
        "name": f"样本{rank}",
        "industry": "测试行业",
        "stage_transition": "2→3" if rank % 2 else "3→4",
        "top10_selected": 1,
        "three_rank_contract_version": THREE_RANK_CONTRACT_VERSION,
        "promotion_pool_size": count,
        "promotion_rank": rank,
        "predicted_promotion_probability": 0.90 - rank * 0.05,
        "big_loss_safety_rank": count + 1 - rank,
        "predicted_big_loss_probability": 0.02 + (count - rank) * 0.03,
        "profit_rank": rank,
        "predicted_profit_probability": 0.75 - rank * 0.04,
        "feature_snapshot_sha256": "f" * 64,
        "p_fill_shadow_rank": rank,
        "p_fill_shadow_probability": 0.95 - rank * 0.05,
        "p_fill_shadow_status": "SHADOW_READY",
        "p_fill_shadow_model_version": "p_fill_shadow_v1",
        "p_fill_shadow_model_as_of_date": "20260819",
        "p_fill_shadow_model_artifact_sha256": "4" * 64,
        "p_fill_shadow_validation_gate_pass_count": 26,
        "p_fill_shadow_validation_gate_total_count": 26,
        "p_fill_shadow_validation_gate_score_pct": 100.0,
    }
    gate_scores = {
        "promotion": (26, 26, 100.0),
        "big_loss": (18, 26, 69.2),
        "profit": (15, 26, 57.7),
    }
    for index, head in enumerate(("promotion", "big_loss", "profit"), start=1):
        pass_count, total_count, score = gate_scores[head]
        row.update(
            {
                f"{head}_model_status": "READY",
                f"{head}_model_version": f"{head}_v1",
                f"{head}_model_as_of_date": "20260819",
                f"{head}_model_artifact_sha256": str(index) * 64,
                f"{head}_validation_gate_pass_count": pass_count,
                f"{head}_validation_gate_total_count": total_count,
                f"{head}_validation_gate_score_pct": score,
            }
        )
    return row


def _plan(count: int = 3) -> dict[str, object]:
    return {
        "generated_at_utc": "2026-08-20T13:20:00Z",
        "signal_date": SIGNAL_DATE,
        "exec_date": EXEC_DATE,
        "exit_date": EXIT_DATE,
        "stage_watchlist": [
            _ready_row(f"60000{rank}.SH", rank, count)
            for rank in range(1, count + 1)
        ],
        "candidates": [],
        "model": {},
    }


def _payload(contract: dict[str, object]) -> dict[str, object]:
    return {
        "signal_date": contract["signal_date"],
        "exec_date": contract["exec_date"],
        "exit_date": contract["exit_date"],
        "three_rank": contract,
    }


def _materialized(
    output_root: Path,
    plan: dict[str, object] | None = None,
) -> tuple[dict[str, object], Path, Path]:
    json_path, csv_path, contract = materialize_three_rank_artifacts(
        output_root,
        build_three_rank_contract(plan or _plan()),
    )
    return _payload(contract), json_path, csv_path


def test_pages_truth_accepts_bound_shadow_top2_snapshot_and_csv(
    tmp_path: Path,
) -> None:
    payload, _json_path, csv_path = _materialized(tmp_path)
    contract = payload["three_rank"]

    _validate_three_rank_contract_payload(payload, label="test")
    _validate_three_rank_downloads(
        payload=payload,
        site_root=tmp_path,
        label="test",
    )

    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    assert tuple(reader.fieldnames or ()) == THREE_RANK_CSV_FIELDS
    assert [row["p_fill_shadow_rank"] for row in rows] == ["1", "2", "3"]
    assert {row["p_fill_shadow_snapshot_sha256"] for row in rows} == {
        contract["shadow_contract"]["shadow_snapshot_sha256"]
    }
    assert [
        row["ts_code"] for row in contract["shadow_top2"]["rows"]
    ] == ["600001.SH", "600002.SH"]


def test_pages_truth_rejects_valid_looking_shadow_row_tamper(
    tmp_path: Path,
) -> None:
    payload, _json_path, _csv_path = _materialized(tmp_path)
    tampered = copy.deepcopy(payload)
    # Rank 3 is outside Top2.  The dedicated snapshot hash must still bind it.
    tampered["three_rank"]["rows"][2]["p_fill_shadow_probability"] = 0.123

    with pytest.raises(DecisionPagesTruthError, match="shadow snapshot hash"):
        _validate_three_rank_contract_payload(tampered, label="test")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("p_fill_shadow_rank", 2, "shadow ranks"),
        ("p_fill_shadow_probability", 1.01, "outside \\[0,1\\]"),
        ("p_fill_shadow_status", "SHADOW_NOT_READY_TEST", "row statuses"),
    ),
)
def test_pages_truth_strictly_rejects_invalid_ready_shadow_rows(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload, _json_path, _csv_path = _materialized(tmp_path)
    tampered = copy.deepcopy(payload)
    tampered["three_rank"]["rows"][0][field] = value

    with pytest.raises(DecisionPagesTruthError, match=message):
        _validate_three_rank_contract_payload(tampered, label="test")


def test_pages_truth_requires_exact_shadow_top2(tmp_path: Path) -> None:
    payload, _json_path, _csv_path = _materialized(tmp_path)
    tampered = copy.deepcopy(payload)
    tampered["three_rank"]["shadow_top2"]["actual_slots"] = 1

    with pytest.raises(DecisionPagesTruthError, match="shadow Top2"):
        _validate_three_rank_contract_payload(tampered, label="test")


def test_pages_truth_requires_independent_shadow_snapshot_hash(
    tmp_path: Path,
) -> None:
    payload, _json_path, _csv_path = _materialized(tmp_path)
    tampered = copy.deepcopy(payload)
    tampered["three_rank"]["shadow_contract"][
        "shadow_snapshot_sha256"
    ] = "0" * 64

    with pytest.raises(DecisionPagesTruthError, match="shadow snapshot hash"):
        _validate_three_rank_contract_payload(tampered, label="test")


def test_pages_truth_unready_shadow_has_no_rank_probability_or_top2(
    tmp_path: Path,
) -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["p_fill_shadow_status"] = "SHADOW_NOT_READY_VALIDATION_GATE"
    payload, _json_path, csv_path = _materialized(tmp_path, plan)
    contract = payload["three_rank"]

    assert contract["shadow_top2"]["rows"] == []
    assert all(
        row["p_fill_shadow_rank"] is None
        and row["p_fill_shadow_probability"] is None
        for row in contract["rows"]
    )
    _validate_three_rank_contract_payload(payload, label="test")
    _validate_three_rank_downloads(
        payload=payload,
        site_root=tmp_path,
        label="test",
    )
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert all(
        row["p_fill_shadow_rank"] == ""
        and row["p_fill_shadow_probability"] == ""
        for row in csv_rows
    )

    tampered = copy.deepcopy(payload)
    tampered["three_rank"]["rows"][0]["p_fill_shadow_rank"] = 1
    with pytest.raises(DecisionPagesTruthError, match="while not ready"):
        _validate_three_rank_contract_payload(tampered, label="test")


def test_pages_truth_empty_unfrozen_set_cannot_claim_shadow_ready(
    tmp_path: Path,
) -> None:
    plan = _plan()
    for row in plan["stage_watchlist"]:
        row["promotion_model_status"] = "NOT_READY_RUNTIME_FEATURES"
        row["top10_selected"] = 0
    payload, _json_path, _csv_path = _materialized(tmp_path, plan)

    assert payload["three_rank"]["rows"] == []
    assert payload["three_rank"]["shadow_contract"]["model_status"] == (
        "SHADOW_NOT_READY_NO_FROZEN_TOP10"
    )
    _validate_three_rank_contract_payload(payload, label="test")

    tampered = copy.deepcopy(payload)
    tampered["three_rank"]["shadow_contract"]["model_status"] = "SHADOW_READY"
    with pytest.raises(DecisionPagesTruthError, match="without a frozen Top10"):
        _validate_three_rank_contract_payload(tampered, label="test")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("p_fill_shadow_rank", "9"),
        ("p_fill_shadow_snapshot_sha256", "0" * 64),
    ),
)
def test_pages_truth_csv_binds_shadow_rank_and_snapshot_hash(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    payload, json_path, csv_path = _materialized(tmp_path)
    contract = payload["three_rank"]
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0][field] = value

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=list(THREE_RANK_CSV_FIELDS),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")
    csv_path.write_bytes(csv_bytes)
    contract["downloads"]["csv_sha256"] = hashlib.sha256(csv_bytes).hexdigest()
    json_path.write_text(
        json.dumps(contract, ensure_ascii=False, allow_nan=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(DecisionPagesTruthError, match="CSV row 1 differs"):
        _validate_three_rank_downloads(
            payload=payload,
            site_root=tmp_path,
            label="test",
        )


def test_shadow_changes_snapshot_but_not_pages_core_bundle(tmp_path: Path) -> None:
    first_payload, _json_path, _csv_path = _materialized(tmp_path / "first")
    second_plan = _plan()
    rotated_ranks = (3, 1, 2)
    for row, shadow_rank in zip(
        second_plan["stage_watchlist"], rotated_ranks
    ):
        row["p_fill_shadow_rank"] = shadow_rank
        row["p_fill_shadow_probability"] = 0.70 + shadow_rank * 0.05
    second_payload, _json_path, _csv_path = _materialized(
        tmp_path / "second",
        second_plan,
    )

    _validate_three_rank_contract_payload(first_payload, label="first")
    _validate_three_rank_contract_payload(second_payload, label="second")
    first = first_payload["three_rank"]
    second = second_payload["three_rank"]
    assert first["bundle_sha256"] == second["bundle_sha256"]
    assert first["shadow_contract"]["shadow_snapshot_sha256"] != second[
        "shadow_contract"
    ]["shadow_snapshot_sha256"]
