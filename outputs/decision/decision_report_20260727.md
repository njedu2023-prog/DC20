# Decision Report (20260727)

- signal_date: **20260724**
- exec_date: **20260727**
- exit_date: **20260728**
- requested_trade_date: **auto**
- regime: **CAUTION**
- risk_budget: **0.7**
- regime_reason: **tail_risk_mean=0.2892,volatility_mean=0.0558**
- guardrail_reason: **CAUTION:open_board_max=5.0,tail_risk_mean=0.2892,volatility_mean=0.0558**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **41**
- features_base_loaded: **True**
- features_base_rows: **5526**
- features_limit_loaded: **True**
- features_limit_rows: **5526**
- truth_close_loaded: **True**
- truth_close_rows: **5526**
- meta_loaded: **True**

## Engine Status

- p_fill_pred_src: **model:lgbm**
- p_fill_model_loaded: **True**
- p_fill_model_kind: **lgbm**
- p_fill_degrade_reason: **none**
- eret_pred_src: **rule**
- eret_model_loaded: **True**
- eret_model_kind: **none**
- eret_degrade_reason: **model_rejected_by_learning_acceptance:selected_model_pass_false**

## Intraday Risk Status

- fields_present: **True**
- available_rows: **36** / **36**
- hard_risk_rows: **4**
- intraday_ev_bonus_mean: **0**
- intraday_penalty_extra_mean: **0**
- intraday_execution_penalty_mean: **0.003983**

## Decision Diagnostics

- primary_no_trade_reason: **positive_e_ret_cannot_cover_cost_and_risk**
- rows_scored: **36**
- selected_rows: **0**
- positive_ev_rows: **0**
- positive_ev_base_rows: **0**
- positive_e_ret_rows: **32**
- high_pfill_rows: **5**
- low_risk_rows: **1**
- max_EV: **-0.000105**
- max_EV_base: **-0.000105**
- max_E_ret: **0.016346**
- mean_cost: **0.00164**
- mean_risk_penalty: **0.019374**
- mean_extra_penalty_total: **0**

## Artifacts

- candidates_snapshot: `data/decision/decision_candidates_20260724.csv`
- execution_table: `data/decision/decision_execution_20260727.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260727.csv`
- top_evr_latest: `docs/signals/TopEVR_latest.csv`
- top_evr_dated: `docs/signals/TopEVR_20260724.csv`

