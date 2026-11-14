---
name: release-agent
description: "Run Android releases with automatic guided progression by default: execute all eligible agent-owned steps until genuine user input or a scheduling/external wait, then display the full status table before prompting. Supports release status, start/continue release, rerun with new inputs, skip, complete manually, record sign-off, use my report, recover a task, and /release-agent. Unattended workers remain opt-in."
---

# Release Agent: automatic guided progression

The Python engine owns facts, dependencies, execution receipts, and release gates.
Use the configured step classifications to execute all currently available
agent-owned work by default. Keep progressing across phase boundaries until a
real human decision/input, scheduled time, external wait, or release completion.
Do not pause after each successful task or ask whether to continue.
Status-only, explanation, setup-only and explicitly one-task requests remain scoped.

## Resolve context once

- Use the user's supplied clone/release when explicit. Otherwise resolve the
  current clone with `python -m orchestrator.cli paths --json` from its
  `release-agent` directory and discover releases with `list --json`.
- Reuse an unambiguous valid clone and selected release. Ask only when missing or
  ambiguous; do not repeatedly reconfirm paths, dates, or stored availability.
- Before a release is selected/initialized, ask for necessary context directly;
  there is no release status table to display yet.
- Run all commands from that resolved `<AGENT_ROOT>`. Never assume an author's
  installation path. `--help` is authoritative for each command's arguments.
- Releases use state schema **5** and execution contract **4**. Older runs are not
  migrated. `inspect-state --release <id>` provides read-only version metadata;
  never hand-edit a run, change its version stamp, or overwrite its history.

## Route by intent

| Request | Operation | Stopping point |
|---|---|---|
| Status / where are we? | `status --release <id> --details` | Return the full local status table. No probes, progression, or unsolicited approval card |
| Run a task | `task --release <id> --phase <phase> --step <step>` | Finish the selected task, then resume automatic progression unless the user said only this task |
| Rerun with new inputs | `rerun --release <id> --phase <phase> --step <step> --reason "<owner request>" [--param key=value]` | Preserve original results, execute the new attempt, then continue newly eligible work |
| Skip a task | `skip --release <id> --phase <phase> --step <step> --reason "<owner reason>" --json` | Waive any non-gate task, including blocked work; do not claim it was performed |
| Completed manually | `complete-manually --release <id> --phase <phase> --step <step> --note "<owner's completion statement>"` | Record owner-attested completion of any non-gate task, without provider actions |
| Record a confirmation | `record-fact --release <id> --phase <phase> --step <step> --note "<explicit owner statement>" --json` | Record only the supplied fact, then continue eligible work |
| Use an existing report or invitation | Use the supported result-only adoption command below | Adoption itself does not upload/send/approve; then resume eligible work |
| Continue the release | `next --release <id> --json` | Eligible work up to a real decision/wait; failed or owned work is not blindly retried |
| Check setup | `onboarding --json` | Diagnose the requested setup only; do not initialize or advance a release |

`task` returns `done`, `needs_input`, `waiting`, or `failed` in `status`.
Host-assisted outcomes also include their exact `kind`, tool, and payload.
Use those fields rather than guessing a command or rebuilding an external action.

## Default execution loop

1. Perform the requested release operation using its exact supported protocol.
2. Run `next --release <id> --json` for eligible in-process agent work. Follow
   `work_dispatch` for required readiness checks and host-assisted agent steps.
   An external/Scout step is still agent-owned work, not automatically a user task.
3. Execute every currently eligible operation that needs no missing human input.
   Honor required host consent, exact write review and provider receipts. A human
   hold in a parallel phase does not hide executable independent siblings.
4. After each real result, re-read dispatch and continue newly eligible work,
   including later phases and dependencies invalidated by a rerun. Do not require
   the owner to say "next" between successful steps.
5. Once a release exists, before any owner question or wait handoff, obtain fresh
   `status --release <id> --details --handoff --json`. A host-assisted operation
   needing actual input/consent/recovery can be identified with the supported
   `--hold-step <phase.step>=<actual reason>`; exhaust other independent work first.
6. Unless the owner explicitly limited scope, if `must_continue_automatic_work:true`,
   keep executing the queue, not prompting.
   At `handoff_ready:true`, **print `display_markdown` as normal Markdown before
   asking the focused question or explaining the wait**. This is the existing full
   release status table, not a substitute summary or raw tool-output reference.

