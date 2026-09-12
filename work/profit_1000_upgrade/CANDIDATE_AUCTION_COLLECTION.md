# Frozen per-candidate auction gap collection

This bounded source-only stage follows two successful real single-stock
protocol probes (run 34687000408). The probes are not market-source imports.
Every source in this stage must come from a new original HTTP response.

- Scope: exactly 1,860 (T, code) pairs derived from the 212 missing dates in
  replay-accepted artifact 10295337206. The 181 accepted dates, 517 precoverage
  dates and all 6,753 original candidate identities remain unchanged.
- Request: documented `stk_auction` with exact `trade_date` and `ts_code`, the
  original six fields. No STK filter, offset, fallback request or retry.
- Preflight: 2025-03-19 / 000612.SZ and 2026-02-03 / 000995.SZ must each return a
  complete, unique matching row before the remaining pairs are requested.
- Budget: 1,860 attempts including preflight; four workers, starts no closer
  than 0.5 seconds, 20-second socket timeout, 4 MB per response. The request-start
  budget is 3,000 seconds, not an absolute HTTP deadline. The workflow imposes a
  separate 55-minute process cutoff and retains partial evidence on failure.
- Successful empty tables remain observed absence, not no-fill or zero return.
  A later explicitly validated overlay may qualify price/capacity and invoke the
  unchanged 10:00/limit-hold exit policy. This collection itself does neither.
- No labels, training, ledger, HTML, production model or future holdout writes.
  Raw envelope messages and credentials are never saved; original table values,
  request identity and HTTP response hashes are retained for provenance.

The new namespace is `data/research/candidate_auction_v1/YYYY/YYYYMMDD/CODE/`.
Independent verification re-derives scope from the immutable ZIP, checks every
request, code and file binding, and returns a sealed read-only source capability
for a later overlay. A green collection job alone is not model acceptance.
