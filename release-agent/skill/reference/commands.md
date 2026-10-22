# Reference — Commands & manual overrides

_Loaded on demand. Run all from `<AGENT_ROOT>` — the confirmed `release-agent` folder (see SKILL.md → FIRST RUN; `python -m orchestrator.cli paths --json` prints it)._

New failure-only v4 sources freeze `classification_policy: 2`: canonical Stress source
suites route to distinct **Stress Tests** leaves before device/UI routing. Missing
policy means historical policy 1; existing v3 and v4 evidence, source hashes and create
payloads are not automatically reclassified. Only the explicit completed TEST ONLY
correction below records a checked effective-label amendment while retaining that history.
For this rename-only correction, exact acknowledged automation points may retain paired
published run/result IDs with Completed dictionary details. Their complete provider
history and tester fields are frozen in the reviewed preview and must remain identical
through rename/readback/recovery; duration or actor identity never proves origin.
Unreceipted manual history and inconsistent/in-progress metadata still hold.

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
| Guided onboarding (user-facing Scout prompt) | `/release-agent check setup` — automatically register declared missing MCPs through `infra`, load tools and handle required sign-in, then verify reads. No host-form/capability questions. Existing entries/auth/permissions are preserved; conflicts, repeated failures and denied access stop. No release discovery/init/advance. |
| Plan/resume onboarding internally | `python -m orchestrator.cli onboarding [--observation-file <temporary-scout-observations.json>] [--scout-root <existing-profile>] [--agency-path <executable>] --json` — read-only planner, no release state. `structured_fetch` discovers actual MCP or existing native CLI capability. `ready` requires fresh exact profile/calendar/roster/all pages/Teams/report site/library proofs; the user never creates this file. Conductor saves only compact profile-local routing/how-to, never a cached pass. |
| Discover releases | `python -m orchestrator.cli list --json` |
| Check setup BEFORE new-release init | `python -m orchestrator.cli infra --no-register --tools-file <fresh-inventory.json> --json` — enforces `m365_connectors` using the actual loaded canonical-to-exposed tool map and separate discovered `structured_fetch` MCP/CLI route. CLI help is not live access proof; missing required evidence blocks setup. |
| Exact native WorkIQ structured read (machine-only) | `python -m orchestrator.cli workiq-read --scout-root <selected-existing-profile> [--workiq-path <existing-workiq-launcher>] --account <verified-owner> --entity-url '<exact Graph-relative URL>'` — discovers the profile's existing `bin\workiq.cmd` or actual PATH launcher, verifies `fetch --help`, preserves Scout wrapper argv and prints the canonical `workiq.fetch` observation with actual route. No release state, tokens, raw-binary bypass or shell URL interpolation. Missing launcher/version support returns diagnostic/`--workiq-path` guidance; auth/denial stops, no alternate transport/identity. |
| Resolve installation paths | `infra --no-register --scout-root <existing-profile> --agency-path <executable> --json` — validate user-specific locations; ask for returned `path_requests`, never guess or overwrite an existing server. Custom-profile later CLI calls use `RELEASE_AGENT_SCOUT_ROOT`. |
| Start a new release | `python -m orchestrator.cli init --release <YYYY-MM> [--json]` — JSON returns persisted metadata and `next_action:run_entry_gate` / `checklist_command`; continue entry checks in the same turn, not just initialization. |
| Preview workflow revision adoption | `python -m orchestrator.cli workflow-adopt --release <id> --json` — old/new identity, conservative invalidations and ownership blockers; no execution. Before asking for approval, show `invalidation.summary`, every completed/blocked step being reset, removed gate decisions/offers, and blockers. |
| Confirm reviewed workflow adoption | `python -m orchestrator.cli workflow-adopt --release <id> --approve-hash <hash> --by <owner> --reason "<reviewed change>"` — recomputes under locks, rejects stale reviews/owned work, persists atomically without draining |
| List what a step exposes to `mocks.local.yaml` | `python -m orchestrator.cli mock-spec` |
| Show readiness entry checklist | `python -m orchestrator.cli checklist --release <YYYY-MM> [--verify] [--json]` — `--verify` runs Python checks ONLY; every output includes the remaining host-executed dispatch. |
| Guard readiness handoff | `python -m orchestrator.cli checklist --release <id> --attest-prompt --handoff --json` — exit 2 / `may_return_to_user:false` refuses a return/question while automatic probes remain executable; execute the queue, do not blindly retry the guard. |
| Run Python readiness verifiers | `python -m orchestrator.cli verify --release <YYYY-MM>` — execute the reported Scout queue afterward; never interpret Python success as complete readiness. |
| Attest human items (+auto verify) | `python -m orchestrator.cli sign --release <YYYY-MM> --item <id> [--item <id> …] --note "<what they confirmed>"` |
| Record a scout-assisted check (e.g. ICM on-call) | `python -m orchestrator.cli record-check --release <YYYY-MM> --item <id> --status pass\|fail\|degraded\|needs_input --detail "..."` — `needs_input` requires an actual consent/input reason, remains unsatisfied and lets independent probes continue before the question. |
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
| Phase 5: owner-approved Beta start | `start-beta-play-store --release <id> [--manager-approved-by <manager>]` previews the exact pipeline-397224 `100% Beta - Play Store` stage request pinned to the completed Upload Alpha run. Execute only after the release owner approves the hash: repeat with `--execute --review-hash <hash> --approved-by <owner_email>`. The signed-in Azure CLI identity must be that owner. Friday requires the authenticated owner to attest manager approval via `--manager-approved-by` in both preview and execution; the manager is not independently authenticated. `--auto-approve` and execution-time `--as-of` are forbidden. |
| Localization: recover trigger receipt | `python -m orchestrator.cli record-localization-run --release <YYYY-MM> --execution-id <id> --build-id <buildId>` — reads and verifies provider pipeline/revision/repository/source/parameters and launch-window identity against the already-started review. Never authorizes a trigger or replaces an attached run. Recovery requires the pinned runtime. |
| Localization: one poll | `python -m orchestrator.cli check-localization --release <YYYY-MM> --execution-id <id> [--complete <true\|false> --run-result <ADO-result>] [--logs-file <OneLocBuild-task-log> --logs-complete] [--pr-status <active\|completed\|abandoned>]` — only the active execution with current readiness/frontier/time permission may poll. Read the exact recorded run's status AND result on every poll, plus PR status after discovery. Full logs and the documented PR-created line prove PR creation. A successful complete log with `/createpr: True` and no PR line stages a Code Reviews "no strings to localize" notice, then marks localization done after that send is acknowledged. Missing/partial/unrecognized logs never mean no strings; logs showing `/createpr: False` block as a misconfigured/test run. Manual no-change completion for unsupported logs still requires `--no-change-confirmation "<owner-reviewed explanation>"`. Failed/canceled runs block with a run link; unresolved evidence escalates after 3h even for a finished run. In-flight step-action polls the same execution; a blocked recorded run requires owner-reviewed reopen before a new reserved trigger |
| Localization: deliver staged follow-up | `notification prepare --release <YYYY-MM> --source pending` then claim/result for the initial PR, deadline warning or timeout email. A PR ID alone is not delivery evidence. |
| **Phase 2 — signal a re-triggered RC** | `python -m orchestrator.cli rc-retriggered --release <YYYY-MM> [--reason "..."]` — after the owner re-runs RC (flaky) or the orchestrator triggers a fresh RC (broker cherry-pick), reopens `mrwp_ecs`/`mrwp_local`/`rc_report` so Scout re-evaluates the **newest** RC. Holds `in_flight` (no action) while the run executes; the poller re-applies the gate on completion |
| **Phase 2 — one RC poll** | `python -m orchestrator.cli poll-rc --release <YYYY-MM>` — `waiting` / `nudge` / `ready` / `resolved` / `blocked` / `idle`. `ready` lists eligible Scout work: execute it and its follow-up, then re-poll. `resolved` requires all Phase-2 steps settled as done/skipped; distinguish `status: overridden` from `status: passed`. The worker lives until its owning phase completes, not merely an old RC report. |
| **Phase 3 — bind the invitation's meeting chat** | `python -m orchestrator.cli record-bugbash-chat --release <id> [--chat-id <expected-thread>]` requests exact native connector observations. `--chat-link <link>` previews owner-confirmed recovery; confirmation requires the exact hash, owner and reason below. |
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
| **Isolated TEST ONLY — preview Local Stress label correction** | `python -m orchestrator.commands.broker_scenario_plan --snapshot <collected-state.json> --isolated-root <original-isolated-root> --name "<original TEST ONLY - name>" --correct-stress-category <exact-scenario-key> --correction-preview <new-preview.json>` — read-only provider proof and exclusive-create local preview artifact. Requires completed owned source-v4 policy 1, exactly one source-proven Local Stress row and one acknowledged Failed point in the old UI leaf. Reviews the exact modern suite PATCH, parent/configurations, all suites/points/results and old source/creation-ledger hashes. Mixed/empty leaves, unknown identities, changed human/results or other drift hold. |
| **Isolated TEST ONLY — apply reviewed Stress label correction** | Repeat the same snapshot/root/name/key with `--execute --confirm-source-sha256 <original-source-hash> --confirm-correction-sha256 <fresh-reviewed-correction-hash>`; omit `--correction-preview`. Obtain explicit scoped approval for the preview's exact external payload first. Checkpoints a separate classification amendment before **one** suite rename; never creates/deletes cases/suites/points, replays outcomes, resets defaults with classic PATCH, adopts a workflow or rewrites owned-case provenance. Only exact full readback publishes current Stress roles/categories/fill labels; frozen raw source, original create receipts and historical fill remain intact. |
| **Isolated TEST ONLY — recover uncertain Stress rename** | Replace `--correct-stress-category` with `--recover-stress-category <same-key>` for a fresh read-only preview; then use its new correction digest plus the original source digest with `--execute`. Recovery is **readback/local receipt only**, never a second suite PATCH. Old label, changed points/configs/parent/results, or pending atomic receipt hold for owner recovery; do not retry the rename or invoke create recovery. |
| Activate conditional hotfix phase | `python -m orchestrator.cli activate --release <YYYY-MM> --phase hotfix` |
| **Notify** — push line if something needs me | `python -m orchestrator.cli notify [--release <YYYY-MM>] [--as-of <date>] [--force]` |
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

