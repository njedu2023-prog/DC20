# Decision Report (20260820)

- signal_date: **20260819**
- exec_date: **20260820**
- exit_date: **20260821**
- requested_trade_date: **20260819**
- regime: **RISK_ON**
- risk_budget: **1**
- regime_reason: **risk_metrics_normal**
- guardrail_reason: **PASS**
- execution_gate: **NO_TRADE_MISSING_LEARNING_ACCEPTANCE**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **32**
- features_base_loaded: **True**
- features_base_rows: **5541**
- features_limit_loaded: **True**
- features_limit_rows: **5541**
- truth_close_loaded: **True**
- truth_close_rows: **5541**
- meta_loaded: **True**

## Engine Status

- p_fill_pred_src: **rule**
- p_fill_model_loaded: **False**
- p_fill_model_acceptance_pass: **False**
- p_fill_model_kind: **lgbm**
- p_fill_degrade_reason: **model_rejected_by_learning_acceptance:acceptance_file_missing**
- eret_pred_src: **rule**
- eret_model_loaded: **False**
- eret_model_acceptance_pass: **False**
- eret_model_kind: **none**
- eret_degrade_reason: **model_rejected_by_learning_acceptance:acceptance_file_missing**

## Intraday Risk Status

- fields_present: **True**
- available_rows: **32** / **32**
- hard_risk_rows: **5**
- intraday_ev_bonus_mean: **0**
- intraday_penalty_extra_mean: **0**
- intraday_execution_penalty_mean: **0.004079**

## Decision Diagnostics

- primary_no_trade_reason: **guardrail_stop:NO_TRADE_MISSING_LEARNING_ACCEPTANCE**
- rows_scored: **32**
- selected_rows: **0**
- positive_ev_rows: **6**
- positive_ev_base_rows: **6**
- positive_e_ret_rows: **27**
- high_pfill_rows: **0**
- low_risk_rows: **12**
- max_EV: **0.003527**
- max_EV_base: **0.003527**
- max_E_ret: **0.016635**
- mean_cost: **0.001485**
- mean_risk_penalty: **0.013167**
- mean_extra_penalty_total: **0**

## Artifacts

- candidates_snapshot: `data/decision/decision_candidates_20260819.csv`
- execution_table: `data/decision/decision_execution_20260820.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260820.csv`
- top_evr_latest: `docs/signals/TopEVR_latest.csv`
- top_evr_dated: `docs/signals/TopEVR_20260819.csv`

## EV > 3% & RiskPenalty < 1%

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## TopN Targets

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|

## Full Candidate Pool

