# Complete File Map

Every pipeline file and script across both repositories.

## AuthClientAndroidPipelines

> **Local path**: `1ES-Pipelines/` (cloned via `git droidSetup`)

### Top-Level Pipelines

```
production/
├── monthly-release/
│   ├── start-monthly-release.yml     # Orchestrator: creates branches, triggers RC, publishes (ID: 2828)
│   └── monthly-release.yml           # 1ES pipeline: builds, tests, validates RC (ID: 2519)
├── hot-fix/
│   └── hot-fix.yml                   # Manual hotfix orchestrator (same stages as monthly, targets existing releases)
├── publish-internal/
│   ├── adal.yml                      # Publish ADAL to internal Azure Artifacts feed
│   ├── broker.yml                    # Publish Broker to internal feed
│   ├── broker4j.yml                  # Publish Broker4j to internal feed
│   ├── common.yml                    # Publish Common to internal feed
│   ├── common4j.yml                  # Publish Common4j to internal feed
│   └── msal.yml                      # Publish MSAL to internal feed
├── publish-external/
│   ├── common.yml                    # Publish Common to Maven Central
│   ├── common4j.yml                  # Publish Common4j to Maven Central
│   ├── msal.yml                      # Publish MSAL to Maven Central
│   └── publish-external-template.yml # Shared template for GPG-signed Maven Central publish
├── linux/
│   ├── linux-broker-publishing.yml   # Linux broker package publishing
│   ├── powerlift-client-publishing.yml
│   ├── ev2_pmc/                      # ExpressV2/PMC publishing
│   ├── linux_broker_pkg/             # Linux broker packaging config
│   └── powerlift_pkg/                # PowerLift packaging config
└── test-app/
    └── microsoft-identity-diagnostics-cd.yml  # Diagnostics test app CD

non-production/
├── daily-validation/
│   ├── daily-validation.yml          # Daily dev build & test pipeline (Local/ECS flights)
│   └── release-util.yml             # CRON: auto-triggers monthly release (22-25th)
├── weekly-validation/
│   └── weekly-validation.yml         # Weekly validation pipeline (builds, tests, E2E via UI automation pipeline)
├── ui-automation/
│   └── ui-automation.yml             # Standalone Firebase UI automation (ID: 3076). Triggered by monthly-release/weekly-validation via queue-build.ps1
└── linux-validation/                 # Linux validation pipelines
```

### Templates

