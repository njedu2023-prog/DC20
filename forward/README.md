# DC20 新系统：迁移验收阶段

这是用户授权的“保留模型、抛开旧统计、只向前运行”重建。
**尚未启用生产；已进入独立推理与逐日账本验收，不是已接管的每日生产链。**

## 已落实的边界

- `config.json` 默认未启用、未设新统计起点；旧统计导入和正式交易动作明确禁用。
- `promotion.py` 从 exact-D 候选、行情及固定历史特征重新运行保留的晋级模型；不读取旧榜单。
- `profit.py` 随后独立运行保留的盈利研究模型，成员必须与晋级榜完全一致，不修改晋级名次。
- `daybook.py` 按 D 分开冻结晋级、追加盈利和真值；晋级前三直接记录，盈利生成时将前二一起记录。盈利或旧账本坏数据不阻断新 D 的晋级冻结。
- `ledger.py` 保留为第一阶段完整包兼容测试模块，不再作为未来 P0 写入入口。
- `settlement.py` 区分未到期、缺真值、未买入、退出受阻与扣费结算，保持 D/T/T+1，缺值不当零。
- `metrics.py` 只读取新纪元账本；晋级 Top1/2/3 及前三组合，盈利 Top1/2 及前二组合分别统计。
- `daybook_metrics.py` 是新逐日 v2 账本的独立累计读取入口；不要求缺失的盈利榜补成空榜，辅助数据损坏明确降低覆盖率，不阻断晋级冻结计数。
- `schedule.py` 使用仓库已提交 SSE 日历；时间带明确，延迟跨午夜仍绑定原计划 D，休市日不监管。
- `storage.py` 提供本地并发锁与内容 SHA 比较交换，拒绝覆盖已变化账本。
- `web/` 只保留晋级、盈利、每日验证、新账本累计四块；公司名称后内联标记 Top 席位。

## 保留与备份

基线：`15423ced302ab19b17f459fb3006e0af2b764f24`。
备份分支：`archive/pre-rebuild-20260909-15423ced`。
本阶段不改模型、旧训练/特征依赖、旧页面或生产 workflow；
只增加隔离新模块、只读测试和明确标注的预览。
兼容性回归中另修正一个旧测试的夹具：显式模拟缺行情，而不是假设某历史日
文件永远不存在；没有修改生产真值规则或行情文件。
旧统计仍在归档和旧系统中，但**不会迁入新账本**。
`model_inventory.json` 记录迁移需保持字节一致的资产和依赖。

## 本地验收

从仓库根目录，使用锁文件规定的 Python 环境：

```bash
python -m pytest forward/tests -q
node --check forward/web/app.js
node forward/tests/test_web.cjs
python -m forward.cli --root . preview --signal-date 20260908 --revision 15423ced302ab19b17f459fb3006e0af2b764f24 --output /absolute/resolved/preview-path
```

预览命令只是读已有 exact-D 冻结产物进行迁移比较，**不重新推理、不倒填
历史 Shadow、不证明自然定时成功**。REPLAY 明确禁止进入生产账本。
公开预览使用 `outputs/decision/forward_preview/` 独立入口；首页暂不替换。
预览下载的 `ledger.json` 应当 `days=[]`，`activated_at_utc=null`。
这里的价格验证是研究用日开盘代理，并非真实成交；未核对企业行为不结算持仓收益。

真实模型重算使用独立入口，要求真实 Git checkout、冻结依赖未漂移，输出目录在仓库之外：

```bash
PYTHONPATH=.:src python -m forward.rehearsal --root . promotion --signal-date 20260908 --output /absolute/fresh-primary
PYTHONPATH=.:src python -m forward.rehearsal --root . profit --primary /absolute/fresh-primary --output /absolute/fresh-profit
```

