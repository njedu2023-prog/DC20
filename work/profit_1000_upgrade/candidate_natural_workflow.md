# 固定新模型每日研究冻结与发布

本步骤把已复算通过的固定模型接到原 P0 名单后，**不是替换正式晋级/盈利模型**。
模型、登记、原 P0 导入器、D 特征适配器均固定 SHA。只新增独立研究日文件，
不改网页、正式名单、正式账本、旧统计或既有晚间定时任务。

## 自动入口

`.github/workflows/research_candidate_natural_forward.yml` 在原 P0 成功完成后触发，
先验证新模块及原模型冻结，再从**同一个原始 P0 run**的 Pages artifact 读取发布版本。
只从该 Git tree 导入完整 28 个原始文件；不读取公共页面 latest、不拿现有 main 冒充 D 来源。
P0 必须是自然生成、main、首次成功 attempt，原计算/CAS/部署三个 job 都成功。
新预测、原冻文件与本地回执必须均在 T 09:15 前，给 09:25 实际竞价预留十分钟；晚到拒绝。
D 从 2026-09-14 开始，不能把旧 D 重标为未来样本。

可在 GitHub 手动启动同一工作流，提供原始 `p0_run_id`，默认 `dry_run=true`。
只有明确关闭 dry_run 才提交研究冻表。没有改日期、时钟、模型或激活的可选开关。
push 代码只运行验证，不抓取市场、不生成未来样本、不发布冻表。

## 研究记录与审计

每个 D 仅新增 `work/profit_1000_upgrade/candidate_natural_forward/` 下四份文件：

- `day_D.json`：原始完整候选排名及新模型/晋级 Top1、Top2 四个独立研究槽；亏损预测不跳过。
- `p0_sources_D.json`：原 run、artifact、Git 版本、28 个来源原字节绑定。
- `local_freeze_D.json`：新预测本地完成时间及原文件摘要。
- `workflow_D.json`：本次研究工作流与代码版本、前三份文件摘要。

GitHub Git API 单次非强制 CAS；只允许这四个原本不存在的路径，逐一验证新 blob 与完整 tree，
禁止覆盖、移除旧文件或顺带修改其他目录。同日已有记录或 main 冲突则停止，不重算/重试。
CAS 前保存来源和本地冻结回执；结果不明时仍保留失败证据，不写成功回执。
实际成功的 commit/tree/原字节摘要及主机 ACK 时间保存在 workflow artifact。
所有 token 只用于 api.github.com，请求跳转不转发 token；不使用 SSH 或 git push。

artifact 保留 90 天。原时钟和 Git API ACK **不是独立自然资格证明**：
后续仍需观察该真实 Git commit、成功 job 时间和原冻文件，确认是在竞价前完成发布。
`production_activation_allowed=false` 与 `natural_forward_admission_issued=false` 保持不变。

## 明确限制

这一步还不能证明“每日必有新模型冻结”。P0 未发布、数据损坏、GitHub 延迟、并发冲突或
触发链限制都可能阻止生成；独立并发组不会打断原 P0/P1，但也不是交易所级准时保证。
失败后只允许在原时间窗内、新建工作流并指向同一原 P0 run；不使用 attempt 2 补写资格。
若已有同日文件，不覆盖，需先只读核查原提交。

原模型继续使用；固定新模型的替换阈值尚未配置。历史开发样本的正收益不记入这组自然成绩。
新的 T/T+1 行情采集、原规则结算和真实发布时间独立核查由后续组件负责；未到期、缺失、
未成交、持仓与已结算严格区分，不把缺失收益当零，不承诺盈利。