## External gate approval and recovery

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

The schema-v4 step stores only the approved nested envelope:
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

## Authenticator Manual / Automated categories

By default new `bug_bash.clone_plans_auth` executions create an `auth-manual-automated-v1`
static release parent under the standing Authenticator root, with two dynamic
children: **Manual Tests** and **Automated Tests**. Use the owner's chosen
**verified current Phase 2 Android case mapping**, never Automation Status or
the generic Automated tag. Both queries preserve the curated Android/ReleaseBugBash,
area and non-Closed predicates; one excludes the frozen mapped IDs and the other
includes them. Both categories remain even when empty. No case copies or edits.

Clone data `suite_id` links to the static parent; `manual_suite_id` and
`automated_suite_id` are the direct children. Each create has a durable intent,
acknowledged ID and exact readback, including configuration inheritance. Missing,
duplicate or changed identities/source/queries hold rather than recreate.
The completed fill's typed auth target uses `suite_id` for the automated child
and includes parent/manual IDs plus frozen classification/query proof.

Only **Automated Tests** receives the later fill; Manual Tests stays untouched.
Distribution/progress read actual category points and the completed fill, retaining
manual work and failed-automation owner triage. Monthly Firebase remains report-only on v1.
Legacy single-query-suite executions/data recover as-is; never migrate, clear IDs,
reuse a same-named legacy suite as the static parent, or re-POST uncertain creates.
An explicit retry needs the owner's reason and exhaustive verified absence for
the owning unacknowledged category. Existing acknowledged IDs must reconcile.

