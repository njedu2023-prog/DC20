# P0 confirmed suspension continuity repair — 2026-10-14

The exact-D source pool identifies 002813.SZ and 002860.SZ as stage 2 on
20261014. The original runtime stopped at each missing prior SSE daily row,
turning both into stage 1 and rejecting the entire hard pool. Official issuer
filings now establish that these gaps were full-session suspensions:

- 002813.SZ: suspended from 20260930; resumed 20261014.
  https://static.cninfo.com.cn/finalpage/2026-10-14/1225604178.PDF
- 002860.SZ: suspended from 20260922; resumed 20261014.
  https://static.cninfo.com.cn/finalpage/2026-10-14/1225604019.PDF

`data/market/confirmed_suspensions.json` binds the six reviewed official PDFs
by URL, actual disclosure date and SHA256. Start is inclusive and resumption
exclusive. Announcement signature dates do not replace disclosure dates.
Evidence was collected on D after close and must exist in the dispatch HEAD.

## Runtime behavior

The streak correction is confined to `PrimaryDReadOnlyEngine`. It reads the disclosure manifest
through the existing Git HEAD verifier and records consumed intervals in the
P0 receipt. It independently validates the complete SSE span, the absence of
daily bars during every confirmed suspended session, both boundary bars, and
unchanged pre-close at resumption. It then counts actual close-at-limit bars
using the original frozen engine's price tolerance. Unknown missing bars are
never skipped. Contradictory quotes, incomplete calendar, missing boundaries,
price adjustment, overlapping evidence and evidence disclosed after the
requested signal date remain errors.

The original hard-pool identity audit remains mandatory: every source
identity must survive inference and independently recomputed stage must match.
No stock is removed or replaced to force publication. The path uses real stock
trading sessions and never synthetic OHLC bars.

## Frozen model compatibility

The frozen five-year ledger takes source stage as event identity and rebuilds
promotion context over six strict SSE sessions, preserving missing bars as NaN.
The corrected runtime uses that same six-session window and NaN policy. It
does not compress model context across suspension. Path fields are outside the
production model feature allowlist. No model, training ledger, training cutoff,
score thresholds, or existing dated ranks/forecasts have changed.

The active freeze manifest rotates only the P0 implementation pin and adds
pins for the disclosure manifest and regression test. The generic engine pin,
model artifact pins, and all previous pins remain identical. This is an
explicit reviewed implementation repair, not a model retrain or silent hash
bypass. The original failure and its dispatch remain in the audit trail.

## Verification

Regression coverage includes real-bar continuity, unknown gaps, head binding,
future evidence (including historical queries), overlapping intervals,
suspension/quote contradictions, calendar holes inside and outside the bridge,
price discontinuity, original limit-price tolerance, and exact field equality
with the frozen six-session context helper, including NaN.

A new controlled NATURAL P0 may be dispatched only after local checks pass,
with fresh same-D deduplication, immutable source commits and the existing
23:30/CAS guards. This document does not claim publication success. P0, P1,
independent Shadow and exact Pages acceptance must still be checked separately.

## Candidate provenance compatibility

The 20261014 repair changes the P0 source bytes, so the candidate adapter and
its current dependency guards require an explicit pin update. Historical
predictions keep the original complete tuple of runner SHA256, registration
SHA256 and dependency map. The v1 and v2 readers select either the reviewed
historical tuple (for D before 20261014) or the current complete tuple. Mixing
an old runner with a new registration or dependency map remains an error.
An unknown SHA is never accepted. Current source bytes, module origin and the
start/end guards remain strict even while reading an old snapshot.

The same separation applies to publication code bindings, original importer
receipts, capture/verifier pairs, observer capsules and outcome writer pairs.
Historical artifacts are read without rewriting their bytes or their hashes.
An original outcome, collection or daily writer is accepted only with the complete
historical snapshot tuple it could actually consume; the current writer may
continue either reviewed snapshot version. The publication runner, importer,
legacy coordinator and observer/capture code graph must also belong to the same
selected profile. Cross-layer combinations are rejected even when each individual
hash is separately known.
New writes identify the current code and registration; they retain the
original snapshot registration and prediction when appending an observation.
This compatibility change does not grant a new freeze or backdate publication.

