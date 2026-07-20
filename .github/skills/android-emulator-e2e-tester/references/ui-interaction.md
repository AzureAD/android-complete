# UI Interaction — Auto-Handling Inputs and Knowing When to Ask

Table of contents:
- [Interaction loop](#interaction-loop)
- [Selector strategy](#selector-strategy)
- [Common auth screens and how to drive them](#common-auth-screens-and-how-to-drive-them)
- [Inputs the AI handles automatically](#inputs-the-ai-handles-automatically)
- [Inputs that require the user (blockers)](#inputs-that-require-the-user-blockers)
- [The FLAG_SECURE gotcha](#the-flag_secure-gotcha)
- [Robustness tips](#robustness-tips)

Drive the UI with `scripts/deviceui.ps1`. The goal: perform every input a human tester *could*
reasonably do, automatically — and stop to ask only when an input genuinely cannot be produced by the AI.

## Interaction loop

For each step:
1. `deviceui.ps1 dump` (or `wait-text`) to read what is on screen **now**.
2. Decide the next action from the visible elements.
3. Act: `tap-text` / `tap-desc` / `input-text` / `key` / `finger`.
4. Re-`dump` to confirm the screen advanced. The UI tree changes after every action — never reuse a
   stale dump.
5. On an unexpected screen, `screenshot` to a run file and reason about it before continuing.

## Selector strategy

Prefer, in order: visible **text** → **content-desc** → **resource-id**. `tap-text`/`find-text` match
text first, then content-desc, case-insensitive substring (use `-Exact` for equality, `-Index N` to
pick among duplicates). When text is empty (icon-only buttons), use `tap-desc`. As a last resort, read
`Bounds` from `dump` and `tap-xy`.

## Common auth screens and how to drive them

| Screen | What to do (AI) |
|---|---|
| **App first-run / runtime permissions** | Tap `Allow` / `While using the app` / `Continue`; or pre-grant with `appcontrol.ps1 grant`. |
| **Test app main screen** | Tap the acquire-token control (e.g. `Acquire Token`, `Sign in`, `AcquireTokenSilent`). Set scopes/authority fields first if the scenario needs them. |
| **Broker account picker** | If the target account is listed, `tap-text` it (SSO). If `Add account` / `Use another account`, tap it to start interactive sign-in. |
| **eSTS email page** | `input-text` the username, then `tap-text "Next"`. |
| **eSTS password page** | `input-text` the password, then `tap-text "Sign in"`. Never log the value. |
| **Consent / permissions requested** | `tap-text "Accept"` (safe for test apps/accounts). |
| **"Stay signed in?" / KMSI** | `tap-text "Yes"` (or `No` if the scenario needs a fresh session). |
| **"Approve sign in request" (push MFA)** | Usually a **blocker** — see below, unless a TOTP/seed is available. |
| **Fingerprint / biometric prompt** | On an **emulator**, enroll once if needed (`deviceui.ps1 finger-enroll`), then `deviceui.ps1 finger -Text 1` simulates a touch. On a **real device** `emu finger` can't help — the user must have a fingerprint enrolled and press the sensor (the script will prompt). Check state with `deviceui.ps1 finger-status`. Fall back to PIN if biometrics can't be satisfied. |
| **Device PIN/pattern (keyguard)** | Set a known PIN via adb during setup, then enter it; or `emulator.ps1` dismisses a no-secure keyguard automatically. |

## Inputs the AI handles automatically

Do these without asking:

- Typing a **lab/test** username and password into fields.
- Tapping `Next`, `Sign in`, `Accept`, `Allow`, `Continue`, `Yes`, `Got it`, `Done`.
- Selecting an account from a picker; choosing `Add account`.
- Granting runtime permissions (`appcontrol.ps1 grant` or tapping the dialog).
- Dismissing benign system dialogs/ANRs (`Wait`), closing bottom sheets (`key BACK`).
- **Simulating a fingerprint** on the emulator (`finger` to touch; `finger-enroll` to enroll one first,
  `finger-status` to check). On a real device, enrolling needs the physical sensor — the script will
  prompt the user for that setup.
- Entering a **TOTP** code when the authenticator seed/secret is known to the session (compute it).
- Setting a device PIN during environment setup and re-entering it later.
- Toggling in-app test switches (feature flags exposed in the test app UI).

## Inputs that require the user (blockers)

Stop and ask the user (or report as blocked) when the step needs something the AI cannot produce:

- **Real push-notification MFA approval** on a separate physical device/Authenticator (no TOTP seed).
- **SMS / phone-call OTP** sent to a real phone number.
- **Hardware** security key (FIDO2), NFC, or a real camera/QR scan that the emulator can't provide.
- **CAPTCHA** / "prove you're human" challenges.
- A **credential the AI doesn't have** (no lab account; account needs a password only the user knows).
- A tenant/policy the AI can't provision (specific Conditional Access, federation, sovereign cloud).
- Any step the design says is **manual sign-off only**.

When blocked, state exactly which step and why, and what you need from the user (e.g. "approve the push
on your phone, then tell me to continue"). If the user can complete the manual step live, continue the
automated flow afterward.

## The FLAG_SECURE gotcha

eSTS login pages and some broker screens may set `FLAG_SECURE`, which can make `screenshot` return black
and can hide fields from `uiautomator dump`. Mitigations:
- You can often still `input-text` into a focused field even when it doesn't appear in the dump (the
  field has focus after the page loads or after tapping where it should be).
- Use `deviceui.ps1 current-app` to confirm you're on the expected login activity.
- If the tree is genuinely unreadable, fall back to `tap-xy` using known layout positions, or prefer
  **Mode A** automation tests (they use instrumented hooks that bypass this).
- If nothing works, treat it as a blocker and tell the user.

## Driving WebView login fields reliably (hard-won)

The eSTS email/password pages are a **WebView**, not native widgets. Typing into them fails silently in
a few ways — these fixes came from real runs:

- **Tap the field, then confirm focus before typing.** A blind `input-text` goes nowhere if no field is
  focused. After tapping, verify focus by checking that the soft keyboard is up
  (`adb shell dumpsys input_method | Select-String mInputShown,mServedView` — a WebView `mServedView`
  with `mInputShown=true` means the field is focused), then re-`dump` to read the field node.
- **The visible field is lower than the heading.** On the password page, the big "Enter password" text is
  *not* the input — the actual `EditText` sits below it. Tapping the heading does nothing. Get the real
  node's bounds from the accessibility tree (`uiautomator dump` still exposes the WebView's `EditText`
  node with a `resource-id` like `i0118` even when `FLAG_SECURE` blanks the screenshot) and tap its
  center.
- **Verify the text landed before submitting.** For a password field, re-dump and check the node's
  `text` length or `password="true"` state — do not tap **Sign in** until you've confirmed characters
  were entered, or you'll loop on a blank field.
- **Type passwords with special characters literally.** `~ ! # $ & * ( )` are shell metacharacters.
  `deviceui.ps1 input-text` escapes them, but if you call adb directly, single-quote the whole string
  **device-side**: `adb shell "input text 'P@ss~!word'"`. Never echo the password into the transcript.
- **Some CP/broker WebViews reject programmatic submit entirely.** With `FLAG_SECURE`, typing may work
  but tapping **Sign in** / pressing Enter doesn't register on an emulator. If a secure broker screen
  won't submit, note it as a **harness limitation** (not a defect) and, if the segment under test is
  already proven, stop there — see [mocking-flights-and-segments.md](mocking-flights-and-segments.md).

## Play Store installs (referrer / broker-install flows)

Installing an app from the Play Store during a flow (e.g. installing Company Portal for a MAM/CA flow):

- **Target the real Install button, not the rating chip.** `tap-text "Install"` can hit the "Everyone"
  content-rating label or an unrelated element. Read the button's bounds from `dump` and `tap-xy` its
  center, and dismiss the interstitial **"Got it"** age-rating dialog first if it appears.
- **Installs are slow and interstitials vary.** Poll for completion by package presence
  (`appcontrol.ps1 is-installed -Package <pkg>`) in a loop rather than assuming a fixed delay.
- **Mind time-boxed waits.** If the feature parks a request with a TTL (e.g. a sink-wait that expires
  after N minutes) and a slow emulator install blows past it, the original request may lapse. That's a
  harness-timing artifact, not a defect: re-trigger the flow with the app already installed to exercise
  the same resume path, and note the timing in the report.

## Keyboard & navigation gotchas

- **`BACK` can exit the app.** On a main screen with nothing to pop, `key BACK` (keyevent 4) closes the
  activity/app. To only dismiss the soft keyboard, use `key ESCAPE` (keyevent 111), which hides the IME
  without navigating back.
- **Re-dump after every action.** The tree changes; a stale dump makes you tap the wrong place.
- **Compute bounds-center for taps when `tap-text` is unreliable** (icon buttons, WebView chrome,
  duplicate labels) — read `Bounds` from `dump` and `tap-xy` the midpoint.

## Robustness tips

- Always `wait-text` for the expected element before acting (default 20s) instead of fixed sleeps.
- After `input-text`, verify with `find-text` that the value landed (for non-secure fields).
- Web/ESTS pages load slowly — allow longer `-TimeoutSec` (30–60s) on sign-in pages.
- If an element isn't found, re-`dump` once more (the page may still be rendering) before deciding it's a blocker.
- Keep a screenshot per major step in the run folder for the final report.
