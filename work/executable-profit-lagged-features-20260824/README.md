# Executable-profit lagged-prior research

这是 DC20 的独立研究原型，不是正式运行时组件，不允许发布排名或交易动作。

运行顺序（需使用含 pandas、numpy、scikit-learn、pytest 的 Python 环境）：

```bash
python work/executable-profit-lagged-features-20260824/lagged_priors.py \
  --repo-root . \
  --output-dir work/executable-profit-lagged-features-20260824/outputs

python work/executable-profit-lagged-features-20260824/benchmark.py \
  --repo-root . \
  --work-root work/executable-profit-lagged-features-20260824

python work/executable-profit-lagged-features-20260824/fit_internal_challenger.py \
  --repo-root . \
  --work-root work/executable-profit-lagged-features-20260824

python -m pytest -q work/executable-profit-lagged-features-20260824/tests

python work/executable-profit-lagged-features-20260824/validate_artifact_index.py \
  --repo-root . \
  --work-root work/executable-profit-lagged-features-20260824
```

`RESEARCH_REPORT.md` 是结论摘要；`outputs/benchmark_report.json` 是完整机器可读结果；`outputs/internal_forward_challenger_audit.json` 明确挑战者只能内部前向研究且不是 READY；`validate_artifact_index.py` 只验证 NOT_READY 证据完整性，不存在 release 模式。

报告沿用 `confirmation` 作为最后 180 个历史 D 日的字段名，但这一窗口已经被查看，只能解释为回溯探索段；它不是独立未触碰确认集，也不是前瞻放行证据。

不要从不受信任来源加载 pickle；`outputs/internal_forward_challenger.pkl` 只与本目录锁定的审计 SHA 一起使用。
