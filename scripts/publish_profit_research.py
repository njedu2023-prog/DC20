"""CAS publish only allowlisted research JSON; never commits production files."""
import json
import os
from pathlib import Path
import subprocess
import urllib.request

PREFIX='outputs/decision/profit_research/'

def allowed(path):
    return (path==PREFIX+'latest.json' or path.startswith(PREFIX+'versions/') or
            path.startswith(PREFIX+'snapshots/') or path.startswith(PREFIX+'labels/')) and path.endswith('.json') and '..' not in path.split('/')

def sources_unchanged(comparison, bound_paths):
    files=comparison.get('files',[])
    if comparison.get('status') not in ('ahead','identical') or len(files)>=300 or comparison.get('total_commits',0)>100:
        return False
    protected=('scripts/','src/','work/profit_1000_upgrade/','models/',PREFIX,
               '.github/workflows/profit_research.yml','requirements', 'data/market/trade_cal_sse.csv')
    return not any(p in bound_paths or p.startswith(protected)
                   for f in files for p in (f['filename'],f.get('previous_filename',f['filename'])))

def main():
    repo='njedu2023-prog/DC20'; token=os.environ['GH_TOKEN']; base=os.environ['RESEARCH_BASE']
    def api(path,body=None,method=None):
        req=urllib.request.Request('https://api.github.com/repos/'+repo+'/'+path,
            data=None if body is None else json.dumps(body).encode(),method=method,
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
    changed=subprocess.check_output(['git','ls-files','--modified','--others','--exclude-standard','--',PREFIX],text=True).splitlines()
    if not changed:
        print('No research changes');return
    if not all(allowed(p) for p in changed):raise ValueError('OUTSIDE_RESEARCH_WRITE_SCOPE')
    pointer=json.loads(Path(PREFIX+'latest.json').read_bytes())
    dataset=json.loads(Path(PREFIX+pointer['path']).read_bytes())
    bound_paths={b['path'] for b in dataset['source_bindings']}
    bound_paths.add(dataset['model']['source']['path'])
    bound_paths.update(b['path'] for d in dataset['days'] for b in d['source_bindings'])
    entries=[]
    for p in changed:
        raw=Path(p).read_bytes()
        if token.encode() in raw or (os.environ.get('TUSHARE_TOKEN') and os.environ['TUSHARE_TOKEN'].encode() in raw):raise ValueError('SECRET_IN_OUTPUT')
        entries.append({'path':p,'mode':'100644','type':'blob','content':raw.decode('utf-8')})
    for attempt in range(3):
        head=api('git/ref/heads/main')['object']['sha']
        if head!=base and not sources_unchanged(api('compare/'+base+'...'+head),bound_paths):
            raise ValueError('RESEARCH_SOURCES_MOVED_REBUILD_REQUIRED')
        parent=api('git/commits/'+head)
        tree=api('git/trees',{'base_tree':parent['tree']['sha'],'tree':entries})
        commit=api('git/commits',{'message':'research: append verified feature and outcome evidence [dc20-candidate-pages-owned]',
                                'tree':tree['sha'],'parents':[head]})
        print('Prepared immutable research commit '+commit['sha'],flush=True)
        if api('git/ref/heads/main')['object']['sha']!=head:continue
        try:api('git/refs/heads/main',{'sha':commit['sha'],'force':False},'PATCH')
        except urllib.error.HTTPError as exc:
            if exc.code in (409,422):continue
            raise
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('head='+commit['sha']+'\n')
        print('Published research revision '+commit['sha']);return
    raise ValueError('CONCURRENT_WRITER_RETRY_LIMIT')

if __name__=='__main__':main()
