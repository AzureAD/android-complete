# Troubleshooting — Environment, Build, Install, and Test Failures

Table of contents:
- [SDK / tooling not found](#sdk--tooling-not-found)
- [Emulator won't start or boot](#emulator-wont-start-or-boot)
- [adb device issues](#adb-device-issues)
- [Build failures](#build-failures)
- [Testing a local library change (publish to mavenLocal)](#testing-a-local-library-change-publish-to-mavenlocal)
- [Native (NDK/CMake) build toolchain](#native-ndkcmake-build-toolchain)
- [Install failures](#install-failures)
- [Signing & redirect-URI mismatch (AADSTS50011)](#signing--redirect-uri-mismatch-aadsts50011)
- [Conditional Access blocks the flow (AADSTS530021, approved-client-app)](#conditional-access-blocks-the-flow-aadsts530021-approved-client-app)
- [Feature flags / flights not taking effect](#feature-flags--flights-not-taking-effect)
- [uiautomator dump failures](#uiautomator-dump-failures)
- [Broker pairing failures](#broker-pairing-failures)
- [Distinguishing an env/test-harness problem from a real defect](#distinguishing-an-envtest-harness-problem-from-a-real-defect)

> **Lessons from real runs are folded into the sections below.** The five that cost the most time
> historically: (1) a feature flag that only reads its **source default** because the app never
> initializes the flights manager — see [Feature flags / flights](#feature-flags--flights-not-taking-effect);
> (2) the test app **signed with the wrong keystore** → `AADSTS50011` — see
> [Signing](#signing--redirect-uri-mismatch-aadsts50011); (3) a **non-CA-approved app config** →
> `AADSTS530021` before the flow under test is even reached — see
> [Conditional Access](#conditional-access-blocks-the-flow-aadsts530021-approved-client-app);
> (4) a change in a **library** (common/common4j) that must be **published to mavenLocal** and consumed
> by the test app — see [Testing a local library change](#testing-a-local-library-change-publish-to-mavenlocal);
> (5) a missing **NDK/CMake** toolchain for apps with native code — see
> [Native build toolchain](#native-ndkcmake-build-toolchain).

## SDK / tooling not found

- `emulator.ps1 resolve-sdk` prints the resolved SDK and tool paths. If it throws, `ANDROID_HOME` is
  unset or points nowhere valid. Note: on some machines `ANDROID_HOME` is stored **unexpanded**
  (literally `%LOCALAPPDATA%\Android\Sdk`) — the scripts expand it, but if you invoke adb/emulator
  yourself, expand it too.
- `avdmanager`/`sdkmanager` may live under `cmdline-tools\latest\bin\` **or** a standalone
  `cmdline-tools\latest\`. They are **slow** (Java startup) — prefer `emulator -list-avds` and reading
  the `system-images/` directory over `avdmanager list`.
- No system images installed → `emulator.ps1 list-images` is empty. Install one:
  `sdkmanager "system-images;android-34;google_apis;x86_64"` (or via Android Studio SDK Manager).

## Emulator won't start or boot

| Symptom | Fix |
|---|---|
| Hangs before adb registers | Increase `-TimeoutSec`; the first cold boot can take minutes. |
| Boot never completes | Cold boot clean: `emulator.ps1 start -Avd <n> -ColdBoot -Wait`. |
| HAXM/WHPX/hypervisor error | Enable Windows Hypervisor Platform, or start emulator once from Android Studio to repair. |
| Stuck snapshot / corrupted state | `-ColdBoot` (`-no-snapshot-load`) to skip the stale snapshot. |
| Transient "System UI isn't responding" ANR after boot | `deviceui.ps1 tap-text -Text "Wait"`; not a test failure. |
| Need it faster / CI | `-NoWindow` (headless) — uiautomator still works. |

## adb device issues

- `adb offline` or missing: `adb kill-server; adb start-server`, then `emulator.ps1 status`.
- Multiple emulators running: always pass `-Serial emulator-XXXX` to every script so commands target
  the right device.
- Keyguard/lock screen covering the app: `emulator.ps1` dismisses a non-secure keyguard after boot; if a
  PIN is set, enter it via `deviceui.ps1`.

## Build failures

- **401 Unauthorized / could not resolve dependency** → missing Maven creds. The repo needs
  `vstsMavenAccessToken` (and possibly `adoMsazureAuthAppAccessToken`) in `~/.gradle/gradle.properties`
  (see README). This is an environment blocker — tell the user; the AI cannot mint tokens.
- **Wrong variant** → use `localDebug` for test apps (`assembleLocalDebug` / `installLocalDebug`).
  `MSAuthenticator` uses `devDebug` (PROD) or `integrationDebug` (INT).
- **JDK mismatch** → the Gradle build needs the JDK the project targets (often 17). If the shell's
  default `java` is older, build from the same environment Android Studio uses, or set `JAVA_HOME`.
- Prefer reusing an APK Android Studio already built (`appcontrol.ps1 list-apks`) instead of a fresh
  Gradle build when one exists — it's faster and avoids credential setup.

## Testing a local library change (publish to mavenLocal)

When the change under test is in a **library** (`common` / `common4j`, or another SDK the test app
consumes as a Maven dependency), building the test app is **not** enough — the app resolves the library
from a Maven repo, not from your working tree. You must publish your local library, then build the app
against that version:

1. **Publish the library to mavenLocal** from the working tree that has your change (a git *worktree* is
   still the right source — publish from the folder that actually holds the edited code, not a sibling
   checkout on a different branch). Use a unique version so the app can't silently pull a cached one:
   ```powershell
   $ver = "0.0.0-local-<feature>-$(Get-Date -Format yyyyMMddHHmmss)"
   ./gradlew :common4j:publishToMavenLocal "-PprojVersion=$ver" --console=plain
   ./gradlew :common:publishDistReleasePublicationToMavenLocal "-PprojVersion=$ver" "-PdistCommon4jVersion=$ver" --console=plain
   ```
2. **Build/install the test app pointing at that version** — pass the app's version-override property
   (e.g. `-PandroidCommonVersion=$ver`) and ensure `mavenLocal()` is on its repository list (some repos
   inject this via a Gradle init script rather than editing the app). Confirm the artifact exists first:
   `~/.m2/repository/com/microsoft/identity/common/<ver>/`.
3. If the app repo doesn't already read `mavenLocal()`, add it via an **init script** (`--init-script`)
   rather than committing a repo change.

If a repo ships helper scripts for exactly this (e.g. a `scripts/local-common/` folder with
`publish-common-to-maven-local.ps1` + `build-<app>-with-local-common.ps1`), prefer them — they encode
the correct version-override property and init-script wiring.

## Native (NDK/CMake) build toolchain

Apps with native code (some test apps and OneAuth-based apps) fail the Gradle build if the NDK/CMake
toolchain is missing:

- **Symptom:** `CMake ... was not found`, `NDK not configured`, or a `externalNativeBuild` task failure.
- **Fix:** install the exact CMake version the app's `build.gradle` pins (e.g. `cmake;3.31.5`) and a
  matching NDK via `sdkmanager`, or Android Studio SDK Manager → SDK Tools. The `cmdline-tools` package
  must be present for `sdkmanager` to run at all.
- **ABI:** build the ABI that matches the device. Emulators are usually `x86_64`; physical devices are
  usually `arm64-v8a`. Pass the app's ABI-selection property (e.g. `-PabiSelection=x86_64`) or build a
  universal APK so install doesn't fail with `INSTALL_FAILED_NO_MATCHING_ABIS`.
- If native build isn't needed for the segment you're testing, some apps accept `-PskipNativeBuild=true`
  for a faster compile/resolve check — but a real functional run needs the native libs.

## Install failures

| adb error | Cause | Fix |
|---|---|---|
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | Signature mismatch with an already-installed build | Uninstall first: `appcontrol.ps1 uninstall -Package <pkg>` |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | Installing older versionCode | Uninstall the newer build first |
| `INSTALL_FAILED_INSUFFICIENT_STORAGE` | Emulator disk full | Wipe data / create a larger AVD |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | APK ABI ≠ emulator ABI | Use an `x86_64` system image, or build a universal APK |
| `INSTALL_FAILED_USER_RESTRICTED` | Play-protect / user prompt | Use a non-Play-Store `google_apis` image |

## Signing & redirect-URI mismatch (AADSTS50011)

`AADSTS50011` ("redirect URI ... does not match") after a sign-in almost always means the test app is
**signed with a key the app registration doesn't recognize**. The eSTS-registered redirect URI encodes
the app's package name **and its signing-cert hash** (`msauth://<pkg>/<base64-sig-hash>`), so a debug
build signed with the wrong keystore is rejected even though the package name is right.

- **Confirm the installed signature:** `adb shell dumpsys package <pkg>` and look at the signing cert;
  compare its Base64 SHA-1 to what the app registration expects.
- **Fix:** build/sign the app with the **registered keystore** for that app (some 1P test apps require a
  team keystore fetched from secure storage rather than the default `~/.android/debug.keystore`). If you
  can't obtain the right keystore, this is a **user blocker** — say so.
- This is an **environment/config** problem, not a defect in the feature under test — do not send it to
  the code-fix loop.

## Conditional Access blocks the flow (AADSTS530021, approved-client-app)

`AADSTS530021` ("Application does not meet the approved-client-app requirement") means a Conditional
Access policy blocked the app **before** the flow you're trying to test could run. This happens when the
`clientId` / app configuration you signed in with is **not on the tenant's approved-client-app list**.

- **Why it bites E2E:** you may be testing a MAM / CA-driven flow, but if the test app's configured
  client id isn't CA-approved, eSTS returns `530021` and you never reach the step under test (e.g. the
  install-broker prompt).
- **Fix:** run with an **approved** app configuration. Many test apps let you pick the app config /
  client id at runtime (a spinner or a build flag). A well-known first-party client id with an OOB
  redirect (e.g. an Office client id) is CA-approved and reaches CA-gated flows, whereas a custom test
  client id is not. Pick the approved config for the scenario, or ask the user which config to use.
- Like `50011`, this is **environment/config**, not a code defect.

## Feature flags / flights not taking effect

A flag can silently read a value you didn't set. The common trap: **the app never initializes the
flights manager**, so every lookup falls back to the flag's **source default**.

- **Symptom:** you enabled a flight (via test-app UI, config, or an override) but logs show the
  flag-gated code path never runs, and no error explains why.
- **Diagnose:** find how the flight is read. If the code goes through a flights provider that must be
  installed at startup (e.g. `CommonFlightsManager` / `setFlightsProvider(...)`) and the app never calls
  that, the provider is the **default** one that just returns each flag's coded default.
- **Fix for a local run:** flip the flag's **source default** to the value you need (a temporary code
  change in the library, e.g. `MyFlight("Key", true)`), publish + rebuild (see
  [Testing a local library change](#testing-a-local-library-change-publish-to-mavenlocal)), and **revert
  the change after the run**. Leave a `TODO: revert` marker and never commit or push the flip.
- See [mocking-flights-and-segments.md](mocking-flights-and-segments.md) for the full temp-change +
  revert discipline.

## uiautomator dump failures

- "could not get idle state" / empty tree: the screen is animating or a secure window is up. Retry
  (the script retries 3×), wait for a stable element with `wait-text`, or see the FLAG_SECURE notes in
  [ui-interaction.md](ui-interaction.md).
- WebView-heavy login pages: some nodes are absent; you can still often type into the focused field.

## Broker pairing failures

- `BrokerCommunicationException` / bind failures: the broker isn't installed, isn't the trusted build,
  or the client wasn't cleared after swapping brokers. Reinstall the matching-variant broker, then
  `appcontrol.ps1 clear` the client.
- Two brokers installed causing nondeterminism: keep exactly one broker (or a mock) for a clean run.
- Push/GMS-dependent broker flows failing on a bare image: recreate the emulator with `-RequireGoogleApis`.

## Distinguishing an env/test-harness problem from a real defect

Before blaming the code under test, rule out the harness:

- Missing SDK image / creds / offline adb / signature mismatch / stale snapshot → **environment**, fix
  the setup and re-run; do **not** hand these to the code-fix loop.
- Crash in the changed code (`E AndroidRuntime` with the app package + a class from the diff),
  `AADSTS50011`/`700016`, a regressed `INTERACTION_REQUIRED`, or a broken IPC path introduced by the
  change → **real defect**, hand to the fix loop with the evidence.
- If unsure, reproduce once more from a clean state (`clear` app, `clear` logcat, cold-boot if needed).
  A failure that reproduces deterministically from clean state is almost certainly a real defect.
