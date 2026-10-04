"""Matched diagnostics for immutable forecasts, without model promotion.

QUOTE_RETURN is a price-path observation. QUOTE_PROXY is a reviewed execution
simulation and ACTUAL requires fills. These bases are never pooled.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import lab

# This object is an exact copy of the policy stored in earlier freezes. Never
# mutate it in place: equality is part of those historical contracts.
POLICY_V1 = {'version':'MATCHED_COHORT_V1','strategy':lab.STRATEGY,'costs':[.002,.0045,.008],'scenario':'GAP_0_3','baseline_probability':.5,'selection':'Top2; boundary ties receive fractional selection weights before outcomes','aggregation':'Equal signal-day weight; conditional fills separately reported','missing':'Any absent/MISSING outcome excludes whole day; no-fill is not zero','promotion':'DISABLED','minimum_review_days':80,'note':'80日仅治理门槛；不保证统计效力，不窥探留出集调参。'}
LEGACY_POLICY = POLICY_V1
POLICY_V2 = {
    **POLICY_V1,
    'version': 'MATCHED_COHORT_V2',
    'scenario': 'ANY_EXECUTABLE_AUCTION',
    'probability': 'Only probabilities applicable to the original frozen entry scenario are scored; returns remain observable separately',
    'official': 'Optional matched comparator; missing official top2 does not block local versus equal-pool comparison',
    'ranking': 'Frozen probability rank for historical subjective forecasts; never relabelled as expected-net-return prediction',
    'layers': ['QUOTE_RETURN', 'QUOTE_PROXY', 'ACTUAL'],
}
ACTIVE_POLICY = POLICY_V2
POLICY = ACTIVE_POLICY
BASES = {
    'QUOTE_RETURN': {'label': '报价路径结果，非成交收益', 'settled': ('VERIFIED_PRICE_PAIR', 'SETTLED'), 'execution_claim': False},
    'QUOTE_PROXY': {'label': '已审核执行假设的行情代理', 'settled': ('SETTLED',), 'execution_claim': False},
    'ACTUAL': {'label': '真实成交结果', 'settled': ('SETTLED',), 'execution_claim': True},
}


def policy_compatible(payload):
    """Accept exact declared policies, including the documented V1 gap removal.

    Historical V1 + ANY_EXECUTABLE_AUCTION was already frozen during the first
    no-gap migration. Accepting it does not change its original probability.
    """
    policy = payload.get('comparison_policy')
    scenario = payload.get('entry_scenario_id')
    return ((policy == POLICY_V1 and scenario in ('GAP_0_3', 'ANY_EXECUTABLE_AUCTION'))
            or (policy == POLICY_V2 and scenario == 'ANY_EXECUTABLE_AUCTION'))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid_probability(row):
    return _number(row.get('p')) and 0 <= row['p'] <= 1


def probability_applicable(row):
    """An explicit eligibility decision is authoritative; old gaps fail closed."""
    if not _valid_probability(row) or not row.get('rank_eligible'):
        return False
    if 'probability_eligible' in row:
        return row['probability_eligible'] is True
    if row.get('entry_scenario_id') == 'GAP_0_3':
        pre, entry = row.get('previous_close'), row.get('entry_price')
        return bool(_number(pre) and pre > 0 and _number(entry) and entry > 0
                    and -1e-9 <= entry / pre - 1 <= .03 + 1e-9)
    # Older callers already validate their frozen scenario upstream. New callers
    # should always send probability_eligible rather than infer from current rules.
    return True


def selection(rows, key, k=2):
    values = sorted({r[key] for r in rows if _number(r.get(key))}, reverse=True)
    left, out = k, {}
    for value in values:
        group = [r for r in rows if r.get(key) == value]
        weight = min(1, left / len(group))
        if weight <= 0:
            break
        out.update({r['code']: weight for r in group})
        left -= weight * len(group)
    return out


def _price_pair(row):
    return all(_number(row.get(key)) and row[key] > 0 for key in ('entry_price', 'exit_price'))


def _score_probabilities(rows, returns, basis):
    # A quoted price path alone cannot validate probabilities conditioned on
    # executable entry and exit, even when its opening gap matches.
    eligible = [] if basis == 'QUOTE_RETURN' else [r for r in rows if probability_applicable(r)]
    n = len(eligible)
    y = {r['code']: int(returns[r['code']] > 0) for r in eligible}
    return {
        'probability_n': n,
        'probability_excluded': len(rows) - n,
        'probability_note': 'Execution conditions unverified; quoted returns do not calibrate executable conditional probabilities' if basis == 'QUOTE_RETURN' else 'Only original frozen probability conditions are evaluated',
        'brier': sum((r['p'] - y[r['code']]) ** 2 for r in eligible) / n if n else None,
        'baseline_brier': .25 if n else None,
        'log_loss': -sum(y[r['code']] * math.log(max(1e-12, r['p']))
                         + (1 - y[r['code']]) * math.log(max(1e-12, 1 - r['p']))
                         for r in eligible) / n if n else None,
        'baseline_log_loss': math.log(2) if n else None,
    }


def compare(days, basis):
    if basis not in BASES:
        raise ValueError('INVALID_BASIS')
    layer = BASES[basis]
    report = {
        'policy': POLICY, 'basis': basis, 'basis_label': layer['label'],
        'execution_claim': layer['execution_claim'],
        'ranking_source': 'FROZEN_PROBABILITY_RANK',
        'days': [], 'excluded': [], 'observations': [],
        'state': 'WAITING_MATCHED_OUTCOMES',
    }
    for day, rows in sorted(days.items()):
        if not rows or len({r['code'] for r in rows}) != len(rows):
            report['excluded'].append({'day': day, 'reason': 'duplicate or empty cohort'})
            continue
        # Preserve each observed stock even when the complete matched comparison
        # is blocked. This output is not a cherry-picked full-cohort return.
        for row in rows:
            observed = row.get('status') in layer['settled'] and _price_pair(row)
            report['observations'].append({
                'day': day, 'code': row['code'], 'status': row.get('status'),
                'basis': basis, 'price_verified': observed,
                'net_at_base_cost': row['exit_price'] / row['entry_price'] - 1 - .0045 if observed else None,
                'probability_eligible': probability_applicable(row) if observed and basis != 'QUOTE_RETURN' else False,
            })
        if any(r.get('status') not in (*layer['settled'], 'UNFILLED', 'UNEXITED') for r in rows):
            report['excluded'].append({'day': day, 'reason': 'incomplete outcomes; matched whole-day comparison excluded; individual observations retained'})
            continue
        if any(r['status'] == 'UNEXITED' for r in rows):
            report['excluded'].append({'day': day, 'reason': 'unexited exposure unresolved; no profitability conclusion'})
            continue
        settled = [r for r in rows if r['status'] in layer['settled']]
        if any(not _price_pair(r) for r in settled):
            report['excluded'].append({'day': day, 'reason': 'invalid observed prices'})
            continue
        if not settled:
            report['excluded'].append({'day': day, 'reason': 'no fills or verified price pairs'})
            continue
        choices = {'equal_pool': {r['code']: 1 for r in rows}}
        comparison_blocks = []
        if all(_valid_probability(r) and r.get('rank_eligible') for r in rows):
            choices['independent_top2'] = selection(rows, 'p')
        else:
            comparison_blocks.append({'comparison': 'independent_top2', 'reason': 'unrated/informationless member; no complete frozen ranking'})
        official_rows = [r for r in rows if r.get('official_rank') in (1, 2)]
        if len(official_rows) == 2 and {r['official_rank'] for r in official_rows} == {1, 2}:
            choices['official_top2'] = {r['code']: 1 for r in official_rows}
        else:
            comparison_blocks.append({'comparison': 'official_top2', 'reason': 'formal same-D top2 not frozen or invalid; local comparison unaffected'})
        entry = {
            'day': day, 'pool': len(rows), 'settled': len(settled),
            'unfilled': sum(r['status'] == 'UNFILLED' for r in rows),
            'result_count_kind': 'VERIFIED_PRICE_PAIRS' if basis == 'QUOTE_RETURN' else 'REVIEWED_SETTLEMENTS',
            'comparison_blocks': comparison_blocks, 'costs': [],
        }
        for cost in POLICY['costs']:
            returns = {r['code']: r['exit_price'] / r['entry_price'] - 1 - cost for r in settled}
            scores = {'cost': cost, **_score_probabilities(settled, returns, basis), 'selections': {}}
            for name, selected in choices.items():
                filled = {code: weight for code, weight in selected.items() if code in returns}
                denominator = sum(filled.values())
                scores['selections'][name] = {
                    'selected_weight': sum(selected.values()), 'filled_weight': denominator,
                    'conditional_mean_net': sum(returns[code] * weight for code, weight in filled.items()) / denominator if denominator else None,
                    'worst_filled_net': min((returns[code] for code in filled), default=None),
                    'selected_codes': selected,
                }
            local = scores['selections'].get('independent_top2', {}).get('conditional_mean_net')
            for comparator in ('equal_pool', 'official_top2'):
                baseline = scores['selections'].get(comparator, {}).get('conditional_mean_net')
                scores['local_minus_' + comparator] = local - baseline if local is not None and baseline is not None else None
            entry['costs'].append(scores)
        report['days'].append(entry)
    if report['days']:
        report['state'] = 'DESCRIPTIVE_ONLY_NO_PROMOTION'
        report['n_days'] = len(report['days'])
        probability_days = [d['costs'][1] for d in report['days'] if d['costs'][1]['probability_n']]
        report['probability_days'] = len(probability_days)
        report['probability_n'] = sum(d['probability_n'] for d in probability_days)
        report['day_equal_brier'] = sum(d['brier'] for d in probability_days) / len(probability_days) if probability_days else None
        report['baseline_brier'] = .25 if probability_days else None
        report['paired_comparisons'] = {}
        for name in ('equal_pool', 'official_top2'):
            diffs = [d['costs'][1]['local_minus_' + name] for d in report['days']
                     if d['costs'][1]['local_minus_' + name] is not None]
            report['paired_comparisons'][name] = {
                'n_days': len(diffs),
                'day_equal_mean_uplift': sum(diffs) / len(diffs) if diffs else None,
                'positive_days': sum(v > 0 for v in diffs),
                'inference': 'DESCRIPTIVE_ONLY_NOT_STATISTICAL_EVIDENCE',
            }
    return report


def run(c, model, basis):
    if basis not in ('ACTUAL', 'QUOTE_PROXY'):
        raise ValueError('QUOTE_RETURN_USE_VERIFIED_LEDGER_ROWS_WITH_COMPARE')
    groups, excluded = defaultdict(list), []
    for row in c.execute("SELECT * FROM predictions WHERE model=? AND provenance='PROSPECTIVE_LOCAL'", (model,)):
        r = dict(row)
        payload = json.loads(r['payload'])
        if (not policy_compatible(payload) or payload.get('research_contract') != 'RESEARCH_FREEZE_V1'
                or abs(r['cost'] - .0045) > 1e-10 or r['strategy'] != POLICY['strategy']):
            excluded.append({'prediction_id': r['id'], 'reason': 'legacy or incompatible contract'})
            continue
        outcome = c.execute('SELECT * FROM outcomes o WHERE prediction_id=? AND basis=? AND NOT EXISTS(SELECT 1 FROM outcomes n WHERE n.supersedes=o.id)', (r['id'], basis)).fetchone()
        previous_close = None
        if outcome:
            try:
                artifact = c.execute('SELECT * FROM artifacts WHERE id=?', (outcome['artifact_id'],)).fetchone()
                import hashlib
                blob = Path(c.execute('PRAGMA database_list').fetchone()[2]).parent / 'blobs' / artifact['sha256']
                raw = blob.read_bytes()
                if hashlib.sha256(raw).hexdigest() == artifact['sha256']:
                    previous_close = json.loads(raw).get('execution_review', {}).get('previous_close')
            except (OSError, ValueError, KeyError, TypeError):
                pass
        groups[r['signal_date']].append(dict(
            code=r['code'], p=r['p'], rank_eligible=payload.get('independent_rank_eligible'),
            official_rank=payload.get('comparison_official_rank'),
            status=outcome['status'] if outcome else 'MISSING',
            entry_price=outcome['entry_price'] if outcome else None,
            exit_price=outcome['exit_price'] if outcome else None,
            previous_close=previous_close, entry_scenario_id=payload.get('entry_scenario_id'),
        ))
    out = compare(groups, basis)
    out['excluded_predictions'], out['model'] = excluded, model
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--basis', choices=['ACTUAL', 'QUOTE_PROXY'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with lab.connect() as connection:
        result = run(connection, args.model, args.basis)
        with args.out.open('x') as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
        artifact_id = lab.artifact(connection, args.out)
        lab.event(connection, 'MATCHED_BENCHMARK', dict(artifact_id=artifact_id, state=result['state']))
        connection.commit()
    print(result['state'])
