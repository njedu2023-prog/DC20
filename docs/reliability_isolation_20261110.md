# Daily delivery reliability isolation

This change preserves the current promotion model, fixed Ridge weights, candidate
feature definitions, trading rules and every existing frozen result. The immediate
comparison baseline is main commit `65d4e0c1d80c177645eff1d1bbd53fee71cffda9`.

## Separate market evidence from static model approval

`data/market/confirmed_suspensions.json` remains the exact historical baseline.
New verified suspension facts use the separate versioned evidence registry, first
effective for signal date D20261111. Each record and retained official document is
content addressed; a frozen D receipt binds the registry version and complete
evidence inventory available at that immutable source revision. A changed registry
must not silently alter an existing D freeze. The new registry starts empty and
does not add or reinterpret a suspension fact.

Missing, inconsistent or untrusted evidence remains an error. A valid empty
registry is different from a failed read. Historical D<=20261110 receipts retain
their original schemas and bindings; their bytes are not rewritten.

## Isolate only faults whose scope is actually local

The fixed Ridge scorer already isolates a stock with insufficient observed history
or a missing D observation, while retaining the original promotion rank and full
pool background. This change does not introduce a new exclusion or selection rule.

A contradictory consecutive-limit-up count can change the candidate universe,
stage pool shares and other shared features. It therefore still blocks formal P0
ranking. The failure now preserves complete per-stock diagnostics and verified
identity/stage checks, with company names and explicit unresolved stocks. The
diagnostic never claims the remaining stocks have been scored. See
[primary failure diagnostics](primary_failure_diagnostics.md).

The diagnostic JSON and HTML are retained as the failed run's artifact and linked
from its Actions summary. They do not replace the formal index or frozen lists.
They are not automatically published to Pages by a second competing writer.

## Approve static code once, verify each day's inputs every time

A successful main-push run must actually execute the complete candidate regression,
model/source guard and approval-record step. Daily candidate generation may reuse
that approval only when all controlled code, tests, workflow, model and dependency
Git blobs are identical. A daily run cannot approve itself. API failures, truncated
trees/jobs, skipped tests, a different repository or a changed static surface are
not approval.

The existing daily original-P0, strict calendar, source hashes, model, eligibility,
freeze time and publication checks continue to execute. An approval for code does
not approve market data or issue a freeze. Validation and writer preparation each
check the approval. A matching CI already in progress has a bounded wait; no
deadline is extended and no clock is substituted.

## Historical compatibility and acceptance

The new source review records exact current bytes and exact predecessor changes.
Test-only historical views are reconstructed only after current on-disk bytes have
passed that review. Original review documents, source data, frozen lists and model
weights remain unchanged. Production does not import the inverse-audit helpers.

Acceptance includes malformed registry/version/document tests, source and approval
fault injection, P0 diagnostics that cannot gain formal admission, existing candidate
and settlement regressions, and immutable historical evidence replay. The existing
cleanup acceptance workflow additionally compares immediate old/new code on actual
D20261110 frozen inputs, alternating run order across three trials. Rankings,
scores and input inventories must agree. Timings cover that measured computation;
they do not claim upstream collection, GitHub queue or CDN delivery improvement.

The older `943e62b27102c671cd440c45a160054a3bb092f9` baseline remains a separate
historical ranking compatibility check for D20260914, D20261014, D20261028 and
D20261106. It is not this repair's publication timing baseline. Both publication
builds now use the immediate `65d4e0c1d80c177645eff1d1bbd53fee71cffda9` D20261110
business inputs, with each code checkout retaining its own model manifest and
all trust-root checks. Injecting the older D20261106 data into current code fails
correctly because its historical suspension file differs from the current model
pin; the rehearsal must not rewrite that pin or bypass validation to fit the data.

Artifacts distinguish `historical-ranking-comparison.json` (older compatibility),
`publication-comparison.json` (three matched D20261110 Pages preparation builds)
and `reliability-comparison.json` (three matched D20261110 scoring replays).
Publication timing includes no actual Pages deploy or CDN acceptance.

A passing historical replay is not a new natural freeze. D20261111 end-to-end
natural operation can only be accepted after its real market inputs, frozen
records, release evidence and public versions exist. A workflow success badge
alone is not public delivery evidence.
