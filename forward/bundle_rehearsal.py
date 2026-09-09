"""Compute from independently pinned inputs, preserving P0 before optional P1.

Natural scheduling here provides input provenance and a strict time window,
not forward admission: every output remains REPLAY/staging and cannot enter a
production ledger. Historical collection is an explicit separate CLI command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone

from .input_acceptance import REPOSITORY, WORKFLOW, UPSTREAMS, fetch_bytes, emit_job_outputs
from .rehearsal import _guard, _head, _json
from .schedule import read_calendar, aware
from .storage import encoded, compare_and_swap
from .trigger import bind_schedule


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pinned_json(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("an externally supplied SHA256 is required")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("input artifact must not follow a symlink")
    raw = path.read_bytes()
    if _sha(raw) != expected:
        raise ValueError("input artifact SHA256 mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input artifact must be an object")
    return value, raw


def _state(root):
    """Detect writes without opening old raw/pred/Action/statistics contents."""
    layout = {}
    for relative in ("data", "models", "outputs", "work"):
        base = root / relative
        paths = [base]
        if base.exists() and not base.is_symlink() and base.is_dir():
            # Never traverse a pre-existing legacy symlink. Its lstat identity
            # is monitored without making its target a P0 prerequisite.
            for directory, folders, files in os.walk(base, followlinks=False):
                paths.extend(Path(directory) / name for name in [*folders, *files])
        for path in paths:
            name = path.relative_to(root).as_posix()
            if not path.exists() and not path.is_symlink():
                layout[name] = None
                continue
            info = path.lstat()
            layout[name] = (info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    tracked = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=no"], text=True)
    return _head(root), tracked, layout


def _unchanged(root, before):
    if _state(root) != before:
        raise ValueError("inference changed the source checkout or protected repository files")


def _migration(root, output):
    root, output = _guard(Path(root), Path(output))
    config = _json(root / "forward/config.json")
    if config.get("phase") != "MIGRATION_ACCEPTANCE" or config.get("start_signal_date") is not None:
        raise ValueError("bundle inference requires an inactive migration epoch")
    return root, output


def verify_natural_evidence(root, evidence, *, env=None, now=None):
    """Recheck same-run input provenance and the original admission window.

