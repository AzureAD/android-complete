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

Notes:
- The **notifications** dialog is an Android 13+ (API 33+) system prompt; on older OS it may appear as an
  in-app prompt or not at all. If a screen from this list doesn't appear, just move to the next — that's why
  you match on text, not position.
- Authenticator sets **`FLAG_SECURE`**, so screenshots of its screens come back **black**. Verify each screen
  and the final home state with `uiautomator dump` (save the **XML** as evidence), not a screenshot — see
  [common-blockers.md → FLAG_SECURE](common-blockers.md#flag_secure-black-screenshots).

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
5. Browser: **"Now pair Authenticator with your account"** — offers the hyperlink **"Pair your account to
   the app by clicking this link."**, plus **"Show QR code"**, **"Other options"**, and a **Next** button.
   → tap the **"Pair your account to the app by clicking this link."** hyperlink. **This is the critical,
   easy-to-miss step — do NOT tap Next, and do NOT tap "Show QR code".** The link fires a `msauth://`
   deep-link back into Authenticator that drives the number-match. (The hyperlink lives inside a WebView and
   often is **not** a tappable node in `uiautomator dump`; if `tap-text` can't find it, use the blue-pixel
   tap — see [common-blockers.md → Single-use pairing / setup links](common-blockers.md#single-use-pairing--setup-links).)
6. Browser: **"Let's try it out"** shows a **number** (e.g. `87`) — *"Enter the number shown in the app to
   approve the sign-in request."* **Read this number** from the Chrome page (`deviceui.ps1 dump` or a
   screenshot — Chrome is **not** FLAG_SECURE, so it captures fine). **Act promptly — the number times out.**
7. Authenticator fires a push **"Approve sign-in?"** for the account. Bring up the number-match screen by
   **any** of these (whichever appears first):
   - pull down the **notification shade**, find **"Approve sign-in?"**, and tap it; **or**
   - **open the Authenticator app** — the number-match screen often pops up on its own; **or**
   - if it doesn't, **pull-to-refresh** in the app to summon it.
8. Number-match screen **"Are you trying to sign in?"** — with an **"Enter number here"** field and
   **YES** / **NO, IT'S NOT ME** / **I CAN'T SEE THE NUMBER**. Type the number from step 6, then tap
   **Yes**. *(If Authenticator's **App Lock** / a biometric gates this screen, satisfy it first — prefer an
   **emulator** so you can inject a fingerprint; see
   [common-blockers.md → fingerprint/App Lock](common-blockers.md#steps-that-need-a-fingerprint--biometric--app-lock).)*
9. Browser returns to **"Authenticator Added"** (green check) — *"This is now your default sign-in method."*
   → tap **Done**. The browser is now signed in to the account.
10. Return to the **Authenticator app** → the account now appears in the home list. **That's the success
    criterion** — verify it via `uiautomator dump` of the account list (FLAG_SECURE, so no screenshot).

Reference screenshots of a successful GlobalMFA add (human-driven) are archived at
`C:\Users\zhipanwang\Pictures\Screenshots\tc2579657` (the "Open browser" screen, the pairing screen, the
number `87`, the number-match dialog, and "Authenticator Added").

## What went wrong before — and the fix

The batch that marked cases **2579657, 2723696, 2916481, 1579402, 2916570, 1579417** as BLOCKED with an
"AAD first-time MFA proof-up wall — same-device pairing infeasible" note was **wrong**. The flow above works
on a single device. Three mistakes, each with its fix:

| Mistake | Symptom logged | Fix |
|---|---|---|
| Treated **"Finish setting up on a web browser / aka.ms/mfasetup"** as needing a *second device* | "same-device pairing infeasible" → BLOCKED | It's the normal path. Tap the app's **Open browser** button; the whole proof-up runs on this device. |
| Navigated to **aka.ms/mfasetup manually** in a separate Chrome tab | "aka.ms/mfasetup blank / BadRequest" | **Don't hand-type that URL.** Use the **Open browser** button the app provides so the deep-link handoff stays intact. If any Chrome page is blank, clear the FRE first — [common-blockers.md → Chrome First Run Experience](common-blockers.md#chrome-first-run-experience-swallows-the-auth-page-blank-webview). |
| On **"Now pair Authenticator with your account"** tapped **Next** (or **Show QR code**) | pairing "didn't return to the app" | Tap the **"Pair your account to the app by clicking this link."** hyperlink — that's the deep-link that drives the number-match. |

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
