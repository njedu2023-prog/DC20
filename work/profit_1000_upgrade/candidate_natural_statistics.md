# 自然研究四席统计（独立于旧影子账本）

`summarize_natural_statistics(day_inputs, *, as_of_date, calendar_raw,
expected_calendar_sha256, clock=None)` 只读原字节，不训练、不评分、不联网、不写文件。
每个 D 必须显式提供原 snapshot bytes/SHA、独立私发 publication proof、ledger bytes/SHA、
外部 ledger_as_of_date；可选 source_collection_receipt_sha256 仅用于追溯，不能授予来源资格。
缺账本时 ledger raw/SHA/asof/collection receipt 均为 None，四席仍保留。

先检查全部外部 D/asof，再处理该范围内原字节；内部全部 version/单股身份、成熟日期先于收益
校验。验证原冻结名单、固定 writer/schema、版本封存链、原机械标签合同、实际使用来源 SHA
与账本输入绑定、既有终态不可变。不得将未来版本裁剪后当成原账本；应传当时原始版本文件。
asof 必须是已收盘的固定交易日历交易日；注入时钟明确为 SYNTHETIC_CLOCK_ONLY。

候选 Top1/Top2、晋级 Top1/Top2 是四条独立策略序列，不是同时四笔实际下单。重复股票在不同
策略席位各自保留。不混旧回测、旧影子账本，不改前台或成本（45bp 已由原标签扣除一次）。

| 指标 | 固定分母/处理 |
|---|---|
| 胜率 | 严格正收益的已成交结算 / 全部已成交结算；零收益不算赢 |
| 平均每席净收益 | 已成交结算及原合同确认 NO_FILL（仅这些为零）；不含 pending、缺账本、无候选 |
| 平均成交净收益 | 仅已成交结算，真实亏损与零收益均保留 |
| 日序列 | 所有显式 D 都保留状态、股票、成熟日、值或 None |
| 完整日序列 | 每组仅终态 D；额外提供四席共同完整日集合用于同样本比较 |
| 模拟累计 | 按 D 顺序对完整席位收益计算有符号的 ∏(1+r)−1；没有完整日则 None |

模拟累计忽略持仓重叠与资金占用，不是 100 万资金净值，不证明实际成交、容量、最大回撤或
收益提升。极端含费用损失不会被截成零；不得把有符号序列乘积解释为可交易资金财富。
整个历史没有遗漏任何已发布 D 的资格仍由外部登记清单负责，本模块明确只统计所有显式提供
的绑定 D，`whole_natural_history_completeness_verified=false`，不会自证没有选择性遗漏。

私发 publication proof 只验证原选择的独立发布证据；账本 SHA、seal 和原机械合同验证不等同
行情提供商的实际来源资格。所有 source/execution/production/NAV/DD 权限均为 false。
普通 dict、caller 布尔、local capsule 或单独 SHA 均不能替代 proof。真实未来样本验收尚未运行。
issuer 固定为已审阅的 ad20af83… 版本；本地普通回执不能替代其真实私发 proof。
该 issuer 重新发行仍需要原 observer 的未过期 ACK artifact（当前90日），不能宣称永久离线资格。
