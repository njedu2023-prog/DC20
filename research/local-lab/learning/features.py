"""Deterministic D-cut technical features; adjusted ratios, raw price execution."""
import math,statistics
VERSION='pit_numeric_v1'
# All scalar units ratios, not percent; null is unknown, never neutral.
FEATURES={**{f'return_{n}':{'unit':'ratio','lookback':n+1,'family':'price_volume'} for n in (5,10,20)},**{f'ma_slope_{n}':{'unit':'ratio','lookback':n+5,'family':'price_volume'} for n in (5,10,20,30,60)},'bias20':{'unit':'ratio','lookback':20,'family':'price_volume'},'atr14_ratio':{'unit':'ratio','lookback':15,'family':'price_volume'},'volume_ratio5':{'unit':'multiple','lookback':6,'family':'price_volume'},'turnover':{'unit':'ratio','family':'price_volume'},'market_return5':{'unit':'ratio','family':'market'},'relative_return5':{'unit':'ratio','family':'price_volume'}}

def records(result):
 fields=result.get('fields',[]);items=result.get('items',[])
 if len(set(fields))!=len(fields) or any(len(r)!=len(fields) for r in items):raise ValueError('BAD_RESPONSE_SCHEMA')
 return [dict(zip(fields,r)) for r in items]
def bars(rows,code,day,sessions):
 a=sorted(rows,key=lambda r:r['trade_date']);dates=[r['trade_date'] for r in a]
 if len(dates)!=len(set(dates)):raise ValueError('DUPLICATE_BARS')
 if any(r.get('ts_code')!=code or r['trade_date']>day for r in a):raise ValueError('SYMBOL_OR_FUTURE_BARS')
 if not a or dates[-1]!=day:raise ValueError('STALE_OR_EMPTY_BARS')
 for r in a:
  for k in ('open','high','low','close'):
   if not isinstance(r.get(k),(int,float)) or not math.isfinite(r[k]) or r[k]<=0:raise ValueError('BAD_OHLC')
  if r['low']>min(r['open'],r['close']) or r['high']<max(r['open'],r['close']) or not isinstance(r.get('vol'),(int,float)) or not math.isfinite(r['vol']) or r['vol']<0:raise ValueError('INVALID_BAR')
 needed=[d for d in sessions if d<=day][-65:]
 if len(needed)<65 or dates[-65:]!=needed:raise ValueError('MISSING_SESSION_OR_SHORT_HISTORY')
 return a

def compute(daily,adj,basic,market,code,day,sessions):
 a=bars(daily,code,day,sessions);f={r['trade_date']:r['adj_factor'] for r in adj}
 if len(f)!=len(adj) or any(r['ts_code']!=code or r['trade_date']>day for r in adj):raise ValueError('BAD_ADJUSTMENT_IDENTITY')
 if any(r['trade_date'] not in f or not isinstance(f[r['trade_date']],(int,float)) or not math.isfinite(f[r['trade_date']]) or f[r['trade_date']]<=0 for r in a):raise ValueError('MISSING_ADJUSTMENT')
 scale=f[day];cs=[r['close']*f[r['trade_date']]/scale for r in a];hi=[r['high']*f[r['trade_date']]/scale for r in a];lo=[r['low']*f[r['trade_date']]/scale for r in a];v=[r['vol'] for r in a]
 out={f'return_{n}':cs[-1]/cs[-n-1]-1 for n in (5,10,20)}
 out.update({f'ma_slope_{n}':statistics.mean(cs[-n:])/statistics.mean(cs[-n-5:-5])-1 for n in (5,10,20,30,60)})
 out.update(bias20=cs[-1]/statistics.mean(cs[-20:])-1,atr14_ratio=statistics.mean(max(hi[i]-lo[i],abs(hi[i]-cs[i-1]),abs(lo[i]-cs[i-1])) for i in range(len(a)-14,len(a)))/cs[-1],volume_ratio5=v[-1]/statistics.mean(v[-6:-1]) if sum(v[-6:-1]) else None)
 b=[r for r in basic if r['ts_code']==code and r['trade_date']==day]
 if len(b)>1:raise ValueError('DUPLICATE_BASIC')
 out['turnover']=b[0]['turnover_rate']/100 if b and b[0].get('turnover_rate') is not None else None
 m=bars(market,'000001.SH',day,sessions) if market else []
 out['market_return5']=m[-1]['close']/m[-6]['close']-1 if m else None
 out['relative_return5']=out['return_5']-out['market_return5'] if m else None
 if any(v is not None and not math.isfinite(v) for v in out.values()):raise ValueError('NONFINITE_FEATURE')
 return out,{'raw_close':a[-1]['close'],'n':len(a),'schema':VERSION,'missing_dimensions':['theme_funding','limit_quality','catalyst','payoff_distribution'],'note':'Numeric baseline only; adjusted price ratios, raw close execution. Corporate actions during holding period require separate review.'}