The input job authenticated created_at with GitHub's run API. Same-workflow
artifact routing and the job-output SHA pin supply trust here; this pure check
is not a substitute for those boundaries and does not permit ledger admission.
"""
    env = os.environ if env is None else env
    if evidence.get("origin") != "NATURAL_SCHEDULE_STAGING":
        raise ValueError("natural input evidence required")
    receipt = evidence.get("acceptance_receipt")
    if not isinstance(receipt, dict) or _sha(encoded(receipt)) != evidence.get("acceptance_receipt_sha256"):
        raise ValueError("natural input receipt pin mismatch")
    identity, stored_gate = receipt.get("identity", {}), receipt.get("gate", {})
    if (receipt.get("schema_version") != "dc20_forward_input_acceptance_v1"
            or receipt.get("status") != "INPUTS_VALIDATED_NOT_PRODUCTION"
            or receipt.get("read_only") is not True
            or any(receipt.get(key) is not False for key in
                   ("production_activated", "inference_performed", "ledger_written", "published_list"))
            or stored_gate.get("status") != "ELIGIBLE"
            or receipt.get("bundle_sha256") != evidence.get("manifest_sha256")):
        raise ValueError("not a complete non-producing exact-D input acceptance")
    if (env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_EVENT_NAME") != "schedule"
            or env.get("GITHUB_REPOSITORY") != REPOSITORY or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/{WORKFLOW}@refs/heads/main"
            or env.get("GITHUB_SHA") != _head(root) or env.get("GITHUB_WORKFLOW_SHA") != _head(root)
            or identity.get("source_revision") != _head(root)
            or env.get("GITHUB_RUN_ATTEMPT") != "1" or type(identity.get("run_attempt")) is not int
            or identity.get("run_attempt") != 1 or type(identity.get("run_id")) is not int
            or str(identity.get("run_id")) != env.get("GITHUB_RUN_ID")
            or identity.get("event_name") != "schedule" or identity.get("workflow_path") != WORKFLOW):
        raise ValueError("input artifact does not belong to this first natural run/source SHA")
    event = _json(Path(env["GITHUB_EVENT_PATH"]))
    if event.get("schedule") != identity.get("schedule"):
        raise ValueError("natural input schedule differs from the current event")
    config = _json(root / "forward/config.json")
    dates = read_calendar(root / config["calendar_path"], config["calendar_sha256"])
    gate = bind_schedule("schedule", identity["schedule"], identity["run_created_at_utc"], now or _utc(), dates)
    if (gate["status"] != "ELIGIBLE" or any(gate[key] != stored_gate.get(key) for key in
            ("signal_date", "exec_date", "exit_date", "slot_utc", "slot_kind", "admission_deadline_utc"))
            or receipt.get("finished_at_utc") != stored_gate.get("checked_at_utc")
            or not aware(identity["run_created_at_utc"]) <= aware(receipt["started_at_utc"]) <= aware(receipt["finished_at_utc"]) <= aware(gate["checked_at_utc"])):
        raise ValueError("input acceptance date/window/timestamps do not match the original slot")
    return gate


def _input_evidence(root, bundle, manifest_sha, acceptance=None, receipt_sha=None, *, env=None, now=None):
    manifest, _ = _pinned_json(bundle / "manifest.json", manifest_sha)
    evidence = {"origin": "HISTORICAL_PINNED_INPUT_REPLAY", "manifest_sha256": manifest_sha,
                "signal_date": manifest.get("signal_date"), "forward_ledger_eligible": False}
    if (acceptance is None) != (receipt_sha is None):
        raise ValueError("natural acceptance directory and receipt SHA must be provided together")
    if acceptance is not None:
        if bundle.resolve() != (acceptance / "bundle").resolve():
            raise ValueError("natural inference must consume the accepted bundle directory")
        receipt, raw = _pinned_json(acceptance / "receipt.json", receipt_sha)
        if raw != encoded(receipt):
            raise ValueError("natural acceptance receipt must use its canonical writer encoding")
        evidence.update(origin="NATURAL_SCHEDULE_STAGING", acceptance_receipt=receipt,
                        acceptance_receipt_sha256=receipt_sha)
        gate = verify_natural_evidence(root, evidence, env=env, now=now)
        if (any(manifest.get(key) != gate[key] for key in ("signal_date", "exec_date", "exit_date"))
                or manifest.get("source_commit") != receipt["identity"]["source_revision"]
                or receipt.get("sources") != {UPSTREAMS[0]: manifest.get("pred_commit"), UPSTREAMS[1]: manifest.get("market_commit")}
                or not aware(receipt["started_at_utc"]) <= aware(manifest["collected_at_utc"]) <= aware(receipt["finished_at_utc"])):
            raise ValueError("input manifest differs from natural receipt date/source binding")
    return evidence


def _publish(output, files, *, kind, revision, date, evidence, performed, before_receipt=None):
    hashes = {name: compare_and_swap(output / name, value, None) for name, value in files.items()}
    if before_receipt is not None:
        evidence = {**evidence, "inference_gate": before_receipt()}
    receipt = {"schema_version": "dc20_forward_rehearsal_receipt_v1", "kind": kind,
               "signal_date": date, "source_revision": revision, "files": hashes,
               "generation_mode": "REPLAY", "computation_completed": True,
               "inference_performed": performed, "production_enabled": False,
               "forward_ledger_eligible": False, "formal_trade_count": 0,
               "input_evidence": evidence}
    compare_and_swap(output / "receipt.json", receipt, None)
    return receipt


def compute_promotion(root, bundle, manifest_sha, output, *, acceptance=None, receipt_sha=None, env=None):
    root, output = _migration(root, output)
    current_env = os.environ if env is None else env
    if current_env.get("GITHUB_EVENT_NAME") == "schedule" and acceptance is None:
        raise ValueError("a natural run cannot downgrade to unbound historical input replay")
    bundle = Path(bundle)
    acceptance = Path(acceptance) if acceptance is not None else None
    evidence = _input_evidence(root, bundle, manifest_sha, acceptance, receipt_sha, env=env)
    before = _state(root)
    from .promotion import compute_promotion_from_inputs
    primary = compute_promotion_from_inputs(root, bundle, expected_manifest_sha256=manifest_sha,
                                           generated_at_utc=_utc(), generation_mode="REPLAY")
    if (primary["day"]["source"].get("input_manifest_sha256") != manifest_sha
            or primary["day"]["signal_date"] != evidence["signal_date"]):
        raise ValueError("computed P0 does not bind the requested independent input manifest")
    _unchanged(root, before)
    if evidence["origin"] == "NATURAL_SCHEDULE_STAGING":
        evidence["inference_gate"] = verify_natural_evidence(root, evidence, env=env)
    return _publish(output, {"primary.json": primary, "promotion.json": primary["day"]},
                    kind="PROMOTION_INFERENCE", revision=before[0], date=primary["day"]["signal_date"],
                    evidence=evidence, performed=bool(primary["day"]["rows"]),
                    before_receipt=(lambda: verify_natural_evidence(root, evidence, env=env))
                    if evidence["origin"] == "NATURAL_SCHEDULE_STAGING" else None)


def compute_profit(root, primary_directory, output, *, primary_receipt_sha=None, env=None):
    root, output = _migration(root, output)
    pinned_receipt, raw_receipt = _pinned_json(Path(primary_directory) / "receipt.json", primary_receipt_sha)
    if raw_receipt != encoded(pinned_receipt):
        raise ValueError("P0 receipt must use its canonical writer encoding")
    p0_receipt = pinned_receipt
    if (p0_receipt.get("kind") != "PROMOTION_INFERENCE"
            or p0_receipt.get("generation_mode") != "REPLAY"
            or p0_receipt.get("forward_ledger_eligible") is not False
            or p0_receipt.get("production_enabled") is not False
            or p0_receipt.get("computation_completed") is not True
            or set(p0_receipt.get("files", {})) != {"promotion.json", "primary.json"}):
        raise ValueError("not a complete rehearsal promotion receipt")
    # Parse the exact bytes whose digest was checked, never reopen a file
    # between verification and consumption.
    primary, _ = _pinned_json(Path(primary_directory) / "primary.json", p0_receipt["files"]["primary.json"])
    day, _ = _pinned_json(Path(primary_directory) / "promotion.json", p0_receipt["files"]["promotion.json"])
    if primary.get("day") != day or day.get("signal_date") != p0_receipt.get("signal_date"):
        raise ValueError("promotion day or receipt date does not match the runtime bundle")
    evidence = p0_receipt.get("input_evidence")
    if (not isinstance(evidence, dict) or evidence.get("origin") not in
            {"HISTORICAL_PINNED_INPUT_REPLAY", "NATURAL_SCHEDULE_STAGING"}
            or evidence.get("forward_ledger_eligible") is not False
            or evidence.get("signal_date") != primary["day"]["signal_date"]
            or evidence.get("manifest_sha256") != primary["day"]["source"].get("input_manifest_sha256")
            or p0_receipt["source_revision"] != _head(root)):
        raise ValueError("P1 requires this revision's independently recomputed input-bundle P0")
    current_env = os.environ if env is None else env
    if current_env.get("GITHUB_EVENT_NAME") == "schedule" and evidence["origin"] != "NATURAL_SCHEDULE_STAGING":
        raise ValueError("a natural P1 cannot downgrade to unbound historical replay")
    if evidence["origin"] == "NATURAL_SCHEDULE_STAGING":
        gate = verify_natural_evidence(root, evidence, env=env)
        source = primary["day"]["source"]
        expected_sources = evidence["acceptance_receipt"]["sources"]
        if (any(primary["day"].get(key) != gate[key] for key in ("signal_date", "exec_date", "exit_date"))
                or source.get("candidate", {}).get("resolved_commit") != expected_sources.get(UPSTREAMS[0])
                or source.get("market", {}).get("resolved_commit") != expected_sources.get(UPSTREAMS[1])):
            raise ValueError("P0 dates/upstream sources differ from its accepted natural inputs")
    before = _state(root)
    from .profit import infer_profit
    profit = infer_profit(root, primary)
    _unchanged(root, before)
    if evidence["origin"] == "NATURAL_SCHEDULE_STAGING":
        evidence = {**evidence, "inference_gate": verify_natural_evidence(root, evidence, env=env)}
    profit["rehearsal_promotion_receipt_sha256"] = _sha(encoded(p0_receipt))
    return _publish(output, {"profit.json": profit}, kind="PROFIT_INFERENCE", revision=before[0],
                    date=primary["day"]["signal_date"], evidence=evidence, performed=bool(profit["rows"]),
                    before_receipt=(lambda: verify_natural_evidence(root, evidence, env=env))
                    if evidence["origin"] == "NATURAL_SCHEDULE_STAGING" else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="historical fixed-source input audit; never forward freeze")
    for argument in ("signal-date", "pred-commit", "market-commit"):
        collect.add_argument("--" + argument, required=True)
    collect.add_argument("--output", type=Path, required=True)
    promotion = commands.add_parser("promotion")
    promotion.add_argument("--bundle", type=Path, required=True)
    promotion.add_argument("--manifest-sha256", required=True)
    promotion.add_argument("--acceptance", type=Path)
    promotion.add_argument("--acceptance-receipt-sha256")
    promotion.add_argument("--output", type=Path, required=True)
    profit = commands.add_parser("profit")
    profit.add_argument("--primary", type=Path, required=True)
    profit.add_argument("--primary-receipt-sha256", required=True)
    profit.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "collect":
        from .inputs import collect_inputs
        root, output = _migration(args.root, args.output)
        collect_inputs(root, output, args.signal_date, args.pred_commit, args.market_commit, fetch=fetch_bytes)
        digest = _sha((output / "manifest.json").read_bytes())
        result = {"status": "HISTORICAL_INPUT_AUDIT_ONLY", "manifest_sha256": digest}
        emit_job_outputs(result)
    elif args.command == "promotion":
        result = compute_promotion(args.root, args.bundle, args.manifest_sha256, args.output,
                                   acceptance=args.acceptance, receipt_sha=args.acceptance_receipt_sha256)
        emit_job_outputs({"receipt_sha256": _sha(encoded(result))})
    else:
        result = compute_profit(args.root, args.primary, args.output, primary_receipt_sha=args.primary_receipt_sha256)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
