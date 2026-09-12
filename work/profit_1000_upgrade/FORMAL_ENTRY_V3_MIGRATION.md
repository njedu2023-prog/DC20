# 正式影子入口 v3：未激活的安全预备

状态：`PREPARED_NOT_ACTIVE`。没有启用日期，没有生产派发，没有正式账本写入。
这四个 `work/profit_1000_upgrade` 文件不属于现行 source-freeze 的生产源码，
不能因为测试通过而声称正式入口已经替换。

## 为什么不能修改旧入口

现行 `primary_profit_forward_shadow_bridge._shadow_rows_from_published_projection`
将 D-only 上限固定进 v2 选择；`executable_profit_shadow_settlement.build_t_verification`
依赖该上限，并按旧选择 schema 与 v2 文件是否存在选择价格规则。
旧规则优先 `_o.close`，价格冲突可能形成未成交零槽位。
这些定义及已冻结、已验证的记录必须保留，包括 D=20260910 / 20260911。
新旧政策不能靠“当前有哪些政策文件”决定，必须由每份冻结选择自己的政策 ID / SHA 决定。

## 本轮已经可测试的内容

- `formal_entry_v3.preview_top2` 只读内存预览，两席都保留；不足两只时保留明确空候选席位，
  不伪造股票、不以评分为负过滤 Top1/Top2。
- 只用独立 canonical `stk_auction.price` 及绑定请求回执。`stk_auction_o` 全部价、金额和
  元数据都不在查询路径中，也不借其金额估算容量。
- 有效空表、完整市场响应中无该代码、明确权限缺源回执可用同 T 日线 open 代理；
  没请求、请求日期/代码不符、损坏、哈希变化和竞价/开盘冲突均为 PENDING，不记零。
- canonical 零成交量、已验证日线零量、开盘涨停排队未证实、容量不足保留明确保守代理未成交原因。
  这不是实际挂单成交/未成交的证据。只有这些明确的代理未成交可预览槽位零，
  所有预览仍 `production_ledger_eligible=false`、`terminal_settlement=false`。
- 不使用 D 价上限；每席 10 万元，canonical 金额 1% 容量阈值，缺金额时为未验证容量的价格代理。
  输出绑定原有 45bp 和 `dc20_exit_1000_limit_hold_20260912_v1`，不重新实现退出引擎。
  买入预览不会计算收益，更不会拿日开盘替代缺失的 10:00 分钟退出价格。
- `activation_check` 始终拒绝激活，即使给出未来日期也只列出剩余审查阻断；
  拒绝旧 D、已有 D、审批前冻结和审批当日倒填规则。修改政策文件为 ACTIVE 会失败。
- 与真实研究 `labels.build_labels` 的 v2 路径做经济结果交叉测试，政策 ID 保持不同，
  不把研究记录重新标记为正式验证。

## 正式切换前必须另外完成的原子集成

1. 研究 v2 全量采集、6753 候选回放、完整日门槛及固定比较验收；不能仅凭探针或单测激活。
2. 为 production 编写并审查正式 canonical 源加载/回执契约，绑定来源版本、路径及 SHA。
   本预备模块复用研究 codec 仅用于比较；其 `research_only` / `production_integrated=false`
   不能删除、改名或冒充生产验收。验证上游是否提供 exact-T canonical 真实请求回执；
   生产 v3 未来日期没有“2025 前覆盖缺失”的免请求捷径。
3. 明确分钟时间语义与生产验证适配器。当前研究
   `RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED` 不是供应商确认，不可改成 true。
   保持下一分钟开盘代理、10:00 时点、封板延持、炸板后执行以及缺行情 PENDING。
4. 添加独立 v3 冻结选择 schema。未来自然生成的 P0/P1 原始包先按现有严格验证器检查，
   再原样绑定 Top1/Top2 身份、晋级名次、原模型及源 SHA，并在 D 冻结时写入新 entry policy SHA。
   本预览接受的 `ranked_rows` 本身不构成自然冻结证明，不能直接 materialize。
5. 在 bridge 的选择生成/验证/物化和 settlement 的选择加载/T 验证/T+1 结算分别增加显式版本派发。
   v1/v2 保留原代码和原政策；v3 使用新路径与新不可变验证 schema。
   不能回写旧 D、不能用 v3 重新计算旧已冻结/已验证买价，亦不能串用旧 entry 与新成本。
6. v3 T 验证允许逐席 PENDING，不应因一席错误丢失另一席及整日记录；
   累计统计只能消费相同政策版本、真正终态的记录；PENDING 不进入零收益/已结算日。
   Top1/Top2 每日必须记录，包括不利结果；空候选必须显式标记，不能制造可交易票。
7. 选择未来交易日 cutover：审批完成的下一或更后交易日才可成为起点，
   cutoff 前的自然冻结继续旧版本。保护 D0910/0911，以及所有已存在的更晚冻结记录。
   不能仅凭当前机器日期自动给历史或当日名单换政策。
8. 列出准确生产源修改集合并做 successor source review：保留所有旧审查文件/231 pins/42 模型资产；
   新模块/政策纳入明确的新资产与源清单。不得借本轮 `work/` 准备绕过这一步。
   需要覆盖 clean checkout、原 canonical 冻结重放、旧账本不变、不同政策拒绝混用和生产完整回归。

可能需要独立协调的现有文件（本轮均未修改）：

- `src/top10decision/decision/primary_profit_forward_shadow_bridge.py`
- `src/top10decision/decision/executable_profit_shadow_settlement.py`
- 调用上述 bridge / settlement 的正式日常入口及其策略绑定
- 新版独立 schema / loader / 聚合版本检查对应测试
- `models/decision_model_freeze.json`、`forward/model_inventory.json`、新 successor review，
  以及 `tests/test_decision_source_surface_rotation.py` 的可逆审查层

不需要改变原晋级权重，也不需要增加 HTML 字段。前台依然展示同一组紧凑结果，
政策归属、来源、未成交及待验证依据保留在后台审计链。

## 字段依据和时间边界

已核对 [Tushare canonical stk_auction 官方文档](https://tushare.pro/document/2?doc_id=369)：
price 为元，vol 为股，amount 为元；当日结果在 9:26–9:29 可获取，历史始于 2025 年 1 月。
因此最终竞价价只能用于事后验证，不能当作 9:25 前已知的 D 预测特征。
本准备不发网络请求、不持有凭据、不触发任务、不生成模型或正式净值。
