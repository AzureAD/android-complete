# Reference — Commands & manual overrides

_Loaded on demand. Run all from `<AGENT_ROOT>` — the confirmed `release-agent` folder (see SKILL.md → FIRST RUN; `python -m orchestrator.cli paths --json` prints it)._

### Typed step inputs

`--param name=value` accepts only fields declared by the requested module's `build`
parameter model. There is no shared bag of universally accepted options. Unknown,
irrelevant, duplicate, empty-name and wrong-type inputs are errors before handler IO
or evidence mutation. Text stays literal; non-text values must be JSON (PowerShell:
`--param 'oof=["verified@example.com"]'`). Defaults come from the frozen module model.

Current build inputs: `preflight.notice` and `ccd.final_reminder` accept optional
`variant`; `bug_bash.notify_native_auth` accepts optional `engineer` and
`engineer_source`; `bug_bash.bugbash_updates` accepts optional `members_file`;
`bug_bash.distribute_tests` accepts optional `oof` (JSON array of verified UPNs) and
`oce` (verified UPN). Other configured builds accept no public parameters.
`distribute --oof/--no-oof/--oce` and `bugbash-update --members-file` use the same typed
module inputs. Approval `--comment` belongs to `prepare_approval`; effect-retry
`--reason` belongs to `authorize_retry`. Neither is a build parameter.
`submit_approval`/`reconcile_approval` receive `NoParameters` and use the frozen request.
No live input overrides frozen effect intent or an owned approval target/comment.

For step-source notification preparation, repeat the exact same phase, step and inputs
through review/preparation; changed payloads have different hashes and require a fresh
review. Parameters never authorize sending. Mock redirects and input knobs still use
`mocks.local.yaml`/`mock-spec`, not undeclared `--param` overrides.

### Command index

