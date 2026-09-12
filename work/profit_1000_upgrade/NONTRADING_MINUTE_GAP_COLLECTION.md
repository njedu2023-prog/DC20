# 非交易日续算后的独立分钟来源补采登记 · 第二轮

仅登记研究数据采集，不执行结算、模型训练或生产替换。原始分钟主链和本独立非交易日补充分支分开验收；组合标签绝不作为原始分钟主链的 prior。

## 当前精确范围

- 计划 `NONTRADING_MINUTE_GAP_COLLECTION.json`；SHA-256 `aaed4d1e50056cbb9c56f3821cde7e2f8c373f84b4fd74459681df52f03e82ec`，2667 bytes。
- 绑定已完成真实组合回放标签 SHA `dbc2a10b96da03ef0ca3a32887380258282a5eac417853b26ec382f40e0bbc43`。
- 仅三对：20240506／600234.SH、20240507／600083.SH、20260715／603580.SH；全部为唯一预检，每对一次、零重试。
- 沿用冻结 `minute_gap_collect.expected_plan` 和 canonical JSON，`stk_mins` 09:31–15:00 恰240行。BAR_END 仍为研究假设，未获得提供方确认。
- 工作流显式使用本独立计划，不修改正常主链 `MINUTE_GAP_COLLECTION.json`。

## 真实依据与完整性

本轮来源是完整结束的组合回放，不是运行中临时文件。6753行／910个D日完整保留；旧6713条终态除cohort标志外全字段不变，其余非固定六病例的6747行与原始两轮主链完全相同。

原六个非交易日病例中三条已实际得到10:00／封板延持规则的结算，另三条因继续持有暴露上述新分钟缺口。三条 pending 的净收益、条件净收益、席位净收益均为 null；未将缺失或停牌视为0。原买入价、scheduled T+1、45bp费用保持不变。

三对与先前正常2384+134+24及独立补充6共2548次唯一请求无交集。实际组合根三对 data/meta 文件均不存在，来源清单也未登记，禁止覆盖原文件。各日期必须在原始交易日历内；未来D>=20260914的结果不读取。

## 冻结来源

实际已验收目录：`upgrade-candidate-evidence-20260913.VdRAd5/nontrading-replay-9wv9pcqv`。

| 文件 | SHA-256 |
| --- | --- |
| combined_labels.json | `dbc2a10b96da03ef0ca3a32887380258282a5eac417853b26ec382f40e0bbc43` |
| acceptance.json | `90d3b500ffd17c92ed69811652caa1d66008810f42cf1c7a8537c7d49057dcb1` |
| source_provenance.json | `5f10972ae62eafabf5e6c910d4d5ef15277f72c3135f540264c499a65468b8f0` |
| combined_source_admission.json | `ec1426435c0f1d60e7d3bf7e972710ff5edc53a7a8993b62bb1a11448bcdfcc1` |
| 原始两轮主链标签 | `d159a22b72d0e485fd4c82351170e9861f5d059a25379b67026ee8cc7942159e` |
| 第一轮独立补充计划 | `6023bef2a3e88cc434eb2d7b479a18d4d5b92a9cace8a6c85900dcd853b2d400` |
| 第一轮独立补充ZIP | `a87d711591afdac624ab93f370542d1935fb2f4dee12ebf66c7ce8a44c9bac71` |

第一轮独立补充 run34711597028（head e49144b79be894d79a99a06f28a821fb098a8976）六对全部来源验收通过，原artifact内计划保持不可变。本轮是其后的独立精确补采，不重跑旧计划，不更改旧收据身份。

## 后续验收

新来源必须独立绑定真实run／commit／ZIP摘要及每轮原计划。新补充分支消费者须逐轮验收先前组合标签和来源，只重放固定六病例，保留全部既有终态和负收益；原18个非交易日证明上下文不能延长或伪造。不得把多轮摘要冒充单一GitHub收据。当前固定评估训练数据已达门槛，但验证期173／186日不完整，训练仍被阻止；不能为获得模型而删除这13日。
