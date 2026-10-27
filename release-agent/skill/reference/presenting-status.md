# Reference — Presenting status & the checklist

_Loaded on demand. How to render the CLI's `status` / `checklist` output._

> **Render as markdown, never as a code block.** Both `checklist` and `status` outputs are already markdown (headings + tables). Paste them into your reply as **normal message content** so Scout renders the table/stepper — do **NOT** wrap them in a ``` code fence. Fencing shows raw text instead of a rendered table. Reproduce faithfully, as live markdown. (This is the core "render CLI output" golden rule — detailed here.)
>
> **Hard stop on fenced status output:** if your draft contains ```` ```markdown ```` or any code fence around the status/checklist block, do not send it. Remove the fence and paste the same block as live markdown.

**First exhaust work, then decide what to show.** If entry is unsigned, follow the
shared readiness dispatch returned by init/status/next/checklist. Ordinary
`checklist --verify` runs Python verifiers ONLY; its table is not a terminal result.
Execute all pending Scout probes and record real outcomes. Defer skill-generated
permission/input questions until independent probes are exhausted (`needs_input`
records the exact deferred reason). Preserve real host consent and explicit release holds.
Then run `checklist --release <id> --attest-prompt --handoff --json`: exit 2 or
`may_return_to_user:false` means continue the queue, not display-and-stop.
Only a permitted handoff supplies the final full checklist and real owner question.
After entry clears, use the normal `status` block.

**All phases share the same guarded handoff.** On a release-advancing turn, inspect
`work_dispatch.pending_work` from status/next/step-action/tick and execute every
eligible independent item through its owning protocol before an owner decision.
The `action`/`gate` focus never overrides that queue. Between host results use
`next --pending-only` to avoid rerunning recorded failures or in-flight work.
Then run `status --release <id> --handoff --json --no-pipeline-check`.
Exit 2 / `work_dispatch.may_return_to_user:false` refuses an early final response.
For actual Scout input/consent/recovery needs, finish independent work and pass
`--hold-step "<phase.step>=<actual reason>"` to that same guard. These are transient,
host-reported holds, not verified receipts, state completion or permission.
Show deferred reasons with the full table, then ask. Never defer unattempted engine
work or fabricate a blocker. Real host consent and global release holds retain priority.
Startup automation entries (`kind:automation`, key `automation:<slug>`) are part of
that queue. `startup_automation.complete:false` means NEVER claim the release is
running unattended, even if readiness is signed and phases are future-dated.
Show the mandatory driver/provisioning or owning recovery requirement explicitly.
For an ordinary deleted settled driver, execute the idempotent --ensure repair;
do not return only "driver missing" or ask the owner to clear a registry file.
At each operational entry inspect the actual provider list/details; never call a
cached registry id "running" without its fresh check. Unknown operations remain held.
An actual startup input hold uses `--hold-step "automation:<slug>=<actual reason>"`
and remains host-reported/incomplete. Active registry receipt is not a live-provider check.

**Mid-release status:** run `status --release <id>` (no `--json`) and show its output as rendered markdown — a next-action headline, a **phase map** (✅ done · ⏸ in progress · 📅 scheduled · ⬜ not started), and the **current phase's steps** table. It logs emitted output, not proof of display in the conversation. Add a sentence before/after, but reproduce the block faithfully; don't re-render from `--json` (that skips the auto-log) and don't invent a layout. Never surface raw engine state names (`holding_gate`, `awaiting_action`, `scheduled`) — the view already translates them ("Waiting for your approval", "Action needed from you", "Scheduled").
For explicit `/release-agent show status` requests, this rule is mandatory: the only valid response body is the complete markdown block emitted by `status --release <id>` plus optional surrounding prose. A condensed substitute status table is invalid.

**Every release iteration ends with one fresh table.** This means every user-facing
release turn before a final response or `m_ask_user`, not just a successful advance:
initialization, entry checks, no-op/scheduled requests, failure, interruption, declined
confirmation, manual recovery/completion and status questions all need current context.
Acquire it after the last action/read and paste it in THIS assistant response.
Neither a tool-output pane nor an attestation card proves the table was shown.
If work pauses before checks finish, show the full current checklist including pending/
failing rows. If no table can be rendered, report that exact error and withhold approval/
attestation prompts; never fabricate or reuse a stale table. Setup-only onboarding
without a selected release is exempt.

For readiness prompts, `checklist --release <id> --attest-prompt --handoff --json` bundles the
canonical table in `display_markdown` with its question and answers. Paste that field
once, then ask for attestation only if `ready:true`. A `ready:false` result is not a
display exemption at a genuine boundary, nor permission to stop while the queue is executable.
Never replace `display_markdown` with a condensed/custom variant. If your reply contains a
table that did not come verbatim from `display_markdown`, treat it as invalid and re-render
the canonical block before asking anything.

Alongside the table, tell the user exactly what each outstanding step needs (run the scout steps yourself; for `attest` steps spell out what to confirm). Do **not** replace the table with your own summarized list.

When the status has a focused `action`/`needs_owner` step—or the user asks **“what’s
next?”**—the response must be actionable, not informational only. Handle one item:

- **Attestation/human action:** run `step-action` for the exact prompt, show the fresh
  status table, then use `m_ask_user` choices **Completed**, **Not yet**, **Need help**.
  Completed authorizes `done` with a specific confirmation note; Not yet leaves the
  hold unchanged; Need help routes through `step-info` and then repeats the choices.
- **Owned work:** check the original provider result using its receipt protocol.
  Say what Scout is checking and whether the owner needs to decide anything.
  Do not show hashes, execution IDs, or reset commands as tasks for the owner.
  Never repeat an unknown write, message or approval just to make progress.
- **Partial Git work after a branch moves:** report what already exists and what remains.
  Use the same checked command to reconcile and refresh the remaining plan; preserve
  verified PRs, the shared backlog item and branch updates. Ask only for an actual changed
  human-reviewed action, not a recovery hash or execution ID. If readback cannot resolve
  an attempt, offer recording a manual result, confirming an unapplied stopped push,
  or explicit owner-attested completion of ALL remaining work with evidence. The last
  path needs no provider API or checkout. Explain that it is owner-attested, not
  machine-verified; it does not approve another gate. Never infer the confirmation.
- **Blocked check:** show the reason, then offer **Fixed — rerun**, **Override**,
  **Keep blocked**. Override requires a separate free-text reason before `skip`.

If the owner action is discrete, do not use prose-only instructions. The same turn must
call `m_ask_user` with explicit options. A message ending in "please review/confirm"
without an options card is invalid.

Never treat “what’s next?” as completion. After a confirmed action, advance, render the
new status, and prompt for the next focused action. Process multiple holds one at a time.

**Render once per iteration — after the work, not before.** Do the work first, then
paste the complete resulting `status` or checklist table once before returning or
asking the next question. This is mandatory even when no action succeeded. Don't
paste a pre-work table and then a second one afterward; do not suppress the final
table to avoid repetition across turns. One iteration → one fresh table.

**Structured fields for branching** (`status --json`):
`release_id, status, ccd, ccd_source, as_of, done, total, percent, current_phase_name, current_step_name, gate, action, scheduled, pending_human, readiness_signed, blocked`.
