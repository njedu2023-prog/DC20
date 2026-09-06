# Nineteen missing source partitions — independent research tail

This package does **not** rerun or turn the previous failed run into a success.
Run `34023469106`, commit `d5f3df57c78b0458d1329034c94ec324827aa390`, stopped
at its 90-minute soft deadline after 3,354 queries. Its artifact and audit stay
immutable: 1,833 / 1,852 required partitions verified, including 20260824 daily.
The exact ZIP, manifest, audit and original collector SHA-256 values are pinned
in PLAN.json and repeated in every tail artifact's provenance. The parent audit
is an externally authenticated prerequisite, not a file assumed present in the
runner checkout. A separate closure audit must re-read that actual evidence.

## Fixed scope and boundaries

Only 20260824 stk_limit and both daily/stk_limit for 20260825, 26, 27, 28, 31,
20260901, 02, 03, 04 are collected. There are 19 date/API partitions, 821 distinct
candidate/date keys and 1,533 candidate/date/API keys. Code sets are reconstructed
from the same SHA-pinned 6,753-row ledger and SSE calendar, not manually chosen.
As-of remains 20260904 and the originally fixed 20-session exit observation tail
does not become a forced-exit strategy. No already complete partition is queried.

The byte-pinned original Client/collect_pages/validate_rows helpers are reused;
the original perform_collection/main entrypoints and files are not modified.
Only daily and stk_limit are allowed. Each incomplete partition starts at offset
zero in this new run; no cross-run pagination. At most 76 bulk queries plus two
non-null permission/schema canaries, confined to each API's first missing date.
Single thread, 0.5-second minimum spacing, official TLS endpoint, no proxy,
redirect or retry, 600-second soft deadline; workflow timeout is 20 minutes.
Explicit JSON null remains unknown/empty CSV, never zero. Missing candidates
remain gaps rather than being supplemented. Optional adj_factor is not requested.

## One-time activation, separately reviewed by the repository owner

Install all eight new files in one additive commit directly after
`0904cb6f1fd0bc62a56d47b9a915d8c5374df076`, using `[skip ci]`. REQUEST.json ships
with `NOT_ACTIVATED`. Only the owner creates its following activation commit:
change REQUEST.json alone, set state ACTIVATED, bind the installation SHA and
exact SHA-256 of the seven non-REQUEST files listed in guard.py. This is a new
request-only push, never workflow rerun or dispatch. Only main/attempt 1 is valid
in the fixed 2026-09-06 10:00–14:00 UTC window. Guard runs before tests, after
tests and again inside the collector before reading the token. Tests are offline.

Workflow permissions are contents:read; checkout credentials are not persisted.
The token is scoped to the collection step only. The same existing research
collection concurrency group prevents these source runs overlapping. This does
not claim an audit of account activity from other IPs. Only a new RUNNER_TEMP
directory and GitHub artifact are written; no Git writer, orders, models, rankings,
Shadow, production Action, label reconstruction or training is invoked.

## Artifact contract

`artifact_manifest.json` keeps schema dc20_isolated_source_artifact_manifest_v1
and adds artifact_role required_partition_tail. It binds its own run ID, SHA and
attempt, provenance and every file's bytes/SHA. Files include exact PLAN.json,
activated REQUEST.json, partition_plan.json, status.json, canary CSVs, raw gzip
responses, query receipts and candidate_sources/<date>/<api>.csv.

partition_plan.json is an ordered array of {trade_date, api, codes}; its canonical
SHA excludes the final file newline. status uses dc20_profit_history_tail_collection_v1
and independent COLLECTED_TAIL_REQUIRED_PARTITIONS[_WITH_GAPS] or
BLOCKED_TAIL_COLLECTION status. coverage entries are {trade_date, api,
requested_candidate_count, info}, with original info pagination/query_numbers,
missing_candidate_codes and incomplete_candidate_fields. Progress and status are
flushed after every completed partition, and failures still produce their own
manifest. Startup failure before a valid request never contacts the provider.

A separate two-source closure audit is mandatory. It must verify both raw chains,
all 1,852 required partitions, every source identity/hash, the 29-day committed
evidence overlap and any shared old-partial/new-tail evidence. No conflicting
revision is selected as preferable. Missing rows/null values remain unknown and
are later masked by the unchanged label policy. Receiving or completing this
tail never by itself grants label, training or production-release authorization.
