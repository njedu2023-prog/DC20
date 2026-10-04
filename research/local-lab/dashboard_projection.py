"""Read-only dashboard projection from hash-verified frozen evidence."""
import json

def project(c, payload):
    from research_contract import archived
    metrics = payload.get('metrics') or {}
    sectors = []
    errors = []
    for key in ('price_volume', 'theme'):
        ref = payload.get('evidence_refs', {}).get(key)
        if not ref:
            continue
        try:
            data = json.loads(archived(c, ref).read_text())
            if key == 'price_volume':
                metrics = data.get('metrics') or {}
            else:
                sectors = data.get('linked_sectors') or []
        except (ValueError, OSError, TypeError):
            errors.append(key)
    return {'metrics': metrics,
            'ma_complete': all(metrics.get('ma', {}).get(str(n)) is not None for n in (5,10,20,30,60)),
            'daily_count': metrics.get('n'),
            'sector_windows': any(all(str(n) in x.get('windows', {}) for n in (1,3,5,10,20)) for x in sectors),
            'errors': errors}
