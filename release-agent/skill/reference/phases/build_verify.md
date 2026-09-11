# Reference — Phase `build_verify` (Phase 2 · Build & Lib Verification)

Opens **CCD+1**. The engine runs the five verification
**agent** steps in-process during `next`; you relay their results and drive the **scout**
steps (`telemetry_verify`, then `rc_report`) — `rc_report` is the terminal Phase-2 step **and**
the go/no-go — there is no separate human gate.

## Execution model
Sequential. A single `next` runs the agent chain (checker → orchestrator → ECS/Local
MRWP → Authenticator ECS); collection **blocks** on missing evidence (including absent
MRWP UI coverage), unexecuted MRWP stages, or an unhealthy orchestrator. Evaluated
test failures and a completed failed Authenticator build are reportable data, not
collection holds. Report execution still requires all predecessors, including telemetry,
to be done or explicitly skipped. Telemetry retains its existing policy and is never
automatically waived for a failed build.
A blocked step → show the note, then **fix + `next`** to re-check,
or **`skip … --reason`** to override. After collection and telemetry finish, the scout
`rc_report` step runs — it is the last Phase-2 step and the go/no-go.
Scout discovery and direct `step-action` both enforce phase readiness, timing,
sequential predecessors and explicit dependencies. Re-read `next` after each action;
do not dispatch later steps from an old list.

**In-flight vs blocked.** An MRWP or Authenticator build/test still executing is **`in_flight`**
(⏳ "RC running — Scout is polling"), **not** blocked — a stage that hasn't run *yet* on a
live run is not an aborted pipeline. It needs **no owner action**: the engine holds the
phase and the 30-min poller re-checks until the run completes, then the normal stage rule
+ UI gate apply. Only a stage that never ran on a **completed** run blocks.

## Automated steps (no skill action — relay from the `status` table)
`checker_fired`, `orchestrator_health`, `mrwp_ecs`, `mrwp_local`, `auth_ecs` — read-only `az` agent
steps run inside `next`. Each records the ADO run it evaluated as a Details 🔗 link.
`step-action` refuses them (exit 1); never dispatch them yourself.

## `telemetry_verify` — confirm bug-bash telemetry reaches Kusto (`scout`)
- **Trigger:** `status --json` shows current step `telemetry_verify` (state `scout`), right after
  `auth_ecs` and before `rc_report`. It's checklist Phase 3.3 Step 9, run here so the built APK
  version's telemetry is smoke-checked early.
- **Resolve:** `step-action --release <id> --phase build_verify --step telemetry_verify` →
  `needs_skill` with `tool: kusto_query` and `payload` = `{cluster_uri, database, query, version,
  source, links, followup_command}`. The APK comes from the current RC's persisted
  `pipeline_runs.rcs[].auth.build` captured by `auth_ecs` (pipeline 475778).
  Its ADO `buildNumber` supplies the app version before `-rc<buildId>`; never substitute
  the broker library version or the separate release-app pipeline 355246.
  Run the query with the given `cluster_uri`+`database` (the ADX MCP), read
  the returned **Count**, then run **`record-telemetry --release <id> --rows <N> --version <ver> --build-id <source.build_id>`**
  (do NOT blind-`record-step`):
  The recorder rejects stale/mismatched build evidence and negative counts. It stores
  source IDs, APK version/build number, query, cluster/database, count, timestamp and
  build link in `steps["build_verify.telemetry_verify"]` in the release JSON.
  - **rows > 0 → pass** — telemetry is flowing; the step is done and the flow continues to `rc_report`.
  - **rows == 0 → `attention`** — the step BLOCKS. Ask the owner to post a heads-up in
    **Android Core Team** that telemetry isn't reaching Kusto yet, then re-run once it is.
    This is an owner task, not permission for an ad-hoc automated notification.

## Reading and previewing the RC report

Use the existing HTML report renderer for local review; do not replace it with a
handwritten summary. Clearly label previews and provisional/incomplete data. A preview
never authorizes delivery, completion or extra copies; report sends use the shared protocol.

All MRWP categories use **distinct-test, any-pass-wins counts**: one exact title
within each normalized suite in the current build/provider counts once. Two Failed
attempts plus one Passed means SUCCESS; a Passed followed by Failed also stays success.
Keep parameterizations and suite/API/device distinctions separate, and never merge
ECS/Local or historical RC builds. A real non-NA result without any pass is one failure.
The denominator is **passed + failed**, excluding NA-only titles (`NotExecuted`,
`NotApplicable`, `None`/null, `Inconclusive`, `Warning`). The UI threshold stays 90%.
Authenticator Firebase retains its separate existing count/build/suite-selection policy.

HTML, plain text and CLI show **every unresolved failing title and every recovered
success**, with suite/provider context; never shorten these lists with "and N more."
Historical failed attempts are informational audit evidence, not extra gate failures.

One tools-level paged read of all Test Runs/Results, including successful reruns, feeds
both counts and details. `pipeline_runs.rcs[].ecs/local.tests` stores
`count_basis: distinct_tests_pass_any`, `build_id`, `categories`, `suites` and
`failed_suites`. Each suite has `run_ids`, `result_entries`, and `test_results` with
exact title, verdict, outcome counts and every attempt's run/result IDs.
Unreadable, missing or invalid result pages mean unavailable evidence, not aggregate
fallback or zero failures. Refresh stale execution-count snapshots before reporting
or gating; never relabel old counts, migrate them, or invent missing evidence.

