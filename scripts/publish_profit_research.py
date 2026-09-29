"""CAS publish only allowlisted research JSON; never commits production files."""
import base64
import json
import os
from pathlib import Path
import subprocess
import urllib.request

PREFIX='outputs/decision/profit_research/'

def allowed(path):
    return (path==PREFIX+'latest.json' or path.startswith(PREFIX+'versions/') or
            path.startswith(PREFIX+'snapshots/') or path.startswith(PREFIX+'labels/')) and path.endswith('.json') and '..' not in path.split('/')

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
    if api('git/ref/heads/main')['object']['sha']!=base:raise ValueError('MAIN_MOVED_RETRY_WITH_FRESH_SOURCES')
    parent=api('git/commits/'+base); entries=[]
    for p in changed:
        raw=Path(p).read_bytes()
        if token.encode() in raw or (os.environ.get('TUSHARE_TOKEN') and os.environ['TUSHARE_TOKEN'].encode() in raw):raise ValueError('SECRET_IN_OUTPUT')
        blob=api('git/blobs',{'content':base64.b64encode(raw).decode(),'encoding':'base64'})
        entries.append({'path':p,'mode':'100644','type':'blob','sha':blob['sha']})
    tree=api('git/trees',{'base_tree':parent['tree']['sha'],'tree':entries})
    commit=api('git/commits',{'message':'research: append verified feature and outcome evidence [dc20-candidate-pages-owned]',
                            'tree':tree['sha'],'parents':[base]})
    if api('git/ref/heads/main')['object']['sha']!=base:raise ValueError('MAIN_MOVED_BEFORE_CAS')
    api('git/refs/heads/main',{'sha':commit['sha'],'force':False},'PATCH')
    with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('head='+commit['sha']+'\n')
    print('Published research revision '+commit['sha'])

if __name__=='__main__':main()
