"""Read-only THS chart evidence. Never infer fills, level2 or probabilities."""
import argparse, concurrent.futures, hashlib, json, math, re, subprocess
from datetime import datetime, timezone
from pathlib import Path

def unwrap(text):
    m=re.fullmatch(r'\s*[A-Za-z_][\w]*\((.*)\);?\s*',text,re.S)
    if not m: raise ValueError('Invalid JSONP wrapper')
    return json.loads(m[1])

def minute(text, code, day):
    d=unwrap(text)[code]
    if d.get('date')!=day: raise ValueError('Wrong signal date')
    rows=[x.split(',') for x in d['data'].split(';') if x]
    expected=[f'{h:02}{m:02}' for h in range(9,16) for m in range(60) if '0930'<=f'{h:02}{m:02}'<='1130' or '1301'<=f'{h:02}{m:02}'<='1500']
    times=[r[0] for r in rows]
    if times!=sorted(set(times)):raise ValueError('Minute grid unordered or duplicated')
    excluded=[r[0] for r in rows if r[0] not in expected]
    if any(t!='1300' and not '1505'<=t<='1530' for t in excluded):raise ValueError('Unexpected extra minute')
    rows=[r for r in rows if r[0] in expected]
    if [r[0] for r in rows]!=expected: raise ValueError('Minute grid incomplete')
    p={r[0]:float(r[1]) for r in rows};pre=float(d['pre'])
    if not all(math.isfinite(v) and v>0 for v in [pre,*p.values()]): raise ValueError('Invalid price')
    peak=0;dd=0
    for v in p.values():peak=max(peak,v);dd=min(dd,100*(v/peak-1))
    return dict(name=d['name'],date=day,excluded_times=excluded,n=len(p),previous_close=pre,open=p['0930'],at1000=p['1000'],last=p['1500'],high=max(p.values()),low=min(p.values()),gap_pct=100*(p['0930']/pre-1),open_to_1000_pct=100*(p['1000']/p['0930']-1),open_to_close_pct=100*(p['1500']/p['0930']-1),sampled_max_drawdown_pct=dd,minutes_below_final_price=sum(v<p['1500']-.000001 for v in p.values()),note='09:30 sampled price is not a verified 09:25 execution; sampled extrema are not daily extrema; no volume/queue inference')

def daily(text, day):
    d=unwrap(text);rows=[x.split(',') for x in d['data'].split(';') if x]
    if not rows or rows[-1][0]!=day:raise ValueError('Wrong daily date')
    dates=[r[0] for r in rows]
    if dates!=sorted(set(dates)):raise ValueError('Daily dates invalid')
    close=[float(r[4]) for r in rows]
    if not all(math.isfinite(v) and v>0 for v in close):raise ValueError('Invalid close')
    breaks=[i for i in range(1,len(close)) if not .5<close[i]/close[i-1]<2]
    start=breaks[-1] if breaks else 0;s=close[start:]
    return dict(n=len(rows),break_dates=[dates[i] for i in breaks],latest_segment_n=len(s),latest_segment_start=dates[start],returns_pct={str(n):100*(s[-1]/s[-n-1]-1) if len(s)>n else None for n in (1,3,5,10,20)},ma={str(n):sum(s[-n:])/n if len(s)>=n else None for n in (5,10,20,30,60)},note='Factor-of-two discontinuities excluded; absence of a detected break does not certify index methodology')

def collect(folder,day,codes):
    if (folder/'manifest.json').exists():
        raise ValueError('Output already exists; use a fresh run directory to preserve evidence')
    folder.mkdir(parents=True,exist_ok=True)
    jobs=[(c,'minute',f'https://d.10jqka.com.cn/v6/time/{c}/last.js') for c in codes]+[(c,'daily',f'https://d.10jqka.com.cn/v4/line/{c}/00/last.js') for c in codes if c.startswith('bk_')]
    def get(job):
        c,kind,url=job;r=subprocess.run(['curl','-fLsS','--max-time','20',url],capture_output=True)
        item=dict(code=c,kind=kind,url=url,captured_at=datetime.now(timezone.utc).isoformat(),ok=False)
        if r.returncode:item['error']=r.stderr.decode(errors='replace');return item
        raw=r.stdout;sha=hashlib.sha256(raw).hexdigest();path=folder/(c+'-'+kind+'-'+sha[:12]+'.js');path.write_bytes(raw);item.update(file=str(path.resolve()),sha256=sha)
        try:item['metrics']=minute(raw.decode(),c,day) if kind=='minute' else daily(raw.decode(),day);item['ok']=True
        except (ValueError,KeyError,IndexError) as e:item['error']=str(e)
        return item
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:result=list(pool.map(get,jobs))
    (folder/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--day',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--codes',nargs='+',required=True);a=p.parse_args()
    if not re.fullmatch(r'\d{8}',a.day) or not all(re.fullmatch(r'(hs|bk)_\d{6}',c) for c in a.codes):p.error('Invalid day/code')
    r=collect(a.out,a.day,a.codes);print(json.dumps({'valid':sum(x['ok'] for x in r),'attempts':len(r)}))
