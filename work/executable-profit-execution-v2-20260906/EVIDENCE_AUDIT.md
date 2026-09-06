# 盈利执行标签 v2：历史证据可用性审计

审计边界：读取 DC20 自有固定归档，不训练、不修改生产模型、原名单、旧标签、Shadow 或正式 Action，不访问网络。研究入口是 `audit_sources.py`；它只生成脚本所在研究目录的 `outputs/source_coverage.json`，不随 `--root` 改变输出位置。先读取本目录 `PLAN.json`，核对其 ledger/calendar 两个 SHA pin 与固定 five-year source SHA；拒绝不安全输入路径、输入符号链接、输出符号链接和文件硬链接。只计 PLAN 固定 `as_of_date=20260904` 以前的 raw 日期，并记录 `plan_sha256/code_sha256/source_commit`。每份使用的 ledger、SSE 交易日历、raw dated 文件及九份恢复 K 线均输出 SHA256。

## 结论

**目前不能把原 6,753 行 / 910 个 D 日盈利 ledger 重新命名为“可执行净收益”后重训。必须补新的、不可变、逐日完整的历史行情与证券状态证据。** 当前材料足以实现和测试独立标签引擎，并做小窗口、明确非实际成交的日开盘代理研究；不具备真实竞价容量或可成交性放行条件。

当前 PLAN v2 是**没有额外 D-frozen cap 的保守日开盘代理研究**：仅使用官方涨跌停约束、日量、权益连续性和固定顺延退出规则。它不等于现有前向 Shadow 的额外冻结限价策略；本阶段没有实现额外 D 限价，也没有完成真实竞价容量合同。两者的收益与样本不得直接混用。

旧样本覆盖 D=2022-11-11 至 2026-08-14，共 1,959 支证券；旧 manifest 自己明确 `blocked_limit_down_exit_truth_available=false`、`actual_order_fill_observed=false`、`actual_execution_claimed=false`。其中 5,790 行是旧规则下已成熟的 T/T+1 日开盘代理收益，955 行旧判不可买，另 8 行待真值；这些数字不是实际成交数量。

## 核对的文件与字段

| 来源 | 实际证据 | 不能证明的内容 |
|---|---|---|
| `data/decision_executable_profit/historical_oof_top10_ledger.csv.gz` | OOF 晋级 TopN 身份、D/T/T+1、D 特征、旧 buyable/return 代理 | 无冻结限价、实际订单/排队/成交、顺延退出路径 |
| `data/decision_three_engines/five_year_supervised_ledger.csv.gz` | 匹配 6,753 行 T 的 `t_open/t_high/t_low/t_close/t_pct_change`；`tplus1_open` 有 6,747 行 | `t_amount/t_turnover_pct` 全空；未保存 T volume/pre_close，T1 除 open 外无完整 OHLC/volume/pre_close，亦无官方限价、停牌或公司行动 |
| `data/market/raw/2026/<day>/daily.csv` | `trade_date,ts_code,open,high,low,close,pre_close,vol,amount,pct_chg` | 日开盘价/日成交量不是本人竞价成交，也不证明开盘时的可承载资金量 |
| 同目录 `stk_limit.csv` | `up_limit,down_limit` | 缺行不能按固定 10% 臆造官方价，不能据此识别所有停复牌与权益事件 |
| 同目录 `stk_auction_o.csv` | 仅 20260813、20260814，各 80 行；`open/high/low/close/vol/amount/vwap`，有独立 meta/SHA | 仍无本人订单和排队证据；严谨结算层使用开盘竞价 `close` 等明确来源，不把日 `open` 自动升级为真实竞价 |
| 同目录 `stk_auction.csv` | 29 日的通用 `vol/price/amount` | 无这份文件自身的 opening-session/timestamp 合同；不能仅凭名称当作 `stk_auction_o` 的开盘真值替代 |
| `data/market/trade_cal_sse.csv` | 20200101–20261231 严格 SSE 日历；旧样本 D/T/T+1 邻接全部一致 | 是市场日历，不是个股停牌记录 |
| `data/decision_three_engines/recovery/20260821/daily_bars/*.csv.gz` | 9 支证券 20210709–20260821 连续非复权 K 线（其中1支有缺日）；仅覆盖旧 ledger 8 支证券 / 43 行 | 无官方 limits、停牌、权益与真实竞价，不足以填满全历史 |

