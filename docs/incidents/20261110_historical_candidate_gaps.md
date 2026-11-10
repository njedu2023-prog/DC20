# 2026-11-10 历史固定 Ridge 冻结缺口审计

审计基线：`9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981`。以下结论来自该不可变版本、实际 GitHub run/job/artifact、原始归档字节和只读校验。没有重算历史预测、创建冻结、写入收益或改变模型。

## 结论

`20260921`、`20261022`、`20261102` 均未找到可以恢复为正式前向记录的原始候选冻结。这三日不是“现存正式冻结被新解析器错误拒绝”的情形。原始 P0 名单仍可核验；P0、旧 P1/Shadow 或事后研究结果不能替代固定 Ridge 原始冻结。

当前三日均缺少以下正式链文件：

- `work/profit_1000_upgrade/candidate_natural_forward/day_D.json`
- `work/profit_1000_upgrade/candidate_natural_forward/workflow_D.json`
- `work/profit_1000_upgrade/candidate_natural_evidence/D/context.json`
- `outputs/decision/candidate_profit_v1/day_D.json`

在基线 main 的上述三个目录中，三个日期均无匹配项；三条 `candidate_natural_forward/day_D.json` 路径的 main 祖先提交历史查询均为空。公开研究数据把这些日期标为 `RETROSPECTIVE_UNVERIFIED`，未给出盈利排名、固定模型冻结特征或可结算前向收益。

`FROZEN_BINDING_OR_MEMBERSHIP_REJECTED` 是汇总原因。此次三日进入该分支的直接条件是 `NO_FORMAL_FROZEN_DAY`：`profit_research_operations.py` 在尝试绑定成员前先要求 `FORMAL_FROZEN_REFERENCE`。不能仅凭汇总码判断为 hash 或成员解析兼容问题。

| D | 原始固定候选结果 | 可保留的证据 | 当前处理 |
|---|---|---|---|
| 20260921 | 原始来源导入后，完整历史窗口门禁拒绝；没有生成冻结输出 | 原 P0、21 个行情日文件、原始失败 artifact | 保留前向缺口；后续逐股资格隔离规则不追溯制造该日冻结 |
| 20261022 | 前置导入失败，冻结 job 跳过 | 原 P0；已明确标为事后研究的补算；修复后 Pages 归档 | 保留前向缺口；归档容量问题已修复，但不能补回过去的冻结时点 |
| 20261102 | P0 为明确的 `RETROSPECTIVE_RECOVERY`，候选入口正确拒绝 | 事后 P0 receipt、原始 API run、工作流输入日志 | 保留 `forward_eligible=false`；不得改成 NATURAL |

## 20260921：原始历史窗口不足，未形成冻结

