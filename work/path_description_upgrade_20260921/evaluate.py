"""Read-only replay of the independent display overlay; not a predictive backtest."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess


def evaluate(html_path, records_path, node):
    html = Path(html_path).read_text()
    function = re.search(r'^    function verifiedPathDisplay\(.*?^    }', html, re.M | re.S).group()
    raw = Path(records_path).read_bytes()
    records = json.loads(raw)
    rows = []
    labels = {}
    for record in records:
        sessions = record['sessions']
        first, previous, last = sessions[0], sessions[-2], sessions[-1]
        score = lambda s: sum(a*b for a,b in zip(s['components'], (.25,.3,.2,.25)))
        old = record['baseline']
        labels[old] = old  # Only the legacy identity check; classification never uses this label.
        row = dict(path_evidence_verified=True, stage_transition=f"{len(sessions)}→{len(sessions)+1}",
                   path_days_observed=len(sessions), path_data_coverage=min(s['coverage'] for s in sessions),
                   path_strength_latest=score(last), path_strength_delta=score(last)-score(previous),
                   path_label_code=old, path_label=old)
        for field, source in [('gap','gap'),('first_seal','first'),('open_times','opens'),('seal_ratio','seal_ratio')]:
            row[f'path_{field}_slope']=(last[source]-first[source])/(len(sessions)-1)
        rows.append(row)
    program=function+'\nconst PRIMARY_PATH_LABELS='+json.dumps(labels)+';\n'+\
        'const rows='+json.dumps(rows)+'; const before=JSON.stringify(rows);'+\
        'const results=rows.map(verifiedPathDisplay); if(before!==JSON.stringify(rows))throw Error("input mutation");'+\
        'console.log(JSON.stringify(results));'
    results=json.loads(subprocess.run([node,'-'],input=program,text=True,capture_output=True,check=True).stdout)
    return dict(schema='dc20_path_description_replay_v2',
                purpose='Historical description and abstention audit, NOT accuracy or profitability evaluation',
                source_records_sha256=hashlib.sha256(raw).hexdigest(),
                display_function_sha256=hashlib.sha256(function.encode()).hexdigest(),
                date_min=min(r['signal_date'] for r in records),date_max=max(r['signal_date'] for r in records),
                rows=len(results),legacy_labels=dict(Counter(r['baseline'] for r in records)),
                new_labels=dict(Counter(r['label'] for r in results)),
                statuses=dict(Counter(r['status'] for r in results)),
                inputs_unchanged=True,model_ranking_ledger_changes=False,
                limitations=['Daily endpoint summaries, not tick/minute event reconstruction',
                             'Descriptive bands and 25% sensitivity range are not empirically calibrated probabilities',
                             'No independent semantic truth labels; no accuracy uplift claim',
                             'Three-board endpoints do not establish intermediate-day continuity',
                             'No model training, future outcomes or profit selection used'])


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--html',required=True)
    parser.add_argument('--records',required=True)
    parser.add_argument('--node',default='node')
    args=parser.parse_args()
    print(json.dumps(evaluate(args.html,args.records,args.node),ensure_ascii=False,indent=2))
