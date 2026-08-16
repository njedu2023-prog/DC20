# Security Policy

## Supported code

Security fixes are applied to the current `main` branch. Historical reports, model snapshots,
and frozen research artifacts are retained as evidence and are not independently supported
release lines.

## Reporting a vulnerability

Do not disclose a vulnerability, credential, market-data token, private dataset, or exploit in
a public issue.

Use the repository's **Security → Advisories → Report a vulnerability** flow when it is available.
If private vulnerability reporting is unavailable, contact the repository owner through the
owner's GitHub profile and request a private reporting channel without including sensitive
details in public.

Include, when safe:

- the affected file, workflow, or public URL;
- impact and reproducible steps;
- the least-sensitive proof of concept possible;
- whether a credential or `TUSHARE_TOKEN` may have been exposed;
- suggested remediation, if known.

The target is to acknowledge a complete report within five business days and provide a status
update within ten business days. Complex fixes may take longer.

## Scope notes

- Never commit real tokens or copy them into logs, artifacts, reports, screenshots, or issues.
- Trading outputs are research guidance only; they are not broker execution or a guarantee of return.
- Data-quality or model-performance disagreements without a security impact should use a normal issue.

