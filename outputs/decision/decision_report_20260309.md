# Decision Report (20260309)

- signal_date: **20260306**
- exec_date: **20260309**
- requested_trade_date: **auto**
- regime: **RISK_ON**
- risk_budget: **1**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **74**
- features_base_loaded: **True**
- features_base_rows: **5477**
- features_limit_loaded: **True**
- features_limit_rows: **5477**
- truth_close_loaded: **True**
- truth_close_rows: **5477**
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

- candidates_snapshot: `data/decision/decision_candidates_20260306.csv`
- execution_table: `data/decision/decision_execution_20260309.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260309.csv`

## TopN Targets

| rank | ts_code | name | weight | EV | P_fill | E_ret |
|---:|---|---|---:|---:|---:|---:|
| 1 | 601567.SH | 三星医疗 | 0.1 | 0.018557 | 0.98 | 0.019752 |
| 2 | 605268.SH | 王力安防 | 0.1 | 0.018252 | 0.98 | 0.019441 |
| 3 | 002227.SZ | 奥 特 迅 | 0.1 | 0.015158 | 0.98 | 0.016284 |
| 4 | 000601.SZ | 韶能股份 | 0.1 | 0.011673 | 0.98 | 0.012728 |
| 5 | 301265.SZ | 华新环保 | 0.1 | 0.010965 | 0.98 | 0.012005 |
| 6 | 301032.SZ | 新柴股份 | 0.1 | 0.00453 | 0.98 | 0.005439 |
| 7 | 603778.SH | 国晟科技 | 0.1 | 0.003963 | 0.98 | 0.00486 |
| 8 | 600821.SH | 金开新能 | 0.1 | 0.00271 | 0.98 | 0.003581 |
| 9 | 600825.SH | 新华传媒 | 0.1 | 0.000716 | 0.98 | 0.001547 |
| 10 | 600108.SH | 亚盛集团 | 0.1 | -0.0021 | 0.98 | -0.001327 |
