"""Score independent human references, never replace empty references with rules."""
from collections import Counter

LABELS={'STABLE_STRONG','WEAK_TO_STRONG','STRONG_TO_WEAK',
        'ACCELERATION_CONSENSUS','DIVERGENCE_RESEAL','MIXED','INSUFFICIENT'}


def score(records, reviews):
    keys=lambda r:(r['signal_date'],r['ts_code'],r['stage'])
    indexed={keys(r):r for r in records}
    if len(indexed)!=len(records) or len({keys(r) for r in reviews})!=len(reviews):
        raise ValueError('DUPLICATE_REVIEW_KEY')
    paired=[]
    agreement=[]
    for ref in reviews:
        if keys(ref) not in indexed:
            raise ValueError('REVIEW_OUTSIDE_FROZEN_COHORT')
        a,b,y=[ref.get(k) for k in ('reviewer_a','reviewer_b','adjudicated_label')]
        if a is None or b is None or y is None:
            continue
        if any(v not in LABELS for v in (a,b,y)):
            raise ValueError('INVALID_REFERENCE_LABEL')
        if not ref.get('evidence_notes') or not ref.get('independence_attested'):
            raise ValueError('REFERENCE_EVIDENCE_REQUIRED')
        agreement.append(a==b)
        paired.append((indexed[keys(ref)],y))
    out={'reviewed':len(paired),'unreviewed':len(reviews)-len(paired),
         'reviewer_agreement':sum(agreement)/len(agreement) if agreement else None}
    for field in ('baseline','candidate'):
        accepted=[(r[field],y) for r,y in paired if r[field] not in ('UNCERTAIN','INSUFFICIENT')]
        bylabel={}
        for label in sorted(LABELS-{'INSUFFICIENT'}):
            tp=sum(p==y==label for p,y in accepted)
            predicted=sum(p==label for p,y in accepted)
            actual=sum(y==label for _,y in paired)
            bylabel[label]={'precision':tp/predicted if predicted else None,
                            'recall_including_abstentions':tp/actual if actual else None}
        out[field]={'accepted':len(accepted),
                    'coverage':len(accepted)/len(paired) if paired else None,
                    'selective_accuracy':sum(p==y for p,y in accepted)/len(accepted) if accepted else None,
                    'per_label':bylabel}
    return out
