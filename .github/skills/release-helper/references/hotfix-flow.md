# Hotfix Flow

The hotfix pipeline (`production/hot-fix/hot-fix.yml`) handles out-of-band releases that patch existing release branches.

## Key Differences from Monthly Release

- **Manual trigger only** — no cron schedule
- **Partial releases** — can hotfix broker-only, msal-only, common-only, or any combination (use `skip` for unaffected components)
- **Targets existing release branches** — works with already-published versions
- **`isLatestRelease` flag** — controls whether pipeline variables are updated (only for latest release hotfixes)

## Parameters

```yaml
brokerVersion: 'skip'    # Set to version like '10.2.1' to patch, 'skip' to exclude
commonVersion: 'skip'    # Same
msalVersion: 'skip'      # Same
isLatestRelease: true     # Whether to update prod version variables
```

## Stages

```
hot-fix.yml
├─ 1. VersionResolution
│     └─ hot-fix-version-resolution.yml
│     Validates input versions, determines hotfix versions (bumps patch),
│     resolves which components are being patched
│
├─ 2. ValidateBranchesAndLibraries
│     └─ hot-fix-validation.yml
│     Checks release/<version> branches exist for patched components,
│     verifies hotfix branches don't already exist,
│     validates version compatibility across libraries
│
├─ 3. BranchSetup
│     └─ hot-fix-branch-setup.yml
│     Creates working/release/<hotfix-version> branches from existing release branches,
│     updates version files with RC suffix using android-complete scripts
│
├─ 4. TriggerRCTesting
│     └─ trigger-rc-testing.yml (hotfix variant)
│     Triggers the monthly-release pipeline (2519) with hotfix branch/version params
│
├─ 5. RemoveRC [APPROVAL GATE]
│     └─ hot-fix-remove-rc.yml
│     Strips -RC suffix from version files
│
├─ 6. PublishInternal
│     └─ hot-fix-publish-internal.yml
│     Publishes to Azure Artifacts internal feed
│
├─ 7. PublishToMavenCentral
│     └─ hot-fix-publish-maven-central.yml
│     GPG-signed publish to Maven Central
│
├─ 8. UpdatePipelineVariables (conditional on isLatestRelease)
│     └─ hot-fix-update-pipeline-variables.yml
│     Updates prod version variables in ADO
│
├─ 9. CreateBranchesForAutomatedPRs
│     └─ hot-fix-create-branches-for-automate-prs.yml
│     Creates release-integration/ branches → auto-PR to dev
│
├─ 10. PublishGitHubReleaseNotes
│      └─ hot-fix-publish-github-release-notes.yml
│
└─ (debug) Cleanup — deletes test branches if debugCleanup=true
```

## Debug Flags

All stages have debug skip flags for testing:
- `debug: true` — uses `test-hotfix/` branch prefix instead of `release/`
- `debugCleanup: true` — adds cleanup stage to delete test branches
- `debugSkipRCTesting`, `debugSkipInternalPublish`, `debugSkipExternalPublish`, etc.

## Version Resolution Logic

The hotfix version is determined by:
1. Taking the input version (e.g., `10.2.0`)
2. Finding the existing `release/10.2.0` branch
3. Reading the changelog to determine the patch bump
4. Scripts: `determine-hotfix-version.sh`, `validate-version-compatibility.sh`

## Scripts Used (from android-complete)

- `scripts/release/init_hotfix_broker.ps1` — Broker-only hotfix init
- `scripts/release/init_hotfix_msal.ps1` — MSAL-only hotfix init
- `scripts/release/init_hotfix_for_all.ps1` — All-libs hotfix init
- `scripts/release/update_rc.ps1` — RC bump
- `scripts/release/remove_rc.ps1` — RC removal

## Scripts Used (from AuthClientAndroidPipelines)

- `scripts/validation/determine-hotfix-version.sh` — Calculate next hotfix version
- `scripts/validation/validate-version-compatibility.sh` — Cross-library version checks
- `scripts/validation/verify-hotfix-branches-dont-exist.sh` — Pre-flight check
- `scripts/helpers/build-hotfix-arrays.sh` — Build component arrays for iteration
- `scripts/rc-testing/checkout-component-branches.sh` — Checkout correct branches
- `scripts/rc-testing/load-hotfix-versions.sh` — Load resolved versions
- `scripts/rc-testing/publish-hotfix-branches.sh` — Push branches
