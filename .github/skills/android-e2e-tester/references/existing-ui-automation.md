# The existing Authenticator UIAutomator suite — check it before driving a case by hand

**There is already a compiled, unattended UIAutomator test suite for Microsoft Authenticator, and it is
annotated with the same ADO Test Case ids this skill runs manually.** Before you drive an Authenticator
case screen-by-screen, check the table below: if an automated test exists, running it (or at minimum reading
it) is faster, more repeatable, and tells you the exact selectors and ordering the app really needs.

This skill and that suite are **complementary, not competing** — see
[Which one to use](#which-one-to-use).

- [Where it lives](#where-it-lives)
- [ADO test case → automated test](#ado-test-case--automated-test)
- [How to run it](#how-to-run-it)
- [How it differs from this skill](#how-it-differs-from-this-skill)
- [Which one to use](#which-one-to-use)
- [What we borrowed from it](#what-we-borrowed-from-it)

## Where it lives

| | |
|---|---|
| Repo | `msazure` / project `One` / **`AD-MFA-phonefactor-phoneApp-android`** (ADO) |
| Path | `/PhoneFactor/uiautomator-tests` |
| Module type | `com.android.library` — a **standalone** test module; it does **not** bundle the app, it installs APKs onto the device |
| Tests | `…/src/androidTest/java/com/azure/authenticator/standalone/tests/` |
| How-to doc | IdentityWiki → `Teams/DevEx-Android/How-Tos/How-to-run-Authenticator-UI-Automation-tests-locally.md` |

It is **not** part of `android-complete`, so it is not checked out by `git droidSetup` — read it via the ADO
API or clone that repo separately.

## ADO test case → automated test

Confirmed by the `Test Case <id>` annotation in each test class:

| ADO case | Automated test | Flow |
|---|---|---|
| 1579381 | `MfaViaSignInTest`, `MfaViaSignInUpgradeTest` | Entra MFA registration via sign-in |
| 1579382 | `MfaViaLinkTest`, `MfaViaLinkUpgradeTest` | Entra MFA registration via link |
| 1579386 | `MfaRichContextTest` | MFA rich context |
| 1579390 | `PendingAuthForegroundTest` | Pending auth in foreground |
| 1579395 | `NgcViaSignInTest`, `NgcViaSignInUpgradeTest` | NGC (passwordless) via sign-in |
| 1579396 | `NgcViaMfaUpgradeTest` | NGC upgrade from MFA |
| 1579397 | `NgcCheckForAuthTest` | NGC check-for-auth |
| 1579400 | `NgcRichContextTest` | NGC rich context |
| 1579401 | `LbacInteractiveGpsTest` | LBAC, interactive GPS |
| 1579402 | `LbacBackgroundGpsTest` | LBAC, background GPS |
| 1579405 | `AccountMgmtPagesTest` | Account-management pages |
| 1579419 | `Add3rdPartyViaUrlTest` | Add 3rd-party (TOTP) account via URL |
| 1579422 | `SharedDeviceRegistrationTest` | Shared-device registration |
| 1579428 | `AppLockManualTest` | App Lock, manual enable |
| 1579429 | `AppLockAutoEnabledTest` | App Lock, auto-enabled |
| 1588699 | `DeviceRegistrationWithCpBrokerTest` | Device registration with Company Portal broker |
| 2547138 | `NgmsSanityCheckTest` | NGMS sanity |
| 2916347 | `PasskeyInAppRegistrationTest` | Passkey in-app registration |
| 2916524 | `PasskeyDeregisterTest` | Passkey deregistration |
| 3094596 | `PasskeyInAppAllPreConfiguredTest` | Passkey, all pre-configured |
| 3094640 | `PasskeySkipScreenLockTest` | Passkey, skip screen lock |
| 3094641 | `PsiSkipDeviceRegistrationTest` | PSI, skip device registration |
| 3094649 | `PasskeyFromL2Test` | Passkey from L2 |
| 3261599 | `PsiPushNotificationTest` | PSI push notification |

> **Re-verify this table rather than trusting it.** It's a snapshot; grep the module for
> `Test Case` to regenerate. Presence in this table also doesn't guarantee the test currently passes.

**Coverage is largely disjoint from what this skill runs.** In the 20-case Authenticator batch this skill
ran, only **three** cases had an automated counterpart — 1579397, 1579401, 1579402 — and *all three* were
cases the manual run failed to complete (one ABORTED, two PARTIAL), while none of the five clean manual
passes had one. That is the strongest argument for using both: the automated suite already owns several of
the flows that are hardest to drive interactively.

## How to run it

Per the wiki how-to (read it for the current details — this is the shape, not a substitute):

1. **Prereqs:** a connected device (the suite targets **Android 36 / Pixel 9**), the APKs pushed to
   `/sdcard/`, **no pre-existing screen lock** (the PIN rule sets its own and fails if one exists), and the
   lab cert at `/data/local/tmp/LabAuth.pfx` with `labSecret` in your **global** `~/.gradle/gradle.properties`.
2. **APK names it expects:** `authenticator-rc.apk` (default under test), `authenticator-old.apk` (upgrade
   tests), `authenticator-ngms-rc.apk`, `company-portal.apk`, `teams.apk`.
3. Run a single test from Android Studio, or via Gradle's `connectedAndroidTest` with a class filter.

Note the module runs under **AndroidX Test Orchestrator** with `clearPackageData=true` (fresh state per
test) and `animationsDisabled = true`.

**Its default JUnit rule chain** (worth knowing even if you never run it — it's a good checklist of what
"clean state" means for Authenticator):

```
Timeout (50 min)
  → RetryRule (2 retries → up to 3 attempts)
    → CopyApkRule            # /sdcard/*.apk → /data/local/tmp/  (pm install CANNOT install from /sdcard)
      → CleanBrokerAppsRule  # uninstall Teams + Company Portal so Authenticator wins broker election
        → FreshInstallRule   # uninstall+install; also `wm size reset` / `wm density reset`
          → DevicePinRule    # locksettings set-pin, verify, clear afterwards
            → GrantPermissionRule(POST_NOTIFICATIONS)
```

## How it differs from this skill

| | UIAutomator suite | This skill (`android-e2e-tester`) |
|---|---|---|
| **What it is** | Compiled Kotlin JUnit4 + UIAutomator instrumentation | AI agent driving `adb` + `uiautomator dump` from PowerShell |
| **Test definition** | Hard-coded in a `.kt` test class | Read at run time from the **ADO Test Case** work item |
| **Adding a case** | Write + compile + review + merge Kotlin | Point the skill at a new ADO case id — no code |
| **Handles an unexpected screen** | Fails (the selector isn't there) | Reasons about the dump and adapts |
| **Non-determinism** | `RetryRule` — brute-force re-run | Diagnosis + targeted recovery |
| **Device** | Fixed target (Android 36 / Pixel 9), no pre-existing lock | Any leased emulator or physical device in the pool |
| **State reset** | JUnit rules + Test Orchestrator `clearPackageData` | Phase 3 uninstall→reinstall, leases, `pm clear` |
| **Accounts** | LAB API via a cert-authenticated Java client (`labSecret` + `LabAuth.pfx`) | LAB API via `labapi.ps1` (EasyAuth/WAM SSO) — **same lab, same ID4SLAB2 temp users, same usertypes** |
| **Output** | JUnit XML / `build/reports/androidTests/` | HTML + Markdown ADO test report (`report.ps1`) + suite summary |
| **Runs unattended in CI** | Yes (that's the point) | No — it's an agent-driven interactive harness |
| **Cost per run** | Cheap once written | Model tokens + wall-clock per run |
| **Cost to cover a new case** | High (engineering work) | Low (an ADO case + a prompt) |

**The real connection point: they share our own libraries.** The suite depends on
`com.microsoft.identity:uiautomationutilities` (`com.microsoft.identity.client.ui.automation.utils.UiAutomatorUtils`)
and `com.microsoft.identity:lab-api-utilities` (`com.microsoft.identity.labapi.utilities.*`) — both published
out of **`microsoft-authentication-library-common-for-android`**, i.e. the `common/` repo this project owns.
So the Authenticator team's automation is built on our automation library and our lab plumbing; a selector or
lab helper improved in `common` benefits both sides.

## Which one to use

- **Case has an automated test (table above)** → prefer the automated test for regression/repeatability.
  Use this skill when you need it run *right now* on a device you have, against a specific ECS/Local build,
  or when the automated test is red and you need to see what the screen actually does.
- **Case is not automated** → this skill. That's most of the Broker suite and most of the Authenticator
  cases outside the table.
- **Case is automated but keeps failing** → drive it with this skill to get the screen-by-screen evidence,
  then feed the fix back into the Kotlin test. This is the highest-value pairing: the agent is good at
  *diagnosing* a flaky selector, the compiled test is good at *never regressing* once fixed.
- **New flow that will be run repeatedly** → run it here first to establish the steps and the selectors,
  then hand those to the Authenticator team to encode as a Kotlin test.

## What we borrowed from it

Already folded into this skill:

| Borrowed | Where it landed |
|---|---|
| `settings put secure autofill_service null` — kill the overlay at the OS level instead of typing char-by-char | [common-blockers.md](common-blockers.md#chrome-autofill--passkey-overlay-steals-input), [run-speed.md](run-speed.md#device-prep-turn-off-animations-and-autofill) |
| `animationsDisabled` → the three `*_animation_scale 0` settings | [run-speed.md](run-speed.md#device-prep-turn-off-animations-and-autofill) |
| Canonical Authenticator resource IDs (FRX, account list, overflow, number-match) | [authenticator-app.md](authenticator-app.md#first-run-flow-fresh-install--home) |
| App Lock auto-enables when a device PIN exists → disable it right after the account is added | [authenticator-app.md](authenticator-app.md#app-lock-auto-enables-when-a-device-pin-exists) |
| `menu_check_for_notifications` as a deterministic alternative to pull-to-refresh | [authenticator-app.md](authenticator-app.md#aad-workschool-account-add-with-proof-up-number-match--same-device) |
| Heads-up push covers Chrome's URL bar for ~5 s → wait ~10 s before reading the number | [authenticator-app.md](authenticator-app.md#aad-workschool-account-add-with-proof-up-number-match--same-device) |
| Never press BACK on Chrome's "Save password?" InfoBar; use `infobar_close_button` | [common-blockers.md](common-blockers.md#chrome-autofill--passkey-overlay-steals-input) |
| Chrome identity prompt ("Stay signed out" / "Chrome notifications") survives `pm clear` | [common-blockers.md](common-blockers.md#chrome-first-run-experience-swallows-the-auth-page-blank-webview) |
| `locksettings set-pin` / `verify --old` / `clear --old` to provision a PIN without asking the user | [common-blockers.md](common-blockers.md#steps-that-need-a-fingerprint--biometric--app-lock) |
| `pm install` cannot install from `/sdcard/` — copy to `/data/local/tmp/` first | [troubleshooting.md](troubleshooting.md) |
| Uninstall Teams + Company Portal so Authenticator wins broker election | [app-and-module-map.md](app-and-module-map.md) |
