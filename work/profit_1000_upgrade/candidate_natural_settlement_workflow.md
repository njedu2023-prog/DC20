# 已授权的有限自然研究结算任务

本模块只接线已冻结的自然研究 Top1/Top2 与晋级 Top1/Top2 四席。四席属于独立研究策略，
不是四笔实际下单；重复股票采集一次，空槽、亏损、未买入、缺证与延持均保留原合同状态。
没有训练、生产模型激活、实盘下单、购买订阅或前台扩展。原 T+1 10:00 出场／封板延持
经济内核及 45bp 成本不在本模块重算、修改或额外扣除。

用户于 2026-09-14 明确确认：交易日北京时间 19:50、20:50，以及正常名单验证后的
自动任务，可使用现有 Tushare 额度采集这四席并追加 GitHub 研究影子账本。此权限不是
自然样本已成功结算、收益已提升或正式模型已启用的证据。

## 固定入口与无交易日行为

```sh
python -m work.profit_1000_upgrade.candidate_natural_settlement_workflow \
  --work-parent /isolated/existing/artifact-parent
```

默认只读预检；只有增加 `--execute` 才运行采集与研究 journal 追加。不存在 as-of、
token、URL、股票、重试、费用、退出时间或强制覆盖参数。实际执行仅允许 Linux、
`njedu2023-prog/DC20`、main、首次 attempt，固定登记 workflow 名称／路径／真实 API
run 身份与运行代码 SHA。schedule、workflow_dispatch、成功 observer workflow_run
是唯一允许事件。workflow_run 还要求原固定 observer ID、main、首次、成功与非 push。

手动 dispatch 和 observer 事件按宿主时钟和固定日历，只在**北京时间当天是交易日且
已过 15:00**时处理当日 as-of；盘前、周末或休市日返回
`NO_CLOSED_TRADING_SESSION_TODAY`。它们不能借用 schedule 的补结算权限。

定时事件必须携带原事件中两个固定 cron 之一，并通过真实 API 的当前 workflow/run
身份校验。根据 API `created_at` 定位该 cron 最近一次周一至周五的计划时点，要求
`created_at <= run_started_at <= 宿主当前时间`，且当前时间距计划时点不超过 **12 小时**。
据此保留计划时点对应的北京时间交易日，再用固定 SHA 日历确认已收盘，不由调用者指定
as-of。例如 9 月 14 日 19:50 的任务延至 9 月 15 日 01:06 启动，仍处理 9 月 14 日；
周五定时延至周六凌晨同理。休市日的计划不能倒退为更早交易日，周末没有新的计划时点，
超过 12 小时或缺少／矛盾的 API 时间证据均失败关闭。

plan/result 保留日期选择依据、API 创建／启动时间、计划时点、宿主时间与迟到秒数。
同一计划任务执行跨午夜可以继续使用固定 as-of，所有 guard 仍检查 12 小时上限及
时钟单调；手动／observer 跨午夜、宿主或计划日超出固定日历、时钟倒退、代码或原输入
字节变化仍失败关闭。补结算沿用现有四日和请求上限，不扩大采集范围，也不读取未来数据。

正常 schedule/dispatch P0 → natural → observer → settlement 为三层 workflow_run。
若某个 P0 自己已由更上游 workflow_run 触发，GitHub 的三层限制可能使即时 settlement
不触发；独立 19:50、20:50 调度仍是兜底。不得仅凭 YAML 存在宣称真实触发已验收。

## 原始来源与资格边界

从 fresh main 的完整 Git tree 建立 D 索引，D 不早于 20260914。只接受原封存 snapshot、
observer context、原 journal manifest；未来日期不读 body，目录不能授予来源资格。
本地 checkout 的每个读取文件必须逐字节匹配 fresh main Git blob 和外部 SHA。
新 workflow checkout 可以追踪最新 main 数据，但当前运行代码和 fresh main 都必须
保留同一套受审代码，不能借数据同步更换已运行的逻辑。

每个待处理 D 必须经过原独立 issuer：同 observer 的完整成功 run/job/artifact、
ACK commit、Git-held 原 capsule/context、T 09:25 前完整时间链、全部代码和数据 SHA。
普通字典、调用者布尔或本地摘要不能替代私有 `VerifiedResearchPublication`。
原 observer ACK artifact 当前仍有 90 日保留期限制；这不是永久离线证明。

零候选仍追加四个明确空席，不请求行情。无自然 snapshot 或尚无 observer context
只报告缺少证据；不必配置 Tushare token 才能完成零请求分支。GitHub 读凭证仍用于
必要的 run／原发布证明核验；实际 journal 写分支使用原限权 writer。

## 四日预算、公平轮转与不可变恢复

每次最多处理四个 D；按固定北京时间日序和 20:50 窗口进行确定性批次轮转；迟到定时
使用原计划时点轮转，19:50、20:50 的两个批次不会因都延至凌晨而合并。即使前
四个 D 的证明永久失败，后续 D 也不会永远被它们占住。计划 artifact 明示全部索引
D、缺 context、未来文件、选中／延后日期、轮转索引。轮转不删除任何四席。

每 D 原 daily 最多八步、96 请求、300 秒；全次最多 384 次行情请求。每步只请求
四席股票去重并集的最早缺证交易日，原 collector 保持零自动重试和失败证据留存。
Git writer 另有原独立 API／字节／时间预算；预算不足只能失败，不能拆批绕过。
工作流执行步骤另设 45 分钟上限，整个 job 为 55 分钟，预留诊断上传时间。各层上限
不是每次一定处理完四日的承诺；平台强制取消等异常仍可能导致 artifact 未成功保全。

已有同 as-of journal 只复核，不恢复、不采集、不改字节。已有终态也只读复用；
未决旧日先验证全部 journal bytes，再恢复到原绝对路径，才把原 receipt/ledger 的
外部 SHA 交给 daily。原路径已有文件则拒绝覆盖。新 journal 在调用 Git writer 前
以及 pre-CAS callback 中均全量 `validate_journal`，并重新构建检查原来源未变。
Git 仅追加一个 D/as-of 目录，fresh main + force:false，原全部文件保留。

**不承诺跨失败运行 exactly-once。** daily 若已发请求后超时/异常且未生成合法 manifest，
只能保留本次本地 artifact，不能编造 journal。若后续 runner 无法取得该失败状态，
原未发布请求历史无法自动当成已恢复状态；需核查失败 artifact／权限下的恢复决定。
同 as-of 的已成功 journal 与已确认原历史不重采。CAS 结果不确定不自动重试或删除，
下次先只读核查 main 原目录。

## 结果和测试声明

独立 artifact 包含 plan、每 D publication ACK／固定失败状态、statistics、result。
统计明确 `PARTIAL_SELECTED_DAYS_NOT_FULL_CUMULATIVE`，列出 covered/omitted D；
不能将最多四日的采样报告称为全历史累计，也不改前台已有统计。每个原 ledger 输入
仍由独立 statistics 校验四席／原经济合同。缺账本与缺来源没有收益值，不能补零。

测试全部显式 synthetic hooks，禁用 socket；真正 native daily → collector codec →
outcomes → journal → 次日原字节恢复在 TEST_ONLY 目录验证。私有发布证明和真实 Git
远端 facade 在编排测试使用清楚标识的模拟边界；测试不能签发真实发布／行情资格。
实现、合成测试、云端触发与真实前向结算是四件不同的事情。
