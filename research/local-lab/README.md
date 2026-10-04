# DC20 本地量化研究台

这是独立本地数据库与可运行报告页，不是已经完成自动训练的盈利模型。官方DC20仓库、预测模型、账本和原始报告不变。

## 运行

网页和数据库使用 Python 3.10+ 标准库；模型训练另需 NumPy。现有夜间入口使用 Codex 绑定解释器。在DC20目录运行：

```sh
python3 research/local-lab/lab.py import-archive
python3 research/local-lab/lab.py serve
```

浏览器打开 http://127.0.0.1:8766 。服务只绑定本机；关闭进程/重启电脑后需再次运行。没有安装系统后台服务，也未改变既有自动化频率。

## 已实现

- SQLite数据库 `data/research.sqlite3`；原文件按SHA256保存在 `data/blobs/`。同路径内容改变保留新的版本，原始字节继续保留。
- 预测/结果/模型/审计只追加。SQLite触发器阻止常规UPDATE/DELETE；这不是对拥有文件写权限的管理员的防篡改保护。
- 历史结构化概率导入，保持不同预测版本独立；历史记录不倒签成系统建立后的事前证据。
- 正式同D盈1盈2及晋1晋2晋3，独立概率和全名单对照；页面明确为已归档的正式文件，非实时在线核验。
- 数据覆盖、缺失、预测依据、结果口径、模型状态、待补队列和审计记录；收件人号码不在页面展示。
- 新预测事前冻结接口、结果录入接口、显式更正链；无法买入/未退出/缺结果不计作已结算零收益。
- 按模型与真实成交/行情代理分别计算Brier、log loss、校准分桶、盈利/亏损/平盘比例及成本压力测试。样本不足不宣称校准有效。
- 本地只读HTTP界面、日期/版本/股票过滤、技术明细、打印布局和移动端布局。

## 尚未完成（页面据实显示）

完整历史资金与Level2、精细筹码、10:00成交证据自动回填、全历史报告规范迁移、合格数据驱动的训练/校准效果验证、经新样本验证的方法改进、组合资金净值、重启后无人值守验证。图表批量采集、归档日历结构校验、同群比较程序和维护入口备份已实现；不等于数据完整。不能因已建数据库宣称数据已齐全或概率更准确。

归档数据可得到的指标只是初始观测。历史行情缺少10:00成交证据时不从日线推断胜负。资料来源声明与客观可获得时间必须另外审计。网页刷新不会自动抓同花顺。

## 新预测冻结

`freeze` 输入JSON包括 signal_date、model（不可变版本号）、strategy（`T_AUCTION_T1_1000_NO_HOLD_V1`）、cost、entry_scenario、entry_at、exit_at、information_cutoff、latest_data_available_at、calendar_evidence、predictions。

每条predictions包括code、name、p（0–1或null）、reason、data_sources，可附metrics、missing。日期时间必须包含时区。entry_at为T日09:25，exit_at为T+1日10:00；实际冻结必须在entry_at以前，数据时间不能在冻结之后。新冻结必须附归档日历及源文件哈希，程序按会话序列验证T/T+1；操作者仍须核验源文件及日历内容真实性。

```sh
python3 research/local-lab/lab.py freeze /absolute/path/frozen-input.json
```

代码同模型同D重复文件幂等；内容实质变化必须使用新的模型/研究版本。每次历史档案导入不会覆盖已冻结行。历史报告校准需独立证据审计，本版默认排除。

## 实际结果与行情代理

```sh
python3 research/local-lab/lab.py outcome /absolute/path/outcome.json
```

输入prediction_id、basis（ACTUAL或QUOTE_PROXY）、status（SETTLED/UNFILLED/UNEXITED/MISSING）、evidence。SETTLED需entry_price、exit_price、entry_at、exit_at，必须与预测固定时间一致且已发生。实际订单不能恰好10:00成交的，不可随意改时间填入；需后续另建实际偏离策略记录，本版拒绝把其当固定策略校准。

结果更正须supersedes引用同预测同口径最新结果ID；旧行保留。行情代理并非真实成交，可成交性尚须人工证据检查。程序检查字段、时间、状态，不自动鉴定外部证据真实性。

## 评估、备份、验证

