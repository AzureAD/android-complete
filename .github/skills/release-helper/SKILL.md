---
name: release-helper
description: Understand, navigate, and troubleshoot the Android Auth Client CI/CD pipeline system. Use this skill when asked about pipelines, release processes, build pipelines, hotfix workflows, daily validation, cron schedules, pipeline templates, release branches, RC testing, publishing to Maven Central, or any ADO/GitHub Actions pipeline question. Triggers include "how does the release pipeline work", "what pipeline does X", "where is the cron job for releases", "how do hotfixes work", "trace the monthly release flow", "pipeline template for Y", "how are broker apps built".
---

# Pipeline Explorer

Navigate and understand the Android Auth Client CI/CD system spanning two repositories.

## Repository Layout

Pipeline code lives in **two locations**:

| Repository | Path | Purpose |
|---|---|---|
| **AuthClientAndroidPipelines** | `1ES-Pipelines/` (local clone): `production/`, `non-production/`, `templates/`, `scripts/` | Primary pipeline repo (1ES-compliant). All monthly release, hotfix, weekly validation, UI automation, and publishing pipelines |
| **android-complete** | `azure-pipelines/`, `scripts/release/`, `.github/workflows/` | Legacy pipelines (dev builds, instrumented tests, test apps) and release helper scripts |

> **Key rule:** `AuthClientAndroidPipelines` is the source of truth for production pipelines. `android-complete` contains release scripts consumed by AuthClientAndroidPipelines and legacy dev-build pipelines.

## Pipeline Catalog (Quick Reference)

### Production Pipelines (AuthClientAndroidPipelines)

| Pipeline | File | Trigger | ADO ID |
|---|---|---|---|
| **Release Util (Cron)** | `non-production/daily-validation/release-util.yml` | Cron: `0 6 22-25 * *` (10PM PST, 22nd-25th monthly) | — |
| **Start Monthly Release** | `production/monthly-release/start-monthly-release.yml` | Triggered by Release Util or manual | `2828` |
| **Monthly Release (RC Testing)** | `production/monthly-release/monthly-release.yml` | Triggered by Start Monthly Release | `2519` |
| **Hot-Fix** | `production/hot-fix/hot-fix.yml` | Manual only | — |
| **Daily Validation** | `non-production/daily-validation/daily-validation.yml` | Scheduled / Manual | — |
| **Weekly Validation** | `non-production/weekly-validation/weekly-validation.yml` | Scheduled / Manual | — |
| **UI Automation** | `non-production/ui-automation/ui-automation.yml` | Triggered by monthly-release or weekly-validation via `queue-build.ps1` | `3076` |
| **Publish Internal** | `production/publish-internal/{lib}.yml` | Triggered by orchestrators | — |
| **Publish External** | `production/publish-external/{lib}.yml` | Triggered by orchestrators | — |
| **Linux Broker** | `production/linux/linux-broker-publishing.yml` | Manual | — |

### Legacy Pipelines (android-complete)

| Pipeline | File | Trigger |
|---|---|---|
| **Dev Builds** | `azure-pipelines/continuous-delivery/auth-client-android-dev.yml` | Scheduled daily |
| **Daily Builds** | `azure-pipelines/continuous-delivery/auth-client-android-daily.yml` | Scheduled daily |
| **Flighted Dev** | `azure-pipelines/continuous-delivery/flighted-auth-client-android-dev.yml` | Scheduled |
| **Instrumented Tests** | `azure-pipelines/instrumented-tests-multistage.yml` | Cron: `0 0 * * *` |
| **Test Apps** | `azure-pipelines/test-app/*.yml` | Cron: `0 3 * * *` |
| **Broker VSTS Release** | `broker/azure-pipelines/vsts-releases/*.yml` | Manual |

### GitHub Workflows (android-complete)

