# Build and RC verification

Normal phase progression opens at CCD+1. The declared capture dependencies are:
checker, orchestrator health, MRWP ECS, MRWP Local, then Authenticator ECS.
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

## Report: preview, publish, or adopt

`publish-rc-report` defaults to a read-only checked preview of current captured
evidence. Automatic publication binds the exact target and reviewed HTML bytes.
Execute only a supported checked upload handoff, at most once, then verify its
route-specific owning receipt. An unknown upload is reconciled, not replayed.
Reads, registration, and help output do not prove write access.

If the owner already uploaded a report, use the separate `--adopt-file` path with
actual release/RC, selected URL, SHA-256/size, exact resolve/download observations,
owner identity, and explanation. Different filenames/content are supported as an
owner-selected result, not fabricated proof of the original automatic upload.
For an owned prior uploader, obtain explicit stopped-writer confirmation.
Keep the original attempt and uncertainty in history.

Follow `rc_report_notify` to send the selected link through its notification
claim/result protocol. Never treat a link preview or manual upload as message delivery.
The notification identifies owner-selected provenance and uses captured pipeline
evidence for its summary.

## Quality remains a human gate

`rc_report_gate` is a distinct human approve/deny decision after current report
delivery and the independent telemetry prerequisite. Reporting does not approve it,
waive missing evidence, remove RC tags, or authorize publication.

A new RC invalidates its real business consumers. An unchanged readback or cosmetic
report/communication refresh does not erase unrelated completed work.
Leave genuine waits/failures explicit and stop no-progress retries.
