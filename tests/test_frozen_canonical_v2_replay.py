from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "replay_frozen_canonical_v2.py"
ROOT = SCRIPT.parents[1]
SPEC = importlib.util.spec_from_file_location("replay_frozen_canonical_v2", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)
from top10decision.decision.model_freeze import (  # noqa: E402
    REQUIRED_ACTIVE_PIN_PATHS,
)

LEGACY_V1_MANIFEST_FIXTURE = (
    Path(__file__).parent / "fixtures" / "decision_model_freeze_v1_46d8.json"
)


def _write_manifest(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "models" / "decision_model_freeze.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _legacy_v1_manifest_bytes() -> bytes:
    payload = LEGACY_V1_MANIFEST_FIXTURE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == replay.LEGACY_BOOTSTRAP_MANIFEST_SHA256
    assert json.loads(payload)["schema_version"] == "decision_model_freeze_v1"
    return payload


def _synthetic_history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "signal_date": ["20260804", "20260805"],
            "buy_date": ["20260805", "20260806"],
            "target_exit_date": ["20260806", "20260807"],
            "actual_exit_date": ["20260806", "20260807"],
            "ts_code": ["000001.SZ", "600000.SH"],
        }
    )


def _write_synthetic_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frame: pd.DataFrame,
    *,
    expected_columns: tuple[str, ...] | None = None,
) -> None:
    snapshot = tmp_path / replay.EXPECTED_SNAPSHOT_PATH
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        snapshot,
        index=False,
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "decision_model_freeze_v1",
        "active": False,
        "freeze_id": replay.EXPECTED_FREEZE_ID,
        "training_cutoff_signal_date": replay.EXPECTED_TRAINING_CUTOFF,
        "history_snapshot": {
            "path": replay.EXPECTED_SNAPSHOT_PATH,
            "sha256": snapshot_sha,
            "bootstrap_mode": False,
        },
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    _write_manifest(tmp_path, manifest_bytes)
    expected = expected_columns or tuple(frame.columns)
    monkeypatch.setattr(replay, "EXPECTED_SNAPSHOT_SHA256", snapshot_sha)
    monkeypatch.setattr(
        replay,
        "LEGACY_BOOTSTRAP_MANIFEST_SHA256",
        hashlib.sha256(manifest_bytes).hexdigest(),
    )
    monkeypatch.setattr(replay, "EXPECTED_HISTORY_ROWS", len(frame))
    monkeypatch.setattr(
        replay,
        "EXPECTED_HISTORY_DATES",
        frame["signal_date"].astype("string").nunique(),
    )
    monkeypatch.setattr(replay, "EXPECTED_HISTORY_COLUMNS", len(expected))
    monkeypatch.setattr(
        replay,
        "EXPECTED_HISTORY_COLUMNS_SHA256",
        replay.frame_columns_sha256(expected),
    )


