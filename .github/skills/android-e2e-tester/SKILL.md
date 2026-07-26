---
name: android-e2e-tester
description: "Execute and iterate end-to-end (E2E) tests for an in-development Android Auth feature (MSAL, Broker, Common, ADAL, Authenticator) on a connected real device or an emulator: provision the device, install the app, drive the UI, verify via logcat, loop fix-and-retest, and — for ADO test cases — always write an HTML + Markdown report. Only use this skill when the user explicitly asks to run an end-to-end test — e.g. 'run the E2E test', 'test this feature end to end', 'verify the feature on a device/emulator', 'does this work on a device', 'run this ADO test case', or 'try the sign-in flow'. Do NOT auto-start it after a feature finishes development, when a coding-agent PR lands, or as an implicit step of any other workflow (including an orchestrated build→test flow) — a human has to ask for an E2E run."
---

# Android E2E Tester

Take an in-development Android Auth feature and prove it works on a real device or an emulator: provision
the device, deploy the app, run the scenario (auto-handling every input the AI reasonably can), read the
logs to decide pass/fail, and loop fix-and-retest until it passes — or stop and ask when genuinely blocked.
For a test case driven from Azure DevOps, always finish by writing an HTML + Markdown test report.

## When this runs

- **Only when the user explicitly asks to run an E2E test** — e.g. "run the E2E test", "test this feature
  end to end", "verify the feature on a device/emulator", or "run this ADO test case". This skill does **not**
  auto-start after a feature finishes development, when a coding-agent PR lands, or as an implicit step of any
  other workflow (including an orchestrated build→test flow) — a human has to ask for an E2E run. Once asked,
  run Phase 1 to confirm *what* to test before touching the device.

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
| `deviceui.ps1` | AI-driven UI I/O | `dump`, `wait-text`, `tap-text`, `tap-desc`, `input-text` (`-Clear`, `-CharByChar`, `-SecretRef`), `unlock`, `key`, `finger`, `finger-status`, `finger-enroll`, `screenshot`, `current-app` |
| `authlogs.ps1` | Log capture + verdict | `clear`, `scan`, `snapshot`, `grep`, `watch` |
| `labapi.ps1` | Provision/reset LAB test accounts (EasyAuth via WAM SSO); fetch tenant passwords from Key Vault | `create-user`, `reset`, `enable-policy`, `disable-policy`, `delete-device`, `open`, `fetch-password` |
| `report.ps1` | Render the HTML + Markdown test report (mandatory for ADO test cases); `summary` = overall multi-case run report | `render` (a per-case run JSON), `summary` (a batch/run folder) |
| `secrets.ps1` | Encrypted (DPAPI) store so passwords/PINs never hit the chat | `set`, `set-device-pin`, `list`, `test`, `get-masked`, `remove`, `path` |

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
2. **The parent SUPERVISES on a wall clock — it does not merely block on a completion.** Start the
   sub-agent in the background, then poll it in a loop with a **bounded** timeout
   (`read_agent … wait=true timeout=180`) — **never** a single open-ended wait. **Do not end your turn, and
   do not report to the user, until the sub-agent's verdict is in.** Returning early is failure mode #1 (the
   sub-agent's result never reaches the user); its evil twin is failure mode #2: **blocking forever on a
   wait that can only be woken by a *completion*.** A **hung** sub-agent never completes, so it **never
   fires a completion notification** — and because it's frozen inside a blocking device call (classically a
   wedged `adb install`) it **cannot fire its own per-point abort cap either**. If your only wake-up is "the
   agent finished," a wedged lane hangs the whole turn indefinitely (this is real: two lanes wedged on
   `adb install` once stalled a suite run for ~6 hours). So the parent owns the **wall-clock watchdog** in
   step 3, and long device calls are bounded (`appcontrol.ps1 install -TimeoutSec <n>`) so a wedge fails
   fast instead of freezing the agent.