| Intent | Command |
| --- | --- |
| Discover releases | `python -m orchestrator.cli list --json` |
| Start a new release | `python -m orchestrator.cli init --release <YYYY-MM>` |
| Preview workflow revision adoption | `python -m orchestrator.cli workflow-adopt --release <id> --json` — old/new identity, conservative invalidations and ownership blockers; no execution. Before asking for approval, show `invalidation.summary`, every completed/blocked step being reset, removed gate decisions/offers, and blockers. |
| Confirm reviewed workflow adoption | `python -m orchestrator.cli workflow-adopt --release <id> --approve-hash <hash> --by <owner> --reason "<reviewed change>"` — recomputes under locks, rejects stale reviews/owned work, persists atomically without draining |
| List what a step exposes to `mocks.local.yaml` | `python -m orchestrator.cli mock-spec` |
| Show readiness entry checklist | `python -m orchestrator.cli checklist --release <YYYY-MM> [--verify] [--json]` |
| Run auto readiness verifiers | `python -m orchestrator.cli verify --release <YYYY-MM>` |
| Attest human items (+auto verify) | `python -m orchestrator.cli sign --release <YYYY-MM> --item <id> [--item <id> …] --note "<what they confirmed>"` |
| Record a scout-assisted check (e.g. ICM on-call) | `python -m orchestrator.cli record-check --release <YYYY-MM> --item <id> --status pass\|fail\|degraded --detail "..."` |
| Decide CCOA lockdown overlap | `python -m orchestrator.cli check-lockdown --release <YYYY-MM> --periods-json '[{"name","environment","start","end"}]'` |
| Resolve a migrated step → outcome JSON (done\|blocked\|in_progress\|needs_human\|needs_skill) | `python -m orchestrator.cli step-action --release <YYYY-MM> --step <id> [--phase <p>] [--param k=v …] [--execution-id <id>]` — preparation/poll/refresh requires current readiness, prerequisites, frontier and time permission; owned polling requires its exact ID. Notification outputs are previews; use the shared protocol below. Non-notification reservable actions retain `--reserve --executor <session>` |
| Review/execute a configured external write | `distribute-tests` previews live ADO assignment corrections and still requires explicit owner approval: repeat selections with `--apply --review-hash <hash> --approved-by <reviewer>`. Scheduled release writers (`launch-localization`, `create-integration-prs`, `create-oneauth-common-pr`, `create-payload-wiki`, `start-release-signoff`, `start-upload-whats-new`, `start-upload-alpha`) use `--execute --auto-approve --executor <automation-id>`; each recomputes/checkpoints its current plan hash, fences one provider request and verifies/read-backs before completion. Generic reserve-step cannot authorize these writers. |
| Answer a STEP question (knowledge) | `python -m orchestrator.cli step-info --step <id> [--phase <p>]` |
| **Phase 2 — RC pipeline + test report** (read-only) | `python -m orchestrator.cli rc-report --release <YYYY-MM> [--json]` → the checker→orchestrator→ECS/Local-MRWP chain + per-run test breakdown |
| **Phase 2 — RC report and verdict** | `notification prepare --release <YYYY-MM> --source step --phase build_verify --step rc_report` → claim/result applies the approved report's independent MRWP/Auth verdict and links after confirmed delivery. Legacy `record-rc-report` cannot acknowledge new work. |
| **Simulate a mid-release point** (testing) | `python -m orchestrator.cli sim list` · `python -m orchestrator.cli sim run --scenario <name> [--freeze] [--json]` → **seeds the real release** to a scenario's target (`config/scenarios/<name>.yaml`): fast-forwards the real engine, signs the entry gate + completes earlier phases from mocks, then stops `open`/`gate`/`done` at the target. `data: live` runs the target phase against real `az`; `data: mock` is offline. Any existing state at that id is backed up first, so afterwards you use the **normal** commands (`status`, `rc-report`, `next`, `approve`). `--runs-root <path>` targets a throwaway sandbox instead; `--freeze` snapshots state to `tests/fixtures/<name>.json` |
| Answer an ENTRY-GATE item question (knowledge) | `python -m orchestrator.cli gate-info --item <id>` (build_access, mcp_servers, ccd_confirmed, silent_perms, teams_notify, adx_access, oncall_now, play_console_access, oncall_window, saw_ame, yubikey) |
| Prepare early code-complete notice (JSON) — _legacy; prefer `step-action --step notice`_ | `python -m orchestrator.cli prepare-notice --release <YYYY-MM> [--variant initial\|update]` |
| Prepare flight & string reminders (JSON) | `python -m orchestrator.cli prepare-flight-reminder --release <YYYY-MM>` |
| Record a non-notification observation | `python -m orchestrator.cli record-step --release <YYYY-MM> --step <id> --status pass\|attention --detail "..." [--execution-id <id>]` — notifications require notification result; configured writers require their checked adapter or explicit owner resolution |
| Declare you CANNOT satisfy an item | `python -m orchestrator.cli decline --release <YYYY-MM> --item <id>` |
| Status (structured) | `python -m orchestrator.cli status --release <YYYY-MM> --json` |
| Advance to next gate | `python -m orchestrator.cli next --release <YYYY-MM>` |
| Approve the holding gate | `python -m orchestrator.cli approve --release <YYYY-MM> --comment "<why>"` — gates whose status includes `approval_command` require that command instead; generic `approve` refuses to bypass their external approval |
| Preview an external gate approval | `python -m orchestrator.cli approve-orchestrator-gate --release <id> --preview --comment "<comment>" [--phase <p> --step <s>]` — exact request and `review_hash`, no state/provider changes |
| Submit a reviewed external gate approval | `python -m orchestrator.cli approve-orchestrator-gate --release <id> --comment "<same comment>" --review-hash <hash> --approved-by <reviewer> [--phase <p> --step <s>] [--executor <session>]` — persists authorization and attempt boundary before the fenced write; `--reserve` saves authorization only |
| Recover an owned external gate approval | `python -m orchestrator.cli approve-orchestrator-gate --release <id> --execution-id <owned-id> [--phase <p> --step <s>]` — an attempted request only reads its exact provider approval, never resubmits; an unattempted reservation additionally requires its original hash/reviewer/comment |
| Deny the holding gate | `python -m orchestrator.cli deny --release <YYYY-MM> --comment "<why>"` |
| **Done** — mark a reminder (human to-do) complete | `python -m orchestrator.cli done --release <YYYY-MM> [--phase <p> --step <s>] --note "<what you did>"` |
| **Validate CCD** (temporal + pipeline; read-only; entry gate) | `python -m orchestrator.cli check-ccd --release <YYYY-MM> --json [--as-of YYYY-MM-DD]` → `{ccd, override, ccd_conflict, status: match\|past\|conflict\|unreadable\|unset, days_to_ccd, runway_days, compressed}` |
| **Set/change CCD** (writes pipeline; preview→confirm) | `python -m orchestrator.cli set-ccd --release <YYYY-MM> --date <YYYY-MM-DD> --reason "<why>" [--confirm]` |
| **Revert CCD to default** (2nd Wednesday) | `python -m orchestrator.cli set-ccd --release <YYYY-MM> --default --reason "<why>" [--confirm]` |
| **Skip/cancel the release** (writes pipeline) | `python -m orchestrator.cli skip-release --release <YYYY-MM> --reason "<why>" [--confirm]` (add `--clear` to reactivate; then re-plan/re-provision release automations) |
| **Skip** an eligible non-gate step (reason REQUIRED) | `python -m orchestrator.cli skip --release <YYYY-MM> --phase <p> --step <s> --reason "<why>"` — gates require `approve` or `deny`; future steps and steps with unmet prerequisites are rejected; legacy gate records completed without approval project as pending without mutating their evidence |
| **Reopen** a done/skipped step | `python -m orchestrator.cli reopen --release <YYYY-MM> --phase <p> --step <s> [--reason "..."]` |
| **Retry a blocked transactional effect after verified absence** | `python -m orchestrator.cli retry-effect --release <YYYY-MM> --phase <p> --step <s> --execution-id <id> --reason "<owner-reviewed evidence>" --confirm-absent` — available only when the configured handler independently proves the original resource does not exist |
| **Supersede a blocked idempotent desired-state effect** | `python -m orchestrator.cli supersede-effect --release <YYYY-MM> --phase <p> --step <s> --execution-id <id> --reason "<why current input replaces the partial operation>" --confirm-idempotent` — only for `effect_recovery: match_current`; the next run prepares a new operation |
| **Halt** (emergency, reason REQUIRED) | `python -m orchestrator.cli halt --release <YYYY-MM> --reason "<why>"` |
| **Resume** after a halt | `python -m orchestrator.cli resume --release <YYYY-MM> [--reason "..."]` |
| Show / analyze this release's log | `python -m orchestrator.cli log --release <YYYY-MM> [--analyze] [--json]` |
| Journal interaction (silent) | `python -m orchestrator.cli journal --release <YYYY-MM> --source scout\|user --text "..."` |
| Journal a step Q&A (silent) | `python -m orchestrator.cli journal --release <YYYY-MM> --kind qa --phase <p> --step <id> --question "..." --answer "..."` |
| Localization: checked launch | `python -m orchestrator.cli launch-localization --release <id> [--branch <branch>] [--source-version <full-sha>] [--variable NAME=VALUE …]` previews exact provider/source/variables. The CCD noon worker runs `--execute --auto-approve --executor localization-automation`, which recomputes the current plan, checkpoints its hash, fences one launch and verifies its build receipt without waiting for human approval. Manual/recovery execution can still use `--execute --review-hash <hash> --approved-by <reviewer>`. |
| Finalize / rollout auto-writers | `create-integration-prs`, `create-oneauth-common-pr`, `create-payload-wiki`, `start-release-signoff`, `start-upload-whats-new`, and `start-upload-alpha` are scheduled release automation, not owner approval gates. Their workers run with `--execute --auto-approve --executor integration-pr-automation|oneauth-common-automation|payload-wiki-automation|release-signoff-automation|upload-whats-new-automation|upload-alpha-automation`. If a provider result is uncertain, stop and recover the owned execution; do not retry. |
| Update only the wiki feature-flights section | `create-payload-wiki --release <id> --flight-section-file <UTF-8 markdown>` previews a patch of the existing `Expected Feature flags rollouts` section. It preserves all other page content and binds the exact live content, ETag and replacement text. Missing/duplicate headings, missing pages and mocked build/page inputs fail closed. Execute only after explicit review with the same file, `--execute --review-hash <hash> --approved-by <owner>`; `--auto-approve` is forbidden for section updates. Normal step eligibility, ownership and pause rules still apply; a completed non-repeatable wiki step needs an owner-authorized reopen before a new execution. Never regenerate the whole page to add a flight table. |
| Phase 5: owner-approved Beta start | `start-beta-play-store --release <id> [--manager-approved-by <manager>]` previews the exact pipeline-397224 `100% Beta - Play Store` stage request pinned to the completed Upload Alpha run. Execute only after the release owner approves the hash: repeat with `--execute --review-hash <hash> --approved-by <owner_email>`. The signed-in Azure CLI identity must be that owner. Friday requires the authenticated owner to attest manager approval via `--manager-approved-by` in both preview and execution; the manager is not independently authenticated. `--auto-approve` and execution-time `--as-of` are forbidden. |
| Localization: recover trigger receipt | `python -m orchestrator.cli record-localization-run --release <YYYY-MM> --execution-id <id> --build-id <buildId>` — reads and verifies provider pipeline/revision/repository/source/parameters and launch-window identity against the already-started review. Never authorizes a trigger or replaces an attached run. Recovery requires the pinned runtime. |
| Localization: one poll | `python -m orchestrator.cli check-localization --release <YYYY-MM> --execution-id <id> [--complete <true\|false> --run-result <ADO-result>] [--logs-file <OneLocBuild-task-log> --logs-complete] [--pr-status <active\|completed\|abandoned>]` — only the active execution with current readiness/frontier/time permission may poll. Read the exact recorded run's status AND result on every poll, plus PR status after discovery. Full logs and the documented PR-created line prove PR creation. A successful complete log with `/createpr: True` and no PR line stages a Code Reviews "no strings to localize" notice, then marks localization done after that send is acknowledged. Missing/partial/unrecognized logs never mean no strings; logs showing `/createpr: False` block as a misconfigured/test run. Manual no-change completion for unsupported logs still requires `--no-change-confirmation "<owner-reviewed explanation>"`. Failed/canceled runs block with a run link; unresolved evidence escalates after 3h even for a finished run. In-flight step-action polls the same execution; a blocked recorded run requires owner-reviewed reopen before a new reserved trigger |
| Localization: deliver staged follow-up | `notification prepare --release <YYYY-MM> --source pending` then claim/result for the initial PR, deadline warning or timeout email. A PR ID alone is not delivery evidence. |
| **Phase 2 — signal a re-triggered RC** | `python -m orchestrator.cli rc-retriggered --release <YYYY-MM> [--reason "..."]` — after the owner re-runs RC (flaky) or the orchestrator triggers a fresh RC (broker cherry-pick), reopens `mrwp_ecs`/`mrwp_local`/`rc_report` so Scout re-evaluates the **newest** RC. Holds `in_flight` (no action) while the run executes; the poller re-applies the gate on completion |
| **Phase 2 — one RC poll** | `python -m orchestrator.cli poll-rc --release <YYYY-MM>` — `waiting` / `nudge` / `ready` / `resolved` / `blocked` / `idle`. `ready` lists eligible Scout work: execute it and its follow-up, then re-poll. `resolved` requires all Phase-2 steps settled as done/skipped; distinguish `status: overridden` from `status: passed`. The worker lives until its owning phase completes, not merely an old RC report. |
| **Phase 3 — bind the invitation's meeting chat** | `python -m orchestrator.cli record-bugbash-chat --release <id> [--mcp-evidence-file <fresh-observations.json>] [--chat-id <expected-thread>]` — default prints exact WorkIQ MCP reads, then validates fresh complete evidence; Azure CLI/Graph requires explicit `--transport graph`; never chooses by title |
| **Phase 3 — one bug-bash update** | `python -m orchestrator.cli post-bugbash-update --release <YYYY-MM> [--force]` — `off_hours` / `no_chat` / `error` / `post` / `complete`. Missing/stale invite-chat bindings return no_chat, including with force. The final message's successful claim/result records `poll_complete`; central claimed cleanup retires the on-demand poller. |
| **Phase 3 — private/manual progress** | `python -m orchestrator.cli bugbash-progress --release <id>` — read-only JSON report, live ADO progress and current fill investigation evidence; no chat/Graph identity lookup, mentions, delivery permission, state save or completion. Works with owner-skipped chat/progress steps and during patch diagnosis. |
| **Phase 3 — OOF candidates / manual distribution preview** | `python -m orchestrator.cli distribute-tests --release <id> --oce <verified-primary-upn> [--json]` — owner/OCE/configured exclusions removed before listing candidates; missing OCE blocks without candidates; subsequent runs reuse the recorded OCE and availability |
| **Phase 3 — preview owner availability and corrections** | `python -m orchestrator.cli distribute-tests --release <id> --no-oof` OR `… --oof <verified-upn> [--oof <verified-upn> …] [--oce <upn>]` — only after explicit owner input; saves nothing. Repeat the same selections on execution. |
| **Phase 3 — validate current distribution** | `python -m orchestrator.cli distribute-tests --release <id> --validate --json` — read live ADO; report eligibility, balance, triage and tester mismatches without writes |
| **Phase 3 — apply reviewed corrections** | `python -m orchestrator.cli distribute-tests --release <id> --apply --review-hash <approved-preview-hash> --approved-by <reviewer>` plus the preview's OOF/OCE flags — reread ADO, apply only reviewed current corrections and read back; no stored assignment plan |
| **Phase 3 — inspect Broker plan recovery** | `python -m orchestrator.cli broker-plan --release <id>` — JSON resource record and all same-release candidates; read-only ADO |
| **Phase 3 — preview UI mapping repair** | `python -m orchestrator.cli broker-plan --release <id> --preview-ui-repair [--plan-id <id>]` — deterministic source/old/new configs and affected points; read-only ADO, no state save, bind, apply or cleanup |
| **Phase 3 — bind owner-selected existing plan** | `python -m orchestrator.cli broker-plan --release <id> --plan-id <plan-id> --reason "<owner selection>" [--area-path "<observed area>"]` — local identity binding only; no ADO writes, step completion, or plan replacement |
| **Phase 3 — authorize retry after confirmed non-creation** | `python -m orchestrator.cli broker-plan --release <id> --confirm-not-created --reason "<owner-reviewed evidence>"` — only after original runner stopped; also requires successful discovery with zero candidates and no recorded plan ID |
| Activate conditional hotfix phase | `python -m orchestrator.cli activate --release <YYYY-MM> --phase hotfix` |
| **Notify** — push line if something needs me | `python -m orchestrator.cli notify [--release <YYYY-MM>] [--as-of <date>] [--force]` |
| Prepare phase-completion status drafts | `python -m orchestrator.cli notification prepare --release <id> --source phase-status-email` |
| Prepare an engineer-requested status draft | `python -m orchestrator.cli notification prepare --release <id> --source ad-hoc-status-email --request-id <stable-request-id>`; read-only preview: `status-email --release <id> --ad-hoc <same-request-id>` |
| **Plan startup automations** | `automation plan --release <YYYY-MM> [--slug push-reminders\|daily-status-email] --json` — canonical metadata + complete `provider_spec`; excludes on-demand pollers; CCD one-shots require confirmed date, valid owner/host zones and a future target |
| **Plan one on-demand poller** | `automation plan --release <YYYY-MM> --on-demand <slug> --json` |
| **Discover durable on-demand obligations** | `automation obligations --release <YYYY-MM> --json` — reports `required`, lifecycle `recoveries`, active obligations and problems from current release state; run after every drain and even when `scout_pending` is empty |
| **Prepare automation intent** | Write the exact `provider_spec` once to a temporary JSON file, then run `automation prepare --release <id> --slug <slug> --name "<name>" --schedule "<schedule>" --purpose "<purpose>" --cleanup-when "<rule>" [--step <phase.step> …] --spec-file <temporary-spec.json> [--on-demand <slug>] --json`; identical replay preserves everything; changed intents reject. Inline `--spec-json` is retained only for small/manual inputs |
| **Reconcile/create automation** | Write the fresh exhaustive list/detail read as `{observed_at:<UTC>,complete:true,automations:[{id,spec}]}` to another temporary JSON file → `automation reconcile-create --release <id> --slug <slug> --spec-file <SAME temporary-spec.json> --observed-file <temporary-observations.json> --claim --executor <session> [--on-demand <slug>] --json`. Create only with permission, using exactly returned kwargs; then `automation create-result --release <id> --slug <slug> --spec-file <SAME temporary-spec.json> --attempt-id <attempt> --outcome created\|not_created\|uncertain --evidence "<exact invocation receipt reference>" [--id <created-id>]`. Delete both files only after the owning result is recorded. Incomplete/mismatched/owned observations hold |
| **Plan lifecycle cleanup** | `automation cleanup --release <id> --json`; `automation claim-delete --id <id> --executor <session> --json`; delete only with permission, then `automation delete-result --id <id> --attempt-id <attempt> --outcome deleted\|not_deleted\|uncertain --evidence "<receipt reference>"`. Stop on barriers/errors/uncertainty; retain recovery worker |
| **Confirm provider absence** | Owner verifies original runner and provider operation terminated: `automation confirm-absent --release <id> --slug <slug> --observed-file <fresh-observations.json> --reason "<owner evidence>" --confirm-absent --confirm-no-inflight`. Live creating/deleting claims must first record their owning result |
| **Abandon ID-less terminal intent** | `automation abandon-prepared --release <id> --slug <slug> --observed-file <new-fresh-observations.json> --reason "<owner evidence>" --confirm-absent` — only never-created/verified-absent prepared intents in complete/cancelled releases; zero ID/name matches, no unresolved live creation |
| **Inspect automation drift** | `automation sync --release <id> --json` — `permission_to_update:false`; owner-reviewed delete/recreate only, no update/register shortcut |
| **Inspect automation lifecycle** | `automation list [--release <id>] [--json]` — all eight existing statuses; schema v3 hash-only spec binding, no payload storage, direct register/deregister disabled |

