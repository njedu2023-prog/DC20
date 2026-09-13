# 正式影子入口 v4：仅原生 v3 价格/容量拆分的安全预备

状态仍为 `PREPARED_NOT_ACTIVE`。仅新增这份说明、`formal_entry_v4.py`、
`FORMAL_ENTRY_V4_POLICY.json` 与 `test_formal_entry_v4.py`。旧 v3 四文件、
研究 codec/policy/labels、旧 D、正式桥接/结算、工作流和模型均不修改。

## 此版本解决什么

旧 prep v3 绑定 `auction_truth/policy_v2`，会把金额算术异常视为全表来源错误。
新 prep v4 明确绑定现有 `auction_truth_v3/policy_v3`，不再假定两者经济合同相同。
仍只有 `policy_binding`、始终拒绝的 `activation_check`、只读 `preview_top2`；
没有生产 dispatcher、CLI、writer、自然冻结验证或来源签发功能。

- 通过原生 `auction_truth_v3.load` 读取研究 schema 的不可变精确 T 回执，再调用其
  `entry_price`；不读 `_o`，不读取或改写旧 v2 回执，不把 research 标志转成 production。
- 价格与日开盘按原生分位比对，但原报告价格（含数值字符串或小数位）不改写。
  金额不一致、缺失或不合格只使容量未知；`capacity_amount/amount=null`，
  不能用 `reported_auction_amount` 判为容量不足，也不能宣称实际成交或实际容量。
- 只有合格金额才比较 1% 与每席 100,000 元，复用原生研究的 `+1e-9` 浮点比较容差。
  本预备两席共名义 200,000 元，不是 capital bridge 的 1,000,000 元账户回测。
- 原生 `PENDING_CANONICAL_*` 是候选局部状态而非异常：必须先保留为 PENDING，
  不得回退日开盘或记零。零量但正价格为冲突；合法零成交使用原生零量/零金额/空或零价格形状。
  日线零量还须 OHLC 分位一致，与当前研究 v3 相同；另一有效席位不丢失。
- 日线数值使用原生研究的有限浮点解析；仅 `high <= up + 1e-8` 与
  `low >= down - 1e-8` 使用既有范围容差，OHLC 顺序与 `down < up` 仍严格。
  独审发现旧 prep 的 Decimal 精确边界不等价，已只在新 v4 修正，并测试容差内、
  精确边界、第一可表示超界浮点，以及零量/OHLC 顺序冲突；不修改原竞价价格。
- 两席始终保留；负评分不跳过；无 D 价上限；45bp、T+1 10:00、封板延持及缺分钟待验证
  保持既有定义。此模块只做买入预览，不计算退出、净收益或正式终态。

新增输出显式记录 `price_qualified`、`capacity_amount/reason/evidence`、原报告金额、
价格比较结果及原生资格状态。始终 `source_authority_issued=false`、
`production_integrated=false`、`production_activation_allowed=false`、
`natural_freeze_verified=false`；每席 `terminal_settlement=false`、
`production_ledger_eligible=false`。测试生成的回执只能是合成资料，不能成为真实请求证据。

## 验证边界与仍未完成事项

专测继承旧 v3 的身份、日期、双席、缺源、错误来源、SHA/符号链接、末端变化、只读及拒绝激活保护。
通过原生 codec 编码/加载合成 HTTP 形状并与未改动的 `labels_v3.build_labels` 比较 36 种入口情形，
不是 mock 金额关系或实际历史全量回放。使用现有固定交易日历，但不读取任何未来真实结果。

生产 canonical 回执/loader、明确的冻结 policy ID/SHA 与版本派发、批准之后新自然 D 的切换、
successor source-freeze 审核、分钟时间语义确认及完整研究/前向验收仍是独立阻断。
现有 `_sync_meta` 与五列 auction CSV 仅证明同日上游提交/文件，不能补造 exact-T API 请求、
原 HTTP 摘要、抓取时间或完整分页证据。详见 workspace 的
`formal_v3_canonical_source_gap_review_20260913.md`。旧 v3 迁移说明保留为旧合同历史，不覆盖。

本预备不是“正式 v4 已发布”、不是盈利改善结论，也不替代正在进行的完整 suite/Core 验证。
