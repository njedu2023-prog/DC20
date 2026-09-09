# DC20 forward rebuild — internal interface v1

This is an isolated replacement candidate, not an activated production system.
No old statistical totals or old daily selections are imported into its ledger.
Keep all trained model bytes, preprocessing, calibration, feature definitions,
strict SSE calendar and necessary market history unchanged.

## Package

Python standard-library `forward/` package. Public JSON uses decimal returns:
0.01 = 1%. All timestamps must be timezone-aware. No orders or real Action.

## Phase 2 independent path (production still inactive)

`promotion.compute_promotion_bundle(root, D, generated_at_utc=..., generation_mode='REPLAY')`
recomputes the frozen promotion model from exact-D candidate/market data and
hash-bound committed feature history. It does not read existing daily rankings,
profit projections, Action or performance statistics. The returned bundle has a
promotion-only `day`, complete hard-range `runtime_rows`/`runtime_columns`,
`runtime_sha256` and `feature_snapshot_sha256`. The historical inference is not
a newly admitted forward selection. Existing pure feature code is retained for
numerical compatibility; complete separation from that source package is not
claimed.

`profit.infer_profit(root, promotion_bundle)` independently recomputes the
existing research heads and ranks the exact frozen promotion members. Runtime
features, model and retained historical feature priors are hash-bound. This
stage cannot modify promotion. Its actual generation timestamp is distinct
from promotion generation; it is not backdated to make delayed profit eligible.

`python -m forward.rehearsal ... promotion` saves an isolated promotion artifact
and final receipt before `... profit` runs in a different process. Outputs must
be outside the repository, config inactive, mode REPLAY. No automatic activation,
ledger admission, Git push, order or production page write is provided by these
commands. In CI, the P0 job has no dependency on P1 or aggregate acceptance;
the P1 job consumes the completed hash-checked P0 artifact of the same revision.

### Per-D daybook v2 (future production contract)

`daybook.new_epoch(...)` requires explicit immutable activation. `Daybook`
persists separate `promotion/D.json`, `profit/D.json`, `truth/D.json` with local
file CAS. `freeze_promotion_day` freezes only promotion/path rows and Top1/2/3.
`attach_profit_day` requires the same D/T/T+1 and promotion freeze SHA, a complete
profit permutation, and freezes Top1/2 atomically with that attachment.
`record_day_verification` appends evidence-bound truth without requiring profit.
First admissions must be prospective, after epoch activation and D close but
before T 09:25. REPLAY cannot be admitted. Missing profit is absent, never a
fabricated successful zero-candidate result. A legitimate N=0 is separately
represented by a valid complete promotion/attachment contract.

P0 reads only the epoch and its target-D promotion file: corrupt auxiliary
files, older D files and global latest pointers cannot block it. This is a
local isolation guarantee, **not** a completed remote writer or Pages guarantee.
The v1 bundle ledger and preview below remain compatibility/acceptance readers;
they must not be put back onto the production P0 critical path.

### Unpublished release candidates and v2 statistics

`release_evidence.read_promotion` and `read_profit` require external job-output
receipt SHA256 pins. They consume the exact bytes that were hash-verified and
check current source HEAD, complete member/rank/model bindings and the original
same-run schedule evidence. No model or old ledger is read by these adapters.

`python -m forward.release_candidate ... promotion|profit` writes a fresh,
outside-repository JSON candidate and its final content-hash receipt. Promotion
does not depend on profit; profit candidates contain the exact frozen P0
identities and automatic Top1/2 projections. The projection slots explicitly
say `AWAITING_NEW_EPOCH_NOT_RECORDED`. Every candidate remains REPLAY, with
`ledger_written=false`, `publication_verified=false` and `new_forward_days=0`.
It is not a production admission, remote CAS commit or Pages release receipt.
Natural-input candidates recheck their original slot before and after data CAS;
an expired assembly has no successful completion receipt. Candidate jobs never
become dependencies of either inference job.

`daybook_metrics.statistics_from_daybook(epoch, promotions, profits=None,
truths=None, expected_costs_bps=45.0)` accepts v2 records directly. Promotion
Top1/2/3 do not require a profit attachment. Missing/invalid P1 is separate from
valid READY N=0; broken auxiliaries are reported and excluded. Valid T truth
can remain usable when a different fee contract excludes that D's T1 returns.
Coverage describes only provided frozen P0 days, never claims that unprovided
scheduled days succeeded. This pure reader neither activates an epoch nor
imports historical cumulative totals. Actual publisher/forward admission and
T/T1 input adapters remain separate rollout requirements.

## Frozen day v1 (migration compatibility only)

