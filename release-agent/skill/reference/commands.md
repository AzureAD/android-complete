# Reference — Commands & manual overrides

_Loaded on demand. Run all from `C:\repos\android-complete\release-agent`._

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
| Resolve a migrated step → outcome JSON (done\|blocked\|needs_human\|needs_skill) | `python -m orchestrator.cli step-action --release <YYYY-MM> --step <id> [--phase <p>] [--param k=v …]` |
| Answer a STEP question (knowledge) | `python -m orchestrator.cli step-info --step <id> [--phase <p>]` |
| **Phase 2 — RC pipeline + test report** (read-only) | `python -m orchestrator.cli rc-report --release <YYYY-MM> [--json]` → the checker→orchestrator→ECS/Local-MRWP chain + per-run test breakdown |
| **Phase 2 — record RC verdict** (after emailing the report) | `python -m orchestrator.cli record-rc-report --release <YYYY-MM>` → applies the **three-tier 90% UI-automation gate** across both MRWP runs, records `pass` (100% clean / ≥90% warn → step done, release auto-advances into bug bash) or `attention` (<90% → step **blocks** for investigation), and stashes the checker/orchestrator/ECS/Local run links on the step. This is the follow-up the `rc_report` `needs_skill` names — run it **instead of** `record-step` |
| **Simulate a mid-release point** (testing) | `python -m orchestrator.cli sim list` · `python -m orchestrator.cli sim run --scenario <name> [--freeze] [--json]` → **seeds the real release** to a scenario's target (`config/scenarios/<name>.yaml`): fast-forwards the real engine, signs the entry gate + completes earlier phases from mocks, then stops `open`/`gate`/`done` at the target. `data: live` runs the target phase against real `az`; `data: mock` is offline. Any existing state at that id is backed up first, so afterwards you use the **normal** commands (`status`, `rc-report`, `next`, `approve`). `--runs-root <path>` targets a throwaway sandbox instead; `--freeze` snapshots state to `tests/fixtures/<name>.json` |
| Answer an ENTRY-GATE item question (knowledge) | `python -m orchestrator.cli gate-info --item <id>` (build_access, mcp_servers, ccd_confirmed, silent_perms, teams_notify, adx_access, oncall_now, play_console_access, oncall_window, saw_ame, yubikey) |
| Prepare early code-complete notice (JSON) — _legacy; prefer `step-action --step notice`_ | `python -m orchestrator.cli prepare-notice --release <YYYY-MM> [--variant initial\|update]` |
| Prepare flight & string reminders (JSON) | `python -m orchestrator.cli prepare-flight-reminder --release <YYYY-MM>` |
| Record a scout-assisted phase step | `python -m orchestrator.cli record-step --release <YYYY-MM> --step <id> --status pass\|attention --detail "..."` |
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
| Localization: one poll | `python -m orchestrator.cli check-localization --release <YYYY-MM> --complete <true\|false> [--logs "<OneLocBuild@3 log>"]` — wait / timeout(email) / complete(post PR); acts on the printed decision |
| **Phase 2 — signal a re-triggered RC** | `python -m orchestrator.cli rc-retriggered --release <YYYY-MM> [--reason "..."]` — after the owner re-runs RC (flaky) or the orchestrator triggers a fresh RC (broker cherry-pick), reopens `mrwp_ecs`/`mrwp_local`/`rc_report` so Scout re-evaluates the **newest** RC. Holds `in_flight` (no action) while the run executes; the poller re-applies the gate on completion |
| **Phase 2 — one RC poll** | `python -m orchestrator.cli poll-rc --release <YYYY-MM>` — advances the drain (in-flight verify re-checks live status) and prints a decision: `waiting` / `nudge` (>6h courtesy heads-up to the owner) / `resolved` (passed → deregister the poller) / `blocked` (re-gate failed) / `idle`. Driven by the on-demand `build-verify-rc-poller` automation (every 30 min) |
| **Phase 3 — one bug-bash update** | `python -m orchestrator.cli post-bugbash-update --release <YYYY-MM> [--force]` — gates the working window (09:00–18:00 LA, weekdays, skips US holidays), reads live test progress, and prints a decision: `off_hours` (send nothing) / `no_chat` / `error` / `post` (send `content` HTML + `mentions` to `chatId`) / `complete` (all tests done → post the wrap-up, then **deregister the poller**). Driven by the on-demand `bug-bash-update-poller` automation (every 2 h) |
| Activate conditional hotfix phase | `python -m orchestrator.cli activate --release <YYYY-MM> --phase hotfix` |
| **Notify** — push line if something needs me | `python -m orchestrator.cli notify [--release <YYYY-MM>] [--as-of <date>] [--force]` |
| **Plan timed automations** | `automation plan --release <YYYY-MM> [--json]` — derive the per-release CCD automations (name/schedule/steps/prompt) from `config/automations.yaml` + CCD |
| **Track automations** | `automation register --id <id> --name "<n>" [--shared\|--release <YYYY-MM>] [--purpose "..."] [--step <phase.step> …] [--kind step-driving\|release-level]` · `automation list [--release <YYYY-MM>] [--step-filter <phase.step>] [--kind …] [--json]` · `automation deregister --id <id>` |

