# DC20 Decision Research

DC20 is an independent research copy of the Decision subsystem from
[`njedu2023-prog/top10-decision`](https://github.com/njedu2023-prog/top10-decision).

- Production remains in `top10-decision` and is not modified by DC20.
- DC20 owns its code, models, data, outputs, schedules, validation, and Pages deployment.
- Premium code, workflows, models, data, reports, and tests are intentionally excluded.
- The copied production model remains frozen until a DC20-only reviewed change explicitly releases it.

## URLs

- Repository: <https://github.com/njedu2023-prog/DC20>
- Dashboard: <https://njedu2023-prog.github.io/DC20/decision.html>
- Original production dashboard: <https://njedu2023-prog.github.io/top10-decision/decision.html>

## Isolation Contract

DC20 workflows only write to the DC20 repository. They never update the source repository.
The dashboard reads `outputs/decision` from DC20 and uses a DC20-specific browser cache key.
The strict A-share trading calendar and 9:25 pre-freeze Decision contract are retained.

Repository secrets are isolated by GitHub. `TUSHARE_TOKEN` must be available to DC20 for
minute-level and close-truth synchronization; `GITHUB_TOKEN` is supplied automatically by
GitHub Actions.
