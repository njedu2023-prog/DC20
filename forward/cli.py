"""Read-only migration acceptance and preview commands. No production writer."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .schedule import monitor_target, read_calendar


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _config(root: Path) -> dict:
    value = _json(root / "forward/config.json")
    if value.get("legacy_statistics_import_allowed") is not False:
        raise ValueError("old statistics import is forbidden")
    if value.get("formal_trade_actions_allowed") is not False:
        raise ValueError("real trading action is forbidden")
    return value


def export_site(output: Path, dashboard: dict, ledger: dict, web_root: Path) -> None:
    output = Path(output)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("preview output cannot follow a symlink")
    output.mkdir(parents=True, exist_ok=True)
    serial = json.dumps(dashboard, ensure_ascii=False, sort_keys=True, allow_nan=False)
    serial = serial.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (web_root / "index.html").read_text(encoding="utf-8")
    if template.count("__FORWARD_SNAPSHOT_JSON__") != 1:
        raise ValueError("preview template must have one data placeholder")
    (output / "index.html").write_text(template.replace("__FORWARD_SNAPSHOT_JSON__", serial), encoding="utf-8")
    for filename in ("styles.css", "app.js"):
        shutil.copyfile(web_root / filename, output / filename)
    for filename, data in (("snapshot.json", dashboard), ("ledger.json", ledger)):
        (output / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rows = (dashboard.get("latest") or {}).get("rows", [])
    columns = ["signal_date", "exec_date", "exit_date", "ts_code", "name", "industry", "stage_transition",
               "promotion_rank", "promotion_probability", "path_label", "path_change_pct", "profit_rank", "profit_score"]
    for filename, rank in (("promotion.csv", "promotion_rank"), ("profit.csv", "profit_rank")):
        with (output / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in sorted(rows, key=lambda row: row[rank]):
                dates = {key: dashboard["latest"][key] for key in columns[:3]}
                # Spreadsheet injection guard for untrusted source text fields.
                safe = {key: ("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value)
                        for key, value in row.items()}
                writer.writerow({**safe, **dates})
    manifest = {"schema_version": "dc20_forward_preview_revision_v1", "source_revision": dashboard["source_revision"],
                "production_enabled": False, "files": {}}
    for filename in ("index.html", "styles.css", "app.js", "snapshot.json", "ledger.json", "promotion.csv", "profit.csv"):
        manifest["files"][filename] = hashlib.sha256((output / filename).read_bytes()).hexdigest()
    (output / "revision.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_preview(root: Path, output: Path, signal_date: str | None, revision: str) -> dict:
    from .engine import load_frozen_day
    from .ledger import freeze_day_sha256, new_ledger
    from .metrics import statistics
    from .settlement import verify_day

    root = root.resolve()
    config = _config(root)
    if config.get("production_enabled") is not False:
        raise ValueError("migration preview cannot be used as a live publishing command")
    calendar = read_calendar(root / config["calendar_path"], config["calendar_sha256"])
    ledger = new_ledger(config["epoch_id"])
    if signal_date is None:
        index = _json(root / "outputs/decision/executable_profit_research/index.json")
        signal_date = index.get("latest_signal_date")
    # The migration reader deliberately accepts only REPLAY and performs no writes.
    day = load_frozen_day(root, signal_date, generation_mode="REPLAY")
    day["freeze_sha256"] = freeze_day_sha256(day)
    verification = verify_day(day, {}, calendar, as_of_date=day["signal_date"], costs_bps=config["round_trip_cost_bps"])
    dashboard = {
        "schema_version": "dc20_forward_dashboard_v1", "production_enabled": False,
        "phase": config["phase"], "source_revision": revision,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "epoch": {"id": config["epoch_id"], "activated_at_utc": None, "start_signal_date": None},
        "latest": day, "verification": verification, "statistics": statistics(ledger),
        "costs_bps": config["round_trip_cost_bps"], "history": [],
        "migration_notice": "REPLAY only; original frozen outputs, not newly inferred or forward-selected",
    }
    export_site(output, dashboard, ledger, root / "forward/web")
    return {"status": "MIGRATION_PREVIEW_ONLY", "signal_date": signal_date,
            "candidate_count": len(day["rows"]), "new_forward_days": 0,
            "production_enabled": False, "output": str(output)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview", help="build separately labeled non-forward migration preview")
    preview.add_argument("--output", type=Path, required=True)
    preview.add_argument("--signal-date")
    preview.add_argument("--revision", required=True)
    window = sub.add_parser("window", help="local calendar/time gate only; never access remote services")
    window.add_argument("--now", required=True)
    args = parser.parse_args(argv)
    if args.command == "preview":
        result = build_preview(args.root, args.output, args.signal_date, args.revision)
    else:
        config = _config(args.root)
        dates = read_calendar(args.root / config["calendar_path"], config["calendar_sha256"])
        target = monitor_target(args.now, dates)
        result = {"target": target, "eligible_calendar_window": target is not None,
                  "remote_monitoring_allowed": target is not None and config["production_enabled"] is True,
                  "production_enabled": config["production_enabled"]}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
