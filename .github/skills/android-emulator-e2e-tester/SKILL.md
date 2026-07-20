---
name: android-emulator-e2e-tester
description: "Execute and iterate end-to-end (E2E) tests for an in-development Android Auth feature (MSAL, Broker, Common, ADAL, Authenticator) on an Android emulator. Finds or creates a suitable AVD and reuses a running one or boots it, builds/installs the right test app, drives the UI, and verifies via logcat. Use when asked to 'test this feature on the emulator', 'run the E2E test', 'verify the feature end to end', 'does this work on a device', or 'try the sign-in flow' — or automatically once a feature finishes development and is ready for E2E testing. Discovers what to test from the session's design spec, implementation diff, or user-provided test steps. Auto-performs inputs the AI can handle (typing lab test credentials, tapping buttons, granting permissions, simulating a fingerprint) and asks the user when the intent is unclear or a step is a real blocker (push MFA, hardware, missing credentials, implementation gaps). Checks logs to decide pass/fail and drives a fix-and-retest loop until it passes."
---

# Android Emulator E2E Tester

Take an in-development Android Auth feature and prove it works on a real emulator: provision the
device, deploy the app, run the scenario (auto-handling every input the AI reasonably can), read the
logs to decide pass/fail, and loop fix-and-retest until it passes — or stop and ask when genuinely blocked.

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
| `emulator.ps1` | Emulator/AVD lifecycle | `ensure`, `status`, `list`, `list-images`, `create`, `start` |
| `appcontrol.ps1` | App build/install/state | `build`, `install`, `launch`, `clear`, `uninstall`, `is-installed`, `grant`, `list-apks` |
| `deviceui.ps1` | AI-driven UI I/O | `dump`, `wait-text`, `tap-text`, `tap-desc`, `input-text`, `key`, `finger`, `screenshot`, `current-app` |
| `authlogs.ps1` | Log capture + verdict | `clear`, `scan`, `snapshot`, `grep`, `watch` |

## Workflow

Execute in order. Announce a one-line status at each phase.

### Phase 1 — Determine what to test

Synthesize a concrete E2E scenario from the session context, in priority order:

1. **User-provided test steps** in the session (explicit steps or a scenario the user described).
2. **A test plan** (from the `test-planner` skill or a linked ADO test case).
3. **The design spec** (`design-docs/`) — its acceptance criteria and flows.
4. **The implementation diff** — `git -C <sub-repo> diff` across changed repos to see what actually
   changed and which app/module it affects.

Produce: the **feature summary**, the **target app/module** (see
[references/app-and-module-map.md](references/app-and-module-map.md)), and a **step-by-step scenario**
with an explicit, observable **success criterion** (e.g. "AcquireTokenSilent returns a token; logs show
`executed successfully` with a correlation_id; no crash").

**Ask the user if** the scenario is ambiguous, multiple flows could be meant, or you cannot tell what
"working" looks like. Do not guess a scenario when the intent is unclear.

### Phase 2 — Provision the emulator

Derive requirements from the feature (min API, Google Play services for broker/push flows) — see
"Emulator requirements per feature" in the app-and-module map — then one command finds-or-creates,
reuses-or-starts, and waits for boot:

```powershell
./scripts/emulator.ps1 ensure -RequireGoogleApis -ApiLevel 30 -Wait
# or target a specific AVD: ./scripts/emulator.ps1 ensure -Avd Pixel_7 -Wait
```
Capture the printed `SERIAL=` and use it as `-Serial` everywhere. If provisioning fails, see
[references/troubleshooting.md](references/troubleshooting.md) (missing images, hypervisor, boot hang).

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

Loop per step using [references/ui-interaction.md](references/ui-interaction.md): `wait-text` for the
expected element → act (`tap-text` / `input-text` / `key` / `finger`) → re-`dump` to confirm. Save a
`screenshot` at each major step into the run folder.

**Auto-handle** everything the AI reasonably can: type lab test credentials, tap Next/Sign in/Accept/
Allow/Yes, pick an account, grant permissions, simulate a fingerprint (`finger`), enter a TOTP if the
seed is known, set/enter a device PIN. **Do not** print or commit credentials.

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

## When to ask the user

Ask (don't guess) when:
- The **scenario/intent is unclear**, or several flows could be meant, or success can't be defined.
- A **blocker** needs a human: real push-notification MFA on another device, SMS/phone OTP, a hardware
  key/NFC/QR/camera, CAPTCHA, a credential the AI doesn't have, or a tenant/CA policy it can't provision.
- The **implementation is incomplete** for the path under test (stubs, `TODO`, missing wiring, feature
  flag off with no way to enable) — report the gap instead of forcing a fake pass.
- **Environment setup** is missing and only the user can fix it (Maven creds/PAT, no sub-repo checkout,
  no system image, no lab account).

When you can complete a manual step *with* the user live (e.g. they approve a push), pause, let them do
it, then resume the automated flow.

## References

Load these as needed (don't preload all):

| File | Read it when |
|---|---|
| [references/app-and-module-map.md](references/app-and-module-map.md) | Choosing/deploying the test app, package discovery, broker pairing, credentials, emulator requirements |
| [references/log-signals.md](references/log-signals.md) | Interpreting logcat, success/failure patterns, AADSTS codes, per-flow pass criteria, eSTS correlation |
| [references/ui-interaction.md](references/ui-interaction.md) | Driving auth screens, AI-vs-human inputs, FLAG_SECURE gotcha, selector strategy |
| [references/troubleshooting.md](references/troubleshooting.md) | Emulator/build/install/uiautomator/broker failures; env-vs-defect triage |

## Guardrails

- **Never** print, log, or commit real or lab credentials, tokens, or secrets. Type them onto the device only.
- **Artifacts stay outside the repo** (the run folder). Never commit logs/screenshots.
- **Environment problems are not code defects** — fix setup and re-run; only hand real defects to the fix loop.
- **Cap the loop** (3–5 iterations) and escalate with evidence rather than looping indefinitely.
- **Require a positive success signal** matching the scenario before declaring PASS.
- Follow repo conventions when fixing code (Kotlin for new code, the `Logger` class, minimal changes).