当前 raw dated 分区只有 29 个交易日，20260728–20260904，完整日期列表与哈希以生成 JSON 为准。对 6,753 行旧 ledger：

- T 有官方 `daily + stk_limit`：132 行，1.95%；T1 有两表：142 行，2.10%。
- T 和 T1 同时有两表：132 行、15 个 D（20260727–20260814）。这些行中，T 开盘严格低于官方涨停价有 98 行；132 行日量均大于零；T1 开盘严格高于官方跌停价有 129 行。这里尚未施加一个新定义的 D 冻结限价，不能声称 98 行均可成交。
- T 有 `stk_auction_o` 且 T1 有完整 daily/limits：20 行，0.30%；T/T1 都有 `stk_auction_o` 只有 10 行，0.15%。
- 仓库未找到独立 `adj_factor`、dividend/现金权益、配送股或历史停牌事件表。旧腾讯构建器把 `pre_close` 定义成上一条未复权 `close`，不能靠它正确识别公司行动；manifest 标明 unadjusted，但 `_bars()` 还允许 `day -> qfqday -> hfqday`，新来源接入应明确拒绝未经确认的复权/未复权混用。

## 旧标签为何不是新目标

`scripts/build_three_engine_five_year_ledger.py` 的旧买入标签只排除“开盘涨停且全日最低价仍涨停”的一字板。**开盘涨停、盘中后来打开的股票被标记为 T 开盘已可买**：它使用了 T 日整天的 low 判定，却把 entry price 记在开盘；这是不能用于严格开盘成交目标的乐观代理。旧退出直接使用 `tplus1_open / t_open - 1 - 45bp`，没有跌停无法退出的顺延，更没有资本占用模型。

只作诊断、按旧 10% 假设从未复权前收盘推导（不是官方涨跌停真值）：892 行旧标可买但 T 开盘已在推导涨停附近；322 行旧成熟收益在推导 T1 跌停开盘处结束。这些敏感性计数必须与官方覆盖分开，不可拿来填充新真值。`audit_sources.py` 同时输出近期有官方价的直接差异计数及样例，避免混淆两者。

## 最小安全实现

1. 新建**独立、版本化研究标签**，原 6,753 行、生产排名及原 Shadow 不动。D 身份与特征保持原 OOF/冻结来源；新收益规则属于事后研究定义，不伪装为既有前向策略。
2. 当前 PLAN 固定的入场政策不含额外 D 限价：`0 < open < official_up_limit`、日量与证券状态有效，并通过权益连续性检查，才具备保守日开盘代理入场条件；不把全日后来开板反推为开盘已成交。若后续要与现有 Shadow 对齐，必须另行预定义仅用 D 可知信息的 cap，并增加 `open <= D_frozen_cap` 等条件；这是下阶段的独立政策门禁，不是当前已实现功能。
3. T+1 起按严格 SSE 次序寻找第一个 `open > official_down_limit`、量与状态有效的开盘。缺中间日分区、缺证券行、缺官方限价或权益不连续，立即保留 `UNKNOWN/PENDING_SOURCE`，不得跳过后挑有利价格；明确停牌与缺数据不能混用。
4. 逐日对官方 `pre_close` 与上一个观察日 `close`；不一致且无公司行动证据时留未知，不能用旧未复权 open 比值当成本后真实收益。补数据时保存权益调整依据、取得时间、来源和字节 SHA。
5. 未成交槽位与未知标签分离；未成交可按固定政策为槽位 0，但未知不可当 0、亏损或简单剔除后宣称样本完整。顺延退出需记录实际退出日、持有交易日、成本与资金占用；未建模重叠持仓时不报告可执行资本净值。
6. 全历史证据覆盖、固定规则、成本压力与严格滚动样本外检验完成前，**不启动全历史“执行感知”重训或替换生产盈利模型**。真实竞价/容量校准还要新的开盘竞价或订单层证据；当前日线代理最多支持清楚标注的研究版。

复现：在仓库根目录执行 `python work/executable-profit-execution-v2-20260906/audit_sources.py --root .`。脚本无网络和训练调用；唯一落盘文件为本研究目录 `outputs/source_coverage.json`。
