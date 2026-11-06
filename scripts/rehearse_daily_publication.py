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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root, out):
    root = root.resolve(strict=True)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any((out/name).exists() for name in ('step-output.txt', 'step-env.txt', 'result.json')):
        raise ValueError('Use a fresh rehearsal output directory; prior control files exist')
    if (root / '_site').exists():
        raise ValueError('Use a fresh disposable checkout: _site already exists')
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
    args = parser.parse_args()
    value = run(args.root, args.out)
    print(json.dumps({k:v for k,v in value.items() if k not in {'account','revision'}}, ensure_ascii=False))
