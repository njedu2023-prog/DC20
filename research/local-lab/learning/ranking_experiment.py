"""One fixed, retrospective ranking experiment; no database writes or promotion.

The candidate selects the two least extended stocks by frozen D-day bias20.
It is deliberately a falsifiable rule, not a fitted probability model.  Results
are matched on the SAME observed cohort and clustered by D; incomplete cohorts
are shown explicitly and cannot establish full-pool performance.
"""
import argparse
import hashlib
import json
import math
import random
import statistics
from datetime import datetime
from pathlib import Path


VERSION = 'BIAS20_TOP2_EXPLORATORY_V1'
PLAN = {
    'version': VERSION,
    'hypothesis': 'Lower frozen D-day 20-session price extension improves next-session auction to following-session 10:00 quote return versus the old subjective ranking.',
    'candidate': 'bias20 ascending; two slots; fractional boundary ties',
    'baseline': 'original frozen profit probability descending; two slots; fractional boundary ties',
    'second_baseline': 'equal weight on the identical observed cohort',
    'primary_metric': 'equal-D mean candidate Top2 net-return uplift versus old Top2 and equal cohort',
    'costs': [0.002, 0.0045, 0.008],
    'feature_policy': 'feature_captured_at <= feature_cutoff <= prediction_cutoff < entry_at; feature_day <= D',
    'outcome_policy': 'VERIFIED_PRICE_PAIR only; prices are quote evidence, not executable or actual returns',
    'missing_policy': 'no imputation; same observed cohort for all arms; partial days separate from full-pool days',
    'variants': 1,
    'exploratory_review_after_new_days': 20,
    'min_new_forward_days_for_adoption_review': 80,
    'existing_training_policy': 'Do not lower existing 60 training / 20 calibration / 20 test date gates, minimum 300 rows, seven-day and five-new-date intervals or fresh-shadow review.',
    'forward_acceptance': {
        'coverage': 'complete frozen pool and outcome coverage on all included confirming dates',
        'uplift': 'positive mean uplift versus both baselines at every declared cost',
        'uncertainty': 'day-block bootstrap 95% lower bound > 0 versus both baselines',
        'tail': 'candidate mean worst 20% daily return no worse than either baseline',
        'execution': 'separate executable-quote review required; quote gains alone cannot authorize production',
        'timing': 'only new snapshots captured after hypothesis registration count as confirmation',
    },
    'production_promotion': False,
    'note': 'Thresholds are governance assumptions, not a power guarantee. Single post-hoc rule; no hyperparameter search. Original conditional probabilities are not calibrated on unrestricted entries.',
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return parsed


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def selection(rows, field, ascending=False):
    """Slot weights sum to min(2, n); never break ties by symbol or outcomes."""
    ordered = sorted(rows, key=lambda r: r[field], reverse=not ascending)
    slots = min(2, len(ordered))
    if not slots:
        return {}
    boundary = ordered[slots - 1][field]
    better = [r for r in ordered if (r[field] < boundary if ascending else r[field] > boundary)]
    tied = [r for r in ordered if r[field] == boundary]
    remaining = slots - len(better)
    return {**{r['code']: 1.0 for r in better}, **{r['code']: remaining / len(tied) for r in tied}}


def exclusions(row, as_of):
    reasons = []
    if row.get('quote_status') != 'VERIFIED_PRICE_PAIR':
        reasons.append('QUOTE_PAIR_NOT_VERIFIED')
    if not finite(row.get('entry_price')) or not finite(row.get('exit_price')) or row.get('entry_price', 0) <= 0 or row.get('exit_price', 0) <= 0:
        reasons.append('INVALID_OR_MISSING_PRICE')
    if not finite(row.get('p')) or not 0 <= row.get('p', -1) <= 1 or not row.get('rank_eligible', False):
        reasons.append('OLD_RANK_UNINFORMED_OR_MISSING')
    if not finite(row.get('bias20')):
        reasons.append('MISSING_FROZEN_BIAS20')
    try:
        captured, cut, predicted, entry, exit_at = [stamp(row[k]) for k in ('feature_captured_at', 'feature_cutoff', 'prediction_cutoff', 'entry_at', 'exit_at')]
        if not captured <= cut <= predicted < entry < exit_at:
            reasons.append('FEATURE_NOT_AVAILABLE_AT_PREDICTION')
        if exit_at > as_of:
            reasons.append('EXIT_NOT_DUE')
    except (KeyError, ValueError, TypeError, AttributeError):
        reasons.append('MISSING_OR_INVALID_TIMELINE')
    if not isinstance(row.get('feature_day'), str) or row['feature_day'] > row['day']:
        reasons.append('FEATURE_DAY_MISSING_OR_FUTURE')
    return reasons


def mean(values):
    return statistics.mean(values) if values else None


def quantile(values, q):
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def day_interval(values):
    """Resample complete day-level paired differences, never individual stocks."""
    if len(values) < 5:
        return {'interval': None, 'reason': 'FEWER_THAN_FIVE_DATES', 'unit': 'D'}
    rng = random.Random(20261004)
    draws = [mean([rng.choice(values) for _ in values]) for _ in range(4000)]
    return {'interval': [quantile(draws, .025), quantile(draws, .975)], 'method': 'paired-day-block-bootstrap', 'unit': 'D', 'resamples': 4000, 'warning': 'Exploratory repeated use is not confirmation; few dates and regime dependence limit inference.'}


def aggregate(days):
    answer = {'independent_dates': len(days), 'date_ids': [d['day'] for d in days], 'costs': []}
    for i, cost in enumerate(PLAN['costs']):
        arms = {}
        for arm in ('old_top2', 'candidate_top2', 'equal_cohort'):
            xs = [d['costs'][i][arm] for d in days]
            arms[arm] = {
                'equal_date_mean_net': mean(xs),
                'positive_date_fraction': mean([float(x > 0) for x in xs]),
                'worst_date_net': min(xs) if xs else None,
                'worst_20pct_daily_mean': mean(sorted(xs)[:max(1, math.ceil(len(xs) * .2))]) if xs else None,
            }
        differences = {}
        for arm in ('old_top2', 'equal_cohort'):
            xs = [d['costs'][i]['candidate_top2'] - d['costs'][i][arm] for d in days]
            differences[arm] = {'mean_uplift': mean(xs), 'positive_uplift_dates': sum(x > 0 for x in xs), 'uncertainty': day_interval(xs)}
        answer['costs'].append({'cost': cost, 'arms': arms, 'candidate_minus': differences})
    return answer


def run(data):
    as_of = stamp(data['as_of'])
    rows = data['rows']
    seen = set()
    grouped = {}
    rejected = []
    for row in rows:
        key = row['day'], row['code']
        if key in seen:
            raise ValueError('DUPLICATE_D_STOCK_VERSION_SELECT_EXPLICITLY')
        seen.add(key)
        grouped.setdefault(row['day'], []).append(row)
    days = []
    for day, pool in sorted(grouped.items()):
        valid = []
        for row in pool:
            why = exclusions(row, as_of)
            if why:
                rejected.append({'day': day, 'code': row['code'], 'reasons': why})
            else:
                valid.append(row)
        declared = {r.get('pool_count', len(pool)) for r in pool}
        if len(declared) != 1 or not isinstance(next(iter(declared)), int) or next(iter(declared)) < len(pool):
            raise ValueError('INCONSISTENT_DECLARED_POOL_SIZE')
        pool_count = next(iter(declared))
        if len(valid) < 2:
            rejected.append({'day': day, 'reasons': ['FEWER_THAN_TWO_MATCHED_STOCKS'], 'valid_count': len(valid), 'pool_count': pool_count})
            continue
        weights = {'old_top2': selection(valid, 'p'), 'candidate_top2': selection(valid, 'bias20', True), 'equal_cohort': {r['code']: 1 for r in valid}}
        gross = {r['code']: r['exit_price'] / r['entry_price'] - 1 for r in valid}
        costs = []
        for cost in PLAN['costs']:
            costs.append({'cost': cost, **{arm: sum(gross[code] * w for code, w in ws.items()) / sum(ws.values()) - cost for arm, ws in weights.items()}})
        days.append({
            'day': day, 'pool_count': pool_count, 'observed_count': len(valid),
            'coverage': len(valid) / pool_count,
            'cohort': 'FULL_POOL' if len(valid) == pool_count else 'PARTIAL_MATCHED_COHORT',
            'codes': sorted(gross), 'weights': weights, 'costs': costs,
            'original_probability_outside_scenario': [r['code'] for r in valid if r.get('original_scenario_applicable') is not True],
            'row_evidence': [{'code': r['code'], 'prediction_id': r.get('prediction_id'),
                'forecast_sha256': r.get('forecast_sha256'),
                'feature_id': r.get('feature_id', r.get('feature_evidence_id')),
                'feature_source': r.get('feature_source'),
                'quote_evidence_ids': r.get('quote_evidence_ids', r.get('evidence_ids')),
                'execution_state': r.get('execution_state', 'NOT_VERIFIED'),
                'execution_risks': r.get('execution_risks', []),
                'bias20': r['bias20'], 'p': r['p'], 'gross': gross[r['code']]} for r in valid],
        })
    totals = aggregate(days)
    observed = totals['costs'][1]['candidate_minus']
    old_uplift = observed['old_top2']['mean_uplift']
    pool_uplift = observed['equal_cohort']['mean_uplift']
    if old_uplift is None:
        direction = 'NO_USABLE_MATCHED_DATES'
    elif old_uplift > 0 and pool_uplift > 0:
        direction = 'OBSERVED_GAIN_REQUIRES_FRESH_CONFIRMATION'
    elif old_uplift <= 0 and pool_uplift <= 0:
        direction = 'OBSERVED_UNDERPERFORMANCE_DO_NOT_ADOPT'
    else:
        direction = 'MIXED_OBSERVED_EVIDENCE_DO_NOT_ADOPT'
    return {
        'version': VERSION, 'state': 'RETROSPECTIVE_EXPLORATION_COMPLETED', 'as_of': data['as_of'],
        'basis': 'PRICE_PAIR_ONLY', 'plan': PLAN, 'plan_sha': digest(PLAN),
        'input_sha': digest(data), 'implementation_sha': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'ledger_sha': data.get('ledger_sha'), 'days': days, 'excluded': rejected,
        'all_observed_cohorts': totals,
        'complete_pools_only': aggregate([d for d in days if d['cohort'] == 'FULL_POOL']),
        'decision': 'REJECT_PRODUCTION_PROMOTION', 'confirmation_status': 'WAIT_REGISTERED_FRESH_FORWARD_DATES',
        'exploratory_direction': direction,
        'new_forward_days': 0, 'promotion': False,
        'reason': 'Rule was chosen after these historical data existed. Any apparent uplift is exploratory, not independent evidence; original conditional p is used as frozen ranking only. This script cannot authorize promotion.',
        'limitations': ['Unreviewed execution and corporate actions remain ledger issues; pure price-pair return is not actual profit.', 'Partial matched cohorts do not establish full-pool performance.', 'Same-D stocks are correlated; aggregate and uncertainty use dates.', 'No claim of probability calibration, causality, investable NAV or improved accuracy.'],
    }


def markdown(result):
    lines = ['# 单一改进实验：减少过度乖离', '', '状态：**事后探索已运行；拒绝生产晋升，等待登记后新日期验证。**', '', '旧方法：冻结主观概率降序前二。候选：冻结 D 日 bias20 升序前二。对照：同一观察交集等权。并列边界按名额均分，不凭代码挑选。', '', '结果为竞价到次日10:00的报价收益；不代表真实成交。原概率超出原情景时不作概率校准。', '', '| D | 有效 / 全池 | 范围 | 旧排序 | 候选 | 同交集等权 | 候选−旧排序 |', '|---|---:|---|---:|---:|---:|---:|']
    fmt = lambda x: '—' if x is None else f'{x * 100:+.2f}%'
    for d in result['days']:
        r = d['costs'][1]
        lines.append(f"| {d['day']} | {d['observed_count']}/{d['pool_count']} | {'全池' if d['cohort'] == 'FULL_POOL' else '部分同交集'} | {fmt(r['old_top2'])} | {fmt(r['candidate_top2'])} | {fmt(r['equal_cohort'])} | {fmt(r['candidate_top2']-r['old_top2'])} |")
    aggregate = result['all_observed_cohorts']['costs'][1]
    old_uplift = aggregate['candidate_minus']['old_top2']['mean_uplift']
    pool_uplift = aggregate['candidate_minus']['equal_cohort']['mean_uplift']
    direction = {
        'NO_USABLE_MATCHED_DATES': '暂无可用的同交集日期。',
        'OBSERVED_GAIN_REQUIRES_FRESH_CONFIRMATION': '历史观察中候选优于两个基线，但不可据此采用，须独立新日期确认。',
        'OBSERVED_UNDERPERFORMANCE_DO_NOT_ADOPT': '历史观察中候选未优于两个基线，本轮不采用该规则。',
        'MIXED_OBSERVED_EVIDENCE_DO_NOT_ADOPT': '相对两个基线的结果分化，本轮不采用该规则。',
    }[result['exploratory_direction']]
    lines += ['', f'按日期等权：候选相对旧排序 {fmt(old_uplift)}，相对同交集等权 {fmt(pool_uplift)}。{direction}']
    lines += ['', '基准往返成本0.45%；完整JSON同时保留0.20%、0.80%、逐日权重、缺失原因及日期分组区间。', '', f"有效探索日期：{len(result['days'])}；完整全池日期：{result['complete_pools_only']['independent_dates']}；登记后确认日期：0。", '', '未来登记后20个新日期仅触发探索复查；采用审查至少80个完整新日期，原学习流水线60训练/20校准/20测试日期、300样本及新影子审核门槛均不降低。还须相对两个基线的收益改善与日期分组区间通过、尾部风险不恶化，并另行核验执行条件。门槛是治理约束，不代表统计功效保证。', '', '此次实验不改变旧预测、权重或生产模型。']
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    source = args.input.read_bytes()
    result = run(json.loads(source))
    result['input_file_sha'] = hashlib.sha256(source).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    args.output.with_suffix('.md').write_text(markdown(result))
    print(json.dumps({'state': result['state'], 'days': len(result['days']), 'full_pool_days': result['complete_pools_only']['independent_dates'], 'decision': result['decision'], 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
