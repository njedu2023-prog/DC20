# 非交易日续算后的独立分钟来源补采登记

本文件仅登记研究来源采集范围，不执行网络请求、标签结算、训练或生产变更。原始 minute-only 主链保持独立；本补充报告不是主链下一轮 prior。

## 登记结果

- 计划：`NONTRADING_MINUTE_GAP_COLLECTION.json`。
- canonical SHA-256：`6023bef2a3e88cc434eb2d7b479a18d4d5b92a9cace8a6c85900dcd853b2d400`，2814 bytes。
- 生成规则：冻结 `minute_gap_collect.expected_plan(pairs, label_report_sha256)` 与 `json_bytes`，没有新增或改变合同字段。
- 精确范围 6 对，每对最多一次请求、零重试；原合同的全局 5000 上限不允许扩展这 6 对范围。
- 预检依次为 20240430／600234.SH、20251120／603122.SH、20260730／002036.SZ。三对全部通过后才允许其余三对。
- `stk_mins`，09:31–15:00、恰 240 行；原时间语义仍为研究 BAR_END 假设，未获提供方确认。
- collector 必须以显式 `--plan NONTRADING_MINUTE_GAP_COLLECTION.json` 使用本计划，不替换正常主链的 `MINUTE_GAP_COLLECTION.json`。

## 从真实冻结报告独立提取的六个缺口

| D 信号日 | T 买入日 | 代码 | 原缺日线日期 | 续算后真实缺分钟日期 |
| --- | --- | --- | --- | --- |
| 20240425 | 20240426 | 600234.SH | 20240429 | 20240430 |
| 20240425 | 20240426 | 600083.SH | 20240430 | 20240506 |
| 20250606 | 20250609 | 603226.SH | 20250610 | 20250613 |
| 20251113 | 20251114 | 603122.SH | 20251117 | 20251120 |
| 20260703 | 20260706 | 603580.SH | 20260707 | 20260714 |
| 20260721 | 20260722 | 002036.SZ | 20260723 | 20260730 |

六行均由原 `PENDING_EXIT_MISSING_DAILY`、`proxy_fill=1` 转为 `PENDING_EXIT_MISSING_MINUTES`，缺失种类均为 `research_exit_1000_1m_0931`。净收益、条件净收益和席位净收益仍为 null，未把停牌或缺行情填为 0。

## 完整性与去重检查

独立读取下列已绑定 SHA 的实际 JSON，核对后再次读取哈希，未使用聊天中列举的股票作为唯一来源。

- 原候选标签、首轮分钟标签、非交易日续算标签均为 6753 行／910 个 D 日；全部身份、T 日及行顺序完全相同。
- 六个处理身份准确等于原标签中的全部六个缺日线身份，也等于续算报告的 `processed_identities`。
- 其他 6747 行除允许重算的 `cohort_complete` 外完全相同，未筛除负收益或未完成记录。
- 非交易日证明报告含 18 个已签发证明。补采对象是这些日期之后实际暴露的分钟缺口，不是向停牌日补造分钟行情。
- 旧首轮计划 2384 对与本六对交集为 0；当前正常主链计划 134 对与本六对交集也为 0。
- 首轮增强根中六对的 data/meta 路径全部不存在，没有覆盖旧源或孤文件。
- 当前续算状态：已结算 4726 行；确认未买入 1868 行；缺分钟 149 行；入场价格无效待定 10 行。累计确认终态 6594 行，没有本轮新增结算。
- 本阶段登记不是来源验收：后续必须使用真实外部 run/commit、artifact ZIP SHA、独立 verifier，以及精确计划和来源绑定重新验收。

## 原始绑定

实际续算目录：

`/Users/moclh/Documents/ChatGPT/DC20/upgrade-candidate-evidence-20260913.VdRAd5/nontrading-replay-islvlxkg`

| 文件或计划 | SHA-256 |
| --- | --- |
| 原候选标签 | `2cca1e928b92a6cd46dabf7090714cfbc5e88682f2d93ecbc481c01ecd910609` |
| 首轮原始分钟标签 | `9b9cc3e0015bc5e2eb0c865ca05af93e1900dece7b123d0ff4f051ee372a7c1f` |
| 本次非交易日续算标签／本计划 label_report_sha256 | `1834f2043c2b2a12a7fd39dbbcbd3928340e930fdc010f8a8a8cb9d677c0179a` |
| 本次证明报告 | `53c6da3db897aadce70c73acfd58bbb417b52a6cc8a76fbbeb7251d505f07417` |
| 本次来源报告 | `6c56bd1f35dd5775252ac2bae0b4bfd5cdb0dc4f69199ff927c048b36ddd09c7` |
| 本次验收报告 | `2c184715143ff438630779f622ba5f2560acb89203757f0c762de64ff2ab8454` |
| 旧 2384 对归档计划 | `ecbf13df6a94a738fdcc38b79b537efcf37ce280dc10cf42ae7137bd47b55f09` |
| 当前正常 134 对计划 | `31f420bb86181dc96e20a267ffb6275f34b2d88b7854764f510be7cc85f3e9d8` |

## 后续消费边界

来源产物保持 source-only，不能因下载成功即取得标签或结算资格。不得把本续算标签喂给旧 raw minute-only chain；它的 `eligible_as_minute_chain_prior` 明确为 false。

新组合 runner 必须分别验收主链与本独立补充来源，逐文件追加到全新研究根且保持原始字节和 HTTP 元数据；复核全部证明日期没有新增分钟反证；再从已验收 raw 全量标签沿原 scheduled exit 规则重放六条，保留完整 6753 行／910 日、原买价、45bp 费用和已有终态。额外分钟与非交易证明单独记录，不伪造原收据或更改原 source origin。本登记不自动授权下一轮缺口。