### Phase-3 Broker UI result fill uses this same evidence

`bug_bash.ui_test_status` projects only `rcs[-1]` through the pure tools-level
`project_mrwp_ui_results` helper. Do not refetch MRWP results in Phase 3, merge historical
RCs, or recompute retry verdicts. Missing/incomplete evidence, a stale `count_basis`,
or mismatched summary `build_id`/MRWP `run_id` blocks before **any** plan/assignment write:
refresh Phase-2 MRWP verification first.

The existing case/config map preserves ECS/Local and PROD/RC-MSAL. Multiple distinct
titles/parameterizations/API suites mapping to one point use **Failed if any fails**,
otherwise Passed if anything passed, otherwise NotApplicable for NA-only evidence.
Only same-title retries within a normalized suite get pass-any, upstream. Unknown
title/suite mappings are explicit diagnostics (Lab API tests need not carry case IDs);
unmatched/manual plan points stay untouched. Compact fill provenance records the current
RC, build IDs, count policy and mapping statuses; raw attempts remain in Phase-2 evidence.
Assignments and `ui_failures` use these current verdicts; recovered failures are cleared
on rerun without changing human completion or unrelated notes/data. Partial writes must
be surfaced, not reported as fully applied. Authenticator's independent selected-run fill
and Firebase gate remain unchanged.

## `rc_report` — email the RC report + apply the 90% UI gate (`scout`, terminal)
For identical evidence, API arrival order must not affect verdicts, saved evidence
ordering or report ordering. Test-run/result IDs must be positive numeric IDs;
equivalent numeric forms are normalized before duplicate detection. Missing/invalid
IDs and duplicate IDs within a collection are incomplete evidence, not retries.

This is the Phase-2 go/no-go — there is **no separate approval gate**.
The HTML/plain report prominently states the combined recommendation: **WAIT** for
incomplete evidence, **STOP / HOLD** if either gate blocks, **CONTINUE WITH WARNINGS**
when MRWP has warnings and Authenticator clears, or **PROCEED** when both are clean.
This recommendation does not stop or change an ADO pipeline. Authenticator percentages
display exactly two decimals; gate calculations still use unrounded ratios.
- **Trigger:** `status --json` shows current step `rc_report` (state `scout`), after the
  five agent steps and telemetry are complete (or explicitly overridden).
- **Prepare:** `notification prepare --release <id> --source step --phase build_verify
  --step rc_report` prepares a report bound to the exact evaluated source evidence, verdict
  and run links. Claim freezes it; changed source data invalidates claims and suppresses
  stale completion even within the same RC/build. Retain any delivered receipt.
  Missing/zero evidence blocks preparation; evaluated failures still generate a report.
- **Claim:** review the exact target/payload and use `notification claim` with its ID/hash.
  Send only a durable claim returning permission_to_send; do not send raw previews.
- **Acknowledge:** `notification result --outcome sent` requires provider evidence and
  completes that exact report snapshot. No bare legacy recorder can invent a delivery.
  Failure after successful send requires acknowledgement/finalize recovery, never a resend.
  Completion applies the **three-tier 90% UI-automation gate** (combined pass
  rate across ECS + Local), independently of the Authenticator build/Firebase gate:
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
       After `rc-retriggered`, provision it with `automation plan --release <id>
       --on-demand build-verify-rc-poller --json`; create exactly the returned
       automation and register all fields including `cleanup_when`.
    2. **Cherry-pick (real bug)** — if a product bug is driving the failures, the owner
       patches it via the **broker cherry-pick process**
       (`…/internal-release-checklist/cherry-pick-process-for-broker-libraries`); the
       orchestrator then triggers a fresh RC. Same signal: **`rc-retriggered --release
       <id>`** so Scout tracks the newest RC to completion.
    3. **Override (LAST RESORT)** — **`skip … --step rc_report --reason "<why>"`**. Frame
       this explicitly as the last option: proceeding to Bug Bash with this many UI
       failures is a **team decision** and should be **discussed with the team first**, not
       taken as a default. The reason is recorded for audit.
    Missing evidence → hold without sending or recording a pass. A completed
    Authenticator build failure or explicitly absent suite in a completed test run
    is a reportable quality failure, not clean success.
  It records the failing-suite summary + stashes the checker/orchestrator/ECS/Local/Auth run
  links on the step.
- The command prints `{verdict, blocking, pass_pct, ui_total, detail, links}` for your
  branching; relay the `status` table (the `rc_report` Details shows the verdict + 🔗 links).
  Auto-advance requires **both** quality gates and all phase prerequisites to clear.

`poll-rc` returns `ready` with eligible Scout steps when telemetry/report work remains;
execute them and their exact follow-ups, then re-poll. `resolved` requires every Phase-2
step to be complete; a stale done report cannot hide an auth/telemetry blocker.
For `resolved`, report `status: overridden` as an authorized manual override, never
as passed quality gates; only `status: passed` represents a gate pass.

Terminal `done`/`skipped` records are not rewritten by late report callbacks. For an
earlier premature report, explicitly `reopen --release <id> --phase build_verify --step rc_report`
after owner review, and refresh any missing verification snapshots with `next`
(reopen completed collection steps first). No historical state is automatically migrated.
Skipping a prerequisite does not fabricate missing evidence; skipping the report itself
is the explicit, audited decision to proceed without it.

## External references
Engineering pipelines: Checker def 3038, Orchestrator def 2828, MRWP def 2519
(org `identitydivision.visualstudio.com`, project `Engineering`).
