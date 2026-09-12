# Registered exit-minute evidence collection

Research only. This registration does not activate a model, rewrite a frozen
source archive, or claim actual execution or provider-confirmed bar semantics.

## First collection and its preserved evidence

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

## First registered plan (preserved in commit c9e6883b and its artifact)

The first `MINUTE_GAP_COLLECTION.json` was the exact output of that fresh label replay,
not the earlier 1,047-pair estimate. SHA256:
`ecbf13df6a94a738fdcc38b79b537efcf37ce280dc10cf42ae7137bd47b55f09`.
It registers 2,384 unique date/stock pairs. First/middle/last preflights are:

- `20250117 / 002164.SZ`
- `20251112 / 002759.SZ`
- `20260818 / 603118.SH`

## Current registration: second collection, 134 new pairs

The first minute collection run `34708814087`, commit
`c9e6883b9a0d14bfe05058f0c78b225494429a73`, completed all 2,384 source pairs.
Artifact `10303280540` SHA256:
`09eaa638d725c71c694b1a0a0be6bf567266108b4edef19d5dedd130691d36a8`.
Independently verified receipt SHA256:
`6d3ebf9b93f11b381520eae3b867c32d30cf867150ddac336325e0f1e528f76d`.

Fresh all-row replay produced 6,753 rows / 910 D cohorts; label SHA256:
`9b9cc3e0015bc5e2eb0c865ca05af93e1900dece7b123d0ff4f051ee372a7c1f`.
All 4,353 prior terminal rows stayed unchanged except cohort-completeness flags.
There are now 4,726 settled rows, including 2,826 negative settled rows, and
794 complete D cohorts. Missing evidence is not imputed or dropped: 143 pending
minute rows correspond to 134 unique next date/stock pairs; six daily and ten
invalid-entry-price rows remain separate. No model has been fitted or activated.
Local acceptance SHA256:
`292bd6f60eb02e28124b4630d9d388bf0a83ed1258b26d4a462189cb631ebcfc`.

The current `MINUTE_GAP_COLLECTION.json` binds this exact label SHA and the
134 independently enumerated pairs. SHA256:
`31f420bb86181dc96e20a267ffb6275f34b2d88b7854764f510be7cc85f3e9d8`.
They are disjoint from all first-round requests and absent (both data and meta)
from the already verified augmented base. Preflights:

- `20250120 / 600539.SH`
- `20251107 / 002181.SZ`
- `20260818 / 603330.SH`

The old registration remains unchanged inside its original source artifact.
Each round is independently verified against its own external run, commit,
archive, plan and prior-label SHA. A composite source-chain digest is explicitly
not a single GitHub collection receipt. Nontrading supplements are not admitted
as raw minute-chain priors and cannot silently alter earlier economics.

## Unchanged collection and acceptance constraints

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
