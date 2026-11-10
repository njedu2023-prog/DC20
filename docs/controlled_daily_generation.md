# DC20 当日受控生成与晚间联动

这是 P0 晋级榜与当前固定 Ridge 盈利榜的日常启动与交付规范。
旧 P1/Shadow 已退出自动生产；历史产物与审计代码保留。此调整不重训或替换模型。
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
3. 检查 DC20 P0、当前激活固定 Ridge 的自然冻结/观察/结算/发布分支、
   Pages、晋级结果验证与 delivery watchdog 的启用状态。
   不要求旧 P1/Shadow active、当日运行或产生新冻结；不得为补齐旧链而重新派发。
   已有同 D 完整发布就验收复用；同 D 任务排队/运行时持续跟踪，不重复派发。
   **全仓库有 queued 记录不等于共享 writer 被占用。** 使用下面的实时队列检查。

## 实时队列与等待闭环

从同一个已读取的远端 main SHA 获取 `scripts/dc20_writer_queue.py` 和
`scripts/candidate_delivery_watchdog.py`，保持在同一目录并核验两者文件 SHA，
使用已有授权 API 凭据只读运行：

```sh
python scripts/dc20_writer_queue.py --signal-date YYYYMMDD --workflow run_primary_d_daily.yml
```

脚本仅 GET，不取消作业、不派发、不写仓库。`CLEAR` 表示可继续既有调用前检查，
不是生成成功；`WAIT` 必须跟踪返回的 run。API 不可用、权限不足、限流或分页不完整
不能解释成无锁。凭据不得写入文件、日志或聊天。

队列判断使用 GitHub API `2026-03-10` 的真实 concurrency `group_members`，
结合分页活跃 runs、具体 run 和 jobs。不是仅凭 run 的 `queued` 字样或配置中有组名。
只有异日旧 queued、无 jobs、经并发 API 成功读取确认不持有或等待实际锁，
且不属于当天目标任务的记录才可作为孤立异常隔离，记录 run id 后继续。
真正运行中的 writer、当前目标任务和不确定状态仍然等待；原共享锁与 CAS 不变。
例如 2026-11-03 的旧 run `37655511595` 显示 queued，但 jobs 和关联并发组均为空，
repo 活跃并发组为零；普通与强制取消均返回 409。它不是新 D 的有效互斥证据。
不删除该记录、不反复取消，不把平台的孤立记录误报为正在生成。

本地 19:10 主任务必须持续跟踪到公开验收完成或受控窗口截止。上游未齐、排队、
孤立旧记录均不是结束条件；正常等待每 60 秒复核一次，连续 15 分钟没有推进时
报告一次具体阻塞并继续跟踪。上游 Data/Top10 一旦 exact-D 验收通过，立即进入
受控 P0，不把后续 GitHub cron 当作准点保证。派发前再次核对队列、远端 SHA 和同 D
产物；响应未知时先查 API 真实 run，禁止盲目重发。每个固定源版本最多三次受控尝试。
到 D 日 23:30 仍缺项时报告实际缺失并停止新的日常受控 P0，保留自然 schedule；
不自动转成历史恢复。正常等待不重复通知，失败和关键状态变化必须留存证据。

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

## 主榜交付与辅助更新分别验收

- P0：同 D 晋级榜和连板路径，真实硬范围最多 Top10，不补票，不改变冻结名次。
  晋级榜已通过必要源数据、模型、日期与指纹校验时可先发布；盈利榜尚未完成必须
  明确待验证，不以旧 D 或旧 P1 排名替代。
- 当前正式固定 Ridge 盈利分支：P0 → candidate natural forward → observer →
  candidate publisher；到期结果由 candidate settlement 独立更新。必须核对
  `work/profit_1000_upgrade/candidate_natural_forward/day_D.json` 与 `workflow_D.json`、
  `candidate_natural_evidence/D/context.json`、
  `outputs/decision/candidate_profit_v1/day_D.json`、索引同 D 项和 summary 逐笔账本，
  以及模型 SHA、P0 源 SHA 和实际 Pages revision。
  `late_candidate_research` 事后补算展示不等于正式前向冻结，不能写入正式模拟账户。
- Pages 主榜：核对最终 main、部署 revision、公开首页和 dated 下载产物一致；
  晋1/晋2/晋3、固定 Ridge 盈1/盈2均已验收后才报告“五个排名已公开”。
  记录启动、P0 生成、Ridge 冻结、五个排名公开的真实北京时间。