def test_forced_replay_binds_engine_signal_and_publisher_report_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    history = pd.DataFrame({"signal_date": ["20260805"]})
    semantic_sha = "1" * 64
    snapshot_relative = (
        replay.REPLAY_MARKET_SNAPSHOT_ROOT / f"{semantic_sha}.json"
    ).as_posix()
    snapshot_path = tmp_path / snapshot_relative
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        replay,
        "load_forced_frozen_history",
        lambda _root: (
            history,
            {
                "pinned_files": {
                    snapshot_relative: "2" * 64,
                    replay.REPLAY_SSE_CALENDAR_PATH: "3" * 64,
                }
            },
            {"validated": True},
        ),
    )
    monkeypatch.setattr(
        replay,
        "AuctionV3Config",
        lambda *, root: SimpleNamespace(root=root),
    )
    market_snapshot = {
        "signal_date": "20260814",
        "candidate_snapshot": {"path": "data/pred/archive/pred_source_20260814.csv"},
        "market_dates": ["20260814", "20260817", "20260818"],
        "bound_dates": ["20260814", "20260817", "20260818"],
        "market_files": {},
        "missing_market_tables": [],
        "minute_binding": {"mode": "all_missing", "paths": []},
    }
    monkeypatch.setattr(
        replay,
        "_load_replay_market_snapshot",
        lambda _root, **_kwargs: (market_snapshot, {"validated": True}),
    )

    class FakeEngine:
        def __init__(
            self, config, loaded_history, history_audit, loaded_market_snapshot
        ) -> None:
            calls["engine_init"] = (
                config.root,
                loaded_history,
                history_audit,
                loaded_market_snapshot,
            )

        def run(self, signal_date: str, *, force_prediction: bool):
            calls["engine_run"] = (signal_date, force_prediction)
            return SimpleNamespace(signal_date=signal_date)

    def fake_subprocess_run(command, **kwargs):
        calls["publisher_command"] = command
        calls["publisher_kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"status":"pass"}', stderr="")

    monkeypatch.setattr(replay, "DiagnosticFrozenEngine", FakeEngine)
    monkeypatch.setattr(replay.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        replay,
        "asdict",
        lambda result: {"signal_date": result.signal_date},
    )

    result, audit, published = replay.run_forced_replay(
        tmp_path,
        signal_date="20260814",
        report_date="20260817",
        expected_action_semantic_sha256=semantic_sha,
    )
    assert result == {"signal_date": "20260814"}
    assert audit == {
        "validated": True,
        "market_input_snapshot": {
            "validated": True,
            "trust_source": "active_freeze_pin",
        },
    }
    assert published == {"status": "pass"}
    assert calls["engine_run"] == ("20260814", True)
    assert calls["publisher_command"][-2:] == ["--report-date", "20260817"]
    assert calls["publisher_kwargs"] == {
        "cwd": tmp_path,
        "check": False,
        "capture_output": True,
        "text": True,
    }


def _write_synthetic_market_snapshot(tmp_path: Path, semantic_sha: str) -> Path:
    signal_date, report_date, exit_date = "20260814", "20260817", "20260818"
    dates = [signal_date, report_date, exit_date]
    candidate_relative = f"data/pred/archive/pred_source_{signal_date}.csv"
    candidate_path = tmp_path / candidate_relative
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text("trade_date,ts_code\n20260814,000001.SZ\n", encoding="utf-8")
    auxiliary_relative = (
        "data/auction_v3/promotion_prior/five_year_daily_stage_board.csv"
    )
    auxiliary_path = tmp_path / auxiliary_relative
    auxiliary_path.parent.mkdir(parents=True, exist_ok=True)
    auxiliary_path.write_text("signal_date,ts_code\n", encoding="utf-8")
    calendar_path = tmp_path / replay.REPLAY_SSE_CALENDAR_PATH
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    calendar_path.write_text(
        "exchange,cal_date,is_open,pretrade_date\n"
        "SSE,20260814,1,20260813\n"
        "SSE,20260817,1,20260814\n"
        "SSE,20260818,1,20260817\n",
        encoding="utf-8",
    )
    files: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for date in dates:
        for table in sorted(replay.REPLAY_MARKET_TABLES):
            key = f"{date}:{table}"
            if table != "daily":
                missing.append(key)
                continue
            relative = f"data/market/raw/{date[:4]}/{date}/{table}.csv"
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"trade_date,ts_code,close\n{date},000001.SZ,10.00\n",
                encoding="utf-8",
            )
            files[key] = {
                "path": relative,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
    payload = {
        "schema_version": replay.REPLAY_MARKET_SNAPSHOT_SCHEMA,
        "bound_action_semantic_sha256": semantic_sha,
        "signal_date": signal_date,
        "report_date": report_date,
        "exit_date": exit_date,
        "creator_base_revision": "a" * 40,
        "creator_run_id": "1",
        "market_dates": dates,
        "bound_dates": dates,
        "candidate_snapshot": {
            "path": candidate_relative,
            "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        },
        "market_files": files,
        "missing_market_tables": sorted(missing),
        "auxiliary_files": {
            auxiliary_relative: hashlib.sha256(auxiliary_path.read_bytes()).hexdigest()
        },
        "minute_binding": {"mode": "all_missing", "paths": []},
    }
    payload["canonical_sha256"] = hashlib.sha256(
        replay.canonical_json_bytes(payload)
    ).hexdigest()
    path = (
        tmp_path
        / replay.REPLAY_MARKET_SNAPSHOT_ROOT
        / f"{semantic_sha}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def test_market_snapshot_hides_later_backfilled_pre_d_and_minute_partitions(
    tmp_path: Path,
) -> None:
    semantic_sha = "1" * 64
    snapshot_path = _write_synthetic_market_snapshot(tmp_path, semantic_sha)
    snapshot, audit = replay._load_replay_market_snapshot(
        tmp_path,
        expected_action_semantic_sha256=semantic_sha,
        expected_snapshot_file_sha256=hashlib.sha256(
            snapshot_path.read_bytes()
        ).hexdigest(),
        expected_calendar_file_sha256=hashlib.sha256(
            (tmp_path / replay.REPLAY_SSE_CALENDAR_PATH).read_bytes()
        ).hexdigest(),
        signal_date="20260814",
        report_date="20260817",
    )
    engine = replay.DiagnosticFrozenEngine(
        replay.AuctionV3Config(root=tmp_path),
        pd.DataFrame(),
        {},
        snapshot,
    )
    before_dates = engine.market_dates()
    before_candidate = engine.candidate_snapshots()

    backfilled = tmp_path / "data/market/raw/2026/20260812/daily.csv"
    backfilled.parent.mkdir(parents=True, exist_ok=True)
    backfilled.write_text(
        "trade_date,ts_code,close\n20260812,000001.SZ,9.50\n",
        encoding="utf-8",
    )
    minute = tmp_path / "data/market/minute_1m/2026/20260814/000001_SZ.csv"
    minute.parent.mkdir(parents=True, exist_ok=True)
    minute.write_text("time,open\n09:30,10.00\n", encoding="utf-8")

    assert engine.market_dates() == before_dates == [
        "20260814",
        "20260817",
        "20260818",
    ]
    assert engine.candidate_snapshots() == before_candidate
    assert engine.minute_table("20260814", "000001.SZ").empty
    assert audit["live_raw_inventory_scanned"] is False
    assert audit["live_minute_inventory_scanned"] is False
    with pytest.raises(RuntimeError, match="undeclared market input"):
        engine._market_path("20260812", "daily")


def _rewrite_snapshot_payload(path: Path, payload: dict[str, object]) -> None:
    payload.pop("canonical_sha256", None)
    payload["canonical_sha256"] = hashlib.sha256(
        replay.canonical_json_bytes(payload)
    ).hexdigest()
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_market_snapshot_rejects_non_sse_open_market_date(tmp_path: Path) -> None:
    semantic_sha = "8" * 64
    snapshot_path = _write_synthetic_market_snapshot(tmp_path, semantic_sha)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["market_dates"] = [
        "20260814",
        "20260815",
        "20260817",
        "20260818",
    ]
    payload["bound_dates"] = list(payload["market_dates"])
    _rewrite_snapshot_payload(snapshot_path, payload)

    with pytest.raises(RuntimeError, match="non-SSE-open market date"):
        replay._load_replay_market_snapshot(
            tmp_path,
            expected_action_semantic_sha256=semantic_sha,
            expected_snapshot_file_sha256=hashlib.sha256(
                snapshot_path.read_bytes()
            ).hexdigest(),
            expected_calendar_file_sha256=hashlib.sha256(
                (tmp_path / replay.REPLAY_SSE_CALENDAR_PATH).read_bytes()
            ).hexdigest(),
            signal_date="20260814",
            report_date="20260817",
        )


def test_market_snapshot_rejects_extra_bound_date(tmp_path: Path) -> None:
    semantic_sha = "9" * 64
    snapshot_path = _write_synthetic_market_snapshot(tmp_path, semantic_sha)
    calendar_path = tmp_path / replay.REPLAY_SSE_CALENDAR_PATH
    calendar_path.write_text(
        "exchange,cal_date,is_open,pretrade_date\n"
        "SSE,20260813,1,20260812\n"
        "SSE,20260814,1,20260813\n"
        "SSE,20260817,1,20260814\n"
        "SSE,20260818,1,20260817\n",
        encoding="utf-8",
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["bound_dates"] = [
        "20260813",
        "20260814",
        "20260817",
        "20260818",
    ]
    _rewrite_snapshot_payload(snapshot_path, payload)

    with pytest.raises(RuntimeError, match="exact market plus D/T/T\\+1 union"):
        replay._load_replay_market_snapshot(
            tmp_path,
            expected_action_semantic_sha256=semantic_sha,
            expected_snapshot_file_sha256=hashlib.sha256(
                snapshot_path.read_bytes()
            ).hexdigest(),
            expected_calendar_file_sha256=hashlib.sha256(
                calendar_path.read_bytes()
            ).hexdigest(),
            signal_date="20260814",
            report_date="20260817",
        )


def test_market_snapshot_fails_closed_on_bound_file_drift(tmp_path: Path) -> None:
    semantic_sha = "2" * 64
    snapshot_path = _write_synthetic_market_snapshot(tmp_path, semantic_sha)
    bound = tmp_path / "data/market/raw/2026/20260814/daily.csv"
    bound.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SHA drifted"):
        replay._load_replay_market_snapshot(
            tmp_path,
            expected_action_semantic_sha256=semantic_sha,
            expected_snapshot_file_sha256=hashlib.sha256(
                snapshot_path.read_bytes()
            ).hexdigest(),
            expected_calendar_file_sha256=hashlib.sha256(
                (tmp_path / replay.REPLAY_SSE_CALENDAR_PATH).read_bytes()
            ).hexdigest(),
            signal_date="20260814",
            report_date="20260817",
        )


def test_market_snapshot_fails_closed_when_sidecar_is_not_the_active_pin(
    tmp_path: Path,
) -> None:
    semantic_sha = "3" * 64
    _write_synthetic_market_snapshot(tmp_path, semantic_sha)
    with pytest.raises(RuntimeError, match="active pinned file"):
        replay._load_replay_market_snapshot(
            tmp_path,
            expected_action_semantic_sha256=semantic_sha,
            expected_snapshot_file_sha256="4" * 64,
            expected_calendar_file_sha256=hashlib.sha256(
                (tmp_path / replay.REPLAY_SSE_CALENDAR_PATH).read_bytes()
            ).hexdigest(),
            signal_date="20260814",
            report_date="20260817",
        )


def test_missing_snapshot_is_materialized_from_the_pre_sync_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic_sha = "5" * 64
    signal_date, report_date, exit_date = "20260814", "20260817", "20260818"
    calendar = tmp_path / replay.REPLAY_SSE_CALENDAR_PATH
    calendar.parent.mkdir(parents=True)
    calendar.write_text(
        "exchange,cal_date,is_open,pretrade_date\n"
        "SSE,20260814,1,20260813\n"
        "SSE,20260817,1,20260814\n"
        "SSE,20260818,1,20260817\n",
        encoding="utf-8",
    )
    candidate_relative = f"data/pred/archive/pred_source_{signal_date}.csv"
    candidate = tmp_path / candidate_relative
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        "trade_date,ts_code\n20260814,000001.SZ\n",
        encoding="utf-8",
    )
    auxiliary = tmp_path / replay.REPLAY_AUXILIARY_PATH
    auxiliary.parent.mkdir(parents=True)
    auxiliary.write_text("signal_date,ts_code\n", encoding="utf-8")
    daily_paths: dict[str, Path] = {}
    for trade_date in (signal_date, report_date, exit_date):
        target = (
            tmp_path
            / f"data/market/raw/{trade_date[:4]}/{trade_date}/daily.csv"
        )
        target.parent.mkdir(parents=True)
        target.write_text(
            f"trade_date,ts_code,close\n{trade_date},000001.SZ,10.0\n",
            encoding="utf-8",
        )
        daily_paths[trade_date] = target

    class FakeInventoryEngine:
        def __init__(self, _config: object) -> None:
            pass

        def market_dates(self) -> list[str]:
            return [signal_date, report_date, exit_date]

        def candidate_snapshots(self) -> dict[str, Path]:
            return {signal_date: candidate}

        def _market_path(self, trade_date: str, table: str) -> Path | None:
            return daily_paths[trade_date] if table == "daily" else None

    monkeypatch.setattr(replay, "AuctionV3Engine", FakeInventoryEngine)
    monkeypatch.setattr(replay, "_git_head_sha", lambda _root: "a" * 40)
    path, file_sha256 = replay._materialize_replay_market_snapshot(
        tmp_path,
        expected_action_semantic_sha256=semantic_sha,
        signal_date=signal_date,
        report_date=report_date,
        creator_run_id="123",
        expected_calendar_file_sha256=hashlib.sha256(
            calendar.read_bytes()
        ).hexdigest(),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert file_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert payload["bound_action_semantic_sha256"] == semantic_sha
    assert payload["exit_date"] == exit_date
    assert payload["market_dates"] == [signal_date, report_date, exit_date]
    assert payload["bound_dates"] == [signal_date, report_date, exit_date]
    assert payload["minute_binding"] == {"mode": "all_missing", "paths": []}
    assert len(payload["market_files"]) == 3
    assert len(payload["missing_market_tables"]) == 21


def test_current_action_snapshot_retry_reuses_exact_head_with_old_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic_sha = "7" * 64
    binding = {
        "action_semantic_sha256": semantic_sha,
        "signal_date": "20260814",
        "report_date": "20260817",
        "exit_date": "20260818",
    }
    snapshot_path = (
        tmp_path
        / replay.REPLAY_MARKET_SNAPSHOT_ROOT
        / f"{semantic_sha}.json"
    )
    current_base = ["a" * 40]

    monkeypatch.setattr(
        replay,
        "load_model_freeze",
        lambda _root, required=True: {
            "pinned_files": {replay.REPLAY_SSE_CALENDAR_PATH: "c" * 64}
        },
    )
    monkeypatch.setattr(
        replay,
        "validate_pinned_files",
        lambda *_args, **_kwargs: {"enforced": True},
    )
    monkeypatch.setattr(
        replay,
        "_persisted_action_snapshot_binding",
        lambda _root: dict(binding),
    )
    monkeypatch.setattr(replay, "_git_head_sha", lambda _root: current_base[0])

    def materialize(
        _root: Path,
        *,
        expected_action_semantic_sha256: str,
        signal_date: str,
        report_date: str,
        creator_run_id: str,
        expected_calendar_file_sha256: str,
    ) -> tuple[Path, str]:
        assert expected_action_semantic_sha256 == semantic_sha
        assert (signal_date, report_date) == ("20260814", "20260817")
        assert creator_run_id == "111"
        assert expected_calendar_file_sha256 == "c" * 64
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(
            json.dumps(
                {
                    "schema_version": replay.REPLAY_MARKET_SNAPSHOT_SCHEMA,
                    "bound_action_semantic_sha256": semantic_sha,
                    "creator_run_id": creator_run_id,
                    "creator_base_revision": current_base[0],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return snapshot_path, hashlib.sha256(snapshot_path.read_bytes()).hexdigest()

    def load_snapshot(
        _root: Path,
        **kwargs: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        assert kwargs["expected_snapshot_file_sha256"] == digest
        return payload, {
            "schema_version": replay.REPLAY_MARKET_SNAPSHOT_SCHEMA,
            "path": snapshot_path.relative_to(tmp_path).as_posix(),
            "file_sha256": digest,
            "bound_action_semantic_sha256": semantic_sha,
            "signal_date": "20260814",
            "report_date": "20260817",
            "exit_date": "20260818",
            "live_raw_inventory_scanned": False,
            "live_minute_inventory_scanned": False,
        }

    monkeypatch.setattr(replay, "_materialize_replay_market_snapshot", materialize)
    monkeypatch.setattr(replay, "_load_replay_market_snapshot", load_snapshot)
    monkeypatch.setattr(
        replay,
        "_tracked_snapshot_sha256",
        lambda _root, _relative: hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
    )

    first = replay.materialize_current_action_market_snapshot(
        tmp_path,
        creator_run_id="111",
    )
    assert first["created"] is True
    assert first["creator_run_id"] == "111"
    assert first["creator_base_revision"] == "a" * 40

    current_base[0] = "b" * 40
    second = replay.materialize_current_action_market_snapshot(
        tmp_path,
        creator_run_id="222",
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert second["created"] is False
    assert second["creator_run_id"] == "222"
    assert second["creator_base_revision"] == "b" * 40
    assert payload["creator_run_id"] == "111"
    assert payload["creator_base_revision"] == "a" * 40


def test_exact_minute_inventory_replays_only_bound_files(tmp_path: Path) -> None:
    semantic_sha = "6" * 64
    snapshot_path = _write_synthetic_market_snapshot(tmp_path, semantic_sha)
    minute_relative = (
        "data/market/minute_1m/2026/20260814/000001_SZ.csv"
    )
    minute_path = tmp_path / minute_relative
    minute_path.parent.mkdir(parents=True, exist_ok=True)
    minute_path.write_text(
        "time,open,close,high,low,vol\n"
        "09:30,10.0,10.1,10.1,10.0,100\n",
        encoding="utf-8",
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["minute_binding"] = {
        "mode": "exact_inventory",
        "files": {
            "20260814:000001.SZ": {
                "path": minute_relative,
                "sha256": hashlib.sha256(minute_path.read_bytes()).hexdigest(),
            }
        },
    }
    payload.pop("canonical_sha256")
    payload["canonical_sha256"] = hashlib.sha256(
        replay.canonical_json_bytes(payload)
    ).hexdigest()
    snapshot_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    snapshot, _audit = replay._load_replay_market_snapshot(
        tmp_path,
        expected_action_semantic_sha256=semantic_sha,
        expected_snapshot_file_sha256=hashlib.sha256(
            snapshot_path.read_bytes()
        ).hexdigest(),
        expected_calendar_file_sha256=hashlib.sha256(
            (tmp_path / replay.REPLAY_SSE_CALENDAR_PATH).read_bytes()
        ).hexdigest(),
        signal_date="20260814",
        report_date="20260817",
    )
    engine = replay.DiagnosticFrozenEngine(
        replay.AuctionV3Config(root=tmp_path),
        pd.DataFrame(),
        {},
        snapshot,
    )
    assert not engine.minute_table("20260814", "000001.SZ").empty
    assert engine.minute_table("20260814", "000002.SZ").empty


@pytest.mark.parametrize(
    ("signal_date", "report_date"),
    (
        ("20260230", ""),
        ("", "20260817"),
        ("20260817", "20260814"),
        ("20260814 ", "20260817"),
        ("20260814", "2026-08-17"),
    ),
)
def test_forced_replay_rejects_invalid_explicit_date_binding(
    tmp_path: Path,
    signal_date: str,
    report_date: str,
) -> None:
    with pytest.raises(RuntimeError, match="replay"):
        replay.run_forced_replay(
            tmp_path,
            signal_date=signal_date,
            report_date=report_date,
        )


def test_current_manifest_uses_exact_schema_loader_without_mutating_disk() -> None:
    manifest_path = ROOT / "models" / "decision_model_freeze.json"
    before = manifest_path.read_bytes()
    current = json.loads(before)
    assert current["schema_version"] in {
        "decision_model_freeze_v1",
        "decision_model_freeze_v2",
    }
    history, manifest, audit = replay.load_forced_frozen_history(ROOT)
    assert manifest["schema_version"] == current["schema_version"]
    assert manifest["active"] is current["active"]
    assert manifest_path.read_bytes() == before
    assert len(history) == 40_355
    assert history["signal_date"].nunique() == 715
    assert audit["sha256"] == replay.EXPECTED_SNAPSHOT_SHA256
    assert audit["columns_sha256"] == replay.EXPECTED_HISTORY_COLUMNS_SHA256
    assert audit["forced_frozen_replay"] is True
    assert audit["live_history_fallback"] is False
    assert audit["manifest_active_on_disk"] is current["active"]
    if current["schema_version"] == "decision_model_freeze_v1":
        assert current["active"] is False
        assert hashlib.sha256(before).hexdigest() == replay.LEGACY_BOOTSTRAP_MANIFEST_SHA256
        assert audit["source"] == "legacy_v1_exact_diagnostic_bootstrap"
        assert audit["loader_contract"] == "one_time_exact_v1_no_live_fallback"
        assert "pinned_files" not in audit
    else:
        assert audit["source"] == "forced_frozen_snapshot"
        assert audit["loader_contract"] == "v2_complete_contract_and_pins_no_live_fallback"
        pinned_files = audit["pinned_files"]
        assert pinned_files["active"] is current["active"]
        assert pinned_files["forced_enforcement"] is (not current["active"])
        assert pinned_files["validated"] is True
        assert pinned_files["enforced"] is True
        assert set(current["pinned_files"]) == REQUIRED_ACTIVE_PIN_PATHS
        assert set(manifest["pinned_files"]) == REQUIRED_ACTIVE_PIN_PATHS
        assert pinned_files["pinned_files"] == len(current["pinned_files"])


def test_exact_legacy_v1_fixture_bootstrap_is_independent_of_current_manifest(
    tmp_path: Path,
) -> None:
    manifest_bytes = _legacy_v1_manifest_bytes()
    manifest_path = _write_manifest(tmp_path, manifest_bytes)
    snapshot = tmp_path / replay.EXPECTED_SNAPSHOT_PATH
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / replay.EXPECTED_SNAPSHOT_PATH, snapshot)

    history, manifest, audit = replay.load_forced_frozen_history(tmp_path)

    assert manifest["schema_version"] == "decision_model_freeze_v1"
    assert manifest["active"] is False
    assert manifest_path.read_bytes() == manifest_bytes
    assert len(history) == 40_355
    assert history["signal_date"].nunique() == 715
    assert audit["sha256"] == replay.EXPECTED_SNAPSHOT_SHA256
    assert audit["columns_sha256"] == replay.EXPECTED_HISTORY_COLUMNS_SHA256
    assert audit["source"] == "legacy_v1_exact_diagnostic_bootstrap"
    assert audit["forced_frozen_replay"] is True
    assert audit["live_history_fallback"] is False
    assert audit["loader_contract"] == "one_time_exact_v1_no_live_fallback"
    assert audit["manifest_content_sha256"] == replay.LEGACY_BOOTSTRAP_MANIFEST_SHA256


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("byte_drift", "manifest content SHA drifted"),
        ("path", "history_snapshot.path drifted"),
        ("snapshot_sha", "history_snapshot.sha256 drifted"),
        ("bootstrap", "bootstrap_mode must be false"),
        ("cutoff", "cutoff drifted"),
        ("schema", "unsupported diagnostic freeze schema"),
        ("active", "must remain inactive"),
        ("freeze_id", "freeze_id drifted"),
    ],
)
def test_legacy_bootstrap_rejects_every_manifest_gate_mutation(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    original = _legacy_v1_manifest_bytes()
    if mutation == "byte_drift":
        candidate = original + b" "
    else:
        payload = json.loads(original)
        if mutation == "path":
            payload["history_snapshot"]["path"] = "models/other.csv.gz"
        elif mutation == "snapshot_sha":
            payload["history_snapshot"]["sha256"] = "1" * 64
        elif mutation == "bootstrap":
            payload["history_snapshot"]["bootstrap_mode"] = True
        elif mutation == "cutoff":
            payload["training_cutoff_signal_date"] = "20260804"
        elif mutation == "schema":
            payload["schema_version"] = "decision_model_freeze_v0"
        elif mutation == "active":
            payload["active"] = True
        elif mutation == "freeze_id":
            payload["freeze_id"] = "drifted"
        candidate = json.dumps(payload).encode("utf-8")
    _write_manifest(tmp_path, candidate)
    with pytest.raises(RuntimeError, match=message):
        replay.load_forced_frozen_history(tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("column_order", "column order/schema drifted"),
        ("code", "noncanonical ts_code"),
        ("date", "noncanonical signal_date"),
    ],
)
def test_legacy_bootstrap_validates_schema_code_and_date_after_byte_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    base = _synthetic_history()
    expected_columns = tuple(base.columns)
    candidate = base.copy()
    if mutation == "column_order":
        candidate = candidate.loc[:, list(reversed(candidate.columns))]
    elif mutation == "code":
        candidate.loc[0, "ts_code"] = "000001.SS"
    elif mutation == "date":
        candidate.loc[0, "signal_date"] = "2026-08-04"
    _write_synthetic_legacy(
        tmp_path,
        monkeypatch,
        candidate,
        expected_columns=expected_columns,
    )
    if mutation == "date":
        monkeypatch.setattr(replay, "EXPECTED_HISTORY_DATES", 2)
    with pytest.raises(RuntimeError, match=message):
        replay.load_forced_frozen_history(tmp_path)


@pytest.mark.parametrize("active", (False, True))
def test_v2_path_delegates_to_complete_verified_loader_for_inactive_and_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active: bool,
) -> None:
    raw = json.dumps({"schema_version": "decision_model_freeze_v2"}).encode()
    _write_manifest(tmp_path, raw)
    manifest = {
        "schema_version": "decision_model_freeze_v2",
        "active": active,
    }
    frame = _synthetic_history()
    calls: list[tuple[Path, dict]] = []
    pin_calls: list[tuple[Path, dict, bool]] = []

    def verified_loader(root: Path, candidate: dict):
        calls.append((Path(root), candidate))
        return frame.copy(), {
            "source": "forced_frozen_snapshot",
            "sha256": replay.EXPECTED_SNAPSHOT_SHA256,
        }

    monkeypatch.setattr(replay, "load_model_freeze", lambda root, required: manifest)
    monkeypatch.setattr(
        replay,
        "validate_pinned_files",
        lambda root, candidate, force_enforcement: (
            pin_calls.append((Path(root), candidate, force_enforcement))
            or {
                "active": active,
                "enforced": True,
                "forced_enforcement": not active,
            }
        ),
    )
    monkeypatch.setattr(replay, "load_verified_frozen_history_snapshot", verified_loader)
    monkeypatch.setattr(replay, "EXPECTED_HISTORY_ROWS", 2)
    monkeypatch.setattr(replay, "EXPECTED_HISTORY_DATES", 2)
    monkeypatch.setattr(replay, "EXPECTED_HISTORY_COLUMNS", len(frame.columns))
    monkeypatch.setattr(
        replay,
        "EXPECTED_HISTORY_COLUMNS_SHA256",
        replay.frame_columns_sha256(frame.columns),
    )

    loaded, returned_manifest, audit = replay.load_forced_frozen_history(tmp_path)

    assert loaded.equals(frame)
    assert returned_manifest == manifest
    assert pin_calls == [(tmp_path.resolve(), manifest, True)]
    assert calls == [(tmp_path.resolve(), manifest)]
    assert audit["source"] == "forced_frozen_snapshot"
    assert audit["loader_contract"] == "v2_complete_contract_and_pins_no_live_fallback"
    assert audit["live_history_fallback"] is False


def test_v2_verified_loader_failure_has_no_live_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_manifest(
        tmp_path,
        json.dumps({"schema_version": "decision_model_freeze_v2"}).encode(),
    )
    manifest = {"schema_version": "decision_model_freeze_v2", "active": False}
    monkeypatch.setattr(replay, "load_model_freeze", lambda root, required: manifest)
    monkeypatch.setattr(
        replay,
        "validate_pinned_files",
        lambda root, candidate, force_enforcement: {
            "enforced": True,
            "forced_enforcement": True,
        },
    )

    def fail_closed(root: Path, candidate: dict):
        raise replay.DecisionModelFreezeError("complete V2 pins missing")

    monkeypatch.setattr(replay, "load_verified_frozen_history_snapshot", fail_closed)
    with pytest.raises(replay.DecisionModelFreezeError, match="complete V2 pins missing"):
        replay.load_forced_frozen_history(tmp_path)


def test_v2_required_pin_byte_drift_stops_before_snapshot_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_manifest(
        tmp_path,
        json.dumps({"schema_version": "decision_model_freeze_v2"}).encode(),
    )
    manifest = {"schema_version": "decision_model_freeze_v2", "active": False}
    monkeypatch.setattr(replay, "load_model_freeze", lambda root, required: manifest)
    snapshot_called = False

    def reject_drift(root: Path, candidate: dict, *, force_enforcement: bool):
        assert force_enforcement is True
        raise replay.DecisionModelFreezeError(
            "pinned file drift: src/top10decision/decision/model_freeze.py"
        )

    def forbidden_snapshot(root: Path, candidate: dict):
        nonlocal snapshot_called
        snapshot_called = True
        raise AssertionError("snapshot loader must not run after pin drift")

    monkeypatch.setattr(replay, "validate_pinned_files", reject_drift)
    monkeypatch.setattr(
        replay, "load_verified_frozen_history_snapshot", forbidden_snapshot
    )
    with pytest.raises(replay.DecisionModelFreezeError, match="pinned file drift"):
        replay.load_forced_frozen_history(tmp_path)
    assert snapshot_called is False


def test_candidate_source_evidence_covers_full_executed_action_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_paths = {
        "src/top10decision/auction_v3/engine.py",
        "src/top10decision/decision/trade_selector.py",
        "src/top10decision/decision/canonical_fingerprint.py",
        "src/top10decision/decision/action_plan.py",
        "scripts/publish_decision_action.py",
        "scripts/replay_frozen_canonical_v2.py",
    }
    for index, relative in enumerate(sorted(expected_paths), start=1):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source-{index}\n", encoding="utf-8")
    monkeypatch.setattr(
        replay.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="a" * 40 + "\n",
        ),
    )

    before = replay.candidate_source_evidence(tmp_path)

    assert before["candidate_commit"] == "a" * 40
    assert set(before["file_sha256"]) == expected_paths
    assert all(
        isinstance(value, str)
        and len(value) == 64
        and set(value).issubset(set("0123456789abcdef"))
        for value in before["file_sha256"].values()
    )

    action_path = tmp_path / "src/top10decision/decision/action_plan.py"
    action_path.write_text("mutated-action-source\n", encoding="utf-8")
    after = replay.candidate_source_evidence(tmp_path)
    assert (
        before["file_sha256"]["src/top10decision/decision/action_plan.py"]
        != after["file_sha256"]["src/top10decision/decision/action_plan.py"]
    )
    for relative in expected_paths - {
        "src/top10decision/decision/action_plan.py"
    }:
        assert before["file_sha256"][relative] == after["file_sha256"][relative]


def test_identity_comparison_rejects_ambiguous_duplicate_rows() -> None:
    frame = pd.DataFrame(
        {
            "signal_date": ["20260805", "20260805"],
            "ts_code": ["000001.SZ", "000001.SZ"],
        }
    )
    with pytest.raises(RuntimeError, match="ambiguous duplicate identities"):
        replay._identity_index(frame, label="candidate")


def test_same_machine_raw_behavior_requires_exact_identity_gate_and_gap() -> None:
    reference = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "risk_gate_pass": [1],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [0.035],
        }
    )
    result = replay._compare_behavior(
        reference,
        reference.copy(),
        label="same-machine raw",
        columns=("stage",),
    )
    assert result["identity_equal"] is True
    assert all(value == 0 for value in result["changed_rows"].values())

    changed = reference.copy()
    changed.loc[0, "diagnostic_gap"] = 0.04
    changed.loc[0, "recommended_max_gap"] = 0.04
    with pytest.raises(RuntimeError, match="behavior changed in diagnostic_gap"):
        replay._compare_behavior(
            reference,
            changed,
            label="same-machine raw",
            columns=("stage",),
        )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("stage", None, "missing/empty stage"),
        ("risk_gate_pass", None, "missing risk_gate_pass"),
        ("diagnostic_gap", float("nan"), "missing diagnostic_gap"),
        ("diagnostic_gap", float("inf"), "non-finite diagnostic_gap"),
    ],
)
def test_behavior_schema_rejects_same_invalid_value_on_both_sides(
    column: str,
    value,
    message: str,
) -> None:
    frame = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "risk_gate_pass": [0],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [float("nan")],
        }
    )
    frame.loc[0, column] = value
    with pytest.raises(RuntimeError, match=message):
        replay._compare_behavior(
            frame,
            frame.copy(),
            label="invalid same-side",
            columns=("stage", "risk_gate_pass"),
        )


