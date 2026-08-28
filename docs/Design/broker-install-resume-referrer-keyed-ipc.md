# Design: Seamless Broker Install-and-Resume on Android — "Referrer-keyed Resume over Broker IPC"

| | |
|---|---|
| **Status** | Draft / Proposal |
| **Author(s)** | _TBD_ |
| **Reviewers** | MSAL Android SDK team, Microsoft Authenticator / Company Portal team, eSTS team |
| **Affected repos** | `AzureAD/microsoft-authentication-library-common-for-android` (`common` / `common4j`), Company Portal (internal), eSTS (optional) |
| **Tracking issue** | _TBD_ |

> **Note on repo placement:** `AzureAD/android-complete` is a Gradle umbrella/submodule aggregator; no production code lands here. This document lives here as a cross-cutting design record (under `docs/Design/`, can be relocated later). The implementation changes it describes land in `microsoft-authentication-library-common-for-android` and Company Portal.

---

## 1. Problem statement

During an interactive auth request in a first-party (1P) app, a Conditional Access (CA) policy can require a broker (Company Portal / Microsoft Authenticator) that is not yet installed. Today the Android experience is:

> User signs into 1P app &rarr; blocked by CA to install Company Portal (CP) &rarr; user installs CP &rarr; opens CP &rarr; sees **"Sign in"** on first launch &rarr; user must somehow understand they need to return to the 1P app and retry &rarr; only then is the request completed in broker context.

**Desired experience:**

> User signs into 1P app &rarr; blocked by CA to install CP &rarr; user installs CP &rarr; taps **Open** &rarr; **the original 1P request is resumed and completed in broker context inside CP, and the result is returned to the calling 1P app.** No "Sign in" dead-end, no manual return.

This is the Android equivalent of the iOS behavior in `MSIDBrokerInteractiveController` (`saveToPasteBoard:` + `checkTokenResponse:` replay), where the broker picks up the in-flight request after install.

## 2. Goals / Non-goals

### Goals
- After installing CP from the CA prompt, the user's **original** interactive request resumes automatically and completes in broker context.
- The token result is returned to the **originating** 1P app so its pending `acquireToken(...)` resolves.
- No reliance on the user manually re-initiating the request.
- Use Android-native, broker-trusted transports; **no clipboard**.

### Non-goals
- Changing the CA policy evaluation or the eSTS decision to require a broker.
- Supporting silent (non-interactive) resume across install (interactive only here).
- Persisting requests indefinitely — resume records are short-lived and single-use.

## 3. Background — current behavior in code

When the eSTS interactive page redirects to the broker-install URL, the redirect is classified as `BROKER_INSTALLATION_TRIGGERED` and the SDK **returns control to the 1P app and expects a call back**:

- `common4j/.../providers/oauth2/AuthorizationResultFactory.java` — the `BROKER_INSTALLATION_TRIGGERED` case logs *"we expect the apps to call us back when the broker is installed"* and extracts `upn_to_wpj`.
- `common/.../ui/webview/AzureActiveDirectoryWebViewClient.java` — `processInstallRequest(...)` parses `app_link`, returns the result, then `startActivity(ACTION_VIEW, app_link)` to the Play Store and stops. **Nothing is persisted for CP to pick up.**
- `common4j/.../providers/RawAuthorizationResult.java` — `BROKER_INSTALLATION_TRIGGERED(2006)` is documented as *"Waiting for broker package to be installed, and resume request in broker."*

Existing primitives we build on:
- **Install `referrer`** — `common4j/.../providers/BrokerInstallLinkValidator.kt` documents that each eSTS-emitted `app_link` *"may carry an optional `referrer` parameter set by the server."* Today that value is the **originating package name** (e.g. `referrer=com.msft.identity.client.sample.local`), seen in the example URLs in `processInstallRequest` / `RawAuthorizationResult`. **It does not currently carry a correlation id.**
- **Broker ContentProvider IPC** — `common/.../broker/ipc/ContentProviderStrategy.java` + `BrokerOperationBundle` already provide a trusted, marshalled IPC channel between apps and the broker.
- **Broker result path** — `BrokerMsalController` registers for the `RETURN_BROKER_INTERACTIVE_ACQUIRE_TOKEN_RESULT` broadcast (via `BrokerActivity`) to deliver results back to the waiting caller.

