# 独立观察新模型研究冻表的真实发布

此模块只读 GitHub API，不写文件、不提交、不调用行情 API，不评分、拟合或结算。
只有明确指定的成功冻结运行，才能成为核验对象；不消费调用方自行构造的 publication JSON。

```sh
python -m work.profit_1000_upgrade.candidate_natural_publication --freeze-run-id <明确的运行ID>
```

CLI 使用专用环境变量 `DC20_CANDIDATE_GITHUB_TOKEN`，仅发送到 `api.github.com`。
唯一下载跳转沿用固定 importer：302 后另建不带 Authorization 的受限 HTTPS CDN 请求。
没有写 API、重试、市场数据采集、时间覆盖、替换模型、变更 D 或激活开关。
输出为 stdout JSON；异常只报固定拒绝信息，不输出令牌、签名 URL 或服务端错误内容。

API：`verify_publication(*, expected_freeze_run_id, github_client)`。
返回普通研究观察报告，不签发可供价格、结算、训练调用的 source authority。
只有精确原 `GitHubReadClient` 且从零调用开始，才可返回
`RESEARCH_PROSPECTIVE_PUBLICATION_OBSERVED`；任何注入的测试客户端都固定返回
`SYNTHETIC_PUBLICATION_CHECK_ONLY`，观察资格为 false。

## 闭合证据

1. 精确仓库、main、首次 attempt、成功结束、固定 workflow ID `357010624`、名称和路径。
   仅接受 workflow_run 或 workflow_dispatch；push 验证不属于自然冻结。
   独立读取两个真实 job：Validate fixed natural candidate contracts、
   Freeze original P0 bound new model research slots，要求均成功且前后有序。
2. 同 run 唯一 artifact `dc20-candidate-natural-<run>-1`，API 外部 digest、长度、归属和时钟。
   ZIP 仅允许无根前缀或唯一 `dc20-candidate-natural-…/` 前缀；只读 ACK、源回执、
   local freeze 回执、原 snapshot、可选空 CAS 锁及精确 28 源。多根、未知文件、
   未来行情路径、重复路径、链接/特殊文件、文件与目录冲突、超额与 CRC 错误均拒绝。
   所有源路径身份、日期和成员集合在读取源字节前完成验证；不 extractall。
3. ACK 指定的真实 Git commit/tree 与 API 原始四份日文件相符；不是把 JSON 重构后冒充原始 bytes。
   四份文件交叉绑定 snapshot/source/local/workflow SHA。父提交中不能已有任何一份；
   本提交只能新增四份文件及必要父目录，其他文件和目录不变。
4. 运行 head 和发布 commit 的 workflow、协调器、runner、importer、登记、模型、
   hash-lock 及实际依赖原 blob，全部与本地固定 SHA / Git blob SHA 相符。
   原 779baaff 模型文件和 999666 模型 canonical SHA 不变，只验证，不重新评分。
5. 独立再次读取原 P0 run、三个 job、Pages artifact 和其 revision，核对原 P0 commit/tree；
   用 ZIP 的 28 份原 bytes 与原 P0 Git tree 的 blob SHA/长度/模式及 receipt SHA 闭合。
   不需要再发 28 次 blob 请求，不读取最新页面，不把旧日期改为未来日期。
6. 原 P0 CAS 完成时间不晚于新预测；本次 freeze job 开始不晚于新预测。
   HOST 首次生成、冻结、全部本地验证完成、发布 ACK、成功 freeze job 完成依次有序。
   原 writer 的 T09:15 提交安全余量保留；独立 GitHub job 完成必须严格早于 T09:25。
   不用作者时间、提交者时间或 P0 的旧时间冒充新模型自然发布时间。
7. 尾部重新读取 workflow/run/jobs/artifact API 元数据，以及 main 当前 ref/完整 tree；
   验证四个原日文件仍是相同 blob/字节。此检查不是 commit 祖先证明，
   `git_ancestry_verified=false`，不调用旧客户端明确禁止的 `...` compare 路径。
   再验全部本地固定源文件和 import origins。GET 请求成本至多 40，旧客户端上限 50 不变；
   下载每次计 2 请求、单 socket 20 秒，不声称这是总运行时限。

## 必须在发布后立即核验

现冻结 P0 Pages 工作流没有覆盖 artifact 保留时间。它固定使用的
[upload-pages-artifact action.yml](https://raw.githubusercontent.com/actions/upload-pages-artifact/56afc609e74202658d3ffba0e8f6dda462b719fa/action.yml)
定义默认保留 **1 天**，并传给内部上传步骤。因此本组件是发布后立即/短期核验工具，
不能保证 T+1 或更晚结算时仍可重取原 Pages ZIP。`expired=false` 门槛不放宽；
过期、删除或无法取得原 ZIP 都必须阻塞，不能把源回执摘要冒充该原归档。
未来要跨日重新验收，须另行设计并授权持久化本次原观察及完整必要证据；本版本不实现，
也不改旧 P0、冻结工作流或保留配置。

## 不代表什么

即使真实研究发布时间被独立观察，也不是成交证明、收益验证、资本/NAV 验收、
完整训练数据许可、正式模型替换或下单授权。四个席位原样保留；负分不跳过，
空席不变为零收益。1 分钟 BAR_END 仍未被供应商确认。
`natural_outcome_admission_issued/source_authority_issued/production_activation_allowed/
formal_model_replacement_allowed/actual_execution_claimed/actual_capacity_verified` 均为 false。
原日文件的 false 声明不被改写；本报告仅新增独立的研究发布时间观察。

本次测试全部使用合成 Git、ZIP、P0、时钟和未来 D 结构；真实未来发布验收尚未执行。
旧正式榜、晋级模型、账本、来源与所有冻结文件不改。
