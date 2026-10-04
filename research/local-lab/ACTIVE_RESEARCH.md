# Codex 主动研究运行契约 V2

范围仅 research/local-lab，DC20 正式模型、名单、历史预测不修改，不交易。本契约由现有 dc20-1-2 夜间自动化执行，不新建白天唤醒。代码调度与 Agent 判断分别留痕；scan 成功不能称 Agent 已完成研究。

## 每次夜间的执行顺序

1. 使用绑定 NumPy 的本机解释器执行 `research/local-lab/run-nightly.sh --fetch --out <未使用的按时间归档目录> --trigger-origin SCHEDULED --trigger-id <automation_id:本次实际heartbeat时间>`。触发标识必须来自本次真实唤醒，不可编造。手工运行用 MANUAL，工程验收用 ENGINEERING_ACCEPTANCE。先遵守原任务交易日历和待补规则。维护锁冲突时结束并保留重试，不启动第二写入者。
2. 从报告 steps.learning.active_research 读取任务与 worker.due。维护失败先排错；不得根据陈旧报告宣布完成。没有新证据安静结束。每周最多一次常规研究会话；新影子风险审核任务可以提前触发。日常数据质量问题仍及时处理。
3. 报告的 run_context.run_id 是本次运行身份。due=true 时先执行 start 登记任务；若 worker.resume_run_id 存在，恢复该旧运行，start 加 --resume，保留原触发来源。每次最多三个任务。Codex 必须亲自读取任务 evidence_ids 的不可变原文及有关代码，而非只运行固定脚本。优先解决时间、单位、缺失、成交审核问题。没有合格结果只能提出机制假设与补数任务，不能声称发现统计规律。
4. 对新增合格预测/结果按同日、同池、同方法、同 basis 对齐，调查损失、校准、前二排序、未成交、退出失败及 DC20 分歧。描述性规律不是因果证据；禁止事后修改旧预测。每次最多深挖三个任务。
5. 如有值得检验的新方法，明确假设、机制、反证、缺失处理、主指标及风险限制，以 JSON 登记 research_agent register。登记必须早于确认样本；每周所有方法合计最多两项，不用改名绕过预算。允许从既有 PIT 特征选择新组合、登记最多三个乘积/差值交互特征。更复杂算法由 Codex 在本地独立候选代码中实现、测试、登记新版本协议后才运行，不临时 exec 未审查代码，不绕过门槛。
6. 候选 JSON 字段：task_id, evidence_ids, hypothesis, mechanism, falsification, missing_policy, primary_metric（固定 TOP2_NET_UPLIFT_WITH_TAIL_AND_COVERAGE）, risk_limits, basis（QUOTE_RETURN、QUOTE_PROXY 或 ACTUAL）, features, recipes（name以derived_开头，op为product/difference，left/right为所选原特征）。QUOTE_RETURN 仅允许原数值特征及其有界交互项，新增资金/题材特征先证明原截止前可得性，不使用事后 enrichment。允许字段见 allowed_features()。官方排序/盈利分禁止进入独立候选特征。
7. evaluate 使用登记之后采集的完整事前快照；沿用固定训练/校准/测试分割、成本敏感性和风险审核。样本不足保留明确阻断。每次成功评估后至少七天且新增五个合格日期才重新评估；结果反复查看只能算探索，不能据此自动晋升。禁止悄悄降低样本门槛。
8. 数值实验通过不等于有效。训练模型可以进入独立 SHADOW，但须先核对候选实现、测试、冻结版本与数据时间；用 learning.review lifecycle --state SHADOW 留理由。新影子结果由原 review 模块评价；生产自动采用保持关闭。重大数据错误或不可接受风险经核查后可用 ROLLBACK 停止该影子模型，不改旧模型/预测；保留失败方法。
9. 每个已处理任务通过 event 写 BLOCKED/COMPLETED/REJECTED、具体理由及证据 IDs。BLOCKED 必须说明还缺什么、何时重查；没有结果不能写 COMPLETED 表示方法有效。每个 event 必须带 run_id，最后 finish 使用同一 run_id 和本次实际处理任务 IDs。旧任务处置不能充当本次证据；所有任务完成处置前不可 finish。不能只生成任务就 finish。
10. 报告只在完成、故障、重要变化或需用户行动时通知。不得把工程测试写成盈利提升。失败会话不 finish，下一授权夜间重试；周末、午夜、白天规则保持原任务约束。