### Explicit fresh Monthly failure-tracker layout

For an eligible **fresh** isolated clone, declare the reviewed input
`layout: auth-manual-automated-monthly-v2` alongside its distinct `name` in the
isolated `bug_bash.clone_plans_auth` mocks entry. This input selects the real
handler layout; it does not mock completion or provider calls. Run the automatic
step through `next` with the isolated config, runs root and mocks file.
`step-action` cannot execute this automatic step. The typed build/prepare
`layout` parameter remains available to explicit handler callers.
This is a provider-writing clone, **not a preview command**; do not execute it
without the exact scope review, engine eligibility and runtime-revision handling.
`tools.auth_monthly.preview(rc, release, parent_name)` renders source-bound JSON
without provider I/O and without manufacturing runnable release state.

V2 keeps Manual/Automated queries/IDs unchanged and creates a STATIC sibling
**Monthly UI Failure Triage** only when the validated initial Monthly source has
distinct unresolved failures after exact-title retries. Each gets one NEW owned
investigation case with exact provenance and `ReleaseMonthlyInvestigation`, never
`ReleaseBugBash` or automation IDs. No passing/NA/recovered initial cases, empty
folder, clone-time results, or clone-time assignments. P3S3 alone fills points
and publishes current receipts; failed-point Tester errors are visible/nonblocking.
Manual resolutions and previously tracked now-recovered rows/history remain.
Distribution/progress/invitations require completed fill and actual live points.