## EV > 3% & RiskPenalty < 1%

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## TopN Targets

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## Full Candidate Pool

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 000011.SZ | 深物业A | 2→3 | 0 | -0.000105 | 0.866964 | 0.01184 | 0.001214 | 0.009157 |
| 2 | 601606.SH | 长城军工 | 2→3 | 0 | -0.001686 | 0.851678 | 0.013079 | 0.001234 | 0.011592 |
| 3 | 002208.SZ | 合肥城建 | 1→2 | 0 | -0.002228 | 0.830023 | 0.012733 | 0.001121 | 0.011675 |
| 4 | 002879.SZ | 长缆科技 | 4→5 | 0 | -0.002519 | 0.93 | 0.015282 | 0.001582 | 0.015149 |
| 5 | 000668.SZ | 荣丰控股 | 2→3 | 0 | -0.003656 | 0.853999 | 0.011915 | 0.00122 | 0.012611 |
| 6 | 002298.SZ | 中电鑫龙 | 2→3 | 0 | -0.004178 | 0.865773 | 0.014055 | 0.001231 | 0.015116 |
| 7 | 600698.SH | 湖南天雁 | 2→3 | 0 | -0.004763 | 0.911864 | 0.011592 | 0.001348 | 0.013986 |
| 8 | 000417.SZ | 合百集团 | 1→2 | 0 | -0.005531 | 0.845958 | 0.008701 | 0.001195 | 0.011696 |
| 9 | 000533.SZ | 顺钠股份 | 2→3 | 0 | -0.006011 | 0.688366 | 0.016346 | 0.002119 | 0.015145 |
| 10 | 002300.SZ | 太阳电缆 | 2→3 | 0 | -0.006708 | 0.888905 | 0.013082 | 0.001472 | 0.016864 |
| 11 | 603976.SH | 正川股份 | 1→2 | 0 | -0.006981 | 0.869073 | 0.00958 | 0.001217 | 0.01409 |
| 12 | 002303.SZ | 美盈森 | 1→2 | 0 | -0.007201 | 0.857576 | 0.010926 | 0.001808 | 0.014763 |
| 13 | 600984.SH | 建设机械 | 2→3 | 0 | -0.007801 | 0.891582 | 0.011026 | 0.001356 | 0.016275 |
| 14 | 002498.SZ | 汉缆股份 | 2→3 | 0 | -0.008657 | 0.845745 | 0.011456 | 0.001962 | 0.016383 |
| 15 | 002265.SZ | 建设工业 | 1→2 | 0 | -0.009064 | 0.850486 | 0.01111 | 0.001995 | 0.016518 |
| 16 | 002083.SZ | 孚日股份 | 2→3 | 0 | -0.00917 | 0.93 | 0.014178 | 0.001786 | 0.020571 |
| 17 | 603068.SH | 博通集成 | 1→2 | 0 | -0.009261 | 0.877204 | 0.011658 | 0.001258 | 0.01823 |
| 18 | 001229.SZ | 魅视科技 | 1→2 | 0 | -0.009713 | 0.892871 | 0.010517 | 0.00134 | 0.017764 |
| 19 | 002388.SZ | 新亚制程 | 1→2 | 0 | -0.009734 | 0.91037 | 0.011011 | 0.00153 | 0.018229 |
| 20 | 002374.SZ | 中锐股份 | 1→2 | 0 | -0.009795 | 0.874839 | 0.009499 | 0.002041 | 0.016065 |
| 21 | 002189.SZ | 中光学 | 1→2 | 0 | -0.009816 | 0.842127 | 0.009269 | 0.001816 | 0.015806 |
| 22 | 000670.SZ | 盈方微 | 1→2 | 0 | -0.009931 | 0.885059 | 0.011282 | 0.001382 | 0.018534 |
| 23 | 603956.SH | 威派格 | 2→3 | 0 | -0.010501 | 0.909447 | 0.012314 | 0.001463 | 0.020237 |
| 24 | 603221.SH | 爱丽家居 | 4→5 | 0 | -0.01096 | 0.509229 | 0.013052 | 0.002239 | 0.015367 |
| 25 | 600178.SH | 东安动力 | 1→2 | 0 | -0.011125 | 0.889829 | 0.011565 | 0.001864 | 0.019552 |
| 26 | 603690.SH | 至纯科技 | 1→2 | 0 | -0.01187 | 0.865476 | 0.011299 | 0.001326 | 0.020324 |
| 27 | 002218.SZ | 拓日新能 | nan | 0 | -0.01264 | 0.860373 | 0.007524 | 0.001669 | 0.017445 |
| 28 | 002012.SZ | 凯恩股份 | 1→2 | 0 | -0.013188 | 0.853514 | 0.007547 | 0.001956 | 0.017674 |
| 29 | 603726.SH | 朗迪集团 | 1→2 | 0 | -0.013251 | 0.852347 | 0.008922 | 0.001236 | 0.01962 |
| 30 | 600539.SH | 狮头股份 | 1→2 | 0 | -0.01852 | 0.899036 | 0.007337 | 0.001672 | 0.023443 |
| 31 | 605198.SH | 安德利 | 1→2 | 0 | -0.019007 | 0.870786 | 0.007942 | 0.002255 | 0.023668 |
| 32 | 002199.SZ | 东晶电子 | 2→3 | 0 | -0.023658 | 0.892798 | 0.010329 | 0.001854 | 0.031026 |
| 33 | 600617.SH | 国新能源 | 1→2 | 0 | -0.025455 | 0.722579 | -0.001493 | 0.001989 | 0.022388 |
| 34 | 003001.SZ | 中岩大地 | 2→3 | 0 | -0.043957 | 0.770497 | -0.002681 | 0.00291 | 0.038981 |
| 35 | 603580.SH | 艾艾精工 | 1→2 | 0 | -0.048286 | 0.771403 | -0.001506 | 0.001602 | 0.045522 |
| 36 | 000595.SZ | 新能股份 | 3→4 | 0 | -0.050825 | 0.770677 | -0.003937 | 0.00179 | 0.046 |

