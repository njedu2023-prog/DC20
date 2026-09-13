# Natural publication evidence observer

This NEW research-only workflow immediately follows a successful main/first-attempt
natural candidate freeze. It does not run for the freeze workflow's push-only test
job. The existing P0 publisher, frozen model, accounting, frontend and automation
are unchanged.

The original publication verifier runs through the original GitHub reader. Only
after success are original API bytes, original Pages revision bytes, source Git
bindings, ZIP digests and verification output retained. Large ZIPs, request
credentials and signed download URLs are not persisted. No Tushare requests occur.

One capsule is appended under
`work/profit_1000_upgrade/candidate_natural_evidence/<D>/`:

- Original `manifest.json` and `bodies/<sha256>.bin` from the capture module.
- `context.json` binds the observer run, code head, capture/coordinator/writer
  digests, original freeze run, snapshot, manifest and observation digests.

The writer requires a fresh remote main SHA, exact complete old/new tree
comparison and one non-force Git API CAS. Any existing/partial same-day capsule,
unrelated mutation, expired deadline or concurrent main change stops publication.
There is no force update or automatic retry. The writer has a 128-call budget,
300-second checked elapsed budget and 20-second socket timeout; the workflow has
a separate 15-minute process limit. Maximum capsule: 65 files, 8 MiB per file,
64 MiB plus 64 KiB context. Capsule bodies are content-addressed and immutable.

Local observer writes must acknowledge before T 09:20 China time, providing a
five-minute margin before the independent successful-job T 09:25 check. Host
timestamps and a Git ACK do not themselves issue prospective admission. The
cross-day verifier must bind this exact evidence commit to the original observer
run and its successful job. Synthetic transport output cannot enter the production
coordinator. No actual execution, source qualification, model activation or
positive-return claim is issued here.

The original short-lived P0 Pages ZIP is no longer required after capture. This
version retains its own observer ACK artifact for 90 days; that artifact remains
necessary for independent cross-day qualification. Git-retained original bytes
are durable, but this is NOT a claim that independent qualification works forever
after all provider run/artifact evidence expires. Longer-term cryptographically
verifiable attestation remains a separate requirement and must not be replaced
by trusting a plain persisted status field.

The CLI is `python -m work.profit_1000_upgrade.candidate_natural_observer
--freeze-run-id <actual-run-id> --work-parent <isolated-existing-directory>`.
It defaults to dry run; `--publish` enables only the narrow research Git writes.
It requires the exact main GitHub Actions first-attempt context and the existing
`DC20_CANDIDATE_GITHUB_TOKEN` environment variable. It never prints token values.

Tests are stub-only: original capture verifier transport, coordinator/identity
rejections, append-only Git/CAS race checks, deadlines, post-CAS mutations and
workflow source/permissions checks. They are not evidence of a real D >= 20260914
publication or settlement.
