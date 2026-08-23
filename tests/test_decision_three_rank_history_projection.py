from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import io
import json
import shutil
from pathlib import Path

import pytest

from scripts.build_decision_three_rank_history import (
    AVAILABLE_STATUS,
    OFFICIAL_PROMOTION_STATUS,
    UNAVAILABLE_STATUS,
    UNRELEASED_STATUS,
    HistoryProjectionError,
    _top10_members_sha256,
    build_history_archive,
)
from top10decision.decision.model_freeze import (
    REQUIRED_ACTIVE_PIN_PATHS,
    THREE_RANK_BEHAVIOR_PIN_PATHS,
    THREE_RANK_HISTORY_SOURCE_PIN_PATHS,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_decision_three_rank_history.py"
SOURCES_MANIFEST = ROOT / "models" / "decision_three_rank_history_sources.json"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy_dc20_pages.yml"
DASHBOARD = ROOT / "decision.html"


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("three-rank-history")
    build_history_archive(ROOT, output)
    return output


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_projection_sources(destination: Path) -> Path:
    source = destination / "source"
    for relative in (
        "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz",
        "models/decision_three_engines/validation_latest.json",
        "models/decision_three_rank_history_sources.json",
        "data/market/trade_cal_sse.csv",
    ):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    reports = source / "outputs" / "decision"
    reports.mkdir(parents=True, exist_ok=True)
    manifest = _load(source / "models/decision_three_rank_history_sources.json")
    for entry in manifest["entries"]:
        for kind in ("report", "evaluation"):
            relative = entry[kind]["path"]
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
    return source


def _rewrite_oof(source: Path, mutate) -> None:
    oof = source / "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz"
    with gzip.open(oof, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    mutate(rows)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    with oof.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_handle, mtime=0
        ) as handle:
            handle.write(buffer.getvalue().encode("utf-8"))
    validation_path = source / "models/decision_three_engines/validation_latest.json"
    validation = _load(validation_path)
    raw = oof.read_bytes()
    validation["artifacts"]["oof_top10"]["bytes"] = len(raw)
    validation["artifacts"]["oof_top10"]["sha256"] = hashlib.sha256(raw).hexdigest()
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _rewrite_calendar(source: Path, mutate) -> None:
    calendar = source / "data/market/trade_cal_sse.csv"
    with calendar.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    mutate(rows)
    with calendar.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_generator_is_pure_standard_library_and_has_no_model_runtime_import() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert not imported.intersection(
        {"numpy", "pandas", "scipy", "sklearn", "joblib", "top10decision"}
    )
    text = SCRIPT.read_text(encoding="utf-8")
    assert "final fitted model" in text
    assert "final_model_historical_scoring_used" in text
    assert ".glob(" not in text


def test_authoritative_source_manifest_is_canonical_and_binds_121_pairs() -> None:
    manifest = _load(SOURCES_MANIFEST)
    entries = manifest["entries"]
    canonical = json.dumps(
        entries,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert manifest["schema_version"] == "dc20_three_rank_history_sources_v1"
    assert manifest["inventory_kind"] == "immutable_exact_report_eval_pairs"
    assert manifest["calendar_source"] == "tushare:trade_cal:SSE"
    assert manifest["strict_calendar"] is True
    assert manifest["exchange"] == "SSE"
    assert manifest["report_eval_pairs"] == len(entries) == 121
    assert hashlib.sha256(canonical).hexdigest() == manifest["canonical_inventory_sha256"]
    assert [entry["report_date"] for entry in entries] == sorted(
        entry["report_date"] for entry in entries
    )
    for entry in entries:
        for kind in ("report", "evaluation"):
            binding = entry[kind]
            raw = (ROOT / binding["path"]).read_bytes()
            assert len(raw) == binding["bytes"]
            assert hashlib.sha256(raw).hexdigest() == binding["sha256"]


def test_history_generator_and_contract_test_are_frozen_behavior() -> None:
    expected = {
        "scripts/build_decision_three_rank_history.py",
        "tests/test_decision_three_rank_history_projection.py",
    }
    assert expected <= THREE_RANK_BEHAVIOR_PIN_PATHS
    assert expected <= REQUIRED_ACTIVE_PIN_PATHS
    assert THREE_RANK_HISTORY_SOURCE_PIN_PATHS == {
        "data/market/trade_cal_sse.csv",
        "models/decision_three_rank_history_sources.json",
    }
    assert THREE_RANK_HISTORY_SOURCE_PIN_PATHS <= REQUIRED_ACTIVE_PIN_PATHS


def test_complete_oof_archive_and_121_report_map_have_exact_coverage(
    archive: Path,
) -> None:
    statistics = _load(archive / "statistics.json")
    assert statistics["calendar_source"] == "tushare:trade_cal:SSE"
    assert statistics["strict_calendar"] is True
    assert statistics["exchange"] == "SSE"
    coverage = statistics["coverage"]
    assert coverage == {
        "archive_rows": 6753,
        "archive_unique_signal_dates": 910,
        "archived_noncanonical_report_mappings": 2,
        "canonical_report_mappings": 114,
        "canonical_report_rows": 910,
        "canonical_report_unique_signal_dates": 114,
        "duplicate_signal_dates": ["20260430"],
        "nontrading_source_report_dates": ["20260406", "20260501"],
        "oof_corresponding_report_mappings": 116,
        "oof_cutoff_signal_date": "20260814",
        "report_eval_pairs": 121,
        "unavailable_report_mappings": 5,
        "unavailable_signal_dates": [
            "20260817",
            "20260818",
            "20260819",
            "20260820",
            "20260821",
        ],
    }
    assert statistics["research_diagnostic_coverage"] == {
        "big_loss": {"rows": 5677, "signal_dates": 710},
        "profit": {"rows": 5677, "signal_dates": 710},
    }
    assert [
        (shard["year"], shard["signal_dates"], shard["rows"])
        for shard in statistics["shards"]
    ] == [
        ("2022", 36, 279),
        ("2023", 240, 1315),
        ("2024", 242, 1808),
        ("2025", 243, 2142),
        ("2026", 149, 1209),
    ]


def test_unreleased_heads_are_null_officially_and_only_live_in_diagnostics(
    archive: Path,
) -> None:
    records = _load(archive / "three_rank_history_2026.json")["records"]
    record = next(item for item in records if item["signal_date"] == "20260430")
    assert record["exec_date"] == "20260506"
    assert record["exit_date"] == "20260507"
    assert record["status"] == AVAILABLE_STATUS
    assert record["actual_execution_claimed"] is False
    assert record["models"] == {
        "promotion": {
            "official_historical_fields_populated": True,
            "status": OFFICIAL_PROMOTION_STATUS,
        },
        "big_loss": {
            "diagnostics_only": True,
            "official_historical_fields_populated": False,
            "status": UNRELEASED_STATUS,
        },
        "profit": {
            "diagnostics_only": True,
            "official_historical_fields_populated": False,
            "status": UNRELEASED_STATUS,
        },
    }
    assert record["source_report_dates"] == ["20260501", "20260506"]
    for row in record["rows"]:
        assert row["promotion_rank"] is not None
        assert row["predicted_promotion_probability"] is not None
        assert row["big_loss_safety_rank"] is None
        assert row["predicted_big_loss_probability"] is None
        assert row["profit_rank"] is None
        assert row["predicted_profit_probability"] is None
        assert row["research_diagnostics"]["big_loss"]["rank"] is not None
        assert row["research_diagnostics"]["profit"]["rank"] is not None


def test_every_archive_date_is_time_honest_member_bound_and_contiguous(
    archive: Path,
) -> None:
    signal_dates: list[str] = []
    for year in range(2022, 2027):
        payload = _load(archive / f"three_rank_history_{year}.json")
        assert payload["research_only"] is True
        assert payload["actual_execution_claimed"] is False
        for record in payload["records"]:
            signal_date = record["signal_date"]
            signal_dates.append(signal_date)
            assert signal_date < record["exec_date"] < record["exit_date"]
            rows = record["rows"]
            assert [row["promotion_rank"] for row in rows] == list(
                range(1, len(rows) + 1)
            )
            assert record["top10_members_sha256"] == _top10_members_sha256(
                signal_date, [row["ts_code"] for row in rows]
            )
            for row in rows:
                assert row["promotion_oof"]["train_end"] < signal_date
                for head in ("big_loss", "profit"):
                    diagnostic = row["research_diagnostics"][head]
                    if diagnostic["rank"] is not None:
                        assert diagnostic["train_end"] < signal_date
    assert len(signal_dates) == len(set(signal_dates)) == 910
    assert min(signal_dates) == "20221111"
    assert max(signal_dates) == "20260814"


def test_report_map_preserves_duplicate_and_nontrading_source_truth(
    archive: Path,
) -> None:
    report_map = _load(archive / "report_map.json")
    assert report_map["mapping_kind"] == "exact_report_to_signal_date_no_latest_fallback"
    assert report_map["data_alias"] is False
    rows = report_map["reports"]
    assert len(rows) == 121
    assert [
        row["report_date"]
        for row in rows
        if row["status"] == "ARCHIVED_NONCANONICAL_SOURCE_EXEC"
    ] == ["20260406", "20260501"]
    duplicate = [row for row in rows if row["signal_date"] == "20260430"]
    assert [row["report_date"] for row in duplicate] == ["20260501", "20260506"]
    assert duplicate[0]["source_exec_is_open_session"] is False
    assert duplicate[0]["source_exec_matches_oof_t"] is False
    assert duplicate[0]["status"] == "ARCHIVED_NONCANONICAL_SOURCE_EXEC"
    assert duplicate[1]["source_exec_is_open_session"] is True
    assert duplicate[1]["source_exec_matches_oof_t"] is True
    assert all(row["canonical_oof_exec_date"] == "20260506" for row in duplicate)
    assert all(row["date_bundle_sha256"] for row in duplicate)
    for binding in duplicate:
        for source in ("report", "evaluation"):
            assert len(binding[source]["sha256"]) == 64

    unavailable = [row for row in rows if row["status"] == UNAVAILABLE_STATUS]
    assert [row["signal_date"] for row in unavailable] == [
        "20260817",
        "20260818",
        "20260819",
        "20260820",
        "20260821",
    ]
    assert all(row["date_bundle_sha256"] is None for row in unavailable)
    assert all(row["canonical_oof_exec_date"] is None for row in unavailable)
    assert all(row["source_exec_matches_oof_t"] is None for row in unavailable)
    assert all(row["actual_execution_claimed"] is False for row in unavailable)


def test_index_hash_binds_every_pages_download_and_has_no_latest_alias(
    archive: Path,
) -> None:
    index = _load(archive / "index.json")
    assert index["index_kind"] == "dated_annual_shards_pointer_only"
    assert index["data_alias"] is False
    assert index["latest_fallback_allowed"] is False
    assert index["actual_execution_claimed"] is False
    root_prefix = "outputs/decision/three_rank_history/"
    bindings = [
        (index["statistics_url"], index["statistics_sha256"]),
        (index["report_map_url"], index["report_map_sha256"]),
    ]
    for shard in index["shards"]:
        assert shard["csv_url"].endswith(".csv.gz")
        assert shard["csv_content_encoding"] == "gzip_mtime_0"
        bindings.extend(
            [
                (shard["json_url"], shard["json_sha256"]),
                (shard["csv_url"], shard["csv_sha256"]),
                (shard["evidence_url"], shard["evidence_sha256"]),
            ]
        )
    for url, expected_sha256 in bindings:
        assert url.startswith(root_prefix)
        assert "latest" not in url.lower()
        path = archive / url.removeprefix(root_prefix)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


def test_annual_csv_is_deterministic_gzip_and_keeps_bc_official_fields_empty(
    archive: Path,
) -> None:
    index = _load(archive / "index.json")
    for shard in index["shards"]:
        name = shard["csv_url"].rsplit("/", 1)[-1]
        compressed = (archive / name).read_bytes()
        # RFC 1952 MTIME bytes are all zero; the source filename is omitted.
        assert compressed[:3] == b"\x1f\x8b\x08"
        assert compressed[4:8] == b"\x00\x00\x00\x00"
        raw = gzip.decompress(compressed)
        assert hashlib.sha256(raw).hexdigest() == shard["csv_uncompressed_sha256"]
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
        assert len(rows) == shard["rows"]
        assert all(row["big_loss_safety_rank"] == "" for row in rows)
        assert all(row["predicted_big_loss_probability"] == "" for row in rows)
        assert all(row["profit_rank"] == "" for row in rows)
        assert all(row["predicted_profit_probability"] == "" for row in rows)
        assert all(row["actual_execution_claimed"] == "false" for row in rows)


def test_projection_bytes_are_reproducible(archive: Path, tmp_path: Path) -> None:
    second = tmp_path / "second"
    build_history_archive(ROOT, second)
    first_files = sorted(path.name for path in archive.iterdir() if path.is_file())
    second_files = sorted(path.name for path in second.iterdir() if path.is_file())
    assert first_files == second_files
    for name in first_files:
        assert (archive / name).read_bytes() == (second / name).read_bytes()


def test_source_oof_sha_drift_fails_before_projection(tmp_path: Path) -> None:
    source = _copy_projection_sources(tmp_path)
    oof = source / "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz"
    oof.write_bytes(oof.read_bytes() + b"drift")
    with pytest.raises(HistoryProjectionError, match="OOF SHA256"):
        build_history_archive(source, tmp_path / "site")


def test_source_manifest_prevents_live_report_inventory_resealing(tmp_path: Path) -> None:
    source = _copy_projection_sources(tmp_path)
    report = source / "outputs/decision/decision_report_20260302.md"
    report.write_bytes(report.read_bytes() + b"\nsource drift\n")
    with pytest.raises(HistoryProjectionError, match="manifest report bytes drifted"):
        build_history_archive(source, tmp_path / "site")


def test_skipped_trading_session_in_oof_is_rejected(tmp_path: Path) -> None:
    source = _copy_projection_sources(tmp_path)

    def mutate(rows: list[dict[str, str]]) -> None:
        for row in rows:
            if row["signal_date"] == "20221111":
                row["buy_date"] = "20221115"
                row["target_exit_date"] = "20221116"

    _rewrite_oof(source, mutate)
    with pytest.raises(HistoryProjectionError, match="not adjacent trading sessions"):
        build_history_archive(source, tmp_path / "site")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("exchange", "exchange is not exactly SSE"),
        ("missing_day", "natural dates are not complete and consecutive"),
        ("pretrade", "pretrade_date chain is invalid"),
    ),
)
def test_strict_sse_calendar_rejects_exchange_gap_and_chain_tampering(
    tmp_path: Path, mutation: str, message: str
) -> None:
    source = _copy_projection_sources(tmp_path)

    def mutate(rows: list[dict[str, str]]) -> None:
        if mutation == "exchange":
            rows[10]["exchange"] = "SZSE"
        elif mutation == "missing_day":
            del rows[10]
        else:
            rows[10]["pretrade_date"] = "20200101"

    _rewrite_calendar(source, mutate)
    with pytest.raises(HistoryProjectionError, match=message):
        build_history_archive(source, tmp_path / "site")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("probability", "probability escaped \\[0,1\\]"),
        ("partial_metadata", "partially populated promotion OOF metadata"),
        ("rank_probability", "probabilities are not monotonic by rank"),
    ),
)
def test_oof_probability_metadata_and_rank_contracts_fail_closed(
    tmp_path: Path, mutation: str, message: str
) -> None:
    source = _copy_projection_sources(tmp_path)

    def mutate(rows: list[dict[str, str]]) -> None:
        group = [row for row in rows if row["signal_date"] == "20221111"]
        if mutation == "probability":
            group[0]["predicted_promotion_probability"] = "1.01"
        elif mutation == "partial_metadata":
            group[0]["promotion_oof_calibration"] = ""
        else:
            by_rank = sorted(group, key=lambda row: int(row["promotion_rank"]))
            by_rank[1]["predicted_promotion_probability"] = "0.999"

    _rewrite_oof(source, mutate)
    with pytest.raises(HistoryProjectionError, match=message):
        build_history_archive(source, tmp_path / "site")