Existing completed/frozen v1 targets (including TEST ONLY parent3794450) **cannot
be amended by replay**. Use the separate checked amendment below, or an explicitly
reviewed fresh v2 clone. No Monthly unknown-create reset command is implemented.
Acknowledged IDs reconcile by exact readback; unknown POSTs block without replay
or same-name adoption. Never splice state or rerun an old frozen operation as v2.
Canonical E2E mappings and both source-execution quality gates remain unchanged.

### Checked existing-parent Monthly amendment

From the release-agent directory, use the **original owned state's config and runs
root**, not the normal release or an independent Broker state:

```powershell
python -m orchestrator.cli --config "<original-config>\phases.yaml" --runs-root "<owned-runs-root>" amend-auth-monthly --release "<owned-release-id>"
```

Default is read-only native preflight + exact JSON review. It binds the complete
original v1 clone/old pin, actual release identity, current source/builds/attempts,
native category identities/queries/inheritance/config IDs and explicitly reviewed
new command runtime. Review `review_hash` and separate `payload_sha256`. The only
prospective writes are one Monthly STATIC leaf (iff failures), new investigation
cases and memberships. No parent/Manual/Automated creates, results, Testers or sends.
Case IDs in membership preview are explicit references to the corresponding
prospective create ACKs; point IDs are server-generated, not fabricated.

After a **new exact explicit external approval**, repeat with:
`--execute --reserve --review-hash <sha256:hash> --approved-by "<reviewer>" --executor "<executor>"`.
The independent locked claim and intent ledger are durably checkpointed before
POST. Resume with the same hash/reviewer/executor (omit `--reserve`); acknowledged
IDs read back, never recreate. A completed repeat only validates, never resends.
Partial/source/config/base/runtime/claim drift holds. Unknown creates never repost
or adopt by name. Cancel an incomplete owned amendment using `--cancel` and the
same review/owner arguments plus `--reason "<owner reason>"`; history remains and
cancelled work cannot restart or publish.

Only `clone_plans_auth.data.monthly_amendment` is added. Original clone layout,
completion/execution/receipts and workflow runtime pin remain unchanged. Only a
complete sealed amendment resolves Monthly for P3S3/downstream, all of which require
completed current fill and live points. This compatible command authorizes **only
the amendment**; normal workflow runtime/eligibility guards are unchanged. In
particular, the isolated Auth-only state does not become a combined P3S3 context.
Local implementation/offline previews are **not** external approval or live proof.

Before a live isolated test, review the exact parent/child creation payloads,
source classification and a distinct TEST ONLY name, then obtain explicit approval.
No generic command, old Broker preview or prior public-push approval authorizes it.

## Broker plan recovery

New plans use source-v4 FAILURES ONLY, flight-first: Automation Failure Triage →
ECS/Local → nonempty failure categories, including UI Automation. Both manual
suites stay unchanged; zero failures is manual-only. No passed/NA/recovered cases
or invented UI suite ID. This tracker is NOT a coverage/pass-rate denominator.
Frozen v3 full-device and schema-less matrix plans retain their existing contracts;
never migrate them or adopt a pinned workflow automatically.
`--preview-ui-repair` is historical matrix-only, not a v3/v4 repair.
For an uncertain versioned plan/suite/case create, retain its operation key and original
runner ownership. After the owner supplies an exact provider receipt and reason:
`broker-plan --release <id> --recover-create "<operation-key>" --resource-id <id> --reason "<owner evidence>"`.
This only reads/verifies the frozen payload/provenance and saves that exact receipt;
no creates, guessed matches, ownership clearing or force-create. Legacy
`--confirm-not-created` cannot authorize source-v3/v4 retries. Normal workflow recovery
still requires the pinned revision; never bypass adoption holds.

### Isolated collected-data plan (explicit approval only)

