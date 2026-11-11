<!-- Copyright (c) Microsoft Corporation. All rights reserved. Licensed under the MIT License. -->

# android-complete: agent entry point

This is the `AzureAD/android-complete` integration workspace, not a standalone
authentication library. Read the [README](README.md) for checkout/setup and
[Copilot instructions](.github/copilot-instructions.md) for architecture,
coding, logging/privacy, and repository routing.

## Context and repository boundaries

- [settings.gradle](settings.gradle) maps Gradle projects to independent checkouts:
  `adal`, `common`, `msal`, and `broker`, plus sample applications.
- Those directories are ignored by this repository, not tracked Git submodules.
  Their source changes, tests, and PRs belong in their own repositories.
  Read each checkout's agent instructions before editing it.
- Search across the checkouts for cross-repository flows and API consumers.
  Preserve MSAL/OneAuth -> Common IPC -> Broker -> eSTS contracts.
  OneAuth is owned by another team; coordinate contract changes with them.
- [java-complete/settings.gradle](java-complete/settings.gradle) defines the
  separate JVM-only workspace for Common/Broker and lab utilities.
- Production pipelines live in the separately cloned `1ES-Pipelines` repository.
  This repository's `azure-pipelines` contains legacy/integration definitions.
  Use the [release-helper skill](.github/skills/release-helper/SKILL.md) before
  investigating pipeline behavior.
- Existing specialized workflows are indexed in
  [Copilot instructions](.github/copilot-instructions.md).
  [AI Driven Development Guide](AI%20Driven%20Development%20Guide.md) describes
  design, planning, work items, and dispatch.

## Setup