`day` has `signal_date`, `exec_date`, `exit_date` (YYYYMMDD),
`generated_at_utc`, `generation_mode` (NATURAL or REPLAY),
`source` (JSON object with source commit, model and artifact hashes),
`rows` (0..10 real selected candidates, ordered by promotion rank).
Every row: `ts_code`, `name`, `industry`, `stage_transition` (2→3 or 3→4),
`promotion_rank` (1..N), `promotion_probability` (0..1), `path_label`,
`path_change_pct` (decimal or null), `profit_rank` (1..N), `profit_score`
(finite relative score, not a probability). Profit permutation must have exact
same members; promotion order never changes. Missing path remains null/unknown.

## Ledger API (ledger.py)

`new_ledger(epoch_id, activated_at_utc=None, start_signal_date=None) -> dict`.
`freeze_day(ledger, day, *, open_dates, now_utc) -> dict` returns a new ledger.
An unactivated ledger rejects writes. REPLAY is rejected from production ledger.
The first admitted D sets start_signal_date if null. All source timestamps must
be before T 09:25 Beijing; generation must be D-close or later and now must not
be before generation. No earlier D admission; no old statistics. Exact duplicates
are no-op; changed frozen input for an existing D is rejected.
Epoch identity and activation are immutable. Only explicitly activated epochs
may freeze; tests use synthetic epochs, never write public production data.
Stored days include `freeze_sha256`, immutable `rows` and append-only validations.
`record_verification(ledger, signal_date, verification) -> dict` binds
verification to freeze SHA and exact member set. Do not erase settled truth
on a later missing-data read; contradictory settled values must fail closed.

`statistics(ledger) -> dict` in metrics.py. Groups: promotion_top1,
promotion_top2, promotion_top3, promotion_top3_combined, profit_top1,
profit_top2, profit_top2_combined. Track frozen slots, distinct D days,
T validated and promotion hit rate, T1 settled, conditional positive net rate,
mean net return, slot-weighted daily compounded return and max drawdown.
Pending/missing/exit-blocked not zero. NO_FILL has net_return=null,
slot_return=0 and is excluded from conditional trade win rate.
Only completely resolved group-days enter cumulative daily equity.
This is a resolved-cohort reference curve, not a real account NAV: delayed
exits may overlap other cohorts. Report unresolved slots/days alongside it.

## Verification API (settlement.py)

`verify_day(day, market_by_date, open_dates, *, as_of_date, costs_bps=45.0,
corporate_action_evidence=None) -> dict`.
market_by_date maps YYYYMMDD -> list of dict rows: ts_code, trade_date, open,
high, low, close, vol, up_limit, down_limit (all finite >0, vol >=0).
Each date contains exact-date records; duplicates and mixed dates are rejected.
Missing data is MISSING, never zero. Do not infer absent stock from limit-up-only
tables. T promotion checks T close reaches up_limit; pending before T close.
T1 hypothetical fill = T daily open if volume>0 and not at upper limit;
price is an explicitly labeled daily-open proxy, never a real fill.
Exit = first open session on/after T1 with volume>0 and open>down_limit.
If locked/suspended, EXIT_BLOCKED until valid later session; no skipping missing
sessions or future data. Net return = exit/entry - 1 - costs_bps/10000.
Caller passes as_of_date only for closed, source-verified sessions.
An actual proxy trade cannot become SETTLED without hash-bound corporate-action
review covering T through the actual exit. Without that evidence it stays
MISSING with null returns, even when price bars exist. The initial implementation
supports evidence of no corporate action in the holding window; adjusted-price
and dividend accounting need a separately reviewed input adapter. NO_FILL does
not hold a position and does not require that review.
Return `signal_date`, `exec_date`, `exit_date`, `as_of_date`, `freeze_sha256`,
`rows` with ts_code, t_status PENDING/MISSING/PROMOTED/NOT_PROMOTED,
t1_status PENDING/MISSING/NO_FILL/EXIT_BLOCKED/SETTLED, net_return,
slot_return, entry_price, exit_price, actual_exit_date, and truth evidence hashes.

## Engine API (engine.py)

Migration adapter must reproduce existing frozen promotion + primary mixed-profit
rankings with byte-identical model assets. No training or live weight changes.
`load_frozen_day(root, signal_date, *, generation_mode='REPLAY') -> dict` reads
and verifies exact-D P0/P1 artifact lineage into above day schema. This is a
migration test adapter, not proof of independent runtime or production readiness.
Provide a clear inventory of required assets and replay comparison tests.
Do not write legacy outputs, old ledgers, models, workflows, or HTML.

## Rollout

v1 staging starts with inactive/empty epoch. Any historical replay is separately
labeled and excluded from all forward cumulative statistics. No destructive
cleanup or replacement of production page until natural D/T/T1 acceptance.
Production integration must publish valid promotion D output independently of
profit/settlement/statistics availability. The complete-bundle migration reader
is not a production P0 dependency or a new all-or-nothing publish gate.
