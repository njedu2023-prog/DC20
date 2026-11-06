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
    delivery = revision.get("candidate_profit_delivery")
    if delivery is not None:
        if (delivery.get("schema_version") != "dc20_candidate_delivery_isolation_v1"
            or delivery.get("status") not in ("PARTIAL", "UNAVAILABLE")
            or delivery.get("signal_date") != revision.get("signal_date")
            or delivery.get("summary_status") != "UNAVAILABLE"
            or delivery.get("new_freeze_created") is not False
            or delivery.get("historical_results_rewritten") is not False
            or any(key in revision for key in ("candidate_profit_summary_url", "candidate_profit_summary_sha256",
                                                "profit_paper_account_url", "profit_paper_account_sha256"))):
            raise ValueError("invalid candidate delivery isolation envelope")
        relatives = ["index.html", "decision.html"]
        if delivery["status"] == "PARTIAL":
            for role in ("activation", "index"):
                relative = revision[f"candidate_profit_{role}_url"]
                if hashlib.sha256(safe_read(site, relative)).hexdigest() != revision[f"candidate_profit_{role}_sha256"]:
                    raise ValueError("isolated candidate Pages binding changed")
                relatives.append(relative)
            index = json.loads(safe_read(site, PREFIX + "index.json"))
            relatives += [entry["path"] for entry in index["days"]]
        for relative in relatives:
            if fetch_bytes(relative) != safe_read(site, relative):
                raise ValueError("candidate Pages bytes have not converged: " + relative)
        return {"status": delivery["status"], "current_ranking_status": delivery["current_ranking_status"],
                "summary_status": "UNAVAILABLE", "verified_files": len(relatives) + 1}
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


