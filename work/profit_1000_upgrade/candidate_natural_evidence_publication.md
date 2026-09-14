# 持久研究发布证据的独立复核

`verify_published_evidence(*, evidence_commit, observer_run_id, github_client)` 要求外部明确给定证据 commit 与 observer run；不会把本地 JSON、普通 dict、调用者布尔值当作资格。

实际入口沿用原 `GitHubReadClient`：固定仓库、只读 GET、有限调用、20 秒 socket、零重试、令牌不随 CDN 重定向。验证固定 observer workflow 的路径、名称和 API 注册 ID，main、首次、成功、两项精确 job；再读取**同 run** 未过期 artifact 的原 `publication.json` ACK。ACK 必须指向外部 commit，capsule、context、原捕获结果及全部 body 哈希必须交叉一致。

归档只允许 rootless 或唯一 `dc20-candidate-observer-*` 根；先检查全成员及 manifest 索引，再读取被登记的 body。不允许额外未来结果文件、未登记哈希 body、目录别名、重复路径、符号链接、特殊文件或超预算归档。两个旧大 ZIP 不被保留，也不在跨日复核时重新请求。

GitHub API 的完整 Git tree 会重新计算树对象哈希。归档内原 bytes 必须等于证据 commit 中的原 blob 身份；必须只有一个 D 的 capsule/context 新增，旧文件不变；observer 代码、capture、writer、原发布验证器及其固定模型依赖在真实运行代码树中匹配。最后重复检查 run/job/artifact，核当前 main 仍保留证据原文件；这不是 Git 祖先关系证明。

独立时间约束：原冻结成功 job 完成 ≤ observer 保全 job 开始 ≤ context 本地时间 ≤ ACK 时间 ≤ observer 成功 job 完成 < T 09:25。ACK 还需保留原协调器 T 09:20 安全裕度。API `Z` 时间与原本地带时区 ISO 时间按各自原格式校验，不使用 Git 作者/提交者时间替代工作流完成时间。

## 私有证明对象

只有真实只读客户端的完整流程成功才返回 `VerifiedResearchPublication`。注入 fake client 只返回标明 `SYNTHETIC_OBSERVER_CHECK_ONLY` 的普通报告。该类私有构造、精确类型、不可变字节和独立发行 registry 防止普通 dict、子类、`object.__new__`、替换 `_raw` 或重置 `_guard` 冒充。

只读属性：`signal_date`、`snapshot_file_sha256`、`publication_observation_sha256`、`evidence_manifest_sha256`、`observer_run_id`（exact int）、`evidence_commit`。`report` 返回副本；`assert_unchanged()` 只检查内存 seal 和本地代码/身份，不联网，也不重新签发资格。统计消费者仍须独立检查其结算输入，发布证明不等于成交价或收益真值。

## 已知保留期边界

此实现解决原 P0 Pages artifact 默认 1 日到期后无法再次取得完整证据的问题：原观察响应、revision 字节和来源 Git 绑定已持久保存。它仍需要 observer 同 run ACK artifact（当前工作流设置保留 90 日）。过期或缺失必须拒绝重新签发，不能仅依赖自签摘要。

**永久离线证明尚未完成**；未来需独立可验证的工作流 attestation 等长期证明，不能因此放宽本版门槛。已有报告的本地完整性与“现在还能独立重新发行”的能力是两回事。

所有市场来源授权、自然收益结算授权、实际成交、实际容量、供应商 BAR_END 确认、正式替换和生产激活继续 false。本次测试全部合成，不是任何真实未来 D 已运行或已发布。
