# Reference — Phase `build_verify` (Phase 2 · Build & Lib Verification)

Opens **CCD+1**. The engine runs the five verification
**agent** steps in-process during `next`; you relay their results and drive the **scout**
steps (`telemetry_verify` and `rc_report`) — independent terminal children. `rc_report`
owns the quality go/no-go; telemetry remains a separate required prerequisite.

## Execution model
Parallel phase with an explicit ordered capture DAG. A single `next` runs the agent chain (checker → orchestrator → ECS/Local
MRWP → Authenticator ECS); collection **blocks** on missing evidence (including absent
MRWP UI+Stress coverage), unexecuted MRWP stages, or an unhealthy orchestrator. Evaluated
test failures and a completed failed Authenticator build are reportable data, not
collection holds. Exact dependencies are `checker_fired → orchestrator_health →
mrwp_ecs → mrwp_local → auth_ecs`, then **both** `telemetry_verify` and `rc_report`
depend on `auth_ecs`. Neither child depends on the other. A failed/canceled Auth build
therefore blocks telemetry but still allows the hash-bound HOLD report to be delivered.
Both children must clear before Phase 3; reporting never waives telemetry or invents success.
A blocked step → show the note, then **fix + `next`** to re-check,
or **`skip … --reason`** to override. After collection finishes, dispatch all eligible
Scout children even if one blocks; do not stop failure-report delivery at telemetry.
Scout discovery and direct `step-action` both enforce phase readiness, timing,
explicit dependencies. Re-read `next` after each action;
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
  `auth_ecs`, independently of `rc_report`. It's checklist Phase 3.3 Step 9, run here so the built APK
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
  - **rows > 0 → pass** — telemetry is flowing; the step is done. Phase 3 still requires the report.
  - **rows == 0 → `attention`** — the step BLOCKS. Ask the owner to post a heads-up in
    **Android Core Team** that telemetry isn't reaching Kusto yet, then re-run once it is.
    This is an owner task, not permission for an ad-hoc automated notification.

## Reading and previewing the RC report

### Checked SharePoint publication

`publish-rc-report` now authorizes a Scout upload handoff; the Python engine never
obtains a Graph token or uploads through Azure CLI. First preview the current report
and materialize its exact UTF-8 HTML at an absolute local path whose filename equals
the content-addressed filename in that preview.

For a NEW immutable file, supply `--upload-request-file <request.json>` with
`{version:1,route:"browser_new_file",local_path,library_context:{site_url,library,
folder,folder_url,resolve:<fresh exact-folder native observation>}}`. The context
must match the preview. If folder resolution is unavailable, only actual explicit
owner confirmation may substitute via `owner_confirmed:true`, `confirmed_by` and
fresh offset-aware `confirmed_at`; never default those fields.

Preview again with that same request file, review the exact source/target/action,
then use the checked `--execute` authorization. Execute only the ONE returned
browser handoff: navigate to the frozen folder, verify account/context from a real
snapshot, select observed Upload/Files controls, recheck local bytes/hash and upload
only that single path. Cancel any Replace/Overwrite prompt. If the immutable file
already exists, do not rewrite it: collect its exact provider bytes and recover
the existing execution only if every byte matches. Do not rename or widen sharing.

Native `workiq_upload_file` can replace only a proven existing exact item;
`route:"existing_item"` requires fresh native resolve/download observations proving
the same immutable bytes. Hosted WorkIQ new-file upload is not released. Do not
invent an uploader or use the existing-item API to create a filename.

After acknowledgement, collect `workiq_resolve_m365_link` and
`workiq_download_file` observations with exact arguments/account/time and complete
provider metadata/bytes. Canonical link resolution may return only drive/item IDs:
the result action then requests an exact ID-bound hosted WorkIQ DriveItem metadata
read; place its envelope in `metadata`. Native download text/base64 output is
accepted only when its bytes match the frozen SHA/size. Save
`{receipt_binding:<returned unchanged>,item_url,resolve:<envelope>,
metadata:<requested envelope when needed>,download:<envelope>}` and run
`publish-rc-report --release <id> --execution-id <owning id> --receipt-file <file>`.
This is result-only and never issues another upload. Unknown outcomes retain the
original execution; inspect/read the exact target rather than repeating execute.
Receipts are saved before source/completion checks, but stale content never passes
the current report gate. Permission/acknowledgement is not inferred from a preview.

Use the existing HTML report renderer for local review; do not replace it with a
handwritten summary. Clearly label previews and provisional/incomplete data. A preview
never authorizes delivery, completion or extra copies; report sends use the shared protocol.