def _write(site, relative, raw):
    target = site / relative
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise ValueError('candidate Pages target is a symlink')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def materialize_delivery(repo_root, site_root):
    """Isolate historical display failures without issuing new ranking authority.

    The healthy path keeps the original exact bytes. On failure, only existing
    indexed, frozen days that independently rebind to their original snapshot
    and P0 can be displayed. No day is recalculated, retimed or added to Git.
    A corrupt activation/index quarantines all profit display, never the P0.
    """
    root, site = Path(repo_root).resolve(strict=True), Path(site_root).resolve(strict=True)
    try:
        result = materialize(root, site)
        revision = json.loads(safe_read(site, 'revision.json'))
        if revision.pop('candidate_profit_delivery', None) is not None:
            auxiliary = revision.get('auxiliary_delivery', {})
            if auxiliary.get('profit_paper_account', {}).get('reason') == 'FORMAL_HISTORY_UNAVAILABLE':
                auxiliary.pop('profit_paper_account')
            _write(site, 'revision.json', projection.encoded(revision))
        return {**result, 'summary_ready': True, 'delivery_status': 'READY'}
    except (ValueError, KeyError, TypeError, OSError) as error:
        original_error = type(error).__name__ + ': ' + str(error)

    revision = json.loads(safe_read(site, 'revision.json'))
    signal = revision.get('signal_date')
    projection._date(signal)  # The independently built P0 revision is mandatory.
    # Clear every old candidate/account binding before any fallback. Files from
    # a previous site staging must not remain reachable as a current ledger.
    for key in list(revision):
        if key.startswith(('candidate_profit_', 'profit_paper_account_')):
            del revision[key]
    target_root = site / PREFIX
    if target_root.is_symlink():
        raise ValueError('candidate Pages target is a symlink')
    if target_root.exists():
        for target in target_root.iterdir():
            if target.is_symlink() or not target.is_file():
                raise ValueError('unexpected candidate Pages staging member')
            target.unlink()
    (site / 'outputs/decision/profit_paper_account/account.json').unlink(missing_ok=True)
    delivery = {'schema_version': 'dc20_candidate_delivery_isolation_v1',
                'signal_date': signal, 'status': 'UNAVAILABLE',
                'current_ranking_status': 'UNAVAILABLE', 'summary_status': 'UNAVAILABLE',
                'reason': 'FORMAL_HISTORY_VALIDATION_FAILED', 'excluded_days': [],
                'new_freeze_created': False, 'historical_results_rewritten': False}
    revision['candidate_profit_delivery'] = delivery
    revision.setdefault('auxiliary_delivery', {})['profit_paper_account'] = {
        'status': 'UNAVAILABLE', 'reason': 'FORMAL_HISTORY_UNAVAILABLE'}
    available = []
    config = None
    try:
        config_raw = safe_read(root, CONFIG)
        config = json.loads(config_raw)
        projection.validate_activation(config)
        index_raw = safe_read(root, PREFIX + 'index.json')
        index = projection.validate_public_index(index_raw,
            expected_sha256=hashlib.sha256(index_raw).hexdigest(), activation=config)
        delivery['source_index_sha256'] = hashlib.sha256(index_raw).hexdigest()
        for entry in index['days']:
            try:
                raw = safe_read(root, entry['path'])
                snapshot = safe_read(root, projection.SNAPSHOT_PREFIX + 'day_' + entry['signal_date'] + '.json')
                p0 = safe_read(root, 'outputs/decision/three_rank_top10_' + entry['signal_date'] + '.json')
                day = projection.validate_persisted_day(raw, expected_sha256=entry['sha256'],
                    snapshot_raw=snapshot, current_p0_raw=p0, activation=config)
                if (day['signal_date'] != entry['signal_date'] or
                    day['snapshot_file_sha256'] != entry['snapshot_file_sha256'] or
                    day['p0_file_sha256'] != entry['p0_file_sha256']):
                    raise ValueError('candidate Pages day identity changed')
                _write(site, entry['path'], raw)
                available.append(entry)
            except (ValueError, KeyError, TypeError, OSError):
                delivery['excluded_days'].append({'signal_date': entry['signal_date'],
                                                  'reason': 'FROZEN_DAY_NOT_VERIFIABLE'})
        # This is a disclosed display subset of an existing index, not a new
        # formal publication. Retain original source/version timestamps and SHA
        # entries; never serialize or reconstruct the original daily bytes.
        view = dict(index, days=available, latest_signal_date=available[-1]['signal_date'] if available else None)
        view['status'] = ('DISABLED' if not config['enabled'] else
                          'ACTIVE_FORWARD_VALIDATION' if available else 'ACTIVE_WAITING_FIRST_NATURAL_D')
        view_raw = projection.encoded(view)
        projection.validate_public_index(view_raw, expected_sha256=hashlib.sha256(view_raw).hexdigest(), activation=config)
        _write(site, CONFIG, config_raw)
        _write(site, PREFIX + 'index.json', view_raw)
        revision.update(candidate_profit_activation=projection.activation_identity(config),
            candidate_profit_activation_url=CONFIG,
            candidate_profit_activation_sha256=hashlib.sha256(config_raw).hexdigest(),
            candidate_profit_index_url=PREFIX + 'index.json',
            candidate_profit_index_sha256=hashlib.sha256(view_raw).hexdigest())
        delivery['status'] = 'PARTIAL'
        delivery['current_ranking_status'] = ('READY' if any(e['signal_date'] == signal for e in available)
            else 'UNAVAILABLE' if any(e['signal_date'] == signal for e in index['days']) else 'PENDING')
    except (ValueError, KeyError, TypeError, OSError):
        # Invalid config/index cannot license an invented day or legacy fallback.
        delivery['reason'] = 'ACTIVATION_OR_INDEX_NOT_VERIFIABLE'
    _write(site, 'revision.json', projection.encoded(revision))
    return {'status': delivery['status'], 'days': len(available),
            'enabled': revision.get('candidate_profit_activation', {}).get('enabled'),
            'summary_ready': False, 'delivery_status': delivery['status'],
            'current_ranking_status': delivery['current_ranking_status'],
            'diagnostic': original_error}
