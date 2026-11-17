# Run-Speed Analysis — why steps are slow and how to shorten them

An analysis of where wall-clock time goes in a UI-driven run, using the AAD MFA sign-in run
(`20260721_184540`) as the reference, plus concrete speed-ups. This is guidance, not a hard gate — but the
default driving pattern should follow the "fast path" below.

Table of contents:
- [Measured timeline](#measured-timeline)
- [The dominant cost: agent round-trips, not device time](#the-dominant-cost-agent-round-trips-not-device-time)
  - [The overhead is FIXED per call — it is not thinking time](#the-overhead-is-fixed-per-call--it-is-not-thinking-time)
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

## The dominant cost: agent round-trips, not device time

This was re-measured on a 6-case Authenticator batch (`authn-rerun-tier12-20260729_121549`) and the result
overturns the intuitive explanation. **Process startup is ~nothing. The agent's own think-time is everything.**

Benchmarked on a live emulator — the *same* 4-screen sequence, run two ways:

| | Shell time |
|---|---:|
| 4 separate `deviceui.ps1` calls | 10.8s |
| the identical steps as one `deviceui.ps1 flow` call | 10.5s |

**A 3% difference.** Batching saves almost nothing *in shell time*, because the cost is the adb round-trips,
which you pay either way. But in a real run those 4 calls were **4 separate tool calls** — and a tool call is
a full agent turn.

Measured properly on the densest available sample (`tc2579657-main-20260727_094717\shots`, 108 artifacts, one
written per device interaction, so consecutive file mtimes = consecutive round-trips):

| | Seconds between consecutive device interactions |
|---|---:|
| min | 2.3 |
| p25 | 19.9 |
| **median** | **24.4** |
| p75 | 35.8 |
| p90 | 59.6 |
| max | 120.6 |

Against a directly measured device cost of **2.53 s** for one `dump` (3-run mean, physical device), returning
**~13 k chars ≈ 3.3 k tokens** of UI XML:

```
one screen driven as its own tool call ≈ 25 s median   (of which ~2.5 s is the device)
```

**~90% of per-screen wall clock is turn overhead, not the phone.** Note "overhead", not "deliberation" — see
the next section for why that distinction decides which fix works.

### The overhead is FIXED per call — it is not thinking time

The obvious objection is: *if the test steps are clear, the next action is obvious, so why does it take 25 s?*
The data says the objection is right about the decision and wrong about the cause. Splitting the same run's
gaps by how hard the decision actually was:

| Step class | n | median | mean |
|---|---:|---:|---:|
| **Easy** — deterministic nav (privacy/telemetry/upsell/home/settings) | 16 | **25.7 s** | 37.2 s |
| **Hard** — eSTS, proof-up, pairing, number-match, error screens | 22 | **27.5 s** | 32.2 s |

**Essentially identical — 1.8 s apart on a ~26 s baseline.** Tapping *Accept* on a privacy screen, which needs
zero deliberation, costs the same as diagnosing a proof-up failure. So the time is *not* the model working out
what to do; it is the fixed cost of a turn: emitting the call, running it, and reading a multi-thousand-token
UI dump back into a growing context.

Two consequences, and they are the whole point:

1. **"Think faster" is not available as a lever** — there is little thinking in the 25 s to remove.
2. **"Take fewer turns" is the only lever, and it works best exactly where the steps are most obvious.** A
   deterministic preamble is *all* fixed cost and *no* decisions, so collapsing its N screens into one call
   removes ~(N−1) × 25 s and loses nothing. This is why speed-up #1 is the flow runner, and why it should be
   pointed at the boring parts of a run, not the interesting ones.

### What this cost the batch

Reconstructed from each case's `progress.log` (work time = spans with gaps ≤ 10 min):

| Case | iters | work (min) | span (min) | idle gap (min) |
|---|---:|---:|---:|---:|
| 1579397 | 2 | 58 | 94 | **36** |
| 1579401 | 1 | 26 | 26 | 0 |
| 1579411 | 2 | 65 | 155 | **90** |
| 1579416 | 1 | 11 | 11 | 0 |
| 1579417 | 2 | 67 | 100 | **33** |
| 1579425 | 1 | 39 | 39 | 0 |

Three findings, none of which is "the 30-minute cap was ignored" (it wasn't — every *iteration* came in at
11–39 min):

1. **The cap was per-ITERATION, not per-CASE.** A second iteration silently reset the budget, so a case could
   legitimately consume ~60+ min. Fixed by adding a per-case total budget — see
   [SKILL.md](../SKILL.md) Phase 1/batch section.
2. **159 minutes of pure dead time** (36 + 90 + 33) with *no work happening at all*. Cause: a `write_agent`
   follow-up is only delivered once the target agent's **entire turn** completes, so a follow-up queued behind
   a lane that still had 4 cases to run sat idle for **90 minutes**. **Never queue a follow-up behind a busy
   lane — dispatch it as a fresh agent on a free lane.**
3. **Inside a ~28-min iteration, ~20 min was fixed setup preamble and only ~5 min was the actual test.** For
   1579397: provision → clean install → 4 first-run gates → add account → eSTS sign-in → proof-up pairing →
   App Lock disable ran 12:19→12:39; the assertions the case actually cares about ran 12:42→12:47. That same
   ~20-min preamble was paid **~9 times** across the batch (6 cases + 3 re-iterations) ≈ **3 hours of setup to
   run ~30 minutes of assertions** — and the preamble is 100% deterministic, contains zero decisions, and is
   already fully written down in [authenticator-app.md](authenticator-app.md). Every round-trip in it is pure
   waste. **This is what speed-up #1 exists to remove.**

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
3. **One tool call per micro-step.** Every `powershell` tool call is a fresh process — but the process
   startup itself turned out to be **negligible** (measured: 3% on a 4-step sequence). What is *not*
   negligible is that each call is a separate **agent turn**, costing a ~25 s median round-trip for ~2.5 s of
   device work, and that cost is **fixed regardless of how obvious the step is** (25.7 s for easy nav vs
   27.5 s for hard auth screens). Issuing steps as separate calls is the single largest tax in the whole run;
   see [The overhead is FIXED per call](#the-overhead-is-fixed-per-call--it-is-not-thinking-time).
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

- **Chatty, one-action-per-call driving** — not because of process startup (measured negligible) but because
  every call is a separate agent turn at a fixed ~25 s, so a 12-screen deterministic sequence costs 12 of them
  (~5 min) to do ~30 s of device work.
- **Pessimistic fixed sleeps** substituting for readiness signals.
- **Verify-by-re-dump / verify-by-screenshot** after every action instead of only at decision points.
- **Overlay-forced char-by-char** typing on every field, even when bulk would have worked.
- **No reuse** of the uiautomator dump: we re-dump for the next action instead of reusing the XML we just
  fetched.

## Speed-ups (what to do differently)

Ordered by payoff:

0. <a id="device-prep-turn-off-animations-and-autofill"></a>**Device prep — two settings, once per case, that
   pay off on every single step.** Run these right after the device is leased (Phase 2/3), before installing:
   ```powershell
   # kill the autofill/passkey overlay that forces slow char-by-char typing (see #4 below)
   adb -s <serial> shell settings put secure autofill_service null
   # remove animation time from every screen transition (this is what the team's UIAutomator suite does)
   adb -s <serial> shell settings put global window_animation_scale 0
   adb -s <serial> shell settings put global transition_animation_scale 0
   adb -s <serial> shell settings put global animator_duration_scale 0
   ```
   Animations at `1.0` add ~200–400 ms of *unobservable-but-real* transition time to every navigation, and —
   worse — they make an anchor appear **before** it is stable, which is a common source of taps landing on a
   mid-flight view. At `0` the next screen is present the moment the transition is issued, so anchor polling
   returns on the first poll instead of the second or third. Both settings are device-persistent, so a
   dedicated test device only needs this once; re-apply after any factory reset or `pm clear` of Settings.
1. <a id="batch-a-known-sequence-into-one-flow-call"></a>**Batch a KNOWN sequence into one `flow` call —
   the single biggest lever.** Any run of screens whose order you already know (app first-run gates, an
   add-account wizard, a settings path) has **no decisions in it**, so driving it one tool call per screen
   burns a ~25 s agent round-trip on each of them for ~2.5 s of device work — and since that overhead is
   *fixed* rather than proportional to difficulty, a no-decision sequence is the case where batching wins
   most and risks least. `deviceui.ps1 flow` executes the whole sequence in **one** call:
   ```powershell
   ./scripts/deviceui.ps1 flow -Serial <serial> -Spec <sequence.json>
   # or inline:
   ./scripts/deviceui.ps1 flow -Serial <serial> -Text '[{"tap":"Allow","then":"privacy"},{"tap":"Accept"}]'
   ```
   Each step is `{ label?, wait|tap|tapDesc|tapRes|input|secretRef|key, then?, waitSec?, optional?, exact?,
   clear?, charByChar?, screenshot?, sleepMs? }`. It prints a per-step trace with timings and a
   `N ok, N skipped, N failed` summary, stops at the first **required** failure (exit **5**, remaining steps
   marked SKIPPED), and — critically — **`"optional": true` steps that don't appear are SKIPPED, not failed**,
   which is what makes it safe for real sequences where a screen only *sometimes* shows (a Save-password
   infobar, an "App Lock enabled" popup, a permission dialog that was pre-granted).
   Ready-made specs for the Authenticator preamble are in
   [authenticator-app.md](authenticator-app.md#ready-made-flow-specs-copy-paste). Turning a ~12-screen,
   ~20-minute preamble into one call that finishes in **~40 s of device time** is where the hours are.
   *(Corollary: this supersedes the old advice to "keep one long-lived shell" — the saving was never the shell,
   it was the round-trip.)*
2. **Replace fixed sleeps with anchor polling — and fuse tap+wait.** `deviceui.ps1 wait-text -Text
   "<next screen anchor>"` returns the instant the screen is ready instead of always waiting N seconds.
   Better still, `tap-text "<button>" -Then "<next anchor>"` performs the tap **and** waits for the next
   screen in a **single** call (one process, no fixed sleep). Polling defaults to 600 ms (`-PollMs` to tune).
   Use these after every navigation instead of `Start-Sleep`. Biggest single win (root cause 2).
3. **Reuse one dump for multiple actions.** When a screen has several fields/buttons, `dump` once, then
   compute all the tap targets from that one XML rather than re-dumping per element.
4. **Disable autofill (step 0), then type in bulk.** With `autofill_service=null` the overlay that steals
   input is gone, so bulk `input-text` just works and `-CharByChar` becomes a rare fallback rather than the
   default. That matters: char-by-char is **one adb round-trip per character** (a 20-char password ≈ 20 ×
   ~120 ms ≈ 2.4 s, vs ~0.15 s bulk) and is also where dropped/reordered characters come from — so this is a
   correctness win as much as a speed one (root cause 4). `input-text -Clear` already clears in **one** adb
   call (MOVE_END + bulk DEL) instead of many.
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
9. **Check whether the case is already automated before driving it by hand.** Several Authenticator ADO cases
   have a compiled UIAutomator test that runs the same flow in minutes, unattended. See
   [existing-ui-automation.md](existing-ui-automation.md) — running (or reading) the automated test is often
   strictly faster and more repeatable than manual UI driving.

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

The three fat segments are ~80% overhead, and (per the measurement above) that overhead is **agent turns**,
not the device. Realistic targets:
- **Collapse every known sequence into a `flow` call** — this is the big one. Sizing it honestly: the observed
  ~20-min preamble (12:19→12:39) is *not* 12 round-trips; at a ~25 s median it is on the order of ~45 turns,
  and it also contains irreducible real time (APK install, account provisioning, eSTS network, typing). `flow`
  removes only the **turn overhead of the deterministic UI navigation** — but that is the bulk of it. Measured
  anchor point: the 4-gate first-run preamble went from 4 turns (~100 s) to **16.2 s in one call**. Collapsing
  the ~30 navigation turns in a preamble into ~3 flow calls saves ~27 × 25 s ≈ **11 minutes per iteration**;
  across the reference batch's ~9 preambles that is the bulk of the ~3 hours.
- **Fixed-sleep → poll** and **verify-by-anchor** together cut the remaining decision-driven segments by
  **50–70%**.
- Dropping FLAG_SECURE screenshots and redundant re-dumps trims another chunk.
- **Don't queue a `write_agent` follow-up behind a busy lane** — dispatch a fresh agent on a free lane. That
  alone was 159 minutes of dead air in the reference batch.
- A ~16m40s UI run should land around **5–7 minutes** — within ~2× of a human instead of ~5×, with the
  remainder being genuine eSTS/WebView load and any real re-auths.

None of these change *what* is tested — they only remove harness idle time. Apply them by default; fall
back to the slower, more defensive pattern only on a screen that's proving flaky.
