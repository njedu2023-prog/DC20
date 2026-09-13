# 独立自然候选研究的事后价格观测

此包装只消费原冻全 N 文件和四席去重股票；不重算排序、拟合模型、联网、派单或写正式 ledger。
原 999666 固定模型 / 29 特征 / 45bp 往返费 / 单席 100,000 元 / 1% 竞价容量 / 10:00 涨停持有
出口均不改。实际入场、费用一次、持有和下一分钟出口全部调用原 `labels_v3.build_labels`。
原报告 `historical_counterfactual=true` 保留；任何本地 SHA、研究 codec 或本结果都不签发
自然发布时间、原始网络请求或正式模型替换资格。数值替换门槛仍 `NOT_CONFIGURED`。

## 最小调用

```sh
python -m work.profit_1000_upgrade.candidate_natural_outcomes \
  --snapshot /absolute/candidate_natural_forward/day_20260914.json \
  --snapshot-sha256 <外部已核对的原冻文件SHA256> \
  --output-root /absolute/research/candidate_natural_outcomes \
  --as-of-date 20260916 \
  --source-bundle /absolute/verified-outcome-sources.json
```

API 名为 `evaluate_natural_outcomes(snapshot_path, output_root, *, expected_snapshot_sha256,
as_of_date, source_bundle, expected_existing_ledger_sha256=None, clock=None)`。
CLI 没有时间覆盖。API 注入时钟仅测试/研究，结果显式 `INJECTED_TEST_CLOCK_RESEARCH_ONLY`。
as-of 是已收盘 SSE 交易日，必须不早于 D；当前 UTC 必须到该日北京时间 15:00，且不早于原冻表时间。
所有原来源路径必须绝对、无 symlink、regular 单硬链接；不从待验文件自行推导可信期望 SHA。

`source_bundle` 严格形状：

```json
{
  "calendar": {"origin_path": "/absolute/trade_cal_sse.csv", "sha256": "<原日历SHA>"},
  "by_code": {
    "600000.SH": {
      "source_root": "/absolute/one-stock-root/600000_SH",
      "bindings": [{"path": "data/market/raw/2026/20260915/daily.csv", "sha256": "<原bytesSHA>"}]
    }
  }
}
```

`by_code` 必须恰好是原 `candidate_slots` / `promotion_slots` 四席非空股票的去重并集，
不同股票使用不同 root；不多取其他候选结算数据。完整 0–10 候选/全排名仍从原冻表保留，
不能把逐股 singleton native report 当作 full N 的完整训练数据集。
0 候选用空 `by_code`，仍核对日历并显式输出四空槽，不调用拒绝空 cohort 的原内核。

## 单股来源文件

全局日历必须是原 `150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748`
字节，且 SHA 与原冻结 D 的 calendar 绑定一致。D/T/T+1 邻接、as-of 开市由原严格日历核验。
单股 `bindings` 仅允许以下路径；日期必须是日历中 T 至 as-of，全部路径先验证后才读取价格字节：

- `data/market/raw/YYYY/YYYYMMDD/daily.csv` 与同目录 `stk_limit.csv`：T 入场必需；
  T+1 起每个持有/待卖交易日按内核需要提供。只复制原字节，不裁剪股票行或改变价格。
- T 日 `data/research/canonical_auction_price_v3/YYYY/T/stk_auction.data.json` 与
  `stk_auction.meta.json`：真正单股原始 HTTP 响应由原 `auction_http_v3.source_bytes`
  编码，request 必须是 `auction_truth_v3.request_contract(T, code)`，精确 `{trade_date,ts_code}`。
  `load` 的 `requested_code` 必须等于该 root 股票；不接全市场回执，不重包历史 candidate-scope
  metadata，不拼接多股原始响应。缺失/损坏 pair 不可伪造 empty 或 entitlement 以获得 fallback。