`next` owns effectful in-process auto handlers. Their configured `effect_mode` is not a
manual command choice: `read_only` runs directly, `idempotent` resumes the same stable
operation, and `transactional` invokes handler reconciliation after interruption. A
persisted engine execution is stop evidence. Do not use `done`, `skip`, or a second create
to clear it; rerun `next`. `reopen` also refuses to discard effect ownership. Only a
configured `retry-effect` recovery can clear it, after provider-specific exhaustive
absence verification. A declared match-current idempotent writer instead uses
`supersede-effect` when newly validated desired state must replace a partial older write.
Both recovery commands validate the exact blocked owner, declared mode/recovery and
input hash. Retry verifies absence through the configured handler and checks the same
generation again afterward; stale results cannot clear a replacement. Owner-reviewed
recovery may clear old ownership during a halt/cancellation, but no new work starts
until suspension clears. Malformed state is shown as an integrity diagnostic: preserve
ownership and repair/review it, never infer successful completion from missing fields.

Upstream `reopen`, `rc-retriggered`, and conditional activation also refuse changes if
their invalidation closure contains an owned auto effect or external approval—even one currently blocked.
Settle that exact operation through its configured execute/reconcile path first. The
rejection preserves all evidence, gate decisions and permits; it is not an instruction
to clear ownership or start a second operation. Later-phase owners count; unrelated
same-phase parallel effects outside the closure do not. Already-invalidated/corrupt
owners require explicit review/repair, not an automatic reset or migration.

