# Exact daily-gap supplement: fourteen source queries

This is source evidence, not a suspension decision, synthetic price, completed
trade, training label or production activation. Only the registered JSON push
starts collection; rerunning the same workflow attempt cannot repeat requests.

The fresh 6,753-row candidate replay is bound by SHA256
`2cca1e928b92a6cd46dabf7090714cfbc5e88682f2d93ecbc481c01ecd910609`.
It contains six pending exit-daily rows. Five first-missing-session daily-empty
responses and their suspension/resumption records are already present in the
original, independently audited diagnostic ZIP (19,600 bytes):

- SHA256 `760e70f707559cf92353f97cda7bfc17aec40aabbd528957e0640cec7e0c49d9`
- GitHub run `34672430723`, commit `6ea9616014da3850ae4064c89e56e832608180d3`

The old ZIP remains untouched. Identical empty-table bytes do not establish a
stock/date identity: their request, HTTP metadata and inventory bindings must
also match. New collection does not import or qualify those diagnostic facts.

`DAILY_GAP_COLLECTION.json` SHA256:
`8d74bbca3f7eb5eee888c9ae0ff04272650fb108ea0abe66f29c21b744876465`.
Its only new calls are:

| Stock | Endpoint | Exact dates |
| --- | --- | --- |
| 600083.SH | daily and suspend_d | 2024-04-30 |
| 603226.SH | daily | 2025-06-11, 2025-06-12 |
| 603122.SH | daily | 2025-11-18, 2025-11-19 |
| 603580.SH | daily | 2026-07-08, 07-09, 07-10, 07-13 |
| 002036.SZ | daily | 2026-07-24, 07-27, 07-28, 07-29 |

Thirteen daily queries plus one suspension query, zero retries and no auction
queries. Each response has a 1 MB bound, 20-second socket and parent-process
response deadline; request starts are at least 0.5 seconds apart, serial, within
a 600-second collection budget. The workflow also applies an outer timeout.
Complete qualified empty tables and explicit failures are retained. A partial
source artifact is not complete market coverage; a hard kill before receipt
finalization may be unverifiable and must fail closed.

Before any source call the cloud workflow tests the research code and checks
all production freeze pins. Independent verification requires externally read
run ID, commit, exact plan SHA and prior label SHA; environment assertions alone
are not authentication. Source-only flags remain false for label admission and
nontrading-session qualification. A separate adapter must combine exact full
daily-empty evidence with same-day all-day suspension facts and reject any
contradictory trade evidence before advancing a holding session. Missing prices
are never filled with zero or amount divided by volume.

No trained model, frozen promotion rank, historical source, shadow ledger or
frontend is modified by this source workflow. Future D >= 2026-09-14 stays
outside the historical research scope.
