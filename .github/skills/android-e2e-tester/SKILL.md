---
name: android-e2e-tester
description: "Execute and iterate end-to-end (E2E) tests for an in-development Android Auth feature (MSAL, Broker, Common, ADAL, Authenticator) on an Android emulator or a connected real device. Builds a device pool from emulators and any adb-connected hardware, finds or creates a suitable AVD (or reuses a running emulator / real device), leases the device so concurrent tests don't collide, builds/installs the right test app, drives the UI, and verifies via logcat. Delegates the actual run to a sub-agent (same model as the parent) and waits for its verdict. Use when asked to 'run the E2E test', 'test this feature end to end', 'verify the feature on a device or emulator', 'does this work on a device', 'run this ADO test case', or 'try the sign-in flow' — or automatically once a feature finishes development and is ready for E2E testing. Discovers what to test from user-provided steps, an ADO Test Case work item, a known test-steps file, the session's design spec, or the implementation diff — feature-specific steps live in the test case, not the skill. Auto-performs inputs the AI can handle (typing lab test credentials, tapping buttons, granting permissions, simulating a fingerprint, provisioning lab accounts via the LAB API), mocks unavailable dependencies and sets feature flags via temporary code changes when needed, and asks the user when the intent is unclear or a step is a real blocker (push MFA, hardware, missing credentials, implementation gaps). Checks logs to decide pass/fail, drives a fix-and-retest loop until it passes, and — for ADO test cases — always writes an HTML + Markdown test report at the end. Prefers an emulator when a step needs an injectable fingerprint/biometric (App Lock, number-match with biometric gate)."
---

# Android E2E Tester

Take an in-development Android Auth feature and prove it works on a real device or an emulator: provision
the device, deploy the app, run the scenario (auto-handling every input the AI reasonably can), read the
logs to decide pass/fail, and loop fix-and-retest until it passes — or stop and ask when genuinely blocked.
For a test case driven from Azure DevOps, always finish by writing an HTML + Markdown test report.

## When this runs

