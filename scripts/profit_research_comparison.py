"""Exploratory paired-day comparisons; never fit, rerank, or alter input labels.

Input is the verified research builder's days, not arbitrary provider prices.
The common eligible pool must be completely resolved before any strategy for a
day is used. NO_FILL leaves a reserved cash slot idle; it is not a filled trade.
"""
from collections import Counter
import math
import random

SCHEMA = 'dc20_research_comparison_v1'
MINIMUM_CI_DAYS = 20
BOOTSTRAP_SEED = 20261004
BOOTSTRAP_SAMPLES = 2000
SETTLED = 'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY'
NO_FILL = frozenset({
    'NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME', 'NO_FILL_SUSPENDED',
    'NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED', 'NO_FILL_CAPACITY',
})
BASES = frozenset({'FORMAL_SELECTED_PROXY', 'COUNTERFACTUAL_NATIVE_POLICY'})
STRATEGIES = (
    ('profit_top1', '盈利前1', 'profit_rank', 1),
    ('profit_top2', '盈利前2', 'profit_rank', 2),
    ('promotion_top1', '晋级前1（共同合格池）', 'promotion_rank', 1),
    ('promotion_top2', '晋级前2（共同合格池）', 'promotion_rank', 2),
    ('eligible_equal_weight', '全合格候选等权', 'profit_rank', None),
)
COMPARISONS = (
    ('profit1_vs_promotion1', '盈利前1 − 晋级前1', 'profit_top1', 'promotion_top1'),
    ('profit2_vs_promotion2', '盈利前2 − 晋级前2', 'profit_top2', 'promotion_top2'),
    ('profit1_vs_pool', '盈利前1 − 全合格池', 'profit_top1', 'eligible_equal_weight'),
    ('profit2_vs_pool', '盈利前2 − 全合格池', 'profit_top2', 'eligible_equal_weight'),
)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _rank(value):
    return _number(value) and value >= 1 and value == int(value)


def _mean(values):
    return math.fsum(values) / len(values) if values else None


def _reasons(day, duplicate_dates):
    records = day.get('records', [])
    pool = [r for r in records if r.get('profit_eligible') is True]
    reasons = []
    date = day.get('date')
    if not isinstance(date, str) or len(date) != 8 or not date.isdigit():
        reasons.append('INVALID_SIGNAL_DATE')
    if date in duplicate_dates:
        reasons.append('DUPLICATE_SIGNAL_DATE')
    if day.get('provenance') != 'FORMAL_FROZEN_REFERENCE':
        reasons.append('NO_FORMAL_FROZEN_DAY')
    if len(pool) < 2:
        reasons.append('FEWER_THAN_TWO_ELIGIBLE_CANDIDATES')
    codes = [r.get('ts_code') for r in records]
    if any(not isinstance(c, str) or not c for c in codes) or len(set(codes)) != len(codes):
        reasons.append('INVALID_OR_DUPLICATE_CANDIDATE')
    models = {r.get('model_sha256') for r in pool}
    if pool and (len(models) != 1 or not next(iter(models))):
        reasons.append('MODEL_IDENTITY_MISMATCH')
    for key in ('profit_rank', 'promotion_rank'):
        ranks = [r.get(key) for r in pool]
        if any(not _rank(v) for v in ranks) or len(set(ranks)) != len(ranks):
            reasons.append('INVALID_OR_DUPLICATE_' + key.upper())
    for record in pool:
        if record.get('signal_date') != date:
            reasons.append('RECORD_DATE_MISMATCH')
        if (record.get('provenance') != 'FORMAL_FROZEN_REFERENCE'
                or record.get('feature_snapshot_present') is not True
                or record.get('training_pair_ready') is not True):
            reasons.append('INCOMPLETE_FROZEN_TERMINAL_PAIR')
        outcome = record.get('outcome', {})
        status = outcome.get('status')
        value = outcome.get('net_return')
        fill = outcome.get('proxy_fill')
        # An unknown NO_FILL-like status is not silently accepted as cash zero.
        if status == SETTLED:
            valid = _number(value) and type(fill) is int and fill == 1
        elif status in NO_FILL:
            valid = _number(value) and value == 0 and type(fill) is int and fill == 0
        else:
            valid = False
        if not valid:
            reasons.append('MISSING_PENDING_OR_INVALID_TERMINAL_LABEL')
        if outcome.get('basis') not in BASES or outcome.get('actual_execution_claimed') is not False:
            reasons.append('UNVERIFIED_LABEL_BASIS')
        if not outcome.get('label_available_date'):
            reasons.append('MISSING_LABEL_AVAILABLE_DATE')
    return pool, sorted(set(reasons))


