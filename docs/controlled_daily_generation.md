# DC20 当日受控生成与晚间联动

这是已有 P0/P1 的新增启动通道，不是新模型、历史回填或交易执行器。
保留 GitHub 自然 schedule 作为回退；保留原 Data → Top10 → Decision/Premium 链。
DC20 在共同的 Data、Top10 上游就绪后独立启动，不等待旧 Decision/Premium。
DC20 运行时仍不得读取或写入 `top10-decision`。

## 调用前

1. 北京时间工作日 19:10 的 Codex 任务先读取 DC20 当前 main 已提交的
   `data/market/trade_cal_sse.csv`。仅 SSE 开市日启动；节假日安静退出。
   唯一目标 D 是当天，T/T+1 是其后连续两个 SSE 开市日，不用工作日推算。
2. 等待 `a-share-top3-data` 的当日行情和 `a-top10` 的
   `outputs/decisio/pred_decisio_D.csv` 真正生成并通过日期/非空必需表检查。
   固定两个上游仓库的 40 位 commit，读取该 commit 下的 dated 文件。
   不使用旧日、latest 替代；不使用后来才出现的提交。
3. 检查 DC20 P0、P1、Shadow、Pages，以及共享
   `decision-auction-main-writer` 的 queued/in_progress 状态。
   已有同 D 完整发布就验收复用；有运行中任务就等待，不重复派发。

## 唯一日常入口

`njedu2023-prog/DC20`，`main`，工作流
`DC2.0 · Publish Primary D List (P0)`（ID `343703608`，
`.github/workflows/run_primary_d_daily.yml`）：

| 输入 | 值 |
| --- | --- |
| `dry_run` | `false` |
| `trade_date` | 当天 D，`YYYYMMDD` |
| `generation_mode` | `NATURAL` |
| `pred_commit` | 已验收的 a-top10 40 位 commit |
| `market_commit` | 已验收的 a-share-top3-data 40 位 commit |
| `confirm_daily_generation` | `true` |
| `confirm_recovery` | `false` |

使用授权 Connector/API；若没有 dispatch 能力，可使用已登录 GitHub Actions
的 Run workflow。权限阻塞必须如实报告，不能改用 SSH 或普通本地 git push。
不为了触发而制造无关 commit，也不重跑旧失败 run。

调用必须在 D 日 15:00 后、23:30 前；19:10 是编排启动时间，**不是名单完成时间**。
P0 校验 GitHub API 的 workflow、run id、main、head SHA、首次 attempt、
真实 created_at；计算与 CAS 再验当日窗口。两个上游提交不晚于 dispatch
created_at；源数据生成于 D 收盘后、不晚于 dispatch。排队不会放宽这些条件。
严格候选、模型冻结、完整快照、单提交 CAS 和无 Action 边界全部保留。

真实受控调用标题为 `DC20 controlled daily NATURAL | D=YYYYMMDD`。
该标题仅用于前置过滤，不能替代 API、关键 job、同 D receipt 的验收。
演练、历史恢复和普通手动启动不具备这个生产标记。

## 完整链验收

- P0：同 D 晋级榜和连板路径，真实硬范围最多 Top10，不补票，不改变冻结名次。
- P1：P0 成功后自动启动；19:10 的受控事件按当天 D 解析，不按旧 20:00 锚点减一天。
  必须通过同 D 自然 P0 receipt、API/job/部署校验；盈利排序及每日 Top1/Top2 留档齐全。
- Shadow：自然 P1 成功后由独立 workflow 自动冻结同 D 同 SHA 的 Top1/Top2。
  “每日 Top2 留档”与“已冻结前向 Shadow”分开验收，缺一项不能称闭环。
  未到期 T/T+1 保持待验证，缺失行情不记作零收益；既有 T/T+1 验证流程不变。
- Pages：核对最终 main、部署 revision、公开首页和 dated 下载产物一致。
  记录启动、名单生成、完整链公开的北京时间，不把启动时间当完成时间。

单个上游失败时阻断两个下游；旧 Decision/Premium 独立失败不阻断 DC20。
P0 已完成但 P1/Shadow 不完整时，先诊断并等待已排队的自动链；不得把
`RETROSPECTIVE_RECOVERY` 用作每日默认回退，不得重复冻结或覆盖既有同 D 数据。
过窗、未来提交、错日、部分冻结、CAS 冲突等均应如实 BLOCK，保留证据。

## 变更边界

此变更只扩展受控启动与下游识别，不重训模型，不改股票排名/盈利分值，
不回写历史账本，不生成订单、目标仓位或正式交易 Action。
本地测试和 GitHub CI 通过只代表接入代码验收；当晚 exact-D 完整链成功后
才能报告实际联动成功。