这是历史输入的**重新推理验收**，而不是读取原榜单冒充计算；两条命令仍明确标注
REPLAY，不计入新统计。独立 CI 将晋级产物先保存为 artifact，再启动盈利任务。
此 artifact 不是线上 D 名单发布证明，公开预览也仍然是第一阶段的旧产物对照视图。
`replay_inputs/` 只保存基线提交已经存在的 D0908 来源元数据原字节，供固定回归使用；
不是补造名单或补记前向记录。该回归不读取会随下一交易日改变的 `latest` 元数据。
其他日期必须先具备明确的同日来源合同，不能把 D0908 的固定演练入口当成自动生产。

## 独立输入、模型适配与自然时段只读验收

`accept_forward_inputs.yml` 是独立的**只读取数及推理验收**，不是每日榜单生产发布入口。
它在每个工作日北京时间 21:15 / 22:15 使用各自独立的 cron 身份运行，无手动
dispatch 入口；不依赖盈利、旧 Action、旧统计或生产 writer，不发布 Pages。

- `input_acceptance.py` 先核验 GitHub run API 的仓库、分支、workflow、HEAD、
  首次 attempt 与真实 `created_at`；`trigger.py` 再按原时段绑定 D/T/T+1。
  休市退出，不读取两个上游。GitHub API 仅提供创建时间，无法证明极端整周
  排队后的原 cron occurrence；当前门禁不把这个可观测性边界冒充准时保证。
- 仅在原槽和本地固定 SHA 的 SSE 日历通过后，各读取一次两个上游 main ref，
  固定为不可变 commit。所有候选和行情请求都使用该 commit 的严格日期路径，
  不读 `latest`，不触发或重跑上游，也不写入旧 `data/`。
- `inputs.py` 采集同 D 候选、当 D 及前 20 个交易日的必需行情表和声明成功的
  路径相关可选表，逐文件保存原字节 SHA256、日期、行数及来源证据。静态
  `stock_basic` 明确标注非日期字段表；缺失可选数据不伪装为齐全。
- 取数结果先独立保存为 Actions artifact。末尾的 `receipt.json` 才表示本次输入验收完成；
  晚到保持晚到，截止时间后不写成功回执，缺 exact-D 文件明确失败。
- `INPUTS_VALIDATED_NOT_PRODUCTION` **不等于**模型已计算、榜单已发布、
  Shadow 已记录或新统计已启用。

输入通过后，`bundle.py` 按调用者提供的外部 manifest SHA 重新核验文件、日期、
来源与 CSV 内容，再把原字节固定在内存中。`compute_promotion_from_inputs`
只从该包读取候选与行情；不读旧 raw/pred/输出，不伪造 `_sync_meta` 或 Git 绑定。
保留模型及训练所需历史特征仍从原固定 SHA 资产读取，**不重训、不改特征数学**。
分钟数据未列入当前输入包，明确作为缺失，不到旧目录寻找替代。

`bundle_rehearsal.py` 先输出并保存晋级及路径，再独立计算盈利。P1 使用外部
P0 回执 SHA，并核验同源成员，不改变晋级顺序。自然工作流三段单向依赖，
不同 run 不串行等待旧 P1；休市不会安装 ML 或运行模型。推理开始、完成、写完
数据文件后都复核原时段，只有最后回执才表示本段成功。盈利失败不删除已存的 P0。

即使来自自然 schedule，当前结果仍是 `NATURAL_SCHEDULE_STAGING` 来源下的
`REPLAY` 推理验收，不能写入前向账本。它不证明公开名单已发布、真实首次自然
运行已成功或新统计已激活。固定历史输入的 CI 会在模型计算后单独比较原冻结结果。

从可信输入回执取得 manifest 原字节 SHA 后，可在隔离目录运行：

```bash
python -m forward.bundle_rehearsal --root . promotion --bundle /absolute/input-bundle --manifest-sha256 <trusted-manifest-sha256> --output /absolute/new-primary
python -m forward.bundle_rehearsal --root . profit --primary /absolute/new-primary --primary-receipt-sha256 <trusted-primary-receipt-sha256> --output /absolute/new-profit
```

