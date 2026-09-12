# 独立研究来源 v2：已接线，真实重放与放行仍需证据

研究接线已经实现；这不表示真实补采已完成、模型已拟合或正式入口已激活。现有 `PLAN.json`、
`COLLECTION.json`、旧镜像 marker、历史标签与原始证据均保留原字节。

## 新版本的固定边界

- 新增版本化计划及入口政策，明确绑定 canonical auction source policy
  `dc20_research_canonical_stk_auction_20260912_v2`。
- 明确绑定真实 09:31–15:00 的研究分钟源及
  `RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED`，不能重命名旧元数据。
- 退出规则、45bp/90bp 成本、Ridge(alpha=10, solver=svd)、历史日期切分、
  完整日门槛与 20260914 起前向留出均不变；不做新的调参搜索。
- 晋级模型及 D 日 OOF 晋级名次继续作为固定输入与比较基线，不回填未来预测。

## 已一起接线的入口

1. `labels.build_labels`：新买价读取模块取代旧 `_verified_auction_sources_v2` /
   `_entry_price_v2`，退出分钟回调改为研究 `minute_truth.load`。
   每行保存实际价格来源、缺源原因、政策组合及源 SHA。
2. `collect.official_call/_fetch_one/collect_history`：原 HTTP bytes 直接交给新
   codec；canonical、研究分钟、日线/限价分支分开。保存真实参数、缺源回执及 SHA。
   不拿操作性失败冒充有效缺源，不覆盖损坏的来源。
3. `run.load_plan/initialize_mirror/prepare_history/evaluate`：增加新计划入口及
   完整旧镜像 ZIP 导入器，校验新回执、政策、全部来源与源码，而非旧 `_o` 请求集合。
4. `candidate._prepare/run_candidate`：拒绝混用新旧标签与政策；标签成熟日期、
   所有完整日门槛和每日 Top1/Top2 均保持。只有通过门槛才拟合。
5. `capital.replay_capital_from_repository` 与 `capital_report`：内部重建新标签，
   同步校验新采集回执、来源政策及源码。候选未拟合时不得生成伪净值。

以上入口测试需覆盖：有效 fallback、canonical 零量、未知容量、源损坏、
政策/SHA 漂移、新旧混用、真实请求参数、缺失行情与未完成日期。
已增加针对这些路径的离线回归。v2 明确未成交只接受四种状态：已观察到的
canonical 零量、日线零量停牌、开盘涨停成交不可确认、竞价容量不足；未知
`NO_FILL_*` 一律拒绝，不允许 API 错误或缺源悄悄成为零收益。

## 正式入口也须独立版本迁移（不能遗漏）

现正式 `executable_profit_shadow_settlement._verified_auction_sources_v2` 与
`_entry_price_v2` 仍按 `stk_auction_o.close`、`stk_auction.price` 顺序取价；
`decision_primary_profit_shadow_entry_price_policy_v2.json` 同样将旧 `_o` 排第一。
10:00 退出升级没有改变该入口。

现正式 `build_t_verification` 将竞价/日线价格冲突标为
`T_VERIFIED_PROXY_NO_FILL_AUCTION_DAILY_CONFLICT`，后续按未成交零槽位结算。
新政策必须区分“来源冲突待查”和“确定未成交”，前者不得记零或进入已结算统计；
旧 `_o` 金额也不能继续冒充单次竞价容量。正式源码/策略/账本迁移需独立审查，
同时解决冻结限价与用户最终竞价价规则的差异。不能只换研究模型就称为同口径上线。

旧已冻结验证记录不自动改价或覆写；需保留旧政策归属，以新版本和明确起点向前运行。

`formal_entry_v3.py`、`FORMAL_ENTRY_V3_POLICY.json` 与迁移文档现已提供
默认关闭的只读预备实现及研究经济口径一致性测试。它们位于 `work/`，没有
生产写入器、生产调度或成功激活路径；`PREPARED_NOT_ACTIVE` 不是上线。

## 底稿与增量补采

固定完整底稿：run `34671477608`、head `4675fab984050fa32be875fff07e0285e8903ebb`，
ZIP SHA256 `d004f6decba35d6148082333764ba0988bc3fa25062224492d485ac031fe2a29`。

- 新建研究镜像；安全枚举 ZIP、核验唯一成员及全部来源 SHA。
- 原样复用合格的候选、D-only 特征、日历及 926 对 daily/stk_limit 分区。
- 旧 `_o` 全部价格与金额、旧标签/模型输出及旧分钟仅留档，不进入新活动源搜索路径。
- 910 个 T 中，517 个在 20250101 前：明确登记覆盖前声明；其余 393 个 T
  需要 canonical 合格回执。不得假造网络请求。
- 原 1243 对成功分钟源均是 09:30 请求、241 行，并记录排除了 09:30 点；
  不可直接改写为新版 09:31 来源。563 条旧失败回执亦保留。
- 按新标签实际需要的股票/日期续采缺少的新版分钟；封板延持产生的新日期继续迭代。
  这不是全市场重采，也不能仅凭六例探针通过就视为全量源已齐。
- 保留 3 条退出日线缺口与独立停牌证据；缺日线不能直接推断停牌或填零。

## 发布触发与验收

旧 history/capital workflow 保持原样。新 `research_profit_1000_sources_v2.yml`
仅由版本化 `COLLECTION_V2.json` 的 main push 触发，以当前提交运行，下载
上述单个固定完整底稿并写入全新镜像。测试和生产冻结校验通过后，才把已有
Tushare 凭据提供给只读采集步；其他步骤无该凭据。请求上限仍为 9000 次、
2 次/秒、5400 秒、4 worker、单请求 20 秒。该预算不是全任务墙钟承诺。

同一任务先重建新标签，再通过原门槛决定是否拟合；资金报告再次独立重建，
显式核验本次 run ID / commit，而不信任先前保存的标签或预测。失败和负结果
均随整个镜像归档。分钟来源必须与合格请求回执双向一一对应。

真实结果以该 workflow 的确切 run/SHA/归档及质量门槛报告为准；任何尚未完成的
补采、仍缺失的停牌行情、未确认的分钟时间戳语义，均不可由工程测试代替。

先接线测试，再补源，再全量重建 6753 个候选标签，再按原门槛训练与比较，
随后做两个独立排名基准账户的资金回放及前向验证。研究两个 100 万账户不是
用户合计 200 万实盘；单笔 10 万、100 股取整、09:25 资金占用继续独立核验。

任何一步未通过都保留原因与负结果，不更改原晋级模型、正式模型、正式账本或 HTML。
