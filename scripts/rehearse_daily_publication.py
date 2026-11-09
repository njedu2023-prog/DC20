#!/usr/bin/env python3
"""Execute the actual Pages build steps in a disposable checkout, never deploy.

This measures historical computation/publication preparation, not upstream job
runtime or a newly authorized natural freeze. No GitHub token is needed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import yaml


STEPS = (
    'Validate frozen Decision trust root before projection',
    'Refresh primary observation projection from exact deployment sources',
    'Load explicit daily delivery isolation policy',
    'Validate optional legacy-profit relative research chain',
    'Build isolated DC2.0 Decision site',
    'Build independent compact statistics window',
    'Bind active candidate ranking and independent versioned summary',
    'Generate and bind simulated account with the main publication',
)

# Explicit data surfaces only. Source code, workflows, model/profile files and
# registration contracts remain those of the code checkout under test.
BUSINESS_DIRECTORIES = (
    'data',
    'outputs',
    'work/profit_1000_upgrade/candidate_natural_forward',
    'work/profit_1000_upgrade/candidate_natural_evidence',
    'work/profit_1000_upgrade/candidate_natural_journal',
)
CODE_SUFFIXES = {'.py', '.pyc', '.js', '.cjs', '.mjs', '.ts', '.tsx', '.jsx',
                 '.sh', '.bash', '.zsh', '.ps1', '.rb', '.pl', '.php', '.go',
                 '.rs', '.c', '.h', '.cpp', '.java', '.wasm'}


def checked_root(value):
    path = Path(value).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Aliased rehearsal root is forbidden')
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Rehearsal root must be a directory')
    return root


def stage_business_inputs(root, source_root, expected_day, expected_source_head=None):
    """Replace only fixture data in an already disposable code checkout.

    Replacement, rather than merging, prevents newly committed D data or a new
    required input from leaking into a fixed historical comparison. A new input
    absent from the fixture must fail in the normal build/contract validation.
    """
    root, source = checked_root(root), checked_root(source_root)
    if root == source or root in source.parents or source in root.parents:
        raise ValueError('Fixture and disposable code roots must be disjoint')
    if not re.fullmatch(r'[0-9]{8}', str(expected_day)):
        raise ValueError('Explicit historical fixture signal date is required')
    source_head = None
    if expected_source_head is not None:
        if not re.fullmatch(r'[0-9a-f]{40}', expected_source_head):
            raise ValueError('Immutable fixture commit is required')
        source_head = subprocess.check_output(
            ['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if source_head != expected_source_head:
            raise ValueError('Fixture checkout does not match the immutable commit')
        dirty = subprocess.check_output(
            ['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all', '--ignored=matching',
             '--', *BUSINESS_DIRECTORIES], text=True)
        if dirty:
            raise ValueError('Fixture business inputs are not the committed bytes')
    code_pairs = []
    for relative in BUSINESS_DIRECTORIES:
        for checkout, other in ((source, root), (root, source)):
            directory = checkout / relative
            # Validate every ancestor too: rglob must never traverse an aliased work/.
            for parent in (directory, *directory.parents):
                if parent == checkout:
                    break
                if parent.is_symlink():
                    raise ValueError('Fixture business paths may not be symlinks: '+relative)
            if not directory.exists():
                if checkout == source and relative in ('data', 'outputs'):
                    raise ValueError('Required fixture directory missing: '+relative)
                continue
            if not directory.is_dir():
                raise ValueError('Fixture business path must be a directory: '+relative)
            for path in directory.rglob('*'):
                if path.is_symlink():
                    raise ValueError('Fixture business paths may not be symlinks: '+str(path.relative_to(checkout)))
                if path.is_file() and (path.suffix.lower() in CODE_SUFFIXES or path.stat().st_mode & 0o111):
                    counterpart = other / path.relative_to(checkout)
                    code_pairs.append((path, counterpart, path.relative_to(checkout)))
    # Read counterpart bytes only after all data trees and their ancestors have
    # been checked; do not follow a symlink hidden in the opposite checkout.
    for path, counterpart, relative in code_pairs:
        if (not counterpart.is_file() or path.read_bytes() != counterpart.read_bytes()
                or path.stat().st_mode & 0o111 != counterpart.stat().st_mode & 0o111):
            raise ValueError('Fixture must not replace code: '+str(relative))
    pointer = json.loads((source/'outputs/decision/primary_d_runtime_index.json').read_bytes())
    if pointer.get('latest_signal_date') != expected_day:
        raise ValueError('Fixture pointer does not match the explicit historical D')
    copied_files = 0
    for relative in BUSINESS_DIRECTORIES:
        destination, origin = root/relative, source/relative
        if destination.exists():
            shutil.rmtree(destination)
        if origin.exists():
            shutil.copytree(origin, destination)
            copied_files += sum(path.is_file() for path in destination.rglob('*'))
    return {'signal_date': expected_day, 'source_head': source_head,
            'directories': list(BUSINESS_DIRECTORIES), 'copied_files': copied_files}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root, out, *, source_root=None, expected_signal_date=None, expected_source_head=None):
    root = checked_root(root)
    out = out.resolve()
    if source_root is not None:
        source_root = checked_root(source_root)
        if out == source_root or source_root in out.parents:
            raise ValueError('Rehearsal output must be outside the fixed fixture')
    elif expected_signal_date is not None or expected_source_head is not None:
        raise ValueError('Fixture expectations require an explicit source root')
    out.mkdir(parents=True, exist_ok=True)
    if any((out/name).exists() for name in ('step-output.txt', 'step-env.txt', 'result.json')):
        raise ValueError('Use a fresh rehearsal output directory; prior control files exist')
    if (root / '_site').exists():
        raise ValueError('Use a fresh disposable checkout: _site already exists')
    fixture = (stage_business_inputs(root, source_root, expected_signal_date, expected_source_head)
               if source_root is not None else None)
    workflow = yaml.safe_load((root / '.github/workflows/deploy_dc20_pages.yml').read_text())
    steps = {s.get('name'): s for s in workflow['jobs']['deploy']['steps'] if s.get('name') in STEPS}
    pointer = json.loads((root / 'outputs/decision/primary_d_runtime_index.json').read_bytes())
    day = pointer['latest_signal_date']
    protected = [root / pointer[k] for k in ('latest_receipt_url', 'latest_runtime_features_url',
                'latest_three_rank_json_url', 'latest_three_rank_csv_url')]
    protected += list((root / 'models').rglob('*'))
    protected += list((root / 'work/profit_1000_upgrade/candidate_natural_forward').rglob('*'))
    protected = {str(p.relative_to(root)): digest(p) for p in protected if p.is_file()}
    business_inputs = {path: value for path, value in protected.items()
        if path != 'models/decision_model_freeze.json'
        and not path.startswith('models/decision_source_surface_review_')}
    for directory in ('data/market/raw', 'outputs/decision/candidate_profit_v1',
                      'work/profit_1000_upgrade/candidate_natural_evidence'):
        for path in (root/directory).rglob('*'):
            if path.is_file():
                business_inputs[str(path.relative_to(root))] = digest(path)
    input_signature = hashlib.sha256(json.dumps(business_inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    env = os.environ.copy()
    env.pop('LEGACY_PROFIT_ARCHIVED', None)
    # No publishing credentials are passed into the isolated build.
    for key in list(env):
        if 'TOKEN' in key or 'SECRET' in key:
            del env[key]
    env.update(PYTHONPATH=str(root)+os.pathsep+str(root/'src'),
               GITHUB_REPOSITORY='njedu2023-prog/DC20',
               GITHUB_RUN_ID=os.environ.get('GITHUB_RUN_ID', '1'),
               GITHUB_OUTPUT=str(out/'step-output.txt'),
               GITHUB_ENV=str(out/'step-env.txt'),
               GITHUB_STEP_SUMMARY=str(out/'summary.txt'),
               DC2_SYSTEM_NAME='DC2.0', MAX_REPORT_AGE_DAYS='1',
               PUBLISH_LEGACY_PROFIT_RELATIVE='false')
    result = {'schema_version':'dc20_isolated_publication_rehearsal_v1',
              'signal_date':day, 'historical_replay_only':True,
              'business_input_inventory_sha256':input_signature,
              'natural_freeze_created':False, 'public_deploy_performed':False,
              'workflow_sha256':digest(root/'.github/workflows/deploy_dc20_pages.yml'),
              'steps':[], 'status':'RUNNING'}
    if fixture is not None:
        result['source_fixture'] = fixture
    start = time.perf_counter()
    try:
        for name in STEPS:
            if name not in steps:
                if name == 'Load explicit daily delivery isolation policy':
                    continue
                raise ValueError('Required workflow step missing: '+name)
            if name == 'Validate optional legacy-profit relative research chain' and env.get('LEGACY_PROFIT_ARCHIVED') == 'true':
                result['steps'].append({'name':name, 'status':'ARCHIVED_NOT_ON_MAIN_PATH', 'seconds':0})
                continue
            step = steps[name]
            if 'run' not in step:
                raise ValueError('Only explicit shell build steps are permitted')
            local_env = env.copy()
            for key, value in step.get('env', {}).items():
                if '${{' not in str(value):
                    local_env[key] = str(value)
            index = len(result['steps'])
            script = out/f'{index:02d}.sh'
            script.write_text(step['run'])
            log = out/f'{index:02d}.log'
            before = time.perf_counter()
            with log.open('w') as handle:
                proc = subprocess.run(['bash', '-e', '-o', 'pipefail', str(script)],
                    cwd=root, env=local_env, stdout=handle, stderr=subprocess.STDOUT, timeout=900)
            record = {'name':name, 'seconds':round(time.perf_counter()-before, 6),
                      'returncode':proc.returncode, 'log':log.name}
            result['steps'].append(record)
            if proc.returncode:
                raise ValueError('Rehearsal step failed: '+name+'; see '+str(log))
            env_file = out/'step-env.txt'
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if '=' in line:
                        key, value = line.split('=', 1)
                        env[key] = value
            if name == 'Validate optional legacy-profit relative research chain':
                values = dict(line.split('=', 1) for line in (out/'step-output.txt').read_text().splitlines() if '=' in line)
                env['PUBLISH_LEGACY_PROFIT_RELATIVE'] = values.get('publish', 'false')
        revision = json.loads((root/'_site/revision.json').read_bytes())
        if revision.get('signal_date') != day:
            raise ValueError('Built publication did not retain exact D')
        result['revision'] = revision
        account_path = revision.get('profit_paper_account_url')
        if not account_path:
            raise ValueError('Healthy rehearsal must produce the simulated account')
        result['account'] = json.loads((root/'_site'/account_path).read_bytes())
        changed = [rel for rel, sha in protected.items() if digest(root/rel) != sha]
        if changed:
            raise ValueError('Protected model/freeze bytes changed: '+repr(changed))
        result['protected_files_unchanged'] = len(protected)
        result['status'] = 'PASS'
    except Exception as exc:
        result['status'] = 'FAIL'
        result['error'] = str(exc)
        raise
    finally:
        result['total_seconds'] = round(time.perf_counter()-start, 6)
        (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--expected-signal-date')
    parser.add_argument('--expected-source-head')
    args = parser.parse_args()
    value = run(args.root, args.out, source_root=args.source_root,
                expected_signal_date=args.expected_signal_date, expected_source_head=args.expected_source_head)
    print(json.dumps({k:v for k,v in value.items() if k not in {'account','revision'}}, ensure_ascii=False))
