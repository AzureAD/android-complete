# Commands and result protocols

Run from the resolved `<AGENT_ROOT>`. `python -m orchestrator.cli <command> --help`
is authoritative; do not ask owners to operate command flags, hashes, or JSON.
The agent handles those details from actual engine/tool responses.

## Main intents

| Intent | Command |
|---|---|
| Discover paths/releases | `paths --json`; `list --json` |
| Initialize interactively | `init --release <id> --json` |
| Inspect local state | `status --release <id> [--details] [--json]` |
| Execute one operation | `task --release <id> --phase <p> --step <s> [--param name=value]` |
| Fresh attempt with changed inputs | `rerun --release <id> --phase <p> --step <s> --reason "<owner request>" [--param key=value]` |
| Skip blocked/out-of-order work | `skip --release <id> --phase <p> --step <s> --reason "<owner reason>" --json` |
| Record actual manual completion | `complete-manually --release <id> --phase <p> --step <s> --note "<owner statement>" [--evidence <url>]` |
| Inspect prior attempts | `task-history --release <id> --phase <p> --step <s> [--details]` |
| Discover valid task inputs | `step-info --phase <p> --step <s> --json` |
| Record a fact/manual-only completion | `record-fact --release <id> --phase <p> --step <s> --note "<owner confirmation>" --json` |
| Continue fresh work | `next --release <id> --json` |
| Explicit retry/reconciliation after repair | `task ... --retry` or `next --release <id> --retry`; use the owning result protocol for writes |
| Inspect unsupported version metadata | `inspect-state --release <id>` |
| Required readiness | `checklist --release <id> --attest-prompt --json` |
| Record named readiness confirmations | `sign --release <id> --item <id> --note "<owner confirmation>"` |
| Optional setup diagnostics | `onboarding --json` |
| Select execution mode | `set-mode --release <id> --mode interactive|unattended` |

`done` aliases fact recording. Low-level commands remain atomic; by default the
conversation adapter continues through `next --json` and `work_dispatch` after
recording, approving, rerunning or waiving work. Do not ask whether to continue
between successful steps. Status alone does not trigger provider reads/progression.

Exhaust all eligible agent-owned work before a question or wait, including
host-assisted steps. Preserve classification, dependencies, genuine consent and
schedule. Fetch `status --details --handoff --json` at each handoff and print its
`display_markdown` before the prompt. `must_continue_automatic_work:true` means
the queue is not exhausted. Exact `--hold-step` reasons identify real host input
boundaries without falsifying completion. Explicit "only this step" and status-only
requests do not drain the broader queue.

## Uniform owner controls

All configured steps support a new attempt (`rerun`, also `task --rerun`).
All non-gate steps support skip and complete-manually, regardless of blocked status
or unrelated phase position. Gates retain their real approve/deny/provider-receipt
requirements. A halted/cancelled release must first be resumed/reactivated.

Use the typed `owner_controls.parameters` from `step-info`; unknown parameters
fail before changing state. Effectful task parameters belong to prepare, not execute.
Omitted inputs reuse previous selections; only changed values need to be supplied.
The fresh attempt retains original results, frozen inputs/receipts and resource
bindings in task-history; original external resources are not renamed/deleted.
Broker `--param "name=New plan"` selects a new plan identity, not an individual
suite rename. Authenticator creation separately accepts a parent-suite name/layout.
Test-only mocks never become public inputs.

The configured release owner must confirm the reason/note (`--confirmed-by`
defaults to that owner). An owned/uncertain prior runner additionally requires
`--prior-writer-stopped`; do not infer this from a rerun request. Old uncertainty
stays in history and late acknowledgements cannot finish the replacement attempt.
Ordinary same-attempt recovery still uses `--retry` and its checked continuation.

Explicit reruns bypass unrelated schedule/frontier position, not business inputs,
readiness, genuine approvals or write/send consent. New inputs get a new frozen
attempt and new review where required. Protected downstream effects/approvals must
be settled before changing upstream facts.

Manual completion returns `verification: owner_attested`; skip returns `waived`.
Neither claims automatic/provider success. A manual `--result-file` accepts only
`outputs` and `links`; these remain owner context, not raw receipts or arbitrary
engine-state updates. For machine-consumable identities use the supported owning
bind/adoption/readback operation. The atomic owner-control command starts no
sibling work itself; the default guided flow then continues eligible steps,
including consumers invalidated by a rerun.

## Owner-selected results

### RC report

`publish-rc-report --release <id> --adopt-file <temporary.json>
--confirmed-by <owner> --adoption-reason "<owner statement>"`

The agent supplies:

```text
release, rc, sha256, size, item_url,
resolve: exact workiq_resolve_m365_link observation,
download: exact workiq_download_file observation,
metadata: exact structured item observation if resolution lacks metadata
```

The selected HTML must be in the configured report folder and cover the current
release/RC explicitly. The command verifies actual provider bytes, not equality
with the original automatic report. Applicability is owner-attested. A different
filename/content is supported. Adoption uploads nothing and approves no quality gate.

### Existing Bug Bash invitation

`adopt-bugbash-invite --release <id> --result-file <temporary.json>
--confirmed-by <owner> --reason "<owner statement>"`