- **Manually** — the developer asks to test/verify a feature on the emulator.
- **Automatically** — right after a feature finishes development and is ready for E2E (e.g. the
  feature-orchestrator's testing step, or after a coding-agent PR lands locally). When auto-invoked,
  still run Phase 1 to confirm *what* to test before touching the device.

## Run folder

Put all artifacts (log snapshots, screenshots, per-iteration notes) **outside the repo** so nothing is
committed:
```
$env:USERPROFILE\android-e2e-runs\<feature>-<yyyyMMdd_HHmmss>\
```
Pass this as `-Out` to `authlogs.ps1`/`deviceui.ps1`. Reuse the resolved emulator `-Serial` on every
script call so commands target the right device.

## Scripts

All under `scripts/` (PowerShell — the team's cross-platform shell). Run `-?` mentally via the
`.SYNOPSIS` in each file. They self-resolve the SDK; no hardcoded paths.

| Script | Purpose | Key commands |
|---|---|---|
| `emulator.ps1` | Device pool: emulators **and** real devices | `ensure`, `status`, `list`, `pool`, `list-images`, `create`, `start` |
| `devicelease.ps1` | Lease a device so concurrent tests don't collide | `acquire`, `heartbeat`, `release`, `list`, `reap` |
| `appcontrol.ps1` | App build/install/state | `build`, `install`, `launch`, `clear`, `uninstall`, `is-installed`, `grant`, `list-apks` |
| `deviceui.ps1` | AI-driven UI I/O | `dump`, `wait-text`, `tap-text`, `tap-desc`, `input-text` (`-Clear`, `-CharByChar`), `key`, `finger`, `finger-status`, `finger-enroll`, `screenshot`, `current-app` |
| `authlogs.ps1` | Log capture + verdict | `clear`, `scan`, `snapshot`, `grep`, `watch` |
| `labapi.ps1` | Provision/reset LAB test accounts (EasyAuth via WAM SSO) | `create-user`, `reset`, `enable-policy`, `disable-policy`, `delete-device`, `open` |
| `report.ps1` | Render the HTML + Markdown test report (mandatory for ADO test cases) | `render` (from a run JSON) |

## Execution model — run inside a sub-agent, and supervise it

An E2E run is long and chatty (boot, install, dozens of UI dumps/taps, log scans). **Delegate the actual
device-driving to a sub-agent** so the parent's context stays clean and the parent supervises rather than
performs.

1. **Launch one sub-agent to own the run, on the SAME model as the parent.** If the parent is running on
   Claude Opus 4.8, request the sub-agent on Claude Opus 4.8 (match whatever model the parent is on).
   Give it a **self-contained** prompt: the scenario + explicit success criterion (Phase 1 output), the
   leased `$serial` (or tell it to lease one with **its own agent id** as owner), the app/module +
   package, credential-handling rules (type on device, never print), the run-folder path, any
   mocks/flights to apply, and an instruction to **finish with a `PASS` / `FAIL` / `BLOCKED` verdict plus
   evidence** (success signal + correlation_id, or the exact blocker).
2. **The parent WAITS for the sub-agent to finish.** Start it in the background, then block on its
   completion (read its result with wait=true). **Do not end your turn, and do not report to the user,
   until the sub-agent's verdict is in.** This is the #1 real failure mode we hit: the parent returned
   early, the sub-agent's result and analysis were never surfaced to the user.
3. **Supervise with a watchdog while waiting:**
   - **Hang → restart.** The sub-agent should emit a progress line and refresh its device lease
     (`devicelease.ps1 heartbeat`) at each phase. If there's no progress within a timeout (e.g. ~10–15 min
     for an install-heavy flow) **and the test isn't done**, treat it as hung: nudge once; if still stuck,
     **restart** it (fresh sub-agent, resume from the last known state) rather than waiting forever.
   - **Result in → stop.** If the verdict/evidence is already available, **stop waiting / terminate** the
     sub-agent and move to reporting — don't let a finished run linger.
   - **Cap restarts** (e.g. 2). If still unresolved, escalate to the user with the full evidence trail.
4. **Only the parent reports to the user.** Collect the sub-agent's verdict + artifact links and present
   the Phase 7 report yourself.

If the harness can't spawn a sub-agent (or you're already the deepest agent), run the phases inline — but
keep the same discipline: **don't declare done until the verdict is in, and don't loop forever on a hang.**

## Workflow

Execute in order. Announce a one-line status at each phase.

### Phase 1 — Determine what to test

Synthesize a concrete E2E scenario from the session context, in priority order:

1. **User-provided test steps** in the session (explicit steps or a scenario the user described).
2. **An ADO Test Case work item** — the primary home for feature-specific steps. Fetch and parse it (see
   "Sourcing test-case-specific steps" below); the `test-planner` skill can author/export these.
3. **A known test-steps file** the session points to (an exported plan, a markdown checklist, or a path
   the user names).
4. **The design spec** (`design-docs/`) — its acceptance criteria and flows.
5. **The implementation diff** — `git -C <sub-repo> diff` across changed repos to see what actually
   changed and which app/module it affects.