Follow the [README installation instructions](README.md#install) and the
aliases in [.gitconfig](.gitconfig). A fresh root checkout alone cannot build
the integration workspace. Provision repository access and use `git droidSetup`
in a fresh workspace to populate the required checkouts, including the sample
apps declared in settings. Inspect existing work first: setup and droid aliases
can clone, switch, pull, or update other repositories and their submodules.
Do not run bulk checkout/update commands over unrelated local changes.

Use the checked-in [Gradle 8.11.1 wrapper](gradle/wrapper/gradle-wrapper.properties)
with **JDK 17**; Java 8 source targets in libraries do not mean a Java 8 runtime.
Set `JAVA_HOME`, configure `ANDROID_HOME` or untracked `local.properties`,
and accept Android SDK licenses. Root [version configuration](gradle/versions.gradle)
currently specifies SDK 36 and Build Tools 36.0.0. Individual modules can override
NDK requirements; use Broker's pinned NDK for its native build, not a substitute.

Obtain approved Packaging Read access to the private feeds. Root
[settings](settings.gradle) and [build](build.gradle) consume
`ENV_VSTS_MVN_CRED_USERNAME` / `ENV_VSTS_MVN_CRED_ACCESSTOKEN`, with
`vstsUsername` / `vstsMavenAccessToken` fallbacks in user Gradle properties.
Other modules can require additional feed credentials. Keep secrets outside
tracked files and command history; never print them or bypass feed access.

Authenticator is excluded by default. To work on it, follow the
[README opt-in instructions](README.md#authenticator-app-opt-in): its checkout,
feed setup, `includeAuthenticatorApp=true`, and `android.enableJetifier=true`
must agree. The default gate below does not validate Authenticator or OneAuth.

## Validation entry point

From the repository root on Windows:

```powershell
pwsh -File .\scripts\verify.ps1
```

The [verification gate](scripts/verify.ps1) runs local-debug assembly, JVM/Android
unit tests, and lint for ADAL, Common, MSAL, and Broker in one Gradle invocation.
It propagates failures and restores the caller's working directory. It does not
clone repositories, rewrite pins, install apps, or publish artifacts.
It is a core integration check, not an all-repository/full-regression claim.

For macOS/Linux, invoke the equivalent tasks with `./gradlew`:

```sh
./gradlew :adal:assembleLocalDebug :common:assembleLocalDebug :msal:assembleLocalDebug :AADAuthenticator:assembleLocalDebug :adal:testLocalDebugUnitTest :common:testLocalDebugUnitTest :msal:testLocalDebugUnitTest :AADAuthenticator:testLocalDebugUnitTest :adal:lintLocalDebug :common:lintLocalDebug :msal:lintLocalDebug :AADAuthenticator:lintLocalDebug
```

## Test entry point and fast loop

For a Common/MSAL integration change, from this root:

```powershell
.\gradlew.bat :common:testLocalDebugUnitTest :msal:testLocalDebugUnitTest
```

For other modules, use their corresponding tasks from the gate. The source and
tests are in the independent checkouts mapped by [settings.gradle](settings.gradle).
For JVM-only Common/Broker work, from `java-complete` use its own
[Gradle 8.1 wrapper](java-complete/gradle/wrapper/gradle-wrapper.properties):

```powershell
.\gradlew.bat :common4j:test :broker4j:test
```

JVM-only does not imply network-free: suites can require lab access. Do not
claim a full suite if tests were filtered or long-running tests were disabled.
Inspect task output and the selected module's `build/test-results` and
`build/reports/tests`; report zero tests, skips, missing reports, and failures.

## Additional validation layers

- **Static analysis:** lint is in the gate; inspect findings as well as exit codes.
  For SpotBugs, assemble first, then run
  `.\gradlew.bat :common:spotbugsLocalDebug :msal:spotbugsLocalDebug :AADAuthenticator:spotbugsLocalDebug`.
  Applicable JVM projects expose `spotbugsMain` from `java-complete`.
- **Network integration:** MSAL/Broker exclude some network E2E cases unless
  `-Plabtest` is set. After approved [lab setup](docs/Automation/labsetup.md),
  run `.\gradlew.bat :msal:testLocalDebugUnitTest :AADAuthenticator:testLocalDebugUnitTest -Plabtest`
  with required credentials/certificate configuration. The default gate is not
  service-backed E2E certification.
- **Device tests:** with an approved dedicated emulator/device visible to
  `adb devices`, run the relevant `connectedLocalDebugAndroidTest` tasks,
  for example `.\gradlew.bat :common:connectedLocalDebugAndroidTest :AADAuthenticator:connectedLocalDebugAndroidTest`.
  Provision required lab certificates first; a device path argument does not
  provision a certificate.
- **Cross-app UI:** in this integrated workspace, after approved device/lab setup,
  use `.\gradlew.bat :msalautomationapp:connectedLocalBrokerHostDebugAndroidTest`
  or `.\gradlew.bat :brokerautomationapp:connectedLocalBrokerHostDebugAndroidTest`
  as appropriate. These tests can install apps and mutate device/account state.
  Follow [lab setup](docs/Automation/labsetup.md) and
  [project-property guidance](docs/ProjectBuild/gradle_project_properties.md).
- **Native/consumer/optional projects:** Broker JNI changes require its native
  integrity/ABI procedure; app or public API changes require relevant consumer
  builds and scenario tests. Authenticator, OneAuth, sample apps, and tools need
  their own repository-specific validation beyond the gate.

Do not use production accounts, reset devices, publish packages, or bypass
security/integrity checks without approval.

## Before pushing

Run `git diff --check` and the applicable verification/test layers. For docs-only
changes, validate links; for gate changes, also check PowerShell syntax, failure
propagation, and working-directory restoration. Discover tasks with
`.\gradlew.bat tasks --all` after setup; task discovery itself requires credentials
and populated checkouts.

Report exact commands, results, lint findings, skips, and untested layers.
Missing checkouts/SDK/feed access are blockers, not successful validation.
Keep changes and commits scoped to the owning repository and preserve unrelated
work. Written commands are readiness evidence, not proof of test coverage or
an official Northstar tier.