原 P0 [35593772393](https://github.com/njedu2023-prog/DC20/actions/runs/35593772393) 生成的不可变提交为 `13c33e2290339ba00d3b505205827610c3c9da87`。其 dated JSON 生成时间为北京时间 2026-09-21 19:24:01，P0 原始名单 10 支；该文件在 main 历史中只有这一笔提交。

固定候选 [35594012204](https://github.com/njedu2023-prog/DC20/actions/runs/35594012204) 的 job `106314924151` 在 `Import exact original run and freeze four independent research slots` 失败。实际日志的外层错误为：

```text
CANDIDATE_NATURAL_WORKFLOW_BLOCKED; retained local evidence is not admitted
```

本次从 GitHub 重新下载并核对两份原 artifact：

| Artifact | SHA256 | 实际内容 |
|---|---|---|
| `10635237770`，`dc20-candidate-preflight-35594012204-1` | `e9498942793a1cde01ee4ccfa74c2600eac721d0873a24bb50a2162bd06ce605` | `NEW_P0_REQUIRES_ORIGINAL_IMPORT`；`model_predictions_computed=false`；`files_published=0` |
| `10635946821`，`dc20-candidate-natural-35594012204-1` | `7f2e97f6e8a209f425d40e42dec8840e3165e05f6a3f6818d21690b226a7bbea` | 28 个文件，全部位于 `sources/`；没有 candidate freeze 或 publication evidence 输出 |

第二份归档为 8,107,747 字节，摘要与 GitHub artifact 元数据完全一致；其中 P0 JSON 与当前保留的原始 P0 逐字节相同。

对原始归档中的 21 个 dated `daily.csv` 逐一计数，`601123.SH` 只有 15 个交易日存在一条记录，以下 6 日不存在该股票记录：`20260824`、`20260825`、`20260826`、`20260827`、`20260828`、`20260831`。这是原字节中的历史不足，不应补行或填造 21 日数据。

原提交的 [candidate_natural_forward.py](https://github.com/njedu2023-prog/DC20/blob/13c33e2290339ba00d3b505205827610c3c9da87/work/profit_1000_upgrade/candidate_natural_forward.py) 只接收完整投影状态，并要求 `projection_window_complete=true`；不满足时使用 `D_SOURCE_PROJECTION_BLOCKED` / `COMPLETE_D_OBSERVED_WINDOWS_REQUIRED` 门禁。外层日志未公开具体内部异常，因此这里区分日志直接观测到的错误与源码、原行情计数共同证明的失败条件。

### 后续规则现状

该类“单只股票历史不足导致整批不能评分”的问题后来已有版本化的逐股资格隔离路径，当前基线代码仍保留它：

- [candidate_eligible_forward.py](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/work/profit_1000_upgrade/candidate_eligible_forward.py) 在完整来源字节校验后，允许显式历史不足投影进入逐股分类。
- [candidate_eligible_pool.py](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/work/profit_1000_upgrade/candidate_eligible_pool.py) 的策略为 `dc20_individual_observed_window_eligibility_20260922_v2`，仅隔离 `INSUFFICIENT_OBSERVED_HISTORY` / `MISSING_D_OBSERVATION`；合格股票仍须 21 个有效观测日。完整候选池、晋级排名、来源 hash 和日期守卫不变。
- 当前已提交的 [day_20261008.json](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/outputs/decision/candidate_profit_v1/day_20261008.json) 实际保留 7 支晋级成员，对仅有 11/21 观测日的 `605303.SH` 标注不合格，只给 6 支盈利评分；固定模型 SHA 仍为 `999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589`。

后续规则已经落地不意味着 09/21 曾存在该规则下的盘前冻结。把新规则用于重算再登记成原日冻结，会改变历史契约和实际生成时点，不能作为恢复方案。

## 20261022：打包容量问题已修复，原冻结没有产生

原 P0 [39771417729](https://github.com/njedu2023-prog/DC20/actions/runs/39771417729) 对应提交 `ba12e230fe7fed73e07240a5c3a75311fa798da4`，dated JSON 生成于北京时间 19:33:43，包含 7 支原始晋级成员。

候选 [39771913021](https://github.com/njedu2023-prog/DC20/actions/runs/39771913021) 的 preflight job `119801273975` 报 `CANDIDATE_TRIGGER_PREFLIGHT_BLOCKED / INVALID_OR_CHANGED_TRIGGER_EVIDENCE`，freeze job `119801953868` 为 `skipped`。后继 [39832465081](https://github.com/njedu2023-prog/DC20/actions/runs/39832465081) 同样在前置检查失败，freeze job 跳过。这些 candidate runs 均无可列出的 artifact。

10/23 留存的原始审计将内部失败定位为 `UNSAFE_PAGES_ZIP_ENTRY`：原 Pages artifact `12423155318` 的 `artifact.tar` 为 539,432,960 字节，超过 512 MiB（536,870,912 字节）。原 ZIP SHA256 为 `7e1fbb2b4ced8bde0ba91cad197a6541daff067a24285be04c70c5d76a3065b6`。本次重新读取原 P0 run 的 artifact 列表已为空，未声称再次取得该原 ZIP；此容量数字属于保留的当时审计证据。

当前 Git 中仍保留 [late_candidate_research/day_20261022.json](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/outputs/decision/late_candidate_research/day_20261022.json)，本次重新读取的原文件 SHA256 为 `ff44a3583b70d4fc373e57e49f54a6241f72ddf885792bbdb28836e47a232bce`。它明确写明：

```text
generated_at = 2026-10-23T16:16:51.041339+08:00
status = LATE_RESEARCH_RECONSTRUCTION
natural_forward_admission_issued = false
formal_ledger_written = false
```

该文件绑定原 P0 run、原 P0 commit、原行情/预测 commit 和原 Pages 摘要，是事后研究证据，不是原日正式冻结。

### 打包修复现状

当前 [deploy_dc20_pages.yml](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/.github/workflows/deploy_dc20_pages.yml) 仍调用 [stage_current_profit_research.py](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/scripts/stage_current_profit_research.py)：只暂存 `latest.json` 指向且摘要一致的一个版本，保留 Git 中历史版本，不提高 importer 的 512 MiB 上限。

本次以基线原源码重跑 [test_stage_current_profit_research.py](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/tests/test_stage_current_profit_research.py)，3 项测试通过，覆盖原字节复制、错误 hash 和符号链接拒绝。重新核对本地留存的修复后 artifact ZIP：SHA256 `582d7cfc79bb511b585c2197649d7688da2ad850fc6750430907e6438dfdb827`，tar 为 84,715,520 字节，匹配当时独立审计并低于原上限。

因此当前容量预防修复存在且可验证，但 10/22 freeze job 当时没有执行。修复打包不能补回原来的预测时间，更不能把晚算文件移入正式目录。

## 20261102：事后恢复必须继续排除在前向冻结之外

实际生成 P0 的 [41488438237](https://github.com/njedu2023-prog/DC20/actions/runs/41488438237) 是 `workflow_dispatch`，不是自然 schedule。它创建于北京时间 2026-11-03 01:09:29，在 01:10:28 生成 D=20261102 的 P0，提交为 `22ac6f1aaa1faaa03526c3d121909dfb4061c7a3`。

原 build job `125424822511` 日志明确给出：

```text
INPUT_GENERATION_MODE: RETROSPECTIVE_RECOVERY
INPUT_CONFIRM_RECOVERY: true
```

当前保留的 [primary_d_receipt_20261102.json](https://github.com/njedu2023-prog/DC20/blob/9a0d4f1f03aa2520bf47f2e3b7462e0ea1004981/outputs/decision/primary_d_receipt_20261102.json) 的 Git blob 为 `9e83d5865000300c58c826bb427b4935919e876e`，明确包含：

```text
generation_mode = RETROSPECTIVE_RECOVERY
forward_eligible = false
not_forward_generated = true
shadow_forward_ledger_eligible = false
recovered_at_utc = 2026-11-02T17:10:28+00:00
```

后继候选 [41489189485](https://github.com/njedu2023-prog/DC20/actions/runs/41489189485) 的 preflight job `125427395330` 于北京时间 01:15:47 报 `INVALID_OR_CHANGED_TRIGGER_EVIDENCE`，freeze job 跳过，artifact 列表为空。原提交的 `candidate_natural_p0_github.py` 要求手动 P0 的真实标题为 `DC20 controlled daily NATURAL | D=YYYYMMDD`。对未修改的实际 API run payload 单独重放原 `validate_run` 身份谓词，确定拒绝码为 `CONTROLLED_NATURAL_DISPATCH_REQUIRED`；这是有限的只读身份校验，不是完整 importer 或模型运行。

此拒绝是正确边界。改标题、改 receipt、把旧 run 伪装为自然生成或把 `forward_eligible` 改为 true，均不能创造原日预测证据。

## 检索范围与保留决策

本次对各日期北京时间 D 日 08:00 至 T 日 09:20 的 Actions 创建窗口完整分页读取，共计 138、85、87 个 run，数量与 `total_count` 一致且 ID 无重复。在这些窗口内，固定候选与 observer 均没有成功 run。所有已发现的额外失败候选 artifact 列表均为空；09/21 唯一非空失败归档已逐项验明只有原始输入。

这个结果与 main 的日档缺失、空路径历史、原 job 阶段及当前研究缺口一致。没有发现可以通过恢复原字节、修正引用或兼容旧格式而重新接纳的有效前向冻结。

保留现有隔离：不新增这三日的正式 `day` / `workflow` / `context`，不把研究补采或旧 P1/Shadow 写成固定 Ridge 正式账本，不给缺失收益记零，不改变模型、成员、时间或 CAS 守卫。09/21 与 10/22 的后续预防修复已经存在；11/02 对 retrospective 的拒绝应保持。后续只有发现新的、生成时点可独立核验的原始冻结证据，才重新审查恢复资格。