- 辅助模块：晋级历史统计、盈利次日表现、研究台与模拟账户分别记录状态、数据日期、
  源版本和指纹；未通过本模块校验时明确待更新/不可核验，不补零、不冒用旧日期。
  辅助失败不得篡改或隐藏已核验主榜；主榜完成与辅助待补应分开报告，不能称所有模块完成。
- 研究台仍由正式候选盈利发布任务直接调用 `profit_research.yml`；不再等待退役的
  P1/Shadow。保留既有定时回退，不新增第四层 `workflow_run`。
- 旧 P1/Shadow：不再是每日主榜或联动完成条件。仅保留手动 `dry_run=true` 审计入口，
  不提交、不发布、不创建新正式冻结；历史文件仍按其原 D、原来源和原指纹存档。

共同上游 Data/Top10 真失败仍阻断依赖它的计算；旧 Decision/Premium 独立失败不阻断 DC20。
P0 已完成但当前 Ridge 尚缺时，跟踪其真实同 D 任务与生成证据，不等待或重启旧 P1/Shadow。
不得把 `RETROSPECTIVE_RECOVERY` 用作每日默认回退，不得重复冻结或覆盖既有同 D 数据。
过窗、未来提交、错日、必需输入缺失、部分正式冻结、CAS 冲突等仍必须如实 BLOCK，保留证据。
数据更新不授权训练、模型升级或更改交易规则；未到期、缺失、未成交分别披露。

## 已退役自动任务与恢复边界

- `.github/workflows/run_primary_profit_rankings.yml`、
  `.github/workflows/run_primary_profit_forward_shadow.yml` 只保留手动只读审计。
- `diagnose_candidate_handoff.yml` 只保留手动诊断，不再随成功候选流程重复运行。
- 原 `accept_forward_inputs.yml` 无合法手动自然采集分支，归档原工作流；
  共享 forward 输入/推理模块及已有手动测试入口保留，不伪造 schedule 身份。
- 8 个日期固定的一次性修复入口及固定 run 的 capital replay 原样移至
  `.github/archived-workflows/`，文件 SHA256 记录在其 `manifest.json`；该目录不会由
  GitHub Actions 自动执行。历史运行与输出不删除。
- `run_decision_daily.yml` 的 P0 自然定时回退、必要结算/恢复、周末训练/回填配置本轮保留。
  不因名称含 Legacy、research 或 shadow 而删除共享能力。

恢复旧代码/触发配置须作为独立受控变更，不重新标记或补造已错过日期的自然冻结。
退役任务没有新 run 是正常状态；真实在途任务仍按实际 jobs/并发锁安全判定。

## 变更边界

此变更隔离主榜交付与辅助更新、退役旧自动链，不重训模型，不改股票排名/盈利分值，
不回写历史账本，不生成订单、目标仓位或正式交易 Action。
本地测试和 GitHub CI 通过只代表接入代码验收；当晚 exact-D 完整链成功后
才能报告实际联动成功。


## 已提交 P0 后公开部署失败的受控续接

原始来源支持受控 NATURAL 手动生成或注册 schedule 的首次原始运行：首次 P0 的 compute/CAS 成功但
仅 Pages 公开验收失败时，先保留原 run、原 CAS 和
原生成 artifact；先验证公开字节确实已收敛，再按相同 D、相同两个上游提交执行
一次受控 NATURAL 复用。不得重跑旧 run 或重新生成已有榜单。
后继必须成功且 generation/CAS 均跳过；支持自然 schedule/workflow_run，或在
D 日 15:00–23:30 创建、名称精确匹配的受控 workflow_dispatch。普通手动运行、
dry_run、历史恢复、跨日调用、attempt>1 均不具备该身份。

已有 P0 而正式候选冻结缺失时，检查候选分支没有 active run，然后调用现有
`research_candidate_natural_forward.yml`：`p0_run_id`=成功复用 run，
`original_generation_run_id`=原始部署失败 run，`original_cas_commit`=原 CAS 的
40 位 SHA，`dry_run=false`。原始生成与后继部署身份始终分别记录。
续接仍须验证原 ZIP digest、CAS 单父提交、全量源文件、两个上游时间、完整代码
版本、同 D 日历、冻结缺失状态及 T 日09:15截止；不能因允许受控事件而省略任何
证据。完成后仍需 observer、正式发布、研究台和模拟账户的逐字节公开验收。


