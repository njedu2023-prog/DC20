# Verify 阻断诊断与修复范围

基线：`c26405b044cae2e771577f1dfbc9b8f1db073043`。

## 复现

从 GitHub 固定 SHA 归档解包到独立目录，核验全部原版 freeze pins 后，调用原版
`run_forced_replay(root)`，随后调用 `build_candidate_behavior_contract(root)`。
这是 macOS 本地逻辑复现，不冒充 GitHub Ubuntu 固定数值运行环境的逐字节验收。

原版执行结果：

- Auction engine 的预测日期为 `20260904`，`promoted=false`、`selected_count=0`。
- `publish_decision_action.py` 未获显式报告日期，使用旧报告 `20260826`，
  写出独立工作区 Action 的 D/T/T+1=`20260825/20260826/20260827`。
- 该 Action 的 `prediction_matches_report=false`、
  `status_code=PENDING_AUCTION_MODEL`、`formal_buy_count=0`。
- `_action_candidate_contract` 在 `replay_frozen_canonical_v2.py:1972` 要求
  `NO_TRADE_MODEL_NOT_PROMOTED`，抛出 `action plan status is not NO_TRADE`。
- 该旧 Auction 重训/报告回放在 Verify 真实观察统计和独立 Shadow 结算之前，
  失败阻断后续 compute、candidate、CAS 与 Pages。

因此，已冻结的当前 P0/P1 预测与历史 Auction 报告属于不同日期域。结算已有冻结
预测不需要重训 Auction，更不应要求其旧报告恰好与最新预测同日。

## 修复

- 新 gate 对 **exact-base** 中实际被结算的输入验收：完整活动 freeze pins、
  P0 receipt/runtime/三榜 JSON/CSV 哈希和成员/排名指纹、P1 单一与混合链、
  每个 D 的严格 SSE 相邻 D/T/T+1、每个已有冻结 Shadow、最新自然 Shadow 公共链。
- 主模式为 `PRIMARY_FROZEN_FORECASTS`，不重训模型、不读取或改写 Action。
  主榜残缺、孤儿 pointer、SHA/CSV/日期漂移、缺自然 Top1/Top2 冻结一律失败，
  不退回旧模式。
- 仅完全没有主榜链的 legacy-only 仓库保留原版完整回放；旧回放脚本未修改。
- primary 观察统计单独结算；保留原 Shadow 真值/退出合同，不使用日开盘代理
  冒充 strict auction。
- compute 验证新增 summary/rows 的完整重算；writer 仅允许这两个精确新路径，
  仍校验候选 patch 哈希、exact base、单提交 CAS。不可写 Action 或选择名单。
- 失败 gate JSON 不再丢弃，上传只读诊断 artifact。

## 初步验收

原始基线的全部 pins 与新输入 gate 通过；验收 P0 D 日期：0828、0831、0901、
0902、0903、0904；其中恢复模式是否进入前向统计由结算合同单独严格排除。
验收既存 Shadow 日期：0825、0828、0902、0903、0904；公开统计仍从 0828 起算。

## 第二断点：严格竞价真值从未取到

原 Verify 的 `sync_tushare_minute.py --post-close-truth` 不请求 opening auction：
代码仅在 `is_open and not post_close_truth_window` 时调用 `stk_auction_o`。
原调度也只传当前 as-of 日期，未尝试已冻结且到期的旧 T 日。

在另一个原始基线副本，保持结算合同完全不变，执行
`settle_signal_date(root, D, as_of_date='20260904')` 得到：

| D | T | T+1 | 原始基线真实状态 |
| --- | --- | --- | --- |
| 20260828 | 20260831 | 20260901 | PENDING_T_SOURCE_FILES，缺 T 的 stk_auction_o.csv |
| 20260902 | 20260903 | 20260904 | PENDING_T_SOURCE_FILES，缺 T 的 stk_auction_o.csv |
| 20260903 | 20260904 | 20260907 | PENDING_T_SOURCE_FILES，缺 T 的 stk_auction_o.csv |
| 20260904 | 20260907 | 20260908 | PENDING_T_NOT_REACHED，尚未到 T |

上述已到 T 的 daily.csv 和 stk_limit.csv 存在。原版独立 Shadow 累计仍是
4 个前向 D、8 个槽位、0 个 T 验证、0 个 T+1 结算。原版 public state 重投影为
`EXACT_PAIR_BYTE_IDENTICAL_NO_OP`，未凭日线编造竞价成交或收益。

新增 `sync_frozen_shadow_truth.py` 在自然 Verify 的严格输入 gate 之后同步缺失
真值：只读已有前向冻结名单，按相邻 SSE T 与到期退出 session 去重，最多 24 个
真实 endpoint 请求，所有日期不晚于 as-of。既存分区不覆盖；缺失分区先在临时
目录生成并验证 exact-date/代码/正有限数值，再以独占方式创建。新文件含 endpoint、
date 与 SHA 来源 metadata；候选逐文件重新校验 report SHA 并精确暂存。
空响应、字段不全、缺凭据、响应日期错配或请求预算不足明确待验证，不补造选择，
不降级用日线充当竞价，不改入场价上限、退出或成本合同。

本地测试 `test_verify_forecast_inputs.py`、`test_sync_frozen_shadow_truth.py`、
`test_writer_workflow_hardening.py`、`test_executable_profit_workflow_wiring.py`
共 119 项通过（含新增 parent-symlink containment 与所有 shell literal 的 `bash -n`）。

## 第三断点：此前未执行到的 heredoc 语法错误

对原始 c264 和修复版所有 YAML run literal 逐一执行仅解析的 `bash -n`，发现
原 Shadow settle step 的 primary 分支 Python heredoc 正文和终止 `PY` 均多缩进
4 个空格。原版报 `syntax error: unexpected end of file`，前面 Auction 回放失败
掩盖了该分支从未成功执行的事实。已仅修正 heredoc 缩进，不改变结算逻辑；新增
覆盖整个 Verify workflow 所有 run literal 的解析回归，不执行任何网络或命令副作用。

生产闭环仍需下一次自然 Verify 的实际 run、candidate、CAS、Pages revision 验收；
未手动派发或重跑失败 run。