For a fresh test, use a new explicit TEST ONLY name, not the name/receipt of a
held historical correction. The isolated instance is derived from release plus
name; reusing the same name intentionally resumes its existing frozen source
instead of rebuilding under policy 2. Never delete/reset the old receipt to force
new classification. Fresh plans create Stress Tests leaves directly and do not
depend on completing an old label correction.

From `<AGENT_ROOT>`, preview without state/provider writes:

```powershell
python -m orchestrator.commands.broker_scenario_plan --snapshot "C:\repos\android-complete\.release-runs\2026-10\release-state.json" --isolated-root "C:\repos\android-complete\.release-agent-isolated" --name "TEST ONLY - Fresh Stress separation RC2"
```

Optionally add `--approval-manifest "<new-session-local-file.json>"` to the READ-ONLY
preview. This exports frozen source, exact plan/suite/case/membership payloads,
target area/iteration, source build/run/result links and point-outcome intents.
New provider IDs are explicitly symbolic until actual receipts exist. Export uses
exclusive creation, cannot overwrite source/receipt, requires v4, and cannot combine
with `--execute`; remove that export flag before execution. A saved manifest can be
reviewed without repeating live source reads. Never substitute fake rehearsal IDs.
Show the owner the exact destination, source SHA256, source/build identity, flight/
category hierarchy, failure outcomes, owned-case/reference counts and warnings.
ONLY after explicit approval repeat the SAME arguments with
`--execute --confirm-source-sha256 "<reviewed source_sha256>"`.
This creates/fills only that isolated plan; it does not assign Testers, notify anyone,
bind a release plan, adopt/advance/reopen/complete the real workflow or save source state.
Master reads remain live read-only; changed source requires a fresh preview.
The synthetic identity differs from the real release marker. A separate durable
receipt/OS lock lives outside the source release directory. Keep it for recovery.
Re-running reuses acknowledged resources/results, not uncertain creates.
For exact isolated create-receipt recovery, repeat those arguments plus
`--recover-create "<key>" --resource-id <actual-id> --reason "<owner evidence>"`;
that call performs provider reads/local receipt updates only, not creation/fill.
New RC/inventory/failure sources require explicit new-source review; old resources
and resolved triage are retained, never silently deleted.
The earlier "TEST ONLY - Broker source scenarios RC2" instance remains frozen v3;
the distinct new name above cannot adopt, rewrite or clean up that historical plan.
For a bounded OFFLINE production-path proof with the collected snapshot, run:
`$env:RELEASE_AGENT_MRWP_STATE="<read-only release-state.json>"`
then `python -m pytest tests\test_broker_failure_triage.py::test_collected_rc2_failure_only_production_path -q`.
This injects only the existing in-memory ADO adapter; it does not create provider
records. Optional RELEASE_AGENT_APPROVAL_MANIFEST points to a new local JSON file.

An outstanding `.pending` receipt blocks further execution. Keep both versions and
their provider IDs; reconcile the exact difference under the isolated lock before
archiving a redundant pending file. Never discard pending evidence or clear create
ownership to retry. Windows sharing failures have a bounded atomic-replace retry;
permanent failures remain explicit holds.

Suite configuration pins are verified on resume, not reapplied to existing members.
If an unpublished owned plan has a proven full default-configuration expansion,
repeat the isolated arguments with `--repair-membership` for a read-only preview.
Show the exact removed points and obtain approval, then repeat with `--execute`,
the original `--confirm-source-sha256`, and
`--confirm-repair-sha256 "<membership_repair.repair_sha256>"`.
Only unrun default-expansion points may be removed; unknown/partial matrices,
human results and completed resources hold. Cases are never deleted. An uncertain
PATCH is readback-only, not permission to re-PATCH.

For the specific unfilled TEST ONLY completion-update area-reset regression,
`--repair-metadata` previews restoration of the original frozen subarea. Review its
exact payload against the original approved creation intent; execute using the same
source hash and `--confirm-repair-sha256 "<metadata_repair.repair_sha256>"`.
This cannot choose a new area, alter memberships/results or repair a real release.
Completion updates preserve metadata and require strict post-PATCH readback.
Both repair modes return without filling outcomes; normal isolated execution may
resume afterward. Retain their separate operation receipts, including uncertainty.

### Historical resource recovery

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

## Runtime updates and recovery

Keep the owner's canonical run directory and stop competing release runners before
installing a patch. Never copy a diagnostic run back over their live progress.
Same-contract implementation updates in a fresh process do not need adoption.
Only changed workflow phases need `workflow-adopt` impact review. Unsupported contracts
and old release-state schemas stop clearly; this new version has no migration adapter.
Do not delete entries, rewrite receipts or reinitialize an old live run to clear a hold.