自然工作流额外传入同 run 的取数回执及其外部 SHA；不能省略后降级为历史模式。
上述 CLI 与自然工作流均不发布页面、不生成订单或 Action、不迁移旧累计统计。

当前只读入口要求 `production_enabled=false`。历史固定提交取数可以用于检验
适配兼容性，但不得写入前向账本，也不得被称为首次自然 schedule 成功。

## 发布候选与新账本统计接口（尚未接管生产）

`release_evidence.py` 从外部 job-output SHA 验真 P0/P1 回执和相应原字节，核验
同 D/T/T+1、当前代码 revision、模型及完整成员绑定。P1 还必须绑定那一份 P0
回执，不能靠同日期或名称拼接。自然结果继续核对同一次 run 和原时段；历史
复算始终保持 REPLAY。

`release_candidate.py` 把已验真结果整理为独立、可下载的 JSON 发布候选包：

- 晋级候选只读 P0，包含全部真实成员、路径和 `promotion_top3`。
- 盈利候选在原晋级成员内按盈利名次展示，自动包含有公司名称和 D/T/T+1 的
  `shadow_top2`；不足两支不补票。
- 所有席位明确为 `AWAITING_NEW_EPOCH_NOT_RECORDED`，不是已记录的 Shadow。
  `ledger_written=false`、`publication_verified=false`、`new_forward_days=0`。
- 候选数据先写，完成回执最后写；自然截止跨越或 SHA 冲突不能留下成功回执。
  两个候选 job 都在各自推理之后独立运行；推理不反向依赖候选包或累计统计。

```bash
python -m forward.release_candidate --root . promotion --primary /absolute/primary --primary-receipt-sha256 <trusted-primary-receipt-sha256> --output /absolute/new-promotion-candidate
python -m forward.release_candidate --root . profit --primary /absolute/primary --primary-receipt-sha256 <trusted-primary-receipt-sha256> --profit /absolute/profit --profit-receipt-sha256 <trusted-profit-receipt-sha256> --output /absolute/new-profit-candidate
```

这些入口没有 Git/Pages 写权限，也不把候选席位转换成 NATURAL、冻结账本或启用
统计纪元。未来发布器必须另外验收真实自然运行、前瞻准入、远端 CAS、共享
writer 和公开 revision；当前成功 artifact 不能替代这些证据。

`daybook_metrics.statistics_from_daybook` 直接验证 v2 晋级、盈利和真值分文件，
晋级 Top1/2/3 与盈利 Top1/2 的累计口径各自独立。它不绕回要求“两榜齐全”的
v1 账本；缺盈利、缺真值、损坏或不同费用口径会明确说明，不把未知当作零收益。
本阶段仅用合成测试账本验证此接口，不迁入旧统计或把历史复算写成新前向样本。

## 切换前还必须完成

1. 验收独立取数→晋级→盈利的首次真实自然运行；当前全部推理入口仍只做 REPLAY/staging。
   晋级 D 名单必须先独立可发布；盈利、真值、统计失败不能挡住真实晋级名单。
2. 保持 exact-D 来源 SHA、候选门禁、模型字节及排序数值等价；逐日统计与发布候选接口已备妥，仍需接入经前瞻准入的新纪元与前台读取。
3. 新生产单写入入口、GitHub 原子发布和共享 writer 锁、准确 Pages revision。
4. 按既有北京时间 21:15 生成 / 21:35 验收窗口配置可靠触发与独立兜底。
   GitHub cron 本身没有准点保证；晚到必须显式标记，不能用改时间戳掩盖。
5. 新 T/T+1 真值适配和自然日运行，验证费用、企业行为、停牌/跌停退出、缺数补验。
6. 首个自然 D→T→T+1 完整周期通过后再激活生产切换；统计起点选择切换后的首个合格 D。
7. 新链路接管后停用旧写入/旧展示；保留归档回退，不物理删除已训练资产。

新代码通过单元测试或预览上线，均不等于以上生产验收已完成。
