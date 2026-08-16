# Decision Report (20260303)

- signal_date: **20260302**
- exec_date: **20260303**
- regime: **RISK_ON**
- risk_budget: **1**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **88**
- features_base_loaded: **True**
- features_base_rows: **5470**
- features_limit_loaded: **True**
- features_limit_rows: **5470**
- truth_close_loaded: **True**
- truth_close_rows: **5470**
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

- candidates_snapshot: `data/decision/decision_candidates_20260302.csv`
- execution_table: `data/decision/decision_execution_20260303.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260303.csv`

## TopN Targets

| rank | ts_code | name | weight | EV | P_fill | E_ret |
|---:|---|---|---:|---:|---:|---:|
| 1 | 300164.SZ | 通源石油 | 0.1 | 0.046551 | 0.947149 | 0.049994 |
| 2 | 000554.SZ | 泰山石油 | 0.1 | 0.045629 | 0.927675 | 0.050049 |
| 3 | 601808.SH | 中海油服 | 0.1 | 0.040649 | 0.98 | 0.042295 |
| 4 | 601857.SH | 中国石油 | 0.1 | 0.039486 | 0.98 | 0.041108 |
| 5 | 301302.SZ | 华如科技 | 0.1 | 0.03878 | 0.98 | 0.040388 |
| 6 | 600108.SH | 亚盛集团 | 0.1 | 0.026354 | 0.977122 | 0.02779 |
| 7 | 301213.SZ | 观想科技 | 0.1 | 0.021307 | 0.98 | 0.022558 |
| 8 | 002490.SZ | 山东墨龙 | 0.1 | 0.02102 | 0.5941 | 0.036728 |
| 9 | 002389.SZ | 航天彩虹 | 0.1 | 0.018817 | 0.98 | 0.020017 |
| 10 | 603488.SH | 展鹏科技 | 0.1 | 0.017312 | 0.98 | 0.018482 |