For unchanged phase definitions, three existing **local registry recovery** commands
remain available before adoption: `automation create-result`, `automation delete-result`,
and `automation confirm-absent`. They do not call Scout's create/delete APIs or update
release-state. Missing/unbound state, changed phase definitions, wrong attempt IDs,
changed create specs and incomplete/stale absence evidence still reject.

Checked writes have no durable preflight reservation. Fix missing inputs and rerun
preflight; this is not an owner approval/reset gate. After an attempt is saved, inspect
the exact provider outcome and acknowledge its receipt. Never execute again with that
ID or treat a missing local receipt as proof nothing happened.

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

If workflow phases changed, settle owned work before reviewing adoption, then apply
only the displayed impact with owner approval. Otherwise continue on the same execution
contract without a code-update gate. Preserve each protocol's receipt/no-replay rules.

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
`record-bugbash-chat --release <id>` as the organizer. It prints exact read-only
`workiq_get_my_profile` and `workiq_get_event` requests; gather their timestamped,
owner-bound observation envelopes and supply `--profile-observation <file>` and
`--event-observation <file>`. It validates organizer/subject/times/complete typed
attendees, derives the exact thread encoded in that event's meetup-join URL, and
requests `microsoft_teams-GetChat` for that thread. Supply its envelope through
`--chat-observation <file>`. Missing evidence is a read handoff, not completion.
The engine performs no Azure CLI/Graph call. An opaque numeric join link has no
assumed onlineMeeting resolver; immediately offer owner-confirmed link recovery.
Do not use `workiq_search_chats` or a matching title as identity.
`--chat-id` only asserts that a user-provided candidate equals the resolved thread.
If the thread is unavailable, open the exact event's Chat pane and retry. HTTP 403
is an access failure, not an uninitialized chat; repeatedly opening the event or
waiting does not establish permission. On the FIRST access failure, immediately
offer owner-confirmed link recovery, manual posting, or keeping this held. Do not
require a successful Graph call before allowing the owner-confirmed path.

For owner-confirmed recovery, the owner opens the exact acknowledged invitation's
Chat pane, activates its chat, and copies a Teams chat/message link. Run
`record-bugbash-chat --release <id> --chat-link <link>` to preview without any Graph
request or state write. HTTPS Teams `/l/chat/`, `/l/message/`, and `/l/meetup-join/`
links containing a meeting thread are supported, including URL-encoded IDs and
`teams.cloud.microsoft`. Bare IDs, nonmeeting chats and arbitrary destinations reject.
If the saved invitation join URL encodes a different thread, recovery rejects it.

Show the fresh status plus exact invitation event/subject/times/recipients/link and
the preview warning, then ask for explicit confirmation that the owner activated
THIS invitation's chat and copied THIS link. On confirmation, run the same command
with `--confirm-activated --review-hash <hash> --approved-by <owner_email>
--reason "<owner confirmation>"`. Never infer this from merely pasting a link.
Changed invitation/receipt/link means a changed hash and requires fresh approval.
The command records `meeting.verification: owner_attested`, the review hash,
invitation digest, owner and reason; it never fabricates an onlineMeeting ID.
The confirmer's identity is asserted through Scout, not independently authenticated.
Current event/attendee state and posting access are not independently verified.
Without a thread-bearing join URL in the old receipt, linkage relies explicitly on
the owner's attestation, not an invented API observation.

This binding permits normal progress preparation; it does NOT send a message,
prove delivery, start a poller, or satisfy a release gate. Keep member verification
(including the WorkIQ members-file path below), approval, claims and delivery receipts.
If posting remains unavailable, immediately offer private manual progress rather
than silently looping. Missing/uncertain invitation receipts and existing terminal
skips still require their normal owner recovery; no forced completion or recreation.

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
Use `microsoft_teams-ListChatMembers` for the exact bound chat, or
`workiq_get_chat` when its complete exact-chat member response is available.
Wrap the actual structured response in the shared M365 observation envelope
(version/tool/exact arguments/account/actual observed_at/result); raw legacy
member JSON is not fresh account-bound evidence. Do not copy message history.
Re-prepare with `--param members_file=<path>` or rerun the poller with
`--members-file <path>`. Missing evidence returns a read-only connector request;
it never authorizes a send or step completion. UPN/SMTP differences require
explicit structured directory identity evidence, never a guessed mention.
Follow the returned exact `/users/<verified-UPN>` request through the discovered
MCP fetch or native CLI `workiq-read` route. Supply its canonical `workiq.fetch`
envelope in `directory_observations` alongside the member observation. Bind its
returned Entra GUID to an actual member GUID of this exact meeting; a directory
hit alone is not membership proof. Never use people search or WorkIQ Ask, or infer
that SMTP/local-part similarity identifies a member.

