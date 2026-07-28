# Microsoft Authenticator — first-run flow & AAD account-add (proof-up / number-match)

Microsoft Authenticator (`com.azure.authenticator`) is one of the most common apps-under-test in this
skill. Two of its flows trip up automation in ways that are **not** obvious from the screen, and both cost
real runs before they were understood:

1. the **first-run flow** — a fresh install shows **4 gates before the home screen**; miss one and you
   never reach the account list, or you accidentally start an account-add from the upsell; and
2. the **AAD Work/School account-add with first-time MFA proof-up (number-match)** — which is fully
   automatable on **one device** but was repeatedly (and wrongly) marked BLOCKED as "needs a second phone".

Read this before driving any Authenticator scenario. Match screens by their **on-screen text**, not a fixed
index — order varies slightly by OS/app version. See also
[common-blockers.md](common-blockers.md) (App Lock / number-match / blue-pixel tap / blank Chrome / FLAG_SECURE)
and [app-and-module-map.md](app-and-module-map.md#known-provided-apks) (APK filenames + package).

- [First-run flow (fresh install → home)](#first-run-flow-fresh-install--home)
- [Home screen & adding an account](#home-screen--adding-an-account)
- [AAD Work/School account-add with proof-up (number-match) — SAME DEVICE](#aad-workschool-account-add-with-proof-up-number-match--same-device)
- [App Lock auto-enables when a device PIN exists](#app-lock-auto-enables-when-a-device-pin-exists)
- [What went wrong before — and the fix](#what-went-wrong-before--and-the-fix)
- [Cross-references](#cross-references)

## First-run flow (fresh install → home)

A freshly installed Authenticator (Phase 3 uninstall+reinstall gives you exactly this) shows **4 screens
before the home screen**. Drive them in order — each is a `deviceui.ps1 dump` → `tap-text`:

1. **Enable notifications** — the Android runtime-permission dialog ("Allow *Authenticator* to send you
   notifications?") → tap **Allow**. **Do not deny/skip:** the proof-up's number-match arrives as a *push*,
   so notifications must be on.
2. **Privacy agreement** → tap **Accept**. (No need to open the "Microsoft privacy statement" link.)
3. **"Help us improve Microsoft Authenticator"** (telemetry opt-in) → leave the checkbox **unchecked**,
   tap **Continue**.
4. **"Peace of mind for your digital life"** (an upsell to add an account) → **do not** add an account
   here; tap **Skip** in the **upper-right corner** → lands on the **home screen**.

```powershell
# fast path: one dump per screen, tap by text, verify by the next screen's anchor
./scripts/deviceui.ps1 tap-text -Text "Allow"    -Then "Accept"   -Serial <serial>   # notifications → privacy
./scripts/deviceui.ps1 tap-text -Text "Accept"   -Then "Continue" -Serial <serial>   # privacy → telemetry
./scripts/deviceui.ps1 tap-text -Text "Continue" -Then "Skip"     -Serial <serial>   # telemetry → upsell
./scripts/deviceui.ps1 tap-text -Text "Skip"     -Serial <serial>                    # upsell → home
```

**Prefer the resource IDs when a text tap misses.** Text is locale- and version-fragile; these ids are the
ones the team's own UIAutomator suite drives, so they're the canonical, stable handles
([see the automated suite](existing-ui-automation.md)):

| Screen | Resource ID |
|---|---|
| Notifications permission — negative button | `android:id/button2` |
| Privacy agreement — **Accept** | `com.azure.authenticator:id/privacy_consent_button` |
| "Help us improve" — **Continue** | `com.azure.authenticator:id/privacy_consent_continue_button` |
| "Peace of mind…" upsell — **Skip** | `com.azure.authenticator:id/frx_skip_button` |
| Home — **Add account** (zero accounts) | `com.azure.authenticator:id/zero_accounts_add_account_button` |
| Home — account list | `com.azure.authenticator:id/account_list` |
| Toolbar — overflow (⋮) | `com.azure.authenticator:id/menu_overflow` |
| Overflow → **Add account** | `com.azure.authenticator:id/menu_item_add_account` |
| Overflow → **Settings** | `com.azure.authenticator:id/menu_item_settings` |
| Overflow → **Check for notifications** | `com.azure.authenticator:id/menu_check_for_notifications` |
| Number-match — code input | `com.azure.authenticator:id/auth_enter_correct_num_text_input` |
| Number-match — confirm | `com.azure.authenticator:id/positiveButton` |

The **"+" (add account)** button in the top-right is best matched by **content-description containing
"Add"** rather than text (it's an icon). The overflow *menu items* are a **Compose popup** whose resource
ids are unreliable once open — match those by text.

Notes:
- The **notifications** dialog is an Android 13+ (API 33+) system prompt; on older OS it may appear as an
  in-app prompt or not at all. If a screen from this list doesn't appear, just move to the next — that's why
  you match on text, not position.
- **Screenshots: mostly fine — don't assume they're all black.** Verified on a physical Galaxy (Android 16):
  the **account list / home screen renders normally** in `screencap`, and you *need* that because the
  white-vs-gray row distinction below is **only** visible in pixels. Some inner screens are still
  `FLAG_SECURE` and come back black — so **capture both**: a `screencap` *and* a `uiautomator dump` (XML) at
  each milestone, and if the PNG is black fall back to the XML. Always **cross-check the screenshot against
  the dump's `package=`** before trusting it (see the multi-display trap below).
  See [common-blockers.md → FLAG_SECURE](common-blockers.md#flag_secure-black-screenshots).
- **Foldables/multi-display: never pass `-d 0` to `screencap`.** On a Galaxy Z Flip (two HWC displays)
  `adb shell screencap -p -d 0` returned a **stale frame of a different app** (Chrome) while the device was
  actually showing Authenticator. Plain `adb shell screencap -p` was correct. A screenshot that disagrees
  with `dumpsys window | mCurrentFocus` is a **stale capture**, not a real screen — re-capture without `-d`.

## Home screen & adding an account

The **home screen** is the account list. When it's empty it shows a big **"Add account"** button; there is
also a **"+"** in the **upper-right corner** that does the same thing. **Always add accounts from home** via
**"+"** / **"Add account"** — never from the first-run "Peace of mind…" upsell.

Add-account chooser sequence:
1. **"+"** / **"Add account"** →
2. **"What kind of account are you adding?"** → **Personal account** / **Work or school account** /
   **Other (Google, Facebook, etc.)**. For AAD pick **Work or school account**; for MSA pick
   **Personal account**.
3. For Work or school it then offers **Sign in** / **Scan a QR code** → pick **Sign in** (QR needs a second
   screen — see the QR note in [common-blockers.md](common-blockers.md)).

**If the device already has work accounts, the broker's account chooser comes first.** Tapping **Sign in**
can open `com.microsoft.identity.common…BrokerAuthorizationActivity` showing **"Pick an account"** with the
existing accounts listed. To add a *new* one, tap **"Use another account"** — otherwise you silently re-add
an account that's already there.

<a id="duplicate-account-rows-white-vs-gray"></a>
## Duplicate account rows — white = official, gray = ignore

**After a Work/School account is added but *before* its setup is finished, the home list can show TWO rows
for the SAME UPN**: one with a **white** background and one with a **gray** background. This is **normal and
happens after a SINGLE tap of the pairing link** — it is *not* caused by tapping the link twice, and it is
not an error.

- **The GRAY row is not the official record. Always act on the WHITE row** — open it, enable Passwordless
  sign-in on it, read its OTP from it.
- **Acting on the gray row is what produces a bogus `Passkey — Unknown error`** when you try to enable
  Passwordless sign-in. That error means "wrong row", not "the feature is broken".
- **The gray row disappears by itself** once the account is fully set up (verified: the list went 4 rows →
  3 rows the moment Passwordless sign-in finished on the white row). So don't try to delete it.

**How to tell them apart — you cannot do it from the XML.** Both rows expose *identical*
`resource-id="com.azure.authenticator:id/account_chevron_right_icon"` nodes with the **same**
`content-desc="Open account details for <upn>"`; only `bounds` differ. The background colour is **not** in
the accessibility tree. Two reliable options, in order:

1. **Sample the pixel (authoritative).** The list is not FLAG_SECURE, so screencap it and read the row
   background just left of the chevron. Verified values on a 1080-wide device: **white/official =
   `RGB(255,255,255)`**, **gray/ignore = `RGB(189,189,189)`**.
   ```powershell
   Add-Type -AssemblyName System.Drawing
   $bmp = [System.Drawing.Bitmap]::FromFile("$run\shots\list.png")
   # for each chevron node: bounds="[960,916][1032,988]" -> x = left-60, y = vertical centre
   $p = $bmp.GetPixel(900, 952)
   $isOfficial = ($p.R -ge 245 -and $p.G -ge 245 -and $p.B -ge 245)   # white => official row
   $bmp.Dispose()
   ```
2. **Fallback heuristic** (if the screenshot is unusable): when a UPN appears twice, the **upper/earlier**
   occurrence is the white/official one and the gray duplicate sorts **last**.

## AAD Work/School account-add with proof-up (number-match) — SAME DEVICE

When you add a Work/School account whose tenant requires first-time MFA registration, Authenticator can't
finish the pairing in-app — it hands you to a browser to "prove up". **This entire flow runs on the one
device you're already on.** The browser is launched *by Authenticator*, and a deep-link bounces the
number-match back into the app. Drive it end-to-end:

1. Home → **"+"** → **Work or school account** → **Sign in**.
2. On the Microsoft sign-in page enter **UPN** then **password** and sign in. (Type into these WebView fields
   with `-Clear -CharByChar`; password via `-SecretRef` — see
   [common-blockers.md → Chrome autofill overlay](common-blockers.md#chrome-autofill--passkey-overlay-steals-input).)
3. Authenticator shows **"Finish setting up on a web browser"** — *"To set up Microsoft Authenticator,
   you'll need to go to aka.ms/mfasetup on a web browser."* with **Cancel** / **Open browser**.
   → tap **Open browser**. **This is the normal path — NOT a "second device" blocker.** It opens the
   proof-up in a browser on **this** device.
4. Browser: **"Let's keep your account secure"** → tap **Next**.
4b. Browser: **"Install Microsoft Authenticator"** may appear as an *extra* screen here (it isn't in every
   tenant's flow) → tap **Next**. Don't treat it as a wrong turn.
5. Browser: **"Now pair Authenticator with your account"** — offers the hyperlink **"Pair your account to
   the app by clicking this link."**, plus **"Show QR code"**, **"Other options"**, and a **Next** button.
   → tap the **"Pair your account to the app by clicking this link."** hyperlink **exactly once**. **This is
   the critical, easy-to-miss step — do NOT tap Next, and do NOT tap "Show QR code".** The link fires a
   `msauth://` deep-link back into Authenticator that drives the number-match. (The hyperlink lives inside a
   WebView and often is **not** a tappable node in `uiautomator dump`; if `tap-text` can't find it, use the
   blue-pixel tap — see [common-blockers.md → Single-use pairing / setup links](common-blockers.md#single-use-pairing--setup-links).)
5b. **Confirm the tap by the app coming to the FOREGROUND, not by the page changing.** A success **toast**
   fires and the Chrome page **deliberately stays on the same content** — an unchanged page is **not** a
   missed tap. The authoritative signal is `dumpsys window | mCurrentFocus` flipping to
   `com.azure.authenticator/...MainActivity`. **Never tap the link a second time** — it is single-use, and a
   re-tap yields **"Unable to add the account"**, then a fallback **Add account** screen (pre-filled *Code* +
   *URL* + **FINISH**) whose FINISH fails with **"QR code already used"**. Those two errors are *proof the
   first tap worked* — treat them as confirmation, not as a new failure.
6. Browser: **"Let's try it out"** shows a **number** (e.g. `87`) — *"Enter the number shown in the app to
   approve the sign-in request."* **Read this number** from the Chrome page (`deviceui.ps1 dump` or a
   screenshot — Chrome is **not** FLAG_SECURE, so it captures fine). **Act promptly — the number times out.**
   **The browser wizard may instead dead-end** on the pairing page with *"We're sorry, we ran into a problem.
   Please choose 'Next' to try again."* **even though the app-side add already succeeded.** Don't fight it and
   don't re-tap the link: **go check the app's account list first** — if the account is there (see the
   white/gray rule above), the add is done and you can finish setup entirely in-app (next section). The
   browser wizard is **not** required to enable Passwordless sign-in.
7. Authenticator fires a push **"Approve sign-in?"** for the account. **Give it ~10 s before you look** —
   the FCM push lands about 2 s after the trigger as a **heads-up banner that covers Chrome's URL bar**, then
   auto-collapses after ~5 s. Reading the screen too early gets you the banner instead of the page (or an
   obscured number); reading it too late loses the banner. Then bring up the number-match screen by **any**
   of these (whichever appears first):
   - pull down the **notification shade**, find **"Approve sign-in?"**, and tap it; **or**
   - **open the Authenticator app** — the number-match screen often pops up on its own; **or**
   - **Check for notifications** from the overflow menu (⋮ `menu_overflow` →
     `menu_check_for_notifications`) — a **deterministic** poll, and preferable to the gesture below; **or**
   - if it still doesn't show, **pull-to-refresh** in the app to summon it.
8. Number-match screen **"Are you trying to sign in?"** (some versions title it **"Enter the number shown to
   sign in"**). **Two variants exist — check which one you got:**
   - **Number-entry variant** — an **"Enter number here"** field plus **YES** / **NO, IT'S NOT ME** /
     **I CAN'T SEE THE NUMBER**: type the number from step 6, then tap **Yes**.
   - **Simple approve variant** (verified on Android 16) — **no** number field, just
     `positiveButton` **Yes** / `negativeButton` **No, not me** / `cancelButton` **Cancel**: just tap **Yes**.
     Don't stall looking for a field that isn't there.

   *(If Authenticator's **App Lock** / a biometric gates this screen, satisfy it first — prefer an
   **emulator** so you can inject a fingerprint; see
   [common-blockers.md → fingerprint/App Lock](common-blockers.md#steps-that-need-a-fingerprint--biometric--app-lock).)*
9. Browser returns to **"Authenticator Added"** (green check) — *"This is now your default sign-in method."*
   → tap **Done**. The browser is now signed in to the account.
10. Return to the **Authenticator app** → the account now appears in the home list. **That's the success
    criterion** — verify it with **both** a `screencap` (the list renders; you need the pixels for the
    white/gray check) and a `uiautomator dump` of the account list.

Reference screenshots of a successful GlobalMFA add (human-driven) are archived at
`C:\Users\zhipanwang\Pictures\Screenshots\tc2579657` (the "Open browser" screen, the pairing screen, the
number `87`, the number-match dialog, and "Authenticator Added").

<a id="enable-passwordless-sign-in-psi"></a>
## Enabling Passwordless sign-in (PSI) on an account

Do this **on the white row** (see [duplicate rows](#duplicate-account-rows-white-vs-gray)). Verified
end-to-end on a physical Galaxy running Android 16:

1. Home list → tap the **white** row's chevron → **account detail**. A healthy record shows
   **Sign-in notifications**, a live **One-time password code**, and the buttons **Create a passkey** and
   **Set up Passwordless sign-in requests**.
2. Tap **"Set up Passwordless sign-in requests"**. This starts a **re-auth**, which raises a number-match
   challenge — the challenge number is displayed on the *re-auth page itself* (`Approve sign in request` +
   the number).
3. A push **"Approve sign-in?"** lands in the notification shade for that UPN. Expand the shade
   (`adb shell cmd statusbar expand-notifications`) and tap it.
4. **"Are you trying to sign in?"** → tap **Yes** (see the two variants in step 8 above).
5. **"Let's secure your account — Passwordless sign-in requests — Sign in without a password."** → tap
   **Continue**.
6. A **biometric prompt** appears: *"Scan your fingerprint."* with a **"Use PIN"** fallback. On a **physical**
   device you cannot inject a fingerprint — **tap "Use PIN"**. That opens the OEM credential screen
   (Samsung: `com.samsung.android.biometrics.app.setting`, field
   `…:id/lockPassword`, already focused) → type the stored device PIN with
   `deviceui.ps1 input-text -SecretRef devicepin_<serial>` (never echoed) → tap **Continue**.
   **This "Use PIN" fallback is why PSI does *not* require an emulator.**
7. **"Account added — You can now sign in or verify using the following methods: Passwordless sign-in
   requests"** → tap **Done**. 
8. Back on the home list the account now shows the **blue left-edge bar** (= PSI enabled), and any **gray
   duplicate row for that UPN disappears**.

**If you get `Passkey — Unknown error` here, you almost certainly opened the GRAY row.** Go back and repeat
on the white one.

<a id="app-lock-auto-enables-when-a-device-pin-exists"></a>
## App Lock auto-enables when a device PIN exists

**If the device has a screen lock (PIN/pattern/biometric), Authenticator turns App Lock ON by itself** as
soon as an account lands — it shows an *"App Lock enabled"* popup (dismiss with **OK**). Nobody asked for it,
and it then gates **every** return to the app behind a biometric/PIN prompt. That is a silent killer for any
flow that switches away and back (browser → app → browser), which is exactly what proof-up, number-match and
PSI all do.

**So unless the case is specifically testing App Lock, turn it off immediately after the account is added:**

1. Dismiss the *"App Lock enabled"* popup → **OK**.
2. Overflow (⋮ `menu_overflow`) → **Settings** (`menu_item_settings`) → toggle **App Lock** off.
3. Return to the account list and continue the flow.

This is what the team's own automated suite does as a fixed step in its account-add flow — it is not
optional hygiene, it is the difference between a flow that switches apps cleanly and one that stalls on a
biometric prompt you may not be able to satisfy on a physical device. Combine with the
[fingerprint/App Lock decision](common-blockers.md#steps-that-need-a-fingerprint--biometric--app-lock):
if a case *does* require App Lock, prefer an emulator so you can inject a fingerprint.

Corollary: if you set a device PIN yourself (via `locksettings set-pin`, see
[common-blockers.md](common-blockers.md#steps-that-need-a-fingerprint--biometric--app-lock)), **expect App
Lock to switch on** the moment an account is added — plan the disable step into the flow rather than being
surprised by it mid-run.

<a id="sign-in-information-may-have-changed-hijack"></a>
## "Your sign-in information may have changed" can hijack the run

Authenticator may raise a modal — *"Your sign-in information may have changed. You will need to log in again
to your account."* with **Cancel** / **Continue** — for **any** account it holds, including an **old, expired
temp user unrelated to the account you're setting up**. It steals the foreground and can intercept the
pairing deep-link, which then makes the *browser* wizard fail with "We're sorry, we ran into a problem".

- **Read the dialog's account before acting.** Tapping **Continue** launches a full re-auth **for that other
  account** (`BrokerAuthorizationActivity` pre-filled with *its* UPN) and derails your run.
- Unless that account is the one under test, tap **Cancel** (or back out) and carry on.
- Expect this whenever the device carries **temp lab users older than their ~60-min TTL** — their refresh
  tokens are dead, so the app keeps asking.

## What went wrong before — and the fix

The batch that marked cases **2579657, 2723696, 2916481, 1579402, 2916570, 1579417** as BLOCKED with an
"AAD first-time MFA proof-up wall — same-device pairing infeasible" note was **wrong**. The flow above works
on a single device. Three mistakes, each with its fix:

| Mistake | Symptom logged | Fix |
|---|---|---|
| Treated **"Finish setting up on a web browser / aka.ms/mfasetup"** as needing a *second device* | "same-device pairing infeasible" → BLOCKED | It's the normal path. Tap the app's **Open browser** button; the whole proof-up runs on this device. |
| Navigated to **aka.ms/mfasetup manually** in a separate Chrome tab | "aka.ms/mfasetup blank / BadRequest" | **Don't hand-type that URL.** Use the **Open browser** button the app provides so the deep-link handoff stays intact. If any Chrome page is blank, clear the FRE first — [common-blockers.md → Chrome First Run Experience](common-blockers.md#chrome-first-run-experience-swallows-the-auth-page-blank-webview). |
| On **"Now pair Authenticator with your account"** tapped **Next** (or **Show QR code**) | pairing "didn't return to the app" | Tap the **"Pair your account to the app by clicking this link."** hyperlink — that's the deep-link that drives the number-match. |
| Treated the **unchanged Chrome page** after tapping the pair link as a failed tap | re-tapped → "Unable to add the account" / "QR code already used" → concluded pairing was broken | The page is *supposed* to stay put; confirmation is a toast you'll miss. Verify by **Authenticator coming to the foreground**, then continue in the app. |
| Ran **"Set up Passwordless sign-in requests"** on the **gray** duplicate row | "Passkey — Unknown error. Please try again later." → PARTIAL | Use the **white** row (pixel `RGB(255,255,255)`). Verified: PSI succeeded immediately on the white row for the *same* account. [Duplicate rows](#duplicate-account-rows-white-vs-gray) |
| Kept retrying the **browser wizard** after it errored | stuck in a "We're sorry, we ran into a problem" loop | The app-side add had already succeeded. Check the account list; finish setup **in-app** — the wizard isn't required for PSI. |
| `pm clear`-ed **Chrome** to drop a previous account's SSO | re-triggered Chrome's First Run Experience + profile prompts, adding many extra steps | Use the sign-in page's own **"Use another account"** — no data clearing needed. |

**Only** escalate an AAD proof-up as a genuine blocker when the number-match screen is gated behind a
biometric you can't inject (App Lock on a **physical** device — move that segment to an emulator), or the
tenant/CA genuinely can't be satisfied. "It sent me to a browser" is not a blocker.

## Cross-references

- **Number-match, App Lock, biometric gating** → [common-blockers.md](common-blockers.md#number-match-mfa)
- **Pairing hyperlink not in the accessibility tree (blue-pixel tap)** →
  [common-blockers.md → Single-use pairing / setup links](common-blockers.md#single-use-pairing--setup-links)
- **Blank Chrome page on first navigation** →
  [common-blockers.md → Chrome First Run Experience](common-blockers.md#chrome-first-run-experience-swallows-the-auth-page-blank-webview)
- **Authenticator screenshots are black (FLAG_SECURE) — use `uiautomator dump`** →
  [common-blockers.md → FLAG_SECURE](common-blockers.md#flag_secure-black-screenshots)
- **APK filenames + package + module** →
  [app-and-module-map.md](app-and-module-map.md#known-provided-apks)