```
templates/
├── release/                           # Monthly release stage templates
│   ├── validate-branches-and-versions.yml  # Pre-flight checks
│   ├── create-branches.yml                 # Create release/ and working/release/ branches
│   ├── determine_version.yml               # Auto-determine version from changelog
│   ├── trigger-rc-pipelines.yml            # Bump RC and trigger monthly-release pipeline
│   ├── trigger-release-pipeline.yml        # Generic ADO pipeline trigger via REST API
│   ├── remove-rc-tags.yml                  # Remove -RC suffix (has approval gate)
│   ├── publish-internal.yml                # Publish to Azure Artifacts
│   ├── publish-external.yml                # Publish to Maven Central
│   ├── update-pipeline-variables.yml       # Update ADO variable groups
│   ├── create-branches-for-automated-pr.yml # Create release-integration/ branches
│   ├── publish-github-release-notes.yml    # GitHub release creation
│   └── cleanup-debug-branches.yml          # Debug branch cleanup
├── hotfix/                            # Hotfix stage templates
│   ├── hot-fix-version-resolution.yml      # Resolve which components need hotfix
│   ├── hot-fix-validation.yml              # Validate branches/versions exist
│   ├── hot-fix-branch-setup.yml            # Create hotfix working branches
│   ├── trigger-rc-testing.yml              # Trigger RC testing for hotfix
│   ├── hot-fix-remove-rc.yml               # Remove RC from hotfix branches
│   ├── hot-fix-publish-internal.yml        # Internal publish
│   ├── hot-fix-publish-maven-central.yml   # External publish
│   ├── hot-fix-update-pipeline-variables.yml
│   ├── hot-fix-create-branches-for-automate-prs.yml
│   ├── hot-fix-publish-github-release-notes.yml
│   ├── hot-fix-load-versions-steps.yml     # Load resolved versions into variables
│   ├── hot-fix-debug-cleanup.yml           # Cleanup debug branches
│   └── check-maven-artifact-version.yml    # Check if version exists in Maven/feed
├── build/
│   ├── libraries/                     # Library build templates
│   │   ├── build-and-publish-auth-android-libs.yml  # Orchestrates all lib builds
│   │   ├── build-and-publish-common4j.yml
│   │   ├── build-and-publish-common.yml
│   │   ├── build-and-publish-broker4j.yml
│   │   ├── build-and-publish-broker.yml
│   │   ├── build-and-publish-msal.yml
│   │   ├── build-and-publish-adal.yml
│   │   └── build-test-publish.yml          # Generic build-test-publish template
│   ├── brokerApps/                    # Broker app build templates
│   │   ├── build-and-download-broker-apps.yml  # Orchestrates all broker app builds
│   │   ├── build-authenticator.yml
│   │   ├── build-companyportal.yml
│   │   ├── build-ltw.yml
│   │   └── download-rc-prod-brokers.yml
│   ├── testApps/                      # Test app build templates
│   │   ├── build-test-apps.yml             # Orchestrates all test app builds
│   │   ├── build-msal-test-app.yml
│   │   ├── build-msal-automation-app.yml
│   │   ├── build-broker-automation-app.yml
│   │   ├── build-broker-host.yml
│   │   ├── build-adal-test-app.yml
│   │   ├── build-azure-sample-app.yml
│   │   ├── build-one-auth-test-app.yml
│   │   ├── download-first-party-apps.yml
│   │   └── download-old-test-apps.yml
│   └── utilities/
│       └── build-and-publish-utilities.yml  # Build LabApi, TestUtils, etc.
├── runTests/                          # Test execution templates
│   ├── run-instrumented-tests-for-all-libraries.yml
│   ├── run-instrumented-tests.yml
│   ├── run-monthly-ui-automation.yml
│   ├── run-weekly-ui-automation.yml
│   ├── run-pre-ui-validation.yml
│   ├── run-firebase-tests.yml
│   ├── run-on-firebase.yml
│   ├── run-on-firebase-with-flank.yml
│   ├── flank.yml
│   └── run-lab-api-validation.yml
├── verification/                      # Verification templates
│   ├── flight-verification-for-official-release.yml  # Ensure no local flights in prod
│   ├── kusto-telem-validation.yml          # Validate telemetry after E2E tests
│   └── spotbugs.yml
├── util/                              # Utility templates
│   ├── generate-and-send-automated-report.yml
│   └── send-email-with-ACS.yml
├── utilities/                         # Release utility templates
│   ├── create-github-release.yml
│   ├── generate-release-notes.yml
│   ├── merge-single-release-branch.yml
│   └── update-pipeline-variable.yml
├── Linux/                             # Linux-specific templates
│   ├── produce-deb-ubuntu-2004.yml
│   ├── produce-deb-ubuntu-2204.yml
│   ├── produce-rpm.yml
│   └── OneBranch_packages_microsoft_com-deb-rpm-publishing.yml
├── automation-cert.yml
└── token-from-service-connection.yml
```

### Scripts