| Workflow | File | Trigger |
|---|---|---|
| **Release Integration PR** | `.github/workflows/release-integration.yml` | On branch creation matching `release-integration/*` |
| **AB#ID Validation** | `.github/workflows/validate-pr-ab-id.yml` | On PR events |

## Monthly Release Flow

> **Note:** All steps up to the manual bug bash are fully automated — the cron
> schedule triggers the entire flow without any human intervention needed to
> kick off the initial run.

```
CRON (22-25th monthly, 10PM PST)
  │
  ▼
release-util.yml ──── Gate: exactly 8 days before 1st? ──No──► Skip
  │ Yes
  │ Auto-compute versions from changelogs (get_next_version.py)
  ▼
start-monthly-release.yml (Pipeline 2828)
  ├─ 1. ValidateBranchesAndVersions ─── No conflicts, no existing artifacts
  ├─ 2. BranchSetup ─── Create release/ and working/release/ branches
  │     └─ Uses: android-complete/scripts/release/init_release.ps1
  ├─ 3. TriggerRC ─── Bump RC, trigger monthly-release.yml (2519)
  │     ├─ Uses: android-complete/scripts/release/update_rc.ps1
  │     └─► monthly-release.yml ─── Full build + unit/instrumented/E2E tests
  │                                  (E2E tests run against prod broker APKs
  │                                   — see "Production APK E2E Testing" below)
  │
  │  ▲▲▲ All steps above run automatically via cron — no human trigger needed ▲▲▲
  │
  │  ── Manual Bug Bash (outside pipeline) ──
  │     Once automated RC testing passes, the release engineer schedules a
  │     manual bug bash. The team tests RC builds across broker flows, MSAL,
  │     ADAL, and 1P app integration scenarios. Bugs found are fixed on the
  │     working/release/ branch and additional RCs are cut (update_rc.ps1),
  │     cycling back through automated testing until the build is stable.
  │
  ├─ 4. RemoveRCTags [APPROVAL GATE] ─── Strip -RC suffix
  │     └─ Uses: android-complete/scripts/release/remove_rc.ps1
  ├─ 5. FinalizedMonthlyBuild ─── Production-ready build of all libraries
  │     Uses finalized versions (no -RC suffix), ECS flighting (not local),
  │     no validation flags. Produces the actual release artifacts.
  │     └─► monthly-release.yml (2519) with final version params
  ├─ 6. PublishInternal ─── Azure Artifacts
  ├─ 7. PublishExternal ─── Maven Central (GPG signed)
  ├─ 8. UpdatePipelineVariables ─── Update ADO variable groups
  ├─ 9. CreatePRsToIntegrateRelease ─── Push release-integration/ branches
  │     └─► GitHub workflow auto-creates PR to dev
  └─ 10. PublishGitHubReleaseNotes
```

## Monthly Release Timeline

Typical end-to-end timing for a monthly release:

| Step | Duration | Type |
|------|----------|------|
| 1. Version Auto-Detection | ~3–5 min | Automated |
| 2. Validation | | Automated |
| 3. Branch Cutting | | Automated |
| 4. RC Initialization | | Automated |
| 5. RC Build + Automated Testing | **3.5–4.5 hours** | Automated |
| 6. Manual Bug Bash | **2–4 hours** (can be expedited to ~1 hr) | Manual |
| 7. RC Removal | ~1–2 min | Automated |
| 8. Finalized Monthly Build | **1–2 hours** | Automated |
| 9. Publish Internal | **~1 hour** (combined) | Automated |
| 10. Publish External (Maven Central) | | Automated |
| 11. Pipeline Variable Update | ~3–5 min | Automated |
| 12. Release Integration PR | | Automated |
| 13. GitHub Release Notes | | Automated |

**Typical total: ~8–12 hours of active time** (dominated by two build runs and the bug bash).
**Expedited total: ~6–8 hours of active time** (with a shortened 1-hour bug bash).

