# 单个研究日状态包：追加式 Git 存储

本模块只运输上层明确交付的原字节，**不校验市场真值、不签自然前向资格、不生成收益、不修改正式名单**。原始来源、观察日志和 publication proof 由上层独立核验。

## 接口

`publish_new_journal(files, *, github_client, deadline=None, clock=UTCnow, pre_cas_guard=None)`

`files` 是 `path -> bytes` 的精确字典，只能包含同一个 `(D, asof)`：

```text
work/profit_1000_upgrade/candidate_natural_journal/D/asof/manifest.json
work/profit_1000_upgrade/candidate_natural_journal/D/asof/blobs/SHA256.bin
```

`D >= 20260914`、`asof >= D`，日期必须真实有效。2..1025 个文件，每个不超过 8 MiB，全部不超过 128 MiB；每个 blob 路径必须等于原字节 SHA256。manifest 的业务语义不由本发布器冒充核验，父协调器应先运行 journal 完整性验证。

`JournalGitWriter(token)` 仅接受父调用方从现有环境读取的 token，不自行查找凭证、没有 CLI token/URL 参数。固定 `njedu2023-prog/DC20` 与必要 Git API；最多 600 次请求、600 秒，20 秒 socket；不继承代理、不重试、不跟重定向。请求前保留 socket 时间余量；请求/响应有字节上限，JSON 禁止重复 key/非有限数。原文、JSON 转义及 blob base64 解码后的当前令牌均禁止出现在上传字节。

## 不覆盖与去重

先读取真实 main/ref、原 commit、完整递归 tree，逐目录重算 Git tree SHA。任何同一 D/asof 的已有文件、部分目录或孤立文件均拒绝，而不是修补或替换。

原 main 完整 tree 中已有的 blob 对象 SHA 可复用；本次相同 byte 对象只 POST 一次。根据去重后的真实新增对象数，在创建任何 blob 前检查剩余 API 预算。文件数上限不是承诺一次可上传 1025 个全部不同的新对象；若新对象超过 600 次总预算，拒绝，不擅自拆批或继续。

新 tree 必须仅增加这一个 D/asof 的登记文件与必要父目录，原全部文件/无关目录保持不变；新 commit 必须只有已核原 parent。提交前再次 fresh main，运行上层来源 guard、检查原封存 files 字节，再使用 `force:false` 更新 main。成功 ACK 前再执行来源 guard、payload seal、总时间检查。若 CAS 结果不确定、CAS 后 guard 失败或超时，只报失败；不重试、不删除可能已经发布的不可变记录，应另行只读核对。

返回普通 `JOURNAL_STORAGE_ACKNOWLEDGED` 字典：`signal_date`、`as_of_date`、`commit_sha`、`parent_sha`、`tree_sha`、全部 `files`（path/sha256/git_blob_sha1/bytes）、`created_blob_objects`、`reused_existing_blob_objects`、`unique_blob_objects`、`git_api_calls`、宿主 ACK 时间。发布器不写本地 ACK 文件，由父协调器保留原返回值。

所有来源资格、自然统计资格、生产激活、实际执行与旧文件修改声明均 false。本地合成 Git 测试不等于已经真实发布。