## Azure CLI discovery in background workers

Release-agent keeps a working `az` on PATH as its first choice. On Windows,
when it is absent, Azure/Graph/ADO and PR command adapters also check the standard
64-bit and 32-bit MSI locations (`ProgramW6432`, `ProgramFiles`, and
`ProgramFiles(x86)` under `Microsoft SDKs\Azure\CLI2\wbin\az.cmd`). This handles
Scout hosts retaining an obsolete x86 PATH after a 64-bit installation, without
changing user/machine PATH or rewriting canonical automation definitions.
Missing tools and authentication errors still surface explicitly. Discovery never
installs tools, signs in, retries a provider write, or acknowledges release work.

## External gate approval and recovery

### Record a gate already manually approved in ADO

Use `record-orchestrator-approval`, **not** `approve`, `done`, `skip`, a fake
reservation, or the submission recovery command when Scout never submitted it:

```powershell
python -m orchestrator.cli record-orchestrator-approval --release <id> --phase finalize --step remove_rc_tags_gate --approval-id <exact-approved-uuid> --preview
python -m orchestrator.cli record-orchestrator-approval --release <id> --phase finalize --step remove_rc_tags_gate --approval-id <same-uuid> --review-hash <reviewed-hash> --recorded-by <human-importer>
```

Review the exact target, provider proof, and one-gate impact before confirming
the hash. `publish_notes_gate` is a separate supported selection and requires its
own exact proof and review; importing Remove RC Tags never approves release notes.
The command reads only the **recorded** orchestrator build (no latest-run
discovery), requires the exact approval's approved status and owner build ID,
and follows the **same-ID** timeline approval checkpoint's unique, acyclic parent
chain to the selected configured stage. Stage completion alone proves nothing;
completed checkpoints remain eligible evidence. Missing/ambiguous/wrong-stage
evidence, stale workflow/generation/hash, pending/rejected approvals, nonempty
mocks, and existing owned work or receipts fail closed.

Import atomically records truthful `data.last_approval` evidence with
`provenance: external_manual`, target, proof, release/step/generation/workflow
binding, review hash, `recorded_by` and `recorded_at`, together with the gate
decision. It does **not** claim who approved in ADO, create an execution or
submission timestamp, write to ADO, or run downstream steps. An exact replay is
a no-op preserving the original receipt. Reopening preserves that historical
receipt and prevents silently replacing/reimporting it.

Source updates still require the normal **separately reviewed** `workflow-adopt`
preview/impact/hash before import. Never adopt automatically or assume zero
resets: show the exact completed/blocked-step, gate-decision and notification
impact and obtain owner approval. Import does not adopt workflows or reset work;
normal advancement remains a separate owner-controlled action.

### Submit or recover a Scout-owned approval

The two Phase-4 gates are independent: `finalize.remove_rc_tags_gate` authorizes **Remove RC Tags**
and the following publish stages; `finalize.publish_notes_gate` authorizes **Publish GitHub
Release Notes** after integration PRs merge. An approval for one stage never substitutes
for the other. Plain `approve`, generic `done`/`skip`, and outcome mocks cannot complete
an external gate.

1. Run `approve-orchestrator-gate --release <id> --preview --comment "<comment>"`,
   optionally with `--phase finalize --step remove_rc_tags_gate` or `publish_notes_gate`.
   Preview is read-only and returns `{request, review_hash, ...}`. Review the exact
   organization, project, build id, stage, approval id and comment with the human,
   alongside freshly rendered status. Missing, wrong-stage or ambiguous identities
   block; a completed stage does not invent an approval id.
2. After explicit approval, repeat the same comment and gate selection with
   `--review-hash <approved-hash> --approved-by <human-reviewer>` and optionally
   `--executor <session>`. No `--execute` flag is used for this command. Core
   validates the review and durably reserves the exact request before any provider write.
   Add `--reserve` to stop after saving authorization and return its execution id.
3. To submit a **never-attempted reservation**, repeat the original comment/hash/reviewer
   plus its `--execution-id`, omitting `--reserve`. The writer only receives the saved
   org/project/approval-id/comment; changing them is not a retry.
4. If an attempt was interrupted or its outcome is uncertain, call the command with
   `--execution-id <owned-id>` and the same gate selection. The command only reads the
   frozen provider approval. A matching id, expected build owner and **approved** status
   are required for success. Pending, missing, rejected, canceled, unknown or malformed
   evidence means hold—not resend. Neither a new run nor a later/completed stage can
   settle the original request.

`--as-of` remains a scheduling input only. Reservation, submission and receipt timestamps
always use the trusted current clock. Production rejects every nonempty local mock for
the gate before preview/submission/reconciliation, including legacy `submit: skip`.
BUILD `approval`/`stage_state` previews still work. Offline lifecycle tests inject fake
provider ports directly; mock evidence is never a production authorization shortcut.

The existing schema-v3 step stores only the approved nested envelope:
`execution = {id, owner, started_at, approval}` where `approval` is exactly
`{request, request_hash, workflow_revision, approved_by, submission_started_at, receipt}`.
The request is `{org, project, build_id, stage, approval_id, comment}`.
`submission_started_at` is null until the attempt boundary is checkpointed.
`receipt` is null or `{approval_id, status: "approved", observed_at}`; only a successful
fenced writer or positive exact reconciliation can create it. No top-level fields or
schema-version change are introduced.

Core may save matching receipt evidence during halt/cancellation; lifecycle completion
waits for eligibility. Finalization atomically moves `{execution_id, ...approval}` to
`data.last_approval`, clears `execution`, and records the human gate decision **before**
draining later steps. A failed downstream handler cannot lose that saved approval.
If completion/save fails, recover the same owner/receipt; never submit again.

Unresolved approval ownership blocks reopen, upstream invalidation and workflow adoption.
Restore the pinned runtime when needed and inspect exact provider evidence; there is no
automatic reset, forced clear, timeout lease or generic record-step bypass. Do not edit
the run-state JSON to discard uncertain work. An absent receipt proves only missing
evidence, not that the provider write never happened.

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

## Runtime-only patch recovery

