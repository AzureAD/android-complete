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

## Lockdown (run BEFORE outbound notifications!)

1. Run `step-action --release <id> --phase preflight --step lockdown` to get `payload._gather.url` and `payload.followup_command`.
2. Navigate (Playwright) to `payload._gather.url`. **On the Microsoft AAD account picker / sign-in page, click the user's own `@microsoft.com` account tile (`payload._gather.owner_email`) — or enter `<owner_email>` and click Next so Windows WAM SSO signs in automatically without a password — and wait for `"CCOA Periods"` to load!** Never call `fail-step` on the initial AAD redirect before clicking through Windows-SSO.
3. Read all rows from **"Upcoming CCOA periods"** and **"Past NoFly Zones"** for every calendar year covered by the release window (`[{"name","environment","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}, ...]`).
4. Immediately run `check-lockdown --release <id> --periods-json '<json>'` so the engine deterministically evaluates overlap and marks `lockdown` `done`!

## Notices and feature reminders (2-Step Silent Contract)

1. After `lockdown` is recorded, run `step-action --release <id> --phase preflight --step notice` and `step-action --release <id> --phase preflight --step flight_reminder` to get their `mcp_arguments` (which already honor `mocks.local.yaml` test redirects).
2. **When `requires_approval: false` (default):** This is NOT an owner approval step — invoking `/release-agent` already confirms sending these configured release notices. **Do NOT call `m_ask_user`, do NOT paste the email/Teams payloads asking whether to send, and do NOT pause!** Call `workiq_send_email` and `workiq_send_chat_message` directly with exact `mcp_arguments` (only ask via `m_ask_user` if `requires_approval: true` in `config/phases.yaml`).
3. Immediately record each with `record-step --release <id> --phase preflight --step <notice|flight_reminder> --status pass --detail "Sent"`!

## Handoff

Record a supplied result immediately, then follow `work_dispatch` by default to
exhaust eligible agent work. Before asking the owner or handing off a wait, use
`status --details --handoff --json` and print its full status table.
Only explicit one-task/status-only requests remain scoped.
Honor host consent and preserve held/owned operations. A no-progress check returns
a clear repair/wait instead of another queue loop.