Produce: the **feature summary**, the **target app/module** (see
[references/app-and-module-map.md](references/app-and-module-map.md)), and a **step-by-step scenario**
with an explicit, observable **success criterion** (e.g. "AcquireTokenSilent returns a token; logs show
`executed successfully` with a correlation_id; no crash").

**Ask the user if** the scenario is ambiguous, multiple flows could be meant, or you cannot tell what
"working" looks like. Do not guess a scenario when the intent is unclear.

**Keep specifics out of the skill.** This skill is generic. Anything specific to one feature — which app
configuration / client id to select, which lab account, which broker to pair, the exact tap sequence, the
expected success markers — belongs in the **test case**, not here. Read those specifics from the sources
above at run time; never hardcode a particular feature's steps into the skill or its scripts.

#### Sourcing test-case-specific steps

- **From ADO** (preferred for anything repeatable): a **Test Case** work item stores its steps in the
  `Microsoft.VSTS.TCM.Steps` field (HTML/XML — each `<step>` has an action and an expected result). Fetch it:
  ```powershell
  az boards work-item show --id <testCaseId> --org <org-url> `
    --fields "System.Title,Microsoft.VSTS.TCM.Steps" --output json
  ```
  Parse the steps into an ordered action/expected list, drive them in Phase 4, and use each step's
  **expected result** as its success criterion. A linked **Shared Steps** work item is fetched the same
  way. (The `test-planner` skill authors and pushes these test cases, so the loop is:
  test-planner → ADO → this skill.)
- **From a known file**: if the session provides a test-steps file (an exported plan, a markdown
  checklist, or a path the user names), read the steps from there and treat them like ADO steps.
- If neither exists and the scenario isn't otherwise clear, **ask the user** rather than inventing
  feature-specific steps.

### Phase 2 — Provision the device (emulator or real device)

The **device pool** is every usable target: emulators **and** any real device connected over adb.
`emulator.ps1 pool` lists them. Derive requirements from the feature (min API, Google Play services for
broker/push flows) — see "Emulator / real-device requirements per feature" in the app-and-module map —
then one command finds-or-creates, reuses-or-starts, and waits for boot:

```powershell
./scripts/emulator.ps1 ensure -RequireGoogleApis -ApiLevel 30 -Wait
# prefer a connected real device if one meets the requirements:
./scripts/emulator.ps1 ensure -RequireGoogleApis -PreferPhysical -Wait
# pin an exact device (emulator serial or real-device serial):
./scripts/emulator.ps1 ensure -Serial 39121FDJH003ZK
# or target a specific AVD: ./scripts/emulator.ps1 ensure -Avd Pixel_7 -Wait
# stick to emulators only: add -NoPhysical
```
A connected, booted real device that satisfies the requirements is a valid target (real devices are
never created/booted — only used if already connected). By default `ensure` reuses a running emulator
first, then a matching real device, then boots/creates an AVD; `-PreferPhysical` flips it to try real
devices first. Capture the printed `SERIAL=` and use it as `-Serial` everywhere. **When multiple tests
may run concurrently, lease the device first** (see Phase 2a) so two runs don't collide. If provisioning
fails, see [references/troubleshooting.md](references/troubleshooting.md) (missing images, hypervisor,
boot hang).

> **Speed check (do this if the run feels slow).** Run `./scripts/emulator.ps1 resolve-sdk` — it prints the
> host GPU/perf profile. On a **Cloud PC / VM / RDP host there is no GPU**, so the emulator uses slow
> **software rendering** (SwiftShader) and its NAT makes Play Store downloads crawl — nothing makes an
> emulator fast there. **Prefer a connected physical device** (the skill auto-prefers one on a GPU-less
> host; force with `-PreferPhysical`). AVDs the skill creates already appear in **Android Studio's Device
> Manager** (shared `~/.android/avd`); to see one in Studio's **Running Devices**, start it from Studio and
> let the skill reuse it. Details: [references/emulator-performance.md](references/emulator-performance.md).

### Phase 2a — Lease the device (avoid collisions with other tests)

Multiple E2E tests can run at once (different agents/sessions). To stop two runs from driving the same
device, **lease it** before use and **release it** when done:

```powershell
# Acquire (reaps abandoned leases, picks a free device, or boots one if the pool cap allows).
# Use YOUR agent/session id as the owner so a dead agent's lease can be reclaimed.
$serial = (./scripts/devicelease.ps1 acquire -Owner $AgentId -Feature <feature> `
             -RequireGoogleApis -ApiLevel 30 -Wait |
             Select-String '^SERIAL=(.+)$').Matches[0].Groups[1].Value
```
- **Owner = your agent/session id** (or set `$env:E2E_AGENT_ID`). Liveness is heartbeat-based: refresh
  during the run so a long test isn't reclaimed out from under you:
  `./scripts/devicelease.ps1 heartbeat -Owner $AgentId -Serial $serial` (call it at each phase).
- **Pool cap:** `-MaxPoolSize N` (default 4) limits concurrent devices. If the pool is full and every
  device is held by a **live** owner, acquire fails with guidance (wait, raise the cap, or connect a
  device). If a holder's agent/session is **dead** (heartbeat older than `-StaleMinutes`, default 30),
  its device is reclaimed automatically on the next `acquire`/`reap`.
- `acquire` will **boot/create an emulator** to add a device when the pool has no free one and the cap
  allows — so it subsumes Phase 2's `emulator.ps1 ensure` (pass the same requirement flags). Use
  `emulator.ps1 ensure` directly only when you deliberately don't want leasing.
- Do the whole run against the leased `$serial` (pass it as `-Serial` everywhere).

### Phase 3 — Deploy the app-under-test

1. Decide the test surface and whether a **broker** must also be installed (brokered flows need a
   calling app + a broker) — see the app-and-module map's pairing rules.
2. Prefer an APK Android Studio already built (`appcontrol.ps1 list-apks`); otherwise build
   (`appcontrol.ps1 build -Module <:module>`). Gradle builds need the repo's Maven creds — if they fail
   with 401, that's an environment blocker for the user.
3. Install, and for a clean run reset state:
   ```powershell
   ./scripts/appcontrol.ps1 install -Module :msalTestApp -Serial <serial>
   ./scripts/appcontrol.ps1 clear   -Package <pkg> -Serial <serial>   # clean slate
   ./scripts/appcontrol.ps1 launch  -Package <pkg> -Serial <serial>
   ```
   Verify the exact package/activity at runtime (the map shows how) — don't trust hardcoded IDs.

### Phase 4 — Execute the scenario (auto-handle inputs)

Clear the log buffer first, then drive the flow:

```powershell
./scripts/authlogs.ps1 clear -Serial <serial>
```

Loop per screen using [references/ui-interaction.md](references/ui-interaction.md), following the
**fast path** (see [references/run-speed.md](references/run-speed.md)): `dump` **once** per screen and
compute every target from that one XML → act → verify by the **next** screen's anchor with
`tap-text -Then "<anchor>"` (tap + wait in one call) or `wait-text`, **never a fixed `Start-Sleep`**.
Batch the dump→tap(s) for a screen into a **single** shell call so process/adb startup is paid once per
screen, not once per tap. Re-`dump` only when you must read genuinely new state; verify a navigation by its
anchor, not a reflexive re-dump after every tap. Save a `screenshot` only at **milestones** and only on
screens that actually render (skip FLAG_SECURE screens — they come back black; capture a `uiautomator dump`
as evidence instead).

**Auto-handle** everything the AI reasonably can: type lab test credentials, tap Next/Sign in/Accept/
Allow/Yes, pick an account, grant permissions, simulate a fingerprint (`finger`), enter a TOTP if the
seed is known, set/enter a device PIN. **Do not** print or commit credentials.

**Typing into eSTS/WebView credential fields (hard-won).** The email/password pages are a WebView and
Chrome's autofill/passkey overlay can silently swallow a bulk `input text` (the value lands in the wrong
field or is dropped, producing "Enter a valid email"). **Try bulk first** (it's fast); only fall back to
`-CharByChar` if verification shows the value didn't land:
```powershell
./scripts/deviceui.ps1 input-text -Text $upn -Clear -Serial <serial>                      # bulk first (fast)
./scripts/deviceui.ps1 input-text -Text $upn -Clear -CharByChar -Serial <serial>          # fallback if it didn't land
./scripts/deviceui.ps1 input-text -Text $pw  -Clear -CharByChar -Secret -Serial <serial>  # password (never echoed)
```
`-Clear` empties the field first, `-CharByChar` defeats the overlay, `-Secret` keeps the value out of the
transcript. Dismiss a passkey/"Save password" bottom sheet with `key ESCAPE` before typing if one appears.
See [references/common-blockers.md](references/common-blockers.md).

**Provision / repair the lab account with the LAB API** when the scenario needs a fresh account or the
account state is stuck (e.g. MFA already registered from a previous run, a CA policy blocking the step):
```powershell
./scripts/labapi.ps1 create-user   -UserType GlobalMFA                  # temp user, auto-deletes in 60 min
./scripts/labapi.ps1 reset         -Upn $upn -Operation mfa             # clear stale MFA registration
./scripts/labapi.ps1 disable-policy -Upn $upn -Policy GlobalMFA          # unblock a CA-gated segment
```
See [references/lab-api.md](references/lab-api.md) for all endpoints, usertypes, and the auth workaround.

**Set flags & mock what's missing (don't fake a pass).** If the scenario needs a feature flag on, or a
step depends on data/a dependency you can't produce naturally (a server API not deployed yet, a
collaborator app you can't drive), set the flag and mock the missing piece — including **temporary code
changes** that you revert afterward. If a middle piece genuinely can't be mocked, test the flow in
**segments**. See [references/mocking-flights-and-segments.md](references/mocking-flights-and-segments.md).

**Stop and ask the user** only for genuine blockers (see the consolidated list below).

### Phase 5 — Verify from logs

```powershell
./scripts/authlogs.ps1 scan -Package <pkg> -Serial <serial> -Out <run>\iter1\logcat.txt
```
`scan` prints a heuristic verdict plus evidence (crash stacks, failure lines, success lines, AADSTS
codes, correlation IDs). **Reason over the evidence** — don't blindly trust PASS/FAIL. Confirm the
scenario's specific success criterion is met (a positive success signal, not just absence of errors),
per [references/log-signals.md](references/log-signals.md). For automation-test mode, also read the
`build/reports/androidTests/` results.

Classify the outcome:
- **PASS** — success criterion met, no crash → go to Phase 7.
- **FAIL (real defect)** — crash in changed code, `AADSTS50011/700016`, regressed silent auth, broken
  IPC → go to Phase 6.
- **Environment/harness problem** (missing image/creds, signature mismatch, stale snapshot, offline
  adb) → fix the setup per troubleshooting, then re-run from Phase 2/3. Do **not** send these to the fix loop.
- **INCONCLUSIVE** — wrong package filter, action never executed, logging off → re-run the scenario
  cleanly before deciding.

### Phase 6 — Iterate: fix and retest

For a real defect:

1. **Root-cause** from the evidence: the failing signal, correlation_id, stack, and the code in the
   diff it points to. State the hypothesis with the log line that supports it.
2. **Get it fixed:**
   - If the code is in a checked-out sub-repo the agent can edit, **fix it directly** (Kotlin-first per
     repo conventions), then rebuild.
   - If the work is managed via a coding-agent PR, hand the diagnosis to that agent (e.g. `@copilot`
     on the PR, or the `pbi-dispatcher` skill) with the exact evidence and root cause.
3. **Reinstall → clear → re-run the scenario → re-scan** (Phases 3–5).
4. Repeat, **max 3–5 iterations**. If still failing, **stop and escalate** to the user with the full
   evidence trail (what was tried, current hypothesis, why it's stuck). Do not loop forever.

Track iterations in the run folder (one subfolder per attempt) so regressions/progress are diffable.

### Phase 7 — Report

Summarize: scenario tested, target app/emulator, final verdict, iterations taken, key evidence
(success signal + correlation_id, or the blocker), and links to the run-folder artifacts (logs,
screenshots). If blocked, state exactly what you need from the user to proceed.

**For an ADO Test Case, a written test report is MANDATORY — always generate it, on every outcome
(PASS / FAIL / BLOCKED / PARTIAL), not only on success.** Render both an HTML and a Markdown report into
the run folder with `report.ps1`:
```powershell
# Build a run JSON (verdict, device/app/account metadata, per-step action/expected/result, evidence,
# blockers, artifact paths — UPN only, never a password), then render:
./scripts/report.ps1 render -In <run>\run.json    # writes TestReport.html + TestReport.md next to it
```
The step list should mirror the ADO test case's steps, and each step's **result** should be judged
against that step's **expected result**. Include the ADO ids (`testCaseId`/`planId`/`suiteId`) and a link
so the report ties back to the test plan. See [references/test-reporting.md](references/test-reporting.md)
for the JSON schema and a worked example. (For an automation-test run, also attach the
`build/reports/androidTests/` output.) Present the verdict and the report paths to the user; optionally
publish the outcome back to the ADO Test Run.

**Release the device lease** so the next test can use it (do this even on failure/blocked):
```powershell
./scripts/devicelease.ps1 release -Owner $AgentId -Serial $serial
```

## When to ask the user

Ask (don't guess) when:
- The **scenario/intent is unclear**, or several flows could be meant, or success can't be defined.
- A **blocker** needs a human: real push-notification MFA on another device, SMS/phone OTP, a hardware
  key/NFC/QR/camera, CAPTCHA, a credential the AI doesn't have, or a tenant/CA policy it can't provision.
- The **implementation is incomplete** for the path under test (stubs, `TODO`, missing wiring, feature
  flag off with no way to enable) — **first try** to set the flag (temp code change) and mock/segment
  around a missing middle piece (see
  [mocking-flights-and-segments.md](references/mocking-flights-and-segments.md)); report the gap and ask
  only if the feature genuinely can't be exercised even in segments.
- **Environment setup** is missing and only the user can fix it (Maven creds/PAT, no sub-repo checkout,
  no system image, no lab account).

When you can complete a manual step *with* the user live (e.g. they approve a push), pause, let them do
it, then resume the automated flow.

## References

Load these as needed (don't preload all):

| File | Read it when |
|---|---|
| [references/app-and-module-map.md](references/app-and-module-map.md) | Choosing/deploying the test app, package discovery, broker pairing, credentials, emulator requirements |
| [references/lab-api.md](references/lab-api.md) | Provisioning/resetting a lab test account, LAB API endpoints/usertypes/policies, the EasyAuth auth workaround, `labapi.ps1` |
| [references/common-blockers.md](references/common-blockers.md) | Recurring hiccups & when to switch to an emulator (fingerprint/App-Lock, number-match MFA, session timeouts, FLAG_SECURE, autofill/passkey overlay, screenshot corruption) |
| [references/test-reporting.md](references/test-reporting.md) | Writing the mandatory ADO test report — run-JSON schema, `report.ps1`, worked example |
| [references/run-speed.md](references/run-speed.md) | The run feels slow; understanding per-step latency sources and how to shorten them |
| [references/emulator-performance.md](references/emulator-performance.md) | The run is slow, you're on a Cloud PC/VM/RDP (software rendering), or you want the emulator to show in Android Studio's Device Manager / Running Devices |
| [references/log-signals.md](references/log-signals.md) | Interpreting logcat, success/failure patterns, AADSTS codes, per-flow pass criteria, eSTS correlation |
| [references/ui-interaction.md](references/ui-interaction.md) | Driving auth screens, AI-vs-human inputs, FLAG_SECURE gotcha, selector strategy |
| [references/mocking-flights-and-segments.md](references/mocking-flights-and-segments.md) | A flag must be set, a dependency/server data is unavailable, or the flow can't run fully E2E (mock it or test in segments) |
| [references/troubleshooting.md](references/troubleshooting.md) | Emulator/build/install/uiautomator/broker failures; env-vs-defect triage |

## Guardrails

- **Never** print, log, or commit real or lab credentials, tokens, or secrets. Type them onto the device
  only (use `input-text -Secret` so the value never hits the transcript).
- **Artifacts stay outside the repo** (the run folder). Never commit logs/screenshots/reports.
- **For an ADO test case, always generate the HTML + Markdown report** (`report.ps1`) on every outcome —
  PASS, FAIL, BLOCKED, or PARTIAL. A run driven from a test case is not "done" until the report exists.
- **Prefer an emulator when a step needs an injectable fingerprint/biometric** (App Lock, biometric-gated
  number-match). `adb emu finger touch` works only on emulators; a physical device needs a human at the
  sensor. See [references/common-blockers.md](references/common-blockers.md).
- **When delegating to a sub-agent, wait for its verdict** before reporting or ending the turn; restart it
  if it hangs and the test isn't done, terminate it once the result is in. Never surface "done" without the
  sub-agent's PASS/FAIL/BLOCKED evidence.
- **Lease the device before use and release it after** (even on failure) so concurrent runs don't collide.
- **Temp changes for mocks/flags stay uncommitted and get reverted** — never commit/push a flag flip or a
  mock; leave a `TODO: REVERT` marker and confirm the tree is clean before finishing.
- **Environment problems are not code defects** — fix setup and re-run; only hand real defects to the fix loop.
- **Cap the loop** (3–5 iterations) and escalate with evidence rather than looping indefinitely.
- **Require a positive success signal** matching the scenario before declaring PASS.
- Follow repo conventions when fixing code (Kotlin for new code, the `Logger` class, minimal changes).
