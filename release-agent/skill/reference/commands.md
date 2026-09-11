# Reference — Commands & manual overrides

_Loaded on demand. Run all from `<AGENT_ROOT>` — the confirmed `release-agent` folder (see SKILL.md → FIRST RUN; `python -m orchestrator.cli paths --json` prints it)._

| Intent | Command |
| --- | --- |
| Discover releases | `python -m orchestrator.cli list --json` |
| Start a new release | `python -m orchestrator.cli init --release <YYYY-MM>` |
| List what a step exposes to `mocks.local.yaml` | `python -m orchestrator.cli mock-spec` |
| Show readiness entry checklist | `python -m orchestrator.cli checklist --release <YYYY-MM> [--verify] [--json]` |
| Run auto readiness verifiers | `python -m orchestrator.cli verify --release <YYYY-MM>` |
| Attest human items (+auto verify) | `python -m orchestrator.cli sign --release <YYYY-MM> --item <id> [--item <id> …] --note "<what they confirmed>"` |
| Record a scout-assisted check (e.g. ICM on-call) | `python -m orchestrator.cli record-check --release <YYYY-MM> --item <id> --status pass\|fail\|degraded --detail "..."` |
| Decide CCOA lockdown overlap | `python -m orchestrator.cli check-lockdown --release <YYYY-MM> --periods-json '[{"name","environment","start","end"}]'` |
| Resolve a migrated step → outcome JSON (done\|blocked\|needs_human\|needs_skill) | `python -m orchestrator.cli step-action --release <YYYY-MM> --step <id> [--phase <p>] [--param k=v …]` — configured prerequisites and stop guards apply. Notification outputs are previews; use the shared protocol below. Non-notification reservable actions retain `--reserve --executor <session>` |
| Answer a STEP question (knowledge) | `python -m orchestrator.cli step-info --step <id> [--phase <p>]` |
| **Phase 2 — RC pipeline + test report** (read-only) | `python -m orchestrator.cli rc-report --release <YYYY-MM> [--json]` → the checker→orchestrator→ECS/Local-MRWP chain + per-run test breakdown |
| **Phase 2 — RC report and verdict** | `notification prepare --release <YYYY-MM> --source step --phase build_verify --step rc_report` → claim/result applies the approved report's independent MRWP/Auth verdict and links after confirmed delivery. Legacy `record-rc-report` cannot acknowledge new work. |
| **Simulate a mid-release point** (testing) | `python -m orchestrator.cli sim list` · `python -m orchestrator.cli sim run --scenario <name> [--freeze] [--json]` → **seeds the real release** to a scenario's target (`config/scenarios/<name>.yaml`): fast-forwards the real engine, signs the entry gate + completes earlier phases from mocks, then stops `open`/`gate`/`done` at the target. `data: live` runs the target phase against real `az`; `data: mock` is offline. Any existing state at that id is backed up first, so afterwards you use the **normal** commands (`status`, `rc-report`, `next`, `approve`). `--runs-root <path>` targets a throwaway sandbox instead; `--freeze` snapshots state to `tests/fixtures/<name>.json` |
| Answer an ENTRY-GATE item question (knowledge) | `python -m orchestrator.cli gate-info --item <id>` (build_access, mcp_servers, ccd_confirmed, silent_perms, teams_notify, adx_access, oncall_now, play_console_access, oncall_window, saw_ame, yubikey) |
| Prepare early code-complete notice (JSON) — _legacy; prefer `step-action --step notice`_ | `python -m orchestrator.cli prepare-notice --release <YYYY-MM> [--variant initial\|update]` |
| Prepare flight & string reminders (JSON) | `python -m orchestrator.cli prepare-flight-reminder --release <YYYY-MM>` |
| Record a non-notification Scout step | `python -m orchestrator.cli record-step --release <YYYY-MM> --step <id> --status pass\|attention --detail "..." [--execution-id <id>]` — notification steps require notification result instead |
| Declare you CANNOT satisfy an item | `python -m orchestrator.cli decline --release <YYYY-MM> --item <id>` |
| Status (structured) | `python -m orchestrator.cli status --release <YYYY-MM> --json` |
| Advance to next gate | `python -m orchestrator.cli next --release <YYYY-MM>` |
| Approve the holding gate | `python -m orchestrator.cli approve --release <YYYY-MM> --comment "<why>"` |
| Deny the holding gate | `python -m orchestrator.cli deny --release <YYYY-MM> --comment "<why>"` |
| **Done** — mark a reminder (human to-do) complete | `python -m orchestrator.cli done --release <YYYY-MM> [--phase <p> --step <s>] --note "<what you did>"` |
| **Validate CCD** (temporal + pipeline; read-only; entry gate) | `python -m orchestrator.cli check-ccd --release <YYYY-MM> --json [--as-of YYYY-MM-DD]` → `{ccd, override, ccd_conflict, status: match\|past\|conflict\|unreadable\|unset, days_to_ccd, runway_days, compressed}` |
| **Set/change CCD** (writes pipeline; preview→confirm) | `python -m orchestrator.cli set-ccd --release <YYYY-MM> --date <YYYY-MM-DD> --reason "<why>" [--confirm]` |
| **Revert CCD to default** (2nd Wednesday) | `python -m orchestrator.cli set-ccd --release <YYYY-MM> --default --reason "<why>" [--confirm]` |
| **Skip/cancel the release** (writes pipeline) | `python -m orchestrator.cli skip-release --release <YYYY-MM> --reason "<why>" [--confirm]` (add `--clear` to un-skip) |
| **Skip** a step (reason REQUIRED) | `python -m orchestrator.cli skip --release <YYYY-MM> --phase <p> --step <s> --reason "<why>"` |
| **Reopen** a done/skipped step | `python -m orchestrator.cli reopen --release <YYYY-MM> --phase <p> --step <s> [--reason "..."]` |
| **Halt** (emergency, reason REQUIRED) | `python -m orchestrator.cli halt --release <YYYY-MM> --reason "<why>"` |
| **Resume** after a halt | `python -m orchestrator.cli resume --release <YYYY-MM> [--reason "..."]` |
| Show / analyze this release's log | `python -m orchestrator.cli log --release <YYYY-MM> [--analyze] [--json]` |
| Journal interaction (silent) | `python -m orchestrator.cli journal --release <YYYY-MM> --source scout\|user --text "..."` |
| Journal a step Q&A (silent) | `python -m orchestrator.cli journal --release <YYYY-MM> --kind qa --phase <p> --step <id> --question "..." --answer "..."` |
| Localization: record trigger | `python -m orchestrator.cli record-localization-run --release <YYYY-MM> --build-id <buildId>` — store the queued build; leaves the step in-flight |
| Localization: one poll | `python -m orchestrator.cli check-localization --release <YYYY-MM> [--complete <true\|false>] [--logs "<OneLocBuild@3 log>"] [--pr-status <active\|completed\|abandoned>]` — poll the pipeline before PR discovery and the PR afterward; acts on the printed decision |
| Localization: deliver staged follow-up | `notification prepare --release <YYYY-MM> --source pending` then claim/result for the initial PR, deadline warning or timeout email. A PR ID alone is not delivery evidence. |
| **Phase 2 — signal a re-triggered RC** | `python -m orchestrator.cli rc-retriggered --release <YYYY-MM> [--reason "..."]` — after the owner re-runs RC (flaky) or the orchestrator triggers a fresh RC (broker cherry-pick), reopens `mrwp_ecs`/`mrwp_local`/`rc_report` so Scout re-evaluates the **newest** RC. Holds `in_flight` (no action) while the run executes; the poller re-applies the gate on completion |
| **Phase 2 — one RC poll** | `python -m orchestrator.cli poll-rc --release <YYYY-MM>` — `waiting` / `nudge` / `ready` / `resolved` / `blocked` / `idle`. `ready` lists eligible Scout work: execute it and its follow-up, then re-poll. `resolved` requires all Phase-2 steps settled as done/skipped; distinguish `status: overridden` from `status: passed`. The worker lives until its owning phase completes, not merely an old RC report. |
| **Phase 3 — bind the invitation's meeting chat** | `python -m orchestrator.cli record-bugbash-chat --release <id> [--chat-id <expected-thread>]` — resolves the exact sent event's join URL/thread; enforces prerequisites; never chooses by title |
| **Phase 3 — one bug-bash update** | `python -m orchestrator.cli post-bugbash-update --release <YYYY-MM> [--force]` — `off_hours` / `no_chat` / `error` / `post` / `complete`. Missing/stale invite-chat bindings return no_chat, including with force. The final message's successful claim/result records `poll_complete`; central cleanup deletes then deregisters the on-demand poller. |
| **Phase 3 — OOF candidates / manual distribution preview** | `python -m orchestrator.cli distribute-tests --release <id> --oce <verified-primary-upn> [--json]` — owner/OCE/configured exclusions removed before listing candidates; missing OCE blocks without candidates; subsequent runs reuse the recorded OCE and availability |
| **Phase 3 — record owner availability and refresh preview** | `python -m orchestrator.cli distribute-tests --release <id> --no-oof` OR `… --oof <verified-upn> [--oof <verified-upn> …] [--oce <upn>]` — only after explicit owner input; never sends or writes assignments |
| **Phase 3 — validate current distribution** | `python -m orchestrator.cli distribute-tests --release <id> --validate --json` — read live ADO; report eligibility, balance, triage and tester mismatches without writes |
| **Phase 3 — apply reviewed corrections** | `python -m orchestrator.cli distribute-tests --release <id> --apply --review-hash <approved-preview-hash>` — reread ADO, apply only reviewed current corrections and read back; no stored assignment plan |
| **Phase 3 — inspect Broker plan recovery** | `python -m orchestrator.cli broker-plan --release <id>` — JSON resource record and all same-release candidates; read-only ADO |
| **Phase 3 — preview UI mapping repair** | `python -m orchestrator.cli broker-plan --release <id> --preview-ui-repair [--plan-id <id>]` — deterministic source/old/new configs and affected points; read-only ADO, no state save, bind, apply or cleanup |
| **Phase 3 — bind owner-selected existing plan** | `python -m orchestrator.cli broker-plan --release <id> --plan-id <plan-id> --reason "<owner selection>" [--area-path "<observed area>"]` — local identity binding only; no ADO writes, step completion, or plan replacement |
| **Phase 3 — authorize retry after confirmed non-creation** | `python -m orchestrator.cli broker-plan --release <id> --confirm-not-created --reason "<owner-reviewed evidence>"` — only after original runner stopped; also requires successful discovery with zero candidates and no recorded plan ID |
| Activate conditional hotfix phase | `python -m orchestrator.cli activate --release <YYYY-MM> --phase hotfix` |
| **Notify** — push line if something needs me | `python -m orchestrator.cli notify [--release <YYYY-MM>] [--as-of <date>] [--force]` |
| **Plan startup automations** | `automation plan --release <YYYY-MM> [--json]` — excludes all `on_demand:true` pollers |
| **Plan one on-demand poller** | `automation plan --release <YYYY-MM> --on-demand <slug> --json` |
| **Plan lifecycle cleanup** | `automation cleanup --release <YYYY-MM> --json` — delete each returned Scout id with `m_delete_automation`, then deregister only after success |
| **Track automations** | `automation register --id <id> --name "<n>" --cleanup-when "<rule>" [--cleanup-when "<OR-rule>"] [--shared\|--release <YYYY-MM>] [--purpose "..."] [--step <phase.step> …]` · `automation list …` · `automation deregister --id <id>` |