def _day_strategy(pool, rank_key, slots):
    selected = sorted(pool, key=lambda r: r[rank_key])[:slots]
    evidence = []
    for record in selected:
        outcome = record['outcome']
        filled = outcome['status'] == SETTLED
        evidence.append({
            'ts_code': record['ts_code'],
            'promotion_rank': record['promotion_rank'],
            'profit_rank': record['profit_rank'],
            'status': outcome['status'],
            'basis': outcome['basis'],
            'proxy_fill': outcome['proxy_fill'],
            'slot_weight': 1 / len(selected),
            'slot_net_return': outcome['net_return'] if filled else 0.0,
            'filled_trade_net_return': outcome['net_return'] if filled else None,
        })
    return {
        'slot_net_return': _mean([r['slot_net_return'] for r in evidence]),
        'selected_slots': len(evidence),
        'filled_trades': sum(r['proxy_fill'] for r in evidence),
        'no_fill_slots': sum(r['proxy_fill'] == 0 for r in evidence),
        'selected': evidence,
    }


def _quantile(sorted_values, probability):
    position = (len(sorted_values) - 1) * probability
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _bootstrap(differences):
    result = {
        'method': 'PAIRED_DAY_PERCENTILE', 'confidence_level': 0.95,
        'minimum_days': MINIMUM_CI_DAYS, 'seed': BOOTSTRAP_SEED,
        'resamples': BOOTSTRAP_SAMPLES,
        'status': 'INSUFFICIENT_DAYS', 'lower': None, 'upper': None,
    }
    if len(differences) < MINIMUM_CI_DAYS:
        return result
    # Resample paired daily differences, never individual stocks or each leg
    # independently. This is descriptive and does not establish independence
    # across adjacent days or validity after choosing among multiple strategies.
    rng = random.Random(BOOTSTRAP_SEED)
    samples = sorted(_mean(rng.choices(differences, k=len(differences)))
                     for _ in range(BOOTSTRAP_SAMPLES))
    return {**result, 'status': 'DESCRIPTIVE_ONLY',
            'lower': _quantile(samples, 0.025), 'upper': _quantile(samples, 0.975)}


