# 跨运行原始状态容器

这个模块只校验和运输字节、路径，不签发发布资格、行情来源资格、成交资格或生产激活。
只能保存每日协调器明确列出的最终 collection（全部原文件和 receipt）、最终 native
`day_D.json`、daily manifest；不扫描未知文件，不保存中间重放结果。

`build_journal(daily_result, publication_binding=..., previous_manifest_sha256=None)`
返回原始 manifest、外部 SHA256 和可交给 Git 发布器的精确 `files` 字典。publication
binding 仅引用四项：evidence commit、observer run ID、evidence manifest SHA、snapshot SHA。
真正的 private proof 校验仍由上层执行，普通存储引用绝不能替代它。

仅新增路径：`work/profit_1000_upgrade/candidate_natural_journal/D/asof/manifest.json`
及 `blobs/SHA256.bin`。自然运行固定 Linux `/tmp/dc20-candidate-natural-state/D/asof/`。
后续机器按外部 manifest SHA 和全部内容 SHA，恢复到相同原绝对路径，不重写来源 receipt
或 source bundle 内的路径、时间与请求证据。若该 asof 根已经存在，拒绝覆盖。部分失败
保留原文件，不能改名为已验证或自动删除重做。

`validate_journal(manifest_raw, bodies, expected_manifest_sha256=...)` 仅作字节完整性；
`restore_journal(...)` 独占创建原根，逐文件恢复后重新校验完整磁盘集合。原文、元数据、
外部对象别名、no-follow 文件句柄和首末路径都检查；JSON 重复键、非有限值、符号链接、
硬链接、路径越界、同日覆盖与缺失端点均拒绝。上限 1024 原文件、单文件 8 MiB、128 MiB
完整状态。内容相同可去重，但原始绑定仍全部保留。

测试只能显式传 `test_state_root`，同时 daily 必须 `test_only=true,publishable=false`；
自然输入必须 `test_only=false,publishable=true`。测试容器始终 TEST_ONLY，不能混入正式
自然运行。所有状态仅 `storage_integrity_only`，不会生成收益、模型预测或资格声明。
