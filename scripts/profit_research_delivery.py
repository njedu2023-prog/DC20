"""Bind research to settled source writers and accept the exact public dataset.

Read-only checks: no dispatch, model fitting, source repair or production writes.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

REPO = 'njedu2023-prog/DC20'
PREFIX = 'outputs/decision/profit_research/'
WRITERS = {
    '.github/workflows/run_primary_d_daily.yml',
    '.github/workflows/run_primary_profit_rankings.yml',
    '.github/workflows/run_primary_profit_forward_shadow.yml',
    '.github/workflows/research_candidate_natural_forward.yml',
    '.github/workflows/research_candidate_natural_observer.yml',
    '.github/workflows/research_candidate_natural_settlement.yml',
}


def get(url, token=None):
    headers = {'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return response.read()


def blocking_runs(runs):
    return [r['id'] for r in runs if r.get('path') in WRITERS and r.get('status') != 'completed']


def wait_for_writers():
    # The observer fans out settlement and publication. Bind only after its
    # sibling writers finish; the publisher's existing CAS guards stay intact.
    deadline = time.monotonic() + 600
    while True:
        payload = json.loads(get('https://api.github.com/repos/' + REPO +
                                 '/actions/runs?per_page=100', os.environ['GH_TOKEN']))
        active = blocking_runs(payload['workflow_runs'])
        if not active:
            return
        if time.monotonic() >= deadline:
            raise ValueError('RESEARCH_SOURCE_WRITERS_STILL_ACTIVE')
        print('Waiting for source writer runs:', active, flush=True)
        time.sleep(15)


def signal_date(root):
    index = json.loads((root / 'outputs/decision/candidate_profit_v1/index.json').read_bytes())
    primary = json.loads((root / 'outputs/decision/primary_d_runtime_index.json').read_bytes())
    day = index['latest_signal_date']
    if not re.fullmatch(r'20\d{6}', day) or primary['latest_signal_date'] != day:
        raise ValueError('RESEARCH_SOURCE_NOT_CURRENT_PRIMARY_D')
    return day


def validate_dataset(pointer, raw, day, source):
    if not re.fullmatch(r'versions/[0-9a-f]{64}\.json', pointer.get('path', '')):
        raise ValueError('UNSAFE_RESEARCH_VERSION_PATH')
    digest = hashlib.sha256(raw).hexdigest()
    if pointer.get('sha256') != digest or pointer['path'] != 'versions/' + digest + '.json':
        raise ValueError('RESEARCH_PUBLIC_FINGERPRINT_MISMATCH')
    doc = json.loads(raw)
    if pointer.get('source_revision') != source or doc.get('source_revision') != source:
        raise ValueError('RESEARCH_PUBLIC_SOURCE_MISMATCH')
    if pointer.get('as_of_date') != doc.get('as_of_date') or str(doc.get('as_of_date', '')) < day:
        raise ValueError('RESEARCH_PUBLIC_DATE_STALE')
    current = [d for d in doc.get('days', []) if d.get('date') == day]
    if len(current) != 1 or current[0].get('provenance') == 'SOURCE_ERROR':
        raise ValueError('RESEARCH_CURRENT_D_MISSING')
    return doc


def verify(root):
    day = os.environ['EXPECTED_RESEARCH_D']
    source = os.environ['EXPECTED_RESEARCH_SOURCE']
    head = os.environ['EXPECTED_RESEARCH_HEAD']
    local_pointer = (root / PREFIX / 'latest.json').read_bytes()
    pointer = json.loads(local_pointer)
    # Validate the path before reading or requesting it.
    if not re.fullmatch(r'versions/[0-9a-f]{64}\.json', pointer.get('path', '')):
        raise ValueError('UNSAFE_RESEARCH_VERSION_PATH')
    local_version = (root / PREFIX / pointer['path']).read_bytes()
    validate_dataset(pointer, local_version, day, source)
    base = 'https://njedu2023-prog.github.io/DC20/'
    suffix = '?research_accept=' + os.environ.get('GITHUB_RUN_ID', head)
    for attempt in range(12):
        try:
            revision = json.loads(get(base + 'revision.json' + suffix))
            if revision.get('head_sha') != head:
                raise ValueError('RESEARCH_PUBLIC_REVISION_NOT_EXACT')
            if get(base + PREFIX + 'latest.json' + suffix) != local_pointer:
                raise ValueError('RESEARCH_PUBLIC_POINTER_NOT_EXACT')
            public_version = get(base + PREFIX + pointer['path'] + suffix)
            if public_version != local_version:
                raise ValueError('RESEARCH_PUBLIC_VERSION_NOT_EXACT')
            doc = validate_dataset(pointer, public_version, day, source)
            result = {'status': 'PASS', 'signal_date': day, 'as_of_date': doc['as_of_date'],
                      'source_revision': source, 'public_head': head, 'dataset_sha256': pointer['sha256']}
            print(json.dumps(result))
            if os.environ.get('GITHUB_STEP_SUMMARY'):
                with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
                    stream.write('Research public acceptance: `' + json.dumps(result) + '`\n')
            return
        except (ValueError, OSError):
            if attempt == 11:
                raise
            time.sleep(10)


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'wait':
        wait_for_writers()
    elif command == 'bind':
        print('signal_date=' + signal_date(Path('.')))
    elif command == 'verify':
        verify(Path('.'))
    else:
        raise SystemExit('Unknown delivery check')
