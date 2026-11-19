# Android Auth Code Simplification Examples

Concrete before-and-after patterns from real `Broker` and `Common` pull requests illustrating how to spot and eliminate AI over-engineering while enforcing strict flight isolation.

## Table of Contents

1. [Exploit Single-Owner Invariants Instead of Token Maps](#1-exploit-single-owner-invariants-instead-of-token-maps)
2. [Collapse Single-Caller Tracker Classes & Revert Collaborators](#2-collapse-single-caller-tracker-classes--revert-collaborators)
3. [Gate Flight Once at the Boundary & Flatten Helpers](#3-gate-flight-once-at-the-boundary--flatten-helpers)
4. [Stop Review-Driven State Accretion & Remove Dead `Api24Impl` Wrappers](#4-stop-review-driven-state-accretion--remove-dead-api24impl-wrappers)
5. [Data-Driven Central Registries (`buildSet`) Over Cross-Repo Plumbing](#5-data-driven-central-registries-buildset-over-cross-repo-plumbing)

---

## 1. Exploit Single-Owner Invariants Instead of Token Maps

**Scenario (`Broker` `AccountChooserActivity`)**: Release the calling package's entry in `sCallingApps` during `finish()` (behind a default-off flight) while ensuring that the finishing Activity's delayed `onDestroy()` does not evict a newer sequential Activity's entry.

### Over-engineered (`+93` prod lines, `+586` test lines, unflighted allocations)
- Added a static `Map<String, Object> sCallingAppRegistrationTokens = new HashMap<>()`
- Allocated `private final Object mCallingAppRegistrationToken = new Object()` on **every** Activity instance even when `flight = false`
- Added `@Nullable private Integer mRegisteredCallingAppProcessId`
- Added 3 `@VisibleForTesting` / private initialization helpers with speculative `try/catch (RuntimeException)` around `isFlightEnabled(...)`

```java
// BAD: Unflighted per-instance allocation + parallel static token map
private static final Map<String, Object> sCallingAppRegistrationTokens = new HashMap<>();
private final Object mCallingAppRegistrationToken = new Object();
@Nullable private Integer mRegisteredCallingAppProcessId;
```

### Simplified (`+31/-1` prod lines, `+196` test lines, 100% flight-isolated)
Because `AccountChooserActivity` already rejects parallel callers in `onCreate()`, at most one Activity instance owns a calling package's registration at a time. Each owning instance only needs to release **at most once** across `finish()` and `onDestroy()`:

```java
// GOOD: Zero allocations when flight is off; 1 primitive boolean guarded by existing sLock
private volatile boolean mReleaseCallingAppUiLockOnFinishEnabled;
private boolean mCallingAppUiLockReleased;

// In onCreate():
if (mBrokerFlightsProvider != null
        && mBrokerFlightsProvider.isFlightEnabled(BrokerFlight.RELEASE_CALLING_APP_UI_LOCK_ON_FINISH)) {
    mReleaseCallingAppUiLockOnFinishEnabled = true;
}

private void clearCallingAppUIRequestOnce(final String packageName) {
    sLock.lock();
    try {
        if (!mCallingAppUiLockReleased) {
            mCallingAppUiLockReleased = true;
            sCallingApps.remove(packageName);
        }
    } finally {
        sLock.unlock();
    }
}
```

---

## 2. Collapse Single-Caller Tracker Classes & Revert Collaborators

**Scenario (`Common` WebView passkey back-navigation)**: Skip superseded passkey challenge pages when pressing Back in embedded WebView behind a default-off `CommonFlight`.

### Over-engineered (`10 files / +1,087 lines`)
- Created a standalone 196-line `FidoChallengeHistoryTracker.kt` + 410-line `FidoChallengeHistoryTrackerTest.kt`
- Added an unflighted constructor callback parameter on `AuthFidoChallengeHandler.kt` (+66 lines in `AuthFidoChallengeHandlerTest.kt`)
- Added `@VisibleForTesting` setters on `WebViewAuthorizationFragment.java`

### Simplified (`6 files / +393 lines`, `0` new classes)
- Deleted `FidoChallengeHistoryTracker.kt` and `FidoChallengeHistoryTrackerTest.kt`
- Reverted `AuthFidoChallengeHandler.kt` and `AuthFidoChallengeHandlerTest.kt` completely to `origin/dev`
- Kept the entire production change inside the 3 existing files (`CommonFlight`, `WebViewAuthorizationFragment`, `AzureActiveDirectoryWebViewClient`) with lazy `null` state until the flight is enabled

---

## 3. Gate Flight Once at the Boundary & Flatten Helpers

### Hard to read (large inline flight block or double-checked flight)
```java
// BAD: Caller checks flight, AND the new helper checks the exact same flight again with deep nesting
// In WebViewAuthorizationFragment:
if (CommonFlightsManager.INSTANCE.getFlightsProvider()
        .isFlightEnabled(CommonFlight.SKIP_PASSKEY_CHALLENGE_ON_WEBVIEW_BACK)) {
    if (mWebViewClient.navigateBackSkippingSupersededPasskeyPages(mWebView)) {
        return true;
    }
}

// In AzureActiveDirectoryWebViewClient:
public boolean navigateBackSkippingSupersededPasskeyPages(@Nullable final WebView webView) {
    if (CommonFlightsManager.INSTANCE.getFlightsProvider()
            .isFlightEnabled(CommonFlight.SKIP_PASSKEY_CHALLENGE_ON_WEBVIEW_BACK)) {
        if (webView != null) {
            // deeply nested logic...
        }
    }
    return false;
}
```

### Clean & readable (gate once at caller, flat early returns in dedicated helper)
```java
// GOOD: New helper is only called when flight is enabled; body stays flat with early returns
public boolean navigateBackSkippingSupersededPasskeyPages(@Nullable final WebView webView) {
    if (webView == null || mSupersededPasskeyHistoryUrls == null) {
        return false;
    }
    final WebBackForwardList history = webView.copyBackForwardList();
    if (history == null || history.getCurrentIndex() <= 0) {
        return false;
    }
    // concise, flat logic...
}
```

Similarly, when adding flighted logic inside a large existing callback (`shouldOverrideUrlLoading`, `onCreate`, `finish`), keep the existing method readable by gating in one `if (isFlightEnabled(...)) { recordPasskeyChallenge(...); }` line rather than inlining a 25-line block.

---

## 4. Stop Review-Driven State Accretion & Remove Dead `Api24Impl` Wrappers

**Scenario (`Common` PR `#3357` review follow-up)**: After simplifying `AzureActiveDirectoryWebViewClient`, an automated review comment raised a theoretical uncommitted-redirect edge case.

### Over-engineered review patch (`+74` prod lines added back, then `-59` stripped)
- Added `NavigableMap`/`TreeMap`, `mPendingPasskeyMainFrameUrl`, `mLastCommittedPasskeyMainFrameUrl`, `onPageStarted`, `doUpdateVisitedHistory`, and `onPageFinished` bookkeeping.
- Added `SDK_INT >= Build.VERSION_CODES.N` + `@RequiresApi(N) private static final class Api24Impl` just to call `request.isRedirect()`, because a Robolectric unit test without `@Config(sdk = [Build.VERSION_CODES.P])` ran on an older default SDK.
- Reviewers immediately pushed back: *"What is this? Why needed?"* and *"Why do we need so much complex code? Doesn't Android / WebView provide a native way?"*

### Simplified (`22` lines vs. `81` lines)
1. **Verified native API tradeoffs first**: Checked `WebView.clearHistory()`, confirmed it wipes *all* prior history entries (breaking 2-step email -> passkey Back flows) and requires `onPageFinished` timing, so kept a flat lazy `HashMap<Integer, String>` rather than `TreeMap` + 3 lifecycle hooks.
2. **Deleted `Api24Impl` and `SDK_INT >= N`**: `minSdkVersion` is already `24`; adding `@Config(sdk = [Build.VERSION_CODES.P])` on `WebViewAuthorizationFragmentTest` let production call `request.isRedirect()` directly.
3. **Resisted bot ping-pong**: When automated review immediately asked to restore the removed pending-vs-committed state machine, paused to decline/justify rather than re-bloating the PR.

---

## 5. Data-Driven Central Registries (`buildSet`) Over Cross-Repo Plumbing

**Scenario (`Common` + `Broker` Browser SSO allowlists — Island PRs `#3298`/`#330` -> Samsung PRs `#3360`/`#383`)**: Authorize a new partner browser's package names and SHA-512 signing certificates behind a default-off `CommonFlight`.

### Over-engineered pattern (early Island drafts)
- Invented an `AppFamily` model, dedicated partner `ICallingAppValidator` fields, nullable constructor injection params with `@JvmOverloads`, a boolean flag parameter on `getBrowserSsoAuthorizedApps(isIslandEnabled)`, and a caller-side package-name precheck in `Broker` (whose exact-case comparison subtly disagreed with the shared validator's case-insensitive check).

### Simplified data-driven pattern (final Island + Samsung Browser SSO)
- **Central no-arg provider in `Common` (`AppRegistry.getBrowserSsoAuthorizedApps()`)**: Evaluates each partner `CommonFlight` centrally and returns eligible `App` identities. Downstream `Broker` (`BrowserAllowList`) required **zero production logic changes** for Samsung Browser SSO—only KDoc, tests, and the `common` submodule pointer.
- **Unmodifiable `@JvmField` sets via `buildSet`**: Used `@JvmField val SAMSUNG_BROWSER_SSO_APPS = buildSet { ... }` instead of `setOf(...)` (which exposes a mutable `LinkedHashSet` to Java callers).
- **Behavioral validator tests over copied constants**: Tested `ICallingAppValidator.requireAllowedCaller(uid)` (flight off, unknown signer via `PackageUtils.verifySignatureHash`, valid signers) and independent flight toggles rather than copying thumbprint strings or nesting $2^N$ `listOf(false, true).forEach` loops across every partner flight.
