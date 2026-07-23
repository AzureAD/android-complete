# App & Module Map — What to Build, Install, and Test

Table of contents:
- [Pick the test surface from the feature](#pick-the-test-surface-from-the-feature)
- [Module → path → typical package](#module--path--typical-package)
- [Known provided APKs (staged test builds)](#known-provided-apks)
- [Discover the exact package & launch activity at runtime](#discover-the-exact-package--launch-activity-at-runtime)
- [Two E2E execution modes](#two-e2e-execution-modes)
- [Broker pairing rules](#broker-pairing-rules)
- [Test credentials (Lab API) and when they block](#test-credentials-lab-api-and-when-they-block)
- [Emulator / real-device requirements per feature](#emulator--real-device-requirements-per-feature)

The repo root (`android-complete`) aggregates sub-repos cloned via `git droidSetup`:
`msal/`, `common/`, `broker/`, `adal/`, and optionally `authenticator/`. Gradle modules are
declared in `settings.gradle`. If a sub-repo folder is missing, the checkout hasn't been set up —
that is a blocker to raise with the user.

## Pick the test surface from the feature

Map the changed repo(s) (from `git diff` / the design) to the app you drive:

| Feature lives in | App to install & drive | Also install (dependency) |
|---|---|---|
| **msal** (SDK) | `:msalTestApp` (interactive) or `:msalautomationapp` (automated) | A broker for brokered flows (see pairing) |
| **broker / broker4j** | `:brokerHost` test host + a broker (`:AADAuthenticator`) | `:msalTestApp` as the calling client |
| **common / common4j** (IPC, cache, telemetry) | Both sides: `:msalTestApp` **and** a broker | — |
| **adal** (legacy) | `:adalTestApp` | A broker for brokered ADAL flows |
| **authenticator** (opt-in) | `:MSAuthenticator` | — |

Isolated broker testing without real Authenticator/Company Portal can use the **mock brokers**:
`:mockauthapp` (mock Authenticator), `:mockcp` (mock Company Portal), `:mockltw` (mock Link-to-Windows).

## Module → path → typical package

Package IDs below are **typical hints** — always verify at runtime (next section), because they
vary by build variant (e.g. `localDebug` often adds a `.local` suffix).

| Gradle module | Project path | Typical applicationId |
|---|---|---|
| `:msalTestApp` | `msal/testapps/testapp` | `com.msft.identity.client.sample.local` |
| `:msalautomationapp` | `msal/msalautomationapp` | `com.microsoft.identity.client.msalautomationapp` |
| `:brokerHost` | `broker/userapp` | `com.microsoft.identity.testuserapp` |
| `:brokerautomationapp` | `broker/brokerautomationapp` | `com.microsoft.identity.client.brokerautomationapp` |
| `:AADAuthenticator` | `broker/AADAuthenticator` | `com.azure.authenticator` |
| `:adalTestApp` | `adal/userappwithbroker` | `com.microsoft.aad.adal.testapp` |
| `:mockcp` | `broker/mockbrokers/mockcp` | mock Company Portal |
| `:mockauthapp` | `broker/mockbrokers/mockauthapp` | mock Authenticator |
| `:MSAuthenticator` | `authenticator/PhoneFactor/app` | `com.azure.authenticator` |

Real production brokers used in some E2E: Microsoft Authenticator `com.azure.authenticator`,
Intune Company Portal `com.microsoft.windowsintune.companyportal`, Link to Windows `com.microsoft.appmanager`.

## Known provided APKs

<a id="known-provided-apks"></a>
When the user hands you a folder of APKs (commonly `…\Downloads\ECS` and `…\Downloads\Local`, or
`…\Downloads\APKs\…`), map them by **filename** so you install the right thing without re-deriving it each
run. `-signed` = a production-signed release build; `-dist-debug` / `-local-debug` = the test/automation
harness builds. Always confirm the real `applicationId` after install (next section) — filenames are a
convenience, not a contract.

| Provided APK filename (with/without `.apk`) | App | Package (verify at runtime) | Role in a test |
|---|---|---|---|
| `app-production-universal-release-signed` | **Microsoft Authenticator (app under test)** | `com.azure.authenticator` | The build being tested; **universal** ABI — safe on emulators too. |
| `app-production-arm64-v8a-release-signed` | Microsoft Authenticator (arm64 only) | `com.azure.authenticator` | Same app, arm64-only — use on a **physical** arm64 device; can fail dexopt on an x86_64 emulator (prefer the universal one there). |
| `com.microsoft.windowsintune.companyportal-signed` | **Intune Company Portal** | `com.microsoft.windowsintune.companyportal` | Broker / MDM companion for WPJ / device-registration / MAM-CA flows. |
| `company-portal` | Intune Company Portal | `com.microsoft.windowsintune.companyportal` | Same app, alternate filename. |
| `teams` | Microsoft Teams | `com.microsoft.teams` | 1P OneAuth client (some brokered/SSO scenarios). |
| `msalTestApp-dist-debug` | MSAL test app | `com.msft.identity.client.sample(.local)` | Calling client for MSAL / brokered flows (Mode B). |
| `adalTestApp-dist-debug` | ADAL test app | `com.microsoft.aad.adal.testapp` | Calling client for legacy ADAL flows. |
| `brokerHost-local-debug` | Broker Host test app | `com.microsoft.identity.testuserapp` | Drives broker APIs directly (Get Accounts / device state). |

## ECS vs Local builds (test-point configuration)

When a case is staged with both an `ECS\` and a `Local\` folder, **which folder you install the app-under-test
from is decided by the ADO test point's *configuration*, not by a fixed default:**

| Test-point configuration name | Build folder to install from |
|---|---|
| contains **`LocalFlights`** (e.g. `RC MSAL - RC Broker (LocalFlights)`) | **`Local\`** |
| anything else (e.g. plain `RC MSAL - RC Broker`) | **`ECS\`** |

> ⚠️ **Counter-intuitive — read twice.** The `LocalFlights` configuration maps to the **`Local\`** folder, and
> the *plain* configuration maps to the **`ECS\`** folder. Do **not** assume "LocalFlights → ECS". A case with
> two test points is therefore installed **from `Local\` for the LocalFlights point and from `ECS\` for the
> plain point** — i.e. you run that case **twice**, once per folder.

Install the **app-under-test** from the folder the point's configuration selects; pull any *other* required app
(a broker the case needs but didn't stage, Teams, Chrome) from that **same** folder if present, else the Play
Store. How to enumerate a case's test points and read each one's configuration is in the SKILL
([Phase 1 → Test points and configurations](../SKILL.md#test-points-and-configurations-which-build-to-run)).

## Discover the exact package & launch activity at runtime

Do not trust the hints above blindly. Resolve the real values:

```powershell
# applicationId from the module's build.gradle (accounts for variant suffixes):
Select-String -Path msal/testapps/testapp/build.gradle -Pattern 'applicationId'

# After install, confirm the installed package and find the LAUNCHER activity:
./scripts/appcontrol.ps1 is-installed -Package com.msft.identity.client.sample.local
adb shell cmd package resolve-activity --brief -c android.intent.category.LAUNCHER <package>

# Or read a built APK directly:
& "$env:ANDROID_HOME\build-tools\<ver>\aapt.exe" dump badging <apk> | Select-String 'package:|launchable-activity'
```

Then launch with the resolved values:
```powershell
./scripts/appcontrol.ps1 launch -Package <package> -Activity <fully.qualified.Activity>
```

## Two E2E execution modes

**Mode A — Automated instrumented tests (preferred when a matching test exists).**
The automation apps drive full sign-in flows and fetch lab accounts automatically via the Lab API.
Run the connected test task for the variant:
```powershell
./gradlew :msalautomationapp:connectedLocalDebugAndroidTest --tests "*<TestClassOrMethod>*"
./gradlew :brokerautomationapp:connectedLocalDebugAndroidTest --tests "*<TestClassOrMethod>*"
```
Results land in `build/reports/androidTests/connected/` and `build/outputs/androidTest-results/`.
Read those reports plus `./scripts/authlogs.ps1 scan` to judge pass/fail.

**Mode B — Interactive UI-driven (for new features with no automated test yet).**
Install the test app, launch it, and drive the UI with `deviceui.ps1` (tap the acquire-token
button, sign in, handle prompts), then verify with `authlogs.ps1 scan`. Use this when the feature
is brand-new or the scenario is exploratory. See [ui-interaction.md](ui-interaction.md).

Choose Mode A if an automation test already covers the flow; otherwise Mode B.

## Broker pairing rules

Brokered auth requires **a calling app + a broker app**, both installed:

- The broker must be a build the client trusts (signature/redirect-URI must match). Local `localDebug`
  test apps are built to trust the `localDebug` broker signatures — keep both on the same variant.
- Signature mismatch on install shows `INSTALL_FAILED_UPDATE_INCOMPATIBLE` → uninstall the old one first
  (`./scripts/appcontrol.ps1 uninstall -Package <pkg>`).
- Only one active broker is used at a time. If both Authenticator and Company Portal are present, the
  broker-selection logic picks one; for deterministic tests install a single broker (or a mock).
- After installing/replacing a broker, **clear the client app state** (`appcontrol.ps1 clear`) so it
  re-discovers the broker cleanly.

## Test credentials (Lab API) and when they block

- Automated tests (Mode A) obtain lab accounts through `common/labapi` / the Lab API automatically —
  no manual credential handling.
- Interactive tests (Mode B) need a test account. Preferred sources, in order:
  1. A lab account the user provides in the session.
  2. A credential the automation harness/Lab API exposes.
- **Provision or repair accounts on demand with `scripts/labapi.ps1`** (`create-user`, `reset`,
  `enable-policy`/`disable-policy`, `delete-device`). Full endpoint list, usertypes/policies, the EasyAuth
  auth workaround, and entitlements are in **[lab-api.md](lab-api.md)**.
- **Blocker to raise with the user** if none is available, or if the scenario needs a real MSA/consumer
  account, a specific tenant/CA policy, or a federated account that the AI cannot provision (try
  `labapi.ps1 disable-policy` first if it's a lab CA policy).
- **Never** hardcode, print, or commit real credentials. Type them into the device only (`input-text
  -Secret`); never echo them into logs or the transcript.

## Emulator / real-device requirements per feature

Pass these to `emulator.ps1 ensure` (they gate both AVD selection and real-device eligibility):

- **Broker / push / GMS-dependent flows** → `-RequireGoogleApis` (needs Google Play services for FCM,
  account manager). Prefer a `google_apis` or `google_apis_playstore` image.
- **Min OS behavior** (e.g. scoped storage, notification permission on API 33+) → `-ApiLevel <n>`.
- **Play Store app installs** (rare in tests) → `-RequirePlayStore`.
- Most auth E2E works on a standard `google_apis` image, API 30+.