## Broker plan recovery

`clone_plans_broker` runs through `next`. Its durable identity and source snapshot live
in `resources.broker_test_plan`, independent of step status. Reopen clears completion,
not this resource record. Successful discovery/validation restores `step.data.plan_id`.
Never hand-edit either record, infer an ID from a note, or use `done`/`skip` to claim a
plan was recovered. Never delete the ID to force another create.

1. On a discovery/identity block, run `broker-plan --release <id>`. Present the returned
   candidates (IDs, URLs, names, areas, iteration) and the saved identity. Inspect the
   candidate's suites and test-point results with read-only ADO tools before recommending
   retention or cleanup. Do not choose newest/first automatically.
2. Ask the owner which existing plan to retain using `m_ask_user`, then wait. Run
   `broker-plan ... --plan-id <confirmed-id> --reason "<owner's selection>"` only after
   confirmation. It verifies identity, the three flat suites, query, configurations and
   complete static point matrices without changing outcomes/assignments. It cannot replace
   an already-bound ID. If an existing plan's area differs, explicitly confirm its exact
   observed area and supply `--area-path`; this binds the existing location, never moves it.
   Older creates used an ignored `area` field and may live at the project root; new creates
   correctly use `areaPath`. No automatic area migration is performed.
3. Resume `next`. Binding alone neither completes the step nor advances a phase.
   API/auth/404 errors always block; fix access or restore/repair the existing plan.
   Partial plans are retained and must be repaired in place against the saved source.
