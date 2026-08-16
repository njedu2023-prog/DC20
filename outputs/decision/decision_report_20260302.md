# Decision Report (20260302)

- signal_date: **20260227**
- exec_date: **20260302**
- requested_trade_date: **20260227**
- regime: **RISK_ON**
- risk_budget: **1**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **75**
- features_base_loaded: **True**
- features_base_rows: **5471**
- features_limit_loaded: **True**
- features_limit_rows: **5471**
- truth_close_loaded: **True**
- truth_close_rows: **5471**
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

- candidates_snapshot: `data/decision/decision_candidates_20260227.csv`
- execution_table: `data/decision/decision_execution_20260302.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260302.csv`

## TopN Targets

| rank | ts_code | name | weight | EV | P_fill | E_ret |
|---:|---|---|---:|---:|---:|---:|
| 1 | 000035.SZ | 中国天楹 | 0.1 | 0.044872 | 0.98 | 0.046604 |
| 2 | 603318.SH | 水发燃气 | 0.1 | 0.010205 | 0.98 | 0.01123 |
| 3 | 002843.SZ | 泰嘉股份 | 0.1 | 0.005002 | 0.98 | 0.00592 |
| 4 | 002177.SZ | 御银股份 | 0.1 | 0.000033 | 0.98 | 0.00085 |
| 5 | 603232.SH | 格尔软件 | 0.1 | -0.00026 | 0.972387 | 0.000555 |
| 6 | 002216.SZ | 三全食品 | 0.1 | -0.005866 | 0.98 | -0.005169 |
| 7 | 300830.SZ | 金现代 | 0.1 | -0.005967 | 0.98 | -0.005273 |
| 8 | 688343.SH | 云天励飞-U | 0.1 | -0.006492 | 0.98 | -0.005808 |
| 9 | 001216.SZ | 华瓷股份 | 0.1 | -0.007284 | 0.98 | -0.006617 |
| 10 | 600108.SH | 亚盛集团 | 0.1 | -0.008405 | 0.98 | -0.00776 |
