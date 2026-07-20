# Troubleshooting — Environment, Build, Install, and Test Failures

Table of contents:
- [SDK / tooling not found](#sdk--tooling-not-found)
- [Emulator won't start or boot](#emulator-wont-start-or-boot)
- [adb device issues](#adb-device-issues)
- [Build failures](#build-failures)
- [Install failures](#install-failures)
- [uiautomator dump failures](#uiautomator-dump-failures)
- [Broker pairing failures](#broker-pairing-failures)
- [Distinguishing an env/test-harness problem from a real defect](#distinguishing-an-envtest-harness-problem-from-a-real-defect)

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

## Install failures

| adb error | Cause | Fix |
|---|---|---|
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | Signature mismatch with an already-installed build | Uninstall first: `appcontrol.ps1 uninstall -Package <pkg>` |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | Installing older versionCode | Uninstall the newer build first |
| `INSTALL_FAILED_INSUFFICIENT_STORAGE` | Emulator disk full | Wipe data / create a larger AVD |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | APK ABI ≠ emulator ABI | Use an `x86_64` system image, or build a universal APK |
| `INSTALL_FAILED_USER_RESTRICTED` | Play-protect / user prompt | Use a non-Play-Store `google_apis` image |

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