4. After a timeout/interruption, `creating` with no ID remains reserved even when no plan
   is visible. Stop the original runner and inspect ADO. Only an explicit owner confirmation
   that creation did NOT occur permits `--confirm-not-created --reason "<evidence>"`.
   The command independently requires a successful zero-candidate discovery, retains the
   interrupted attempt and records retry authorization; it does not itself create a plan.
   An unattended worker must surface this hold, never assert non-creation on its own.

Creation snapshots the master before writes, checkpoints intent under the existing OS
release lock, and checkpoints the returned ID immediately. Success requires read-back
against that snapshot. ADO also carries the release identity and completion marker for
recovery after local metadata loss. Lookup pagination errors/caps are failures, not absence.
Future resource creators should use release-owned identity + locked checkpoints rather
than engine special cases. This protects workers sharing one authoritative runs directory;
ADO does not provide an atomic uniqueness key, so independent copied runners are NOT safe.
Stop old workers before deploying the updated code and installed skill together.

Duplicate-plan cleanup remains a separate explicit approval. Deleting a duplicate plan
must never delete its shared test-case work items. No historical release-state migration
or automatic plan deletion is part of recovery.

### Two-plan mapping and existing-point repair

Broker suite routing includes **both** MSAL and Broker versions and keeps ECS/Local
separate: 292/328 = PROD MSAL + RC Broker, 294/344 = RC MSAL + PROD Broker,
293/330 = RC MSAL + RC Broker. LTW and mapped Stress use 293/330. BrokerHost explicitly
rolls up into 292/328, matching the master BrokerHost subtree. Unknown combinations
are diagnosed, never guessed. Distinct API/device tests use Failed-wins at a mapped point.

