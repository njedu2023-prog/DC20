"""Import only verified daily/limit bytes from the immutable v1 research ZIP.

No old auction/minute source, marker, request receipt, label, score or model is
installed into the new active mirror. The old ZIP is only ever opened read-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

MAX_ZIP_BYTES = 128 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 10000


def import_verified_archive(root, archive, plan):
    from work.profit_1000_upgrade import run
    from top10decision.decision import executable_profit_shadow_settlement as settlement
    root = run.require_research_mirror(root, plan_version="v2")
    if plan != run.load_plan("v2"):
        raise ValueError("base import must use the registered v2 plan")
    archive = Path(archive)
    if any(p.is_symlink() for p in (archive, *archive.parents)) or not archive.is_file():
        raise ValueError("base archive missing or aliased")
    if root == archive.resolve() or root in archive.resolve().parents:
        raise ValueError("immutable base archive must be outside new mirror")
    spec = plan["base_archive"]
    if archive.stat().st_size > MAX_ZIP_BYTES or run.sha(archive) != spec["zip_sha256"]:
        raise ValueError("base archive ZIP SHA/size mismatch")
    output = root / "research_inputs/base_archive_import.json"
    if output.exists():
        raise ValueError("base import receipt is immutable")
    imported = []
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        names = [item.filename for item in infos]
        if len(infos) > MAX_MEMBERS or len(names) != len(set(names)) or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("base archive duplicate members or size bound exceeded")
        for info in infos:
            relative = PurePosixPath(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if (relative.is_absolute() or ".." in relative.parts or "\\" in info.filename
                    or relative.as_posix() != info.filename or mode not in (0, 0o100000, 0o040000)
                    or info.flag_bits & 1):
                raise ValueError("unsafe base archive member")
        marker = json.loads(bundle.read(".dc20-profit-1000-research-root.json"))
        receipt_raw = bundle.read("collection_receipt.json")
        receipt = json.loads(receipt_raw)
        # The saved candidate is read for execution identity only, never outcomes.
        provenance = json.loads(bundle.read("research_results/candidate.json"))["execution_provenance"]
        if (marker.get("schema_version") != "dc20_profit_1000_research_mirror_v1"
                or marker.get("production_writes") is not False or marker.get("plan_sha256") != spec["plan_sha256"]
                or str(provenance.get("run_id")) != spec["run_id"] or provenance.get("run_commit") != spec["run_commit"]
                or hashlib.sha256(receipt_raw).hexdigest() != spec["collection_receipt_sha256"]
                or receipt.get("production_writes") is not False or receipt.get("existing_truth_overwritten") is not False):
            raise ValueError("base archive origin or receipt binding mismatch")
        for binding in plan["source_inputs"].values():
            if hashlib.sha256(bundle.read(binding["path"])).hexdigest() != binding["sha256"]:
                raise ValueError("base feature/calendar bytes changed")
        selected = []
        for name in names:
            match = re.fullmatch(r"data/market/raw/(20\d{2})/(20\d{6})/(daily|stk_limit)\.csv", name)
            if match:
                year, day, kind = match.groups()
                if year != day[:4]:
                    raise ValueError("base partition date/path conflict")
                selected.append((name, day, kind))
        if (sum(kind == "daily" for _, _, kind in selected) != spec["daily_partitions"]
                or sum(kind == "stk_limit" for _, _, kind in selected) != spec["limit_partitions"]):
            raise ValueError("base partition scope changed")
        daily_days = {day for _, day, kind in selected if kind == "daily"}
        if daily_days != {day for _, day, kind in selected if kind == "stk_limit"}:
            raise ValueError("base daily/limits dates differ")
        # Validate all destinations before the first copy; never overwrite.
        for name, _, _ in selected:
            destination = root / name
            if destination.exists() or any(p.is_symlink() for p in (destination, *destination.parents)):
                raise ValueError("base import target already exists or is aliased")
        for name, day, _ in sorted(selected):
            raw = bundle.read(name)
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(raw)
            settlement._market_rows(destination, day)
            imported.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    result = {"schema_version": "dc20_profit_1000_base_archive_import_v2",
              "base_archive": dict(spec), "plan_sha256": run.sha(run.plan_path("v2")),
              "source_files": imported, "imported_partitions": len(imported),
              "old_auction_and_minute_sources_imported": False, "old_outcome_labels_imported": False,
              "old_source_bytes_modified": False, "production_writes": False}
    run.write_json(output, result)
    return result
