# Decision Report (20260720)

- signal_date: **20260717**
- exec_date: **20260720**
- requested_trade_date: **auto**
- regime: **CAUTION**
- risk_budget: **0.7**
- regime_reason: **tail_risk_mean=0.2235,volatility_mean=0.0514**
- guardrail_reason: **CAUTION:open_board_max=5.0,tail_risk_mean=0.2235**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **32**
- features_base_loaded: **True**
- features_base_rows: **5522**
- features_limit_loaded: **True**
- features_limit_rows: **5522**
- truth_close_loaded: **True**
- truth_close_rows: **5522**
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
- available_rows: **32** / **32**
- hard_risk_rows: **5**
- intraday_ev_bonus_mean: **0.002837**
- intraday_penalty_extra_mean: **0.005624**
- intraday_execution_penalty_mean: **0.00426**

## Decision Diagnostics

- primary_no_trade_reason: **positive_e_ret_cannot_cover_cost_and_risk**
- rows_scored: **32**
- selected_rows: **0**
- positive_ev_rows: **0**
- positive_ev_base_rows: **0**
- positive_e_ret_rows: **27**
- high_pfill_rows: **3**
- low_risk_rows: **1**
- max_EV: **-0.011107**
- max_EV_base: **-0.000999**
- max_E_ret: **0.014329**
- mean_cost: **0.001621**
- mean_risk_penalty: **0.018514**
- mean_extra_penalty_total: **0.028022**

## Artifacts

- candidates_snapshot: `data/decision/decision_candidates_20260717.csv`
- execution_table: `data/decision/decision_execution_20260720.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260720.csv`
- top_evr_latest: `docs/signals/TopEVR_latest.csv`
- top_evr_dated: `docs/signals/TopEVR_20260717.csv`

## EV > 3% & RiskPenalty < 1%

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## TopN Targets

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## Full Candidate Pool

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 600722.SH | 金牛化工 | 1→2 | 0 | -0.011107 | 0.866012 | 0.011883 | 0.001127 | 0.010163 |
| 2 | 600881.SH | 亚泰集团 | 1→2 | 0 | -0.012778 | 0.874964 | 0.009293 | 0.00135 | 0.009421 |
| 3 | 003001.SZ | 中岩大地 | 1→2 | 0 | -0.012982 | 0.802949 | 0.012815 | 0.001202 | 0.010254 |
| 4 | 000037.SZ | 深南电A | 1→2 | 0 | -0.013471 | 0.87814 | 0.012218 | 0.001193 | 0.011538 |
| 5 | 300795.SZ | 米奥会展 | 1→2 | 0 | -0.015589 | 0.873031 | 0.010469 | 0.00135 | 0.012685 |
| 6 | 002632.SZ | 道明光学 | 2→3 | 0 | -0.015817 | 0.896569 | 0.013836 | 0.001502 | 0.015428 |
| 7 | 300577.SZ | 开润股份 | 1→2 | 0 | -0.01759 | 0.93 | 0.013793 | 0.001677 | 0.016323 |
| 8 | 000722.SZ | 湖南发展 | 1→2 | 0 | -0.020565 | 0.828124 | 0.010347 | 0.001236 | 0.01229 |
| 9 | 002766.SZ | 索菱股份 | 1→2 | 0 | -0.021022 | 0.876149 | 0.011435 | 0.001879 | 0.013175 |
| 10 | 600992.SH | 贵绳股份 | 1→2 | 0 | -0.022125 | 0.898615 | 0.01099 | 0.001321 | 0.015057 |
| 11 | 000899.SZ | 赣能股份 | 1→2 | 0 | -0.023079 | 0.854252 | 0.012047 | 0.001714 | 0.012979 |
| 12 | 002432.SZ | 九安医疗 | 1→2 | 0 | -0.025675 | 0.85631 | 0.014329 | 0.001403 | 0.017413 |
| 13 | 600644.SH | 乐山电力 | 1→2 | 0 | -0.02576 | 0.884946 | 0.013598 | 0.001235 | 0.016884 |
| 14 | 600744.SH | 华银电力 | 1→2 | 0 | -0.027271 | 0.838269 | 0.011864 | 0.001233 | 0.015233 |
| 15 | 001258.SZ | 立新能源 | 2→3 | 0 | -0.027315 | 0.872763 | 0.01324 | 0.001285 | 0.016228 |
| 16 | 603118.SH | 共进股份 | 1→2 | 0 | -0.029514 | 0.88738 | 0.012898 | 0.001485 | 0.01789 |
| 17 | 001369.SZ | 双欣材料 | 1→2 | 0 | -0.030335 | 0.892066 | 0.009657 | 0.001307 | 0.017474 |
| 18 | 605011.SH | 杭州热电 | 1→2 | 0 | -0.032722 | 0.884281 | 0.013496 | 0.001873 | 0.018739 |
| 19 | 600982.SH | 宁波能源 | 1→2 | 0 | -0.035915 | 0.845519 | 0.010616 | 0.001234 | 0.017315 |
| 20 | 002829.SZ | 星网宇达 | 2→3 | 0 | -0.036123 | 0.903073 | 0.009796 | 0.00173 | 0.0197 |
| 21 | 002677.SZ | 浙江美大 | 3→4 | 0 | -0.037594 | 0.786473 | 0.011272 | 0.002113 | 0.016863 |
| 22 | 600236.SH | 桂冠电力 | 1→2 | 0 | -0.038596 | 0.844531 | 0.013176 | 0.002349 | 0.018421 |
| 23 | 600227.SH | 赤天化 | 1→2 | 0 | -0.041819 | 0.88053 | 0.008327 | 0.00181 | 0.019838 |
| 24 | 600095.SH | 湘财股份 | 1→2 | 0 | -0.042503 | 0.913247 | 0.01046 | 0.001599 | 0.021984 |
| 25 | 601369.SH | 陕鼓动力 | 1→2 | 0 | -0.048168 | 0.88799 | 0.008665 | 0.002242 | 0.021522 |
| 26 | 603161.SH | 科华控股 | 1→2 | 0 | -0.051681 | 0.89248 | 0.010749 | 0.002148 | 0.024 |
| 27 | 603580.SH | 艾艾精工 | 4→5 | 0 | -0.058695 | 0.709904 | 0.012664 | 0.002269 | 0.027469 |
| 28 | 600162.SH | 香江控股 | 3→4 | 0 | -0.060642 | 0.74699 | -0.001419 | 0.001326 | 0.022456 |
| 29 | 603567.SH | 珍宝岛 | 2→3 | 0 | -0.077069 | 0.744814 | -0.001915 | 0.001329 | 0.027721 |
| 30 | 000676.SZ | 智度股份 | 3→4 | 0 | -0.088081 | 0.763656 | -0.002498 | 0.001981 | 0.031802 |
| 31 | 000779.SZ | 甘咨询 | 1→2 | 0 | -0.09418 | 0.755954 | -0.00643 | 0.002425 | 0.03017 |
| 32 | 001388.SZ | 信通电子 | 1→2 | 0 | -0.095353 | 0.775453 | -0.00387 | 0.001956 | 0.034 |