## CLI（在 research/local-lab 工作目录执行）

解释器：`/Users/moclh/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`

- `python -m learning.research_agent scan --day YYYYMMDD`
- `python -m learning.research_agent register proposal.json`
- `python -m learning.research_agent evaluate --proposal ID`
- `python -m learning.research_agent start --run-id RUN_ID --tasks ID ...`（恢复已开始会话追加 --resume）
- `python -m learning.research_agent event disposition.json`（task_id,state,reason,evidence_ids,run_id）
- `python -m learning.research_agent finish --day YYYYMMDD --run-id RUN_ID --tasks ID ...`

所有 CLI 使用现有 nightly.lock；试验测试数据库必须用 --db 单独路径。首次人工验收与未来无人值守运行分开。定时 Codex 是研究执行者，Python 是可重复的证据与验证工具；缺少前者不能声称自主找方法。

## 2026-10-02 入场政策覆盖
用户取消跳空硬限制，当前政策 `ANY_EXECUTABLE_AUCTION`，见 top2-nightly/METHOD.md 最新条款。所有候选按可执行证据审核结算；原情景排除重开审核。旧条件概率保留，区间外收益不用于旧概率校准。不得将重开审核说成已结算。

## 2026-10-04 本地预测核验与探索实验

夜间 learning/nightly.py 在 quote_review 之后调用 effectiveness.refresh_context 和 publish。只读取本地事前冻结预测，按 D/股票选择首次事前版本，不重复计算后续版本。到期后补取复权因子、涨跌停价和日线，结合已归档竞价及固定10:00分钟报价核验；未到期保留等待，不写未来结果。输出 data/reports/local-effectiveness-latest.json/md，历史正文追加存入学习记录并保留证据哈希。

报价结果单独进入 quote_ledger，不写入已审核 QUOTE_PROXY/ACTUAL 结算，不自动变成训练标签。涨停竞价、跌停退出及订单规模未知必须明确标注；官方名次缺失只影响官方前二对照，不阻止本地与全池比较。旧条件概率不在不限跳空报价样本上校准。

首个固定规则实验为 ranking_experiment.py：以冻结D日20日乖离升序选前二，同概率/同乖离边界均分名额；不采用入场后价格作为特征。实验在 ranking_hypothesis/ranking_experiment 中保存计划、输入、代码哈希及逐日结果，并通过研究事件处置。该实验仅为事后报价探索，不能代替第5—8步的登记后确认和可执行结果审核。没有新合格日期时不反复搜索规则；失败结果也保留。本轮较原榜单弱，未采用，未修改旧概率与生产权重。


## 报价学习、恢复及验收边界

维护已将 QUOTE_RETURN 就绪检查、独立训练门槛和研究任务接入原夜间入口。不改变唤醒频率。报价标签状态 VERIFIED_PRICE_PAIR，与 SETTLED 分开；snapshot 和 raw 的本地记录时间必须不晚于原预测截止。缺原池或原特征不倒填；整日不完整不进入学习。可得时间晚于下一阶段预测截止的标签会连同所属日期剔除。最低300条与60/20/20日期是预先固定的准入门槛，不能当作统计有效性的保证。

维护失败或进程中断：在新的输出目录重用原 `--run-id`、`--trigger-origin`、`--trigger-id`，追加 `--resume`。数据库锁内恢复并追加事件。已完成或正在等待Agent的维护不重复采集。Agent中断：复用原run_id与同一组task_ids执行start --resume，仅补未处置任务。下一授权夜间优先恢复；不新增定时任务。

QUOTE_RETURN 模型与影子记录独立存储，禁止进入执行模型自动晋升。新假设只能用登记以后采集的新事前快照确认，旧结果只能用于动机或探索。新影子预测必须来自预测截止前已存在的模型，且模型未见该日期；不回填旧概率。自然定时完成还要核对外部调度记录与run_id，人工工程验收永远单列。
