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
- [FLAG_SECURE black screenshots](#flag_secure-black-screenshots)
- [Screenshot corruption via redirection](#screenshot-corruption-via-redirection)
- [Single-use pairing / setup links](#single-use-pairing--setup-links)
- [Stale account state between runs](#stale-account-state-between-runs)
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
under its **own** secret name (e.g. `devicepin_pixel8`, `devicepin_pixel8pro`) so `unlock -SecretRef` targets
the correct one. Serials are stable for a physical device across reconnects; emulator serials (`emulator-55xx`)
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
   ./scripts/secrets.ps1 set -Name devicepin_pixel8                     # you paste the PIN, stored DPAPI-encrypted
   ./scripts/deviceui.ps1 unlock -SecretRef devicepin_pixel8 -Serial <serial>   # verified; gives up after 3
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
- If the number appears as a **push you must approve on a *separate* physical phone**, it's a genuine
  blocker (no TOTP seed) — ask the user.

## Session timeouts & SSO resets mid-flow

Switching between the browser and Authenticator (or a slow manual segment) can **time out the eSTS web
session**. Symptoms: you return to the browser and it's back on the password page, or SSO silently
re-prompts. Mitigations:
- Keep the biometric/PIN setup done **before** you start the timed web segment so you don't stall on it.
- If you get bounced to sign-in, just re-drive the email/password screens (SSO often re-completes quickly).
- Don't leave the flow parked while doing long setup on the side — provision the account/emulator first,
  then run the web segment in one go.

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

MFA-setup "pair your account to the app" deep-links (e.g. from `aka.ms/mfasetup`) and QR codes are often
**single-use** — a second attempt shows "code already used" or a stale QR. If a pairing step fails on a
retry, **re-generate** the link/QR from the setup page rather than reusing the old one, or provision a
fresh user (`labapi.ps1 create-user`).

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
| Real push MFA on another phone | ❌ | — | **Blocker** — ask the user |
| eSTS password typing | ✅ | Either | `input-text -Clear -CharByChar -Secret` |
| Autofill/passkey overlay | ✅ | Either | char-by-char + `key ESCAPE` to dismiss sheet |
| Verify state on FLAG_SECURE screen | ✅ | Either | `uiautomator dump` + XML (screenshot is black) |
| Session timed out mid-flow | ✅ | Either | re-drive sign-in; set up biometric/PIN before timed segment |
| Single-use pairing link reused | ✅ | Either | re-generate the link / fresh temp user |
| Pairing link inside a WebView (not in uiautomator) | ✅ | Either | screenshot → PIL blue-pixel scan → `tap -X -Y` |
| Emulator won't stay up (BT-HAL / system_server loop) | ⚠️ host-dependent | — | try boot recipe; if unstable, biometric step is a **blocker** on this host |
| Wrong-ABI APK on emulator (arm64 on x86_64) | ✅ | Emulator | install the **universal** APK; keep arm64 APK for physical |
| Stale MFA already registered | ✅ | Either | `labapi.ps1 reset -Operation mfa` or new temp user |
| SMS / phone / hardware-key / CAPTCHA | ❌ | — | **Blocker** — ask the user |
| Multiple devices / same model attached | ✅ | Either | unique adb serial each; always pass `-Serial`; `adb devices -l` to pick |
| Unlock the lock screen with a PIN | ✅ | Either | `unlock -SecretRef <name> -Serial <s>` — verifies + **stops after 3** tries |