Keep the owner's canonical run directory and stop competing release runners before
installing a patch. Never copy a diagnostic run back over their live progress.
Preview `workflow-adopt --release <id> --json`: a runtime-only patch has unchanged
phase definitions and zero step/gate resets. Unresolved automation attempts still block
adoption; do not delete registry entries, change hashes by hand, reinitialize, or claim a
second provider operation to clear them.

### Receipt-backed integration PR continuation

`recover-integration-prs` is a separate **manual**, same-execution recovery command,
not `retry-effect`, `record-step`, a new six-PR plan, or a new reservation. Stop the
original runner and any outstanding provider operation first. Keep the original
approved preview file and all hash-bound checkout/resolution inputs intact.

Preview (provider reads only; no state checkpoint or provider writes):

```powershell
python -m orchestrator.cli --runs-root <canonical-runs-root> recover-integration-prs `
  --release 2026-10 --execution-id a67b2a229c194c2288a6b19af1930acc `
  --executor scout-294e38ff-approved-prs-20261021 `
  --original-preview 'C:\Users\melissaahn\Repos\release-inputs\2026-10\integration-write-preview-after-recovery.json' `
  --existing-pbi 3796724
```

The preview binds the original active execution's review (never archived
`last_write_review`), exact owner/generation/revision, current runtime and fresh
provider receipts. It verifies the PBI's exact organization/project/type/title,
creation time and unique title lookup; PR source/base/repository/source commit,
open/unmerged/non-draft state, exact content/labels and ADO work-item links; every
approved branch tip; and resolution-file bytes. Complete all-state PR history is
required: closed/abandoned PRs, duplicate candidates, lookup errors, changed inputs
or unexplained effects hold. Only a contiguous applied prefix can be adopted.

Only Common's exact `, Fixes AB#<id>` title suffix and the known Engineering
work-item Markdown-link substitution are eligible content differences. Both
before/after strings are displayed under `content_changes_requiring_approval`.
This is **not** silent bot normalization or proof of who edited the PR. The owner
must inspect and manually approve the displayed content and receipts. Other
edits, missing/extra labels or changed source commits are never accepted.

After manual approval of the exact **fresh** `recovery_hash`, execution uses the
same arguments plus:

```powershell
  --execute --recovery-hash <fresh-recovery-sha256> `
  --approved-by melissaahn@microsoft.com --confirm-no-inflight
```

There is no `--auto-approve`, `--reserve`, PBI creation or ownership replacement.
Original approval provenance stays separate from recovery approvals. Bounded
operation receipts are checkpointed before and after each remote operation;
recovery repeats all guards before subsequent calls and uses the exact reviewed
commit bytes and compare-and-swap lease for pushes. New PR readbacks must match
exactly: even an eligible bot rewrite that first appears during execution stops
the invocation and needs another explicit recovery review.

After a crash or failed readback/checkpoint, an `in_flight` operation cannot be
reset to pending based on absence, even with `--confirm-no-inflight`. An exact
provider result can be reconciled only through a new fresh preview/manual hash.
If the result cannot be proved, retain ownership and stop. Generic completion,
skip, reopen and receipt annotation cannot bypass this writer's fenced attempt.
Successful completion settles the existing execution; it does not advance/drain
the release. Future ordinary integration writes also checkpoint operation fences.

**Broker dev fast-forward drift:** do not ignore drift or replace the original
preview. Add `--broker-target-tip <exact-current-dev-object-id>` to the recovery
preview, inspect `replans` and `effective_plan_hash`, and obtain a new manual
`recovery_hash` approval. Repeat that exact target argument when executing.

Externally merged GitHub PRs are accepted only for already-checkpointed applied
operations with unchanged identity/content. The provider merge commit must contain
the approved source and base commits, or be a squash with the exact approved source
tree and the reviewed base as its parent; a changed target branch must equal that exact
merge commit. Fetch those commits only into the isolated reviewed checkouts first.
The recovery preview displays `external_merges_requiring_approval`; obtain fresh
manual approval of this evidence. Closed-unmerged PRs, unknown merges, rewritten
branches, changed content, and further unexplained target changes remain held.
Only the pending Broker RI push and adjacent integration PR source/base guards
can change; the Broker freeze PR, every applied receipt, other repositories,
source selections, titles, bodies, labels and PBI identity stay unchanged.
The preview includes old/new target commits, complete target diff, exact merged
tree/commit bytes and file edits. Non-fast-forward targets, conflicts, dirty
checkouts, unexplained source changes, applied/fenced operations and uncertain
absence reject. Current provider tips must exactly match the proposed target.
Authorization checkpoints the bounded, hash-linked extension chain alongside
the original owner, prior receipts and approvals before any provider operation.
Further drift requires another exact fresh manual review, never an automatic
rebase. No replan is applied by previewing it.

**Owned integration runtime bridge:** installing a patch still does not make the
old owned attempt runnable. `workflow-adopt` continues to reject *every* owned
step execution. Use the separate, narrowly scoped
`migrate-integration-recovery-runtime` command below; the registry exceptions do
not cover this Scout writer. Never clear/reopen the execution, rewrite its review
hash, restore the retired reservation, or approve a new remaining-PR plan.

Preview the migration first (local reads only; no providers, state save, or
permission to write):

```powershell
python -m orchestrator.cli --runs-root <canonical-runs-root> migrate-integration-recovery-runtime `
  --release 2026-10 --execution-id a67b2a229c194c2288a6b19af1930acc `
  --executor scout-294e38ff-approved-prs-20261021 `
  --original-preview 'C:\Users\melissaahn\Repos\release-inputs\2026-10\integration-write-preview-after-recovery.json' `
  --json
```

Review the exact source/target runtime hashes, original approval and execution,
whole-state/registry fingerprints, and empty impact lists. After the owner
explicitly approves this migration and confirms the old runner/provider operation
has stopped, repeat the same arguments with:

```powershell
  --source-runtime-hash <plan.source_runtime_hash> `
  --target-runtime-hash <plan.target_runtime_hash> `
  --approve-hash <migration_hash> --by melissaahn@microsoft.com `
  --reason "<owner-approved reason for this runtime-only migration>" --confirm-no-inflight
```

Each migration requires unchanged phase definitions, the original exact
versioned preview matching the active execution/reviewer/generation and source
revision, and freshly loaded target sources. Other step owners, unresolved
resources/automation/notification owners, changed source/target hashes, stale
state/registry/artifact snapshots and nonzero reset impacts reject. Release then
registry locks are held through one atomic checkpoint. Failure rolls local state
back; a committed result is inspected rather than replayed as another migration.

Only the runtime binding, normal save timestamp and a bounded engine-owned audit
receipt change. Every step status, completion/invalidation timestamp, execution
ID/owner, original approval, gate decision, notification record and resource
remains intact. Original and target bindings and the manual migration decision
are retained separately. Old notification descriptors are not reauthorized.
Migration does not create an invocation permit, change the original envelope
bytes/hash, authorize provider work, or run recovery.

In a new command invocation, obtain a **fresh** `recover-integration-prs` preview,
inspect current provider receipts/content differences, and obtain separate manual
approval of its `recovery_hash`. This hash also binds the migration receipt. The
original source-runtime envelope is authenticated through that receipt; it is
never rewritten to the target revision. Final closed write-review provenance
retains the original review revision. Changed target code after migration is
still held. A later runtime-only patch requires another separately approved
zero-reset migration, then another fresh recovery approval. At most eight
migration receipts are retained, with each later plan binding the exact prior
receipts and the previous target binding as its source. Legacy single receipts
stay byte-for-byte intact in the version-2 chain. Existing recovery receipts and
approvals are preserved; any per-operation `in_flight`/uncertain journal blocks
migration, even when the runner is confirmed stopped. Migration is never
provider authorization.

