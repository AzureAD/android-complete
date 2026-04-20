# Template Catalog (AuthClientAndroidPipelines)

> **Local path**: `1ES-Pipelines/templates/` (cloned via `git droidSetup`)

All reusable YAML templates organized by category.

## Release Templates (`templates/release/`)

Used by `start-monthly-release.yml`:

| Template | Stage | Purpose |
|---|---|---|
| `validate-branches-and-versions.yml` | ValidateBranchesAndVersions | Pre-flight: no existing branches, no existing Maven artifacts |
| `create-branches.yml` | BranchSetup | Creates `release/<ver>` + `working/release/<ver>` in common, msal, broker |
| `determine_version.yml` | — | Runs `get_next_version.py` for a single library |
| `trigger-rc-pipelines.yml` | TriggerRC | Bumps RC, commits, triggers monthly-release pipeline (2519) |
| `trigger-release-pipeline.yml` | — | Generic: triggers an ADO pipeline via REST API |
| `remove-rc-tags.yml` | RemoveRCTags | Environment approval gate + RC removal |
| `publish-internal.yml` | PublishInternal | Publish to Azure Artifacts (with retry detection) |
| `publish-external.yml` | PublishExternal | Publish to Maven Central |
| `update-pipeline-variables.yml` | UpdatePipelineVariables | Updates ADO variable groups with new prod versions |
| `create-branches-for-automated-pr.yml` | CreatePRsToIntegrateRelease | Creates `release-integration/<ver>` branches |
| `publish-github-release-notes.yml` | PublishGitHubReleaseNotes | Creates GitHub releases with notes |
| `cleanup-debug-branches.yml` | Cleanup (debug only) | Deletes test branches |

## Hotfix Templates (`templates/hotfix/`)

Used by `hot-fix.yml`. Mirror the release templates with hotfix-specific branching:

| Template | Purpose |
|---|---|
| `hot-fix-version-resolution.yml` | Resolve which components need patching, compute hotfix versions |
| `hot-fix-validation.yml` | Validate release branches exist, hotfix branches don't |
| `hot-fix-branch-setup.yml` | Create working branches from existing release branches |
| `trigger-rc-testing.yml` | Trigger RC testing pipeline for hotfix |
| `hot-fix-remove-rc.yml` | Remove RC suffix (with approval gate) |
| `hot-fix-publish-internal.yml` | Internal publish |
| `hot-fix-publish-maven-central.yml` | Maven Central publish |
| `hot-fix-update-pipeline-variables.yml` | Update prod version vars (conditional on `isLatestRelease`) |
| `hot-fix-create-branches-for-automate-prs.yml` | Create integration branches for auto-PR |
| `hot-fix-publish-github-release-notes.yml` | GitHub release notes |
| `hot-fix-load-versions-steps.yml` | Load resolved versions as pipeline variables |
| `hot-fix-debug-cleanup.yml` | Debug branch cleanup |
| `check-maven-artifact-version.yml` | Check if a version exists in Maven/Azure Artifacts |

## Build Templates (`templates/build/`)

### Libraries (`build/libraries/`)

| Template | Purpose |
|---|---|
| `build-and-publish-auth-android-libs.yml` | **Orchestrator**: builds all SDK libraries in dependency order |
| `build-and-publish-common4j.yml` | Build + publish common4j |
| `build-and-publish-common.yml` | Build + publish common (depends on common4j) |
| `build-and-publish-broker4j.yml` | Build + publish broker4j (depends on common4j) |
| `build-and-publish-broker.yml` | Build + publish broker (depends on common, broker4j) |
| `build-and-publish-msal.yml` | Build + publish msal (depends on common) |
| `build-and-publish-adal.yml` | Build + publish adal (depends on common) |
| `build-test-publish.yml` | Generic build-test-publish template (assemble, unit test, publish) |

**Library dependency order:** common4j → common + broker4j → broker + msal + adal

