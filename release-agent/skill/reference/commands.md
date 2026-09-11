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
| **Phase 3 — one bug-bash update** | `python -m orchestrator.cli post-bugbash-update --release <YYYY-MM> [--force]` — `off_hours` / `no_chat` / `error` / `post` / `complete`. The final message's successful claim/result records `poll_complete`; central cleanup deletes then deregisters the on-demand poller. |
| **Phase 3 — OOF candidates / manual distribution preview** | `python -m orchestrator.cli distribute-tests --release <id> [--json]` — blocks with candidate names/verified UPNs until the owner answers; subsequent runs reuse that release's confirmation |
| **Phase 3 — record owner availability and refresh preview** | `python -m orchestrator.cli distribute-tests --release <id> --no-oof` OR `… --oof <verified-upn> [--oof <verified-upn> …] [--oce <upn>]` — only after explicit owner input; never sends or writes assignments |
| **Phase 3 — apply reviewed distribution** | `python -m orchestrator.cli distribute-tests --release <id> --apply` — separate explicit write of stored assignments; rejects missing/stale confirmation or changed roster/exclusions |
| Activate conditional hotfix phase | `python -m orchestrator.cli activate --release <YYYY-MM> --phase hotfix` |
| **Notify** — push line if something needs me | `python -m orchestrator.cli notify [--release <YYYY-MM>] [--as-of <date>] [--force]` |
| **Plan startup automations** | `automation plan --release <YYYY-MM> [--json]` — excludes all `on_demand:true` pollers |
| **Plan one on-demand poller** | `automation plan --release <YYYY-MM> --on-demand <slug> --json` |
| **Plan lifecycle cleanup** | `automation cleanup --release <YYYY-MM> --json` — delete each returned Scout id with `m_delete_automation`, then deregister only after success |
| **Track automations** | `automation register --id <id> --name "<n>" --cleanup-when "<rule>" [--cleanup-when "<OR-rule>"] [--shared\|--release <YYYY-MM>] [--purpose "..."] [--step <phase.step> …]` · `automation list …` · `automation deregister --id <id>` |

## Bug Bash availability

Before `distribute_tests` computes the first manual-test preview, the **release owner**
must supply availability for **this release's Bug Bash**. Do not query calendars,
Teams presence, O365 automatic replies/OOF, or infer dates. Graph is used only to resolve
the configured roster and its names/verified UPNs, not to determine availability.

1. When `next` reports the distribution owner-input block, run
   `python -m orchestrator.cli distribute-tests --release <id> --json`. Its blocked JSON
   includes `candidates` (`name`, `upn`). Display that list as context.
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
   Optional `--oce` keeps the existing best-effort ICM exclusion; it is not an OOF source.
5. Show the new preview, including the OOF names/UPNs. Keep the existing explicit
   **review then `--apply`** flow. Never combine `--apply` with `--oof`, `--no-oof`, or `--oce`,
   and never use `done`/`record-step` as a substitute for availability confirmation.
   Continue normal `next` after handling the distribution.

The answer is saved in `bug_bash.distribute_tests.data.oof` with canonical UPNs,
`confirmed_by` (owner), `source: release-owner`, `confirmed_at`, and `release_id`.
The engine and CLI read the same record. Repeated previews reuse it; explicit choices
replace it (including "Nobody is OOF"). The saved plan binds that confirmation plus
roster/owner/OCE/always-excluded inputs in `review_inputs`. A failed/revised preview
cannot leave an old plan applicable. Apply revalidates those inputs and every assignee
before writing anything. Availability changes never alter already-written assignments;
review a new preview and explicitly apply it separately.

For offline tests, inject the step's documented `roster`/case mocks and explicitly
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
hash as an identity that evades an existing claim. Invitations expire at their owner-local
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