> Steps 1–4, 7, and 11–13 are negligible automation overhead. The three big blocks are **RC testing** (~4 hrs), the **manual bug bash** (~2–4 hrs), and the **finalized production build** (~1–2 hrs). The finalized build is shorter than the RC run because no E2E validation is performed — that's already covered during RC testing and the manual bug bash.

> **Calendar time vs. active time:** The totals above reflect active pipeline or human work. In practice, elapsed calendar time can be significantly longer. The cron job runs on the **22nd–25th of each month** regardless of the day of week. If the automated steps (1–5) complete on a **Saturday or Sunday**, the manual bug bash (step 6) will not be scheduled until the **next business day** (typically Monday). This means a release that triggers on a Saturday could span 2–3 calendar days even though the active work totals only 8–12 hours.

## Hotfix Timeline

Hotfix releases are **manually triggered** (no cron), skip the manual bug bash and finalized build, and can patch a subset of components. Assuming no manual bug bash:

| Step | Duration | Type |
|------|----------|------|
| 1. Version Resolution | ~3–5 min | Automated |
| 2. Branch & Library Validation | ~3–5 min | Automated |
| 3. Branch Setup (RC init) | ~3–5 min | Automated |
| 4. RC Build + Automated Testing | **3.5–4.5 hours** | Automated |
| 5. RC Removal (approval gate) | ~1–2 min | Manual approval |
| 6. Publish Internal | **~30 min** | Automated |
| 7. Publish to Maven Central | **~30 min** | Automated |
| 8. Update Pipeline Variables | ~3–5 min | Automated (conditional) |
| 9. Release Integration PR | ~3–5 min | Automated |
| 10. GitHub Release Notes | ~3–5 min | Automated |

**Active time: ~5–6 hours** — dominated by the RC build + E2E testing. Publishing is the only other meaningful block.

**Calendar time: Same day** — with no manual bug bash there is no dependency on business hours. The pipeline runs end-to-end without waiting for humans (aside from the approval gate click at step 5). A hotfix triggered on a weekend completes the same day.

> **Key differences from monthly release:** No bug bash, no separate finalized build (the RC build becomes the final build after RC removal), and partial releases (broker-only, msal-only, etc.) may reduce build time further.

