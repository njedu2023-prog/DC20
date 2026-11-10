# P0 逐股缺口诊断与停止边界

本改动不改变模型、选股范围、晋级名次、Ridge 参数或交易规则。
正式 P0 不能完成时，保存完整逐股身份/板数审计，并生成独立 JSON 与 HTML；
错误仍返回退出码 2，不授予正式排名或业绩账本资格。

## 已有正式逐股隔离

固定 Ridge 已有经过注册的逐股评分资格规则：只有来源、身份与原始晋级池均可信时，
`INSUFFICIENT_OBSERVED_HISTORY` 或 `MISSING_D_OBSERVATION` 才允许某股不评分，
其他可评分股票继续使用原始输入。原晋级名次、完整候选池及全池特征不变；
输出保留原候选数、可评分数与逐股排除原因。该规则不等于所有异常都能忽略。

## 本次 P0 诊断边界

`RECOMPUTED_STAGE_MISMATCH` 等身份/板数映射异常仍阻断正式 P0 及依赖它的 Ridge。
原因是模型使用全池特征，例如 `stage_pool_share`、`focus_pool_size`、
`stage2_pool_size`、`stage3_pool_size`、`promotion_rank`。删掉问题股后重算可能改变
其他股票的输入与排名，不能把所得结果冒充完整原池的正式排名。

新增 `PrimaryPoolIdentityError` 保存原审计的所有股票、公司名和每股原因。
身份/板数映射通过的股票只标为 `PASSED_NOT_SCORED`；这不表示全部特征或评分已通过。
若失败发生在逐股审计之前，则候选数量和影响范围显示尚未核验，不猜测正常股票。

| 问题 | 正式处理 | 诊断输出 |
| --- | --- | --- |
| Ridge 单股明确历史不足/缺 D 行情 | 按既有规则只禁用该股评分 | 原池、可评分数及排除原因 |
| P0 单股连板重算与上游不一致 | 停止受影响的正式排名 | 保留整池身份/板数检查，明确异常股 |
| 全局日期、来源指纹、模型等错误 | 继续停止依赖它的计算 | 若主入口捕获错误，显示审计前失败，不假定影响范围 |
| 诊断文件本身写入/校验失败 | 原生产失败不变 | 记录诊断不可用，不能转为成功 |

## 产物与交付

独立路径为 `outputs/decision/primary_diagnostics/day_YYYYMMDD.json` 和同名 `.html`。
不写 `three_rank_index.json`、正式自然冻结、盈利索引或业绩账本。
JSON 固定 `formal_output_ready=false`、`formal_ledger_admission=false`、
`calculation_resumed=false`、`candidate_pool_modified=false`，并带自身 SHA-256。
创建时间是诊断时间，不是预测、成交或冻结时间。

P0 计算步骤失败后，工作流检查诊断日期、当前 run/attempt、自身指纹和 HTML/JSON 一致性，
将结果写入运行摘要并上传 `primary-d-diagnostic-RUN-ATTEMPT` 附件。
工作流不使用 `continue-on-error` 掩盖失败，不新增诊断 CAS writer。

**附件保存不等于 GitHub Pages 已公开。** HTML 可直接查看，结构也允许后续受控发布，
但本轮不自动把失败路径写入主仓或覆盖网站。对外报告须区分附件已保存与公开页面已验收。

## 验证要求

- 健康输入的审计返回值与现有正式输出不变。
- 注入单股板数冲突后，原候选数据不变、全池诊断完整、正式入口仍失败。
- 不允许篡改 JSON 的正式资格、恢复状态、统计数量或指纹。
- 不接受其他 run/attempt 的旧附件，HTML 必须与核验 JSON 完全对应。
- 股票名称按 HTML 文本转义；输出目录及文件拒绝符号链接。
- 诊断生成不写正式 index、预测或历史冻结文件。

## 冻结补跑与代码验收等待

Watchdog 在派发缺失的固定 Ridge 冻结前，复用生产代码验收器检查当前 main 的
完整静态文件集合是否已有成功 push CI；批准必须对应真实 run、job 和实际执行的
回归、模型指纹与验收记录三项步骤。每日来源、日期、冻结与发布证据仍独立核验。

相同代码尚在验收时，只返回 `WAITING_FOR_CODE_CI_APPROVAL`，不创建新冻结任务。
完全没有匹配 CI 时明确给出 `NO_MATCHING_SUCCESSFUL_PUSH_CI`；相同代码最近一次
CI 已失败或取消则报 `CODE_CI_FAILED_REQUIRES_REPAIR`，不能伪装为普通等待。
API 错误或不完整证据继续失败。已经发生的失败全部保留在原有重试计数中，
不豁免、不清零，原始冻结及竞价前截止时间也不延长。