def test_recommended_gap_presence_must_exactly_follow_risk_gate() -> None:
    rejected = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "risk_gate_pass": [0],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [float("nan")],
        }
    )
    result = replay._compare_behavior(
        rejected,
        rejected.copy(),
        label="legal rejected gap",
        columns=("stage", "risk_gate_pass"),
    )
    assert result["candidate_schema"]["recommended_max_gap_missing"] == 1

    illegal = rejected.copy()
    illegal.loc[0, "recommended_max_gap"] = 0.035
    with pytest.raises(RuntimeError, match="missing-state disagrees"):
        replay._compare_behavior(
            illegal,
            illegal.copy(),
            label="illegal rejected gap",
            columns=("stage", "risk_gate_pass"),
        )

    passed = rejected.copy()
    passed.loc[0, "risk_gate_pass"] = 1
    passed.loc[0, "recommended_max_gap"] = 0.04
    with pytest.raises(RuntimeError, match="differs from diagnostic_gap"):
        replay._compare_behavior(
            passed,
            passed.copy(),
            label="illegal passed gap",
            columns=("stage", "risk_gate_pass"),
        )


def test_canonical_score_comparison_gates_at_eight_and_audits_higher_precision() -> None:
    reference = pd.DataFrame(
        {
            "signal_date": ["20260805", "20260806"],
            "ts_code": ["000001.SZ", "600000.SH"],
            "selection_score": [0.1234567801, 0.2],
        }
    )
    candidate = reference.copy()
    candidate.loc[0, "selection_score"] = 0.1234567805
    report = replay._canonical_score_comparison(
        reference,
        candidate,
        label="model",
        columns=("selection_score",),
    )
    assert report["8"]["equal"] is True
    assert report["8"]["gate"] == "hard"
    assert report["6"]["gate"] == "audit_only"
    assert report["10"]["gate"] == "audit_only"
    assert report["10"]["equal"] is False

    candidate.loc[0, "selection_score"] = 0.12345680
    with pytest.raises(RuntimeError, match="8-decimal scores drifted"):
        replay._canonical_score_comparison(
            reference,
            candidate,
            label="model",
            columns=("selection_score",),
        )