New-plan snapshots freeze per-case assignments: the four baseline configurations plus
293/330 only for cases represented by current source evidence. Recovery validates this
exact frozen matrix, not a freshly queried master or a blanket six-config expansion.
Existing historical four-config snapshots are not silently migrated.

Use `--preview-ui-repair` to inspect source titles/result URLs, old/new configuration IDs,
existing point IDs/outcomes and missing pairs. Missing RC/RC points block the Broker
writer before its first outcome write. **No repair apply command is implemented.**
Obtain later exact approval for in-place changes. Never recreate the plan, duplicate case
work items, or automatically clear old 294/344 LTW results. Equal outcomes do not prove
automation ownership; historical cleanup requires owner review, ownership receipts and
match-before-write. If receipts are unavailable, leave the old/manual results intact.

Authenticator's Monthly UI Tests intentionally has no case map. All failures remain in
the standard report and release-owner `ui_failures` reminder, by exact title and source
link. No case creation, forced mapping, new notification lifecycle, or automatic attestation.

## Bug Bash invite and exact chat identity

For the `send_invite` step, preserve its exact `start`, `end`, and `timeZone` payload.
The body and approval summary include the scheduling timezone and meeting-date UTC offset;
all scheduling rules use `America/Los_Angeles`, with the earliest start at 09:00 Los Angeles
time regardless of the owner/runner location. DST offsets follow the meeting date. Check
an existing event's actual calendar times separately from body
text before diagnosing an overnight invite. Do not rerun creation to correct an existing
meeting; any organizer update must keep the displayed body time consistent with start/end.

