# v3 source collection and full-cohort reconstruction

This stage wires the separately reviewed `auction_truth_v3.py` codec into an
isolated research collector and label builder. It does **not** train a model,
activate a production entry policy, change promotion ranking, publish new HTML,
rewrite shadow transactions, or evaluate the forward holdout beginning D
2026-09-14. `formal_entry_v3.py` is a different, older dormant preparation path;
this research stage does not invoke it.

## Fixed scope

- Keep all 6,753 frozen candidates on 910 D dates, 2022-11-11 to 2026-08-14.
- Preserve the pinned D-close features and strictly earlier OOF promotion rank.
- Reconstruct outcomes from sources, never from old ledger outcome columns.
- Preserve T+1 10:00 / observed sealed-limit hold / first observed break exit,
  45bp cost, negative outcomes, and explicit unknown or unfilled states.
- Historical observations are already-inspected development data, not an
  untouched test set or evidence of improved live profitability.

## Source reuse and new collection

`research_v3.initialize` first audits the exact v2 archive from run 34676871475,
SHA256 `58467518002c587349587eebb32681b1d81c3c8850ca5595b342818157ebfc64`.
Only three pinned feature/calendar input files, 926 daily and 926 limit files,
and 2,726 qualified 09:31-minute data/metadata pairs are imported. Every minute
pair retains its original request identity, response digest and research-only
BAR_END assumption. Fixed digests of the entire reused-binding and reused-request
lists prevent modified inputs from being silently adopted as a fresh baseline.
Old auction sources, derived outcomes, models and diagnostic tables are excluded.

`COLLECTION_V3.json` registers only canonical auction collection: 517 explicit
pre-coverage declarations plus at most one genuine HTTP request for each of 393
covered T dates. Limits are 393 calls, 1,200 seconds, two starts per second, four
workers and a 20-second request timeout, with no retries or minute/daily requests.
Token and server messages are not retained. No diagnostic object is wrapped or
reconstructed as an HTTP response. All new sources use a separate v3 namespace.

Price qualification and capacity qualification are distinct. Positive auction
price/volume and matching daily-open cents can qualify a posthoc entry-price
observation even when reported amount arithmetic is invalid. Such a sample has
unknown capacity, not proven execution. A raw price is never rounded in evidence.
Malformed, conflicting or incomplete sources remain pending; they never become
empty market tables, free trades, zero returns or qualified fallback prices.

## Rebuild and handoff

The workflow first runs all upgrade tests and the existing production freeze.
It imports the old sources, collects new auction evidence, and validates both
original and current request/source bindings before rebuilding all 6,753 labels.
Pending requests are exported for a separately bounded follow-up minute/daily
phase. Existing missing daily rows remain pending; no suspension is inferred.

`research_results/summary.json` distinguishes source integrity, auction coverage
and complete label days. A successful workflow only means this research stage
ran and retained evidence. The summary may still say `BLOCKED_CANONICAL_SOURCE`
or `BLOCKED_EXIT_OR_ENTRY_EVIDENCE`. Neither success nor a positive subset outcome
authorizes model fitting or activation. Full cohort and source acceptance,
chronological evaluation and untouched forward validation remain separate gates.

All output directories and files are fresh and append-only. There is no resume,
overwrite, production writer, fitting function or release switch in this stage.