- `research_inputs/minute_truth_0931/YYYY/YYYYMMDD/600000_SH.data.json` 与 `.meta.json`：
  日期限 T+1 至 as-of、股票精确相同。原 `minute_truth.source_bytes` 只接受真实已收盘请求的
  全部 240 行：09:31–11:30、13:01–15:00，保留原字段和值，不裁切/移位/补缺。
  请求为 `minute_truth.request_parameters(day, code)`；metadata 中请求与 HTTP body SHA 必须
  保留原含义。`BAR_END` 仍是研究假设，未获提供方确认。未来抓取时间也拒绝。

允许缺文件：仅列实际存在且外部已绑定的来源。缺失时临时 root 没有相应文件，原内核返回
准确 pending；不搜旧目录、不读未登记文件、不用日开盘替换缺失退出分钟。
有绑定但 bytes 不符则整个写入失败。绑定且格式损坏的 canonical/minute 仍保留原生 pending，
没有损坏来源 fallback。有效完整空响应/权限拒绝的日开盘代理、金额未知、NO_FILL 容量边界
全部由原资格内核判断。本组件不补非交易日/缺分钟的外部来源证明。

collector 是后续独立薄层：必须独立绑定实际请求、原响应 body SHA、采集时间和发布回执。
`network_request_performed=true` 是 codec 合同字段，不等于本组件见证网络请求；本组件无网络。

## 输出与不可替换

只写独立 `candidate_natural_outcomes/day_<D>.json` 的追加版本及 CAS 锁；目录不得与任何
单股输入 root 重叠，不能进入正式输出/数据/ledger。若放源码根内，只能在
`work/profit_1000_upgrade/candidate_natural_outcomes`。
传给内核的临时根只含已验证原来源副本、原日历与原冻表字节；临时目录用后清理。
manifest 特征仅 `board_stage` 数值、原 `promotion_rank`、`shadow_max_price=None`。
原 native `evidence_kind` 保守使用 `RETROSPECTIVE_D_ONLY_RECONSTRUCTION`：只是对已绑定原冻表
做 stage 投影，不用本地时钟或外部 SHA 自签 `NATURAL_PRE_BUY_FREEZE`。上层另标
`BOUND_PREBUY_FROZEN_SELECTION_STAGE_ONLY_PROJECTION`，真实冻结选择与真实 Git 自然发布资格分离。
完整特征和实际 rank/score 身份仍在原冻表及输出 `full_frozen_prediction`，没有重造源文件。

每个 `versions[]` 保留完整 `native_singleton_manifests` / `native_singleton_reports`、
`input_bindings` / `origin_bindings`、四席结果、as-of、模型/29 features/代码 SHA。
原 native 结果不改字段、不抹 `historical_counterfactual`。`cohort_complete` 只是该股票
singleton 原报告含义，不是全 N 或四席齐全声明。
空席 `MISSING_CANDIDATE` 保持 null；真实 NO_FILL 仅 `slot_net_return=0`、条件收益 null；
待定均 null；负分候选不跳过，已结算亏损照保留，费用不重复扣。

后续调用必须提供 `--existing-ledger-sha256 <此前外部保存的原ledger文件SHA>`。
同 as-of 只能内容一致的幂等重验，不改原观察时间（仅实际副本路径可不同）；有变化不可替换。
新 as-of 严格递增追加，原版本完整保留；旧 terminal 股票的原行/实际已消费来源 SHA 不可改变。
原 pending 可在新 as-of 有新证据后成熟，不能把旧 pending 版本重写成事后已知。
CAS 冲突、源/代码/冻表末验变化均无成功回执，不得仅凭留存文件宣称本次观察通过。
stdout 回执给出 ledger 原 bytes SHA；发布与自然样本入账审查由外部独立处理，所有 authority /
activation / actual execution 字段持续 false。

## 验证

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  work/profit_1000_upgrade/test_candidate_natural_outcomes.py -p no:cacheprovider
```

测试只使用合成 D/P0/时钟/价格、真实固定 999666 参数和未修改原生 codec/labels/退出内核。
没有读取 D>=20260914 的真实价格结果，也不是自然收益验证或完整实际来源回放。