After `workiq_create_event` succeeds, preserve its returned event JSON (top-level `id`)
using `notification result ... --receipt-file <response.json>`. Never invent an event ID.
If sending succeeded but recording failed, retry acknowledgement, not calendar creation.

`activate_chat` consumes the completed send_invite's acknowledged receipt. Run
`record-bugbash-chat --release <id>` as the organizer. It reads that exact event, validates
organizer/subject/times and resolves its join URL to `onlineMeeting.chatInfo.threadId`,
then verifies that thread. Do not use `workiq_search_chats` or a matching title as identity.
`--chat-id` only asserts that a user-provided candidate equals the resolved thread.
If the thread is unavailable, open the exact event's Chat pane and retry; API permission
failures remain blocked, not a reason to accept an unverified chat.

The same completed binding is a no-op. To replace a stale/completed binding, the owner must
first review and reopen **activate_chat**, never recreate the meeting. Older runs missing
the event receipt require explicit owner recovery; this change does not migrate them.
Both initial and recurring updates reject missing/stale bindings. Their prepared payloads
also bind the invitation receipt and chat record for claim/finalization checks. Stop old
workers and review already-prepared legacy payloads when deploying; do not replay them.

Progress posts use the exact prepared `content`, `contentType: html`, and `mentions`
array together. The owning progress step resolves display names and Entra user GUIDs
against that meeting's members (directory lookup handles UPN/SMTP differences).
Never replace the GUID with an email, omit mentions metadata, or rewrite `<at id="N">`
as plain text. Unresolved pending owners block rather than producing fake mentions;
fix the assignment/membership or directory access and prepare again. Completed owners
remain plain names and are not notified. No new message is sent merely to validate tagging.
The numerator/denominator cover human manual/triage work, not the entire Authenticator
suite. The completed fill's automated-case classification excludes automation-only Auth
cases from both counts (as distribution does), while applied automation failures remain
visible as triage. Preserve real manual Passed/N/A completions. Do not infer automation
from a Passed outcome, a shared tag, or a saved assignment list.
If the CLI Graph token lacks chat-member access, fetch the exact verified chat with
`workiq_get_chat` and save its fresh `id`, `chatType`, and complete `members` fields as
JSON (do not copy message history). Re-prepare the first post with `--param members_file=<path>`, or rerun
`post-bugbash-update` with `--members-file <path>`. The response must match the bound chat;
do not reuse another meeting's member list or hand-author identities.

## Bug Bash availability

Before `distribute_tests` computes the first manual-test preview, the **release owner**
must supply availability for **this release's Bug Bash**. Do not query calendars,
Teams presence, O365 automatic replies/OOF, or infer dates. Graph is used only to resolve
the configured roster and its names/verified UPNs, not to determine availability.

1. Resolve the **current primary** on-call engineer using ICM
   `get_on_call_schedule_by_team_id` for the team in `readiness.yaml`'s `oncall_now`
   (currently 78848). Resolve the returned contact to a verified UPN; never guess it.
   Run `python -m orchestrator.cli distribute-tests --release <id> --oce <verified-upn> --json`.
   Its blocked JSON includes filtered `candidates` (`name`, `upn`). Display only that list,
   not the raw DL membership: the release owner, OCE, Jia Le He, Moumita Ghosh and Veena
   Soman have already been removed. Missing OCE blocks without showing candidates; resolve
   it before asking OOF. The recorded OCE is reused; refresh it if the on-call engineer changes.
