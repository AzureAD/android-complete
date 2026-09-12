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

### Authenticator detailed capture and intentional report-only coverage

`auth_ecs` completely pages Test Runs and Results once in Phase 2. It verifies the
test-build → APK resource link and each completed Test Run's build attribution and
aggregate/detail counts. `auth.test.evidence` freezes version, RC, APK/test build IDs,
run metadata, exact titles, outcome/attempt evidence and source run/result IDs.
Capture and neutral validation do not decide target-plan mappings. `rc_report` prepares
source-only counts, failures, recovery facts and links for renderers; `ui_test_status`
separately maps the capture during the Phase-3 fill. Distribution and progress consume
the fill's published result, not raw capture internals.
No alternate Phase-3 refetch. Missing/incomplete/mismatched data blocks before writes;
historical aggregate-only captures require refresh, not silent migration. Rediscovery
invalidates old Auth evidence while a new APK/test build is still pending.

**Firebase Test Lab - Monthly UI Tests intentionally has NO test-plan case map.**
This is valid report-only evidence, not a reason to create cases, force mappings or
block merely for unmapped coverage. Keep every failure by name and source link in the
standard HTML/plain/CLI report and existing release-owner `ui_failures` reminder.
Do not mark investigations complete or send outside the existing lifecycle.
Preserve owner notes and attestations when generated evidence changes.
The HTML uses **Broker UI-automation results** for the MRWP section. Broker source links
are attached to the failing titles in each suite card. The **Authenticator ECS** card
includes its full failing-title lists and source links grouped by Firebase suite,
immediately below the rates. There is no separate source-evidence/investigation panel
duplicating these lists. Monthly keeps its report-only mapping label.
These lists use already-prepared source facts; rendering must not collect/project again
or replace aggregate gate counts with distinct-title counts. Recovered titles do not
appear as unresolved; when retries explain aggregate failures with no unresolved titles,
the card explains that distinction.

Authenticator gates retain aggregate source-execution counts per exact gated suite
(Passed + Failed denominator), separately from Broker. Plan projection first reconciles
exact-title retries with Passed-wins, then applies Failed-wins across distinct mapped
scenarios (for example freshInstall versus upgrade). Execution counts, distinct tests
and mapped case points are not interchangeable. Monthly failures never enter Broker's rate.

### Phase-3 two-plan result fill uses this same evidence

`bug_bash.ui_test_status` projects only `rcs[-1]` through the pure tools-level
`project_mrwp_ui_results` helper. Do not refetch MRWP results in Phase 3, merge historical
RCs, or recompute retry verdicts. Missing/incomplete evidence, a stale `count_basis`,
or mismatched summary `build_id`/MRWP `run_id` blocks before **any** plan/assignment write:
refresh Phase-2 MRWP verification first.

The map preserves ECS/Local AND the full MSAL/Broker pair: 292/328 = PROD MSAL/RC Broker,
294/344 = RC MSAL/PROD Broker, 293/330 = RC MSAL/RC Broker (including LTW and mapped Stress).
BrokerHost explicitly rolls up into 292/328, matching the master subtree. Multiple distinct
titles/parameterizations/API suites mapping to one point use **Failed if any fails**,
otherwise Passed if anything passed, otherwise NotApplicable for NA-only evidence.
Only same-title retries within a normalized suite get pass-any, upstream. Unknown
title/suite mappings are explicit diagnostics (Lab API tests need not carry case IDs);
unmatched/manual plan points stay untouched. Compact fill provenance records the current
RC, build IDs, count policy and mapping statuses; raw attempts remain in Phase-2 evidence.
Assignments use only cases actually written Failed; failures without applied points remain
investigations in `ui_failures`, including every report-only failure. Reassignment errors
are separately visible and nonblocking, never claimed as owner changes. Recovered failures are cleared
on rerun without changing human completion or unrelated notes/data. Partial writes must
be surfaced, not reported as fully applied. New plans selectively add RC/RC points and
freeze that per-case matrix for recovery. Missing RC/RC points require
`broker-plan --preview-ui-repair`; the writer never mislabels or clears historical results.
The preview does not apply repairs or mutate release state. In-place changes need later
exact approval; cleanup additionally needs ownership receipts and match-before-write.
Authenticator mapping is owned by this fill; its independent Firebase gate stays unchanged.

The fill durably invalidates its prior result before any new attempt, retains partial-write
diagnostics, and publishes one completed result only after both required writes succeed.
`steps.bug_bash.ui_results.completed_result` exposes actual targets/points, automated and
applied-failed case IDs, investigation evidence and minimal authoritative identity binding.
`distribute_tests` consumes automated IDs; `bugbash_updates` consumes applied-failed IDs
plus live progress/assignments. Neither reacquires, reprojects, reconciles or fingerprints
raw Phase-2 evidence. Distribution preview/apply checks the receipt ID and current release,
RC, ECS/Local builds, Auth APK/test builds, and both plan/suite identities.

Missing, partial or stale results are errors, not empty work or successful historical
fallback. Refresh the owning clone/verification steps as needed and rerun `ui_test_status`;
older clone records also require the validated Broker UI suite ID from `clone_plans_broker`.
Do not silently migrate copied historical state. Renderers never call either target projection.

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
