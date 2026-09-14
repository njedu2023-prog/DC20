"""Materialize and bind the active candidate's small versioned Pages view."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from top10decision.decision import candidate_profit_publication as projection
from top10decision.decision import candidate_formal_shadow_summary as ledger

CONFIG = "models/decision_candidate_profit_activation_v1.json"
PREFIX = "outputs/decision/candidate_profit_v1/"


def verify_public(site_root, fetch_bytes):
    """Require exact deployed bytes, including the newest frozen daily ranking."""
    site = Path(site_root).resolve(strict=True)
    revision_raw = safe_read(site, "revision.json")
    if fetch_bytes("revision.json") != revision_raw:
        raise ValueError("candidate Pages revision has not converged")
    revision = json.loads(revision_raw)
    relatives = ["index.html", "decision.html"]
    for role in ("activation", "index", "summary"):
        relative = revision[f"candidate_profit_{role}_url"]
        expected = safe_read(site, relative)
        if hashlib.sha256(expected).hexdigest() != revision[f"candidate_profit_{role}_sha256"]:
            raise ValueError("local candidate Pages binding changed")
        relatives.append(relative)
    index = json.loads(safe_read(site, PREFIX + "index.json"))
    if index["days"]:
        relatives.append(index["days"][-1]["path"])
    for relative in relatives:
        if fetch_bytes(relative) != safe_read(site, relative):
            raise ValueError("candidate Pages bytes have not converged: " + relative)
    return {"enabled": revision["candidate_profit_activation"]["enabled"],
            "status": index["status"], "verified_files": len(relatives) + 1}


def safe_read(root, relative):
    path = root / relative
    if path.is_symlink() or any(p.is_symlink() for p in path.parents) or not path.is_file():
        raise ValueError("regular candidate Pages file required: " + relative)
    if not 0 < path.stat().st_size <= 32 * 1024**2:
        raise ValueError("candidate Pages file exceeds bound")
    return path.read_bytes()


def materialize(repo_root, site_root):
    root, site = Path(repo_root).resolve(strict=True), Path(site_root).resolve(strict=True)
    config_raw = safe_read(root, CONFIG)
    config = json.loads(config_raw)
    projection.validate_activation(config)
    index_raw = safe_read(root, PREFIX + "index.json")
    summary_raw = safe_read(root, PREFIX + "summary.json")
    index = projection.validate_public_index(index_raw, expected_sha256=hashlib.sha256(index_raw).hexdigest(), activation=config)
    summary = json.loads(summary_raw)
    ledger.validate_summary(summary, activation_config=config)
    if summary["published_signal_dates"] != [entry["signal_date"] for entry in index["days"]]:
        raise ValueError("candidate Pages index and ledger cover different frozen days")
    sequences = {name: {row["signal_date"]: row for row in summary["groups"][name]["daily_sequence"]}
                 for name in ledger.GROUPS}
    copies = {CONFIG: config_raw, PREFIX + "index.json": index_raw, PREFIX + "summary.json": summary_raw}
    for entry in index["days"]:
        relative = entry["path"]
        raw = safe_read(root, relative)
        day = projection.validate_public_day(raw, expected_sha256=entry["sha256"], activation=config)
        if (day["signal_date"] != entry["signal_date"]
                or day["snapshot_file_sha256"] != entry["snapshot_file_sha256"]
                or day["p0_file_sha256"] != entry["p0_file_sha256"]):
            raise ValueError("candidate Pages day identity changed")
        p0_raw = safe_read(root, "outputs/decision/three_rank_top10_" + day["signal_date"] + ".json")
        if hashlib.sha256(p0_raw).hexdigest() != day["p0_file_sha256"]:
            raise ValueError("candidate Pages frozen promotion source changed")
        for sequence in sequences.values():
            row = sequence[day["signal_date"]]
            if (row["formal_projection_sha256"] != ledger.canonical(day)
                    or row["snapshot_file_sha256"] != day["snapshot_file_sha256"]):
                raise ValueError("candidate Pages ranking and ledger identity differ")
        copies[relative] = raw
    for relative, raw in copies.items():
        target = site / relative
        if target.is_symlink() or any(p.is_symlink() for p in target.parents):
            raise ValueError("candidate Pages target is a symlink")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    revision_path = site / "revision.json"
    revision = json.loads(safe_read(site, "revision.json"))
    revision.update(
        candidate_profit_activation={k: config[k] for k in
            ("enabled", "activation_id", "effective_from_signal_date", "model_canonical_sha256")},
        candidate_profit_activation_url=CONFIG,
        candidate_profit_activation_sha256=hashlib.sha256(config_raw).hexdigest(),
        candidate_profit_index_url=PREFIX + "index.json",
        candidate_profit_index_sha256=hashlib.sha256(index_raw).hexdigest(),
        candidate_profit_summary_url=PREFIX + "summary.json",
        candidate_profit_summary_sha256=hashlib.sha256(summary_raw).hexdigest(),
    )
    revision_path.write_text(json.dumps(revision, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return {"status": index["status"], "days": len(index["days"]),
            "activation_id": config["activation_id"], "enabled": config["enabled"]}
