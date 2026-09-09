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

## 当前一步：独立输入与自然时段只读验收

`accept_forward_inputs.yml` 是独立的**只读输入验收**，不是新的每日榜单生产入口。
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
- 结果仅保存为 Actions artifact。末尾的 `receipt.json` 才表示本次输入验收完成；
  晚到保持晚到，截止时间后不写成功回执，缺 exact-D 文件明确失败。
- `INPUTS_VALIDATED_NOT_PRODUCTION` **不等于**模型已计算、榜单已发布、
  Shadow 已记录或新统计已启用。模型输入适配与数值等价仍需下一步验收。

当前只读入口要求 `production_enabled=false`。历史固定提交取数可以用于检验
适配兼容性，但不得写入前向账本，也不得被称为首次自然 schedule 成功。

## 切换前还必须完成

1. 验收新只读入口的首次自然取数，再将独立输入包适配到已验证的独立推理入口；当前重算入口只做 REPLAY。
   晋级 D 名单必须先独立可发布；盈利、真值、统计失败不能挡住真实晋级名单。
2. 保持 exact-D 来源 SHA、候选门禁、模型字节及排序数值等价；新阶段的逐日账本还需接入累计指标与前台读取。
3. 新生产单写入入口、GitHub 原子发布和共享 writer 锁、准确 Pages revision。
4. 按既有北京时间 21:15 生成 / 21:35 验收窗口配置可靠触发与独立兜底。
   GitHub cron 本身没有准点保证；晚到必须显式标记，不能用改时间戳掩盖。
5. 新 T/T+1 真值适配和自然日运行，验证费用、企业行为、停牌/跌停退出、缺数补验。
6. 首个自然 D→T→T+1 完整周期通过后再激活生产切换；统计起点选择切换后的首个合格 D。
7. 新链路接管后停用旧写入/旧展示；保留归档回退，不物理删除已训练资产。

新代码通过单元测试或预览上线，均不等于以上生产验收已完成。