> **If a manual bug bash is required**, the hotfix effectively becomes equivalent to a full monthly release process — add ~2–4 hours for the bug bash plus ~1–2 hours for a finalized build, and the same calendar-time caveats apply (bug bash won't be scheduled until the next business day if triggered on a weekend). In that case, refer to the [Monthly Release Timeline](#monthly-release-timeline) for expected durations.

> **Fully expedited (emergency) release: ~1–2 hours.** See [Emergency (Expedited) Hotfix Release](#emergency-expedited-hotfix-release) below.

### Emergency (Expedited) Hotfix Release

> **WARNING:** This procedure skips all automated validation (unit tests, instrumented tests, E2E / UI tests). It should **only** be used when all of the following are true:
> 1. The fix has **already been validated** independently (e.g., manual testing, separate CI run, or prior RC cycle).
> 2. The issue is **critical** and cannot wait for a standard hotfix cycle (~5–6 hours).
> 3. The release engineer has **explicit approval** from the team lead or on-call DRI.

**How to run an emergency hotfix:**

1. **Navigate to the Hot-Fix pipeline** in Azure DevOps: `production/hot-fix/hot-fix.yml` (manual trigger only).
2. **Click "Run pipeline"** and fill in the standard hotfix parameters:
   - `brokerVersion` / `commonVersion` / `msalVersion` — set the version(s) to patch (use `skip` for components not being patched)
   - `isLatestRelease` — set to `true` if this patches the current production release
3. **Skip RC testing entirely** by setting the debug parameter:
   - `debugSkipRCTesting` = `true`

   This bypasses the RC build + E2E stage completely. The pipeline will proceed straight from branch setup to the approval gate (RC removal), then to publishing.

4. If you still want the build to run but need to **skip only the test suites** (unit tests, instrumented tests, E2E), leave `debugSkipRCTesting` as `false` and instead override these parameters on the **Monthly Release Work Pipeline** (2519) when it is triggered:
   - `shouldRunUnitTest` = `False`
   - `shouldRunInstrumentedTests` = `False`
   - `shouldRunUiValidation` = `False`

   This will still produce the build artifacts but skip all test execution, reducing the RC stage from ~3.5–4.5 hours to ~30–60 minutes (build-only).

5. **Approve the RC removal gate** as soon as the pipeline reaches that stage.
6. The pipeline proceeds to publish internally, publish to Maven Central, update pipeline variables, and create the release integration PR as normal.

**Expected time:** ~1–2 hours end-to-end (branch setup + build-only or no-build + publishing + automation cleanup).

## Production APK E2E Testing

The monthly release E2E test configuration runs against **production broker APKs** (e.g., Company Portal, Authenticator). These APKs are **not built by the pipeline** — they must be manually uploaded to the Azure Artifacts feed beforehand.

### How it works

- The E2E tests in `monthly-release.yml` (Pipeline 2519) are configured to pull production broker APKs from the **`Android-Broker`** Azure Artifacts universal package feed.
- These are the same signed APKs that ship to end users, ensuring E2E tests validate against real production builds.

### Uploading production APKs

The release engineer (or a team member) must manually upload the latest production APKs to the artifact feed before RC testing begins. The current production versions are tracked in the **pipeline variables tab** under:

| Variable | APK |
|---|---|
| `prodAuthenticatorApkVersion` | Microsoft Authenticator |
| `prodCompanyPortalApkVersion` | Intune Company Portal |
| `prodLtwApkVersion` | Link to Windows |

Example upload command:

```powershell
az artifacts universal publish \
  --organization "https://msazure.visualstudio.com/" \
  --feed Android-Broker \
  --name com.microsoft.windowsintune.companyportal-signed \
  --version 5.0.XXXX \
  --description "Prod Company Portal, 5.0.XXXX.0" \
  --path .
```

> **Important:** If the production APKs in the feed are outdated, E2E tests will run against stale builds and may not catch regressions introduced by newer broker app versions. Always ensure the latest production APKs are uploaded before each release cycle.

## Hotfix Flow

Similar to monthly release but **targets existing release branches** and allows partial releases (broker-only, msal-only, etc.). See [references/hotfix-flow.md](references/hotfix-flow.md).

## Key Concepts

- **`release/<version>`** — Protected release branch (from `dev`)
- **`working/release/<version>`** — Editable working branch for RC updates
- **`release-integration/<version>`** — Branch that auto-triggers PR back to `dev`
- **RC (Release Candidate)** — Version suffix like `-RC1`, `-RC2` during testing
- **1ES Pipeline Templates** — Microsoft compliance framework used by production pipelines

## Detailed References

For deeper exploration of specific areas, read the appropriate reference file:

- **[Full file map](references/file-map.md)** — Complete listing of every pipeline file and script with descriptions
- **[Hotfix flow](references/hotfix-flow.md)** — Hotfix pipeline stages and version resolution
- **[Release scripts](references/release-scripts.md)** — PowerShell/Python scripts used during releases (from android-complete)
- **[Template catalog](references/template-catalog.md)** — All reusable templates in AuthClientAndroidPipelines

## Searching Pipeline Code

When investigating pipelines:

1. **Start in `1ES-Pipelines/`** (local clone of AuthClientAndroidPipelines) for production/release/hotfix pipelines
2. **Check android-complete/scripts/release/** for version manipulation scripts
3. **Check android-complete/azure-pipelines/** for legacy dev/daily build pipelines
4. **Template references** follow the pattern `../../templates/{category}/{name.yml}`
5. **Pipeline IDs** are hardcoded in trigger scripts (`2519` = monthly-release, `2828` = start-monthly-release, `3076` = ui-automation)
6. **`queue-build.ps1`** is used to trigger child pipelines from parent pipelines (monthly/weekly → UI automation). It queues an ADO build via REST API and polls for completion. Key parameters: `-BuildDefinitionId`, `-TemplateParams` (JSON of pipeline parameters), `-Branch`, `-WaitTimeoutInMinutes`
7. **Cross-pipeline artifact download** uses `DownloadPipelineArtifact@2` with `buildType: specific` to pull artifacts from a source pipeline. The `sourcePipelineId`/`sourceBuildId` parameters are threaded from the standalone UI automation pipeline down through `run-monthly-ui-automation.yml` → `run-firebase-tests.yml` → `run-on-firebase.yml`/`run-on-firebase-with-flank.yml`
8. **`1ES-Pipelines/` is in `.gitignore`** — use `includeIgnoredFiles: true` when searching with `grep_search`
9. **1ES Pipeline Templates**: Production pipelines extend `v1/1ES.Official.PipelineTemplate.yml@1ESPipelineTemplates`, non-production extend `v1/1ES.Unofficial.PipelineTemplate.yml@1ESPipelineTemplates`

## Customizing the Release Schedule

The monthly release cron is defined in **AuthClientAndroidPipelines** at:

```
non-production/daily-validation/release-util.yml
```

### Cron expression

The `schedules` block in the YAML looks like:

```yaml
schedules:
  - cron: "0 6 22-25 * *"   # 6:00 UTC = 10:00 PM PST
    displayName: Monthly Release Trigger
    branches:
      include:
        - main
    always: true
```

The cron format is **`minute hour day-of-month month day-of-week`** (UTC).

### How the date gate works

The cron fires on **every day in the day range**, but the pipeline's first stage contains a gate that checks whether the current date is **exactly 8 days before the 1st of the next month**. This means the pipeline only truly proceeds on one night within the range. The gate condition in `release-util.yml` uses an `or` clause:

```yaml
condition: or(eq(variables['isCodeCompleteDay'], 'true'), eq(variables['Build.Reason'], 'Manual'))
```

This means the pipeline proceeds if **either**:
- The date gate passes (scheduled run on the correct day), **or**
- The pipeline was triggered manually (`Build.Reason` = `Manual`)

### Manually triggering a release

As of [PR #22827](https://identitydivision.visualstudio.com/Engineering/_git/AuthClientAndroidPipelines/pullrequest/22827), the release can be started manually at any time — **no cron schedule or date gate required**. To do this:

1. Go to the **release-util** pipeline in Azure DevOps
2. Click **Run pipeline**
3. Select the `main` branch and run

The `Build.Reason` will be `Manual`, which bypasses the date gate entirely. The pipeline will auto-compute versions from changelogs and proceed through the full release flow. This is useful for early releases, deadline-driven releases, or re-runs after a failed scheduled attempt.

> **Note:** A manual run is independent of the cron schedule — it does not affect or cancel any upcoming scheduled runs.

### Changing the scheduled trigger date

To move the scheduled release to a specific day (e.g., the 15th):

1. **Update the cron day range** to include the target day. For example, to trigger on the 15th:
   ```yaml
   cron: "0 6 13-16 * *"
   ```
   Use a small range around the target day to account for month-length variation.

2. **Update the date gate** inside `release-util.yml` so the "days before 1st" check matches the new target day. For the 15th, that's approximately **16–17 days before the 1st** depending on the month. Adjust the gate condition accordingly.

3. **Both changes must be made together** — updating the cron without the gate (or vice versa) will cause the pipeline to either never trigger or never pass the gate.

4. **Test with a manual run** after updating to confirm the gate logic passes on the intended date.

> **Tip:** The cron expression uses UTC. 10:00 PM PST = 6:00 AM UTC (next day). Account for this offset when choosing the day range.