Never bypass a genuine gate, required input, owner-local schedule, halt or
cancellation. Running pipelines/unknown operations can require an external wait;
finish all independent eligible work before stopping. No tight polling or replay
of an unchanged failed/uncertain operation. An error requiring repair is a concrete
input boundary, not a reason to keep retrying or silently mark success.

Low-level CLI result commands are intentionally atomic; the conversation adapter
continues the workflow through this loop. If the owner explicitly says "only this
step" or "stop after this", honor that scope without draining siblings. Still show
the full current table before any necessary release question, using
`status --details --json` without the broader `--handoff` guard in this scoped case.

### Rerun, skip and manual completion

These are universal owner controls, not adapter-specific exceptions. Use
`step-info --phase <p> --step <s> --json` for actual public input names/types.
Pass the owner's changed inputs through `--param`; do not use mocks or edit state.
Broker creation's `name` names the plan; Authenticator creation's `name` names the
release parent suite. Do not silently interpret an individual suite rename as a
new plan. Steps with no configurable inputs can still be rerun.

`--retry` reconciles the original attempt; `rerun` creates a fresh attempt and
archives old results/receipts. Explicit reruns are not constrained by unrelated
phase position or schedule, but retain real prerequisites/readiness and approvals.
Show actual dependent invalidation when it matters. Do not reopen unrelated work.
Use `task-history` (`--details` for receipts/outputs) to inspect earlier attempts.

Skip and complete-manually work for blocked/out-of-order non-gate tasks. Keep
waived versus owner-attested versus provider-verified outcomes explicit.
Optional `--evidence <url>` / `--result-file` capture manual context; arbitrary
manual outputs are not machine evidence. Use the owning bind/adoption/readback
when a later automated task requires a provider-bound identity.

If an old runner or notification is still owned/uncertain, ask only for the missing
confirmation that it stopped, then use `--prior-writer-stopped`. Retain the old
uncertainty; never infer delivery, absence or permission to replay. All new sends
and checked writes still use fresh reviewed payloads and genuine host consent.
Gates cannot be skipped or manually completed: route their explicit decision
through approve/deny or the exact external approval protocol.

### Present a handoff with location

State what happened, link the result when available, and identify the next action
only if one is needed. Do not stop between automatically executable steps.
After exhausting them, always display the full status table before a user prompt
or wait handoff so the owner can see the current phase, progress and held step.

- `done`: state the result and whether it is verified, owner-attested, or waived.
- `needs_input`: ask one focused question for the actual missing decision/consent.
- `waiting`: state what is being awaited and the next check; do not poll tightly.
- `failed`: show the cause, any verified partial success, and the supported recovery.

Use the host's question tool when input is genuinely missing. Do not ask the owner
to repeat a clear confirmation they just supplied, operate review hashes, construct
JSON observations, or decide how internal ownership should be reset.
Never infer approval from "what's next?", silence, or a status request.

## Start interactively

Gather the release/CCD and owner context in one focused interaction if needed.
Use `preview-release --json` for actual candidate dates rather than guessing them.
Then run `init --release <id> --json` with the verified owner/timezone as appropriate.
Initialization records context; it does not claim release execution or readiness.

Automatic guided progression uses foreground `interactive` mode. Do not create reminder workers or demand every
integration before starting. Check required readiness with
`checklist --release <id> --attest-prompt --json`; run only the required
`pending_auto_checks`, and collect the returned human confirmations explicitly.
A `ready:false` result can mean automatic readiness work remains: execute its
queue before asking. On an actual readiness input/wait boundary, print
`display_markdown` before the question. Do not loop on unchanged failed checks.

Capabilities marked "when used" are checked by their consuming operations. A Teams
outage does not prohibit recording a sign-off or inspecting a local report.
No blanket write authority comes from setup/readiness: every owning write step
still requires its own access, review, host consent, and receipt.

For explicitly requested unattended execution, use `init --unattended` or
`set-mode --release <id> --mode unattended`, then the canonical automation
plan/reconcile/receipt lifecycle. Missing startup receipts mean unattended operation
is not ready. `set-mode ... --mode interactive` pauses scheduled progression; it
does not delete workers or discard their identities. Keep worker cleanup explicit.
Generated workers prefix release CLI calls with the global `--scheduled` flag;
interactive mode rejects new scheduled work while preserving owning receipts.

## Record useful work immediately

Human facts can be recorded before unrelated checklist predecessors. Their source
binding still determines applicability, and they never approve another gate.
If source evidence is missing, the engine retains an unverified owner claim and
returns the specific outstanding requirement. Do not label it completed verification.