## Checked finalization and rollout writes

The lifecycle is project-wide: localization, report publication, test distribution,
integration/OneAuth PRs, payload wiki and all checked pipeline-stage starts share
`write_review.authorize`: one complete plan/validation and one atomic durable attempt
before execution. There are no reserve-only writes, cancellation commands, extra
recovery descriptors or step-specific unlocks. Read-only policy checks belong in plan
validation. Provider-specific preconditions still get checked before each real write.

Effects, notifications, external approvals, automation registry operations and the
independent Monthly amendment retain their specialized evidence/receipt recovery.
Never translate their owned/unknown outcomes into permission for another write.
Generic skip/reopen is not an ownership recovery protocol.

Use the full JSON plan from the corresponding command, not a step-action summary.
The scheduled finalize/rollout writers run with auto-approval, so they recompute and checkpoint
the current plan hash at execution time:

- `create-integration-prs --release <id> [--repos common msal broker authenticator]
  [--pbi <existing-id>] [--pbi-title "<new PBI title>"]` binds selected repositories,
  hosting targets, exact branch tips/RI edits, existing PRs, labels and PBI linkage.
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
A partial Git attempt keeps its owner and verified operation receipts. Run the same
checked command to reconcile its exact provider results and preview only remaining
work. Reuse the existing PBI and PRs; preserve proven branch updates. Apply a refreshed
remaining plan through the original review policy, not the previous stale hash.
Scheduled auto-writers retain their fixed auto-approval policy; changed human-reviewed
actions require a new exact review. A branch advance must never cause a stale RI push.

If an attempted call has no exact matching readback, keep it held. Do not infer absence
from a failed lookup or retry an unknown PBI creation. Do not use generic done/skip/reopen
to discard ownership. The latest `last_write_review` proves authorization, not provider
success; compact operation receipts separately prove reusable progress.

Do not trap the owner at that hold. Both Git commands support these result-only paths:
- `--manual-result <commit-or-PR-ID-or-URL-or-PBI-ID>` reads the exact original target
  and records the owner's manual result. A different manual commit/content is explicitly
  owner-confirmed, not certified as the earlier automatic payload.
- `--confirm-not-applied` requires the owner to confirm the previous writer stopped,
  plus successful source-branch readback matching the saved pre-push tip. It records
  a retired attempt without deleting it. Normal continuation then freshly plans/reviews
  remaining work; it does not replay the old payload. Unknown creates cannot use this.
- `--manual-completion <HTTPS-evidence-URL>` records the owner's explicit confirmation
  that the previous writer stopped and ALL remaining work in the original step is done.
  This closes only that step as owner-attested, NOT machine-verified. It needs no provider
  API or checkout and never certifies unknown operations or approves publication/merges/
  another gate. Omit all repository/PBI/path selectors: this is the whole original step.

Every path needs `--confirmed-by <configured-release-owner>` and the owner's
`--recovery-reason <explanation>`. Do not combine with `--execute`, `--auto-approve`,
`--executor`, review hashes or `--approved-by`. Recovery itself sends no provider writes.
Use the SAME command and normal approval policy to continue after an operation result;
whole-step manual completion needs no further writer invocation.

Present the failed action and offer **Record manual result**, **Confirm push never applied**
(only for a pending push), **Confirm all work manually completed**, or **Keep blocked**.
Use `m_ask_user`; collect missing result/evidence and the owner's explanation as needed.
For whole-step completion explicitly warn that the engine cannot verify it, and explain
the exact original step covered. Never choose the recovery for the owner, invent evidence,
or treat choosing "manual work" as a claim that all work is already complete.
After one failed read, offer the owner-attested path immediately; do not require another
successful API read before permitting it. Show final status with manual attribution.

