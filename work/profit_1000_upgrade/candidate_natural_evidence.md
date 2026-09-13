# 自然研究发布证据即时保全

本层只在原 `candidate_natural_publication` 固定版本完整验证成功后保存证据。它不下载行情，不重新评分，不训练，也不修改正式名单、账本或订单。

## 接口与输出

`capture_evidence(output_root, *, freeze_run_id, token, transport=None)` 使用原只读 GitHub 客户端和原发布验证器。输出目录必须不存在、无路径别名且在代码目录外。成功返回 `signal_date`、`snapshot_file_sha256`、`publication_observation_sha256`、`manifest_path`、`manifest_sha256`、`files`（每项 path/sha256/bytes）、`output_root`。

目录只有 `manifest.json` 和 `bodies/<SHA256>.bin`。原 JSON HTTP 响应与原 Pages `revision.json` 字节按内容寻址去重；原验证器返回值另外绑定为观察记录。28 项原来源保留 Git blob 与 SHA 索引。两个原始 ZIP 只保存原 HTTP 长度、SHA 与真实 API artifact digest，**不保存整个 Pages 或冻结 ZIP**。

单文件最多 8 MiB，全包最多 64 MiB、64 文件；即时 HTTP 内存捕获最多 320 MiB，沿用原客户端 50 次 GET、20 秒 socket、零重试、受限重定向规则。GitHub 令牌仅送原 API 域；302 的 Location、签名 CDN URL、HTTP 请求头不保存。验证完成以前只在内存捕获；落盘前检查原文、JSON 解码与 Git blob base64 解码后的令牌以及凭证形态/签名链接。任意失败不给资格，不静默裁剪证据。

显式 `transport` 注入仅用于合成测试；即便完整原验证器路径通过，整个包仍有 `test_transport_injected=true`，后续发行器不得接受。CLI 没有传输覆写参数。

## 完整性与独立信任边界

`verify_local_evidence(root, *, expected_manifest_sha256)` 和纯函数 `verify_materials(manifest_raw, bodies, *, expected_manifest_sha256)` 只检查字节/索引/来源关联。前者还复查完整库存、路径、代码、文件身份和末端 SHA；陌生文件/目录在读取内容前拒绝。返回普通字典，不能伪装成自然前向资格。

原 Pages action 默认保留 1 日，所以必须在冻结成功后立即运行 capture，不能等 T+1 再无条件取原 Pages 包。本层持久文件免除了未来重新读取原 Pages ZIP 的需要，但**本地 JSON 或 Git 哈希本身不是独立网络观察证明**。之后必须绑定独立 observer 工作流真实 run/job、代码版本、原快照和 Git 证据 commit。该部分由单独的 `candidate_natural_evidence_publication` 实现；capture 永不签发其私有证明类型。

observer 后续发布全部是独立研究路径新增；context 在 capture 精确库存之外，由协调器另行绑定。需要原 observer artifact ACK 的验收，仍受该 artifact 保留期限制，不宣称永远可重新发行独立资格。单纯保存本地回执不能绕开过期或缺失的外部证明。

所有实际执行、真实容量、供应商 BAR_END 已确认、正式模型替换和生产激活许可保持 false。合成测试通过不等于真实未来 D 已发布。