### Automation registry receipts

For unchanged phase definitions, three existing **local registry recovery** commands
remain available before adoption: `automation create-result`, `automation delete-result`,
and `automation confirm-absent`. They do not call Scout's create/delete APIs or update
release-state. Missing/unbound state, changed phase definitions, wrong attempt IDs,
changed create specs and incomplete/stale absence evidence still reject.

On the original machine, inspect `automation list --release <id> --json` and the actual
Scout provider evidence. For a creating/uncertain entry, recover the original exact
provider spec and owning attempt ID; acknowledge only its proven result using
`create-result --slug <slug> --attempt-id <id> --spec-file <original-spec.json>
--outcome created|not_created|uncertain --evidence "<proof>" [--id <provider-id>]`.
For a deleting/delete_uncertain entry, use its stored provider ID and owning attempt:
`delete-result --id <provider-id> --attempt-id <id>
--outcome deleted|not_deleted|uncertain --evidence "<proof>"`.
Include `--release <id>` in each command. Do not infer deletion from the local registry
or a missing result receipt. A different machine's provider list proves nothing.

If the result remains unknown, record `uncertain` on the owning attempt, not success.
Only after the owner confirms the original runner AND provider operation have stopped,
and a fresh exhaustive provider list proves absence, use `confirm-absent --release <id>
--slug <slug> --observed-file <observations.json> --confirm-absent --confirm-no-inflight
--reason "<owner-reviewed proof>"`. Preserve the existing observation envelope contract.
If an automation exists but its exact original create spec cannot be recovered, stop for
owner recovery; never adopt by name alone or rewrite its intent hash.

After settling every blocker, preview adoption again (the old hash is stale), show its
exact impact and obtain owner approval. Apply that hash without advancing the release.
Only then resume normal operation. New prepares, create claims and delete claims still
require the current runtime. Step effects, external approvals and notification ownership
retain their existing pinned-runtime recovery rules.

## Bug Bash invite and exact chat identity

The invitation template's **Auth pipeline** link opens the current RC's Authenticator
ECS APK build (`auth.build.run_id`), supplied by `auth_ecs`. It is not a TBD and must
match the current RC. This differs from the progress report's Auth pipeline link, which
opens the post-build UI-test run. Missing/stale build metadata blocks invite preparation;
refresh the owning verification step rather than inserting an arbitrary run URL.

The first progress post is prepared when `bugbash_updates` is reached and delivered
through the normal approval flow; it does not wait for a polling interval or for the meeting
start. Only after confirmed delivery/completion is the on-demand poller provisioned.
Its cadence is owned by `bug-bash-update-poller.every` in `config/automations.yaml`
(currently **3 hours**), also used by notification checkpoints/expiry and generated prompts.
Recurring ticks send only within 09:00–18:00 Los Angeles on working days; they do not
guarantee a post exactly at 09:00. The first post is not subject to that tick-only gate.
When deploying a cadence change, update the existing worker's schedule/prompt instead
of creating a duplicate or replaying the first post.

Routine progress delivery uses bounded local storage, not a report archive. The shared
delivery contract keeps the current full approved payload while it can still be sent or
its outcome is uncertain. Never-claimed refreshes replace the previous preview without
keeping full superseded reports. Once sending AND completion have been saved, ordinary
updates retain only the notification/release identity, approved hash, destination,
execution ID, acknowledgement time, expiry and provider message ID (or evidence).

Retention runs on progress ticks and notification commands using the real clock, never
`--now`/`--as-of`. Expired `prepared`/`not_sent` updates are removed. Compact sent receipts
remain for 24 hours beyond expiry to cover supported cadence changes, then are removed.
They cannot be claimed again. `notification prepare --source pending` omits settled
compact receipts; add `--id` to inspect a retained one. Missing expired history is not
permission to replay. Claimed/uncertain and sent-but-unfinalized records are never aged
out. First/final notifications, calendar invitations, other notification types and any
record referenced by a source binding or step execution are untouched.
Deploy the updated delivery readers together and stop old workers before switching.
Older binaries cannot read compact receipts; do not delete recovery records to work
around that incompatibility. Retention is automatic, not a reason to resend anything.

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
`record-bugbash-chat --release <id>` as the organizer. By default it prints a
`needs_mcp_evidence` request, without changing state or using Azure CLI. Gather its
fresh WorkIQ reads: signed-in profile, exact event, calendar evidence explicitly
showing `isCancelled:false`, canonical online-meeting metadata resolved by that
calendar event ID, and the exact chat encoded in its Teams join URL. The
transcript-list tool is used only for canonical meeting metadata; do not read
transcript content. A missing transcript is fine if the complete meeting metadata
is present. Keep responses intact in the printed version-1 envelope, timestamp
the fresh reads, and repeat with `--mcp-evidence-file <path>`. Evidence older than
five minutes, missing fields, changed owner/times/cancellation, or mismatched
meeting/chat identity is rejected without changing state. Delete the temporary
evidence after successful recording. Use `--transport graph` only to explicitly
choose the older Azure CLI Graph read path; it is never a fallback after MCP failure.
Do not use `workiq_search_chats` or a matching title as identity.
`--chat-id` only asserts that a user-provided candidate equals the resolved thread.
If the thread is unavailable, open the exact event's Chat pane and retry; API permission
failures remain blocked, not a reason to accept an unverified chat.
In particular, HTTP 403 is an access failure, not an uninitialized chat; repeatedly
opening the event or waiting does not establish permission.

If the owner explicitly keeps chat/progress skipped and posts manually, leave those
decisions intact. `bugbash-progress --release <id>` returns the complete progress HTML
without mentions plus frozen `investigations` from the current completed UI fill,
including report-only/unmapped evidence. It reads live ADO owners/outcomes but never
reads Graph, offers a notification, writes a receipt, starts a poller, or attests work.
Show it privately for review/manual use; it is not a payload to send automatically.
Missing/stale fill evidence and failed reads remain errors, not zero outstanding tests.
Keep all three human decisions explicit: UI-failure review, Native Auth sign-off and
Bug Bash completion. Do not reopen chat or resend a manually delivered Native Auth ping
to obtain this report.

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
The code-generated header also links to the latest saved RC's MRWP ECS run, MRWP Local
run and Auth pipeline (the `auth.test.run_id` UI-test run, not the upstream APK build).
Keep those links unchanged alongside the Broker-plan and Authenticator-suite links;
never discover another run or substitute an older RC to fill a missing ID.
The same header includes **Get test accounts**, whose URL is owned by
`config/coordinates.yaml` → `links.test_accounts` and read through `coords.link("test_accounts")`.
Change that single config value to retarget the portal; share the link, not account credentials.
Render every workload case, including completed rows and all rows under finished owners.
Put unresolved work first, followed by Passed/N/A rows. Use only the leading icon for
each row's outcome, with a single legend: checkmark Passed, dash N/A, cross Failed,
stop sign Blocked, square Not run. Do not append redundant "(Passed - resolved)",
"(Not run)", "(Blocked)" or other outcome suffixes.
Every row identifies its source plan as `[Broker]` or `[Authenticator]` (both for a shared
case); do not infer product from titles or IDs. Automation triage keeps only its short
"(Automation triage)" context note; the icon reflects the current outcome. The final poller summary includes
the full completed list too. Only owners with remaining work are mentioned.
The numerator/denominator cover human manual/triage work, not the entire Authenticator
suite. The completed fill's automated-case classification excludes automation-only Auth
cases from both counts (as distribution does), while applied automation failures remain
visible as triage for BOTH Broker and Authenticator. Broker triage comes from the
completed fill's originally failed points in its exact UI suite, not just the Broker
manual subtree. Match point/case/configuration IDs; missing/changed points block.
Count each case once, requiring all originally failing configurations to resolve;
unexecuted configurations and successful automation do not inflate triage.
Preserve real manual Passed/N/A completions. Do not infer automation
from a Passed outcome, a shared tag, or a saved assignment list.
If the CLI Graph token lacks chat-member access, fetch the exact verified chat with
`workiq_get_chat` and save its fresh `id`, `chatType`, and complete `members` fields as
JSON (do not copy message history). Re-prepare the first post with `--param members_file=<path>`, or rerun
`post-bugbash-update` with `--members-file <path>`. The response must match the bound chat;
do not reuse another meeting's member list or hand-author identities.

