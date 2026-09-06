# Historical execution evidence acquisition

This is a one-time, retrospective, read-only market-data acquisition for the
profit-model upgrade. It does not fit or publish any model, change rankings,
generate forecasts, recover D lists, or write forward Shadow/Action records.

The preceding execution-v2 study has only 132 terminal candidate labels and 15
complete D cohorts. The frozen 6,753-candidate / 910-D feature ledger is unchanged.
This stage acquires additional raw evidence and preserves that earlier study.

## Installation and activation

1. Locally test and independently review all seven new files. Install them as an
   additions-only commit on base `3e2299a07f7b4430002da0b870c47ecf57c49bb3`.
   Use `[skip ci]` for this research-only installation because adding a workflow
   otherwise triggers existing Core and indirectly the production P0 writer.
   Do not change, disable, or weaken any existing production workflow/pin.
   If repository protection rejects this installation, stop instead of bypassing it.
2. A separate additions-only `REQUEST.json` commit binds the installation SHA,
   all research source hashes, fixed input hashes, and the full exact-date request
   plan checksum. The compact contract binds 926 dates / 102,935 candidate-date
   keys; the full derived session plan is retained inside the artifact.
   Only the new research workflow matches this work-only push. Its guard refuses
   a rerun, forced push, unknown parent, changed source, or other changed path.
3. The one-time start window is 2026-09-06 08:00–12:00 UTC (Sunday); maximum job
   duration is 110 minutes, with a 90-minute collector deadline to leave time for
   failure receipts and artifact upload. There is no cron or production dispatch/retry.
   A single runner/serial request stream limits contention with existing credentials.

## Sources and limits

Tushare's current [change log](https://tushare.pro/document/1?doc_id=9) cancels
multi-code requests (2025-11-03), even though older daily examples still show
comma-separated codes. All three APIs therefore use exact-date pagination,
then filter candidates locally. Pagination stops only after finding every
requested candidate or seeing an empty page; a nonempty short page is not proof
of exhaustion. Its 2025-11-10 entry also prohibits concurrent
multi-IP use; this workflow is not sharded across runners.

Required sources are unadjusted `daily` and dated `stk_limit`; optional
`adj_factor` is diagnostic only. After endpoint canaries, all required dates
are collected before factors use the remaining request/time budget. Missing source rows remain unknown, never
zero returns or presumed suspension. Adjustment factors alone do not prove
cash/share entitlement returns. Required credential/source errors stop the
run; an optional factor permission denial is recorded without buying access.

The T through T+1-plus-20-session collection window is a data budget, not an
exit policy. Any unobserved intermediate or later exit remains unresolved under
the unchanged execution-v2 label rules. This is daily-open proxy research, not
actual auction fills or executable capacity evidence.

## Artifacts and next gate

The only output is a runner-temporary Actions artifact, including request and
source/run identity, per-response checksums, raw market responses, filtered
CSV snapshots, row coverage and failure receipts. Token values and upstream
error text must never enter logs or artifacts. Nothing is committed by a runner.

After download, verify every hash, compare overlapping previously committed
official-source evidence, and rebuild labels in a separate research snapshot.
Only then apply the unchanged complete-D training gate. Retrospective training
results are not new forward performance and cannot automatically replace the
live ranking model. Existing production pins and outputs remain byte-identical.