All MRWP categories use **distinct-test, any-pass-wins counts**: one exact title
within each normalized suite in the current build/provider counts once. Two Failed
attempts plus one Passed means SUCCESS; a Passed followed by Failed also stays success.
Keep parameterizations and suite/API/device distinctions separate, and never merge
ECS/Local or historical RC builds. A real non-NA result without any pass is one failure.
The denominator is **passed + failed**, excluding NA-only titles (`NotExecuted`,
`NotApplicable`, `None`/null, `Inconclusive`, `Warning`). The threshold stays 90%
on the historical **UI+Stress aggregate**, not the newly separated UI-only row.
Explicit `Stress Tests` source suites have category `stress` and display **Stress Tests**;
classification uses the source suite/run name, never a test-title guess. New captures
record `category_basis: suite_name_stress_v1` and all four category summaries.
Validated historical three-category captures remain unchanged: the report derives a
separate UI/Stress view without rewriting frozen evidence. Missing/corrupt Stress
coverage is unavailable, not zero failures. Stress adds no independent blocking gate.
Historical Unit/Instrumented precedence is retained even for a `Stress Tests - ...`
name containing `UnitTests`/`InstrumentedTests`; only the historical UI bucket is split.
That derived report copy uses `legacy_suite_precedence_stress_view_v1`, not the new
capture classifier basis, so historical gate coverage remains unchanged.
Authenticator Firebase retains its separate existing count/build/suite-selection policy.

Report summaries must show **MRWP ECS, MRWP Local and Authenticator ECS separately**.
Do not combine ECS/Local into a headline rate or blend Authenticator into Broker counts.
All large percentages show **success rates**, including the Authenticator card's
**worst gated suite** (labeled explicitly). MRWP headline rates explicitly say
**UI+Stress**. Unit, Instrumented, UI, Stress Tests and Firebase rows show
`75.86% pass · 24.1% fail`. Checker, Orchestrator and Versions each get their own row,
with run links beside Checker/Orchestrator rather than beside Versions. Never average
Firebase suites to hide a failing suite. Missing/empty evidence is unavailable, not 0% failure.
These are presentation rules; the owning step's release recommendation policy is unchanged.

HTML, plain text and CLI show **every unresolved failing title and every recovered
success**, with suite/provider context; never shorten these lists with "and N more."
Historical failed attempts are informational audit evidence, not extra gate failures.

One tools-level paged read of all Test Runs/Results, including successful reruns, feeds
both counts and details. The hydrated `pipeline_runs.rcs[].ecs/local.tests` contains
`count_basis: distinct_tests_pass_any`, `build_id`, `categories`, `suites` and
`failed_suites`. Each suite has `run_ids`, `result_entries`, and `test_results` with
exact title, verdict, outcome counts and every attempt's run/result IDs.
Unreadable, missing or invalid result pages mean unavailable evidence, not aggregate
fallback or zero failures. Refresh stale execution-count snapshots before reporting
or gating; never relabel old counts, migrate them, or invent missing evidence.

### Separate evidence files

The main release-state JSON and normal status output keep compact summaries and
`evidence_ref` (relative path, SHA-256, artifact schema version) for ECS, Local and Auth.
Detailed snapshots live under the release directory's `evidence\rc<N>\` in immutable
`mrwp-ecs-<hash>.json`, `mrwp-local-<hash>.json` and `auth-ecs-<hash>.json` files.
State loading verifies each reference and restores the same logical inputs for reports,
telemetry and Phase 3; do not manually splice files or replace missing evidence with a
fresh ADO read. Surface the recovery error and restore the original bundle.
Legacy inline schema-v3 captures are extracted on the next normal save without step
reruns or changing decisions/source fingerprints; this is not a count-policy upgrade.
Preserve the whole release directory when copying test inputs or backups. Historical
and unreferenced files are not automatically deleted. This is storage separation, not
another rehearsal runner.

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
This is valid report-only canonical evidence, not authority for Phase-2 case creation, forced mappings or
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

