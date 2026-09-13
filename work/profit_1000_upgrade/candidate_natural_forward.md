# 固定 Ridge 自然日研究冻结入口

仅新增研究 sidecar；不改正式排序、正式 ledger、旧 D、模型、费用或资格规则。
`candidate_natural_forward_registration.json` 是固定登记，不是模型替换授权。
数值化的模型替换阈值为 `NOT_CONFIGURED`，`production_activation_allowed=false`。

## 输入与运行

使用仓库根作为 Python 工作目录，不需要训练环境或网络访问。CLI 只读取本机当前 UTC，
没有时间、模型 SHA、注册文件或激活覆盖参数。全部路径必须绝对且不含 symlink。

```sh
python -m work.profit_1000_upgrade.candidate_natural_forward \
  --source-root /absolute/isolated-source \
  --output-root /absolute/research/candidate_natural_forward \
  --model-evaluation /absolute/fixed/candidate_development_evaluation.json \
  --signal-date 20260914 \
  --p0-hashes /absolute/import-receipt/exact-four-p0-sha256.json
```

`--p0-hashes` JSON 必须仅包含 `receipt`、`runtime_features`、`three_rank_json`、
`three_rank_csv` 四角色的原文件 SHA256。由外部已核对的 P0 导入回执提取，不是 runner
自行扫描/猜测或从当前文件自签。可选 `--source-path-map /absolute/map.json` 指向已登记的
原始 blob（原逻辑相对路径→实际绝对路径）；不能增加接口或重新映射四份 P0。
导入方的 `imported=true`、旧 P0 发布时间、SHA 相等均不自动升级成新候选自然资格。

模型参数只能来自 SHA256
`779baaffb008165e88a88f568497d42b410dcbec03e9f613b099464d245a4872`
的实际 evaluation 原字节；其中 canonical model SHA256 必须为
`999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589`。
不接受重新封装、替代模型或重新拟合。部署方可放在任意独立位置，但内容必须完全相同。

D 必须由原来源日历确认开市且 `D>=20260914`，原日历给出 T/T+1。
首次打分/保存只能在北京时间 D 15:00 至 **严格早于 T 09:25**；后者是本研究入口，
不修改任何旧正式冻结截止。晚到、观测窗不完整、错误原始来源均拒绝，无降级输入。
四个历史缺失信号仍是 `None`；不能把当天已有的晋级概率/路径回填固定模型。

## 冻表与幂等

输出目录 basename 必须为 `candidate_natural_forward`。若位于源码或输入根内，唯一允许
相对位置是 `work/profit_1000_upgrade/candidate_natural_forward`，不能进入正式输出、
数据或模型目录。只操作请求 D 的 `day_<D>.json` 和其 CAS 锁，不扫描其他日期。

日文件保留 `D_source_evidence` 全部实际输入绑定与投影，`prediction.rows` 全 N 排名
（原完整池核验后最多 10 候选），两组独立 `candidate_slots` / `promotion_slots` 各 2 席。
负分仍保留；0/1 候选时缺席显式 `MISSING_CANDIDATE`，所有未到期收益和 fill 均为 `null`，
不是零收益。入场 `research_canonical_price_capacity_split_no_cap_v3`、出口 10:00/涨停持有
规则、45bp 往返费、100,000 元单席和 1% 竞价参与率保持固定。没有执行/容量已验证声明。

`prediction_generated_at_utc` / `pre_cas_freeze_at_utc` 是新预测/保存前时钟，
绝不挪用 P0 发布时点。保存后 stdout 返回 `dc20_candidate_natural_local_freeze_receipt_v1`：
包含 `snapshot_file_sha256`（原 bytes）、`snapshot_sha256`（内容封印）和
`local_operation_completed_at_utc`。API 注入 clock 仅测试/研究，日文件和回执标
`INJECTED_TEST_CLOCK_RESEARCH_ONLY`，不能作为自然时间证据；默认 CLI 标 `HOST_SYSTEM_UTC`。

同 D 不可替换。重验必须加 `--existing-snapshot-sha256 <此前外部保存的原文件SHA>`；
重新核模型、全部输入/参数/代码及同一来源路径，完全相同才幂等返回
`EXISTING_IDENTICAL_SNAPSHOT_REVALIDATED_NO_NEW_ADMISSION`，不重写旧时间。
来源位置/字节/模型/注册/代码变化或并发 CAS 冲突均拒绝替换。不要从待验文件本身推导
“此前可信 SHA”。新 runner 发布版本也不能覆盖既有 D。

若 CAS 写入后跨过 T 09:25，文件可留存审计，但回执为
`BLOCKED_POST_CAS_DEADLINE_SNAPSHOT_NOT_ADMITTED` 且 CLI 非零退出；写后 guard/时钟错误
同样无成功回执。保留下来的日文件不得仅凭保存前时钟、后续幂等返回或自封摘要补发资格。

## Workflow 发布边界与后续

调用方应固定 runner/注册/模型/来源代码 SHA，并核实实际源回执和 P0 Git commit/tree/运行时序；
runner 只核内容闭环。保存 stdout 回执，先要求首次 `LOCAL_RESEARCH_SNAPSHOT_FROZEN`、
`HOST_SYSTEM_UTC`、`local_freeze_completed_before_cutoff=true`，再发布**原字节**日文件与回执。
独立发布记录必须绑定 D/T/T+1、两文件 SHA、实际 Git commit/tree/path、workflow run/job、
生成及真正发布时序，确认真实发布也早于 T 09:25。P0 的旧发布回执不能替代本次新预测发布。
发布/CAS/远端复核失败不记自然观察样本；本模块始终保留 `git_publication_verified=false`、
`source_authority_issued=false`、`natural_forward_admission_issued=false`，不伪造外部证明。

此入口只冻结研究选择，尚不采集 T/T+1、不写研究结算或正式 ledger、不训练/联网/派单。
后续应另行只消费已独立认可的原冻全 N 身份与四席，用固定 research codec、入口/出口规则
和真实 T/T+1 来源结算；缺失/坏来源保持 pending，不将旧研究 codec 强转正式来源资格。
它不能证明收益提升，也不启用正式盈利排序替换。

## 专测

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  work/profit_1000_upgrade/candidate_natural_forward_test.py -p no:cacheprovider
```

默认使用真实 999666 参数、合成 P0 与显式测试时钟；包装 evaluation 的测试 SHA 只在 fixture
内替换，不是运行时能力。`DC20_RUN_FIXED_NATURAL_MODEL_SMOKE=1` 另启用本机现有 779 原文件
的真实数学打分测试，仍是合成 D 来源/测试时钟，不是自然前瞻记录、完整来源回放或 refit。
