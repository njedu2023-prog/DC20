# 非交易日续算后的独立分钟来源补采登记 · 第三轮

仅登记研究来源采集，不执行结算、训练或生产替换。原始分钟主链与本非交易日补充分支分开验收；组合标签不能作为原始分钟主链的 prior。

## 本轮范围

- `NONTRADING_MINUTE_GAP_COLLECTION.json` SHA `ce015ac1b719c992e26e773a1dc2ae3c49950a52c1378d6f82352bd86712d471`，2667 bytes。
- 绑定已完整完成的组合标签 `2f3bc39862e8e26048fb90ff47fcfd0679d734960e3e7a2d4e8715c090620dde`。
- 三对：20240507／600234.SH、20240508／600083.SH、20260716／603580.SH。三对均为唯一预检，每对最多一次、零重试。
- 沿用冻结 expected_plan 与 canonical JSON；stk_mins 09:31–15:00 恰 240 行。BAR_END 仍为未获提供方确认的研究假设。

## 已完成的真实依据

本轮 prior 是原始三轮分钟主链加两轮独立非交易日补充的完整回放，不是运行中临时文件。6753 行／910 个 D 日完整保留，4867 条结算、2884 条负收益、902 个完整 D。原始主链 6732 条旧终态保持；固定六病例中已结算的三条也完整保留，另外三条延持后出现本次缺口。其他 6747 行与对应 raw 三轮结果一致。

三条 pending 的 net_return、conditional_net_return、slot_net_return 均为 null。买入价、scheduled T+1 和 45bp 未改变。增强来源根无目标 data/meta，不能覆盖原证据；日期必须在原始日历内，不读取 D>=20260914 的结果。

这三对与已完成四轮普通分钟 2547 对、两轮独立补充 9 对，共 2556 对无交集；也不与同期登记的普通第五轮 20251203／003018.SZ 重复。组合报告中的其他五个旧普通缺口已经由第四轮原始主链处理，本次不得重采。

## 冻结证据

实际根：`upgrade-candidate-evidence-20260913.VdRAd5/nontrading-replay-l0kp8enq`。

| 文件 | SHA-256 |
| --- | --- |
| combined_labels.json | `2f3bc39862e8e26048fb90ff47fcfd0679d734960e3e7a2d4e8715c090620dde` |
| acceptance.json | `5bd26cd0ff045fb597f1440a516ef23bdf39a3265bee5ccb34a5238c9fa4a0f9` |
| source_provenance.json | `ab459e988ca2138734775ef6e89b8325246dc6fbbd9ebd1e72acf4e5d147ac74` |
| combined_source_admission.json | `b7ab854fdd9540f43ae9e55b6f280cea9117e999ea8bb7e3d334127b0c231937` |
| 独立补充链摘要 | `9ac80284047234bf5d640ac80ea879002b34dc5c62fdca518d62fc75b0c48d74` |
| 第二轮独立补充 ZIP | `9e003eeb1b2bcfa83ee36d03dc97a795bfc32ecb03dc9bf33b3e7592920ee8a4` |

第二轮补充 run34713888288／head36d49ad32a6e9d8ee4d905088b868fedc5dc82a4，三对全部合格、零重试。本轮继续独立登记，每份旧 artifact 内的原计划与回执保持不可变，多轮摘要不冒充单一 GitHub 收据。

对应固定评估已完成，训练完整 718／724 日、验证完整 182／186 日，仍为 BLOCKED_DATA_QUALITY，未训练新模型；不能删去四个不完整验证日来放行。之后要纳入最新原始主链再评估，本节不将旧组合快照冒充最新合并结果。

原 18 个非交易日证明上下文不能扩大或伪造；新补充必须绑定真实 run／commit／ZIP、各轮 prior 后，再在固定六病例中续算，保留全部终态与亏损。原晋级模型、前台与正式账本不改。