def test_nonempty_output_root_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "site"
    output.mkdir()
    (output / "stale.json").write_text("{}", encoding="utf-8")
    with pytest.raises(HistoryProjectionError, match="output root"):
        build_history_archive(ROOT, output)


def test_pages_workflow_builds_and_publicly_hash_verifies_history() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/build_decision_three_rank_history.py" in text
    assert "_site/outputs/decision/three_rank_history" in text
    assert "public_history_index_bytes" in text
    assert "history public SHA256 mismatch" in text
    assert 'history_statistics["official_model_status"]["big_loss"]' in text
    assert 'history_statistics["official_model_status"]["profit"]' in text
    assert 'history_statistics.get("calendar_source")' in text
    assert 'history_statistics.get("strict_calendar") is not True' in text
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in text
    assert '--only-binary=:all: --require-hashes -r requirements.lock' in text
    assert "python scripts/validate_decision_model_freeze.py" in text
    push_header = text.split("schedule:", 1)[0]
    assert "requirements.lock" in push_header
    assert "models/decision_three_rank_history_sources.json" in push_header


def test_dashboard_only_exposes_history_coverage_and_downloads_as_research() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    for token in (
        "历史三排名研究归档",
        "历史覆盖未验证",
        "B/C仅研究诊断，未发布为正式排名",
        "loadThreeRankHistorySummary",
        "statisticsBytes",
        "await sha256Hex(statisticsBytes) !== index.statistics_sha256",
        "年度下载已隐藏",
        "${year} JSON",
        "${year} CSV.gz",
        "tushare:trade_cal:SSE",
        "strict_calendar=true",
    ):
        assert token in text
    assert "历史覆盖 910 个D日 · 6753 行" not in text