3. **Watchdog — detect a STALL on the wall clock, not via notifications:**
   - **Track liveness yourself every poll.** The sub-agent emits a progress line + refreshes its lease
     (`devicelease.ps1 heartbeat`) at each phase. Between bounded `read_agent` polls, tail its
     `tc<id>\progress.log` and note `tool_calls_completed`. **Growth in *either* = alive; no growth in
     *both* across two consecutive bounded polls (~6–10 min of zero forward motion) = STALLED**, whether or
     not a completion ever arrives.
   - **Stalled → abort the lane, don't keep waiting.** Treat a stall as a dead lane: mark the case
     **ABORTED** from its **partial** `run.json` (the sub-agent renders a skeleton early, so partial
     evidence survives its death), then **recover the device before reuse** (`adb kill-server;
     adb start-server`, re-`acquire` the lease under a fresh owner). Re-dispatch a fresh sub-agent from the
     last known state, or move on — never sit on an unbounded wait.
   - **Result in → stop.** If the verdict/evidence is already available, stop waiting / terminate the
     sub-agent and move to reporting — don't let a finished run linger.
   - **Cap restarts** (e.g. 2). If still unresolved, escalate to the user with the full evidence trail.
4. **Only the parent reports to the user.** Collect the sub-agent's verdict + artifact links and present
   the Phase 7 report yourself.

If the harness can't spawn a sub-agent (or you're already the deepest agent), run the phases inline — but
keep the same discipline: **don't declare done until the verdict is in, and don't loop forever on a hang.**
Bound any long blocking device call so a wedge can't freeze you past your own cap — install with a timeout
(`appcontrol.ps1 install -TimeoutSec <n>`; on timeout it kills the `adb install` and throws, so recover with
`adb kill-server; adb start-server` rather than rebooting the device mid-install).

## Running multiple test cases (batch) — split across devices, run in parallel

When asked to run **more than one** test case (a suite, a list of test points, several ids), treat it as a
**batch**, not a serial slog:

