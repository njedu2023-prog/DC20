# 有界自然研究日常编排

`run_daily(snapshot_path, *, expected_snapshot_sha256, calendar_path,
expected_calendar_sha256, as_of_date, previous_collection_path=None,
expected_previous_collection_sha256=None, previous_outcomes_path=None,
expected_previous_outcomes_sha256=None, test_hooks=None)`。

只接同 D 原冻结文件、固定日历和外部 SHA 绑定的旧来源/账本。自然 CLI 仅 Linux，输出固定于
`/tmp/dc20-candidate-natural-state/<D>/<asof>/`，不提供目录覆盖参数。每步为新的
`collection_<step>/candidate_natural_outcome_sources` 和
`observation_<step>/candidate_natural_outcomes/day_<D>.json`。
Mac 或其他合成测试只能使用显式 transport/clock/monotonic/sleep/state_parent 全套 test_hooks，
结果 TEST_ONLY、publishable=false；不能用测试替代真实来源或前瞻样本。

每次最多8步、每步预留12调用、累计最多96调用。collector只取四席股票并集的最早缺证日；
原 source/HTTP bytes 不覆盖或重试。每步调用固定 5949 outcomes 原经济内核，不改模型或费用。
终态、尚未到期/无新来源、无效/空/失败来源停止。亏损、NO_FILL、pending 和无候选始终保持
原四席，不以缺失补零。

每步 outcomes 输出根均为新目录，且只 seed **本次进入时的旧 ledger**，绝不把上一中间
同 asof 版本作为追加前缀。中间 ledger 只提供给下一 collector 决定缺证位置；中间已经终态
的 rows/source_files 必须保持不变。最终 ledger = 原旧版本前缀 + 当前唯一 asof 版本。
如果外部旧账本已是同 asof，仅复验原证据并返回不采集/不发布，不修改同 asof 原记录。

正式 CLI 用新进程组监督子进程，300秒到即停止该组（含 collector 的子进程）。WNOWAIT
保留 leader 身份直到 group cleanup，避免 PID 重用误杀；从不广泛匹配或杀其他进程。
日志直接写独立普通文件，不用 PIPE/tee。TERM/KILL 清理最多另3秒；超时或清理不确定只保留
partial、supervisor.json，不签发可发布 daily_manifest。逃离新 session 的进程不在声明范围内。
父协调器不读取凭据，也不把凭据放入 argv；仅授权 collector 自己使用已有专用环境来源。

成功父进程返回 `final_collection_receipt_path/sha256`、`final_outcomes_path/sha256`、
`daily_manifest_path/sha256`、`publishable_file_bindings`。白名单仅最后 collection 原收据
列出的全部文件及 receipt.json、最终 day_D.json、daily manifest；不扫描/发布中间步骤。
manifest 不包含自己的 SHA，返回 envelope 另附 manifest binding，避免循环。
后续 Git journal 负责按原绝对路径恢复原字节。本模块不写 Git、前台、正式账本，不签发
publication/source/自然样本/成交/NAV 权限。真实行情请求及真实自然样本均未在本地测试执行。