def test_canonical_score_comparison_rejects_shared_missing_or_nonfinite_score() -> None:
    for value, message in (
        (float("nan"), "missing selection_score"),
        (float("inf"), "non-finite selection_score"),
        ("bad", "invalid numeric selection_score"),
    ):
        frame = pd.DataFrame(
            {
                "signal_date": ["20260805"],
                "ts_code": ["000001.SZ"],
                "selection_score": [value],
            }
        )
        with pytest.raises(RuntimeError, match=message):
            replay._canonical_score_comparison(
                frame,
                frame.copy(),
                label="invalid score",
                columns=("selection_score",),
            )


def _prediction_policy_fixture() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    rows = 51
    domain_rows = 9
    domain = pd.Series([1] * domain_rows + [0] * (rows - domain_rows))
    model_projection = {
        "version": "model-policy-v1",
        "ready": True,
        "reason": "ready",
        "max_positions": 2,
        "thresholds": {
            "max_big_loss_probability": 0.2,
            "min_mean_return_lcb": 0.0,
            "min_fill_probability": 0.5,
            "min_exit_probability": 0.8,
            "min_conservative_ev": 0.0,
            "min_selection_score": -0.023437114100227405,
        },
    }
    selector_projection = {
        "version": "selector-policy-v1",
        "ready": True,
        "reason": "ready",
        "max_positions": 2,
        "tail_risk_weight": 0.25,
        "thresholds": {
            "min_trade_score": 0.1,
            "min_mean_return_lcb": 0.0,
            "min_fill_probability": 0.5,
            "max_big_loss_probability": 0.2,
        },
    }
    frame = pd.DataFrame(
        {
            "signal_date": ["20260814"] * rows,
            "ts_code": [f"{index:06d}.SZ" for index in range(rows)],
            "observation_selected": domain,
            "predicted_big_loss_probability": [0.1] * rows,
            "predicted_mean_return_lcb": [0.1] * rows,
            "predicted_fill_probability": [0.8] * rows,
            "predicted_public_market_buyable_probability": [0.8] * rows,
            "predicted_actual_order_fill_probability": [float("nan")] * rows,
            "actual_order_fill_probability_available": [0] * rows,
            "predicted_exit_probability": [0.9] * rows,
            "conservative_ev": [0.1] * rows,
            "selection_score": [0.2] * rows,
            "stage_focus": [1] * rows,
            "gate_policy_ready": [1] * rows,
            "gate_stage_focus": [1] * rows,
            "gate_exit_probability": [1] * rows,
            "gate_fill_probability": [1] * rows,
            "gate_big_loss_probability": [1] * rows,
            "gate_mean_return_lcb": [1] * rows,
            "gate_conservative_ev": [1] * rows,
            "gate_selection_score": [1] * rows,
            "risk_gate_pass": [1] * rows,
            "trade_gate_pass": [1, 1] + [0] * (rows - 2),
            "trade_shadow_selected": [0] * rows,
            "trade_selected": [0] * rows,
            "trade_selector_policy_ready": [1] * domain_rows
            + [0] * (rows - domain_rows),
            "trade_selector_promoted": [0] * rows,
            "trade_model_reason": ["learned_policy_pass"] * domain_rows
            + ["outside_observation_top10"] * (rows - domain_rows),
        }
    )
    for name, column in {
        "max_big_loss_probability": "policy_max_big_loss_probability",
        "min_mean_return_lcb": "policy_min_mean_return_lcb",
        "min_fill_probability": "policy_min_fill_probability",
        "min_exit_probability": "policy_min_exit_probability",
        "min_conservative_ev": "policy_min_conservative_ev",
        "min_selection_score": "policy_min_selection_score",
    }.items():
        frame[column] = model_projection["thresholds"][name]
    domain_values = [0.8] * domain_rows + [float("nan")] * (rows - domain_rows)
    for column in replay.SELECTOR_PREDICTION_SCORE_COLUMNS:
        frame[column] = domain_values
    frame["trade_score"] = [0.2] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_mean_return_lcb"] = [0.1] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_fill_probability"] = [0.8] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_public_market_buyable_probability"] = [0.8] * domain_rows + [float("nan")] * (rows - domain_rows)
    frame["trade_predicted_big_loss_probability"] = [0.1] * domain_rows + [float("nan")] * (rows - domain_rows)
    for column in replay.SELECTOR_PREDICTION_RANK_COLUMNS:
        frame[column] = list(range(1, domain_rows + 1)) + [float("nan")] * (
            rows - domain_rows
        )
    for column, value in (
        ("trade_selector_artifact_sha256", "a" * 64),
        ("trade_selector_artifact_v2_sha256", "b" * 64),
    ):
        frame[column] = [value] * domain_rows + [None] * (rows - domain_rows)
    text = frame.map(lambda value: "" if pd.isna(value) else str(value))
    return frame, text, model_projection, selector_projection


