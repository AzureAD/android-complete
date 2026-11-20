# Build and RC verification

Normal phase progression opens at CCD+1. The declared capture dependencies are:
`checker_fired` → `orchestrator_health` → (`mrwp_ecs` and `mrwp_local` in parallel);
`auth_ecs` depends on `mrwp_ecs`; `telemetry_verify` depends on `auth_ecs`;
`rc_report_notify` depends on `[mrwp_ecs, mrwp_local, auth_ecs, telemetry_verify]`; and
`rc_report_gate` depends on `[telemetry_verify, rc_report_notify]`.
Use the returned task/continuation rather than discovering later stages from memory.

An in-progress build is waiting, not an aborted pipeline. A completed run with
unexecuted required stages is a real collection failure. Actual evaluated test
failures are report data; report publication is not a quality approval.

## Capture one operation

`task --release <id> --phase build_verify --step <id>` runs that read-only capture
when its business inputs are available. It does not execute sibling tasks.
Retain exact run IDs, RC identity, source links, and complete mapped test evidence.
Never replace missing source data with a previous RC's successful result.

## Telemetry

Resolve `telemetry_verify` through its returned Kusto request and owning
`record-telemetry` follow-up. Use the captured current Authenticator APK version
and build, not a broker-library or unrelated release-app version.
Positive row count proves flow; zero/failed/stale evidence is not a pass.
The query does not authorize an ad-hoc notification or quality decision.

## RC summary notification (`rc_report_notify`)

Once `auth_ecs` finishes, run `deliver-step --release <id> --phase build_verify --step rc_report_notify --json` (or `next --release <id> --deliver --json`) so Python delivers the RC summary directly via Agency MCP and records completion with no SharePoint upload or confirmation prompt.

## Quality remains a human gate

`rc_report_gate` is a distinct human approve/deny decision after current report
delivery and the independent telemetry prerequisite. Reporting does not approve it,
waive missing evidence, remove RC tags, or authorize publication.

A new RC invalidates its real business consumers. An unchanged readback or cosmetic
report/communication refresh does not erase unrelated completed work.
Leave genuine waits/failures explicit and stop no-progress retries.
