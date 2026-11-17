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
- `vitals` records the owner's actual Play Console vitals and policy portal review.
- `record-fact` accepts explicit human confirmations independently of positional
  order. The relevant source binding still determines applicability. Unverified
  claims remain visibly unverified; gates do not advance implicitly.

## Notices and feature reminders (Direct Python Delivery — Zero Prompts)

1. Run `python -m orchestrator.cli next --release <id> --deliver --json` (or `deliver-step --release <id> --phase preflight --step notice --json` and `deliver-step --release <id> --phase preflight --step flight_reminder --json`).
2. **When `requires_approval: false` (default):** Python sends both notifications silently via Agency MCP (`mail` / `teams`), applies `mocks.local.yaml` redirects (`[TEST → me]`), and records exact delivery receipts + step completion in one atomic CLI call. **NEVER call `workiq_send_email`, `workiq_send_chat_message`, or `m_ask_user`, and NEVER wrap `next` in a PowerShell `for`/`while` loop!** (Only preview + ask via `m_ask_user` if `requires_approval: true` in `config/phases.yaml`.)

## Lockdown (`preflight.lockdown`)

1. Run `step-action --release <id> --phase preflight --step lockdown` to get `payload._gather.url` and `payload.followup_command`.
2. Navigate (Playwright) to `payload._gather.url`. **On the Microsoft AAD account picker / sign-in page, click the user's own `@microsoft.com` account tile (`payload._gather.owner_email`) — or enter `<owner_email>` and click Next so Windows WAM SSO signs in automatically without a password — and wait for `"CCOA Periods"` to load!** Never call `fail-step` on the initial AAD redirect before clicking through Windows-SSO.
3. Read all rows from **"Upcoming CCOA periods"** and **"Past NoFly Zones"** for every calendar year covered by the release window (`[{"name","environment","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}, ...]`).
4. Immediately run `check-lockdown --release <id> --periods-json '<json>'` so the engine deterministically evaluates overlap and marks `lockdown` `done`!

## Handoff

Record a supplied result immediately, then follow `work_dispatch` by default to
exhaust eligible agent work. Before asking the owner or handing off a wait, use
`status --details --handoff --json` and print its full status table.
Only explicit one-task/status-only requests remain scoped.
Honor host consent and preserve held/owned operations. A no-progress check returns
a clear repair/wait instead of another queue loop.