### Broker Apps (`build/brokerApps/`)

| Template | Purpose |
|---|---|
| `build-and-download-broker-apps.yml` | **Orchestrator**: builds/downloads all broker apps |
| `build-authenticator.yml` | Build Microsoft Authenticator APK |
| `build-companyportal.yml` | Build Company Portal APK |
| `build-ltw.yml` | Build Link to Windows APK |
| `download-rc-prod-brokers.yml` | Download RC and PROD broker APKs from feeds |

### Test Apps (`build/testApps/`)

| Template | Purpose |
|---|---|
| `build-test-apps.yml` | **Orchestrator**: builds all test apps |
| `build-msal-test-app.yml` | MSAL test app |
| `build-msal-automation-app.yml` | MSAL automation app (for E2E) |
| `build-broker-automation-app.yml` | Broker automation app |
| `build-broker-host.yml` | Broker host app |
| `build-adal-test-app.yml` | ADAL test app |
| `build-azure-sample-app.yml` | Azure sample app |
| `build-one-auth-test-app.yml` | OneAuth test app |
| `download-first-party-apps.yml` | Download 1P apps (Teams, Outlook) |
| `download-old-test-apps.yml` | Download old app versions for backward-compat testing |

### Utilities (`build/utilities/`)

| Template | Purpose |
|---|---|
| `build-and-publish-utilities.yml` | Build KeyVault, LabApi, LabApiUtilities, TestUtils, UiAutomationUtilities |

## Test Templates (`templates/runTests/`)

| Template | Purpose |
|---|---|
| `run-instrumented-tests-for-all-libraries.yml` | Run instrumented tests for all libs |
| `run-instrumented-tests.yml` | Run instrumented tests for a single lib |
| `run-monthly-ui-automation.yml` | Full monthly E2E UI automation suite |
| `run-daily-ui-automation.yml` | Daily E2E UI automation (subset) |
| `run-pre-ui-validation.yml` | Pre-UI validation checks |
| `run-firebase-tests.yml` | Execute tests on Firebase Test Lab |
| `run-on-firebase.yml` | Firebase test runner (gcloud CLI) |
| `run-on-firebase-with-flank.yml` | Firebase test runner using Flank |
| `flank.yml` | Flank configuration template |
| `run-lab-api-validation.yml` | Lab API validation tests |

## Verification Templates (`templates/verification/`)

| Template | Purpose |
|---|---|
| `flight-verification-for-official-release.yml` | Fail if local flights are active in prod release |
| `kusto-telem-validation.yml` | Query Kusto to validate telemetry after E2E runs |
| `spotbugs.yml` | SpotBugs static analysis |

## Utility Templates

### `templates/util/`
| Template | Purpose |
|---|---|
| `generate-and-send-automated-report.yml` | Generate test reports and send via email |
| `send-email-with-ACS.yml` | Send email using Azure Communication Services |

### `templates/utilities/`
| Template | Purpose |
|---|---|
| `create-github-release.yml` | Create a GitHub release |
| `generate-release-notes.yml` | Generate release notes from changelog |
| `merge-single-release-branch.yml` | Merge a single release branch |
| `update-pipeline-variable.yml` | Update a single ADO pipeline variable |

### Linux Templates (`templates/Linux/`)
| Template | Purpose |
|---|---|
| `produce-deb-ubuntu-2004.yml` | Build .deb package (Ubuntu 20.04) |
| `produce-deb-ubuntu-2204.yml` | Build .deb package (Ubuntu 22.04) |
| `produce-rpm.yml` | Build .rpm package |
| `OneBranch_packages_microsoft_com-deb-rpm-publishing.yml` | Publish to packages.microsoft.com |

### Other
| Template | Purpose |
|---|---|
| `automation-cert.yml` | Certificate provisioning for automation |
| `token-from-service-connection.yml` | Get token from ADO service connection |
