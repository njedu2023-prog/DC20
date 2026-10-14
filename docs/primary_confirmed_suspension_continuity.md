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

Only `PrimaryDReadOnlyEngine` has changed. It reads the disclosure manifest
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
