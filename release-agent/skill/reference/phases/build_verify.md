# Reference — Phase `build_verify` (Phase 2 · Build & Lib Verification)

Opens **CCD+1** — the engineer wakes to a resume. The engine runs the four verification
**agent** steps in-process during `next`; you relay their results and drive the one
**scout** step (`rc_report`), which is the terminal Phase-2 step **and** the go/no-go —
there is no separate human gate.

## Execution model
Sequential. A single `next` runs the agent chain (checker → orchestrator → ECS/Local
MRWP); each **blocks** on a real problem (a stage that never ran, an unhealthy
orchestrator, an auth failure). A blocked step → show the note, then **fix + `next`** to
re-check, or **`skip … --reason`** to override. When the chain is green the scout
`rc_report` step runs — it is the last Phase-2 step and the go/no-go.

## Automated steps (no skill action — relay from the `status` table)
`checker_fired`, `orchestrator_health`, `mrwp_ecs`, `mrwp_local` — read-only `az` agent
steps run inside `next`. Each records the ADO run it evaluated as a Details 🔗 link.
`step-action` refuses them (exit 1); never dispatch them yourself.

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
  - **< 90% → `attention`** — the step **BLOCKS** (`awaiting_action`). Large UI failure: the
    owner investigates and decides — patch a real bug + re-trigger RC, or (if it's an
    automation flake to re-run later) proceed to bug bash. Exits: fix + re-run, then
    `next` re-runs `rc_report`; or `skip … --reason` to override.
  - (No UI tests found → `clean` with a ⚠ note.)
  It records the failing-suite summary + stashes the checker/orchestrator/ECS/Local run
  links on the step.
- The command prints `{verdict, blocking, pass_pct, ui_total, detail, links}` for your
  branching; relay the `status` table (the `rc_report` Details shows the verdict + 🔗 links).
  On `clean`/`warn` the engine auto-advances into Phase 3 — brief the owner and continue.

## External references
Engineering pipelines: Checker def 3038, Orchestrator def 2828, MRWP def 2519
(org `identitydivision.visualstudio.com`, project `Engineering`).
