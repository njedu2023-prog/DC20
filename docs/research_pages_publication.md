# Independent research Pages publication

The formal publisher publishes the verified P0, fixed Ridge projection and account
first. Research publication uses `profit_research_pages.yml`, a separate real
`workflow_dispatch` run whose GitHub head is the research commit being deployed.
This avoids two different Pages bundles sharing the caller run's build version.
The existing pinned Pages builder and all frozen data remain unchanged.

Normal research runs dispatch and track one child after their successful research
CAS. A push runs checks only; it does not collect or publish research. The existing
research schedule, workflow call and manual entry remain available.

For an existing research CAS whose later Pages failed, the manual child accepts:

* `expected_head`: the actual current main SHA; it must equal the new run's head.
* `signal_date`: the exact research date, YYYYMMDD.
* `source_revision`: the source revision recorded by the existing dataset.
* `source_run_id`: the actual producer run with a successful research CAS job.
* `wait_for_deployment` and `wait_expected_head`: an optional pair identifying the
  real previous Pages deployment and the exact public head it was meant to publish.

The guard binds the original CAS to the successful source job's real time window,
its single source parent, current-head ancestry and matching pointer/version Git
blobs. A subsequent code-only repair may be deployed using its real main SHA; the
original research commit, source or run identity must never be fabricated.

The prior backend is read with the runner's scoped token every 60 seconds for up
to 20 minutes. A successful backend status also requires the expected ordinary
public revision. A terminal failure permits one safe new deployment attempt; it
does not prove that a reused deployment ID exposed an independent attempt record.
Unknown statuses, API errors and incomplete pagination fail closed. A timeout is
an unresolved publication, not completion. The guard never cancels a deployment.

Dispatch uncertainty is reconciled against real API runs, without resending.
Existing queued or running matching children are followed. Failed children are
reported for cause-specific repair, not automatically retried. Do not regenerate
P0, rewrite freezes, manufacture a commit, or cancel normal work to force Pages.
Final acceptance still checks the exact public head, research date, source and
pointer/version bytes. A green deployment step alone is insufficient.