def test_prediction_policy_exact_decimal_surface_and_51_9_domain_pass() -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    result = replay.validate_prediction_policy_execution(
        frame,
        prediction_text=text,
        model_execution_projection=model,
        selector_execution_projection=selector,
    )
    assert result["rows"] == 51
    assert result["selector_domain_rows"] == 9
    assert result["selector_outside_rows"] == 42
    assert result["policy_threshold_text_surface"]["exact_decimal_match"] is True
    assert result["selector_trade_gate_pass_rows"] == 2
    assert result["fill_contract"]["selector_alias_equal_rows"] == 9


@pytest.mark.parametrize(
    "raw",
    (
        "-0.0234371141002274",
        "-0.023437094100227405",
        "NaN",
        "Inf",
        "",
        "malformed",
    ),
)
def test_prediction_policy_text_surface_rejects_any_decimal_drift(raw: str) -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    text.loc[0, "policy_min_selection_score"] = raw
    with pytest.raises(RuntimeError, match="policy text surface|differs from model"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text,
            model_execution_projection=model,
            selector_execution_projection=selector,
        )


def test_exact_text_reader_rejects_duplicate_policy_header(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    path.write_text(
        "policy_min_selection_score,policy_min_selection_score\n0.1,0.1\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="duplicate header"):
        replay._read_csv_exact_text(path)


def test_prediction_policy_text_surface_requires_same_row_count() -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    with pytest.raises(RuntimeError, match="row count differs"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text.iloc[:-1].copy(),
            model_execution_projection=model,
            selector_execution_projection=selector,
        )


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("gate_policy_ready", 0.5),
        ("risk_gate_pass", 2),
        ("stage_focus", "true"),
        ("observation_selected", 0.5),
        ("trade_gate_pass", 2),
        ("trade_shadow_selected", 0.5),
        ("trade_selected", 2),
        ("trade_selector_policy_ready", "true"),
        ("trade_selector_promoted", 0.5),
    ),
)
def test_prediction_policy_rejects_nonbinary_aliases(column: str, value) -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = value
    with pytest.raises(RuntimeError, match="non-binary|non-integral|invalid numeric"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text,
            model_execution_projection=model,
            selector_execution_projection=selector,
        )


