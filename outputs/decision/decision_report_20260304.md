# Decision Report (20260304)

- signal_date: **20260303**
- exec_date: **20260304**
- regime: **RISK_ON**
- risk_budget: **1**
- input_mode: **pred_plus_fs**
- fs_degrade_reason: **none**

## Input Status

- pred_loaded: **True**
- pred_rows: **83**
- features_base_loaded: **True**
- features_base_rows: **5472**
- features_limit_loaded: **True**
- features_limit_rows: **5472**
- truth_close_loaded: **True**
- truth_close_rows: **5472**
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

- candidates_snapshot: `data/decision/decision_candidates_20260303.csv`
- execution_table: `data/decision/decision_execution_20260304.csv`
- learning_table: `data/decision/decision_learning.csv`
- weights_latest: `docs/weights/weights_latest.csv`
- weights_dated: `docs/weights/weights_20260304.csv`

## TopN Targets

| rank | ts_code | name | weight | EV | P_fill | E_ret |
|---:|---|---|---:|---:|---:|---:|
| 1 | 600108.SH | 亚盛集团 | 0.1 | 0.048105 | 0.98 | 0.049903 |
| 2 | 603318.SH | 水发燃气 | 0.1 | 0.028478 | 0.98 | 0.029876 |
| 3 | 301302.SZ | 华如科技 | 0.1 | 0.02691 | 0.98 | 0.028275 |
| 4 | 000407.SZ | 胜利股份 | 0.1 | 0.019964 | 0.98 | 0.021187 |
| 5 | 002490.SZ | 山东墨龙 | 0.1 | 0.017967 | 0.98 | 0.01915 |
| 6 | 300332.SZ | 天壕能源 | 0.1 | 0.015696 | 0.98 | 0.016833 |
| 7 | 600688.SH | 上海石化 | 0.1 | 0.014085 | 0.98 | 0.015189 |
| 8 | 002040.SZ | 南 京 港 | 0.1 | 0.01193 | 0.98 | 0.012989 |
| 9 | 600403.SH | 大有能源 | 0.1 | 0.009527 | 0.98 | 0.010538 |
| 10 | 600871.SH | 石化油服 | 0.1 | 0.005631 | 0.98 | 0.006563 |