## Checked finalization and rollout writes

Use the full JSON plan from the corresponding command, not a step-action summary.
The scheduled finalize/rollout writers run with auto-approval, so they recompute and checkpoint
the current plan hash at execution time:

- `create-integration-prs --release <id> [--repos common msal broker authenticator]
  [--checkout-root "<absolute clean-checkout parent directory>"]
  [--resolutions-file "<absolute pinned version-1 JSON file>"]
  [--pbi <existing-id>] [--pbi-title "<new PBI title>"]` binds selected repositories,
  hosting targets, exact branch tips/RI edits, existing PRs, labels and PBI linkage.
  Without `--checkout-root`, the usual repository locations are unchanged. When supplied,
  the existing directory must contain the selected `common`, `msal`, `broker`, and/or
  `authenticator` checkout directories directly beneath it. The resolved parent and
  actual checkout paths are hash-bound; repeat the same root when reserving/executing.
  This does not clone, fetch, initialize submodules, or modify the original checkouts.
  Every selected checkout must still be clean, match the exact provider repository and
  branch tips, and contain the objects required for isolated RI planning. Execution
  rechecks these guards and uses the normal authorization, CAS push and receipt fences;
  a different root requires a fresh review. Never use this option to bypass identity checks.
  Conflicts still hold by default. `--resolutions-file` supports only the explicitly
  reviewed MSAL `common` gitlink (`take: "base"`) and Authenticator
  `PhoneFactor/gradle.properties` (`take: "head"`) integration choices; it cannot edit
  freeze/release branches, existing integration PRs, or arbitrary paths. The JSON is
  exactly `{"version":1,"repos":{"<selected repo>":{...}}}`. Each repo contains
  `root` (normalized absolute checkout), `head`, `base`, `ancestor` (exact commit IDs),
  and `paths`. Each path contains `take` plus `ancestor`, `head`, and `base` entries,
  each exactly `{"mode":"160000|100644","object_id":"<exact entry ID>"}`.
  The Authenticator path additionally contains `values` with exactly
  `broker_adAccountsVersion`, `broker_commonVersion`, and `broker_msalVersion`,
  matching the final release versions. Its pinned head/base/ancestor blobs must
  prove both parents changed only those three property lines; base values must be
  the same finals with `-rc2-ecs`. Otherwise whole-file selection is refused.
  Unknown/duplicate fields, excluded repos, non-conflict paths, unsafe paths,
  symlinks and stale source/root/ancestor/entry pins are rejected. New conflicts
  require separate owner review, not an automatic choice or a general force merge.
  File bytes/path, all choices, original entries, resolved bytes/gitlinks, and the
  exact resulting tree/commit are review-hash bound. Execution rereads the spec,
  replans explicit resolutions before its first provider write, and rehydrates only
  the exact reviewed tree through the existing fenced CAS push. Preview uses
  disposable scratch objects only; do not hand-commit resolutions or bypass review.
- `create-oneauth-common-pr --release <id> [--repo-dir "<clean OneAuth checkout>"]`
  calculates the exact merge and version/changelog edits using existing local objects.
  It does not fetch, check out branches, or merge through an unreviewed server PR.
  Missing objects/dirty work/conflicts require separate resolution and another preview.
- `create-payload-wiki --release <id>` includes exact page content, target and
  existing-page ETag.