The P0 receipt's optional `confirmed_suspensions` entry is a typed disclosure
source, not a daily-price row. The adapter binds its exact Git blob and SHA256,
validates the declared intervals and disclosure dates, and keeps the file in
all subsequent source inventories. An inventory therefore has the original
28 files, or exactly 29 with that one registered disclosure file. The original
21 SSE-session daily-file registration and the stock's 21-observed-bar feature
requirement are unchanged. Suspension evidence never fills a missing price.

Read-only verification of the actual D=20261014 receipt and fixed Ridge model
preserved all ten promotion members in their original order. Seven members
had scoreable candidate features. The observed-bar counts for 002860.SZ,
000668.SZ and 002813.SZ were 11, 18 and 16 respectively; their insufficient
history states remain explicit and they receive no invented profit score.
The model canonical SHA256 remains
`999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589`.
This local check is not a forward freeze, publication or trade execution.

Compatibility regression checks cover original 20260914 v1 and 20261013 v2
snapshot bytes and their original durable evidence bodies, mixed/unknown
profiles, external-byte changes, current-code drift, and reading old snapshots
through the current collector and outcome writer. Pending observations retain
null returns. The dated P0 JSON, CSV, runtime features and receipt, existing
model artifacts, score math and historical frozen predictions are unchanged.

## Explicit continuation after original P0 deployment failure

The candidate importer retains the original single-run contract: that P0's
compute, CAS and deployment must all succeed. A separate, paired-input entry
supports one narrowly defined case: a first-attempt controlled NATURAL P0
created and committed its D output before D 23:30, but its canonical deployment
failed; a later first-attempt natural P0 then verified the same D output,
skipped all six generation steps and CAS, and successfully deployed its own
exact head in its own Pages artifact. A push-only Pages run cannot stand in
for that successor P0. Neither run is relabeled.

Use the existing candidate workflow's `p0_run_id` for the successful natural
reuse, and supply both `original_generation_run_id` and `original_cas_commit`.
The original CAS remains `published_git_sha`; the later deployment has its
own `successful_publication_git_sha`. The receipt retains both run identities,
job outcomes, artifact digests, original source production times and the real
new candidate freeze time. Local synchronization timestamps are not source
production timestamps.

Admission reconstructs every file in the original four-member candidate ZIP,
including bounded Git binary literal/delta patches, and matches the complete
patch to the original base-to-CAS Git tree difference. The CAS has exactly the
original run head as its parent. Its four new P0 files and the closed 28/typed
29 source inventory must remain identical at the original CAS, later deployment
and current main. Both upstream commits must predate the original dispatch.
The original base, CAS and later deployment must retain the registered P0
workflow and its 27 code/model/lock bindings, including the freeze loader and
all 18 receipt runtime-code bindings. The original promotion model bytes are
independently hashed; they are never deserialized by the continuation reader.

The entry requires four absent candidate files before a new write, a genuine
host-clock freeze and publication before T 09:15, and the existing non-force
CAS with no overwrite, retiming or automatic retry. A complete existing day
is revalidated and reused; a partial day is an error. The independent observer
rechecks both runs and the same source proof. Its typed capsule contains three
archive identities and preserves the complete original candidate ZIP as a
bounded base64 envelope for offline digest, member, patch and Git-tree replay.
The original two-archive capsule format and historical v1/v2 profiles remain
valid with their original limits and bytes. Only typed continuation capsules
may use their separately bounded larger inventory. Unknown binary blobs remain
invalid; gzip history is bounded and scanned, and the sole binary model
exception requires the exact registered Git blob, SHA256 and byte length.

Tests distinguish portable synthetic successor runs from actual acceptance.
The actual 20261014 original run, original ZIP, 21 patch changes and 29 original
sources are checked independently; a synthetic successor proves mechanics,
not that a successful live successor or forward freeze has occurred. Final
acceptance still requires the actual successful natural reuse, new candidate
freeze, independent observer and public revision. This candidate continuation
is independent of the P1 and Shadow branch and grants no trading authority.
