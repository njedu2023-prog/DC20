# Decision Report (20260306)

- signal_date: **20260305**
- exec_date: **20260306**
- regime: **RISK_ON**
- risk_budget: **1**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **69**
- features_base_loaded: **True**
- features_base_rows: **5476**
- features_limit_loaded: **True**
- features_limit_rows: **5476**
- truth_close_loaded: **True**
- truth_close_rows: **5476**
- meta_loaded: **True**

## Engine Status

- p_fill_pred_src: **model:lgbm**
- p_fill_model_loaded: **True**
- p_fill_model_kind: **lgbm**
- p_fill_degrade_reason: **none**
- eret_pred_src: **model:lgbm**
- eret_model_loaded: **True**
- eret_model_kind: **lgbm**
- eret_degrade_reason: **none**

## Artifacts

- candidates_snapshot: `data/decision/decision_candidates_20260305.csv`
- execution_table: `data/decision/decision_execution_20260306.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260306.csv`

## TopN Targets

| rank | ts_code | name | weight | EV | P_fill | E_ret |
|---:|---|---|---:|---:|---:|---:|
| 1 | 002195.SZ | 岩山科技 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 2 | 300323.SZ | 华灿光电 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 3 | 601179.SH | 中国西电 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 4 | 600875.SH | 东方电气 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 5 | 601727.SH | 上海电气 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 6 | 300303.SZ | 聚飞光电 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 7 | 300895.SZ | 铜牛信息 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 8 | 600703.SH | 三安光电 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 9 | 600744.SH | 华银电力 | 0.1 | 0.001383 | 0.98 | 0.002228 |
| 10 | 002520.SZ | 日发精机 | 0.1 | 0.001383 | 0.98 | 0.002228 |