2. Call `m_ask_user` with **"Is anyone OOF for this Bug Bash?"** and exactly the choices
   **"Nobody is OOF"** and **"Exclude people"**. **Stop and wait for the actual reply.**
   No response is not confirmation. An unattended runner must surface the question to
   the owner and leave the step blocked; it must not invent an answer.
3. If "Exclude people", ask which people with free-text `m_ask_user`, using the displayed
   roster as context; **stop and wait again**. Do not turn a large roster into choice chips
   (the tool supports at most five). Resolve each exact name uniquely to a candidate UPN.
   Ask for clarification for ambiguous/unknown names; never guess aliases or email addresses.
4. Only after the owner answers, run `distribute-tests … --no-oof` or repeat
   `--oof <verified-upn>` per excluded person. Exact roster display names are also accepted
   by the CLI, which rejects unknown/ambiguous entries. `--no-oof` and `--oof` are mutually exclusive.
   `--oce` is required before the first availability list and is then retained for this
   distribution; it is not an OOF source. The full roster still validates explicit/stored
   OOF answers, so an already-excluded person's older OOF entry does not re-enable them.
5. Show the live validation and any proposed corrections, including OOF names/UPNs.
   After explicit approval, use **`--apply --review-hash <hash from that preview>`**.
   Never combine `--apply` with `--oof`, `--no-oof`, or `--oce`,
   and never use `done`/`record-step` as a substitute for availability confirmation.
   Continue normal `next` after handling the distribution.

The preview has two separate groups: balanced **manual assignments** for eligible testers,
and **owner triage** for Blocked-tagged cases and applied automated failures. Show every
triage case and reason; do not silently leave those cases with excluded/former owners.
The cross-platform `Automated` tag is not an Android automation exclusion; only the
completed fill's automated-case set proves that. The owner remains excluded from manual
work but receives triage. Apply writes both case assignees and selected plan-point testers;
neither outcomes nor Blocked tags are changed. A failed tester update is an incomplete
apply, not success. Review again if the triage set changes.

**ADO is the sole assignment source.** Validation reads case assignees and release-plan
testers, checks the distribution rules, and proposes only needed corrections. It does not
silently rebalance. `next` holds when mismatches exist and completes this step when ADO is
valid. Existing valid manual changes are accepted without restoring any old allocation.

Previews exist only in command output/memory. The returned `review_hash` is an approval
digest, not an assignment list; it is not saved in run-state. On `--apply`, live inputs
must still match the reviewed digest before any correction is written. Changed work items
also use ADO's native revision check. Read-back must confirm correct assignments and testers
before completion; tags and outcomes are preserved.

After partial failure or timeout, read the current ADO values and review remaining
corrections. Never restore a saved map or roll back earlier successes. If ADO is already
valid, the command does not redistribute; it can finish local completion after an earlier
interruption. Legacy `data.plan` allocations are ignored and removed when this step runs.

The answer is saved in `bug_bash.distribute_tests.data.oof` with canonical UPNs,
`confirmed_by` (owner), `source: release-owner`, `confirmed_at`, and `release_id`.
The engine and CLI read the same record. Repeated previews reuse it; explicit choices
replace it (including "Nobody is OOF"). Only availability choices and workflow status are
retained: no assignment map, preview baseline, approval digest or apply-attempt ledger.
Availability changes never alter already-written assignments; review live corrections and
explicitly apply them separately.

For offline tests, inject the step's documented `roster`, case-selection, `case_snapshot`
and `point_sets` observations, and explicitly
confirm OOF in the test fixture or call `build(..., oof=[])`. There is no permissive OOF
default or production `--param` override. Engine-level `outcome: done` mocks short-circuit
steps for simulation only; they are not an availability record and cannot authorize apply.

