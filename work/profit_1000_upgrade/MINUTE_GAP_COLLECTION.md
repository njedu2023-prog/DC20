# 原始分钟主链补采登记 · 第五轮

仅登记来源采集，不能视为结算、模型或生产验收。非交易日补充分支保持独立，不把组合标签传给原始主链。

## 已完成的真实回放

四轮主链全部验收后重放完整 6753 条／910 个 D 日：4868 条结算、2883 条负收益、903 个完整 D；旧 6732 条终态保持不变（只允许重算 cohort）。新增 4 条结算，仍有 1 条缺分钟、6 条需独立非交易日证据续算、10 条无效入场价格。未知收益仍为 null。

实际根：`upgrade-candidate-evidence-20260913.VdRAd5/minute-chain-replay-6kkmkdon`。

| 文件 | SHA-256 |
| --- | --- |
| minute_chain_labels.json | `a04efbf38b8f3120c7e75be8f69d6ace2526e209851c6a1713906d3f1f883e33` |
| acceptance.json | `46d8dd275dd97666a244eac64638c4e0b1318f8b8bce4328e92049dae7a2b994` |
| source_provenance.json | `103e159959a540f1014e9ca55fb589e8ae5cf86074722bcfe27baf35774aa58c` |
| minute_source_chain.json | `78023988625c2dfb97e245465a085301b586f152874d6d663def211a01ff2237` |
| 第四轮来源 ZIP | `fc2a13cf85ea84c87219f60e799f215f49f058432b85d897109d4f7cef343145` |

第四轮真实 run 34714233675／head ecc3bd080c84c4c6aa2db69c36e9274b3381ced7，5 对全部成功，零重试；本轮只补完整回放继续暴露的一对，不重复之前请求。

## 本轮计划

- `MINUTE_GAP_COLLECTION.json` SHA `5597bb15a71cb9374fc4e5115171f88ba7a64cbd789b39f52bfe1cdf8836ce89`，2471 bytes。
- label_report_sha256 绑定本次 raw 四轮标签，不是非交易日组合报告。
- 唯一 pair：20251203／003018.SZ；对应信号 D=20251125，T=20251126，原定 T+1=20251127，前四个延持日的分钟已经保存。
- 首／中／末去重后只有这一对，预检即全部采集；最多一次、零重试。
- 沿用冻结 expected_plan 和 canonical JSON；stk_mins 09:31–15:00 恰 240 行。45bp／原 10:00 封板延持标签规则不变。
- 提供方时间戳语义仍未确认，研究数据不证明实际成交。

## 复核与后续

完整 6753 条身份、顺序、910 个 D 范围与旧 6732 条终态保持；不读取 D>=20260914 结果。该 pending 的 net_return、conditional_net_return、slot_net_return 都为 null。原日历包含缺失日，当前来源根无对应 data/meta，不覆盖旧证据。与前四轮主链 2547 对及两轮独立补充 9 对合计 2556 对无交集。

发布后仍需绑定真实 run／commit／artifact ZIP SHA 和每轮 prior，全量重建后才能判断是否结算或继续延持。不允许删掉不完整验证日，也不允许缺失填零。原晋级模型、冻结名次、前台、正式账本保持不变。