## Manual overrides (steer when reality diverges from the plan)
- **skip** — a step doesn't apply, or was done manually outside the tool. **Reason required** (audited). Confirm the reason, then run.
- **reopen** — a step (incl. an approved gate) needs to run again; reopening a gate makes it re-hold for a fresh decision.
- **halt** — emergency freeze (e.g. production incident). **Reason required.** While halted, `next` refuses; status shows a HALTED banner.
- **resume** — clear a halt and continue.

Map natural language to these ("skip the CG report, doesn't apply" → `skip … --reason`; "halt, we have an incident" → `halt --reason`; "resume" → `resume`). Never skip or halt without capturing the user's reason.

## `step-action` — the generic step dispatcher
`step-action` resolves a **migrated** step into one uniform outcome JSON (`kind`). It replaces the per-step `prepare-*` commands — react by `kind`:
- **`done`** — already complete; nothing to run.
- **`blocked`** — surface `reason` to the owner; don't proceed.
- **`needs_human`** — show `prompt` (attestation or reminder to-do).
- **`needs_skill`** — run `tool` with `payload` (an MCP/browser call the engine can't make, already fully resolved), then confirm with `record-step --step <record_as> --status pass\|attention`. **UNLESS `payload.followup_command` is set** — then run that engine command **instead** of `record-step` (it records the verdict itself): `rc_report` sets `followup_command: record-rc-report` (send the email, then run `record-rc-report` — it applies the 90% UI gate and records pass/block + links). Runs are real — the payload targets the real DL/chat unless the engineer's `mocks.local.yaml` has a `send_to` redirect (then `payload.to`/`chatId` points at them and the subject carries `[TEST → me]`).

If a step isn't migrated yet, `step-action` returns `{"error": …}` with exit 1. **Use `step-action` for scout steps** (`needs_skill` → run the tool, then `record-step`) **and attest steps** (`needs_human` → show the `prompt` via `m_ask_user`, then clear with `done --step <id>`). Migrated: scout — `preflight.notice`, `preflight.flight_reminder`, `preflight.lockdown` (gather-then-decide: its `needs_skill` carries a `_gather` browser-scrape directive + a `check-lockdown` follow-up); attest — `preflight.confirm_reminders`, `preflight.vitals`. **Agent steps** (`preflight.breaking`, `cg`, `cron`, `wiki`) are migrated too but the **engine runs them in-process during `next`** — `step-action` refuses them (exit 1); relay their results from the `status` table.


The human-readable commands (`checklist`, `status`, `next`, `approve`, `deny`, `decline` without `--json`) emit a **canonical block AND auto-log it**. Prefer these and show their output; use `--json` only for your own logic.

## Event logging (silent — never changes the interaction)
Each release keeps an append-only log at `.release-runs/<id>/events.jsonl` (per-release only; no machine-wide aggregate). For debugging only. **Invisible to the user** — don't announce it, don't add questions to populate it.
- **Scout output is logged automatically** by the CLI for every human-readable command — you don't journal what was shown.
- **User input is your responsibility** — the engine can't see what the user typed/clicked. Every time the user makes a choice, immediately (and silently) journal it: `journal --release <id> --source user --kind choice --text "<what they said>" --choice "<option>"`.
- **Step questions are interactions too** — when the user asks a detail/how/why/who question about a step and you answer from `step-info`, silently journal the pair: `journal --release <id> --kind qa --phase <p> --step <id> --question "<their question>" --answer "<one-line gist>"`. This is what surfaces missing/inaccurate knowledge later. Only when a release is active; skip if there's no run.
- Capture the **decision driver** passively: a reason given while approving/denying/declining → pass as `--comment "<their words>"` (or `--reason` for decline). No reason → empty comment. Never prompt just for the log.
- User asks "what happened" / "show the log" → `log --release <id>` (add `--analyze` for a rollup).