```
scripts/
├── get_next_version.py                # Auto-determine next version from changelog
├── queue-build.ps1                    # Queue ADO pipeline builds
├── promote-packages.ps1               # Promote packages in Azure Artifacts feed
├── trigger-external-pipeline.ps1      # Trigger external ADO pipelines via REST
├── setup-alternating-schedule.ps1     # Day-of-week alternating test schedule
├── day-and-hour-of-week.ps1           # Get current day/hour for scheduling
├── changelog-helper.sh                # Parse changelog files
├── git-helper.sh                      # Git operation helpers
├── gpg-signing.sh                     # GPG signing for Maven Central
├── version-helper.sh                  # Version string manipulation
├── aggregate-test-reports.py          # Aggregate test results across runs
├── helpers/
│   └── build-hotfix-arrays.sh         # Build arrays for hotfix components
├── rc-testing/
│   ├── checkout-component-branches.sh # Checkout correct branches for RC
│   ├── load-hotfix-versions.sh        # Load versions for hotfix testing
│   └── publish-hotfix-branches.sh     # Push hotfix branches
└── validation/
    ├── check-version-exists-in-feed.ps1       # Check Azure Artifacts for version
    ├── check-maven-central-version-exists.ps1 # Check Maven Central for version
    ├── create-version-artifacts.sh
    ├── determine-hotfix-version.sh            # Calculate hotfix version
    ├── determine-versions-for-skipped-parameters.sh
    ├── validate-input-parameters.sh
    ├── validate-version-compatibility.sh
    ├── validate-version-tags-and-branches-exist.sh
    └── verify-hotfix-branches-dont-exist.sh
```

## android-complete

### Azure Pipelines (Legacy)

```
azure-pipelines/
├── continuous-delivery/
│   ├── auth-client-android-dev.yml         # Dev build pipeline (scheduled)
│   ├── auth-client-android-daily.yml       # Daily "test" build pipeline (scheduled)
│   ├── flighted-auth-client-android-dev.yml # Flighted dev builds
│   └── assemble&publish.yml                # Build & publish template
├── instrumented-tests-multistage.yml       # Cron: nightly instrumented tests
├── build-publish-docker-image.yml          # Docker image for CI
├── test-app/
│   ├── adal-test-app.yml                   # ADAL test app CD (cron 3AM UTC)
│   ├── azure-sample-app.yml                # Azure sample app CD
│   ├── broker-host.yml                     # Broker host app CD
│   ├── java-linux-test-app.yml             # Java Linux test app CD
│   ├── microsoft-identity-diagnostics-cd.yml
│   └── msal-test-app.yml                   # MSAL test app CD
├── ui-automation/
│   ├── broker-test.yml                     # Broker E2E test orchestration
│   ├── build-and-publish-automation-artifacts.yml
│   ├── downloadAndPublishprodApksToUniversalPackages.yml
│   ├── templates/                          # Firebase test templates
│   └── hydralab/                           # HydraLab test config
├── templates/
│   ├── buildBrokerApps/                    # Legacy broker app build templates
│   ├── buildProduct/                       # Product build templates
│   ├── buildTestApps/                      # Test app build templates
│   ├── runTests/                           # Test execution templates
│   ├── tagBuild/                           # Build tagging templates
│   ├── variables/                          # Variable templates (global-variables.yml)
│   ├── parameters/                         # Parameter templates
│   └── universal-packages/                 # Universal package templates
└── scripts/
    ├── queue-build.ps1                     # Queue ADO builds
    ├── promote-packages.ps1                # Package promotion
    ├── setup-alternating-schedule.ps1       # Alternating day schedule
    ├── day-of-week.ps1
    └── import-testResults.ps1
```

### Release Scripts

```
scripts/
├── release/
│   ├── init_release.ps1                # Initialize release: set RC1 versions across all libs
│   ├── update_rc.ps1                   # Bump RC number across all libs
│   ├── remove_rc.ps1                   # Strip -RC suffix for final release
│   ├── init_hotfix_broker.ps1          # Broker-only hotfix initialization
│   ├── init_hotfix_msal.ps1            # MSAL-only hotfix initialization
│   ├── init_hotfix_for_all.ps1         # All-libs hotfix initialization
│   └── libs/
│       ├── constants.ps1               # File paths, gradle variable names
│       └── helper_methods.ps1          # Version update, changelog update functions
├── BrokerReleaseAutomation.ps1         # Local broker release test runner
├── MsalReleaseAutomation.ps1           # Local MSAL release test runner
└── ado/
    └── promote-package.ps1
```

### GitHub Workflows

```
.github/workflows/
├── release-integration.yml             # Auto-PR when release-integration/* branches pushed
└── validate-pr-ab-id.yml              # Validate AB#ID on PRs
```
