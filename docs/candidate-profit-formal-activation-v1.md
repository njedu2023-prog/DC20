# Fixed candidate profit model: explicit formal activation

The user authorized formal connection before waiting for natural forward return
evidence on 2026-09-14. `models/decision_candidate_profit_activation_v1.json`
records that new scope. It is not an edit of the original research registration,
model, source/label kernel, or historical return record. The enabled adapter
changes only the formal profit ranking and its independently versioned shadow
presentation from D 2026-09-14 onward. Existing promotion ranks are preserved.

`src/top10decision/decision/candidate_profit_publication.py` is read-only: it
creates in-memory JSON objects/bytes and never writes Git, trades, trains,
collects quotes, settles outcomes, or claims proven profitability.

## Entry points

- `load_activation(path=None)`, `validate_activation(document)`, and
  `activation_identity(document)` check the explicit policy and expose its
  four presentation identity fields. Only `enabled` may be toggled for rollback;
  changing the frozen model/date/policy requires a new reviewed version.
- `project_verified_day(snapshot_raw=..., publication_proof=...,
  current_p0_raw=..., activation=..., source_main_sha=...)` requires the exact
  private `VerifiedResearchPublication` type. A dict with `admitted=true`,
  injected transport report, test clock, wrong model, or wrong-D P0 cannot
  authorize a new public day.
- `validate_cached_day(raw, expected_sha256=..., snapshot_raw=...,
  current_p0_raw=..., publication_proof=..., activation=...)` rechecks an
  **already existing** immutable Git-held public day against the original
  snapshot/proof/P0. It does not retimestamp or reproject the day.
- `validate_persisted_day(raw, expected_sha256=..., snapshot_raw=...,
  current_p0_raw=..., activation=...)` checks old day/snapshot/P0 consistency
  **without issuing source admission**. A publisher may use it for previously
  Git-published days whose two original public-summary slots are already
  terminal, or genuinely `MISSING_CANDIDATE`. It must SHA-bind the original
  summary and day from the current verified Git tree, preserve their exact
  economics, and never use this path for pending quote evidence, new outcomes,
  or a new D. Absent candidates stay null, never settled or zero-return slots.
- `build_public_index(days, activation=..., source_main_sha=...,
  generated_at_utc=...)` lists SHA-bound immutable daily JSON objects. An enabled
  empty index means `ACTIVE_WAITING_FIRST_NATURAL_D`, never a fabricated D list
  or successful settlement.
- `validate_public_day` and `validate_public_index` require an external file
  SHA. They are structural/cache-integrity validators, not admission issuers.

The caller must independently verify `source_main_sha`, all checkout bytes and
the parent Git tree, and perform an atomic compare-and-swap for new public
files. Fresh formal days are accepted only after D close and strictly before
T 09:20 Shanghai time. The returned `formal_publication_deadline_utc` is that
safety deadline; the Git writer must check it again immediately before CAS.
An old D whose first formal publication missed this deadline remains missing;
it must not be backdated or prevent subsequent timely days from publishing.

## Public objects

`outputs/decision/candidate_profit_v1/index.json` uses
`dc20_candidate_formal_profit_index_v1`; it has the activation identity both
at the top level and in `activation`. Its `days` entries contain exact D path,
public file SHA256, original snapshot SHA256 and original three-rank P0 SHA256.

`day_<D>.json` uses `dc20_candidate_formal_profit_day_v1`. It retains original
prediction/freezing times, records a separate projection generation time, and
includes independently observed provenance. Rows contain stock code, name,
industry, original promotion rank and fixed candidate score/rank. Scores are
not probabilities. All scores, including negatives, are preserved.

Exactly two candidate slots and two promotion reference slots are copied from
the original frozen snapshot. A zero- or one-candidate day contains explicit
`MISSING_CANDIDATE` slots, not invented stocks or zero returns. Daily rank
objects contain no subsequent outcomes; independently verified journal results
belong in the separate `summary.json` and must not merge legacy model returns.

Setting `enabled=false` blocks new day projection. Existing day files, their
timestamps and SHA bindings remain intact. This is a deliberate configuration
rollback, not permission for automatic fallback when a new-model day is pending.

## Verification

Run `PYTHONPATH=src:. python -m pytest tests/test_candidate_profit_publication.py`.
The tests monkeypatch the proof-type boundary explicitly and use synthetic P0
source envelopes with the unchanged fixed model; they do not constitute real
publication, actual broker fills, or natural T/T+1 settlement evidence.
