# 非交易日续算后的独立分钟来源补采登记 · 第四轮

仅登记研究来源采集，不执行结算、训练或生产替换。原始分钟主链与本非交易日补充分支分开验收；组合标签不能作为原始分钟主链的 prior。

## 本轮范围

- `NONTRADING_MINUTE_GAP_COLLECTION.json` SHA `d5dafa58d91d31426600a0b34d4764c8ea4b3ec95cf838523b9d687a473a8a26`，2667 bytes。
- 绑定已完整完成的组合标签 `c040124db9fe910e154d8c1fddd8975b783bb0f11a42f703860cff73824a172e`。
- 三对：20240508／600234.SH、20240509／600083.SH、20260717／603580.SH。三对均为唯一预检，每对最多一次、零重试。
- 沿用冻结 expected_plan 与 canonical JSON；stk_mins 09:31–15:00 恰 240 行。BAR_END 仍为未获提供方确认的研究假设。

## 已完成的真实依据

本轮 prior 是原始四轮分钟主链加三轮独立非交易日补充的完整回放，不是运行中临时文件。6753 行／910 个 D 日完整保留，4871 条结算、2884 条负收益、906 个完整 D。原始四轮主链 6736 条旧终态保持；固定六病例中已结算的三条完整保留，另外三条继续待验证。其他 6747 行除 cohort 汇总外与对应 raw 四轮结果逐字段一致。

三条 pending 的 net_return、conditional_net_return、slot_net_return 均为 null。买入价、scheduled T+1 和 45bp 未改变。本轮日期均为各自上一已采日后的下一个开市日；同日 daily／stk_limit 记录同码唯一。增强来源根与 raw 五轮根均无目标分钟 data／meta，原 inventory 亦无登记，不得覆盖原证据，不读取 D>=20260914 的结果。

这三对与已完成五轮普通分钟 2548 对、三轮独立补充 12 对，共 2560 对无交集。组合四轮快照仍显示的普通缺口 20251203／003018.SZ 已由原始第五轮完整回放结算，本次不得重采。该原始第五轮结果与本轮 prior 是不同来源快照，不拼接或改写旧收据。

## 冻结证据

实际根：`upgrade-candidate-evidence-20260913.VdRAd5/nontrading-replay-1tg91kw0`。

| 文件 | SHA-256 |
| --- | --- |
| combined_labels.json | `c040124db9fe910e154d8c1fddd8975b783bb0f11a42f703860cff73824a172e` |
| acceptance.json | `0a28dede7c25a0a49f36791acb77d19984193aab895a36ae4d7b08f2b6284334` |
| source_provenance.json | `542907e3e152c0e21d5d6be868d71e1bb12f3b2d5a7499e3ddc6598f0c062c48` |
| combined_source_admission.json | `615c048605b39b959b70396e90e9572ce60abc0e1003b801dcb5e776f4979a58` |
| 原始四轮分钟链摘要 | `78023988625c2dfb97e245465a085301b586f152874d6d663def211a01ff2237` |
| 独立三轮补充链摘要 | `ccd17dfabeffdddf9bfa45cf94dadc685744a8b03f9d02b8bbdcd794acb4778e` |
| 第三轮独立补充 ZIP | `5f20286cb4ffe94fc9bad91813a46fd874588f3338b820e68741c76e692dd5ca` |

第三轮补充 run34715996618／headc1b9523fefefc081431e55839c10168560170a64，三对全部合格、零重试。本轮继续独立登记，每份旧 artifact 内的原计划与回执保持不可变，多轮摘要不冒充单一 GitHub 收据。

本轮仅通过来源、完整回放及独立守恒审查，未训练新模型，也未证明盈利提升。之后须纳入最新原始第五轮主链及全部独立补充重新执行固定评估；验证集必须保持原 186 个 D 日完整，不能删除不完整日放行。四个资金账户仍为独立研究对照，不能合并冒充实盘净值。

原 18 个非交易日证明上下文不能扩大或伪造；新补充必须绑定真实 run／commit／ZIP、各轮 prior 后，再在固定六病例中续算，保留全部终态与亏损。原晋级模型、前台与正式账本不改。