| rank | ts_code | name | 晋阶 | weight | EV | P_fill | E_ret | Cost | RiskPenalty |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 603102.SH | 百合股份 | 1→2 | 0 | 0.003527 | 0.730271 | 0.013153 | 0.001222 | 0.004857 |
| 2 | 600127.SH | 金健米业 | 3→4 | 0 | 0.003173 | 0.647288 | 0.016635 | 0.00122 | 0.006374 |
| 3 | 000505.SZ | 京粮控股 | 3→4 | 0 | 0.001964 | 0.722361 | 0.012988 | 0.001196 | 0.006222 |
| 4 | 603113.SH | 金能科技 | 1→2 | 0 | 0.000957 | 0.726468 | 0.013683 | 0.001224 | 0.00776 |
| 5 | 601011.SH | 宝泰隆 | 1→2 | 0 | 0.000435 | 0.70502 | 0.014784 | 0.00183 | 0.008158 |
| 6 | 002953.SZ | 日丰股份 | 2→3 | 0 | 0.000057 | 0.775398 | 0.014371 | 0.001242 | 0.009844 |
| 7 | 002543.SZ | 万和电气 | 1→2 | 0 | -0.000173 | 0.697225 | 0.011996 | 0.001195 | 0.007342 |
| 8 | 600403.SH | 大有能源 | 1→2 | 0 | -0.000252 | 0.682157 | 0.01205 | 0.001198 | 0.007274 |
| 9 | 603366.SH | 日出东方 | 1→2 | 0 | -0.000365 | 0.712054 | 0.0121 | 0.001207 | 0.007773 |
| 10 | 000526.SZ | 学大教育 | 2→3 | 0 | -0.001147 | 0.724714 | 0.013647 | 0.001231 | 0.009807 |
| 11 | 603395.SH | 红四方 | 3→4 | 0 | -0.001723 | 0.784021 | 0.013379 | 0.001626 | 0.010586 |
| 12 | 000736.SZ | 中交发展 | 1→2 | 0 | -0.001773 | 0.713182 | 0.011685 | 0.001325 | 0.008781 |
| 13 | 601015.SH | 陕西黑猫 | 1→2 | 0 | -0.001936 | 0.766202 | 0.013529 | 0.001331 | 0.010971 |
| 14 | 600783.SH | 鲁信创投 | 1→2 | 0 | -0.002106 | 0.771073 | 0.013125 | 0.001291 | 0.010935 |
| 15 | 002333.SZ | 罗普斯金 | 1→2 | 0 | -0.002318 | 0.729012 | 0.012202 | 0.001983 | 0.009231 |
| 16 | 000020.SZ | 深华发Ａ | 2→3 | 0 | -0.003967 | 0.774106 | 0.010507 | 0.00131 | 0.010791 |
| 17 | 002412.SZ | 汉森制药 | 1→2 | 0 | -0.004015 | 0.730659 | 0.013154 | 0.001277 | 0.012349 |
| 18 | 600610.SH | 中毅达 | 1→2 | 0 | -0.00407 | 0.777096 | 0.012053 | 0.001385 | 0.012051 |
| 19 | 603506.SH | 南都物业 | 1→2 | 0 | -0.0042 | 0.778876 | 0.013225 | 0.00136 | 0.013141 |
| 20 | 002900.SZ | 哈三联 | 1→2 | 0 | -0.004254 | 0.728347 | 0.013086 | 0.001391 | 0.012394 |
| 21 | 603290.SH | 斯达半导 | 1→2 | 0 | -0.005642 | 0.765936 | 0.011101 | 0.001367 | 0.012778 |
| 22 | 000059.SZ | 华锦股份 | 1→2 | 0 | -0.005998 | 0.739617 | 0.011214 | 0.00132 | 0.012973 |
| 23 | 000723.SZ | 美锦能源 | 1→2 | 0 | -0.00708 | 0.771397 | 0.012045 | 0.001572 | 0.0148 |
| 24 | 002040.SZ | 南 京 港 | 1→2 | 0 | -0.007195 | 0.808419 | 0.010832 | 0.001513 | 0.014439 |
| 25 | 603801.SH | 志邦家居 | 1→2 | 0 | -0.009509 | 0.753098 | 0.010214 | 0.001952 | 0.01525 |
| 26 | 603848.SH | 好太太 | 1→2 | 0 | -0.010393 | 0.780091 | 0.010884 | 0.002207 | 0.016676 |
| 27 | 001301.SZ | 尚太科技 | 1→2 | 0 | -0.013511 | 0.76241 | 0.008199 | 0.001762 | 0.018 |
| 28 | 001277.SZ | 速达股份 | 1→2 | 0 | -0.020353 | 0.547517 | -0.000159 | 0.001186 | 0.01908 |
| 29 | 001400.SZ | 江顺科技 | 1→2 | 0 | -0.02622 | 0.6019 | -0.000268 | 0.001534 | 0.024524 |
| 30 | 001338.SZ | 永顺泰 | 1→2 | 0 | -0.029946 | 0.673574 | -0.000264 | 0.001768 | 0.028 |
| 31 | 000560.SZ | 我爱我家 | 1→2 | 0 | -0.031566 | 0.667252 | -0.00039 | 0.002196 | 0.02911 |
| 32 | 600371.SH | 万向德农 | 2→3 | 0 | -0.033553 | 0.694944 | -0.003434 | 0.002109 | 0.029058 |