def build_comparison(days):
    """Return a deterministic, all-history descriptive comparison projection.

    Does not mutate days, features, ranks, readiness, outcome labels or models.
    A day excluded by any check is excluded from ALL five strategies.
    """
    date_counts = Counter(d.get('date') for d in days)
    duplicates = {date for date, count in date_counts.items() if count > 1}
    included, excluded = [], []
    for day in sorted(days, key=lambda d: str(d.get('date', ''))):
        pool, reasons = _reasons(day, duplicates)
        if reasons:
            excluded.append({'date': day.get('date'), 'eligible_candidates': len(pool), 'reasons': reasons})
            continue
        included.append({
            'date': day['date'], 'eligible_candidates': len(pool),
            'model_sha256': pool[0]['model_sha256'],
            'strategies': {key: _day_strategy(pool, rank_key, slots)
                           for key, _, rank_key, slots in STRATEGIES},
        })
    # Comparisons across different frozen model versions must be separately
    # stratified, not silently pooled into a single "current model" claim.
    if len({day['model_sha256'] for day in included}) > 1:
        excluded += [{'date': day['date'], 'eligible_candidates': day['eligible_candidates'],
                      'reasons': ['MIXED_FROZEN_MODEL_VERSIONS']} for day in included]
        included = []
        excluded.sort(key=lambda day: str(day['date']))
    strategies = []
    for key, label, _, _ in STRATEGIES:
        observations = [day['strategies'][key] for day in included]
        trades = [record['filled_trade_net_return'] for observation in observations
                  for record in observation['selected'] if record['proxy_fill'] == 1]
        strategies.append({
            'id': key, 'label': label, 'observed_days': len(observations),
            'mean_daily_slot_return': _mean([r['slot_net_return'] for r in observations]),
            'selected_slots': sum(r['selected_slots'] for r in observations),
            'filled_trades': len(trades),
            'no_fill_slots': sum(r['no_fill_slots'] for r in observations),
            'mean_filled_trade_net_return': _mean(trades),
            'trade_win_rate': sum(value > 0 for value in trades) / len(trades) if trades else None,
            'worst_filled_trade_net_return': min(trades) if trades else None,
        })
    comparisons = []
    for key, label, primary, baseline in COMPARISONS:
        differences = [day['strategies'][primary]['slot_net_return']
                       - day['strategies'][baseline]['slot_net_return'] for day in included]
        comparisons.append({
            'id': key, 'label': label, 'primary': primary, 'baseline': baseline,
            'paired_days': len(differences), 'mean_daily_difference': _mean(differences),
            'positive_difference_days': sum(value > 0 for value in differences),
            'bootstrap': _bootstrap(differences),
        })
    all_pool = [day['strategies']['eligible_equal_weight'] for day in included]
    return {
        'schema_version': SCHEMA,
        'status': 'EXPLORATORY_INSUFFICIENT_DAYS' if len(included) < MINIMUM_CI_DAYS else 'EXPLORATORY_ONLY',
        'scope': 'ALL_HISTORY_COMMON_ELIGIBLE_COMPLETE_DAYS',
        'training_allowed': False, 'automatic_promotion_enabled': False,
        'independent_holdout': False, 'production_model_changed': False,
        'method': {
            'version': 'paired_daily_reserved_cash_v1',
            'candidate_pool': 'FROZEN_PROFIT_ELIGIBLE_CANDIDATES',
            'minimum_candidates_per_day': 2,
            'incomplete_day_policy': 'EXCLUDE_WHOLE_DAY_FROM_ALL_STRATEGIES',
            'selection': 'FIXED_FROZEN_RANK_NO_REFILL_ON_NO_FILL',
            'daily_weighting': 'EQUAL_DAY', 'slot_weighting': 'EQUAL_RESERVED_SLOT',
            'no_fill_slot_return': 0.0, 'no_fill_trade_return': None,
            'pending_or_missing_return': None,
            'slot_return_is_account_nav': False,
        },
        'counts': {
            'available_days': len(days), 'eligible_days': len(included), 'excluded_days': len(excluded),
            'eligible_candidates': sum(day['eligible_candidates'] for day in included),
            'settled_trades': sum(row['filled_trades'] for row in all_pool),
            'no_fill_records': sum(row['no_fill_slots'] for row in all_pool),
        },
        'strategies': strategies, 'comparisons': comparisons,
        'days': included, 'excluded_days': excluded,
        'disclaimers': [
            '全历史独立比较，不随页面筛选变化；仅比较同日共同盈利合格候选池。',
            '晋级基准是共同合格池内的晋级排序，不等于包含盈利不合格股票的正式晋级策略。',
            '固定排名选取，不因未成交而补位；未成交仅按闲置资金槽位计零，不计零成交收益或成交胜率。',
            '挂起、缺失、未核验记录不补零；任一合格候选未终态则整日排除。',
            '每日等权资金槽位回报是研究代理，不是可交易账户净值；未模拟跨日持仓重叠与资金占用。',
            '少于20个完整日不展示区间；20日只为描述区间门槛，不是模型有效或可训练的门槛。',
            '按日重采样区间仅供描述，不能排除跨日相关、重复股票、多重比较和样本选择的影响。',
            '当前样本已被查看，不是独立测试集；不能据此宣称盈利排序有效、自动训练或自动升级。',
        ],
    }