Keep necessary checkouts/resolution inputs in durable owner-selected storage, not a
session/temp directory. A failed preview owns nothing. Once execution starts, recover
the original provider outcome. A compound writer may rebuild the remaining plan after
verified reconciliation, but must never replay completed or uncertain mutations.

## Bug Bash availability

Before `distribute_tests` computes the first manual-test preview, the **release owner**
must supply availability for **this release's Bug Bash**. Do not query calendars,
Teams presence, O365 automatic replies/OOF, or infer dates. Inspect actual Scout
tools and the selected profile's existing native CLI capability; logical structured
fetch can use either discovered route without restoring the removed custom remote
Hosted WorkIQ registration. Missing roster evidence returns an exact read handoff:
`/groups?$filter=mail eq '<configured-email>'&$select=id,displayName,mail&$top=100`,
then `/groups/<returned-id>/members?$select=id,displayName,userPrincipalName,mail&$top=100`
and every returned next page. Use the configured androididentity@microsoft.com;
the historical androidauth example is not a target change.
Call the actual exposed fetch tool or machine-only `workiq-read` with the exact
URL as a literal argument. Save `--roster-file <path>` as
`{observations:[<fresh owner-bound canonical workiq.fetch envelopes>]}` with
complete exact group/member evidence and actual pagination, recording the actual
route where available. No people-search, WorkIQ Ask or Azure CLI Graph fallback
is allowed. Authentication/denial stops; do not try another identity/transport.
The same complete roster evidence is required during setup/readiness, not waived.
Repeat the same roster file in preview/apply; source and membership participate
in the reviewed hash and are revalidated before each ADO Tester write.

1. Resolve the **current primary** on-call engineer using ICM
   `get_on_call_schedule_by_team_id` for the team in `readiness.yaml`'s `oncall_now`
   (currently 78848). Resolve the returned contact to a verified UPN; never guess it.
   Run `python -m orchestrator.cli distribute-tests --release <id> --oce <verified-upn> --json`;
   if it requests roster evidence, collect the exact fetch handoff above and repeat
   with `--roster-file <path>`. Retain that same fresh file for preview/apply.
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
work but receives triage. Apply writes only Current Tester on release plan/suite points,
never shared test-case System.AssignedTo;
neither outcomes nor Blocked tags are changed. A failed tester update is an incomplete
apply, not success. Review again if the triage set changes.

Current Broker failure-only flight/category suites retain separate Stress/UI targets.
Current Authenticator Manual/Automated/Monthly targets come from the completed fill.
Monthly investigations, including resolved points, never enter manual balancing.
A shared Broker case can independently use `B:<id>` for manual work and
`B:<id>:triage` for failure work; apply must keep their exact point scopes separate.

**ADO is the sole assignment source.** Validation reads live release-point Current
Tester, never the shared case's Assigned To, checks the distribution rules, and proposes
only needed corrections. It does not
silently rebalance. `next` holds when mismatches exist and completes this step when ADO is
valid. Existing valid manual changes are accepted without restoring any old allocation.

Previews exist only in command output/memory. The returned `review_hash` is an approval
digest, not an assignment list; only the approved digest/reviewer is saved with execution.
On `--apply`, live inputs
must still match the reviewed digest before any correction is written. Native writes
recheck the exact point/case/configuration and Current Tester; Monthly outcome-dependent
selection is rechecked too so a new resolution stops its assignment. Read-back must
confirm correct Current Testers before completion; tags and outcomes are preserved.

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
| Persist exact preparation, without send permission | `notification prepare --release <id> --source step\|digest\|status-email\|pending [--phase <phase> --step <step> --param k=v]` |
| Scout-reviewed reservation for that exact target/payload | `notification claim --release <id> --id <logical-id:channel> --hash <hash> --executor <session>` |
| Acknowledge one channel | `notification result --release <id> --id <id> --execution-id <execution> --outcome sent\|not_sent\|uncertain --evidence "<proof>" [--receipt-file <JSON>] [--owner-review]`; `workiq_send_email` sent results require a receipt file with matching `tool`, `descriptor_hash`, `payload_hash`, `target`, and `provider_receipt.transport: "workiq_send_email"` |
| Retry domain completion, never sending again | `notification finalize --release <id> --id <id>` |

For normal notification delivery, the Scout runner reviews the descriptor and claims it silently;
do not ask the owner to approve sending unless the engine explicitly surfaces a human gate/review.
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
