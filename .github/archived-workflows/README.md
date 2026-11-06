# 已退役工作流原件

这里保存 2026-11-07 日常交付清理中退出活动目录的工作流原件。GitHub Actions
只识别 `.github/workflows/` 中的定义；本目录的 cron、push、workflow_run 不会执行。

`manifest.json` 记录每份原件的来源路径、归档路径、SHA256 和基线 commit。归档不删除
历史运行、正式冻结、研究输出、共享脚本或模型。需要调查历史时使用原 commit 和运行证据，
不能把恢复旧 YAML 当作补造当时自然冻结的许可。

- 八个固定日期的修复任务已完成其历史用途。
- `research_profit_1000_capital.yml` 只认可固定历史 run，已退出自动监听。
- `accept_forward_inputs.yml` 的自然输入演练退出重复定时运行；其采集器严格要求真实
  schedule 身份，没有合法的手动自然采集分支，因此保留完整原件而不伪造调用方式。

当前 P0、固定 Ridge、Pages、结算、监测和必要恢复流程仍保留。旧 P1/Shadow 在活动
工作流目录中只允许手动 dry-run 审计，不能发布或占用生产 writer 锁。
