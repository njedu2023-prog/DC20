"""Delivery isolation only; never changes models, frozen selections or returns."""
from pathlib import Path
import hashlib
import json
import re
import shutil

POLICY_PATH = 'models/decision_daily_delivery_policy_v1.json'
ARCHIVE_ROOTS = (
    'outputs/decision/legacy_profit_relative_research',
    'outputs/decision/executable_profit_research',
    'data/decision_executable_profit/forward',
)

ARCHIVE_CONTRACTS = (
    'models/decision_primary_profit_research_contract.json',
    'models/decision_primary_profit_forward_shadow_bridge_contract.json',
    'models/decision_executable_profit_research_projection_contract.json',
)


def load_policy(root):
    path = Path(root) / POLICY_PATH
    if not path.exists():
        return None  # Historical fixtures / code before the explicit cutover.
    if path.is_symlink() or not path.is_file():
        raise ValueError('DELIVERY_POLICY_UNSAFE')
    policy = json.loads(path.read_bytes())
    if (set(policy) != {'schema_version', 'legacy_profit_mode',
                        'archive_through_signal_date', 'effective_new_signal_date',
                        'auxiliary_failure_policy'}
        or policy['schema_version'] != 'dc20_daily_delivery_policy_v1'
        or policy['legacy_profit_mode'] != 'ARCHIVED'
        or policy['auxiliary_failure_policy'] != 'ISOLATE'
        or any(not re.fullmatch(r'20\d{6}', policy[k]) for k in
               ('archive_through_signal_date', 'effective_new_signal_date'))
        or policy['effective_new_signal_date'] <= policy['archive_through_signal_date']):
        raise ValueError('DELIVERY_POLICY_INVALID')
    return policy


def legacy_archived(root):
    return load_policy(root) is not None


def stage_legacy_archive(root, site):
    """Keep original historical URLs/bytes without presenting them as current.

    Missing archive files stay missing; no restoration or result is fabricated.
    Every copied file is byte-checked. Archive failures are separately reported.
    """
    root, site = Path(root), Path(site)
    policy = load_policy(root)
    if not policy:
        return None
    files, failures = [], []
    for relative in (*ARCHIVE_ROOTS, *ARCHIVE_CONTRACTS):
        source = root / relative
        if source.is_symlink():
            failures.append({'path': relative, 'reason': 'UNSAFE_SYMLINK'})
            continue
        if not source.exists():
            failures.append({'path': relative, 'reason': 'MISSING_ARCHIVE'})
            continue
        items = sorted(source.rglob('*')) if source.is_dir() else [source]
        for item in items:
            rel = item.relative_to(root).as_posix()
            if item.is_symlink():
                failures.append({'path': rel, 'reason': 'UNSAFE_SYMLINK'})
                continue
            if not item.is_file():
                continue
            try:
                raw = item.read_bytes()
                target = site / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                if target.read_bytes() != raw:
                    raise OSError('COPY_MISMATCH')
                files.append({'path': rel, 'sha256': hashlib.sha256(raw).hexdigest()})
            except OSError as exc:
                failures.append({'path': rel, 'reason': str(exc)})
    target = site / POLICY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / POLICY_PATH, target)
    manifest = {'schema_version': 'dc20_legacy_archive_manifest_v1',
                'status': 'ARCHIVED' if not failures else 'ARCHIVE_INCOMPLETE',
                'policy': policy, 'files': files, 'failures': failures,
                'used_for_current_rankings': False}
    destination = site / 'outputs/decision/legacy_profit_archive_manifest.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    return manifest


def mark_auxiliary_unavailable(site, component, reason):
    """Fail only a named auxiliary surface; never retain stale current bindings."""
    site = Path(site)
    path = site / 'revision.json'
    revision = json.loads(path.read_bytes())
    if component == 'compact_statistics_window':
        prefixes = ('compact_statistics_window_', 'profit_paper_account_', 'next_day_statistics_')
        artifacts = ('outputs/decision/compact_statistics_window.json',
                     'outputs/decision/profit_paper_account/account.json',
                     'outputs/decision/next_day_statistics.json')
    elif component == 'profit_paper_account':
        prefixes = ('profit_paper_account_',)
        artifacts = ('outputs/decision/profit_paper_account/account.json',)
    elif component == 'next_day_statistics':
        prefixes = ('next_day_statistics_',)
        artifacts = ('outputs/decision/next_day_statistics.json',)
    else:
        raise ValueError('UNKNOWN_AUXILIARY_COMPONENT')
    for key in list(revision):
        if key.startswith(prefixes):
            del revision[key]
    for relative in artifacts:
        (site / relative).unlink(missing_ok=True)
    revision.setdefault('auxiliary_delivery', {})[component] = {
        'status': 'UNAVAILABLE', 'reason': str(reason)}
    path.write_text(json.dumps(revision, ensure_ascii=False, indent=2)+'\n')