- `start-release-signoff --release <id>` binds the matching pipeline-397224 run,
  the `Release Sign Off` stage identity and the current stage state before setting
  that stage to `pending` (ADO's Run-stage operation).
- `start-upload-whats-new --release <id>` and `start-upload-alpha --release <id>`
  use the same checked plan shape for the `Upload What's New` and `Upload Alpha`
  stages on that selected pipeline-397224 run.

Execution uses `--execute --auto-approve --executor integration-pr-automation`,
`--execute --auto-approve --executor oneauth-common-automation`, or
`--execute --auto-approve --executor payload-wiki-automation`, or
`--execute --auto-approve --executor release-signoff-automation|upload-whats-new-automation|upload-alpha-automation`
respectively.
An uncertain or partial attempt keeps that owner; inspect provider evidence and explicitly resolve with
`done --note`, `skip --reason` or `reopen --reason` as appropriate. Never simply
retry a compound create. The latest `last_write_review` is retained authorization
evidence, not a provider-success receipt or permission to execute again.

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
   `--oce` is required before the first availability list; repeat it until a reviewed
   execution saves availability. It is not an OOF source. The full roster validates explicit/stored
   OOF answers, so an already-excluded person's older OOF entry does not re-enable them.
5. Show the live validation and any proposed corrections, including OOF names/UPNs.
   After explicit approval, use **`--apply --review-hash <hash from that preview> --approved-by <reviewer>`**.
   Repeat the preview's `--oof`/`--no-oof` and `--oce` selections; previews save nothing.
   Never use `done`/`record-step` as a substitute for availability confirmation.
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
digest, not an assignment list; only the approved digest/reviewer is saved with execution.
On `--apply`, live inputs
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
- **reopen** — a settled step (incl. an approved gate) needs to run again; reopening a gate makes it re-hold for a fresh decision. It cannot discard owned external approval work or invalidate upstream dependencies around that owner; recover its exact receipt first.
- **halt** — emergency freeze (e.g. production incident). **Reason required.** While halted, `next` refuses; status shows a HALTED banner.
- **resume** — clear a halt and continue.

Map natural language to these ("skip the CG report, doesn't apply" → `skip … --reason`; "halt, we have an incident" → `halt --reason`; "resume" → `resume`). Never skip or halt without capturing the user's reason.

## `step-action` — the generic step dispatcher
Apply the execution-reservation rule in **SKILL.md → The universal loop** before
acting on `reservable:true` results. The existing `done`/`reopen` commands recover
interrupted reservations only after owner review and stopping the original runner;
provide evidence via `--note`/`--reason`. Never automatically repeat an uncertain action.

`step-action` resolves a **migrated** step into one uniform outcome JSON (`kind`). It replaces the per-step `prepare-*` commands — react by `kind`:
- **`done`** — already complete or the authorized observation just completed; nothing to run.
- **`blocked`** — surface `reason` to the owner; don't proceed.
- **`in_progress`** — preserve the execution ID and follow the configured poller later; never retrigger the operation.
- **`needs_human`** — show `prompt` (attestation or reminder to-do).
- **`needs_skill`** — notifications MUST use prepare/claim/result, not raw tool/payload or legacy followup_command. Non-notification browser/gather/trigger work retains its named follow-up. Existing local test redirects are applied BEFORE snapshot hashing; never change a claimed payload.
- `completion.automation.on_demand` is an executor directive, not an MCP argument. After confirmed delivery and completion, provision that slug if absent using `automation plan --on-demand`. Source pending retains the directive for recovery if provisioning failed.

If a step isn't migrated yet, `step-action` returns `{"error": …}` with exit 1.
Scout notification steps use the shared contract; attest steps return `needs_human`
for the owner's explicit decision. Agent steps execute only in-process through `next`.

The dispatcher captures generation-bound permission before a handler runs and applies
canonical results only through guarded operations. Generic results cannot complete
human actions or approval gates. A new call cannot bypass suspension or readiness;
an already-invoked result or exact reserved receipt may still settle without enabling
downstream work. Stale/invalidated generations cannot complete replacement work.
Refresh respects `refresh_invalidation`: `always` blocks invocation/reservation before
provider work if an active effect would be invalidated; `status` allows observation but
rejects a status-changing result before applying its evidence; `never` does not invalidate
dependents. Status-preserving `status` refreshes remain allowed. Completion/omission of an
owned refresh uses the same policy. A rejected outcome retains its permit and prior
evidence; retry application only after the blocking owner settles, without replaying writes.
Notification receipts are retained while halted/cancelled, but lifecycle finalization
waits until suspension clears. Never interpret a retained receipt as permission to resend.
Claims bind their execution and notification ledger together. A proven `not_sent` result
can release only that exact current bound owner (including while suspended); stale
attempts cannot reset a reopened/new generation. `uncertain` and `sent` never release
ownership. No send permission is emitted until the claim is durably saved.

## Shared notification delivery and persisted schema

All commands below require an explicit release and use the existing OS-held state lock.

| Operation | Command |
| --- | --- |
| Persist exact preparation, without send permission | `notification prepare --release <id> --source step\|digest\|status-email\|phase-status-email\|ad-hoc-status-email\|pending [--phase <phase> --step <step> --param k=v]`; ad-hoc source requires `--request-id` |
| Reserve that exact target/payload | `notification claim --release <id> --id <logical-id:channel> --hash <hash> --executor <session>`; owner-reviewed drafts additionally require `--approved-by <release-owner-email>` after explicit draft approval |
| Acknowledge one channel | `notification result --release <id> --id <id> --execution-id <execution> --outcome sent\|not_sent\|uncertain --evidence "<proof>" [--receipt-file <JSON>] [--owner-review]`; `workiq_send_email` sent results require a receipt file with matching `tool`, `descriptor_hash`, `payload_hash`, `target`, and `provider_receipt.transport: "workiq_send_email"` |
| Retry domain completion, never sending again | `notification finalize --release <id> --id <id>` |
| Review a proven differently delivered daily email | `notification resolve-mismatch --release <id> --id <notification-id> --execution-id <original-id> --observed-file <JSON>` previews exact body differences and subsequent adoption impact. The JSON must contain the original `tool`, actual invocation `payload`, and successful `provider_receipt` (`transport`, `success`, `tool_call_id`, `event_id`, `evidence`). Recipients, subject, format and all non-body fields must match. After the owner reviews the exact hash and original runner is stopped, repeat with `--approve-hash <hash> --by <owner-email> --reason "<owner decision>" --confirm-runner-stopped`. This records a disposition only: status remains uncertain, ownership/attempts are preserved, no send receipt or completion is granted, and retries remain forbidden. It supports non-step daily email only, never step notifications. Workflow adoption still needs its separately reviewed hash and exact reset scope; rollout is never advanced. |

For normal notification delivery, the Scout runner reviews the descriptor and claims it silently.
For `requires_owner_approval:true`, show the full rendered draft, recipients, subject and
hash to the engineer and use `m_ask_user` with **Send this draft** / **Not now**. Only an
explicit Send decision authorizes claim with `--approved-by <release-owner-email>`.
Do not infer approval from a request for status, the draft request, or an automation run.
Send ONLY a successful claim's exact returned payload (`permission_to_send:true`) through
the claimed MCP transport; for `workiq_send_email`, use the direct MCP mail tool, never
browser/OWA/Outlook/manual compose.
For email, the receipt file must bind the actual send acknowledgement to the claimed
descriptor and payload hashes; a plaintext/manual/fallback email with different body,
HTML flag, subject, recipients or non-MCP transport must be recorded as
`not_sent`/`uncertain`, not `sent`.
Validation copies must also use the renderer-produced HTML/plain payload verbatim through
the MCP mail tool. Do not hand-compose shortened report emails that summarize or omit
sections; a failed MCP send is a failed validation send, not permission to switch to
browser/manual formatting.
`not_sent` requires proof nothing was sent; timeout, interruption or unknown outcome is
uncertain. Claims never expire. After successful send plus failed acknowledgement, retry
the acknowledgement only. Owner-reviewed recovery requires the original runner stopped.
Exactly-once delivery is impossible without downstream idempotency support.

### Phase-completion and ad-hoc status drafts

After each advancement and on every run, including release completion, prepare
`--source phase-status-email` before inspecting `--source pending`. Prepare one
`phase-status-email:<phase>:email` draft for each included, due, completed phase.
Exclude inactive conditional phases. Completion comes from engine projection, including
the actual gate decisions, never a guessed cursor or a successful-looking DUMMY result.
Show skipped work and DUMMY limitations in the rendered status. These emails report
workflow completion; they do not prove unfinished operations occurred.

Each phase identity is sent at most once per release, independently of daily and
step-required notices. The complete snapshot stays in the normal delivery ledger.
On an existing release, previously completed phases without a receipt are surfaced as
drafts for explicit engineer review, not silently sent as historical catch-up.
The final completed-release phase email is a narrowly scoped exception to the usual
closed-release send guard: it still requires proof of phase completion and owner
approval and cannot advance or reopen release work. Halts, cancellation, unsigned
readiness, source changes and runtime mismatches still block sending.

For an explicit engineer request, prepare:

```powershell
python -m orchestrator.cli notification prepare --release <id> --source ad-hoc-status-email --request-id <stable-request-id>
```

Generate one ID per new engineer request (1-80 letters/digits/period/underscore/hyphen,
starting with a letter or digit). Reuse it for that request's entire review/delivery/
recovery lifecycle. Do not generate IDs in a scheduled worker or to evade an uncertain
send. Resolve an existing claimed/uncertain ad-hoc email before another request.
To preview without saving a draft, use `status-email --release <id> --ad-hoc <id>`.

Ad-hoc drafts work in any open, signed, unsuspended release phase, before 17:00,
on weekends/holidays and after another email that day. They use independent
`ad-hoc-status-email:<request-id>:email` identities and never update
`last_status_email_date`, advance steps or complete release gates. They do not send
after release closure. Use configured partner recipients unless the engineer explicitly
requests a test redirect with `--send-to`; always show the actual destination in review.

For both draft types, wait for **Send this draft**, then claim with the exact displayed
hash, executor and `--approved-by <release-owner-email>`. The engine persists approval
provenance with the claim before granting send permission. A wrong/missing approver or
stale snapshot cannot authorize a send. Drafts expire at owner-local midnight;
refresh only never-claimed drafts and obtain fresh approval. Send the exact claimed
HTML through MCP and record its exact-payload receipt through the usual result/finalize
protocol. A claim, uncertain result or successful delivery never permits a duplicate.
**Not now** leaves the draft prepared without blocking independent release work.
Unattended workers prepare and surface drafts but never approve them.

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