## Manual overrides (steer when reality diverges from the plan)
- **skip** — a step doesn't apply, or was done manually outside the tool. **Reason required** (audited). Confirm the reason, then run.
- **reopen** — a step (incl. an approved gate) needs to run again; reopening a gate makes it re-hold for a fresh decision.
- **halt** — emergency freeze (e.g. production incident). **Reason required.** While halted, `next` refuses; status shows a HALTED banner.
- **resume** — clear a halt and continue.

Map natural language to these ("skip the CG report, doesn't apply" → `skip … --reason`; "halt, we have an incident" → `halt --reason`; "resume" → `resume`). Never skip or halt without capturing the user's reason.

## `step-action` — the generic step dispatcher
Apply the execution-reservation rule in **SKILL.md → The universal loop** before
acting on `reservable:true` results. The existing `done`/`reopen` commands recover
interrupted reservations only after owner review and stopping the original runner;
provide evidence via `--note`/`--reason`. Never automatically repeat an uncertain action.

`step-action` resolves a **migrated** step into one uniform outcome JSON (`kind`). It replaces the per-step `prepare-*` commands — react by `kind`:
- **`done`** — already complete; nothing to run.
- **`blocked`** — surface `reason` to the owner; don't proceed.
- **`needs_human`** — show `prompt` (attestation or reminder to-do).
- **`needs_skill`** — notifications MUST use prepare/claim/result, not raw tool/payload or legacy followup_command. Non-notification browser/gather/trigger work retains its named follow-up. Existing local test redirects are applied BEFORE snapshot hashing; never change a claimed payload.
- `completion.automation.on_demand` is an executor directive, not an MCP argument. After confirmed delivery and completion, provision that slug if absent using `automation plan --on-demand`. Source pending retains the directive for recovery if provisioning failed.

If a step isn't migrated yet, `step-action` returns `{"error": …}` with exit 1.
Scout notification steps use the shared contract; attest steps return `needs_human`
for the owner's explicit decision. Agent steps execute only in-process through `next`.

## Shared notification delivery and persisted schema

All commands below require an explicit release and use the existing OS-held state lock.

| Operation | Command |
| --- | --- |
| Persist exact preparation, without send permission | `notification prepare --release <id> --source step\|digest\|status-email\|pending [--phase <phase> --step <step> --param k=v]` |
| Approve and reserve that exact target/payload | `notification claim --release <id> --id <logical-id:channel> --hash <hash> --executor <session>` |
| Acknowledge one channel | `notification result --release <id> --id <id> --execution-id <execution> --outcome sent\|not_sent\|uncertain --evidence "<proof>" [--receipt-file <JSON>] [--owner-review]` |
| Retry domain completion, never sending again | `notification finalize --release <id> --id <id>` |

Send ONLY a successful claim's exact returned payload (`permission_to_send:true`).
`not_sent` requires proof nothing was sent; timeout, interruption or unknown outcome is
uncertain. Claims never expire. After successful send plus failed acknowledgement, retry
the acknowledgement only. Owner-reviewed recovery requires the original runner stopped.
Exactly-once delivery is impossible without downstream idempotency support.

Schema v1 adds `notification_deliveries`, an empty map for a new release. Each
release-local `logical-checkpoint:channel` maps to `descriptor`, `prepared_at`, `status`,
`attempts`, optional `superseded` preparation history, and optional `completion`.
Never-claimed preparations may refresh on source/payload changes; their previous descriptor
and preparation/replacement timestamps remain in `superseded`, and the new hash needs approval.
After the first claim the snapshot is frozen, even for known-not-sent outcomes. An expired
claimed invitation requires owner recovery; it is never automatically replaced by another event.
The descriptor contains release, scope (release,
phase, window or step), semantic checkpoint, target, tool, exact payload, completion
metadata and hash. Attempts preserve execution ID, runner, timestamps, outcome,
evidence and raw provider receipt (null if none). Successful result replay preserves
evidence. Step-owned sends share the engine's `_execution` ID. Legacy date-only stamps
are conservative stop evidence, never converted into provider receipts; incomplete
old claims/registrations require deliberate owner recovery, not automatic migration.