@pytest.mark.parametrize(
    ("column", "row", "value"),
    (
        ("trade_score", 9, 0.1),
        ("trade_selector_artifact_v2_sha256", 9, "c" * 64),
        ("trade_gate_pass", 9, 1),
        ("trade_model_reason", 9, "learned_policy_pass"),
        ("trade_rank", 0, float("nan")),
        ("promotion_rank", 1, 1),
    ),
)
def test_prediction_selector_domain_contract_rejects_mixed_domain(
    column: str,
    row: int,
    value,
) -> None:
    frame, text, model, selector = _prediction_policy_fixture()
    frame.loc[row, column] = value
    with pytest.raises(RuntimeError, match="selector domain"):
        replay.validate_prediction_policy_execution(
            frame,
            prediction_text=text,
            model_execution_projection=model,
            selector_execution_projection=selector,
        )


@pytest.mark.parametrize(
    ("column", "row", "value"),
    (
        ("predicted_public_market_buyable_probability", 0, 0.7),
        ("predicted_fill_probability", 0, 1.1),
        ("trade_predicted_public_market_buyable_probability", 0, 0.7),
        ("trade_predicted_fill_probability", 9, 0.8),
        ("predicted_actual_order_fill_probability", 0, 0.5),
        ("actual_order_fill_probability_available", 0, 0.5),
        ("actual_order_fill_probability_available", 0, 1),
        ("predicted_actual_order_fill_probability", 0, float("inf")),
        ("predicted_fill_probability", 0, float("nan")),
    ),
)
def test_prediction_fill_contract_rejects_alias_range_or_availability_drift(
    column: str,
    row: int,
    value,
) -> None:
    frame, _, _, _ = _prediction_policy_fixture()
    frame[column] = frame[column].astype(object)
    frame.loc[row, column] = value
    with pytest.raises(RuntimeError, match="fill contract|selector domain"):
        replay.validate_prediction_fill_contract(frame)


def test_prediction_fill_contract_accepts_available_actual_probability() -> None:
    frame, _, _, _ = _prediction_policy_fixture()
    frame.loc[0, "predicted_actual_order_fill_probability"] = 0.6
    frame.loc[0, "actual_order_fill_probability_available"] = 1
    result = replay.validate_prediction_fill_contract(frame)
    assert result["actual_probability_available_rows"] == 1
    assert result["actual_probability_missing_rows"] == 50


def _action_plan_fixture() -> dict:
    return {
        "signal_date": "20260805",
        "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
        "formal_buy_count": 0,
        "stage_watchlist": [
            {
                "ts_code": "000001.SZ",
                "stage_watch_rank": 1,
                "trade_rank": 1,
                "trade_shadow_selected": 1,
                "watch_label": "二筛影子",
                "action": "SHADOW_ONLY",
                "target_weight": 0.0,
            },
            {
                "ts_code": "600000.SH",
                "stage_watch_rank": 2,
                "trade_rank": 2,
                "trade_shadow_selected": 1,
                "watch_label": "二筛影子",
                "action": "SHADOW_ONLY",
                "target_weight": 0.0,
            },
            {
                "ts_code": "000002.SZ",
                "stage_watch_rank": 3,
                "trade_rank": 3,
                "trade_shadow_selected": 0,
                "watch_label": "仅观察",
                "action": "REJECT",
                "target_weight": 0.0,
            },
        ],
    }