1. **Enumerate the work and the devices.** Resolve each case's scenario (Phase 1 per case) and **expand each
   case into its test points** (Phase 1 → "Test points and configurations") so you know how many points each
   case has and which build each uses (`Local\` for a `LocalFlights` config, `ECS\` otherwise). The **unit of
   work is the whole case** — all of a case's test points run together and land in **one consolidated report**
   (a case is one lane; its points run back-to-back within it). List every free device in the pool
   (`emulator.ps1 pool` / `devicelease.ps1 list`) and count how many you can drive in parallel (respect the
   lease pool cap, `-MaxPoolSize`).
2. **Fan out one sub-agent per test case, capped at the device count.** Give each case its own sub-agent
   (same model as the parent) with a **self-contained** prompt: that case's scenario + success criterion,
   the app/module + which APKs to install, credential rules (type on device, never print), its run-folder
   subpath, and "finish with a PASS/FAIL/BLOCKED verdict + evidence". The sub-agent **runs every test point
   for its case** (back-to-back on its leased device) and writes a **single consolidated report** for the
   case. **Each sub-agent leases its OWN device** with its own agent id as owner
   (`devicelease.ps1 acquire -Owner <caseAgentId>`) so no two lanes touch the same device. If there are **more
   cases than devices**, each device is a **lane** that runs its queue of cases one after another; the parent
   keeps every lane busy until the queue drains.
3. **One shared batch folder, one folder per case (all its test points inside).** Use a single
   `…\android-e2e-runs\<suite>-<yyyyMMdd_HHmmss>\`. Give **each case** one folder `tc<id>\` holding a single
   case-level `run.json` and a single `TestReport.html/.md`. When a case has more than one test point, record
   the points as a **`testPoints[]` array** in that one `run.json` (each entry carries its own
   `ado.testPointId` + `configuration` + `buildSource`, steps, evidence, verdict) and put each point's
   screenshots in a point-scoped subfolder `tc<id>\<ecs|local>\iter<N>\` so paths stay relative. `report.ps1
   render` then produces **one report per case** with a section per test point + a single shared "Proposed
   test steps". The suite summary still scans this folder and expands each case's points into their own rows
   in the **Config** column.
4. **The parent runs a polling watchdog across ALL lanes — on a wall clock.** Poll each live lane with a
   **bounded** `read_agent` (e.g. `wait=true timeout=180`), rotating through the lanes; between polls tail
   each lane's `tc<id>\progress.log` and note its `tool_calls_completed`. Apply the single-run watchdog per
   lane: **growth in the progress log *or* `tool_calls_completed` = alive; no growth in both across two
   consecutive polls (~6–10 min of no motion) = STALLED → mark that case ABORTED from its partial report,
   recover the device (`adb kill-server; adb start-server`, re-lease under a fresh owner), and re-dispatch
   the next pending case onto the freed lane.** The **parent** — not just the in-agent cap — enforces the
   per-point wall-clock cap (30 min/point unless told otherwise), because a sub-agent frozen in a blocking
   call (e.g. a wedged `adb install`) **cannot fire its own cap**. **Never block the turn on a single
   open-ended wait:** a hung sub-agent emits **no completion notification**, so a notification-only loop
   hangs forever if every live lane wedges at once (this stalled a real suite run for ~6 hours). Keep the
   sub-agents' installs bounded (`appcontrol.ps1 install -TimeoutSec <n>`) so a wedge surfaces as a fast
   failure the agent can record rather than a freeze. Keep every lane busy until the queue drains, and
   **don't end the turn until every case has a verdict** (including ABORTED for lanes you had to kill, and
   an explicit "not reached" for cases the batch never got to).
5. **Aggregate into an overall report.** After all cases finish, render the **suite summary** over the batch
   folder (Phase 7): `./scripts/report.ps1 summary -In <suiteFolder> -Title "<suite>"` → `SUMMARY.html` +
   `SUMMARY.md` (per-case verdict table linked to each report, plus overall counts). Present the overall
   verdict and the per-case breakdown — not just the last case.

**Only one device available?** Run the cases **serially** in a single lane (still clean-state per case, still
a per-case report each, still a final suite summary). **Independence is mandatory:** every case starts from a
clean state (Phase 3) and must not rely on another case's leftover accounts/registrations.

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

#### Test points and configurations (which build to run)

A single ADO **Test Case** can appear in a suite as **one or two *test points*, each carrying a
*configuration*** (visible as the "Configuration" column on the test point). In the Android **Broker** suite
the two configurations are `RC MSAL - RC Broker` and `RC MSAL - RC Broker (LocalFlights)`, and the
configuration decides **which staged build folder you install from**:

> ⚠️ **Counter-intuitive mapping — get this right:**
> - configuration name **contains `LocalFlights`** → install from the **`Local\`** folder
> - **any other** configuration (plain, no `LocalFlights`) → install from the **`ECS\`** folder
>
> `LocalFlights` does **not** mean the ECS folder. See
> [references/app-and-module-map.md → ECS vs Local builds](references/app-and-module-map.md#ecs-vs-local-builds-test-point-configuration).

**Run every test point of the case, once each** — a 2-point case runs **twice** (once from `Local\` for the
LocalFlights point, once from `ECS\` for the plain point); a 1-point case runs once. The **only** exceptions
are when the **case body itself** says it is ECS-only or Local-only — then run just that one build.

**Enumerate a case's test points + configurations from the Test Plan** (this is the source of truth — a case
in isolation doesn't tell you its points). Verified recipe:
```powershell
$org='https://dev.azure.com/identitydivision'; $project='Engineering'
$plan=<planId>; $suite=<suiteId>
$tok = az account get-access-token --resource '499b84ac-1321-427f-aa17-267ca6975798' --query accessToken -o tsv
$h   = @{ Authorization = "Bearer $tok" }
$u   = "$org/$project/_apis/testplan/Plans/$plan/Suites/$suite/TestPoint?api-version=7.1-preview.2"
$pts = (Invoke-RestMethod -Headers $h -Uri $u -Method GET).value
$pts | ForEach-Object {
  [pscustomobject]@{
    testPointId = $_.id
    caseId      = $_.testCaseReference.id
    config      = $_.configuration.name
    build       = if ($_.configuration.name -match 'LocalFlights') { 'Local' } else { 'ECS' }
  }
} | Where-Object caseId -eq <testCaseId> | Format-Table
```
`499b84ac-1321-427f-aa17-267ca6975798` is the Azure DevOps AAD resource GUID (`az` is already signed in for
identitydivision). Each returned row is **one run** for this case: feed its `build` into Phase 3 (which folder
to install from) and record `configuration` + `buildSource` in that run's `run.json` (Phase 7) so the report
and suite summary show a **Config** column. When you're driving a **batch**, each `(case, test point)` pair is
its own lane/run (see "Running multiple test cases").

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
   with 401, that's an environment blocker for the user. **When the case is staged with both an `ECS\` and a
   `Local\` folder, the folder to install from is dictated by the test point's configuration** — `Local\`
   for a `LocalFlights` config, `ECS\` otherwise (Phase 1 → "Test points and configurations";
   [map](references/app-and-module-map.md#ecs-vs-local-builds-test-point-configuration)). Run the case once
   per test point, each from its own folder.
3. **Start every test case from a clean state (unless told otherwise): uninstall, then freshly install all
   test apps.** A `pm clear` alone is **not** a clean slate — it leaves AccountManager work-account entries
   and broker/WPJ registration behind; only **uninstalling** the app removes those. So for each app the case
   needs (app-under-test + any broker/companion), uninstall any existing copy first, then install the
   provided/built APK:
   ```powershell
   ./scripts/appcontrol.ps1 uninstall -Package <pkg> -Serial <serial>            # ok if "not installed"
   ./scripts/appcontrol.ps1 install   -Module :msalTestApp -Serial <serial>      # or -Apk <path> for a provided APK
   ./scripts/appcontrol.ps1 launch    -Package <pkg> -Serial <serial>
   ```
   Verify the exact package/activity at runtime (the map shows how) — don't trust hardcoded IDs. Known
   provided-APK filenames (e.g. `app-production-universal-release-signed` = the **Authenticator** under test;
   `com.microsoft.windowsintune.companyportal-signed` = **Company Portal**) are in
   [references/app-and-module-map.md](references/app-and-module-map.md#known-provided-apks). **Do not tear
   down accounts/registrations at the *end* of a case** — leaving them is intended (the *next* run's
   uninstall clears them, or a tester handles them); see Phase 7.

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
seed is known, set/enter a device PIN. **Do not** print or commit credentials — when the user gives you
a password or device PIN, take it via the encrypted store and reference it by name (see
[references/secrets-and-files.md](references/secrets-and-files.md)), never inline.

**Typing into eSTS/WebView credential fields (hard-won).** The email/password pages are a WebView and
Chrome's autofill/passkey overlay can silently swallow a bulk `input text` (the value lands in the wrong
field or is dropped, producing "Enter a valid email"). **Try bulk first** (it's fast); only fall back to
`-CharByChar` if verification shows the value didn't land:
```powershell
./scripts/deviceui.ps1 input-text -Text $upn -Clear -Serial <serial>                       # bulk first (fast)
./scripts/deviceui.ps1 input-text -Text $upn -Clear -CharByChar -Serial <serial>           # fallback if it didn't land
./scripts/deviceui.ps1 input-text -SecretRef labpw -Clear -CharByChar -Serial <serial>     # password from the store (never echoed)
```
`-Clear` empties the field first, `-CharByChar` defeats the overlay. For the **password**, prefer
`-SecretRef <name>` (resolves from the encrypted store and implies `-Secret`) so the value never lands
in a tool call or the transcript — see [references/secrets-and-files.md](references/secrets-and-files.md);
`-Text $pw -Secret` still works if you already hold the value out-of-band. For **shared lab-tenant
passwords**, don't ask the user to paste — pull it from Key Vault into the store first with
`./scripts/labapi.ps1 fetch-password -TestTenant ID4SLAB2 -IntoSecret labpw` (needs `az login` +
`TM-MSIDLABS-DevKV`), then reference `-SecretRef labpw`. Dismiss a passkey/"Save
password" bottom sheet with `key ESCAPE` before typing if one appears.
See [references/common-blockers.md](references/common-blockers.md).

**Unlocking the lock screen on a physical device.** If the device sleeps/relocks mid-run and a step needs a
PIN, seed it once with `secrets.ps1 set-device-pin` (done by the human — it picks the device, or shows a
numbered menu when several are attached, and stores the PIN as `devicepin_<serial>`), then let the tool enter
+ **verify** it: `deviceui.ps1 unlock -Serial <serial>` **auto-resolves** that per-device PIN (no `-SecretRef`
needed). It confirms the keyguard actually cleared and
**stops after 3 attempts** (`-MaxAttempts`, default 3, exits 3 if it gives up) so a wrong PIN never trips
Android's Gatekeeper lockout — do **not** loop it yourself. With several devices attached (even the same
model) each has a unique adb serial; because the PIN is stored per serial, `unlock -Serial <serial>` always
uses the right device's PIN. A PIN only substitutes for biometric where a "Use PIN" path is offered; a
fingerprint-only step needs an emulator (`finger`) or a human. See
[references/secrets-and-files.md](references/secrets-and-files.md) and
[references/common-blockers.md](references/common-blockers.md).

**Provision / repair the lab account with the LAB API** when the scenario needs a fresh account or the
account state is stuck (e.g. MFA already registered from a previous run, a CA policy blocking the step):
```powershell
./scripts/labapi.ps1 create-user   -UserType GlobalMFA                  # temp user, auto-deletes in 60 min
./scripts/labapi.ps1 reset         -Upn $upn -Operation mfa             # clear stale MFA registration (temp users only)
./scripts/labapi.ps1 disable-policy -Upn $upn -Policy GlobalMFA          # unblock a CA-gated segment
./scripts/labapi.ps1 fetch-password -TestTenant ID4SLAB2 -IntoSecret labpw  # pull tenant pw from Key Vault (no paste)
```
**Don't reset a password just because sign-in rejected it.** If the device shows *"Your account or password is
incorrect"* / *"password has expired"* / *"that Microsoft account doesn't exist"*, do **not** run
`reset -Operation password` (or any password change) unless the **test case explicitly tells you to**. A wrong
password almost always means you have the wrong *value* or the wrong *account*, not that the account needs
changing — so instead: re-pull the shared value with `fetch-password` (it may have rotated in Key Vault), confirm
the UPN/tenant matches what the case named, and check the account isn't `Locked_…` from a prior lockout. Resetting
is especially dangerous for **shared durable accounts** (durable, pre-created accounts, not temp `Locked_…` users) that other tests reuse —
changing their password breaks every other case. If the value is genuinely right and it still fails, mark the run
**BLOCKED** with the exact on-screen error rather than mutating the account.

**Account policy — prefer fresh ID4SLAB2 temp users, one per case.** Provision a **new temp user for each
test case** (`create-user` makes an ID4SLab2 user that auto-deletes in ~60 min) instead of reusing one across
cases — **even when a test case names a fixed MSIDLAB4 account** (that lab is being deprecated): create the
matching `-UserType` instead (e.g. `MAMCA`, `MDMCA`, `Basic`, `GlobalMFA`). Reuse a specific named/durable
account only when the case genuinely requires that exact identity, or the user tells you to.

**Freshness gate — poll ≤ 3 min, then recreate or reuse a < 30-min-old user.** After `create-user`, a brand-new
temp user can lag ESTS replication and show *"This username may be incorrect"* at sign-in. Don't keep waiting:
if the new user isn't **consistently** sign-in-able within **3 minutes** of polite polling, **create another**
temp user, **or reuse a previously created temp user still under ~30 minutes old** (inside its 60-min TTL and
already propagated — one that already signed in once is safest). Note the swap in the report; never "fix" the lag
with a password reset. See
[common-blockers.md → Fresh temp user not sign-in-able yet](references/common-blockers.md#fresh-temp-user-not-sign-in-able-yet-ests-propagation-lag).

See [references/lab-api.md](references/lab-api.md) for all endpoints, usertypes, the account policy, and the auth workaround.

**Set flags & mock what's missing (don't fake a pass).** If the scenario needs a feature flag on, or a
step depends on data/a dependency you can't produce naturally (a server API not deployed yet, a
collaborator app you can't drive), set the flag and mock the missing piece — including **temporary code
changes** that you revert afterward. If a middle piece genuinely can't be mocked, test the flow in
**segments**. See [references/mocking-flights-and-segments.md](references/mocking-flights-and-segments.md).

**Do system-settings / on-device actions yourself before calling something blocked.** Some steps have no
adb command (advance the device clock, delete a user certificate, toggle a system setting, change the
language). Don't jump straight to BLOCKED — **open the relevant Settings screen and drive it with
`deviceui.ps1` (`dump` → `tap-text`/`tap-desc` → `input-text`) exactly like a human tester would.** E.g. to
advance the clock: Settings → *Date & time* (Samsung: *General management → Date and time*) → turn **off**
automatic date/time → set it manually. Only fall back to a blocker when the surface itself is genuinely
undrivable — a **secure/native system dialog** (Knox cert install, keyguard credential prompt, biometric
sensor) uiautomator can't read/act on, or an action that truly needs **root** (e.g. expiring an app's
*internal* monotonic cached-token timer on a non-rooted retail device — moving the wall clock won't affect
it). See [references/common-blockers.md](references/common-blockers.md#doing-it-yourself-in-system-settings).

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
so the report ties back to the test plan. **When the run came from a specific test point, also record
`ado.testPointId`, `ado.configuration` (the config name), and `ado.buildSource` (`ECS` or `Local`)** so the
report header and the suite summary's **Config** column show which build this run exercised.

**If a case has more than one test point, put them all in ONE report** — don't write a separate report per
point. Give the case one `run.json` with a **`testPoints[]` array**; each entry carries its own point-level
fields (`ado.testPointId`/`configuration`/`buildSource`, `device`, `app`, `account`, `steps`, `evidence`,
`blockers`, `verdict`) and references its screenshots by a point-scoped relative path (e.g.
`ecs/iter1/07_token.png`, `local/iter1/07_token.png`). `render` emits one section per test point and derives
the overall verdict from the points. Any **"Proposed test steps"** you add live at **case level** and must be
**generic across every test point** (don't mention ECS/Local or a specific point); give each proposed step an
optional `attachment` (a screenshot path/URL) to fill the report's **Attachments** column. See
[references/test-reporting.md](references/test-reporting.md)
for the JSON schema and a worked example. (For an automation-test run, also attach the
`build/reports/androidTests/` output.) Present the verdict and the report paths to the user; optionally
publish the outcome back to the ADO Test Run.

**When you ran a batch (multiple ADO cases), also generate the overall run summary** — in addition to each
per-case report — so the user gets one roll-up verdict:
```powershell
./scripts/report.ps1 summary -In <suiteFolder> -Title "<suite>"   # SUMMARY.html + SUMMARY.md: per-case table + counts
```

**Do not tear down accounts or registrations after a case (unless told otherwise).** Leave temp users,
device/WPJ registrations, and installed apps as they are — the *next* run's clean-state step (Phase 3
uninstall+reinstall) removes them, or a tester handles them manually. This keeps a failed/blocked case's
state available for inspection and avoids racing another lane (temp lab users self-destruct in ~60 min
anyway). The only required end-of-case teardown is **releasing the device lease** (below) and **reverting any
temporary code/flag/mocks** (Guardrails).

**Release the device lease** so the next test can use it (do this even on failure/blocked):
```powershell
./scripts/devicelease.ps1 release -Owner $AgentId -Serial $serial
```

## When to ask the user

Ask (don't guess) when:
- The **scenario/intent is unclear**, or several flows could be meant, or success can't be defined.
- A **blocker** needs a human — but only *after* you've tried the on-device/Settings path yourself (Phase 4):
  real push-notification MFA on another device, SMS/phone OTP, a hardware key/NFC/QR/camera, CAPTCHA, a
  **secure native system dialog** (Knox cert install, keyguard/biometric prompt), a credential the AI doesn't
  have, or a tenant/CA policy it can't provision.
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
| [references/lab-api.md](references/lab-api.md) | Provisioning/resetting a lab test account, the account policy (prefer fresh ID4SLAB2 temp users over MSIDLAB4), LAB API endpoints/usertypes/policies, the EasyAuth auth workaround, fetching tenant passwords from Key Vault, `labapi.ps1` |
| [references/common-blockers.md](references/common-blockers.md) | Recurring hiccups & when to switch to an emulator (fingerprint/App-Lock, number-match MFA, session timeouts, FLAG_SECURE, autofill/passkey overlay, screenshot corruption); driving System Settings yourself before declaring a blocker; clean-state between runs |
| [references/test-reporting.md](references/test-reporting.md) | Writing the mandatory ADO test report — run-JSON schema, `report.ps1`, worked example |
| [references/run-speed.md](references/run-speed.md) | The run feels slow; understanding per-step latency sources and how to shorten them |
| [references/emulator-performance.md](references/emulator-performance.md) | The run is slow, you're on a Cloud PC/VM/RDP (software rendering), or you want the emulator to show in Android Studio's Device Manager / Running Devices |
| [references/log-signals.md](references/log-signals.md) | Interpreting logcat, success/failure patterns, AADSTS codes, per-flow pass criteria, eSTS correlation |
| [references/ui-interaction.md](references/ui-interaction.md) | Driving auth screens, AI-vs-human inputs, FLAG_SECURE gotcha, selector strategy |
| [references/mocking-flights-and-segments.md](references/mocking-flights-and-segments.md) | A flag must be set, a dependency/server data is unavailable, or the flow can't run fully E2E (mock it or test in segments) |
| [references/secrets-and-files.md](references/secrets-and-files.md) | The user must hand you a password / device PIN / keystore password, or an APK / test file — keep secrets out of the chat (`secrets.ps1`, `labapi.ps1 fetch-password`, `-SecretRef`, `unlock`) and use a file drop-folder |
| [references/troubleshooting.md](references/troubleshooting.md) | Emulator/build/install/uiautomator/broker failures; env-vs-defect triage |

## Guardrails

- **Never** print, log, or commit real or lab credentials, tokens, or secrets. Type them onto the device
  only — use `input-text -SecretRef <name>` (or `-Text ... -Secret`) so the value never hits the
  transcript. When the user needs to hand you a password or device PIN, take it via the encrypted store
  (`secrets.ps1 set`) and reference it by name; for **shared lab-tenant passwords** pull them straight
  from Key Vault with `labapi.ps1 fetch-password` (no paste needed); for APKs/files use a drop-folder path.
  See [references/secrets-and-files.md](references/secrets-and-files.md).
- **Artifacts stay outside the repo** (the run folder). Never commit logs/screenshots/reports.
- **For an ADO test case, always generate the HTML + Markdown report** (`report.ps1`) on every outcome —
  PASS, FAIL, BLOCKED, or PARTIAL. A run driven from a test case is not "done" until the report exists.
- **Run every test point of a case, and match the build to its configuration.** A Broker-suite case may have
  two test points; run each once. Install from **`Local\`** when the point's configuration name contains
  `LocalFlights`, from **`ECS\`** otherwise (⚠️ not the other way round). Skip a point only if the case body
  says it's ECS-only or Local-only. Record `configuration`/`buildSource` in each run's report.
- **A batch of ADO cases also needs the overall `report.ps1 summary`** over the batch folder, not just the
  per-case reports.
- **Split a multi-case batch across available devices and run in parallel** (one sub-agent per case, each
  leasing its own device); fall back to serial only when a single device is free. See "Running multiple test
  cases".
- **Start each case clean; don't clean up after.** Unless told otherwise, uninstall+reinstall the test apps
  at the **start** of every case (a `pm clear` doesn't remove work accounts/registrations), and **do not**
  tear down accounts/registrations at the **end** — leave them for the next run or a tester.
- **Drive System Settings / on-device UI yourself before declaring a blocker** (advance the clock, delete a
  cert, toggle a setting) — only block on a genuinely secure/native dialog or a true root requirement.
- **Prefer fresh ID4SLAB2 temp accounts, one per case** — use `create-user -UserType <matching>` even when a
  case names a deprecated MSIDLAB4 account, unless a specific durable account is required.
- **Don't reset/change a password on an auth failure unless the test case says so.** "Incorrect password" or
  "password expired" on the sign-in screen means re-fetch the value (`fetch-password` — it may have rotated) and
  re-check the UPN/tenant, **not** `reset -Operation password`. Never change the password of a shared durable
  account (a durable, pre-created account, not a temp `Locked_…` user) — it breaks other tests; if the value is right and it still fails,
  mark the run **BLOCKED** with the exact error.
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