Daily identities use the owner's configured timezone; acknowledgement preserves the
prepared day even across midnight. `--force` changes cadence only, never halt,
scope, terminal state or acknowledged identity. There is no automatic resend operation.
All workers execute cleanup in a finally block, delete live automation before deregistration,
and preserve shared/manual exceptions. Suspended work can resume.
Scope and source evidence are checked at preparation, claim and completion. Source changes
during delivery preserve the receipt but suppress completion; stale RC verdicts cannot advance.
A halt racing an already executing external call
cannot undo that call; retain its evidence without advancing closed work. Claims are not
transport idempotency keys. Never change the shared state path or retry ambiguous calls.
An explicit `done` + `no_delivery_required:true` outcome (for example already-complete tests)
may use `record-step --status pass`, which revalidates that no message is required.
The ledger contains destination-specific payloads and receipts: protect the run directory
with the same access controls as release source evidence; don't relay ledger dumps to Teams.
Every worker run discovers `notification prepare --release <id> --source pending`, even
after a silent/terminal producer result. Finalize sent-but-unfinished work without sending;
only eligible prepared/not_sent records can be claimed. Only **preparation** accepts `--as-of`
(ISO date or timestamp, normalized to the owner timezone). Claim/result/finalize reject
clock overrides and use trusted current time. An unavailable timezone blocks
delivery rather than silently falling back to the runner's local day.

### Adding an upcoming notification
Declare `NOTIFICATION = True` on an outbound step module and return a supported
transport. The generic dispatcher derives step/phase scope and the stable step identity;
optional `NeedsSkill.notification` supplies a semantic checkpoint, `not_before`/`expires_at`,
`state_matches`, and declarative completion data. A source binding is a state `path`
(dictionary keys/list indexes) with either exact `value` or `hash: delivery.fingerprint(value)`.
Bind the actual evaluated inputs, not just an RC number: result changes and reruns matter.
Keep the logical identity stable when refreshing the same unsent work; don't use a payload
hash as an identity that evades an existing claim. Invitations expire at their Los Angeles-local
start time; expiry is not a claim lease and never discards an in-flight receipt.
A polling producer supplies `delivery.descriptor` with explicit
scope, checkpoint (day/build/PR), target and completion, then `delivery.offer` under
the existing command lock. Never stamp a send during preparation. Declare automation
`cleanup_when` against configured phase/step keys, not name heuristics, and reuse the
same claim/result prompt. No engine branch or bespoke sender is needed.


The human-readable commands (`checklist`, `status`, `next`, `approve`, `deny`, `decline` without `--json`) emit a **canonical block AND auto-log it**. Prefer these and show their output; use `--json` only for your own logic.

## Event logging (silent — never changes the interaction)
Each release keeps an append-only log at `.release-runs/<id>/events.jsonl` (per-release only; no machine-wide aggregate). For debugging only. **Invisible to the user** — don't announce it, don't add questions to populate it.
- **Scout output is logged automatically** by the CLI for every human-readable command — you don't journal what was shown.
- **User input is your responsibility** — the engine can't see what the user typed/clicked. Every time the user makes a choice, immediately (and silently) journal it: `journal --release <id> --source user --kind choice --text "<what they said>" --choice "<option>"`.
- **Step questions are interactions too** — when the user asks a detail/how/why/who question about a step and you answer from `step-info`, silently journal the pair: `journal --release <id> --kind qa --phase <p> --step <id> --question "<their question>" --answer "<one-line gist>"`. This is what surfaces missing/inaccurate knowledge later. Only when a release is active; skip if there's no run.
- Capture the **decision driver** passively: a reason given while approving/denying/declining → pass as `--comment "<their words>"` (or `--reason` for decline). No reason → empty comment. Never prompt just for the log.
- User asks "what happened" / "show the log" → `log --release <id>` (add `--analyze` for a rollup).
