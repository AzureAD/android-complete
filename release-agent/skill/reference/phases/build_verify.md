# Reference — Phase `build_verify` (Phase 2 · Build & Lib Verification)

Opens **CCD+1** — the engineer wakes to a resume. The engine runs the four verification
**agent** steps in-process during `next`; you relay their results and drive the **scout**
steps (`telemetry_verify`, then `rc_report`) — `rc_report` is the terminal Phase-2 step **and**
the go/no-go — there is no separate human gate.

## Execution model
Sequential. A single `next` runs the agent chain (checker → orchestrator → ECS/Local
MRWP); each **blocks** on a real problem (a stage that never ran, an unhealthy
orchestrator, an auth failure). A blocked step → show the note, then **fix + `next`** to
re-check, or **`skip … --reason`** to override. When the chain is green the scout
`rc_report` step runs — it is the last Phase-2 step and the go/no-go.

**In-flight vs blocked.** An MRWP step whose RC run is still executing is **`in_flight`**
(⏳ "RC running — Scout is polling"), **not** blocked — a stage that hasn't run *yet* on a
live run is not an aborted pipeline. It needs **no owner action**: the engine holds the
phase and the 30-min poller re-checks until the run completes, then the normal stage rule
+ UI gate apply. Only a stage that never ran on a **completed** run blocks.

## Automated steps (no skill action — relay from the `status` table)
`checker_fired`, `orchestrator_health`, `mrwp_ecs`, `mrwp_local` — read-only `az` agent
steps run inside `next`. Each records the ADO run it evaluated as a Details 🔗 link.
`step-action` refuses them (exit 1); never dispatch them yourself.

## `telemetry_verify` — confirm bug-bash telemetry reaches Kusto (`scout`)
- **Trigger:** `status --json` shows current step `telemetry_verify` (state `scout`), right after
  `auth_ecs` and before `rc_report`. It's checklist Phase 3.3 Step 9, run here so the built APK
  version's telemetry is smoke-checked early.
- **Resolve:** `step-action --release <id> --phase build_verify --step telemetry_verify` →
  `needs_skill` with `tool: kusto_query` and `payload` = `{cluster_uri, database, query, version,
  followup_command}`. Run the query with the given `cluster_uri`+`database` (the ADX MCP), read
  the returned **Count**, then run **`record-telemetry --release <id> --rows <N> --version <ver>`**
  (do NOT blind-`record-step`):
  - **rows > 0 → pass** — telemetry is flowing; the step is done and the flow continues to `rc_report`.
  - **rows == 0 → `attention`** — the step BLOCKS. Post a heads-up in the **Android Core Team**
    channel that telemetry isn't reaching Kusto yet, then re-run once it is.

## `rc_report` — email the RC report + apply the 90% UI gate (`scout`, terminal)
This is the Phase-2 go/no-go — there is **no separate approval gate**.
- **Trigger:** `status --json` shows current step `rc_report` (state `scout`), after the
  four agent steps are done.
- **Resolve:** `step-action --release <id> --phase build_verify --step rc_report` →
  `needs_skill` (`workiq_send_email`) with a fully-composed HTML dashboard, plus
  `payload.followup_command: record-rc-report`.
- **Act:** send the email verbatim (`payload.to/subject/body`, `isHtml:true`) — honoring
  a `send_to` redirect if the engineer set one. **Always send** — the owner gets the
  dashboard (failing suites + run links) whatever the verdict is.
- **Record (the two-hop):** because `followup_command` is set, run
  `record-rc-report --release <id>` **instead of** `record-step`. It re-reads the model,
  applies the **three-tier 90% UI-automation gate** (combined pass rate across ECS + Local):
  - **100% → `clean`** — step done; the release auto-advances into Phase 3 (bug bash).
  - **≥ 90% & < 100% → `warn`** — step done; auto-advances into bug bash, but the owner
    should investigate the failing UI tests **in parallel** (a later step confirms the
    retest — bug bash is **not** blocked).
  - **< 90% → `attention`** — the step **BLOCKS** (`awaiting_action`). This is a large UI
    failure. Present the note plainly, then walk the owner through **three exits** (do NOT
    reduce it to "fix or override"):
    1. **Re-trigger (flaky)** — if the owner judges the failures are automation flakiness,
       they re-run the failed RC test run, then signal **`rc-retriggered --release <id>
       --reason "..."`**. That reopens `mrwp_ecs`/`mrwp_local`/`rc_report` so Scout
       re-evaluates the **newest** RC. While the new run is still executing the verify step
       is **`in_flight`** (⏳ "RC running — Scout is polling") — **no owner action**; the
       `build-verify-rc-poller` re-checks every 30 min and re-applies this gate the moment
       the run completes. If it runs past 6h the owner gets one courtesy nudge.
    2. **Cherry-pick (real bug)** — if a product bug is driving the failures, the owner
       patches it via the **broker cherry-pick process**
       (`…/internal-release-checklist/cherry-pick-process-for-broker-libraries`); the
       orchestrator then triggers a fresh RC. Same signal: **`rc-retriggered --release
       <id>`** so Scout tracks the newest RC to completion.
    3. **Override (LAST RESORT)** — **`skip … --step rc_report --reason "<why>"`**. Frame
       this explicitly as the last option: proceeding to Bug Bash with this many UI
       failures is a **team decision** and should be **discussed with the team first**, not
       taken as a default. The reason is recorded for audit.
    (No UI tests found → `clean` with a ⚠ note.)
  It records the failing-suite summary + stashes the checker/orchestrator/ECS/Local run
  links on the step.
- The command prints `{verdict, blocking, pass_pct, ui_total, detail, links}` for your
  branching; relay the `status` table (the `rc_report` Details shows the verdict + 🔗 links).
  On `clean`/`warn` the engine auto-advances into Phase 3 — brief the owner and continue.

## External references
Engineering pipelines: Checker def 3038, Orchestrator def 2828, MRWP def 2519
(org `identitydivision.visualstudio.com`, project `Engineering`).
