# Presenting status and operation results

For a status request, use `status --release <id> --details` for the full phase
map/checklist, or `--json` for machine branching. Omit `--details` only when the
owner specifically wants a short brief.
Status does not probe providers, advance phases, record a decision, or log a new
status event. Never turn a status request into a consent/approval questionnaire.

For a task, use the result's `status`: `done`, `needs_input`, `waiting`, or `failed`.
Show what happened, a result/link where available, and the exact missing next action.
Keep verified, owner-attested, waived, and manual-only work distinguishable.
A percentage measures checklist completion, not production rollout performed.

At every owner-input or wait boundary, **always print the existing full status
table before the question/wait explanation**. Use the fresh handoff report's
`display_markdown`, rendered as normal Markdown, not a code fence or raw JSON.
Tool output is not proof the owner saw the table. Do not fabricate statuses,
stale results, links, receipts or approvals.
`ready:false` alone is not a user-input boundary: consult dispatch and finish
executable checks before showing an actual input/repair/wait.

On a real decision, provide enough current context for that decision and use the
host's question tool. Ask only for missing information, not a confirmation the owner
already clearly gave. After recording it, resume automatic guided progression
unless the owner explicitly limited the scope.
Offer rerun with changed inputs, skip, or completed-manually when the owner wants
those controls, not merely another retry of the same failed attempt. Use the
selected task's actual public inputs and preserve verification provenance.
An uncertain old writer needs a stopped-runner confirmation, not fabricated
success. Approval gates remain genuine decisions, not manual-completion toggles.

Automatic guided progression is the default for release actions: use `next --json`
and `work_dispatch` to exhaust executable agent-owned work, rechecking after each
result. Human holds do not veto independent siblings. Stop only for genuine
input/consent/repair, a scheduling/external wait, or completion; never ask "continue?"
between successful steps. At a handoff, use `status --details --handoff --json`;
identify genuine host input boundaries with exact `--hold-step` reasons when needed.
Preserve actual consent and ownership. Do not replay an unchanged failed operation.
Status-only and explicit "only this step" requests do not drain the broader queue.

In unattended mode, show a missing driver's startup receipt/health as unavailable
coverage. Do not claim unattended success. Interactive operation does not require
that driver, recreate it, or delete it implicitly.
