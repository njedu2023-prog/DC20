# Decision Report (20260305)

- signal_date: **20260304**
- exec_date: **20260305**
- regime: **RISK_ON**
- risk_budget: **1**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **44**
- features_base_loaded: **True**
- features_base_rows: **5474**
- features_limit_loaded: **True**
- features_limit_rows: **5474**
- truth_close_loaded: **True**
- truth_close_rows: **5474**
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

- candidates_snapshot: `data/decision/decision_candidates_20260304.csv`
- execution_table: `data/decision/decision_execution_20260305.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260305.csv`

## TopN Targets

| rank | ts_code | name | weight | EV | P_fill | E_ret |
|---:|---|---|---:|---:|---:|---:|
| 1 | 600313.SH | 农发种业 | 0.1 | 0.033293 | 0.98 | 0.034789 |
| 2 | 600108.SH | 亚盛集团 | 0.1 | 0.014036 | 0.98 | 0.015138 |
| 3 | 002167.SZ | 东方锆业 | 0.1 | 0.008716 | 0.98 | 0.00971 |
| 4 | 000010.SZ | 美丽生态 | 0.1 | 0.004964 | 0.98 | 0.005882 |
| 5 | 002357.SZ | 富临运业 | 0.1 | 0.003041 | 0.98 | 0.003919 |
| 6 | 600759.SH | 洲际油气 | 0.1 | 0.001222 | 0.975216 | 0.002073 |
| 7 | 002162.SZ | 悦心健康 | 0.1 | -0.001367 | 0.98 | -0.000579 |
| 8 | 600545.SH | 卓郎智能 | 0.1 | -0.001393 | 0.98 | -0.000605 |
| 9 | 002272.SZ | 川润股份 | 0.1 | -0.004073 | 0.98 | -0.00334 |
| 10 | 603318.SH | 水发燃气 | 0.1 | -0.00698 | 0.469574 | -0.013161 |
