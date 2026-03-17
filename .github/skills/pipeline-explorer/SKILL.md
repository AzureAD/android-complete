---
name: pipeline-explorer
description: Understand, navigate, and troubleshoot the Android Auth Client CI/CD pipeline system. Use this skill when asked about pipelines, release processes, build pipelines, hotfix workflows, daily validation, cron schedules, pipeline templates, release branches, RC testing, publishing to Maven Central, or any ADO/GitHub Actions pipeline question. Triggers include "how does the release pipeline work", "what pipeline does X", "where is the cron job for releases", "how do hotfixes work", "trace the monthly release flow", "pipeline template for Y", "how are broker apps built".
---

# Pipeline Explorer

Navigate and understand the Android Auth Client CI/CD system spanning two repositories.

## Repository Layout

Pipeline code lives in **two locations**:

| Repository | Path | Purpose |
|---|---|---|
| **AuthClientAndroidPipelines** | `production/`, `non-production/`, `templates/`, `scripts/` | Primary pipeline repo (1ES-compliant). All monthly release, hotfix, daily validation, and publishing pipelines |
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
  ├─ 4. RemoveRCTags [APPROVAL GATE] ─── Strip -RC suffix
  │     └─ Uses: android-complete/scripts/release/remove_rc.ps1
  ├─ 5. PublishInternal ─── Azure Artifacts
  ├─ 6. PublishExternal ─── Maven Central (GPG signed)
  ├─ 7. UpdatePipelineVariables ─── Update ADO variable groups
  ├─ 8. CreatePRsToIntegrateRelease ─── Push release-integration/ branches
  │     └─► GitHub workflow auto-creates PR to dev
  └─ 9. PublishGitHubReleaseNotes
```

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

1. **Start in AuthClientAndroidPipelines** for production/release/hotfix pipelines
2. **Check android-complete/scripts/release/** for version manipulation scripts
3. **Check android-complete/azure-pipelines/** for legacy dev/daily build pipelines
4. **Template references** follow the pattern `../../templates/{category}/{name}.yml`
5. **Pipeline IDs** are hardcoded in trigger scripts (`2519` = monthly-release, `2828` = start-monthly-release)