> &#9888; **Unverified assumptions (must be validated before build):**
> 1. That eSTS emits a `referrer` on the **Company Portal** install link (not just the Authenticator one).
> 2. That Google Play reliably delivers that `referrer` to CP's `InstallReferrerClient` on first launch.
> 3. That a `correlationId` can be added to the referrer within size/format limits.
>
> These are design dependencies, not confirmed capabilities.

## 4. Recommended design — "Referrer-keyed resume over broker IPC"

### Step A — 1P persists the pending request before leaving (SDK change, `common`)
At the `BROKER_INSTALLATION_TRIGGERED` branch (in `processInstallRequest` / `BrowserAuthorizationFragment.completeAuthorizationInBrowserFlow`), the 1P SDK saves the original `InteractiveTokenCommandParameters` into a **local, broker-readable resume store**, keyed by a freshly generated `correlationId`.
- Encrypted at rest.
- Exposed only to the broker package signature.
- Single-use and TTL-bounded.

### Step B — Carry a correlation key into CP via the install referrer
Extend the install `referrer` (which already conveys the originating package) to also carry the `correlationId`. The referrer is small, so it carries a **pointer**, not the payload. **No clipboard involved.** *(Depends on the §3 unverified assumptions; may require an eSTS tweak.)*

### Step C — CP reads the referrer on first launch (CP change)
On first run, **before** showing "Sign in," CP calls the Play Install Referrer API and extracts `{originating package, correlationId}`.

### Step D — CP pulls the real request from 1P over ContentProvider IPC (both sides)
CP queries the originating 1P app's resume provider for `correlationId` and retrieves the full `InteractiveTokenCommandParameters`. This is the Android-native equivalent of iOS reading the request off the named pasteboard — but over the IPC the broker already trusts. Access is gated by package + signature checks on both sides.

### Step E — CP runs it in broker context and returns the result
CP executes the interactive request locally (it is the broker), then returns the token result to the originating 1P app through the **existing broker result path** (`AccountManager` / `RETURN_BROKER_INTERACTIVE_ACQUIRE_TOKEN_RESULT`). Add the Android analog of iOS's `MSID_IGNORE_BROKER_REQUEST=1` so CP does **not** re-trigger an install prompt on resume.

**Result:** User taps **Open** on CP &rarr; CP detects the pending 1P request &rarr; completes it in broker context &rarr; 1P's original `acquireToken` resolves with a token. No "Sign in" dead-end, no manual return.

### Sequence

```
1P app          eSTS            Play Store        Company Portal        1P resume provider
  |  interactive  |                  |                   |                       |
  |-------------->|                  |                   |                       |
  |  app_link (CA: install broker)   |                   |                       |
  |<--------------|                  |                   |                       |
  | [A] persist request (corrId), keyed + encrypted ----------------------------->|
  | [B] launch install (referrer = pkg + corrId) ------->|                       |
  |               |                  |  install + Open   |                       |
  |               |                  |------------------>|                       |
  |               |                  |   [C] read referrer{pkg, corrId}          |
  |               |                  |   [D] query resume provider(corrId) ----->|
  |               |                  |   [D] return InteractiveTokenCommandParams<-|
  |               |   [E] run interactive in broker ctx  |                       |
  |   [E] result via RETURN_BROKER_INTERACTIVE_ACQUIRE_TOKEN_RESULT              |
  |<-------------------------------------------------------|                     |
  | acquireToken(...) resolves with token                  |                     |
```

## 5. Fallback design — ships without CP changes ("redirect back to calling app")