`bug_bash.ui_test_status` projects only `pipeline_runs.rcs[-1]`. NEW source-v4 uses
`tools.broker_failure_triage`: reconciled FAILURES ONLY from BOTH complete MRWP
all-category snapshots. Conditional hierarchy is Automation Failure Triage →
ECS/Local → UI Automation, Stress Tests, Instrumented Tests, Unit Tests, Lab API, Native Auth E2E
and/or Other UI Automation; no empty categories/flights/root. Passing/NA/recovered
scenarios stay in Phase2 evidence/reports, not tracker cases. No failures means
manual-only. Exact scenario title/parameters/suite/API/flight and true build/run/
result links survive; no-ID concurrent failures are real owned cases, never dropped.
Verified singleton UI pairs may reference originals; collisions and other categories
use owned cases. Both original manual suites retain membership/config/query behavior.
The failure tracker is NOT execution coverage or a pass-rate denominator; broad
Phase2 UI totals/gates and Authenticator Firebase/Monthly remain unchanged.
Frozen source-v3 retains its full exact device inventory/additional-category layout;
older matrix resources keep their original contract. Retain resolved history and
explicit current source-outcome proof. RC/build/new-failure changes require a new
reviewed source before writes; never auto-migrate any existing plan/pinned workflow.
The legacy `project_mrwp_ui_results` helper remains for historical matrix resources.
New Authenticator release folders separately partition the curated bug-bash cases
into Manual Tests and Automated Tests using the verified case IDs mapped from this
captured current-RC Authenticator evidence. Classification does not use ADO
Automation Status or a generic Automated tag. The clone freezes RC/APK/test-build
identities and the mapped set; changed sources hold rather than move cases silently.
Only the automated child receives canonical E2E fills. Historical/v1 Monthly coverage
remains report-only. Explicit fresh `auth-manual-automated-monthly-v2` layouts additionally
own a STATIC `Monthly UI Failure Triage` sibling, containing only initially unresolved
distinct failed scenarios after exact-title retries; zero failures creates no folder.
These are release-owned human investigations, never canonical automation mappings.
P3S3 alone fills their exact bound points and attempts failed-point Tester assignment;
manual resolutions and acknowledged histories survive retries. New pass/NA/recovered
cases are never created. Old/frozen/completed layouts cannot be amended by replay.
This split does not change either gate or any canonical case ID.
Do not refetch MRWP results in Phase 3, merge historical
RCs, or recompute retry verdicts. Missing/incomplete evidence, a stale `count_basis`,
or mismatched summary `build_id`/MRWP `run_id` blocks before **any** plan/assignment write:
refresh Phase-2 MRWP verification first.

The map preserves ECS/Local AND the full MSAL/Broker pair: 292/328 = PROD MSAL/RC Broker,
294/344 = RC MSAL/PROD Broker, 293/330 = RC MSAL/RC Broker (including LTW and mapped Stress).
BrokerHost uses 292/328 while retaining exact scenario identity. In LEGACY resources, multiple distinct
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
be surfaced, not reported as fully applied. Historical plans retain their frozen
per-case matrix for recovery. Historical missing RC/RC points require
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
  five agent steps are complete (or explicitly overridden), regardless of telemetry status.
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
  - **100% → `clean`** — step done; Phase 3 also requires Auth quality and telemetry.
  - **≥ 90% & < 100% → `warn`** — step done; with Auth quality and telemetry clear, advances into bug bash, but the owner
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
       complete spec through prepare, fresh reconcile-create and owning create-result;
       never directly create/register.
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
  - **Authenticator ECS HOLD** — handle independently from the MRWP rate:
    1. Open the linked Authenticator ECS UI-test run and inspect the failing Firebase
       suite(s), failing titles and source links shown in the Authenticator ECS card.
       UIAutomator E2E below 90% is the usual gate hold. Monthly UI failures are not
       canonically mapped to ADO test-plan cases; never force such mappings. Fresh
       explicitly selected Monthly-v2 layouts may separately own failure investigations;
       still investigate the source failures by name and source link.
    2. If the owner judges the failures flaky or infrastructure-related, re-run the
       post-build Firebase UI tests for the captured Authenticator build. After the
       rerun completes, run `reopen --release <id> --phase build_verify --step auth_ecs
       --reason "Auth ECS UI tests reran"`, then `next --release <id>`. When `auth_ecs`
       is recaptured, resend the report with `notification prepare --release <id>
       --source step --phase build_verify --step rc_report`, then claim/send/acknowledge
       that exact payload.
    3. If this is a real Authenticator product bug, follow the
       [Android Authenticator cherry-pick instructions](https://identitydivision.visualstudio.com/IdentityWiki/_git/IdentityWiki.wiki?path=/IdentityWiki/Services/Microsoft-Authenticator/Release/Android/Cherry%252DPick-to-Android-Release-Instructions.md&version=GBwikiMaster&line=1&_a=preview).
       After the cherry-pick produces a new Authenticator ECS build + UI-test run, use
       the same reopen/next/report commands above to re-evaluate.
    4. Override only as a last resort after team discussion:
       `skip --release <id> --phase build_verify --step rc_report --reason "<why>"`.
  It records the failing-suite summary + stashes the checker/orchestrator/ECS/Local/Auth run
  links on the step.
- The command prints `{verdict, blocking, pass_pct, ui_total, detail, links}` for your
  branching; relay the `status` table (the `rc_report` Details shows the verdict + 🔗 links).
  Auto-advance requires **both** quality gates and all phase prerequisites to clear.

`poll-rc` returns `ready` with eligible Scout steps when telemetry/report work remains,
even if its sibling is blocked; execute them and their exact follow-ups, then re-poll.
`resolved` requires every Phase-2
step to be complete; a stale done report cannot hide an auth/telemetry blocker.
Reopening capture invalidates both children and downstream phases. Reopening telemetry
invalidates Phase 3 onward but preserves its independent report and delivery receipt.
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
