# Flexy: verification and operations

Reviewed against [default-branch commit 6d95bd071313](https://github.com/noel-ult/Flexy/commit/6d95bd0713136c67fb1fc61366e73e0bfff7bb31) on 2026-10-28. Start with the [developer guide](PROJECT_GUIDE.md) for installation and architecture, and the [source reference](REFERENCE.md) for exact scripts, configuration readers, routes, and source locations.

## Job lifecycle and evidence

Use supported/unsupported fixtures to verify inspection, capability-scoped status/events, supported build execution, download-token behavior, and expiry. Check healthz and readyz separately.

These are acceptance scenarios for a maintainer to run in the documented environment. Their inclusion is not a claim that live integrations or production credentials were tested during this documentation update.

## Deployment and failure diagnosis

Keep runner isolation and executor availability explicit. Package output is separate evidence from installation, launch, or application function. Preserve service volumes before migrations.

## Maintaining the contract

When behavior changes, update the guide and examples in the same PR. Check the entry point and working directory before copying commands. Use the linked source readers to distinguish build-time configuration from process environment. For failures, capture the first application error, runtime version, selected mode, and redacted configuration names before later proxy or port errors.

Keep changes tied to evidence: a declared command is not proof it passes, a process health response is not a successful end-to-end workflow, and a historical report describes its own revision. Record actual check results and any unavailable external services in the PR.