看门狗可自动发现上述受控续接：以同 D receipt 的 `inputs.git_head` 锁定原生成
run 和唯一 CAS，只接受 compute/CAS 已成功而公开部署失败的记录。已存在成功的
P0 no-op 复用时，复用该部署身份；没有复用时，仅在同 D 15:00–23:30 窗口内
按原 receipt 两个上游提交派发既有 P0 no-op，下一轮再续接正式候选冻结。
过窗只能等待既有自然 schedule 或报告明确阻塞，不改写时间和原生成身份。
同 D 活动任务始终复用跟踪，API 查询失败不视为空队列；最终证据校验仍由原
导入器执行，T 日09:15截止与全部源指纹规则不变。

原生成是 schedule 时保留真实事件、注册工作流名称与原始空 target envelope；
从原 CAS receipt 与源元数据交叉绑定实际解析的两个 40 位上游提交，独立查验
上游提交和源生成时间均不晚于原运行创建时间。不能把 schedule 改写成手动身份。
仅接受同 D 15:00–23:30 原始生成、原 artifact、唯一 CAS、完整 reviewed 代码组合。
D1106 及此前历史 capsule 保留其整套原始 importer/capture/observer profiles；不改旧字节。

## 20261107 交付补强与仍保留的边界

- P0 前先检查严格日历前 20 个交易日的必需历史表。发现缺失/空表时，看门狗只派发
  `prepare_primary_history.yml`，锁定当前 main、D 与市场源 commit；该独立任务在共享
  写锁下校验原冻结引用并作单次 CAS。下轮读新 main 后才派发 P0，绝不合并进原 P0 artifact。
  该准备任务仅手动受控入口，同 D 15:00–23:30，有活动任务不重复派发，API 错误不当成功。
- 合法晋级池只有 0/1/2 只、盈利合格池只有 0/1 只时，不要求凑足晋3/盈2。
  排名必须连续且唯一，缺失 slot 必须明确 `MISSING_CANDIDATE`，不能伪造股票或零收益。
- Pages 显式隔离旧历史故障时，只在 revision、公开主榜、当日盈1/盈2文件及其指纹一致后
  报 `CORE_DELIVERED_AUX_PENDING`；表示当日核心榜已交付，历史汇总/账户仍未完成。
  健康路径同样核验公开主榜，不仅核验盈利文件，避免误判成功或重复派发。
- observer 已提交完整原 capsule 后仅 artifact 尾部或 checkout 清理失败，可复用同一原始
  observer run 的完整 ACK artifact：原核心计算/写入/工作区未修改检查必须全部成功，
  原 ZIP digest、完整 capsule、唯一 CAS、时间窗口、source/model 指纹依然逐项校验。
  复用后直接发布，不重写 context，也不把失败 run 伪装成功。ACK 缺失/过期、核心步骤失败
  或正式文件被修改时，仍不能恢复。
- 部分四件冻结不能盲目补跑：原子 CAS 正常情况下应一次提交完整四件。
  缺少可验证原 artifact 和唯一 CAS，或现存字节有冲突，明确要求修复并保留原冻结，
  不自动覆盖、重算或补造时点。本轮不以放宽证据守卫换取“必定有名单”。

## Ridge 新冻结重试预算的原始来源下界（20261110 修复）

11 月 10 日早间四次 Ridge 失败（42717610137、42721268555、42723775332、42727404242）
分别绑定 P0 42716693737、42720359036、42723200937、42726704719；这些 P0 的不可变
运行版本均仍指向 11 月 9 日名单。它们发生在 11 月 10 日原始生成 P0 42811412247
创建之前，仅按北京时间创建日计数会错误阻塞当晚的新来源。

仅 Ridge 新冻结的预算增加时间下界：从已由 receipt、原始 jobs 和生成身份核验的
**原始 P0 创建时间**开始计数。成功生成及原 CAS 失败部署的续接采用同一规则；
续接绑定原生成 run，不采用后继 no-op、当前 main 或代码提交时间。三次上限不变；
下界起所有失败/超时仍保守计入，包括 preflight 失败和后继部署之前的失败，
不因跨午夜、代码修复或换后继部署而重置。缺失或无效来源时间/身份仍拒绝派发。
P0、observer、publisher 的预算与既有队列、截止时间、原始 artifact、CAS、冻结/观察/
公开验收门禁均保持不变。此修复本身不代表盈利榜已冻结或上线。
