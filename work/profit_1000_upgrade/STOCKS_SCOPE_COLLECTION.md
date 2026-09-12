# Isolated stock-scoped auction gap collection

This source-only stage follows the replay-accepted v3 artifact from run
34684000971. It does not replace the old v3 codec, labels, fitted model, daily
rankings, forward holdout, frontend or shadow ledger.

The exact base ZIP is read and copied unchanged as `base_v3.zip`; its 181 accepted
dates are not requested again. All 6753 frozen candidate identities and 910 dates
remain in that immutable archive. Only the 212 rejected auction dates (1860
candidate/date pairs) enter this collection. The 517 precoverage dates are not
requested. Prior labels are audited, not reused as new outcomes or used to select
the gap universe.

## Registered request and acceptance

The official `stk_auction` documentation lists `ts_type=STK`. It does not document
pagination for this endpoint, so this stage does not invent `offset` or remove
the completeness checks. Requests preserve the actual STK parameter in a new
source namespace and new metadata policy; they are never relabeled as old v3
date-only requests.

The three fixed preflight T dates are 20250319, 20250826 and 20260203. Each must
return a complete, nonempty, same-day table with at least one frozen candidate.
Failure stops the unstarted dates. Subsequent missing candidates remain explicit
missing codes; a qualified empty table is not a price, fill, no-fill or zero
return. Every table requires success code exactly integer 0, `has_more` exactly
false, an explicit compatible integer count, fewer than 8000 rows, correct fields
and unique valid same-day codes. No truncation, duplicate removal, unregistered
pagination or fallback request is permitted.

The total budget, including preflight, is 212 requests, zero retries, at most two
workers, one request start per second, 20-second socket timeout and 4 MB per HTTP
body. The collector stops admitting new requests at 1180 seconds, reserving
20 seconds from its 1200-second request-start budget. The socket timeout is not
a hard whole-response wall-clock deadline; the cloud job has a 35-minute limit.
The bounded workflow only runs for the registered JSON change on main and attempt
1. Source rejection is retained as blocked/partial evidence; a successful job is
not proof of completed source coverage.

## Output and limitations

Outputs are the unchanged base ZIP, candidate-derived gap manifest, request
journal, separately qualified STK data/meta pairs, and exact file/code bindings.
Rejected response bodies, server messages and credentials are not persisted.
Source data preserve the provider's reported numeric values. This stage does not
establish reporting precision, per-candidate price validity, execution capacity,
buyability, entry-price labels, returns or model uplift. Synthetic transport
injection is rejected before it can create source files.

Run `stocks_scope_verify.py --output OUTPUT` to independently verify all output
bytes, requests, candidate coverage and source metadata against the immutable
base. Completeness means 212 source tables qualified, not that every candidate
has a usable auction quote. A separate later gate must integrate qualified
sources into new labels and revisit remaining minute/exit gaps before training.

Official reference: https://tushare.pro/document/2?doc_id=369
