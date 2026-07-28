# Troubleshooting — Environment, Build, Install, and Test Failures

> For **in-flow UI hiccups** (fingerprint/App-Lock, number-match MFA, session timeouts, autofill overlay,
> FLAG_SECURE black screenshots, single-use pairing links) see
> [common-blockers.md](common-blockers.md). This file covers **environment/build/install/tooling**
> failures — the things that go wrong *before or around* the flow rather than *inside* it.

Table of contents:
- [SDK / tooling not found](#sdk--tooling-not-found)
- [PowerShell script encoding (UTF-8 BOM)](#powershell-script-encoding-utf-8-bom)
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
| **Silent crash right after launch with `-gpu host`** | On a **GPU-less host** (Cloud PC/VM/RDP) `-gpu host` can crash the emulator process instantly. Use `-gpu swiftshader_indirect` (software). See recipe below. |
| **Boot wedges for 20+ min** | An AVD baked with `hw.gpu.enabled=no` **and** low `hw.ramSize` (e.g. `2G`) can hang boot forever. Override on the CLI: `-gpu swiftshader_indirect -memory 4096`. Don't edit the AVD in place — flags win. |
| **`-no-boot-anim` makes boot look stuck** | With `-no-boot-anim`, `init.svc.bootanim` stays empty, so polling it never flips. Poll `getprop sys.boot_completed` = `1` instead. |
| **Repeated tombstones / system_server restarts / `Can't find service: <x>`** | The emulator's **Bluetooth HAL** can crash-loop (`hci_backend_aidl.cc:40 initializationComplete`), dragging `system_server` down again and again. On some hosts this makes a heavy app (e.g. Authenticator) impossible to drive. `-feature -Bluetooth` may not stop it. **If it won't stabilize, abandon the emulator and fall back to a physical device** (note the biometric limitation — see [common-blockers.md](common-blockers.md)). |
| **Everything is slow** (UI, screencap, downloads) | Likely **no host GPU** (Cloud PC / VM / RDP) → software rendering. Run `emulator.ps1 resolve-sdk` to confirm; prefer a **physical device** (`ensure -PreferPhysical`). See [emulator-performance.md](emulator-performance.md). |
| Emulator not in Android Studio **Running Devices** | The skill starts it standalone; it's still in **Device Manager** (shared AVD home). Start the AVD from Studio and let the skill reuse it. See [emulator-performance.md](emulator-performance.md#android-studio-device-manager--running-devices). |

**Working cold-boot recipe on a GPU-less host** (booted in <2 min in a real run where every other combo
failed):
```powershell
emulator -avd <name> -no-snapshot -wipe-data -gpu swiftshader_indirect -memory 4096 -no-window -no-boot-anim -no-audio
# then poll: adb -s emulator-5554 shell getprop sys.boot_completed   # wait for '1'
```
`-no-snapshot -wipe-data` gives a clean state; `swiftshader_indirect` + `4096` MB avoids the GPU-crash and
low-RAM-wedge traps; headless trims overhead. WHPX CPU acceleration still applies. If, after this, the
system is still unstable (see the Bluetooth-HAL row above), stop fighting it — use a physical device.

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
| **App installs but crashes on launch / dexopt kills `system_server`** | A large **arm64-only** APK on an `x86_64` emulator can pass the ABI check (image lists `x86_64,arm64-v8a`) but crash during **install-time dexopt** — you'll see a Watchdog kill, `Broken pipe`, or `Can't find service: package`. Use the **universal** APK (`app-production-universal-release-signed.apk`) on emulators; keep the `arm64-v8a` APK for **physical** devices. The package sometimes still commits — check `adb shell pm path <pkg>`; a reinstall once dexopt has cached often returns `Success`. |
| `INSTALL_FAILED_USER_RESTRICTED` | Play-protect / user prompt | Use a non-Play-Store `google_apis` image |
| **`pm install` fails on an APK sitting in `/sdcard/`** | The `package` installer service **cannot read `/sdcard/`** (it's a FUSE/emulated view the installer uid can't access), so an on-device `pm install /sdcard/foo.apk` fails even though the file plainly exists | Copy to `/data/local/tmp/` first: `adb shell cp /sdcard/foo.apk /data/local/tmp/ && adb shell pm install -r /data/local/tmp/foo.apk`. Not an issue for host-side `adb install <hostpath>` (that streams the file). The Authenticator UIAutomator suite has a dedicated `CopyApkRule` for exactly this — see [existing-ui-automation.md](existing-ui-automation.md). |
| **`adb install` hangs (never returns)** | adb server or device wedged mid-transfer (common after a slow/interrupted install, a device reboot, or a flaky emulator) — the call blocks the caller **and any supervising agent forever**, defeating the per-point abort cap | Bound the install: `appcontrol.ps1 install -Apk <apk> -TimeoutSec <n>` (default 300s) kills the wedged `adb install` and throws. Recover with `adb kill-server; adb start-server`, re-lease the device, then retry the install. **Do not reboot the device *during* a pending install** — it orphans the install session and the waiting call (this is what turned a wedged install into a multi-hour stall). |

**A hung install is the classic lane-killer.** Because `adb install` is a blocking call, a wedge freezes the
whole sub-agent — its own 30-min/point cap can't fire while it's stuck inside the call, and (since the agent
never *completes*) the parent gets **no completion notification** to wake on. Two safeguards, both already in
the skill: (1) sub-agents install with a bounded `-TimeoutSec` so a wedge fails fast and is recorded; (2) the
**parent** watches lanes on a wall clock (progress-log / `tool_calls_completed` growth), and on a stall marks
the case ABORTED, recovers adb, and re-dispatches — it never blocks on an open-ended wait. See the
"Execution model" and "Running multiple test cases" sections of [SKILL.md](../SKILL.md).

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

- **Why it bites E2E:** if the test app's configured client id isn't CA-approved, eSTS returns `530021`
  and blocks the flow *before* the step you're trying to test — so it looks like the feature failed when
  the app config is the real cause.
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

## PowerShell script encoding (UTF-8 BOM)

**Symptom:** a skill script (`report.ps1`, `deviceui.ps1`, …) that runs fine under `pwsh` 7 fails to
**parse** under **Windows PowerShell 5.1** — `ParserError`, "Unexpected token", or garbled characters like
`â€"` where an em-dash (`—`) should be.

**Cause:** Windows PowerShell 5.1 decodes a `.ps1` that has **no byte-order mark (BOM)** as **Windows-1252**,
not UTF-8. Any non-ASCII byte (em-dash, arrows `→`, `×`, box-drawing) then mojibakes and can break the
parser. `pwsh` 7 defaults to UTF-8, so the same file runs there and the bug hides until a 5.1 host runs it.

**Fix:** save scripts as **UTF-8 *with* BOM** (Microsoft's recommended cross-version encoding). All skill
scripts are stored this way. Idempotent re-encode (only touches files that have non-ASCII and lack a BOM):
```powershell
Get-ChildItem .github/skills/android-e2e-tester/scripts/*.ps1 | ForEach-Object {
  $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
  $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
  $nonAscii = $bytes | Where-Object { $_ -gt 0x7F }
  if ($nonAscii -and -not $hasBom) {
    [System.IO.File]::WriteAllBytes($_.FullName, ([byte[]](0xEF,0xBB,0xBF)) + $bytes)
    "BOM added: $($_.Name)"
  }
}
```
**Verify under both editions:** `powershell -NoProfile -Command "& { . ./scripts/report.ps1 }"` (5.1) and
the same with `pwsh`. Prefer ASCII in scripts where practical; when you do use non-ASCII, keep the BOM.
This is a **tooling** bug in the harness, never a defect in the app under test.
