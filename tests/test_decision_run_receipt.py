from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.decision.run_receipt import (  # noqa: E402
    DecisionRunReceiptError,
    validate_decision_run_receipt,
    write_decision_run_receipt,
)


def _write_bound_outputs(root: Path) -> tuple[str, str]:
    output = root / "outputs" / "decision"
    output.mkdir(parents=True)
    report_path = output / "decision_report_20260824.md"
    report_path.write_text(
        "# Decision Report (20260824)\n\n"
        "- signal_date: **20260821**\n"
        "- exec_date: **20260824**\n"
        "- exit_date: **20260825**\n",
        encoding="utf-8",
    )
    eval_path = output / "eval_20260824.json"
    eval_path.write_text(
        json.dumps(
            {
                "signal_date": "20260821",
                "exec_date": "20260824",
                "exit_date": "20260825",
                "requested_trade_date": "20260821",
                "stop_trading": False,
            }
        ),
        encoding="utf-8",
    )
    return (
        "outputs/decision/decision_report_20260824.md",
        "outputs/decision/eval_20260824.json",
    )


def test_receipt_binds_requested_signal_and_exact_generated_outputs(
    tmp_path: Path,
) -> None:
    report_path, eval_path = _write_bound_outputs(tmp_path)
    receipt_path = tmp_path / "runner" / "receipt.json"
    written = write_decision_run_receipt(
        requested_trade_date="20260821",
        signal_date="20260821",
        exec_date="20260824",
        exit_date="20260825",
        report_path=report_path,
        eval_path=eval_path,
        stop_trading=False,
        receipt_path=str(receipt_path),
    )

    assert written == receipt_path
    receipt = validate_decision_run_receipt(
        root=tmp_path,
        receipt_path=receipt_path,
        requested_trade_date="20260821",
    )
    assert receipt["exec_date"] == "20260824"
    assert receipt["eval_path"] == eval_path


def test_receipt_does_not_allow_a_newer_unrelated_eval_to_select_the_date(
    tmp_path: Path,
) -> None:
    report_path, eval_path = _write_bound_outputs(tmp_path)
    unrelated = tmp_path / "outputs" / "decision" / "eval_20260825.json"
    unrelated.write_text(
        json.dumps({"signal_date": "20260824", "exec_date": "20260825"}),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "receipt.json"
    write_decision_run_receipt(
        requested_trade_date="20260821",
        signal_date="20260821",
        exec_date="20260824",
        exit_date="20260825",
        report_path=report_path,
        eval_path=eval_path,
        stop_trading=False,
        receipt_path=str(receipt_path),
    )

    receipt = validate_decision_run_receipt(
        root=tmp_path,
        receipt_path=receipt_path,
        requested_trade_date="20260821",
    )
    assert receipt["exec_date"] == "20260824"


def test_receipt_rejects_requested_or_report_eval_date_mismatch(
    tmp_path: Path,
) -> None:
    report_path, eval_path = _write_bound_outputs(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    write_decision_run_receipt(
        requested_trade_date="20260821",
        signal_date="20260821",
        exec_date="20260824",
        exit_date="20260825",
        report_path=report_path,
        eval_path=eval_path,
        stop_trading=False,
        receipt_path=str(receipt_path),
    )
    with pytest.raises(DecisionRunReceiptError, match="requested TRADE_DATE"):
        validate_decision_run_receipt(
            root=tmp_path,
            receipt_path=receipt_path,
            requested_trade_date="20260820",
        )

    evaluation = json.loads((tmp_path / eval_path).read_text(encoding="utf-8"))
    evaluation["signal_date"] = "20260820"
    (tmp_path / eval_path).write_text(json.dumps(evaluation), encoding="utf-8")
    with pytest.raises(DecisionRunReceiptError, match="evaluation signal_date"):
        validate_decision_run_receipt(
            root=tmp_path,
            receipt_path=receipt_path,
            requested_trade_date="20260821",
        )


def test_daily_workflow_uses_receipt_instead_of_eval_filename_sorting() -> None:
    workflow = (ROOT / ".github/workflows/run_decision_daily.yml").read_text(
        encoding="utf-8"
    )
    assert "DECISION_RUN_RECEIPT_PATH" in workflow
    assert "validate_decision_run_receipt" in workflow
    assert "sorted(Path('outputs/decision').glob('eval_20??????.json')" not in workflow
    runner = (ROOT / "scripts/run_v2.py").read_text(encoding="utf-8")
    assert runner.count("write_decision_run_receipt(") == 2
