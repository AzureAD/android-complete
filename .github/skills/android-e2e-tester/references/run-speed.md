# Run-Speed Analysis — why steps are slow and how to shorten them

An analysis of where wall-clock time goes in a UI-driven run, using the AAD MFA sign-in run
(`20260721_184540`) as the reference, plus concrete speed-ups. This is guidance, not a hard gate — but the
default driving pattern should follow the "fast path" below.

Table of contents:
- [Measured timeline](#measured-timeline)
- [Where the time actually goes](#where-the-time-actually-goes)
- [Root causes](#root-causes)
- [Speed-ups (what to do differently)](#speed-ups-what-to-do-differently)
- [Fast-path recipe](#fast-path-recipe)
- [Expected savings](#expected-savings)

## Measured timeline

Timestamps are screenshot capture times; the gap is the time that segment took.

| Segment | From → To | Elapsed | What happened |
|---|---|---:|---|
| App first-run | — → 01_firstrun 18:48:16 | (setup) | launch + accept privacy |
| Add account → sign-in page | 01 → 02_chrome 18:53:30 | **5m14s** | navigate menus, load eSTS WebView, type UPN |
| Enter password → post-sign-in | 02 → 03_after_signin 18:57:35 | **4m05s** | type password (char-by-char + retries), eSTS round-trips |
| MFA wizard | 03 → 04_mfa_wizard 18:58:11 | 36s | "More info required" wizard |
| Pairing | 04 → 05_pairing 18:58:54 | 43s | pair account |
| MFA challenge | 05 → 07_mfa_challenge 19:03:33 | **4m39s** | switch to browser, re-auth, number-match appears |
| Number match | 07 → 08_number_match 19:04:55 | 1m22s | read number, attempt approval (blocked by App Lock) |

**UI total ≈ 16m40s**, plus **≈3–5m** of setup (provision account, install/verify APK, device lease).
A human does the same flow in **~3–4 minutes**. The gap is almost entirely **harness overhead**, not the
device or the network.

## Where the time actually goes

The three fat segments (5m14s, 4m05s, 4m39s) share the same shape — they're dominated by the
**observe→act loop overhead**, not by anything the app is doing:

1. **Per-action adb round-trips.** A single "tap the button labeled X" is really: `uiautomator dump` →
   `exec-out cat` the XML → parse → compute center → `input tap x y` → **fixed `Start-Sleep`** → often a
   re-`dump` to confirm. That's 4–6 adb invocations and a hard sleep for **one** logical action, and each
   screen has several actions.
2. **Fixed sleeps instead of polling.** Waiting a flat `Start-Sleep -Seconds 3–5` after every tap "to be
   safe" is the single biggest tax. Screens that were ready in 300 ms still cost the full sleep; multiply
   by dozens of actions across a run.
3. **Fresh process per tool call.** Every `powershell` tool call is a brand-new process that re-resolves
   `adb`, re-reads env, and re-establishes the adb client each time — hundreds of ms of pure startup, paid
   on every micro-step because steps were issued as separate calls.
4. **Char-by-char typing.** The autofill/passkey overlay forces one-character-at-a-time input at ~55–60 ms
   per character; a 20-char password + a UPN is a couple of seconds *just typing*, before any verification.
5. **Screenshot capture + pull + view.** `screencap` on-device → `adb pull` → open the PNG is ~1–2 s each;
   taking one after every step to "see what happened" adds up fast (and is wasted on FLAG_SECURE screens
   that come back black).
6. **Retries when input didn't land.** WebView typing that silently dropped (autofill) triggered re-typing
   and re-verification — the 4m05s password segment is mostly this.
7. **Genuinely slow bits (unavoidable-ish).** eSTS WebView first paint, the switch to the browser for the
   challenge, and any re-auth after a session timeout are real seconds — but they're the minority.

## Root causes

- **Chatty, one-action-per-call driving** with a fresh shell each time → startup + round-trip cost paid
  hundreds of times.
- **Pessimistic fixed sleeps** substituting for readiness signals.
- **Verify-by-re-dump / verify-by-screenshot** after every action instead of only at decision points.
- **Overlay-forced char-by-char** typing on every field, even when bulk would have worked.
- **No reuse** of the uiautomator dump: we re-dump for the next action instead of reusing the XML we just
  fetched.

## Speed-ups (what to do differently)

Ordered by payoff:

1. **Keep one long-lived shell for a whole screen/segment.** Batch the dump→parse→tap(s) for a screen into
   a *single* `powershell` call (an async session you reuse) so you pay process/adb startup once per
   segment, not once per tap. This alone removes most of the fresh-process tax (root cause 3).
2. **Replace fixed sleeps with anchor polling — and fuse tap+wait.** `deviceui.ps1 wait-text -Text
   "<next screen anchor>"` returns the instant the screen is ready instead of always waiting N seconds.
   Better still, `tap-text "<button>" -Then "<next anchor>"` performs the tap **and** waits for the next
   screen in a **single** call (one process, no fixed sleep). Polling defaults to 600 ms (`-PollMs` to tune).
   Use these after every navigation instead of `Start-Sleep`. Biggest single win (root cause 2).
3. **Reuse one dump for multiple actions.** When a screen has several fields/buttons, `dump` once, then
   compute all the tap targets from that one XML rather than re-dumping per element.
4. **Try bulk input first, fall back to char-by-char.** Attempt `input-text` (bulk) once; only switch to
   `-CharByChar` if verification shows it didn't land. Don't pay per-char cost on fields where the overlay
   isn't present (root cause 4). `input-text -Clear` already clears in **one** adb call (MOVE_END + bulk
   DEL) instead of many.
5. **Verify at decision points, not after every micro-action.** Confirm state only where a wrong turn is
   costly (did the password page advance? is the account in the list?). Skip the reflexive re-dump after
   taps you're confident about.
6. **Stop screenshotting FLAG_SECURE screens.** They come back black — capturing them is pure waste. Use a
   single `uiautomator dump` as evidence instead, and reserve screenshots for the non-secure screens that
   actually render (root cause 5). See [common-blockers.md](common-blockers.md#flag_secure-black-screenshots).
7. **Provision with cached SSO.** `labapi.ps1` reuses a cached Edge profile so the account-creation call
   isn't paying an interactive sign-in each time; provision the account **just before** the run (temp users
   expire in ~60 min) so setup overlaps nothing.
8. **Prefer a connected physical device on GPU-less hosts.** On a Cloud PC/VM the emulator renders in
   software and is far slower; unless a step needs an injectable fingerprint, a physical device removes a
   whole class of slowness (see [emulator-performance.md](emulator-performance.md)).

## Fast-path recipe

Per screen:
1. `wait-text` on an anchor for the *expected* screen (no fixed sleep) — or let the previous screen's
   `tap-text -Then` land you here (the `-Then` wait doubles as this arrival check).
2. `dump` **once**; compute every tap/field target from that XML.
3. Do the taps / `input-text` (bulk first) in the **same** shell call; for the navigation tap, use
   `tap-text "<button>" -Then "<next anchor>"` so the tap and the wait-for-next-screen are one call.
4. Verify **once** by the next screen's anchor — the `-Then` (or a `wait-text`) both confirms success *and*
   is the wait for the following screen. One signal does double duty.
5. Screenshot only if the screen is non-secure and you need it as evidence.

## Expected savings

The three fat segments are ~80% overhead. Realistic targets:
- **Fixed-sleep → poll** and **batch-per-screen** together typically cut those segments by **50–70%**.
- Dropping FLAG_SECURE screenshots and redundant re-dumps trims another chunk.
- A ~16m40s UI run should land around **5–7 minutes** — within ~2× of a human instead of ~5×, with the
  remainder being genuine eSTS/WebView load and any real re-auths.

None of these change *what* is tested — they only remove harness idle time. Apply them by default; fall
back to the slower, more defensive pattern only on a screen that's proving flaky.