```sh
python3 research/local-lab/lab.py evaluate MODEL_ID --basis QUOTE_PROXY
python3 research/local-lab/lab.py backup /absolute/path/new-backup.sqlite3
python3 -m unittest discover -s research/local-lab/tests -v
```

数据库备份通过SQLite在线backup生成一致性快照；完整恢复还须备份data/blobs目录。不要仅复制运行中的主sqlite文件而遗漏WAL。

## 下一阶段的量化准入要求

1. 决策时点数据版本化，特征可获得时间不晚于预测冻结；复权/行业分类/停牌/涨跌停口径固定。
2. 预测样本与真实可执行结果逐条关联；记录未买入、未退出、价格偏差及缺失原因，报告筛选造成的样本偏差。
3. 基线、中性先验与候选模型在相同时间样本、成本、可成交约束下比较。按交易日分块，不随机打散相邻日期；清除跨训练/验证边界的重叠持有期。
4. 概率校准层只在训练/验证资料上拟合，保留最终未参与选择的测试区间。报告Brier/log loss、可靠性图、样本和交易日数、区间不确定性；不因小数更多宣称更准。
5. 收益和风险同时评估，实际与代理分开，费用与入场价敏感性、尾部亏损、覆盖率、不同市场阶段稳定性均纳入。
6. 模型先影子运行；晋升需预先固定验收规则与充分证据、版本记录及回退方案。本版没有自动晋升逻辑。

## 主动研究的数据审查
`data-catalog.json` 定义12组针对超短线盈利的取证问题和下一步动作，包括883900/883958、动态成分与亏损尾部。`context.py` 可审查观察包、分解隔夜高开与开盘后收益、在完整同日成分下计算广度。

```sh
python3 research/local-lab/lab.py research-context research/top2-nightly/data-audit-20260926/context-observations.json
```

输入为cutoff和observations。每条包含family、instrument、source、evidence、captured_at、as_of、available_at、status、values及quality_issues。status=VERIFIED仅为采集者声明，不自动验证来源真实性。日期未知可填null，审查会隔离；values可含last/open/previous_close/high/low（统一单位）。可选members每项code/return_pct（百分数），必须同时给expected_members及与as_of完全一致的membership_as_of，才计算完整广度。通过基础校验也不证明历史序列齐全或有预测价值。

隔离观察保留诊断计算，但predictive_metrics为空；不自动写入旧预测、不生成概率。审查结果及原始文件哈希进入SQLite，追加保存。当前只展示最新审查，明确独立于页面选定D；尚未完成每次预测与观察包的强制数据库关联。后续每次研究仍由Codex执行取证与正反证推理。该模块不是自动抓取器。

## 同花顺公开图表批量证据（2026-09-26实测）
`ths_evidence.py` 从同花顺公开页面图表端点只读获取日线/分钟价格，最多3路请求，保存原始JSONP、SHA256、来源和抓取时点。只用JSON解析，不执行响应脚本。传入精确D，正文日期不同即拒绝；输出目录含manifest时拒绝覆盖。

```sh
python3 research/local-lab/ths_evidence.py --day 20260924 --out /tmp/ths-new-run --codes bk_883900 bk_883958 hs_002909
```

每日研究应从已核验冻结的**全候选池**构造hs代码，并同时采集两强势群体及相关行业bk代码，禁止只采Top10。先读manifest失败原因并补证，再分析；不能因HTTP200就判数据有效。日期不符、空资金、缺分钟均不得用于结算。端点last仅为最近一日，不是历史回放接口。

当前实测数据：D20260924候选9只、强势指数2只、行业对照10只；33份数据包通过字段校验。分钟采用241点共同交易时段，排除午间13:00边界与15:05后记录；这不是交易所逐笔成交校验。日线数量级断点隔离，最新连续段不足窗口时返回null。成交量及资金字段尚未验证单位，因此暂不解释。

完整审查和逐股反证见 `research/top2-nightly/20260924/deep-review-20260926/report.md`。新证据单独存research_context，不回写旧预测。该采集能力已运行；定时任务仍需主动调用，不表示独立后台采集守护进程或自动训练已上线。

