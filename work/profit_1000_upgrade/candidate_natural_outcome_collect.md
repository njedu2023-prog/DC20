# 自然候选四席：有限、只取原始来源的研究采集器

`candidate_natural_outcome_collect.py` 是新自然冻表的独立 source-only 工具。
它不修改旧历史 collector、旧 as-of 边界、冻结模型、原 labels v3、正式排名或账本。
凭证只能来自现有 `TUSHARE_TOKEN` 环境变量；没有 token、URL、时间、预算扩展、重试或交易 CLI。
本版本测试全部使用模拟 HTTP / 时间，不构成真实自然日前向样本或真实提供方可用性证明。

## 固定输入与最小接口

```python
collect_natural_outcome_sources(
    snapshot_path, output_root,
    expected_snapshot_sha256=external_raw_file_sha256,
    calendar_path=original_calendar_file,
    expected_calendar_sha256=fixed_calendar_sha256,
    as_of_date="YYYYMMDD",
    previous_collection_path=None,
    expected_previous_collection_sha256=None,
    previous_outcomes_path=None,
    expected_previous_outcomes_sha256=None,
)
```

`snapshot_path` 必须是完整的原 `candidate_natural_forward` 冻表，不是重新拼装的 Top2 manifest。
原始文件 SHA、原完整 N=0..10 身份/29 features/两套排名、四个槽位、999666 固定模型、
779baaff 原评估、登记、代码 pins、D/T/T+1、事前预测/冻结窗口均由原 outcomes 校验器核对。
固定 calendar 的原字节 SHA 必须是
`150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748`，
并与 D 冻表中的 calendar 来源绑定相同。D 不得早于 20260914；as-of 必须是 calendar
中的交易日、不得早于 D，且宿主 UTC 时钟显示对应北京时间 15:00 已过。

```sh
python -m work.profit_1000_upgrade.candidate_natural_outcome_collect \
  --snapshot /absolute/frozen/day_YYYYMMDD.json \
  --snapshot-sha256 EXTERNALLY_BOUND_RAW_SNAPSHOT_SHA256 \
  --calendar /absolute/original_trade_cal.csv \
  --calendar-sha256 150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748 \
  --as-of-date YYYYMMDD \
  --output-root /absolute/new_run/candidate_natural_outcome_sources
```

续采必须成对追加原路径和外部原文件 SHA：
`--previous-collection /absolute/old_run/candidate_natural_outcome_sources/receipt.json`
与 `--previous-collection-sha256 ...`；如已有研究观察，再提供
`--previous-outcomes /absolute/candidate_natural_outcomes/day_D.json`
与 `--previous-outcomes-sha256 ...`。后者必须同时提供前者。
这里的外部 SHA 是文件字节 SHA，不是 JSON 内部 canonical seal。

Python API 的 `transport/clock/monotonic/sleep` 仅用于测试；任一注入都要求同时注入
transport，不能以测试时钟落入真实网络。注入 receipt 明示 TEST / network=false；
原 native codec 所需的内部布尔形状不能被提升为真实 HTTP 或自然入账证明。
真实模式拒绝 TEST 冻表；旧 collection/history/观察的 TEST 与真实模式不能混接或改标。

## 每次只推进最早缺证的一天

- 范围仅原 candidate Top1/Top2 与 promotion Top1/Top2 的去重股票；负分仍保留，
  空槽不消失，N=0 不发请求。最多 4 股，每股最多 1 个交易日、3 个精确接口，即最多 12 次计划请求。
- T 未到期：0 请求。首次即使 as-of 很晚，也只请求 T 的 `daily` / `stk_limit` /
  单股 `stk_auction`；不会直接遍历 T..as-of。
- T 来源已齐但没有原生研究观察：标为 `REQUIRES_OUTCOME_VALIDATION`，先运行原 outcomes。
- 已有可信观察：优先沿原 native `missing_evidence_date` 请求最早缺证日；
  T+1 及后续出场证据为同日 `daily` / `stk_limit` / 单股 `stk_mins`。
- 已验证终态：`TERMINAL_ORIGINAL_EVIDENCE_REUSED`，不再请求。
  原 native 持续封板/不可卖等未决状态只推进到旧 as-of 后第一个到期交易日；
  本工具不判断价格/买卖条件、不签停牌或 nontrading 来源资格。
- 每个已尝试的 `(api, trade_date, ts_code)` 永不自动重请求。旧有效、空响应、无效响应、
  传输失败都留在 `request_history`，原文件不覆盖。
  有 source 则复用，无 source 则 `PRIOR_INVALID_OR_EMPTY_ATTEMPT_PRESERVED_PENDING`。
  凭证缺失和预算未发出的计划项是 api_calls=0，不伪称已尝试，可由后续有预算运行补采。

多日封板、旧无效/空证据或预算不足不会删除槽位，不会产生 0 回报或 fallback。
若需要纠正此前失败的实际提供方证据，应另外获得明确版本/治理决定；本实现无覆盖或重试开关。

## 来源文件与原始响应

每次输出目录必须全新、basename 精确为 `candidate_natural_outcome_sources`，且与输入隔离。
若输出在代码仓库内，只允许 `work/profit_1000_upgrade/candidate_natural_outcome_sources`；
推荐各次独立 artifact 目录。写入为 EXCL / no symlink / fsync，不写正式 ledger。

