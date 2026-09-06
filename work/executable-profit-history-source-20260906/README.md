# Historical execution evidence acquisition

This is a one-time, retrospective, read-only market-data acquisition for the
profit-model upgrade. It does not fit or publish any model, change rankings,
generate forecasts, recover D lists, or write forward Shadow/Action records.

The preceding execution-v2 study has only 132 terminal candidate labels and 15
complete D cohorts. The frozen 6,753-candidate / 910-D feature ledger is unchanged.
This stage acquires additional raw evidence and preserves that earlier study.

## Installation and activation history

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

### Reviewed preflight-only correction

The first activation `429b751789f88335679d6cf72a9b0986078f819b` created
[run 34022321843](https://github.com/njedu2023-prog/DC20/actions/runs/34022321843).
Its authorization guard passed, but a Linux test fixture failed: a shallow
`/tmp/...` ancestor correctly hit the broad-output-path guard before the test's
expected repository-ancestor guard. The credential-bearing collection step was
skipped; no market query was made. Production files remained unchanged.

The fixture correction does not alter `collect.py` or weaken either guard.
The replacement installation must be a direct child of that exact activation,
modifying only this README, `guard.py`, `test_guard.py`, and `test_collect.py`.
The following activation may only modify REQUEST, bind the new installation and
all seven source hashes, and use nonce `once-v2` with the failed preflight run ID.
This is a new push run after a reviewed test correction, not an Actions rerun.
The source ledger, as-of, query scope, time limit, and source permissions are unchanged.

### Reviewed separation of collection from numerical completeness

The corrected Linux preflight passed all 45 tests in
[run 34022598288](https://github.com/njedu2023-prog/DC20/actions/runs/34022598288),
at commit `8c12086f24c92463d569861d5637d5de1f55805b`. Collection then made 83
requests and completed 25 of 926 required sessions before a candidate numeric
field check stopped the `stk_limit` response for 2022-12-19. This was a genuine
partial collection, not a successful source or a training run. The failed
response was not retained, so its specific stock and field must not be guessed.

Its artifact ZIP SHA256 is
`cd66af08bab4ad73304826a94d1356664ce80c510d602586a730b8d11eb5eaab`, and its
externally logged manifest SHA256 is
`6b6dc4651a73eaeb44e76953266b11a8d2fc71a7a6d38c16eee43cc7b2b523a0`.
All 223 manifest file hashes were independently checked after download.

The reviewed correction separates source acquisition from usable-label gates:
bulk JSON null numerical cells remain null in raw responses and empty in CSV,
are explicitly listed as incomplete evidence, and cannot claim full candidate
coverage. They are never converted to zero. Canary checks, malformed types,
nonfinite values, date/key contracts, and invalid present numerical values still
fail closed. The unchanged label builder classifies missing execution inputs
as unresolved, and the unchanged training gates exclude incomplete D cohorts.

This five-file installation may only update README, collector, guard, and their
tests on the exact failed-run commit. A separate request-only push binds all
source hashes and nonce `null-evidence-v3`, explicitly acknowledges the 83
previous requests, and starts a fresh isolated source collection. The partial
artifact is preserved as failure evidence and is not merged into training.
No failed Actions run is rerun; no production dates, models, ledgers or workflows
are changed. This correction does not add new dates or increase the query budget.

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
