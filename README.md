# DC2.0 Decision Research

**DC2.0** is the public display name of this independent Decision research system.
The stable GitHub repository slug remains `DC20`.

DC2.0 is an isolated research copy of the Decision subsystem from
[`njedu2023-prog/top10-decision`](https://github.com/njedu2023-prog/top10-decision).

- Production remains in `top10-decision` and is not modified by DC2.0.
- DC2.0 owns its code, models, data, outputs, schedules, validation, and Pages deployment.
- Premium code, workflows, models, data, reports, and tests are intentionally excluded.
- The copied production model remains frozen until a reviewed change in this isolated repository explicitly releases it.
- Existing schema names, V11/V12/V13 model identifiers, freeze identifiers, repository paths, cache keys, and concurrency groups are technical contracts and remain unchanged.

## URLs

- Repository: <https://github.com/njedu2023-prog/DC20>
- Dashboard: <https://njedu2023-prog.github.io/DC20/>
- Direct dashboard entry: <https://njedu2023-prog.github.io/DC20/decision.html>
- Published revision evidence: <https://njedu2023-prog.github.io/DC20/revision.json>
- Original production dashboard: <https://njedu2023-prog.github.io/top10-decision/decision.html>

## Isolation Contract

DC2.0 workflows only write to the `njedu2023-prog/DC20` repository. They never update the source repository.
The dashboard reads `outputs/decision` from `DC20` and retains the existing `DC20` browser cache key.
The strict A-share trading calendar and 9:25 pre-freeze Decision contract are retained.

Repository secrets are isolated by GitHub. `TUSHARE_TOKEN` must be available to `DC20` for
minute-level and close-truth synchronization; `GITHUB_TOKEN` is supplied automatically by
GitHub Actions.

