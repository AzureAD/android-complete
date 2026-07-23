# Test Reporting (mandatory for ADO test cases)

Every run driven from an **Azure DevOps test case** must end with a written report — on **every** outcome
(PASS, FAIL, BLOCKED, PARTIAL), not just success. Generate it with `scripts/report.ps1`, which renders a
polished **`TestReport.html`** plus a **`TestReport.md`** from a small run-metadata JSON. This is Phase 7
of the [SKILL](../SKILL.md) and a hard guardrail.

Table of contents:
- [Why mandatory](#why-mandatory)
- [How it works](#how-it-works)
- [Run-JSON schema](#run-json-schema)
- [Worked example](#worked-example)
- [Rendering the report](#rendering-the-report)
- [Suite report — multiple test cases](#suite-report--multiple-test-cases)
- [Rules](#rules)

## Why mandatory

- ADO test execution needs an auditable artifact you can attach to the test run / share with the team.
- A **BLOCKED** or **PARTIAL** outcome is still a result — the report is how you communicate *what* was
  verified, *where* it stopped, and *why* (env constraint vs product defect). Skipping the report on a
  non-PASS hides exactly the information the team needs.
- It forces you to collect evidence (screenshots, XML dumps, logcat scans) as you go instead of
  reconstructing them afterward.

## How it works

`report.ps1 render -In <run.json>` reads a JSON file describing the run and writes `TestReport.html` and
`TestReport.md` next to it (or to `-OutDir`). The renderer is **defensive**: any missing field is simply
omitted, so a half-finished run still produces a valid report. Build the JSON incrementally during the run
(append to the `steps` array as you complete each step) so that even an early failure has a report to emit.

## Run-JSON schema

All fields are optional **except `title` and `verdict`**. Never put passwords/tokens in the JSON — include
the account **UPN only**.

| Field | Type | Notes |
|---|---|---|
| `title` | string | **Required.** Test-case title. |
| `verdict` | string | **Required.** `PASS` \| `FAIL` \| `BLOCKED` \| `PARTIAL`. Drives the colored banner. |
| `verdictNote` | string | One-line justification (esp. for BLOCKED/PARTIAL — say if it's an env constraint). |
| `feature` | string | Human name of the flow under test. |
| `ado` | object | `{ testCaseId, planId, suiteId, url, testPointId, configuration, buildSource }`. `configuration` = the test point's config name (e.g. `RC MSAL - RC Broker (LocalFlights)`); `buildSource` = `ECS` or `Local` (which staged folder the app came from). Both surface in the report header and the suite **Config** column. |
| `device` | object | `{ model, serial, os, resolution, type }` (`type`: `physical`/`emulator`). |
| `app` | object | `{ package, version }`. |
| `account` | object | `{ upn, usertype, tenant }` — **UPN only, no password**. |
| `started` / `finished` | string | Timestamps (any readable format). |
| `iterations` | number | How many attempts the fix-loop took. |
| `steps` | array | `{ n, action, expected, result, notes, screenshot }` per step. `result`: PASS/FAIL/BLOCKED/SKIPPED. |
| `evidence` | array of string | Positive success signals (e.g. "account appears in list — 06_list.xml"). |
| `blockers` | array of string | What stopped a full E2E pass. |
| `artifacts` | array of string | Relative paths to logs/screenshots/XML in the run folder. |

## Worked example

`run.json` (mirrors the AAD MFA sign-in run):
```json
{
  "title": "Register AAD MFA cloud account via Sign in flow",
  "verdict": "PARTIAL",
  "verdictNote": "Core objective met (account registered); browser number-match blocked by Authenticator App Lock — an environment constraint on a physical device, not a product defect.",
  "feature": "AAD MFA sign-in + first-time MFA setup",
  "ado": { "testCaseId": 1579381, "planId": 714514, "suiteId": 3503165,
           "url": "https://identitydivision.visualstudio.com/Engineering/_testPlans/define?planId=714514&suiteId=3503165",
           "testPointId": 3150404, "configuration": "RC MSAL - RC Broker", "buildSource": "ECS" },
  "device": { "model": "Samsung SM-F741U1", "serial": "R5CXB0P430X", "os": "Android 16 (SDK 36)",
              "resolution": "1080x2640", "type": "physical" },
  "app": { "package": "com.azure.authenticator", "version": "6.2607.4584" },
  "account": { "upn": "Locked_5b335908a3@ID4SLab2.onmicrosoft.com", "usertype": "GlobalMFA",
               "tenant": "ID4SLab2.onmicrosoft.com" },
  "started": "2026-07-21 18:45", "finished": "2026-07-21 19:05", "iterations": 1,
  "steps": [
    { "n": 1, "action": "Launch Authenticator, accept first-run", "expected": "First-run/privacy screen",
      "result": "PASS", "screenshot": "iter1/01_firstrun.png" },
    { "n": 2, "action": "Add account > Work/School > Sign in", "expected": "eSTS sign-in WebView",
      "result": "PASS", "screenshot": "iter1/02_chrome.png" },
    { "n": 3, "action": "Enter UPN + password (char-by-char, secret)", "expected": "Password accepted",
      "result": "PASS", "notes": "bulk input swallowed by autofill; -CharByChar worked",
      "screenshot": "iter1/03_after_signin.png" },
    { "n": 4, "action": "Complete first-time MFA setup wizard", "expected": "Account paired",
      "result": "PASS", "screenshot": "iter1/05_pairing.png" },
    { "n": 5, "action": "Approve browser number-match", "expected": "Number entered in Authenticator",
      "result": "BLOCKED", "notes": "App Lock gates entry behind PIN/biometric — not injectable on a physical device",
      "screenshot": "iter1/08_number_match.png" }
  ],
  "evidence": [ "Account 'Locked_5b33...' appears in Authenticator account list (06_authenticator_accountlist.xml)" ],
  "blockers": [ "Authenticator App Lock requires device biometric/PIN to re-enter and approve the number-match; run this segment on an emulator to inject a fingerprint (see common-blockers.md)." ],
  "artifacts": [ "iter1/01_firstrun.png", "iter1/06_authenticator_accountlist.xml", "iter1/logcat_scan.txt" ]
}
```

## Rendering the report

```powershell
./scripts/report.ps1 render -In C:\Users\<you>\android-e2e-runs\aad-mfa-signin-<ts>\run.json
# → writes TestReport.html and TestReport.md into that run folder
```
Optionally target a different folder with `-OutDir`. Reports live in the **run folder outside the repo** —
never commit them.

## Suite report — multiple test cases

When a **batch** of test cases runs in one session (a suite, a test-point list, several ids), generate an
**overall run summary** in addition to each per-case `TestReport`. It rolls every case up into one
`SUMMARY.html` + `SUMMARY.md` so the user sees a single verdict and a per-case table instead of hunting
through N folders.

**Layout it expects.** Put each run in its own subfolder of one batch folder, each with its own `run.json`
(and rendered `TestReport.html`). When a case has **multiple test points**, give each point its own subfolder
named `tc<id>-<local|ecs>` so both show up as separate rows:
```
android-e2e-runs\<suite>-<yyyyMMdd_HHmmss>\
  tc831570-ecs\    run.json  TestReport.html  TestReport.md  iter1\...   # plain config → ECS build
  tc831570-local\  run.json  TestReport.html  TestReport.md  iter1\...   # LocalFlights config → Local build
  tc833550\        run.json  TestReport.html  TestReport.md  iter1\...   # single test point
  ...
```

**Render it** — point `summary` at the batch folder (it recurses for every `run.json`):
```powershell
./scripts/report.ps1 summary -In C:\Users\<you>\android-e2e-runs\<suite>-<ts> -Title "<suite name>"
# → writes SUMMARY.html + SUMMARY.md into that folder
```
The summary reads each run's `title`, `verdict`, `verdictNote`, `ado.testCaseId`, `device.serial`, and
`ado.configuration`/`ado.buildSource`, and links each row to that run's `TestReport.html`. It adds a **Config**
column (e.g. `ECS — RC MSAL - RC Broker`) so a case's two test points are easy to tell apart. Rows are ordered
**problems-first** (FAIL → BLOCKED → PARTIAL → PASS), then by test-case id, then ECS-before-Local. The
**overall** verdict is `FAIL` if any run failed, `PASS` if all passed, else `PARTIAL`, shown with per-verdict
counts (each test point counts as its own run). No extra schema is needed — it reuses the per-case run-JSON
above; render the per-case reports first (or at least drop each run's `run.json`), then run `summary`.

## Rules

- **Mandatory for ADO cases, every outcome.** No report ⇒ the run isn't done.
- **A batch of ADO cases also needs the overall `summary`** — render each per-case report, then
  `report.ps1 summary -In <batchFolder>` for the roll-up.
- **UPN only, never the password/token** in the JSON or report.
- Distinguish **environment constraint** from **product defect** in `verdictNote`/`blockers` — a BLOCKED
  due to a physical-device biometric gate is not a bug; say so and point at the emulator workaround.
- Keep the run folder self-contained: reference screenshots/XML by **relative path** so the HTML links work
  when the folder is zipped and shared.
