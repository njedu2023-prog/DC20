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

## Second registration: 134 pairs (preserved in commit e49144b7 and its artifact)

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

## Current registration: third collection, 24 new pairs

Second minute collection `34711597029`, commit
`e49144b79be894d79a99a06f28a821fb098a8976`, completed 134/134 requests.
Artifact `10303517856`, ZIP SHA256:
`fe6e8fa56b66cc769c7d12c9f2a6d9d676069ae4a1876ea5d12fab1c42ba3200`.
Receipt SHA256:
`8e2f024b0a591e7367819864da5ae24b5306d662dc2a9ae8097ca14ca9fe0dc5`.

Independent full two-round replay preserved 6,753 rows / 910 D cohorts and all
6,594 prior terminal rows. It added 119 settlements: 4,845 settled, including
2,874 negative rows; 881 complete D cohorts. There remain 24 minute gaps, six
separate daily gaps and ten invalid entry prices. No outcome is filled with zero.
Exact raw label SHA256:
`d159a22b72d0e485fd4c82351170e9861f5d059a25379b67026ee8cc7942159e`.
Acceptance SHA256:
`0786e88983341de512e9d887ee500edde63f3a12578e87c49c80e0a82265257f`.
Raw source-chain digest (not a single GitHub receipt):
`437be2431cf83ca3c187e2ad06219b71c0f74dc84116bac5bf218b183e8aafd9`.

Current `MINUTE_GAP_COLLECTION.json` is derived from all 24 actual pending rows
and binds that exact raw label report. SHA256:
`67d5b385c3a4a87223457d0c02663d9a1f28fa6a47d18b958fc365c2372020c2`.
All 24 pairs are distinct, within the original exchange calendar, and have no
existing data/meta or orphan files in the verified augmented base. They are
disjoint from the first 2,384, second 134 and independent nontrading six requests.
Preflights:

- `20250127 / 003030.SZ`
- `20251201 / 003018.SZ`
- `20260812 / 600721.SH`

The separate six-pair nontrading collection completed in run `34711597028`,
but this normal raw registration does not consume its supplemental label report.
It continues only the raw chain; original archives and prior registrations stay
immutable. More held-day gaps, if actually observed, require another explicit
deduplicated registration. Training, production activation and profitability
improvement are not implied by source completeness or this registration.

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
