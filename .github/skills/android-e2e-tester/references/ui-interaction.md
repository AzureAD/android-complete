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

> **See also:** [common-blockers.md](common-blockers.md) for recurring hiccups and the emulator-vs-device
> decision (fingerprint/App-Lock, number-match, session timeouts, autofill overlay), and
> [run-speed.md](run-speed.md) for the fast driving pattern (poll instead of fixed sleeps; batch per screen).

## Interaction loop

Follow the **fast path** (details + rationale in [run-speed.md](run-speed.md)). Per **screen** (not per tap):
1. `deviceui.ps1 dump` **once** to read what is on screen now, and compute **every** target you need from
   that single XML (don't re-dump between taps on the same screen).
2. Act on those targets: `tap-text` / `tap-desc` / `input-text` / `key` / `finger`.
3. **Verify by the next screen's anchor, not a reflexive re-dump.** Use `tap-text -Then "<anchor>"` so the
   tap and the wait-for-next-screen happen in **one** call, or `wait-text "<anchor>"` after a non-tap action.
   Poll (default 600 ms) — **never** a fixed `Start-Sleep`.
4. Re-`dump` only when you genuinely need new state (a screen actually changed, or you must read a value).
   The tree does change after navigation — just don't pay for a fresh dump after every micro-action.
5. On an **unexpected** screen, `screenshot` to a run file (if it renders) and reason about it before continuing.

**Collapse a whole KNOWN sequence into one `deviceui.ps1 flow` call — this is the largest latency win by far.**
It is *not* about process/adb startup (measured: batching 4 steps saved only **3%** of shell time). It's that
every separate call is a separate **agent turn**, costing a ~25 s median round-trip (p90 60 s) for ~2.5 s of
device work. Crucially that overhead is **fixed, not proportional to decision difficulty** — measured, obvious
navigation steps cost 25.7 s and hard auth/error steps cost 27.5 s. So batching pays off *most* exactly where
the steps are most predictable. Any run of screens whose order you already know — app first-run gates, an
add-account wizard, a settings path — should be **one** call:
```powershell
./scripts/deviceui.ps1 flow -Serial <serial> -Text '[{"tap":"Allow","optional":true},{"tapRes":"...:id/accept"}]'
```
Mark conditionally-appearing screens `"optional": true` so they SKIP instead of failing. Measured: the whole
Authenticator 4-gate first-run preamble runs in **16 s** as one call, versus ~4 separate round-trips before.
Keep one-call-per-screen for the **decision-driven** parts — the part the test is actually about.
See [run-speed.md](run-speed.md#the-dominant-cost-agent-round-trips-not-device-time).

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
| **eSTS email page** | `input-text` the username (bulk first; add `-Clear -CharByChar` only if it didn't land), then `tap-text "Next" -Then "password"` (tap + wait for the next page in one call). |
| **eSTS password page** | `input-text -Secret` the password (bulk first; add `-Clear -CharByChar` if it didn't land), then `tap-text "Sign in"`. Never log the value. Verify by whether the page advances (the field text often doesn't reflect back). See [common-blockers.md](common-blockers.md#chrome-autofill--passkey-overlay-steals-input). |
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

## Transient toasts — you will not find them in a dump

A **toast** (the little floating "…succeeded" message) is the confirmation some flows give you *instead of*
changing the page. Two facts make it easy to miss and then wrongly conclude "nothing happened":

- **`uiautomator dump` never contains a toast.** The dump serialises the **active window's** accessibility
  hierarchy; a toast is a separate, short-lived `TYPE_TOAST` window that isn't part of it. Its absence from
  the XML proves **nothing**.
- **A screenshot taken on the *next* tool call is usually too late.** Toasts live ≈**2 s**
  (`LENGTH_SHORT`) or ≈**3.5 s** (`LENGTH_LONG`), while a tap→(return)→screenshot round-trip costs 2–5 s of
  process + adb startup. So the toast has already faded by the time you capture.

Handle it one of three ways, in order of preference:

1. **Assume success and verify by the real downstream signal** *(this is the reliable one — use it)*. If
   you're certain the control was tapped exactly once, don't make the toast the evidence — verify by what the
   action actually causes: the other app coming to the **foreground**
   (`dumpsys window | mCurrentFocus`, or `deviceui.ps1 current-app`), the next screen's anchor, a new item in
   a list, a log line. A page that legitimately doesn't change is **not** a failed tap.
2. **Tap and capture in ONE shell call** so you never pay the round-trip:
   ```powershell
   ./scripts/deviceui.ps1 tap-xy -X 540 -Y 1526 -Serial <serial>; adb -s <serial> shell screencap -p /sdcard/_t.png; adb -s <serial> pull /sdcard/_t.png <run>\shots\toast.png
   ```
   (Pull the file — never redirect `exec-out` into a PowerShell file, it corrupts the PNG.) **Measured
   reality: even this often misses it** — when the tap also switches apps you capture the *transition
   animation* instead of the toast. Treat a miss as inconclusive, never as failure.
3. **Watching logcat for `enqueueToast` — unreliable, don't depend on it.** In principle the system logs each
   toast as it's posted. **Verified on Android 16 (Samsung): a `logcat` grep for `enqueueToast|ToastRecord|Toast`
   returned ZERO lines for a toast that demonstrably fired.** Modern builds don't log it at the default level
   (and the text is never logged anyway). If you try it, a hit is weak positive evidence; **absence of hits
   proves nothing.** Prefer option 1.

**Never re-tap a control just because the screen looks unchanged.** With single-use links that second tap is
actively harmful — see
[common-blockers.md → Single-use pairing / setup links](common-blockers.md#single-use-pairing--setup-links).

## Driving WebView login fields reliably (hard-won)

The eSTS email/password pages are a **WebView**, not native widgets. Typing into them fails silently in
a few ways — these fixes came from real runs:

- **Tap the field, then confirm focus before typing.** A blind `input-text` goes nowhere if no field is
  focused. After tapping, verify focus by checking that the soft keyboard is up
  (`adb shell dumpsys input_method | Select-String mInputShown,mServedView` — a WebView `mServedView`
  with `mInputShown=true` means the field is focused), then re-`dump` to read the field node.
- **Don't match "the first `EditText`" — Chrome's omnibox IS one.** Chrome's address bar
  (`com.android.chrome:id/url_bar`) is itself an `android.widget.EditText`, so a naive "type into the first
  EditText" rule types the UPN into the **address bar**, not the web email field (you'll see the sign-in
  page unchanged and the URL bar full of your UPN; an `ESCAPE` then re-focuses the omnibox and a double
  `BACK` can exit Chrome entirely). Instead **tap the web field by its on-screen coordinates** and
  **confirm the focused node is the eSTS field** before typing: the email box is `resource-id="i0116"`, the
  password box `i0118`, and Next/Sign-in is `idSIButton9` — verify `i0116`/`i0118` has `focused=true` and
  the omnibox `url_bar` has `focused=false`, then type and re-read the field's `text` to confirm it landed.
- **The visible field is lower than the heading.** On the password page, the big "Enter password" text is
  *not* the input — the actual `EditText` sits below it. Tapping the heading does nothing. Get the real
  node's bounds from the accessibility tree (`uiautomator dump` still exposes the WebView's `EditText`
  node with a `resource-id` like `i0118` even when `FLAG_SECURE` blanks the screenshot) and tap its
  center.
- **Verify the text landed before submitting.** For a password field, re-dump and check the node's
  `text` length or `password="true"` state — do not tap **Sign in** until you've confirmed characters
  were entered, or you'll loop on a blank field.
- **⚠️ The dump is NOT masked — a11y XML of the eSTS password page contains the password in PLAINTEXT.**
  Verified: `i0118` exposes the raw value in `text="…"`. So **treat every `uiautomator dump` XML taken on a
  login page as a secret**: keep it in the run folder (outside the repo), **never** commit it, never paste
  its contents into the transcript, and when you verify, print only a **length or a boolean** — never the
  value.
- **HTML-decode dump text before comparing lengths.** The XML escapes `&`, `<`, `>`, `"` — so a 20-char
  password containing `&` reads as **24** characters raw and looks wrong. Decode first:
  ```powershell
  $landed = [System.Net.WebUtility]::HtmlDecode($nodeText)
  if ($landed.Length -eq $expectedLength) { "OK" } else { "retry with -CharByChar" }   # never print $landed
  ```
- **Type passwords with special characters literally.** `~ ! # $ & * ( )` are shell metacharacters.
  `deviceui.ps1 input-text` escapes them, but if you call adb directly, single-quote the whole string
  **device-side**: `adb shell "input text 'P@ss~!word'"`. Never echo the password into the transcript.
- **Some broker WebViews reject programmatic submit entirely.** With `FLAG_SECURE`, typing may work
  but tapping **Sign in** / pressing Enter doesn't register on an emulator. If a secure broker screen
  won't submit, note it as a **harness limitation** (not a defect) and, if the segment under test is
  already proven, stop there — see [mocking-flights-and-segments.md](mocking-flights-and-segments.md).

## Installing an app from the Play Store mid-flow

Some flows install another app from the Play Store partway through (e.g. a broker or a dependency):

- **Target the real Install button, not the rating chip.** `tap-text "Install"` can hit the "Everyone"
  content-rating label or an unrelated element. Read the button's bounds from `dump` and `tap-xy` its
  center, and dismiss the interstitial **"Got it"** age-rating dialog first if it appears.
- **Installs are slow and interstitials vary.** Poll for completion by package presence
  (`appcontrol.ps1 is-installed -Package <pkg>`) in a loop rather than assuming a fixed delay.
- **A slow install can exceed a feature's time-boxed step.** If the flow has a bounded wait and a slow
  emulator install overruns it, that's a harness-timing artifact, not a defect — note the timing and, if
  the scenario allows, re-run the step with the app already installed.

## Keyboard & navigation gotchas

- **`BACK` can exit the app.** On a main screen with nothing to pop, `key BACK` (keyevent 4) closes the
  activity/app. To only dismiss the soft keyboard, use `key ESCAPE` (keyevent 111), which hides the IME
  without navigating back.
- **Re-dump on screen changes, not after every tap.** The tree changes across navigation, so never reuse a
  dump from a *previous* screen — but you don't need a fresh dump after every micro-action on the *same*
  screen. Verify a navigation by its next-screen anchor (`tap-text -Then` / `wait-text`) instead.
- **Compute bounds-center for taps when `tap-text` is unreliable** (icon buttons, WebView chrome,
  duplicate labels) — read `Bounds` from `dump` and `tap-xy` the midpoint.

## Robustness tips

- Always `wait-text` for the expected element before acting (default 20s) instead of fixed sleeps.
- After `input-text`, verify with `find-text` that the value landed (for non-secure fields).
- Web/ESTS pages load slowly — allow longer `-TimeoutSec` (30–60s) on sign-in pages.
- If an element isn't found, re-`dump` once more (the page may still be rendering) before deciding it's a blocker.
- Keep a screenshot at **milestones** in the run folder for the final report — only on screens that
  actually render (skip FLAG_SECURE pages; they come back black — capture a `uiautomator dump` as evidence
  instead). Screenshotting every micro-step is a needless per-step cost.