Delivers the "redirected back to the calling app" half **now**, 1P-side only:
1. On `BROKER_NEEDS_TO_BE_INSTALLED`, save the original `AcquireTokenParameters` + `upn_to_wpj` (encrypted `SharedPreferences`).
2. Register a **package-added receiver** / `onResume` broker-discovery check to detect when CP is installed.
3. On detection, **auto-retry** the saved request. `CommandDispatcher.beginInteractive` + `BrokerMsalController` route it through the now-installed broker and it completes.

To also cover "user tapped **Open** on CP instead of returning to 1P," pair this with a small **CP &rarr; 1P deep-link bounce**: after registration, CP deep-links back into the originating package, triggering the auto-retry. The deep-link bounce is a **much smaller** CP change than full in-CP resume.

Trade-off: completion happens after returning to the 1P app (a blink), not literally inside CP.

## 6. Key constraint

The literal "finish **inside** CP" outcome **requires Company Portal changes** — MSAL in the 1P app cannot make CP resume a request CP never received. There is **no MSAL-only** path to in-CP completion. MSAL alone can deliver the automatic "redirect back to 1P and retry" (&sect;5).

## 7. Why not the clipboard

The iOS implementation uses a **named** `UIPasteboard("WPJ")` as a cross-app, cross-install handoff. This does **not** port to Android:
- Android has **no named/private pasteboards** — only the single system clipboard shared with every app and the user.
- **Background clipboard reads are blocked on Android 10+ (API 29).**
- **Clipboard reads raise a user-visible toast on Android 12+ (API 31).**

So a freshly-installed CP cannot silently read the request off the clipboard the way Authenticator does on iOS. The **install referrer + ContentProvider IPC** route is the correct Android substitute.

## 8. Security considerations

- **Confidentiality:** resume records encrypted at rest; provider access gated by broker **package + signature** verification on both sides.
- **Integrity / anti-spoofing:** `correlationId` is single-use, TTL-bounded, and validated against the originating package from the referrer.
- **Allowlist:** continue enforcing `BrokerInstallLinkValidator` on `app_link`; any added `referrer` content must be validated and length-bounded.
- **No secrets in referrer:** the referrer carries only a pointer (`correlationId` + package), never request payload or tokens.
- **Cleanup:** resume record deleted on successful completion, on TTL expiry, and on failure.

## 9. Open questions / dependencies

1. **eSTS:** Does it emit a `referrer` on the **CP** install link today? Can it include a `correlationId`? (Blocks Step B.)
2. **Play delivery:** Confirm Google Play delivers the referrer to CP's `InstallReferrerClient` on first launch reliably. (Blocks Step C.)
3. **CP first-run hook:** Where in CP's first-launch sequence can the referrer check + IPC pull run **before** the "Sign in" screen?
4. **Protocol versioning:** New ContentProvider operation + bump of the MSAL&harr;Broker protocol version for the resume-pull.
5. **Ignore-install guard:** Define the Android analog of `MSID_IGNORE_BROKER_REQUEST=1` and where it is applied on resume.
6. **TTL / lifecycle:** Concrete resume-record TTL and the "user never opens CP" cleanup path.

## 10. Work breakdown

**`microsoft-authentication-library-common-for-android` (`common` / `common4j`):**
- Persist-request at `BROKER_INSTALLATION_TRIGGERED` (encrypted, keyed by `correlationId`).
- Resume `ContentProvider` (signature-gated) exposing the saved request.
- Ignore-install guard on resume.
- Fallback (&sect;5): save params + package-added/`onResume` auto-retry.

**Company Portal (internal):**
- First-launch referrer read (before "Sign in").
- IPC pull of the request from the originating app.
- Run interactive in broker context + return via the existing result path.
- (Fallback) deep-link bounce back to the originating package.

**eSTS (optional):**
- Emit/extend `referrer` with `correlationId` on the CP install link.
