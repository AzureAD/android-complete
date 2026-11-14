# Preflight tasks

Preflight is a parallel phase. A whole-release `next` runs eligible fresh automatic
work once; a scoped `task` runs only the requested operation. Do not retry a failed
CG/access check or send unrelated notices because the owner asked for status.

## Checks and human facts

- `breaking`, `cg`, and `cron` are read-only automatic checks. Use `task --phase
  preflight --step <id>` to inspect one. A failed check gives its actual repair;
  retry only after repair or an explicit retry request.
- `oneauth_access` is an engine-owned effect with durable recovery. Never clear
  its ownership or probe through a different identity after denial.
- `confirm_reminders` records what feature owners actually completed. Sending a
  reminder is not proof of that work. `vitals` records the owner's actual portal review.
- `record-fact` accepts explicit human confirmations independently of positional
  order. The relevant source binding still determines applicability. Unverified
  claims remain visibly unverified; gates do not advance implicitly.

## Notices and feature reminders

Use the shared notification protocol from [commands](../commands.md).
The module supplies configured recipients and exact payloads; do not hardcode them.
Preview, obtain required consent, claim, send once with permission, and record the
real provider receipt. A wording variant is not permission for another acknowledged send.
After lost acknowledgement retry result/finalize, not the message.

## Lockdown

The task returns the exact gather URL/window and `check-lockdown` follow-up.
Use supported browser access to read complete Production CCOA periods for every
calendar year covered by the release window. Verify table completeness before using
an empty list. Missing pages, SSO/access failures, malformed dates, or reversed ranges
are errors, not "no overlap."

The engine computes overlap. If it finds one, show the actual periods/window and
the supported CCD-change decision. Do not change dates, permissions, or accounts
to make the check pass.

## Handoff

Record a supplied result immediately, then follow `work_dispatch` by default to
exhaust eligible agent work. Before asking the owner or handing off a wait, use
`status --details --handoff --json` and print its full status table.
Only explicit one-task/status-only requests remain scoped.
Honor host consent and preserve held/owned operations. A no-progress check returns
a clear repair/wait instead of another queue loop.
