# 非交易日续算后的独立分钟来源补采登记 · 第五轮

仅登记研究来源采集，不执行结算、训练或生产替换。原始分钟主链与本非交易日补充分支分开验收；组合标签不能作为原始分钟主链的 prior。

## 本轮范围

- `NONTRADING_MINUTE_GAP_COLLECTION.json` SHA `ca5b19d0b31a5e29b6adde6bfd7da5a4103da1baf0b714161f41776a4975ffa8`，2667 bytes。
- 绑定已完整完成的组合标签 `aa012648f09e018a096d3b8d6ff505dfda6d35c1b212b5c198f05c37a5b9a2b4`。
- 三对：20240509／600234.SH、20240510／600083.SH、20260720／603580.SH。三对均为唯一预检，每对最多一次、零重试。
- 沿用冻结 expected_plan 与 canonical JSON；stk_mins 09:31–15:00 恰 240 行。BAR_END 仍为未获提供方确认的研究假设。

## 已完成的真实依据

本轮 prior 是原始五轮分钟主链加四轮独立非交易日补充的完整回放，不是运行中临时文件。6753 行／910 个 D 日完整保留，4872 条结算、2884 条负收益、907 个完整 D。原始五轮主链 6737 条旧终态保持；固定六病例中已结算的三条完整保留，另外三条继续待验证。其他 6747 行除 cohort 汇总外与对应 raw 五轮结果逐字段一致。

三条 pending 的 net_return、conditional_net_return、slot_net_return 均为 null。买入价、scheduled T+1 和 45bp 未改变。本轮日期均为各自上一已采日后的下一个开市日；同日 daily／stk_limit 记录同码唯一。增强来源根与 raw 五轮根均无目标分钟 data／meta，原 inventory 亦无登记，不得覆盖原证据，不读取 D>=20260914 的结果。

这三对与已完成五轮普通分钟 2548 对、四轮独立补充 15 对，共 2563 对无交集。普通分钟缺口已在原始第五轮清除，本轮只补上述三个实际续持缺口。缺失买入价格的 10 条记录仍保持待验证，不虚构竞价或开盘价。不同来源轮次分别核验，不拼接或改写旧收据。

## 冻结证据

实际根：`upgrade-candidate-evidence-20260913.VdRAd5/nontrading-replay-zb5kwfch`。

| 文件 | SHA-256 |
| --- | --- |
| combined_labels.json | `aa012648f09e018a096d3b8d6ff505dfda6d35c1b212b5c198f05c37a5b9a2b4` |
| acceptance.json | `ed805bcacdd4429866ef202d18c8b511a9e1380484fdd3e2c3d5dffb529e1643` |
| source_provenance.json | `15a368e54af0ce8bb5f452f44325f13d8c55f87e2cd7c811133d381a3cca6ec1` |
| combined_source_admission.json | `9e578835995b6baf2954af2a3d598036ad4c935552977ee44adc6cf0b4add958` |
| 原始五轮分钟链摘要 | `f4a346a2edb3d62397a396722a350b2b52a983eb61c5027e12ed4252d868b65b` |
| 独立四轮补充链摘要 | `d566e575e6a458542896085310897dc7cf2622192eabe212021cb19bbdba98da` |
| 第四轮独立补充 ZIP | `3e6a23c4984f7ab0b17eba10c0bad2fe13d5b749dfdc7445e400d43253ceda30` |

第四轮补充 run34740089048／head338308ec2167520ad8fbfe96ec2036da586b9e29，三对全部合格、零重试。本轮继续独立登记，每份旧 artifact 内的原计划与回执保持不可变，多轮摘要不冒充单一 GitHub 收据。

本轮仅为来源与完整回放结果，未训练新模型，也未证明盈利提升。之后须纳入最新原始第五轮主链及全部独立补充重新执行固定评估；验证集必须保持原 186 个 D 日完整，不能删除不完整日放行。四个资金账户仍为独立研究对照，不能合并冒充实盘净值。

原 18 个非交易日证明上下文不能扩大或伪造；新补充必须绑定真实 run／commit／ZIP、各轮 prior 后，再在固定六病例中续算，保留全部终态与亏损。原晋级模型、前台与正式账本不改。
