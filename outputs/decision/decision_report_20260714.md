# Decision Report (20260714)

- signal_date: **20260713**
- exec_date: **20260714**
- requested_trade_date: **auto**
- regime: **CAUTION**
- risk_budget: **0.7**
- regime_reason: **tail_risk_mean=0.2168,volatility_mean=0.0480**
- guardrail_reason: **CAUTION:tail_risk_mean=0.2168**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **28**
- features_base_loaded: **True**
- features_base_rows: **5524**
- features_limit_loaded: **True**
- features_limit_rows: **5524**
- truth_close_loaded: **True**
- truth_close_rows: **5524**
- meta_loaded: **True**

## Engine Status

- p_fill_pred_src: **model:lgbm**
- p_fill_model_loaded: **True**
- p_fill_model_kind: **lgbm**
- p_fill_degrade_reason: **none**
- eret_pred_src: **rule**
- eret_model_loaded: **True**
- eret_model_kind: **none**
- eret_degrade_reason: **model_rejected_by_learning_acceptance:model_meta_not_trained**

## Intraday Risk Status

- fields_present: **True**
- available_rows: **28** / **28**
- hard_risk_rows: **1**
- intraday_ev_bonus_mean: **0.003**
- intraday_penalty_extra_mean: **0.004336**
- intraday_execution_penalty_mean: **0.003747**

## Decision Diagnostics

- primary_no_trade_reason: **base_ev_positive_but_extra_penalties_filter**
- rows_scored: **28**
- selected_rows: **0**
- positive_ev_rows: **0**
- positive_ev_base_rows: **1**
- positive_e_ret_rows: **27**
- high_pfill_rows: **7**
- low_risk_rows: **2**
- max_EV: **-0.009433**
- max_EV_base: **0.000333**
- max_E_ret: **0.015548**
- mean_cost: **0.001703**
- mean_risk_penalty: **0.017863**
- mean_extra_penalty_total: **0.026995**

## Artifacts

- candidates_snapshot: `data/decision/decision_candidates_20260713.csv`
- execution_table: `data/decision/decision_execution_20260714.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260714.csv`
- top_evr_latest: `docs/signals/TopEVR_latest.csv`
- top_evr_dated: `docs/signals/TopEVR_20260713.csv`

## EV > 3% & RiskPenalty < 1%

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## TopN Targets

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## Full Candidate Pool

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 600513.SH | 联环药业 | 2→3 | 0 | -0.009433 | 0.864266 | 0.01328 | 0.00134 | 0.009804 |
| 2 | 003020.SZ | 立方制药 | 3→4 | 0 | -0.010929 | 0.891091 | 0.015057 | 0.001285 | 0.012141 |
| 3 | 603669.SH | 灵康药业 | 1→2 | 0 | -0.013127 | 0.894701 | 0.012279 | 0.001266 | 0.011339 |
| 4 | 603313.SH | 梦百合 | 1→2 | 0 | -0.013327 | 0.846978 | 0.011025 | 0.001274 | 0.009301 |
| 5 | 600664.SH | 哈药股份 | 2→3 | 0 | -0.016959 | 0.917766 | 0.015548 | 0.00156 | 0.015378 |
| 6 | 001388.SZ | 信通电子 | 1→2 | 0 | -0.018241 | 0.847279 | 0.01057 | 0.001202 | 0.011999 |
| 7 | 603120.SH | 肯特催化 | 1→2 | 0 | -0.019555 | 0.829372 | 0.010542 | 0.00112 | 0.012215 |
| 8 | 603318.SH | 水发燃气 | 1→2 | 0 | -0.022359 | 0.861998 | 0.012571 | 0.001965 | 0.015098 |
| 9 | 300534.SZ | 陇神戎发 | 1→2 | 0 | -0.026363 | 0.892923 | 0.010992 | 0.001873 | 0.017395 |
| 10 | 600992.SH | 贵绳股份 | 3→4 | 0 | -0.026711 | 0.786854 | 0.012402 | 0.001967 | 0.014308 |
| 11 | 603677.SH | 奇精机械 | 1→2 | 0 | -0.028685 | 0.859095 | 0.00965 | 0.001946 | 0.015086 |
| 12 | 603065.SH | 宿迁联盛 | 1→2 | 0 | -0.028737 | 0.858867 | 0.01259 | 0.001289 | 0.017444 |
| 13 | 600671.SH | 天目药业 | 1→2 | 0 | -0.029441 | 0.916448 | 0.012019 | 0.001705 | 0.018891 |
| 14 | 605090.SH | 九丰能源 | 2→3 | 0 | -0.032429 | 0.93 | 0.012137 | 0.0017 | 0.020485 |
| 15 | 600536.SH | 中国软件 | 1→2 | 0 | -0.034363 | 0.853718 | 0.010757 | 0.001381 | 0.017084 |
| 16 | 600629.SH | 华建集团 | 2→3 | 0 | -0.03496 | 0.862623 | 0.011229 | 0.001345 | 0.019292 |
| 17 | 000989.SZ | 九芝堂 | 1→2 | 0 | -0.035124 | 0.851879 | 0.010504 | 0.001442 | 0.01645 |
| 18 | 600881.SH | 亚泰集团 | 1→2 | 0 | -0.037301 | 0.875329 | 0.009438 | 0.002135 | 0.019019 |
| 19 | 601002.SH | 晋亿实业 | 1→2 | 0 | -0.037737 | 0.890932 | 0.009227 | 0.002102 | 0.018957 |
| 20 | 003030.SZ | 祖名股份 | 1→2 | 0 | -0.04172 | 0.86464 | 0.007578 | 0.001432 | 0.019179 |
| 21 | 601608.SH | 中信重工 | 2→3 | 0 | -0.042022 | 0.866835 | 0.009738 | 0.001463 | 0.020049 |
| 22 | 603137.SH | 恒尚节能 | 1→2 | 0 | -0.043968 | 0.916523 | 0.010511 | 0.001951 | 0.022081 |
| 23 | 002379.SZ | 宏桥控股 | 1→2 | 0 | -0.044899 | 0.897879 | 0.010979 | 0.001672 | 0.02343 |
| 24 | 603933.SH | 睿能科技 | 1→2 | 0 | -0.045025 | 0.906906 | 0.009079 | 0.001739 | 0.022114 |
| 25 | 000920.SZ | 沃顿科技 | 2→3 | 0 | -0.045299 | 0.91572 | 0.010217 | 0.001775 | 0.023508 |
| 26 | 301520.SZ | 万邦医药 | nan | 0 | -0.051974 | 0.909416 | 0.00767 | 0.00326 | 0.024305 |
| 27 | 300214.SZ | 日科化学 | 1→2 | 0 | -0.067134 | 0.519338 | 0.01258 | 0.002295 | 0.017361 |
| 28 | 001395.SZ | 亚联机械 | 3→4 | 0 | -0.10693 | 0.738986 | -0.005307 | 0.002199 | 0.036461 |

