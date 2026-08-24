# DC20 可实现盈利模型 v2：研究复跑包

## 结论

本包只用于离线研究，结论为 **NOT_READY**。LR 与 HGB 在原型的 23 项检查中都只有 10 项满足；这个计数不是完成百分比，也不是放行分数。两者在最后 180 个 OOF 日期上的绝对 Top2 净收益和双成本压力收益均为负，且相对冻结 `p_fill Top2` 的收益提升置信区间都跨越 0。不得接前端、不得生成交易动作、不得覆盖晋级模型或正式模型。

最后 180 个日期已经被查看。它们只是 `retrospective evaluation/exploration`，不是独立、未触碰的确认集，也不能作为 forward release evidence。真正的放行证据仍需要从冻结方案之后开始追加的前瞻 Shadow 账本、真实成交/可买观测和跌停退出真值；本包没有实现完整 release protocol。

## 固定研究设计

- 宇宙：冻结晋级 OOF 的每日 Top10；晋级模型和晋级排序完全不训练、不改写。
- 上游可买组件：直接冻结 OOF `p_fill`，只作为 `P(fill)` 组件和同日基线，不进入条件模型特征，也不与条件模型统计量合并。
- 条件分布：在代理可买样本上训练 `BIG_LOSS / NON_PROFIT / PROFIT` 三分类；固定候选只有 LR 和 HGB，各运行一次，不做超参数搜索。
- 条件收益：另外拟合条件净收益均值和 10% 分位预测。当前字段名 `expected_net_return_lcb` 仅沿用本轮原型命名；它是模型 q10/均值下取小值，不是带覆盖保证的统计置信下界。
- 权威联合概率：严格为 `q = frozen_p_fill_probability * predicted_conditional_profit_probability`，没有乘积后的 Platt 或其他校准；逐行断言 `q <= P(fill)` 且 `q <= P(profit|fill)`。
- 排序：先按 `q` 降序，再依合同按 `expected_net_return_lcb` 降序、条件大跌概率升序、`ts_code` 升序。
- 记账：先冻结完整 Top2，再等标签成熟；固定两个等权资金槽，缺位按现金 0 计算。
- 基线：只在共同成熟日期上同时比较本模型 Top2、晋级 Top2、晋级 Top10 等权、冻结 `p_fill Top2`。
- 时间约束：外层 walk-forward 使用严格 SSE 开市日和至少 2 个开市日 embargo；每折都断言 `fit 标签退出日 < component`、`component 标签退出日 < final audit`、`final audit 标签退出日 < test`。

## 精确输入绑定

| 输入 | SHA-256 |
| --- | --- |
| `historical_oof_top10_ledger.csv.gz` | `b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd` |
| 冻结 three-engine OOF | `c768cb0eb019fba6be7ca41284841006195dd54bf4d641f426d2fbbf513a4ebd` |
| SSE `trade_cal` | `150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748` |
| 48 列特征清单 | `9f403117278b73653014a3682442072f026d8e73abef37d318086565dae23425` |

输入任一漂移都会 fail closed。GitHub `main@cdbc43f67401c876d98f61585bea6d9375117e5b` 自有默认 OOF 路径正是上述精确版本，可以在 DC20 内独立复跑；本地旧工作副本若哈希不符必须拒绝运行，不能用临时文件冒充仓库依赖。

## 实测结果

最后 180 个 OOF 日期中有 178 个共同成熟记账日。以下收益均为每日期两个固定资金槽的平均净收益。

| 指标 | LR | HGB | 同日冻结 `p_fill Top2` |
| --- | ---: | ---: | ---: |
| 联合事件 AUC | 0.5732 | 0.5518 | — |
| 联合 Brier / 同折基线 | 0.23940 / 0.23474 | 0.23895 / 0.23474 | — |
| Brier 改善 95% CI | [-0.00704, -0.00214] | [-0.00763, -0.00088] | — |
| 联合概率 ECE | 10.11% | 8.45% | — |
| 条件盈利 AUC | 0.5078 | 0.5045 | — |
| Top2 平均净收益 | -0.9705% | -0.8466% | -0.9611% |
| 双成本压力收益 | -1.4066% | -1.2764% | -1.3985% |
| Top2 盈利率 | 39.61% | 40.73% | 38.76% |
| Top2 大跌率 | 45.22% | 42.13% | 48.31% |
| 相对 `p_fill` 收益提升 | -0.0094pp | +0.1145pp | — |
| 收益提升 95% CI | [-0.6338, +0.6735]pp | [-0.7264, +0.9645]pp | — |
| 相对 `p_fill` 盈利率提升 | +0.8427pp | +1.9663pp | — |
| 盈利率提升 95% CI | [-3.9326, +5.8989]pp | [-3.0969, +7.5843]pp | — |
| 2→3 平均槽位收益 | -0.8112% | -0.6021% | — |
| 3→4 平均槽位收益 | -1.1646% | -1.1640% | — |

HGB 的点估计略优于 `p_fill Top2`，且大跌率更低，但联合概率质量显著劣于同折常数基线、条件盈利 AUC 接近随机、收益提升区间跨 0、绝对收益和双成本收益均为负，因此没有可放行证据。

## 未通过门

两名候选均未通过同一 13 项：确认段联合 Brier 改善为正及其区间下界为正、ECE 不高于 8%、Top2 绝对收益为正、双成本收益非负、相对 `p_fill` 的收益和盈利率提升区间下界为正、相对全部基线的两类提升区间下界为正、两个阶段收益不显著为负、追加式前瞻 Shadow 180 日、真实成交观测、跌停退出真值。

## 文件与复核

- `prototype_v2.py`：独立研究 runner；只写本目录指定输出。
- `tests/test_prototype_v2.py`：乘积/上界、`p_fill` 组件隔离、主排序和全部 tie-break、固定资金槽、三层退出日边界、fail-closed、共同成熟面板测试。
- `outputs-lr/validation_report.json`、`outputs-hgb/validation_report.json`：逐折时间证明、开发段、已查看的回溯段、23 项原型检查；检查计数不代表完成度。
- `outputs-lr/oof_lr_distribution.csv.gz`、`outputs-hgb/oof_hgb_distribution.csv.gz`：610 个 OOF 日期、4,996 行。
- `ARTIFACT_INDEX.json`：输入、制品哈希和状态索引。

测试命令：

```bash
.venv/bin/python -m pytest -q work/executable-profit-model-v2-20260824/tests/test_prototype_v2.py
```

实测为 `8 passed`。完整复跑命令必须显式提供精确 OOF；本轮停止后未继续为了复跑哈希而重复训练，现有 OOF 由确定性 gzip（`mtime=0`）写出，制品哈希见索引。
