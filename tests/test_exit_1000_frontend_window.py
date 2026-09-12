"""New exit-policy display uses bound evidence; old summaries remain readable."""
from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

from scripts import build_compact_statistics_window as window
from scripts import settle_primary_observations as observations

ROOT = Path(__file__).resolve().parents[1]


def helper(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = helper("test_compact_statistics_window")
frontend = helper("test_primary_observation_frontend")
row_frontend = helper("test_three_rank_truth_frontend")
empty_root = base.empty_root
promotion_fixture = base.promotion_fixture


@pytest.fixture
def versioned_promotion(promotion_fixture):
    root, _ = promotion_fixture
    path = window.settlement.EXIT_POLICY_PATH_1000.as_posix()
    base.put(root, path, (ROOT / path).read_bytes())
    payload, rows = observations.build(root, "20260911")
    payload.update(generated_at_utc="2026-09-12T00:00:00Z", rows_path=window.PRIMARY_ROWS)
    payload["rows_sha256"] = base.put(root, window.PRIMARY_ROWS, observations.csv_bytes(rows))
    base.put(root, window.PRIMARY_SUMMARY, payload)
    return root, payload


def test_versioned_pending_rows_keep_promotion_hits_and_bind_policy(versioned_promotion):
    root, payload = versioned_promotion
    sources = window.Sources(root)
    section = window.promotion_section(sources, payload["latest_signal_date"], window.settlement._strict_open_dates(root), "20260911")
    assert section["status"] == "READY"
    assert all(row["verified"] == 1 for row in section["ranks"])
    assert payload["schema_version"] == "dc20_primary_observation_summary_v2"
    assert window.settlement.EXIT_POLICY_PATH_1000.as_posix() in sources.checked


@pytest.mark.parametrize("field,value", [
    ("exit_policy_id", "LEGACY_T1_OPEN"),
    ("actual_exit_time", "2026-09-14T10:00:00+08:00"),
    ("actual_exit_price", "10.99"),
])
def test_rehashed_csv_cannot_fabricate_new_policy_or_pending_exit(versioned_promotion, field, value):
    root, payload = versioned_promotion
    rows = list(csv.DictReader(io.StringIO((root / window.PRIMARY_ROWS).read_text())))
    rows[0][field] = value
    payload["rows_sha256"] = base.put(root, window.PRIMARY_ROWS, observations.csv_bytes(rows))
    base.put(root, window.PRIMARY_SUMMARY, payload)
    with pytest.raises(window.WindowSourceError, match="exit policy|exit price"):
        window.promotion_section(window.Sources(root), payload["latest_signal_date"], window.settlement._strict_open_dates(root), "20260911")


def test_missing_policy_manifest_binding_is_rejected(versioned_promotion):
    root, payload = versioned_promotion
    payload["source_files"] = [item for item in payload["source_files"] if item["path"] != window.settlement.EXIT_POLICY_PATH_1000.as_posix()]
    base.put(root, window.PRIMARY_SUMMARY, payload)
    with pytest.raises(window.WindowSourceError, match="policy missing"):
        window.promotion_section(window.Sources(root), payload["latest_signal_date"], window.settlement._strict_open_dates(root), "20260911")


@pytest.mark.skipif(frontend.NODE is None, reason="Node required")
def test_browser_accepts_new_pending_summary_but_rejects_policy_tamper(versioned_promotion):
    _, payload = versioned_promotion
    assert frontend._run("console.log(JSON.stringify(!!validatedPublicObservationStatistics(payload)))", payload)
    for mutation in ("payload.policy.current_exit_policy_id='LEGACY_T1_OPEN';",
                     "payload.policy.exit_policy_config.sha256='bad';"):
        assert not frontend._run(mutation + "console.log(JSON.stringify(!!validatedPublicObservationStatistics(payload)))", payload)


@pytest.mark.skipif(row_frontend.NODE is None, reason="Node required")
def test_browser_keeps_sealed_holding_pending_and_never_zero():
    body = "const row={ts_code:contract.rows[0].ts_code,validation_status:'PENDING_EXIT_PROXY',exit_policy_id:'dc20_exit_1000_limit_hold_20260912_v1',actual_net_return:null};console.log(JSON.stringify(threeRankRowTruth(contract,row.ts_code,null,{status:'READY',rows:[row]})));"
    result = row_frontend.run(body)
    assert result["actual_net_return"] is None
    assert result["validation_status_label"] == "封板延持"


def test_pending_capital_display_does_not_claim_compounded_nav():
    html = (ROOT / "decision.html").read_text()
    assert 'c.return_policy_status === "OPEN_OR_DELAYED_POSITIONS_NOT_CAPITAL_NAV"' in html
