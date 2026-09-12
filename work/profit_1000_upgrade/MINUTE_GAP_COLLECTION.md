# Registered exit-minute evidence collection

Research only. This registration does not activate a model, rewrite a frozen
source archive, or claim actual execution or provider-confirmed bar semantics.

## Evidence used to derive the scope

- Candidate auction collection run: `34706876949`, attempt 1, commit
  `0d270f04abb77f3f08f6393a59bfe2589defe5ef`.
- Artifact: `10302790770`; SHA256
  `472e777c93379dcc845ac4d6a9c7c4dfa2a71ded84d814baa5fb51c3ad9d60d4`.
- Independent verifier accepted 1,860 of 1,860 requested auction pairs;
  receipt SHA256 `8523d4f7a8ed83154dbfdec19fc55fc93e6789d445321016e4fdb7046fd910ae`.
- Fresh 6,753-row / 910-D overlay label report SHA256:
  `2cca1e928b92a6cd46dabf7090714cfbc5e88682f2d93ecbc481c01ecd910609`.
- All 3,832 previously terminal rows remained unchanged and 1,537 negative
  settled rows were retained. There are 2,384 pending minute pairs, six pending
  daily rows and ten invalid-entry-price rows. Missing evidence is not zero.
- Fixed candidate evaluation stopped at `BLOCKED_DATA_QUALITY`; no model fit.
  Train completeness: 526/724 dates; validation completeness: 0/186 dates.
  Report SHA256 `1e41870ed72926ab4e2801c634427d9cffcde92bce640d063d56db228cf2b8a8`.

## Exact registered plan

`MINUTE_GAP_COLLECTION.json` is the exact output of that fresh label replay,
not the earlier 1,047-pair estimate. SHA256:
`ecbf13df6a94a738fdcc38b79b537efcf37ce280dc10cf42ae7137bd47b55f09`.
It registers 2,384 unique date/stock pairs. First/middle/last preflights are:

- `20250117 / 002164.SZ`
- `20251112 / 002759.SZ`
- `20260818 / 603118.SH`

Only `stk_mins`, registered 09:31–15:00 queries and strict 240-bar acceptance
are allowed. Maximum four workers; request starts at least 0.5 seconds apart;
20-second socket bound; zero retries; 4,200-second collection budget with the
last 20 seconds reserved for completion. Any failed preflight stops the bulk
collection. A workflow retry cannot repeat collection.

The workflow runs research tests and the production freeze guard before using
the source credential. Its artifact includes the exact plan, request journal,
source bytes and final receipt when the process reaches receipt finalization.
A hard outer timeout may produce an incomplete, unverifiable artifact; it must
not be treated as accepted evidence. The verifier independently binds external
run ID, commit SHA, plan SHA and prior label-report SHA. Verified partial source
evidence is not the same as complete labels or an accepted model.

Next: independent artifact verification, fresh immutable-base extraction,
typed source-only overlay, all-row label replay, then the unchanged fixed
quality gates. Additional held-day or daily gaps require separately registered
evidence. Do not discard incomplete validation cohorts to force training.
Future holdout D >= 2026-09-14 remains excluded; promotion models, frozen
membership/ranks, production accounting and the compact frontend are unchanged.
