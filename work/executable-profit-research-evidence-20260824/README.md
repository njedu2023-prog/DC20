# DC20 可实现盈利：被否决研究证据包

## 结论

本目录只证明一件事：已完成的 LR/HGB 回溯研究应被拒绝，状态必须保持
`RESEARCH_NOT_READY`。

最后 180 个 OOF 日期已经被查看，只能作为回溯探索，不能再称为独立确认集，
也不能作为 forward release evidence。本目录没有发布门、模型提升门、前端写入、
交易动作或正式模型制品。

以下标志全部永久为 `false`，任意一个被篡改为 `true` 都会使验证失败：

- `front_end_allowed`
- `official_trade_action_allowed`
- `model_publish_allowed`
- `production_model_selected`
- `formal_model_artifact_created`
- `release_validator_or_publish_mode_exists`

晋级模型与晋级排名保持冻结。本研究仅消费冻结的历史 Top10/P_fill OOF，未训练、
覆盖或发布晋级模型。

## 为什么被否决

LR 与 HGB 在已经查看的 180 日回溯窗口中都具有以下否决事实：

- Top2 普通成本平均收益为负；
- Top2 双成本压力平均收益为负；
- 联合概率相对同折基线的 Brier 改善为负，且 95% 区间整体低于 0；
- 相对冻结 P_fill Top2 的收益提升 95% 区间跨 0；
- 相对冻结 P_fill Top2 的盈利率提升 95% 区间跨 0；
- 没有锁定后开始的追加式 forward Shadow 证据。

`prototype_check_pass_count / prototype_check_total_count` 只是原型诊断计数，
不是完成度、百分比、模型分数或发布依据。本证据包不使用该计数作结论。

## 严格绑定

`research_state.json` 绑定：

- GitHub `main@cdbc43f67401c876d98f61585bea6d9375117e5b` 研究基线；
- 设计合同、历史 Top10 ledger、manifest、冻结 OOF、严格 SSE 日历；
- v2 runner 与合同测试；
- LR/HGB validation report 与逐行 OOF；
- 冻结晋级源代码哈希。

所有文件都使用 DC20 仓库内的唯一正式路径，不复制冻结 OOF，不引用 `/tmp`，
不读取 `top10-decision` 或 recovery snapshot。

## 文件

- `research_state.json`：不可发布状态、输入和研究制品哈希、精确否决事实。
- `validate_research_state.py`：只读验证器；只有“证据完整且仍被否决”才返回 0。
- `tests/test_validate_research_state.py`：状态、哈希、发布标志、解盲语义和晋级隔离的恶意篡改测试。

验证命令：

```bash
.venv/bin/python \
  work/executable-profit-research-evidence-20260824/validate_research_state.py

.venv/bin/python -m pytest -q \
  work/executable-profit-research-evidence-20260824/tests/test_validate_research_state.py
```

验证成功不表示模型 READY；它只表示 `RESEARCH_NOT_READY` 的否决证据没有被篡改。