```text
candidate_natural_outcome_sources/
  receipt.json
  source_bundle.json
  sources/CODE_EX/
    data/market/raw/YYYY/YYYYMMDD/daily.csv
    data/market/raw/YYYY/YYYYMMDD/stk_limit.csv
    data/research/canonical_auction_price_v3/YYYY/T/stk_auction.data.json
    data/research/canonical_auction_price_v3/YYYY/T/stk_auction.meta.json
    research_inputs/minute_truth_0931/YYYY/YYYYMMDD/CODE_EX.data.json
    research_inputs/minute_truth_0931/YYYY/YYYYMMDD/CODE_EX.meta.json
  http/CODE_EX/YYYYMMDD/API.response.json
  http/CODE_EX/YYYYMMDD/API.receipt.json
```

`source_bundle.json` 精确复用原 outcomes 输入形状：
`calendar.{origin_path,sha256}` + `by_code[ts_code].{source_root,bindings:[{path,sha256}]}`。
每股 root 独立，因此 T auction 的原 request 是精确 `{trade_date,ts_code}`；
不将全市场或混股响应改壳为单股。分钟原请求是 1min / 09:31:00..15:00:00；
不挪 09:30、不扩大字段或补造 bars。native codec 是否接受响应由原代码决定。

`http/*.response.json` 是通过敏感内容检查的原 HTTP body **逐字节原样保存**。
auction/minute 的 source pair 直接使用冻结原 codec，未重写源响应。
daily/limit CSV 则明确是原响应 scalar table 的可审计序列化投影，**不是上游原 CSV**：
保留响应字段顺序、用 Decimal 保留数字精度，不缩放单位、不修价、不补行。
原 `stk_limit.pre_close=null` 只序列化为空 cell，其他所需数值不得缺失；空表不产生 CSV。
raw receipt 同时绑定精确请求、原 HTTP SHA/长度、CSV/native source SHA，
`csv_origin=ORIGINAL_JSON_SCALAR_TABLE_PROJECTION_NOT_UPSTREAM_CSV`。

response 必须明确完整、单股/单日身份一致，未知 envelope、分页、不明 detail 或非有限数值
不被升级为有效 CSV。安全但无效的原响应可保存为 HTTP sidecar；
含凭证/转义凭证/敏感 key、重复 JSON key、坏 JSON、过大响应不保存原 body，只保留固定失败状态
以及在安全长度内已取得的 body SHA/长度。提供方错误正文和 token 都不输出到 stdout/stderr。

## 预算、续采与验收边界

仅固定 `https://api.tushare.pro` POST；不继承环境 proxy、不跟 redirect，0 retry，单 worker。
硬上限 128 requests / 300 秒 / 每响应 1,000,000 bytes / 新响应总计 64,000,000 bytes；
本四席计划进一步收紧到最多 12 requests。读取最多多 1 byte 仅检测超限，绝不接受或落盘该超限 body。
单次 parent-process deadline 为 20 秒，异常子进程 terminate/kill；请求开始留 21 秒余量，
相邻请求起点至少间隔 0.5 秒。预算不可经 CLI 扩张。整个本地最终 guard 超时也拒绝成功返回。

旧 receipt 的外部 SHA、seal、collector/outcomes pin、D/T/T+1、全 N/union、calendar、
所有请求日期和全部文件路径先核完，才读取旧价格/HTTP body；再核每个字节 SHA、原 raw/source
交叉引用和 per-HTTP receipt。所有有效旧 body 逐字节复制到新 root，旧 root 不动。
跨 workflow run 可将经 Git 原文件 SHA 核验的上一份 collection 恢复到其 receipt 原绝对路径，
再传入原 receipt 字节 SHA；不依赖原 inode，但禁止改写 receipt 中路径或搬迁后重封。
例如 `/tmp/dc20-candidate-natural-state/D/asof/collection_step/candidate_natural_outcome_sources`。
新 step 使用另一个全新根；原路径已被别物占用或含 links 时 fail closed。
同一 as-of 内多次推进可各用新的临时 outcomes 根作 native 观察，不覆盖同 as-of ledger；
最终发布仍由上层对正式研究旧 ledger CAS 追加一版，本组件不负责发布。
既有原 native 终态及其消耗的 source bindings 必须匹配原 freeze/source 证据，不能用伪终态跳过采集。
所有 caller 源在末端再做 identity-before-read + SHA 检查，登记/源码/凭证环境和落盘文件均重验。

receipt 主要读取路径：`slot_union_codes`、`full_frozen_candidate_count`、`stock_plan[]`、
`requests[]`（本次计划）/ `request_history[]`（累计已尝试）、`api_calls`、`response_bytes`、
`source_bundle`、`output_file_bindings`、`budget`、外部 prior SHA 链。
`source_processing_completed_at_utc` 只覆盖来源处理，不谎称已覆盖随后自己的落盘；
返回前还检查最终字节、最终墙钟/单调时钟与总预算。
若失败，可能留下未发布的新目录；不可仅看到 receipt 文件就宣称成功，必须同时有成功进程结果及外部文件 SHA。

成功只表示 `SOURCE_COLLECTION_RESEARCH_ONLY`，不等于每股证据齐备或可成交。
`source_authority_issued`、`natural_forward_admission_issued`、`git_publication_verified`、
`production_activation_allowed`、`actual_capacity_verified`、`provider_timestamp_semantics_confirmed`
仍全部 false。下一层应以 receipt/source_bundle 原文件 SHA 调用固定 outcomes，保留其
`historical_counterfactual=True` native 返回，再独立处理已授权 Git 发布回执和自然观察资格。
无训练、无下单、无购买服务、无模型替换授权；BAR_END 仍未获提供方明确确认。
