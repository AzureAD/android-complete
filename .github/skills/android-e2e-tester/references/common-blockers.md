# Common Scenarios, Hiccups & Blockers

Recurring friction from real E2E runs, what causes it, and how to get past it — plus **when to switch
from a physical device to an emulator**. Read this before driving an auth flow so you don't lose time
rediscovering a known trap. See also [ui-interaction.md](ui-interaction.md) (screen-by-screen driving)
and [troubleshooting.md](troubleshooting.md) (env/build/install).

Table of contents:
- [Decision: emulator vs physical device](#decision-emulator-vs-physical-device)
- [Steps that need a fingerprint / biometric / App Lock](#steps-that-need-a-fingerprint--biometric--app-lock)
- [Number-match MFA](#number-match-mfa)
- [Session timeouts & SSO resets mid-flow](#session-timeouts--sso-resets-mid-flow)
- [Chrome autofill / passkey overlay steals input](#chrome-autofill--passkey-overlay-steals-input)
- [Chrome First Run Experience swallows the auth page (blank WebView)](#chrome-first-run-experience-swallows-the-auth-page-blank-webview)
- [FLAG_SECURE black screenshots](#flag_secure-black-screenshots)
- [Screenshot corruption via redirection](#screenshot-corruption-via-redirection)
- [Single-use pairing / setup links](#single-use-pairing--setup-links)
- [Stale account state between runs](#stale-account-state-between-runs)
- [Fresh temp user not sign-in-able yet (ESTS propagation lag)](#fresh-temp-user-not-sign-in-able-yet-ests-propagation-lag)
- [Password rejected at sign-in — don't reset it](#password-rejected-at-sign-in--dont-reset-it)
- [Doing it yourself in System Settings](#doing-it-yourself-in-system-settings)
- [Genuine blockers (stop and ask)](#genuine-blockers-stop-and-ask)
- [Quick reference table](#quick-reference-table)

## Decision: emulator vs physical device

The skill can run on either, but some steps are **only automatable on an emulator** because they need an
input you can inject programmatically. Choose up front from the test case's steps:

**Prefer an emulator when the scenario includes any of:**
- A **fingerprint / biometric** prompt you must satisfy (enroll + inject a touch).
- **App Lock** in Microsoft Authenticator (it re-prompts for device credential/biometric).
- A **biometric-gated number-match** approval.
- Anything that needs `adb emu ...` (finger, sensors, GSM, battery) — emulator-only console commands.

**A physical device is fine (or better) when:**
- No biometric is required (pure password + KMSI + token acquisition).
- You're on a **GPU-less host** (Cloud PC / VM / RDP) where the emulator is painfully slow — a physical
  device is much faster there (see [emulator-performance.md](emulator-performance.md)).
- The feature needs real hardware (real FCM push, real SIM) — though real push MFA is still a human step.

> Rule of thumb: **biometric/App-Lock → emulator; everything else → whatever is fastest** (usually a
> connected physical device on a Cloud PC). If you start on a physical device and hit an unavoidable
> fingerprint gate, that's the signal to re-run the biometric segment on an emulator.

> **Caveat — the emulator isn't always available.** On some GPU-less hosts the emulator **won't stay up**:
> the Bluetooth HAL crash-loops and takes `system_server` down repeatedly, so a heavy app (e.g.
> Authenticator) can't be driven even after it boots. See the boot recipe and the instability signal in
> [troubleshooting.md](troubleshooting.md#emulator-wont-start-or-boot). If you've exhausted that recipe and
> it still won't stabilize, then a **biometric-gated step becomes a genuine blocker on this host** — do the
> non-biometric parts on the physical device, mark the biometric step BLOCKED with evidence, and ask the
> user (don't burn the whole run fighting the emulator). Also make sure you install the **right ABI**: use
> the **universal** APK on emulators — a large `arm64-v8a`-only APK can crash install-time dexopt on an
> `x86_64` image (see [Install failures](troubleshooting.md#install-failures)).

## Targeting the right device (multiple devices / same model)

Every attached device gets a **unique adb serial**, independent of model — two identical phones still show up
as two different serials. List them with their model/transport so you can tell them apart:

```powershell
adb devices -l
# emulator-5554        device product:sdk_gphone64_x86_64 model:sdk_gphone64_x86_64 transport_id:21
# 39181FDH2000ABC      device product:husky model:Pixel_8_Pro transport_id:9
# 39181FDH2000XYZ      device product:husky model:Pixel_8_Pro transport_id:12   <- same model, different serial
```

Then **always pass `-Serial <serial>`** to every `deviceui.ps1` call so the action lands on the intended
device. If you omit `-Serial` while more than one device is attached, adb **errors out** ("more than one
device/emulator") instead of guessing — a safe failure, not a wrong-device action, but it will stall the run.
When the human owns several devices, ask which serial to use (or which model), and store each device's PIN
with **`secrets.ps1 set-device-pin`** — it shows a numbered picker (serial + model, so you can tell same-model
units apart) and saves the PIN under that device's own name `devicepin_<serial>`. Then `unlock -Serial <serial>`
**auto-resolves** the matching PIN, so it always targets the correct device without you re-typing a secret name.
Serials are stable for a physical device across reconnects; emulator serials (`emulator-55xx`)
depend on the console port and can change between boots, so re-check `adb devices -l` at the start of a run.

## Steps that need a fingerprint / biometric / App Lock

`adb emu finger touch <id>` injects a fingerprint **only on an emulator**. A physical device has no adb
path to press the sensor — a human must do it. Microsoft Authenticator makes this bite because it
**enables App Lock by default** right after you register an account, so the *next* action (re-opening the
app, approving a browser number-match) is gated behind the device PIN/biometric.

Options, best first:
1. **Run on an emulator.** Enroll once, then inject touches:
   ```powershell
   ./scripts/deviceui.ps1 finger-enroll -Serial <emu>        # sets a PIN + enrolls (idempotent)
   ./scripts/deviceui.ps1 finger -Text 1 -Serial <emu>       # inject a touch when the prompt appears
   ./scripts/deviceui.ps1 finger-status -Serial <emu>        # verify one is enrolled
   ```
2. **Turn App Lock off** so no biometric is required: Authenticator → Settings → toggle **App Lock** off
   (drive it with `tap-text`), then proceed with password-only steps. Do this early if the scenario
   doesn't specifically test App Lock.
3. **Fall back to a device PIN.** If a keyguard/biometric prompt also accepts a PIN, set a known PIN during
   setup and enter it with `deviceui.ps1 unlock` (many biometric prompts have a "Use PIN" path). Seed the PIN
   once into the encrypted store, then let the tool type + **verify** it — it checks the keyguard actually
   cleared and **stops after 3 tries** (`-MaxAttempts`, default 3) so a wrong PIN can't drive a physical
   device into an escalating lockout:
   ```powershell
   ./scripts/secrets.ps1 set-device-pin                                 # picks the device, you paste the PIN (DPAPI-encrypted)
   ./scripts/deviceui.ps1 unlock -Serial <serial>                       # auto-uses devicepin_<serial>; verified; gives up after 3
   ```
   On an **emulator** you can set one deterministically: `adb -s <emu> shell locksettings set-pin 1234`, then
   satisfy biometric prompts with `adb -s <emu> emu finger touch 1`.
4. **Probing an unknown PIN on a physical device — don't brute-force.** `adb shell locksettings verify --old <pin>`
   tests a guess **non-destructively** (it doesn't change anything), but Android's **Gatekeeper throttles
   after ~5 wrong tries** and too many failures can lock the user out of their own device. Try at most a
   couple of obvious values, and if they miss, **stop and ask the user for the PIN** — never loop guesses on
   hardware you don't own.
5. **Physical device + human.** If the scenario *must* run on hardware, pause and ask the user to press the
   sensor, then resume — see [Genuine blockers](#genuine-blockers-stop-and-ask).

## Number-match MFA

Modern MFA shows a **number in the browser** that you must select/enter in Authenticator. It's
automatable **only if** getting into Authenticator isn't biometric-gated:
- If App Lock is on → satisfy the biometric first (see above) → then read the number from the browser
  (`deviceui.ps1 dump` on the Chrome page — it's **not** FLAG_SECURE) and tap the matching tile in
  Authenticator.
- If the number appears as a **push you must approve on a *separate* physical phone** whose Authenticator
  is *already registered* and you don't control (no TOTP seed), it's a genuine blocker — ask the user.

> **Not a blocker: adding a work account to *this* device's Authenticator (first-time proof-up).** When you
> add an AAD Work/School account and the tenant requires first-time MFA registration, Authenticator sends
> you to a browser ("Finish setting up on a web browser / aka.ms/mfasetup") and the number-match bounces
> back to the app **on the same device** — this is fully automatable and must **not** be marked BLOCKED as
> "needs a second phone". The critical, easy-to-miss step is tapping the **"Pair your account to the app by
> clicking this link."** hyperlink (not **Next**, not **Show QR code**) on the *"Now pair Authenticator with
> your account"* screen. Full step-by-step, plus the exact mistakes that caused false BLOCKEDs before, are in
> [authenticator-app.md](authenticator-app.md#aad-workschool-account-add-with-proof-up-number-match--same-device).

## Session timeouts & SSO resets mid-flow

Switching between the browser and Authenticator (or a slow manual segment) can **time out the eSTS web
session**. Symptoms: you return to the browser and it's back on the password page, or SSO silently
re-prompts. Mitigations:
- Keep the biometric/PIN setup done **before** you start the timed web segment so you don't stall on it.
- If you get bounced to sign-in, just re-drive the email/password screens (SSO often re-completes quickly).
- Don't leave the flow parked while doing long setup on the side — provision the account/emulator first,
  then run the web segment in one go.
- **Adding a SECOND/THIRD account in the SAME tenant on the same device? Don't nuke Chrome — drop just the
  web session.** When you tap the app's **Open browser** for the next account, Chrome may re-authenticate the
  **previous** account from its cached web session, so the proof-up wizard shows the wrong identity. Escalate
  in this order and stop at the first that works:
  1. **Try it as-is first.** The handoff carries a `login_hint` for the new UPN, so eSTS often lands on the
     right account with no intervention. Only act if the page actually shows the wrong identity.
  2. **Switch identity on the page** — use the account picker's **"Use another account" / "Sign in with a
     different account"**, or the avatar in the top-right → **Sign out**. Cheapest by far: a couple of taps,
     no state lost.
  3. **Delete cookies only** — Chrome **⋮ → Settings → Privacy and security → Delete browsing data** →
     tick **Cookies and site data** (time range *All time*) → **Delete data**. This drops every web session
     while **keeping** the Chrome profile/FRE state.
  4. **Last resort: `pm clear com.android.chrome`** (`appcontrol.ps1 clear -Package com.android.chrome`).
     ⚠️ This wipes the whole profile, so you pay the **entire First Run Experience again** (Use without an
     account / **Stay signed out** → **No thanks**, plus profile sign-in prompts) — several extra screens and
     a slower, more fragile run. Use it only when the lighter options genuinely fail.

## Chrome autofill / passkey overlay steals input

On eSTS **WebView** email/password fields, Chrome's autofill / **passkey** overlay can intercept a bulk
`input text` — the value lands in the wrong field or is dropped, and eSTS shows "Enter a valid email".
Fix (baked into `deviceui.ps1`):
```powershell
./scripts/deviceui.ps1 input-text -Text $upn -Clear -CharByChar -Serial <serial>
./scripts/deviceui.ps1 input-text -Text $pw  -Clear -CharByChar -Secret -Serial <serial>
```
`-Clear` empties the field, `-CharByChar` types one character at a time (defeats the overlay), `-Secret`
keeps the value out of the transcript. If a "Save password / use passkey" bottom sheet pops, dismiss it
with `key ESCAPE` (not `BACK`, which can exit the app) before typing. **Verify by whether the page
advances**, not by reading the field's `text` — a WebView often doesn't reflect typed content back in the
accessibility tree (the password field `i0118` is an exception and does show a length).

## Chrome First Run Experience swallows the auth page (blank WebView)

**Symptom:** on a **fresh Chrome profile** — right after `pm clear com.android.chrome`, or the first-ever
Chrome launch on a new emulator — an auth handoff (an app's "Sign in" Custom Tab, or a direct
`login.microsoftonline.com` URL) shows a **blank page** and never returns to the app. It looks like a
broken WebView; it is **not**.

**Cause:** Chrome's **First Run Experience** (the "Make Chrome your own / Sign in to get your bookmarks…"
promo + a sync/notifications prompt) intercepts the very first navigation on a fresh profile and **swallows
the auth URL**, so the `msauth://`/app-link handoff never fires. This repeatedly ABORTed emulator AAD
registration cases until the root cause was found — past the FRE, the same emulator renders the real MS
sign-in form (email field `i0116`) normally.

**Fix — dismiss the FRE before any auth handoff** (at case start, and again after ANY Chrome clear):
```powershell
adb -s <serial> shell am start -a android.intent.action.VIEW -d 'https://login.microsoftonline.com' com.android.chrome
./scripts/deviceui.ps1 tap-text -Text "Use without an account" -Serial <serial>   # main FRE button
./scripts/deviceui.ps1 tap-text -Text "No thanks" -Serial <serial>                # sync/turn-on promo, if shown
# also dismiss any "Not now"/"Got it"/notification prompt; Chrome is ready when mCurrentFocus=ChromeTabbedActivity
```
Then re-run the flow — the sign-in page loads. **If a test step clears the browser cache, re-dismiss the FRE
afterward** or the next auth page is blank again. This is most common on emulators (fresh profile) but the
same promo can appear on a freshly-provisioned physical device.

## FLAG_SECURE black screenshots

eSTS login pages, some broker screens, and **Microsoft Authenticator** set `FLAG_SECURE`, so `screencap`
returns an all-black image and some nodes are hidden. Don't rely on screenshots to verify these:
- Verify state with `uiautomator dump` + XML parse instead (the account list, a specific `resource-id`,
  the focused activity via `deviceui.ps1 current-app`). Save the **XML** as your evidence artifact.
- Chrome pages are **not** FLAG_SECURE and screenshot fine — capture those normally.
- Full detail: [ui-interaction.md](ui-interaction.md#the-flag_secure-gotcha).

## Screenshot corruption via redirection

`adb ... screencap -p > file.png` through PowerShell **corrupts the PNG** (newline translation on the
binary stream). Always capture on-device then pull — which is exactly what `deviceui.ps1 screenshot` does:
```powershell
adb shell screencap -p /sdcard/_sc.png ; adb pull /sdcard/_sc.png <out>   # never `screencap -p > file`
```

## Single-use pairing / setup links

> Driving the **AAD account-add proof-up** end-to-end (where this pairing hyperlink comes from)? See the
> full same-device flow in
> [authenticator-app.md](authenticator-app.md#aad-workschool-account-add-with-proof-up-number-match--same-device).
> The pairing link is reached via Authenticator's own **Open browser** button — don't hand-navigate to
> `aka.ms/mfasetup`.

MFA-setup "pair your account to the app" deep-links (e.g. from `aka.ms/mfasetup`) and QR codes are often
**single-use** — a second attempt shows "code already used" or a stale QR. If a pairing step fails on a
retry, **re-generate** the link/QR from the setup page rather than reusing the old one, or provision a
fresh user (`labapi.ps1 create-user`).

**The same-device "Pair your account…click this link" hyperlink is consumed by the FIRST tap — never
double-tap it.** Tapping *"Pair your account to the app by clicking this link"* launches Authenticator in the
**background** and hands it the single-use token. **Confirmation arrives as a near-instant *toast*, and the
Chrome page content deliberately does NOT change** — it stays on "Now pair Authenticator with your account".
You will usually **not** catch that toast (it lives ~2–3.5 s and **never** appears in `uiautomator dump` — see
[ui-interaction.md → Transient toasts](ui-interaction.md#transient-toasts--you-will-not-find-them-in-a-dump)),
and **that is expected — an unchanged page is not a failed tap.** Do **not** tap it again: a second tap
re-opens the now-spent link, Authenticator shows **"QR code already used — You've already used this QR code to
add an account,"** and you can end up with a spurious extra registration for the account.

**Rule:** tap the link **exactly once**, then verify by the **downstream** signal instead of the toast — check
that Authenticator came to the **foreground** (`deviceui.ps1 current-app`) / a push arrived / the number-match
screen is up. Continue in Authenticator (notification shade → *Approve sign-in?*, or open the app, or
pull-to-refresh); never re-tap in Chrome. If you genuinely need a fresh token (it timed out), regenerate it via
the browser wizard's **Back → Next** rather than re-tapping the old link.

**What a second tap actually looks like (verified) — and why it is *confirmation*, not failure.** A re-tap
produces, in order: **"Unable to add the account"** (Cancel), then a fallback **Add account** screen with a
**pre-filled Code + URL and a FINISH button**, and tapping FINISH yields **"QR code already used."** If you see
that chain, the **first** tap already succeeded — go look at the account list rather than retrying.

**⚠️ Correction — a duplicate row is NOT evidence of a double-tap.** Earlier guidance here blamed the extra
account row on tapping the link twice. That is **wrong**: a **single** tap reproducibly leaves **two rows for
the same UPN** (one white, one gray), and the gray one clears itself once setup finishes. See
[authenticator-app.md → Duplicate account rows](authenticator-app.md#duplicate-account-rows-white-vs-gray).

**"Set up Passwordless sign-in requests" can fail with "Passkey — Unknown error. Please try again later."**
**Root cause found: you opened the GRAY duplicate row instead of the WHITE one.** The gray row is not the
official record, and PSI registration against it reproducibly errors. **Fix: go back to the home list, pick the
**white** row (verify by pixel — white `RGB(255,255,255)` vs gray `RGB(189,189,189)`), and run PSI there** —
this was verified to succeed on the same account that had just failed from the gray row. Full recipe:
[authenticator-app.md → Duplicate account rows](authenticator-app.md#duplicate-account-rows-white-vs-gray)
and [→ Enabling Passwordless sign-in](authenticator-app.md#enable-passwordless-sign-in-psi). Only after a
clean run **from the white row** should you record a real **FAIL**.

**Finding a link that isn't in the accessibility tree.** The pairing hyperlink often lives **inside a
WebView** and does **not** appear as a tappable node in `uiautomator dump`, so `tap-text` can't find it.
It's rendered in the usual link **blue**, though, so detect it visually and tap by pixel:
1. `deviceui.ps1 screenshot` the Chrome page (Chrome is **not** FLAG_SECURE, so it captures fine).
2. Scan the PNG for link-blue pixels (Python + PIL) and tap the centroid of the topmost blue run. A blue-ish
   test that works well: `b > 120 and (b - r) > 50 and (b - g) > 30 and r < 120`.
```powershell
python -c "from PIL import Image; im=Image.open(r'<png>').convert('RGB'); w,h=im.size; px=im.load(); \
ys=[(x,y) for y in range(0,h) for x in range(0,w) if (lambda r,g,b: b>120 and b-r>50 and b-g>30 and r<120)(*px[x,y])]; \
print(min(ys,key=lambda p:p[1]) if ys else 'none')"
# then: deviceui.ps1 tap -X <x> -Y <y>   (coords are in device pixels)
```
Remember screenshot coords are **device pixels**; if you scaled the image, scale the tap back up. Once you
know the link's rough y-band on a given page you can re-tap it directly without re-scanning.

## Stale account state between runs

A lab account that already completed first-time MFA setup will **skip** the very registration step you
want to test, making the run look like it "passed" without exercising anything. Reset the state:
```powershell
./scripts/labapi.ps1 reset       -Upn $upn -Operation mfa          # clear MFA registration
./scripts/labapi.ps1 create-user -UserType GlobalMFA               # or just get a brand-new temp user
```
See [lab-api.md](lab-api.md).

**Clean state comes from a fresh install, at the START of a case — not teardown at the end.** `pm clear`
does **not** remove work-account entries from AccountManager or an existing broker/WPJ registration; only
**uninstalling** the app does. So each case's clean slate = **uninstall → reinstall** the app-under-test (and
any broker it uses) as the first step. Do **not** try to delete accounts/registrations after a case — leave
them in place for the next run's uninstall (or a human) to clear. Combined with a **fresh temp account per
case**, this keeps runs independent without brittle post-run cleanup. Device-clock or policy state you changed
mid-run should still be restored so it doesn't leak into the next case.

## Fresh temp user not sign-in-able yet (ESTS propagation lag)

A brand-new temp user from `CreateTempUserID4SLab2` (or any just-created lab account) can take **several
minutes to become consistently sign-in-able**. During that window the sign-in page shows *"This username may
be incorrect. Make sure you typed it correctly."* and the error often **flaps** — it clears for one attempt,
then returns — because the account exists in Graph (you got an `objectId`) but ESTS replication across
front-end nodes lags behind. Observed lag has exceeded **14 minutes**. This is **not** a typo or a real
product failure, so per [Password rejected at sign-in](#password-rejected-at-sign-in--dont-reset-it) do **not**
reset or mutate anything. Options, in order:

1. **Reuse an already-propagated temp user** you created earlier in the same run (they live ~60 min). For flows
   where the account is incidental — e.g. a *browser* SSO test where only the MSAL APK folder differs between
   test points — reusing one propagated user across both points is legitimate and does not weaken the
   assertion. Note the reuse in the report.
2. **Pre-warm**: create the temp user **early** (at the start of the case, before installing/clean-stating apps)
   so replication finishes while you do other setup. Better still, create the *next* case's user before you need it.
3. **Poll politely**: retry sign-in every ~60–90 s (re-type the UPN each time) rather than hammering in a tight
   loop; tight retries just re-hit the same stale node.

**Freshness gate — poll ≤ 3 min, then recreate or reuse a < 30-min-old user.** Don't wait indefinitely on a
stuck new account. If a freshly created temp user is not **consistently** sign-in-able within **3 minutes** of
polite polling, stop waiting on it and either **create another** temp user, **or reuse a previously created temp
user that is still very fresh — under ~30 minutes old** (comfortably inside its ~60-min TTL, and old enough to
have finished replicating; one that already signed in once is the safest reuse). Record the substitution —
which user you abandoned and which you used instead — in the report. This is a time cap on option 1–3 above,
not a replacement for them.

If a case genuinely needs a *distinct fresh* account and none is sign-in-able even after recreating, record the
lag as a **PASS-with-note** (if you completed via reuse) or **BLOCKED** with the exact on-screen error and the
`objectId` as evidence — not a password reset. See [lab-api.md](lab-api.md).

## Password rejected at sign-in — don't reset it

When eSTS shows *"Your account or password is incorrect"*, *"Your password has expired"*, or *"That Microsoft
account doesn't exist"*, the reflex to "fix" it by resetting the password is almost always **wrong**. Resetting
mutates a shared lab account and can mask a real product bug. Treat a rejected password as a *value/identity*
problem first:

1. **Re-fetch the value** — the shared tenant password may have rotated in Key Vault since you cached it:
   `./scripts/labapi.ps1 fetch-password -TestTenant <tenant> -IntoSecret <name>`, then re-type with
   `deviceui.ps1 input-text -SecretRef <name> -Secret`.
2. **Check the identity** — confirm the UPN and tenant match exactly what the test case named (an easy slip is
   using an `@msidlab4` account with the `id4slab2` password, or vice-versa).
3. **Check for a lockout** — a `Locked_…` prefix or repeated failures may mean a prior run locked the account;
   provision a **fresh temp user** instead of hammering it.
4. **Re-type carefully** — special characters can drop in `-CharByChar` mode; verify the field length matches.

Only run `labapi.ps1 reset -Operation password` when the **test case itself** exercises a password-change /
expiry flow. **Never** reset a **shared durable account** (a durable, pre-created account, not a temp
`Locked_…` user) — other cases reuse it. If the value is confirmed correct and sign-in still fails, mark the
run **BLOCKED** with the exact on-screen error rather than changing the account. See [lab-api.md](lab-api.md).

## Doing it yourself in System Settings

Some test steps have **no adb command** — advance the device clock, delete a user certificate, toggle a
system setting, switch language, add/remove an account. That does **not** make them blockers: a human tester
just opens **Settings** and taps through, and so should you. Drive the Settings app with `deviceui.ps1` the
same way as any other screen (`dump` → `tap-text`/`tap-desc` → `input-text`) **before** you ever mark a step
BLOCKED.

Open Settings (or jump straight to a sub-screen via its intent action):
```powershell
adb -s <serial> shell am start -a android.settings.SETTINGS                 # top-level Settings
adb -s <serial> shell am start -a android.settings.DATE_SETTINGS            # Date & time
adb -s <serial> shell am start -a android.settings.SECURITY_SETTINGS        # Security (credential/cert mgmt is under here)
adb -s <serial> shell am start -a android.settings.LOCALE_SETTINGS          # Language
# then: deviceui.ps1 dump / tap-text / tap-desc to drive the screen
```

**Worked example — advance the clock >1h** (a common token-expiry step). Moving the *device wall clock* needs
**no root** (only `adb shell date` does):
1. `am start -a android.settings.DATE_SETTINGS` (Samsung path: Settings → **General management → Date and time**).
2. `dump` to confirm state, then `tap-text "Automatic date and time"` to turn it **off**.
3. `tap-text "Set date"` / `tap-text "Set time"`, set a value >1h ahead, confirm with **Done/OK**.
4. Do the app action you were timing, then restore automatic time afterward if later steps need real time.

**When it *is* still a blocker.** Only fall back to BLOCKED when the surface is genuinely undrivable:
- A **secure/native system dialog** uiautomator can't read or act on — Samsung **Knox** certificate install,
  the **keyguard** credential prompt, a **biometric** sensor, a PIN pad on a FLAG_SECURE screen.
- An action that truly needs **root** — e.g. expiring an app's *internal* monotonic cached-token timer
  (`SystemClock.elapsedRealtime`), which the wall clock doesn't affect; that belongs on a rooted device or
  emulator, not a retail phone. (Advancing the *wall clock* above is **not** this case.)
- A step that would leave **partial/destructive state** on a device you don't own (e.g. a real MDM
  enrollment) with no clean rollback — pause and ask.

Capture the exact screen (XML dump / screenshot) as evidence and mark just that step BLOCKED, per
[Genuine blockers](#genuine-blockers-stop-and-ask).

## Genuine blockers (stop and ask)

These can't be produced by the AI — report them and ask the user (see SKILL "When to ask the user"):
- **Real push-notification MFA** approval on a *separate* physical device with no TOTP seed.
- **SMS / phone-call OTP** to a real number.
- **Hardware** FIDO2 key, NFC, real camera/QR the emulator can't provide.
- **CAPTCHA** / "prove you're human".
- A **fingerprint on a physical device** that the scenario insists must run on that hardware.
- A credential/tenant/CA policy the AI can't provision (try `labapi.ps1 disable-policy` first if it's a
  lab CA policy).

## Quick reference table

| Scenario / step | Automatable? | Where | How / workaround |
|---|---|---|---|
| Fingerprint / biometric prompt | ✅ emulator · ❌ physical | **Emulator** | `finger-enroll` + `finger`; on physical, human presses sensor |
| Authenticator **App Lock** re-prompt | ✅ emulator · ⚠️ physical | **Emulator** | inject biometric, or turn App Lock **off**, or use device PIN |
| Number-match MFA (same device) | ✅ if not biometric-gated | Either | read number from Chrome dump → tap tile in Authenticator |
| AAD **first-time proof-up** adding a work account (sends you to a browser) | ✅ **same device** | Either | **Not** a "second phone" blocker — tap the app's **Open browser**, then the **"Pair your account…link"** hyperlink; [authenticator-app.md](authenticator-app.md#aad-workschool-account-add-with-proof-up-number-match--same-device) |
| Real push MFA on another *already-registered* phone | ❌ | — | **Blocker** — ask the user |
| Authenticator first-run gates (fresh install) | ✅ | Either | 4 screens → Allow · Accept · Continue · **Skip** (upper-right); [authenticator-app.md](authenticator-app.md#first-run-flow-fresh-install--home) |
| eSTS password typing | ✅ | Either | `input-text -Clear -CharByChar -Secret` |
| Autofill/passkey overlay | ✅ | Either | char-by-char + `key ESCAPE` to dismiss sheet |
| Auth page blank on fresh Chrome profile (post `pm clear` / new emulator) | ✅ | Either | **Chrome First Run Experience** — dismiss "Use without an account" + "No thanks" before the auth handoff |
| Verify state on FLAG_SECURE screen | ✅ | Either | `uiautomator dump` + XML (screenshot is black) |
| Session timed out mid-flow | ✅ | Either | re-drive sign-in; set up biometric/PIN before timed segment |
| Single-use pairing link reused | ✅ | Either | tap the same-device "click this link" hyperlink **exactly once**; a 2nd tap → "Unable to add the account" → prefilled Code/URL screen → **"QR code already used"** — that chain *proves the first tap worked*, so check the account list instead of retrying |
| Pairing link inside a WebView (not in uiautomator) | ✅ | Either | screenshot → PIL blue-pixel scan → `tap-xy -X -Y` |
| UPN typed into Chrome's **address bar**, not the login field | ✅ | Either | `url_bar` is an EditText too — tap the web field by coords, confirm focus is `i0116`/`i0118` (omnibox `focused=false`) before typing |
| 2nd account, **same tenant**, browser shows the wrong (previous) account | ✅ | Either | use the page's own **"Use another account"** first — don't `pm clear` Chrome (that re-triggers the FRE) |
| **Two rows for the same UPN** (one white, one gray) | ✅ | Either | **Normal after a single tap.** Gray = not official; act on the **white** row (pixel `RGB(255,255,255)` vs `RGB(189,189,189)`); gray self-clears after setup — [authenticator-app.md](authenticator-app.md#duplicate-account-rows-white-vs-gray) |
| "Set up Passwordless" → **"Passkey — Unknown error"** | ✅ | Either | **You opened the GRAY row.** Redo PSI from the **white** row — verified to succeed on the same account; real FAIL only if it fails from the white row |
| PSI setup hits **"Scan your fingerprint"** on a physical device | ✅ | Either | tap the **"Use PIN"** fallback → OEM `lockPassword` field → `input-text -SecretRef devicepin_<serial>` — **no emulator needed** |
| Browser wizard dead-ends: *"We're sorry, we ran into a problem… choose Next to try again"* | ✅ | Either | the **app-side add may already have succeeded** — check the account list; the wizard isn't required for PSI |
| Modal *"Your sign-in information may have changed"* steals focus | ✅ | Either | it may name a **different/expired** account — read the UPN, tap **Cancel** (Continue re-auths the wrong account) |
| `screencap` shows a stale/other app on a **foldable** | ✅ | Either | don't pass `-d 0`; use plain `screencap -p` and cross-check against `mCurrentFocus`/dump `package=` |
| Emulator won't stay up (BT-HAL / system_server loop) | ⚠️ host-dependent | — | try boot recipe; if unstable, biometric step is a **blocker** on this host |
| Wrong-ABI APK on emulator (arm64 on x86_64) | ✅ | Emulator | install the **universal** APK; keep arm64 APK for physical |
| Stale MFA already registered | ✅ | Either | `labapi.ps1 reset -Operation mfa` or new temp user |
| Fresh temp user "username may be incorrect" (just created) | ✅ | Either | ESTS propagation lag (can be >14 min) — **don't** reset; poll ≤3 min then **recreate** or **reuse a <30-min-old** temp user; pre-warm early |
| System-settings step (advance clock, delete cert, toggle) | ✅ drive Settings UI · ❌ only if secure/root | Either | `am start -a android.settings.*` → `dump`/`tap-text`; block only on Knox/keyguard/root |
| Clean slate for a new case | ✅ | Either | **uninstall+reinstall** app-under-test (`pm clear` keeps work accounts/registration); don't tear down after |
| SMS / phone / hardware-key / CAPTCHA | ❌ | — | **Blocker** — ask the user |
| Multiple devices / same model attached | ✅ | Either | unique adb serial each; always pass `-Serial`; `adb devices -l` to pick; `secrets.ps1 set-device-pin` to store per-device PIN |
| Unlock the lock screen with a PIN | ✅ | Either | `secrets.ps1 set-device-pin` once, then `unlock -Serial <s>` (auto-uses saved PIN) — verifies + **stops after 3** tries |
| "Password incorrect / expired" at sign-in | ✅ | Either | **don't** `reset -Operation password`; re-`fetch-password` (may have rotated), re-check UPN/tenant, use a fresh temp user; reset **only** if the case says so; never reset shared durable accounts |
