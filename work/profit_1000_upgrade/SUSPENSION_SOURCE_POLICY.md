# Isolated research suspension event source v1

Status: **EVENT_CODEC_ONLY_NOT_INTEGRATED**. This module neither changes the
running v2 collection nor reads daily price truth, changes an exit engine,
replays labels, modifies a ledger, fits a model, or relaxes a training gate.

## Event contract

Official documents reviewed 2026-09-12:

- [Tushare suspend_d](https://tushare.pro/document/2?doc_id=214): `S` is suspension,
  `R` is resumption. `suspend_timing` is supplied for intraday suspension and is
  empty otherwise. An exact code/date `S` with null or empty timing is therefore
  evidence of a full-session suspension **event on that date only**.
- [Tushare daily](https://tushare.pro/document/2?doc_id=27): daily rows are not
  provided during suspension. A missing row alone does not prove suspension.

`session_evidence` reports only event evidence. Its `market_absence_verified`,
`can_advance_holding_day`, `settlement_allowed`, and
`production_activation_allowed` are always false. It accepts no caller boolean
as proof that a daily request or partition was complete. There are no OHLC,
return, zero-return, fill, mark-to-market or settlement outputs.

No date interval is inferred from a start event, an end/resumption event or an
empty response. Multiple rows for one code/date, including S/R contradictions,
are rejected, not silently resolved. Nonempty intraday intervals are retained
as intraday evidence and never promoted to full-session evidence.

## Two distinct provenance paths

`source_bytes(raw_http, trade_date, code, request_params=..., fetched_at_utc=...,
network_request_performed=True)` validates one actual successful suspend_d HTTP
envelope and returns canonical JSON of its unchanged `data` table plus new
metadata. It neither requests data nor writes files. A collection must bind the
actual request and source bytes independently. Errors, permission denials and
rate-limit responses are not event-absence evidence. Filtered `suspend_type`
requests are rejected because they could hide contradictory R or S events.

`import_diagnostic_bytes(zip_bytes)` accepts only the immutable archive below:

- Run: `34672430723`.
- Commit: `6ea9616014da3850ae4064c89e56e832608180d3`.
- ZIP SHA256: `760e70f707559cf92353f97cda7bfc17aec40aabbd528957e0640cec7e0c49d9`.

It verifies all 31 source-file bindings and preserves the entire original ZIP.
All diagnostic flags inside it remain unchanged, including
`full_day_suspension_automatically_confirmed=false` and `settlement_allowed=false`.
New metadata uses `DIAGNOSTIC_TABLE_IMPORT`, `raw_http_retained=false`, and
`raw_http_supplied_to_codec=false`. The old archive retained original data tables
and HTTP digests, **not** the full raw HTTP bodies. No HTTP envelope is rebuilt.
Each of the 22 event-day pairs retains the original full-range table bytes.
Seventeen are S/null, five R/null. Projection to one date occurs only while
reporting the event. The returned 45-file bytes bundle is not written or
published by this module. `load` rechecks the pinned archive and original member
bytes for every imported source pair; callers must also bind/recheck source
files at the end of a replay, as for other immutable preloads.

Both paths retain original values and table order, reject truncated/paginated
responses, wrong identities, malformed timestamps, duplicates and SHA changes.
The provider's `count=0` sentinel is accepted; a positive count must equal the
actual row count. Recorded fetch time must be UTC and after 16:00 Shanghai on
the request's final date. This proves the recorded contract, not that an
unavailable API event was known to a trader before the session.

## Future integration gates (not implemented here)

1. Independently bind the exact-day/code daily absence to an actually complete
   request or complete market partition. An arbitrary CSV or bare boolean is
   insufficient. Existing diagnostics directly re-requested only the first
   missing day of each of five intervals; later days need their own verified
   market-source completeness evidence in addition to S events.
   Bind the exchange trading calendar too: an event date alone does not certify
   that the exchange was open. This codec validates dates, not trading sessions.
2. Preserve holdings, reserved costs, original scheduled exit, any pending sell
   decision, and the true count of delayed trading sessions through confirmed
   suspension. Do not inject fictitious flat OHLC/zero-volume bars to satisfy an
   engine that currently requires daily prices.
3. Obtain verified resumption daily, limit and the versioned minute source before
   executing the 10:00/limit-hold policy. Resumption alone never guarantees a
   sale: limit-down illiquidity remains blocked, and a sealed limit-up can extend
   holding. No sealed-hold logic is overridden by this codec.
4. A changed resumption `pre_close` requires independent corporate-action/share
   accounting evidence before continuing a wealth chain. S/R events do not
   establish split factors, dividends, shares, reference prices or economic
   returns. Do not invent a price link from a suspension.
5. A missing quote leaves current NAV unknown. If a future accounting policy
   adopts last-trade valuation for confirmed suspension, name/version that
   convention and flag the stale mark explicitly; it is not observed daily
   price truth or a zero realized return. Negative subsequent exits remain in
   results. The same convention must be used in labels and capital replay.
6. Preserve frozen rankings and every Top1/Top2 record, including pending,
   suspended and loss cases. The frozen v2 collection/plan/model gates remain
   unchanged; event-codec test success does not activate a production model.

## Offline verification

Unit tests include malformed data/requests, S/R conflicts, intraday timing,
count/pagination errors, tampered metadata, wrong origin, partial pairs and
aliases. The real archive test is explicitly opt-in, without network access:

```sh
DC20_SUSPENSION_DIAGNOSTIC_ZIP=/absolute/path/suspension-34672430723.zip \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.:work/profit_1000_upgrade \
python -m pytest -q work/profit_1000_upgrade/test_suspension_truth.py -p no:cacheprovider
```

If the artifact path is not supplied, only this real-artifact test is skipped;
ordinary unit/adversarial tests still run. Tests create disposable source pairs,
not production evidence or permission to settle.