def test_action_plan_candidate_projection_hashes_discrete_and_weight() -> None:
    base = _action_plan_fixture()
    original = replay._action_candidate_contract(base)
    changed = _action_plan_fixture()
    changed["stage_watchlist"][2]["stage_watch_rank"] = 4
    mutated = replay._action_candidate_contract(changed)
    assert (
        mutated["action_plan_candidates_sha256_q8"]
        != original["action_plan_candidates_sha256_q8"]
    )
    for replacement in (" 仅观察 ", "ＷＡＴＣＨ"):
        exact_changed = _action_plan_fixture()
        exact_changed["stage_watchlist"][2]["watch_label"] = replacement
        with pytest.raises(RuntimeError, match="shadow contract drifted"):
            replay._action_candidate_contract(exact_changed)

    invalid_action = _action_plan_fixture()
    invalid_action["stage_watchlist"][0]["action"] = "BUY"
    with pytest.raises(RuntimeError, match="action is invalid"):
        replay._action_candidate_contract(invalid_action)

    invalid_weight = _action_plan_fixture()
    invalid_weight["stage_watchlist"][0]["target_weight"] = 0.01
    with pytest.raises(RuntimeError, match="target_weight is nonzero"):
        replay._action_candidate_contract(invalid_weight)


def test_action_plan_candidate_requires_exact_relative_best_two() -> None:
    missing = _action_plan_fixture()
    missing["stage_watchlist"][1]["action"] = "REJECT"
    missing["stage_watchlist"][1]["watch_label"] = "仅观察"
    missing["stage_watchlist"][1]["trade_shadow_selected"] = 0
    with pytest.raises(RuntimeError, match="shadow contract drifted"):
        replay._action_candidate_contract(missing)

    extra = _action_plan_fixture()
    extra["stage_watchlist"][2]["action"] = "SHADOW_ONLY"
    extra["stage_watchlist"][2]["watch_label"] = "二筛影子"
    extra["stage_watchlist"][2]["trade_shadow_selected"] = 1
    with pytest.raises(RuntimeError, match="shadow contract drifted"):
        replay._action_candidate_contract(extra)

    wrong = _action_plan_fixture()
    for index in (0, 2):
        is_shadow = index == 2
        wrong["stage_watchlist"][index]["action"] = (
            "SHADOW_ONLY" if is_shadow else "REJECT"
        )
        wrong["stage_watchlist"][index]["watch_label"] = (
            "二筛影子" if is_shadow else "仅观察"
        )
        wrong["stage_watchlist"][index]["trade_shadow_selected"] = int(
            is_shadow
        )
    with pytest.raises(RuntimeError, match="shadow contract drifted"):
        replay._action_candidate_contract(wrong)


def test_action_plan_candidate_projection_rejects_duplicate_or_empty_identity() -> None:
    duplicate = _action_plan_fixture()
    duplicate["stage_watchlist"][1]["ts_code"] = "000001.SZ"
    with pytest.raises(RuntimeError, match="ambiguous duplicate identities"):
        replay._action_candidate_contract(duplicate)
    empty = _action_plan_fixture()
    empty["stage_watchlist"][0]["ts_code"] = ""
    with pytest.raises(RuntimeError, match="empty ts_code"):
        replay._action_candidate_contract(empty)


def _fingerprint_integrity_fixture() -> tuple[dict, dict, dict, dict]:
    model_policy = {
        "version": "nested_temporal_utility_v1",
        "ready": False,
        "reason": "no_policy_passed_independent_holdout",
        "max_positions": 2,
        "thresholds": {
            "max_big_loss_probability": 0.4,
            "min_mean_return_lcb": -0.03,
            "min_fill_probability": 0.1,
            "min_exit_probability": 0.9,
            "min_conservative_ev": -0.01,
            "min_selection_score": 0.0,
        },
    }
    selector_policy = {
        "version": "trade_selector_v2_nested_oos_top10_promotion_rank",
        "ready": False,
        "reason": "best_shadow_policy_failed_profit_or_coverage_gate",
        "max_positions": 2,
        "tail_risk_weight": 0.75,
        "thresholds": {
            "min_trade_score": 0.0,
            "min_mean_return_lcb": -0.03,
            "min_fill_probability": 1.0,
            "max_big_loss_probability": 0.5,
        },
    }
    model_projection = replay.canonical_execution_projection(
        replay._model_executable_policy_projection(model_policy),
        decimals=8,
    )
    selector_projection = replay.canonical_execution_projection(
        replay._selector_executable_policy_projection(selector_policy),
        decimals=8,
    )
    model_policy_sha = replay.canonical_mapping_sha256(
        {
            "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
            "artifact_kind": "decision_model_executable_policy",
            "projection": model_projection,
        },
        decimals=8,
        exact_strings=True,
    )
    selector_policy_sha = replay.canonical_policy_fingerprint(
        selector_projection,
        decimals=8,
    )["sha256"]
    model = {
        "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": replay.MODEL_CANONICAL_V2,
        "canonical_contract": {
            "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
            "layer": "model",
            "decimals": 8,
            "rounding": "decimal_string_half_even",
            "execution_mode": "raw_float64",
            "raw_execution_preserved": True,
        },
        "policy_projection": model_projection,
        "policy_sha256": model_policy_sha,
        "provenance_sha256": "a" * 64,
        "semantic_sha256": "b" * 64,
        "schema_valid": True,
        "missing_columns": [],
        "invalid_cell_count": 0,
    }
    model["artifact_sha256"] = replay.compose_artifact_fingerprint(
        artifact_kind="decision_model_canonical_runtime_v2",
        provenance_sha256=model["provenance_sha256"],
        semantic_sha256=model["semantic_sha256"],
        policy_sha256=model_policy_sha,
        decimals=8,
    )
    selector = {
        "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": replay.TRADE_SELECTOR_CANONICAL_V2,
        "canonical_contract": {
            "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
            "layer": "trade_selector",
            "decimals": 8,
            "rounding": "decimal_string_half_even",
            "execution_mode": "raw_float64",
            "raw_execution_preserved": True,
        },
        "policy_projection": selector_projection,
        "policy_sha256": selector_policy_sha,
        "provenance_sha256": "c" * 64,
        "semantic_sha256": "d" * 64,
        "schema_valid": True,
        "missing_columns": [],
        "invalid_cell_count": 0,
    }
    selector["artifact_sha256"] = replay.compose_artifact_fingerprint(
        artifact_kind="decision_trade_selector_canonical_runtime_v2",
        provenance_sha256=selector["provenance_sha256"],
        semantic_sha256=selector["semantic_sha256"],
        policy_sha256=selector_policy_sha,
        decimals=8,
    )
    return model, selector, model_policy, selector_policy


def _resign_policy_projection(
    fingerprint: dict,
    *,
    layer: str,
) -> None:
    projection = fingerprint["policy_projection"]
    if layer == "model":
        policy_sha = replay.canonical_mapping_sha256(
            {
                "schema": replay.CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": projection,
            },
            decimals=8,
            exact_strings=True,
        )
        artifact_kind = "decision_model_canonical_runtime_v2"
    else:
        policy_sha = replay.canonical_policy_fingerprint(
            projection,
            decimals=8,
        )["sha256"]
        artifact_kind = "decision_trade_selector_canonical_runtime_v2"
    fingerprint["policy_sha256"] = policy_sha
    fingerprint["artifact_sha256"] = replay.compose_artifact_fingerprint(
        artifact_kind=artifact_kind,
        provenance_sha256=fingerprint["provenance_sha256"],
        semantic_sha256=fingerprint["semantic_sha256"],
        policy_sha256=policy_sha,
        decimals=8,
    )