Supply `release`, `ccd`, `event_id`, and exact owner-bound `profile`/`event`
observations. The event request uses the engine's exact selection fields.
Organizer, typed complete attendees, times, and Teams URL are verified.
No engine send receipt is invented; chat binding consumes the recorded event.

If either original operation is still owned, explicitly confirm that the previous
writer stopped and pass `--prior-writer-stopped`. Unknown original outcomes remain
in history. Never use adoption as permission to resend/reupload.

## Checked writes and continuation

Preview the owning command. Obtain the required review of actual destinations and
actions, then execute only its checked plan. A preview is not permission.
Do not call raw pipeline/Git/ADO/storage writers to avoid a held checked operation.

`distribute-tests --apply --review-hash <hash> --approved-by <owner>` retains the
original allocation/availability in verified progress. Continuation reconciles
attempted operations, validates unchanged business inputs, and writes only the
remaining approved corrections. Do not reopen/reset it or ask for unchanged OOF/OCE
again. Changed targets/selections or ambiguous outcomes need actual owner review.
An owned unchanged distribution can continue with `--apply` alone, reusing its
original approval. Owner-attested whole-step completion uses result-only
`--manual-completion`, `--confirmed-by`, and `--recovery-reason`, without new
availability/path selectors or provider access. Original uncertainty is retained.

Integration/OneAuth commands retain their existing result-only recovery options:
manual result, verified stopped/unapplied push, or owner-attested completion of all
remaining work. Use only options that the particular command supports. They do
not approve another gate or certify uncertain provider effects as applied.

`skip` records an explicit waiver with a reason, not a verified pass. `reopen`
invalidates declared business consumers and retains gate history; it is not routine
partial-write recovery. Never force-clear an active effect or approval.

## Notifications (2-Step Silent Contract)

1. Run `step-action --release <id> --phase <p> --step <s>` to obtain `tool`, `mcp_arguments`, `requires_approval`, and `complete_command`.
2. **When `requires_approval: false`:** This is NOT an owner approval step — invoking `/release-agent` already explicitly confirms sending configured `requires_approval: false` notifications. **Do NOT call `m_ask_user`, do NOT paste the payload for approval, and do NOT pause** — call the MCP tool (`workiq_send_email`, `workiq_send_chat_message`, `m_send_teams_message`, `microsoft_teams-*`) directly with `mcp_arguments`. (Only preview + call `m_ask_user` when `requires_approval: true` in `config/phases.yaml`.)
3. Record completion in one command: `record-step --release <id> --phase <p> --step <s> --status pass --detail "Sent via <tool>"`.

## Human gates and external approval

Local gates use explicit owner `approve`/`deny`. Externally backed gates use
`approve-orchestrator-gate --preview` and the exact reviewed hash, comment, target,
and reviewer. Once attempted, the execution ID permits only its owning reconciliation;
never resubmit because a new stage/build appears.

RC report selection is not RC quality approval. Bug-bash sign-off is not Remove RC
Tags approval. Publication and Beta remain independently human-controlled.
Beta Play Store start requires the authenticated release owner. Friday additionally
requires the explicit manager-approval policy; no auto-approve or generic skip.

Workflow adoption is separate from task execution. Show `invalidation.summary`,
affected business steps/approvals, and ownership blockers before obtaining the
exact adoption review. Schema 5/contract 4 do not migrate older runs.

## Bug Bash facts, chat, and progress

- Resolve the current primary OCE through actual ICM evidence; ask the owner once
  for filtered OOF selections. Do not infer availability from calendars or presence.
- `record-fact` accepts received sign-off before unrelated UI review. Source identity
  remains bound. An unverified claim does not satisfy the final gate.
- `record-bugbash-chat` verifies the exact recorded invitation, owner, event, and
  meeting thread. An explicitly owner-confirmed chat-link path is available when
  supported native reads fail. Do not search by topic, guess a thread, or create
  another invitation.
- `bugbash-progress` is read-only and available for manual posting. It does not
  acknowledge delivery, sign-off, or approval. Do not reopen communication steps
  just to inspect progress.
- Mention identities need complete fresh exact-chat membership evidence. Preserve
  provider GUIDs and source tags; no guessed identities or people-search substitutes.
- Completed-fill evidence owns test-point classification/attribution. Do not
  overwrite shared case ownership or invent mappings for Monthly UI tests.
- Broker/Authenticator manual work, automation failure triage, UI, and Stress remain
  separate as defined by the compiled source/configuration. Use the owning preview
  for plan repair; preserve original create/results and manual histories.

## Structured reads and optional workers

`workiq-read --scout-root <profile> --account <owner> --entity-url '<exact path>'`
uses the verified native wrapper and literal arguments. Reuse its fresh discovery
descriptor with `--route-file` across pagination. Stale/changed routes require
rediscovery; denied reads never trigger alternate-account/transport fallback.

Unattended mode alone provisions workers. Use canonical `automation plan`,
fresh complete provider observations, `reconcile-create --ensure --claim`, and
the owning creation receipt. Reuse exact workers or recreate only a proven-absent
settled generation. Unknown operations retain ownership. Cleanup uses its checked
claim/result protocol, not direct registry deletion.

The engine journals operational outcomes. Journal owner decisions only when they
are not already captured by the owning command; never announce internal logging
or turn status inspection into a new operational event.
