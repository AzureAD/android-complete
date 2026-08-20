# Reference — Phase `build_verify` (Phase 2 · Build & Lib Verification)

Opens **CCD+1** — the engineer wakes to a resume. The engine runs the four verification
**agent** steps in-process during `next`; you relay their results and drive the one
**scout** step (`rc_report`) + the human **gate** (`go_test`).

## Execution model
Sequential. A single `next` runs the agent chain (checker → orchestrator → ECS/Local
MRWP); each **blocks** on a real problem (a stage that never ran, an unhealthy
orchestrator, an auth failure). A blocked step → show the note, then **fix + `next`** to
re-check, or **`skip … --reason`** to override. When the chain is green the scout
`rc_report` step becomes ready, then the `go_test` gate.

## Automated steps (no skill action — relay from the `status` table)
`checker_fired`, `orchestrator_health`, `mrwp_ecs`, `mrwp_local` — read-only `az` agent
steps run inside `next`. Each records the ADO run it evaluated as a Details 🔗 link.
`step-action` refuses them (exit 1); never dispatch them yourself.

## `rc_report` — email the RC report + apply the 90% UI gate (`scout`)
- **Trigger:** `status --json` shows current step `rc_report` (state `scout`), after the
  four agent steps are done.
- **Resolve:** `step-action --release <id> --phase build_verify --step rc_report` →
  `needs_skill` (`workiq_send_email`) with a fully-composed HTML dashboard, plus
  `payload.followup_command: record-rc-report`.
- **Act:** send the email verbatim (`payload.to/subject/body`, `isHtml:true`) — honoring
  a `send_to` redirect if the engineer set one. **Always send** — the owner gets the
  dashboard (failing suites + run links) whether the gate passes or not.
- **Record (the two-hop):** because `followup_command` is set, run
  `record-rc-report --release <id>` **instead of** `record-step`. It re-reads the model,
  applies the **90% UI-automation gate** (combined pass rate across ECS + Local):
  - **≥ 90% → `pass`** — the step is done; advance to `go_test`.
  - **< 90% → `attention`** — the step **BLOCKS** (`awaiting_action`). This is a large UI
    failure: the owner must **investigate the root cause** (usually a **fix + an MRWP
    re-run**). Exits: fix + re-run, then `next` re-runs `rc_report`; or `skip … --reason`
    to override. It records the failing-suite summary + stashes the checker/orchestrator/
    ECS/Local run links on the step.
  - (No UI tests found → passes with a ⚠ note.)
- The command prints `{verdict, pass_pct, ui_total, detail, links}` for your branching;
  relay the `status` table (the `rc_report` Details shows the verdict + 🔗 links).

## `go_test` — RC verified, proceed to bug bash (`gate`, human)
Present the settled `status`; `m_ask_user` Approve/Deny; `approve` / `deny --comment`.
Never authorize yourself. If `rc_report` blocked, `go_test` isn't reached until it clears.

## External references
Engineering pipelines: Checker def 3038, Orchestrator def 2828, MRWP def 2519
(org `identitydivision.visualstudio.com`, project `Engineering`).