## 夜间结果证据采集与历史预备样本
- `python3 research/local-lab/result_capture.py --out <全新目录> --fetch`：从本地前瞻预测生成入场/退出取证任务，到期才采集准确日期分时。已有捕获复用，日期回退拒绝。只保存价格证据，不默认买入/卖出，不自动结算。
- `history_inventory.py --minute-root <归档根> --auction-root <竞价归档根> --out <全新目录>`：SHA审计和去重，检查唯一10:00；源元数据中的时间语义未确认会保留。
- `history_pairs.py <inventory.json> <trade_cal_sse.csv> <新输出.json>`：按前交易日形成探索性价格对，冲突/缺失排除；不加入预测性能表。

2026-09-26实跑：18项未来结果取证任务；历史1112组价格对、170个入场日，合格校准样本仍为0。新增资金和成分冲突审查见 `research/top2-nightly/20260924/active-followthrough-20260926/report.md`。


## 2026-09-26 闭环入口

先按 `research/top2-nightly/METHOD.md` 的最新闭环步骤执行。`operations.py --out <唯一目录> --fetch` 在既有夜间任务中执行备份、哈希回读及到期取证，不建立新定时任务。`prepare_research.py --pool <完整名单JSON> --calendar <日历JSON> --out <唯一目录> --model <新版本>` 只产生未评分草稿。

新freeze强制RESEARCH_FREEZE_V1：全池覆盖、已注册权重、六维缺失/有效状态、归档证据哈希与可获得时点、逐股反证和执行风险。草稿不可冻结；同一证据不可跨维度重复加分；官方排名不进入独立评分。旧记录保留，不倒填。

新契约结果的SETTLED状态要求执行审查；订单成交和可执行行情代理分列。报价本身不足以证明成交。`benchmark.py --model <版本> --basis ACTUAL或QUOTE_PROXY --out <唯一JSON>` 只做冻结政策下的同群比较，完整日缺失或未退出时不比较；概率并列分摊Top2边界权重。

当前真实状态与执行证据见 `../top2-nightly/20260924/closed-loop-20260926/report.md`。发送重试分类函数已单测，消息会话核验及发送仍由既有授权流程执行，维护程序不会自行发短信。

## 跨交易日学习底座

见 [LEARNING_FOUNDATION.md](LEARNING_FOUNDATION.md)。统一夜间入口 `./run-nightly.sh --out data/runs/<唯一目录> --fetch`。凭据改用 data/credentials/tushare.token（0600），不再读取钥匙串。

本轮主动工程与实测边界：[ACTIVE_ACCEPTANCE_20260927.md](ACTIVE_ACCEPTANCE_20260927.md)。数据覆盖状态可在本地页面查看；历史重建与真实前瞻训练保持隔离。


## 报价学习与主动研究追踪（2026-10-04）

`learning/quote_learning.py` 建立独立 QUOTE_RETURN 通道。只纳入原预测截止前保存的完整数值特征及原候选全池，连接已核验竞价与固定10:00报价。合格日期不足时保存缺口；未成交、缺失及尚未到期均不填零。原可执行条件概率、QUOTE_PROXY 与 ACTUAL 结果不改。

- 所有数据保留预测截止、原文哈希、归档时刻、结果可得时刻。按日期分割训练/校准/测试，剔除持有期重叠及晚到标签所影响的整日。
- 收益回归与独立概率校准已有实现，准入门槛保持 300 条、60/20/20 个日期；至少七天且新增五个日期才重训。更多数据不自动等于更好模型。
- 同日同池比较收益排序、等权池、5日动量；边界同分均分名额。成本 0.2%/0.45%/0.8%；尾部风险与缺失基线明确报告。QUOTE_RETURN 输出只代表报价研究，不证明可成交收益。
- `run_tracking` 将触发来源、run_id、维护、Agent 开始、处置与完成关联；恢复必须显式引用旧run_id。人工验收与定时触发分开，生产自动晋升关闭。
- 实际调查按 [ACTIVE_RESEARCH.md](ACTIVE_RESEARCH.md) 执行；生成任务或执行脚本不能代替 Codex 阅读证据与提出反证。

数据库、供应商原始数据、令牌、短信及本机运行报告只保留本地。仓库仅归档可复现源码、固定政策与测试。API认证使用本机权限受限凭据文件（参见 TUSHARE.md），不要提交数据或凭据。