def test_fingerprint_integrity_recomputes_live_policy_and_artifact() -> None:
    model, selector, model_policy, selector_policy = _fingerprint_integrity_fixture()
    report = replay.validate_fingerprint_integrity(
        model,
        selector,
        live_model_policy=model_policy,
        live_selector_policy=selector_policy,
    )
    assert report["live_policies_match_fingerprint_projection"] is True

    within_q8_model = json.loads(json.dumps(model_policy))
    within_q8_model["thresholds"]["min_selection_score"] = 0.0000000004
    within_q8_selector = json.loads(json.dumps(selector_policy))
    within_q8_selector["thresholds"]["min_trade_score"] = -0.0000000004
    stable = replay.validate_fingerprint_integrity(
        model,
        selector,
        live_model_policy=within_q8_model,
        live_selector_policy=within_q8_selector,
    )
    assert stable["model_projection"] == model["policy_projection"]
    assert stable["selector_projection"] == selector["policy_projection"]
    assert (
        stable["model_execution_projection"]["thresholds"][
            "min_selection_score"
        ]
        != model["policy_projection"]["thresholds"]["min_selection_score"]
    )

    stale_model_policy = json.loads(json.dumps(model_policy))
    stale_model_policy["thresholds"]["min_selection_score"] += 0.00000002
    with pytest.raises(RuntimeError, match="does not canonicalize"):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=stale_model_policy,
            live_selector_policy=selector_policy,
        )

    stale_selector_policy = json.loads(json.dumps(selector_policy))
    stale_selector_policy["thresholds"]["min_trade_score"] += 0.00000002
    with pytest.raises(RuntimeError, match="does not canonicalize"):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=stale_selector_policy,
        )

    stale_fingerprint = dict(model)
    stale_fingerprint["policy_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="policy SHA does not recompute"):
        replay.validate_fingerprint_integrity(
            stale_fingerprint,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=selector_policy,
        )


@pytest.mark.parametrize(
    ("layer", "mutation", "message"),
    [
        (
            "model",
            lambda projection: projection["thresholds"].__setitem__(
                "min_selection_score", 0
            ),
            "threshold must be a native float",
        ),
        (
            "selector",
            lambda projection: projection.__setitem__("max_positions", 2.0),
            "max_positions must be a native integer",
        ),
        (
            "model",
            lambda projection: projection.__setitem__("max_positions", True),
            "max_positions must be a native integer",
        ),
        (
            "model",
            lambda projection: projection.__setitem__("ready", 0),
            "ready must be a native boolean",
        ),
        (
            "selector",
            lambda projection: projection.__setitem__("ready", 1),
            "ready must be a native boolean",
        ),
        (
            "selector",
            lambda projection: projection["thresholds"].__setitem__(
                "min_trade_score", False
            ),
            "threshold must be a native float",
        ),
        (
            "selector",
            lambda projection: projection["thresholds"].__setitem__(
                "min_fill_probability", True
            ),
            "threshold must be a native float",
        ),
        (
            "selector",
            lambda projection: projection.__setitem__("tail_risk_weight", 0),
            "tail_risk_weight must be a native float",
        ),
        (
            "model",
            lambda projection: projection.__setitem__("unknown", 1),
            "projection keys drifted",
        ),
        (
            "selector",
            lambda projection: projection["thresholds"].__setitem__(
                "unknown", 0.0
            ),
            "threshold keys drifted",
        ),
    ],
)
def test_fingerprint_integrity_rejects_fully_resigned_json_type_aliases(
    layer: str,
    mutation,
    message: str,
) -> None:
    model, selector, model_policy, selector_policy = _fingerprint_integrity_fixture()
    fingerprint = model if layer == "model" else selector
    mutation(fingerprint["policy_projection"])
    _resign_policy_projection(fingerprint, layer=layer)
    with pytest.raises(RuntimeError, match=message):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=selector_policy,
        )


@pytest.mark.parametrize("value", ["0.0", float("nan"), float("inf"), float("-inf")])
def test_fingerprint_integrity_rejects_non_native_or_nonfinite_thresholds(
    value,
) -> None:
    model, selector, model_policy, selector_policy = _fingerprint_integrity_fixture()
    model["policy_projection"]["thresholds"]["min_selection_score"] = value
    message = "native float" if isinstance(value, str) else "non-finite"
    with pytest.raises(RuntimeError, match=message):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=selector_policy,
        )


def test_live_policy_projection_comparison_is_json_type_sensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, selector, model_policy, selector_policy = _fingerprint_integrity_fixture()
    aliased_live_projection = dict(model["policy_projection"])
    aliased_live_projection["max_positions"] = 2.0
    original_canonicalize = replay.canonical_execution_projection

    def canonicalize_with_live_alias(value, *, decimals: int):
        canonical = original_canonicalize(value, decimals=decimals)
        if (
            value is not model["policy_projection"]
            and isinstance(value, dict)
            and value.get("version") == model_policy["version"]
        ):
            return aliased_live_projection
        return canonical

    monkeypatch.setattr(
        replay,
        "canonical_execution_projection",
        canonicalize_with_live_alias,
    )
    assert aliased_live_projection == model["policy_projection"]
    assert (
        replay.canonical_json_bytes(aliased_live_projection)
        != replay.canonical_json_bytes(model["policy_projection"])
    )
    with pytest.raises(RuntimeError, match="does not canonicalize"):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=selector_policy,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__("unknown", 1), "envelope keys drifted"),
        (lambda value: value.pop("semantic_sha256"), "envelope keys drifted"),
        (lambda value: value.__setitem__("schema", "wrong"), "schema drifted"),
        (
            lambda value: value.__setitem__("provenance_sha256", "bad"),
            "provenance_sha256 invalid",
        ),
        (lambda value: value.__setitem__("schema_valid", False), "schema is not valid"),
        (
            lambda value: value.__setitem__("missing_columns", ["gross_return"]),
            "semantic columns missing",
        ),
        (
            lambda value: value.__setitem__("invalid_cell_count", 1),
            "semantic invalid cells present",
        ),
    ],
)
def test_fingerprint_integrity_rejects_non_strict_envelope(
    mutation,
    message: str,
) -> None:
    model, selector, model_policy, selector_policy = _fingerprint_integrity_fixture()
    mutation(model)
    with pytest.raises(RuntimeError, match=message):
        replay.validate_fingerprint_integrity(
            model,
            selector,
            live_model_policy=model_policy,
            live_selector_policy=selector_policy,
        )


def test_golden_path_cannot_leave_integrity_validator_unused() -> None:
    source = inspect.getsource(replay.compare_frozen_golden)
    assert "validate_fingerprint_integrity(" in source
    assert "validate_prediction_policy_execution(" in source


def test_reference_profile_rejects_mutable_files_unless_explicit_same_machine(
    tmp_path: Path,
) -> None:
    for name in replay.REFERENCE_C6_GIT_BLOBS:
        (tmp_path / name).write_text(f"local {name}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="untrusted persisted-c6 reference blob"):
        replay.validate_reference_snapshot(tmp_path, profile="persisted-c6")
    report = replay.validate_reference_snapshot(
        tmp_path,
        profile="same-machine-c6",
    )
    assert report["persisted_trust_root_verified"] is False
    assert report["same_machine_reference_only"] is True


def test_behavior_contract_hashes_text_and_stage_exactly() -> None:
    frame = pd.DataFrame(
        {
            "signal_date": ["20260805"],
            "ts_code": ["000001.SZ"],
            "stage": ["2→3"],
            "model_reason": ["selection_policy_not_ready"],
            "observation_risk_label": ["HIGH_RISK"],
            "risk_gate_pass": [0],
            "diagnostic_gap": [0.035],
            "recommended_max_gap": [float("nan")],
        }
    )
    discrete = (
        "stage",
        "model_reason",
        "observation_risk_label",
        "risk_gate_pass",
    )
    original = replay._candidate_frame_contract(
        frame,
        label="exact behavior",
        discrete_columns=discrete,
        score_columns=("diagnostic_gap", "recommended_max_gap"),
    )["discrete_sha256"]
    for column, replacement in (
        ("stage", "2->3"),
        ("model_reason", " selection_policy_not_ready "),
        ("observation_risk_label", "ＨＩＧＨ＿ＲＩＳＫ"),
    ):
        changed = frame.copy()
        changed.loc[0, column] = replacement
        digest = replay._candidate_frame_contract(
            changed,
            label="exact behavior",
            discrete_columns=discrete,
            score_columns=("diagnostic_gap", "recommended_max_gap"),
        )["discrete_sha256"]
        assert digest != original


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("signal_date", "2026-08-05", "invalid exact-format signal_date"),
        ("signal_date", "20260805 ", "whitespace in signal_date"),
        ("ts_code", "000001.sz", "invalid exact-format ts_code"),
        ("ts_code", " 000001.SZ", "whitespace in ts_code"),
    ],
)
def test_identity_requires_exact_canonical_format(
    column: str,
    value: str,
    message: str,
) -> None:
    frame = pd.DataFrame(
        {"signal_date": ["20260805"], "ts_code": ["000001.SZ"]}
    )
    frame.loc[0, column] = value
    with pytest.raises(RuntimeError, match=message):
        replay._identity_index(frame, label="identity")