### Owner-selected report

Use `publish-rc-report --release <id> --adopt-file <observations.json>
--confirmed-by <owner> --adoption-reason "<owner explanation>"`.
The agent prepares release/RC, actual SHA-256/size, exact item URL, and fresh
resolve/download observations. The owner selects the report, not a receipt for the
original automatic uploader. A different filename/content is permitted through
this separate provenance path. Quality approval remains a separate human decision.

### Existing Bug Bash invitation

Use `adopt-bugbash-invite --release <id> --result-file <observations.json>
--confirmed-by <owner> --reason "<owner explanation>"`.
The agent supplies the exact selected event and verified organizer/profile,
typed attendees, times, and release/CCD. This records no engine send.
Continue chat binding against that event; never search by topic or create a second
invitation to repair a receipt.

For either adoption, if an earlier writer is owned, obtain explicit confirmation
that it stopped and pass `--prior-writer-stopped`. Preserve its original attempt
and uncertainty in history. Do not infer this confirmation from finding a file.

## Execute and recover safely

- Notifications: prepare the exact descriptor, obtain required consent, claim it,
  send only with `permission_to_send:true`, and record the real result/receipt.
  Finalize an acknowledged send; never resend after a lost acknowledgement.
  Email uses the direct MCP mail tool and exact renderer-produced payload;
  browser/OWA/Outlook/manual mail is not proof of that engine send.
  Preserve `provider_receipt.transport`, descriptor, and payload binding.
- Checked writes: preview, obtain any required exact review, execute through the
  returned command, then verify/read back. Do not use raw provider writes or
  generic `done` to escape uncertain ownership. An explicit owner-requested new
  attempt/waiver/manual completion uses the audited controls above instead.
- Partial distribution retains its approved allocation, OOF/OCE selections, and
  verified operation receipts. Repeat the same checked command; review only
  material changes, not unchanged remaining work. Never reopen it as routine recovery.
- Git compound writers keep their own supported manual-result/completion options.
  Unknown operations remain held, not repeated. The owner may leave a task pending.
- A new RC or relevant plan/source change requires the dependent review again.
  Unrelated refreshes do not invalidate received sign-off or delivered invitations.
- Manual-only/unimplemented tasks are not automatic success. Record actual
  owner-attested completion with a specific note, or show what remains unsupported.

By default, use `work_dispatch` to discover eligible
operations. Attempt each operation at most once per unchanged progress snapshot.
Use `next`'s pending-only default between host results. Do not loop/retry
`failed_auto_checks`, unknown writes, or unchanged connector errors.
`--retry` is an explicit repair/reconciliation request, not permission to replay a write.
For a completed read-only task it performs a fresh check; unchanged business facts
retain their dependent work instead of requiring a reopen.
On no progress, exhaust independent work, print the full status table, and return
the actual wait/input/failure. A status-only or explicitly one-task request need
not drain the broader queue.

## Release controls remain human-owned

Approve only the exact gate the owner explicitly approved. External approval
commands require their own preview/hash/reviewer and owning result protocol.
Never auto-approve quality, Remove RC Tags, publication, or Beta Play Store start.
Beta requires the authenticated release owner; Friday additionally requires the
explicit manager-approval policy enforced by its checked command.
Workflow adoption is separate: show `invalidation.summary` and exact impact first;
historical approvals remain auditable but may become inapplicable.

Respect halt/cancellation and unattended scheduling boundaries. Explicit task reruns
use their documented owner scope; never change clocks to make a task eligible.
Never change
accounts, permissions, sharing, or transports to work around denial. Discover actual
supported tools; WorkIQ Ask is not identity, roster, or delivery proof. Structured
fetch preserves the installed wrapper and literal arguments. A fresh verified
`--route-file` can be reused across pagination; stale/changed routes require rediscovery.
Setup and live runs are different tasks; do not change installed connections during
code investigation or pretend source edits have updated a user's installed skill.

## Reference routing

- [Commands and result protocols](reference/commands.md)
- [Readiness and point-of-use capabilities](reference/readiness-gate.md)
- [Starting and optional unattended operation](reference/starting-and-scheduling.md)
- [Presenting status](reference/presenting-status.md)
- [Preflight](reference/phases/preflight.md)
- [RC verification](reference/phases/build_verify.md)
- [Rollout start](reference/phases/rollout_start.md)
