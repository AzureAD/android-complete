# Test Reporting (mandatory for ADO test cases)

Every run driven from an **Azure DevOps test case** must end with a written report — on **every** outcome
(PASS, FAIL, BLOCKED, PARTIAL), not just success. Generate it with `scripts/report.ps1`, which renders a
polished **`TestReport.html`** plus a **`TestReport.md`** from a small run-metadata JSON. This is Phase 7
of the [SKILL](../SKILL.md) and a hard guardrail.

Table of contents:
- [Why mandatory](#why-mandatory)
- [How it works](#how-it-works)
- [Run-JSON schema](#run-json-schema)
- [Multiple test points — one consolidated report per case](#multiple-test-points--one-consolidated-report-per-case)
- [Proposed test steps (recommendation, not applied to ADO)](#proposed-test-steps-recommendation-not-applied-to-ado)
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
| `ado` | object | `{ testCaseId, planId, suiteId, url, testPointId, configuration, buildSource }`. `configuration` = the test point's config name (e.g. `RC MSAL - RC Broker (LocalFlights)`); `buildSource` = `ECS` or `Local` (which staged folder the app came from). Both surface in the report header and the suite **Config** column. **With a `testPoints[]` array** (see below), keep `testCaseId`/`planId`/`suiteId`/`url` here at **case level** and move `testPointId`/`configuration`/`buildSource` into **each point**. |
| `device` | object | `{ model, serial, os, resolution, type }` (`type`: `physical`/`emulator`). |
| `app` | object | `{ package, version }`. |
| `account` | object | `{ upn, usertype, tenant }` — **UPN only, no password**. |
| `started` / `finished` | string | Timestamps (any readable format). |
| `iterations` | number | How many attempts the fix-loop took. |
| `steps` | array | `{ n, action, expected, result, notes, screenshot }` per step. `result`: PASS/FAIL/BLOCKED/SKIPPED. |
| `evidence` | array of string | Positive success signals (e.g. "account appears in list — 06_list.xml"). |
| `blockers` | array of string | What stopped a full E2E pass. |
| `artifacts` | array of string | Relative paths to logs/screenshots/XML in the run folder. |
| `testPoints` | array | **Multi-point cases.** One entry per ADO test point; each carries its own **point-level** fields (`ado.testPointId`/`configuration`/`buildSource`, `device`, `app`, `account`, `started`/`finished`/`iterations`, `steps`, `evidence`, `blockers`, `artifacts`, `verdict`, `verdictNote`). When present, the top-level `verdict` is optional (**derived** from the points) and top-level `ado` holds only the **case-level** ids. Omit it for a single-point run — the run object itself is the one point (backward compatible). See [Multiple test points](#multiple-test-points--one-consolidated-report-per-case). |
| `proposedScope` | string | **REQUIRED for an ADO case** (see [Proposed test steps](#proposed-test-steps-recommendation-not-applied-to-ado)). One-line scope, e.g. "Full rewrite as 5 steps", "Minor — steps 1–2 only", or `"No change needed"` when the case really is fine as written. |
| `proposedSteps` | array | **REQUIRED for an ADO case** (may be `[]` only when `proposedScope` is `"No change needed"`). Suggested ADO steps: `{ n, action, expected, attachment, automation }`. Rendered **once at case level** as a `# / Action / Expected result / Attachments` table; `attachment` = a screenshot path/URL that fills that step's **Attachments** cell; `automation` renders in a separate skill-only list. Must be **generic across all test points** and **self-contained** (no preconditions block — fold prerequisites into the first steps). |
| `proposedMinimalEdits` | array of string | Optional — smallest high-value wording edits if you'd rather not rewrite the whole case. |
| `skillNotes` | array of string | Optional notes for the e2e-tester skill (rendered separately; not part of the ADO steps). |

## Multiple test points — one consolidated report per case

An ADO test **case** often has more than one **test point** (e.g. an `ECS`-build point and a `LocalFlights`
→ Local-build point). Run **every** point, but produce **ONE report per case** — not a report per point. Put
each point in a **`testPoints[]` array** inside a single case-level `run.json`:

- **Case-level fields** (top of the JSON, shared by all points): `title`, `feature`, `ado` (only
  `testCaseId`/`planId`/`suiteId`/`url`), the optional proposed-steps block, and `skillNotes`.
- **Point-level fields** (inside each `testPoints[]` entry): `verdict` + `verdictNote`, `ado`
  (`testPointId`/`configuration`/`buildSource`), `device`, `app`, `account`, `started`/`finished`/`iterations`,
  `steps`, `evidence`, `blockers`, `artifacts`.
- **Overall verdict** — if you omit the top-level `verdict`, it is derived: `FAIL` if any point failed →
  else `BLOCKED` if any blocked → else `PARTIAL` if any partial/unknown → else `PASS`.

Keep everything relative and zip-portable by giving each point its **own screenshot subfolder** under the case
folder, and referencing screenshots by that relative path:
```
android-e2e-runs\<suite>-<ts>\
  tc497038\                         # one folder per CASE
    run.json                        # case-level, with a testPoints[] array
    TestReport.html  TestReport.md  # ONE report for the whole case
    ecs\iter1\   07_token.png ...   # ECS point's screenshots  → steps use "ecs/iter1/07_token.png"
    local\iter1\ 10_token.png ...   # Local point's screenshots → steps use "local/iter1/10_token.png"
```
`render` emits a **section per test point** (config/build/device + that point's steps/evidence/blockers/
artifacts) followed by a **single shared** proposed-steps section, and the suite `summary` still expands the
array into **one row per point** (all linking to the one report).

## Proposed test steps (recommendation, not applied to ADO)

> **MANDATORY for every ADO test case — every app, every run, every verdict.** This was silently skipped on
> **all 26** Authenticator runs while 39/39 Broker runs had it, purely because the field used to read
> "optional". It is not optional: you have just executed the case step-by-step and are the best-placed
> reviewer it will ever get. If the case genuinely needs no change, say so explicitly with
> `proposedScope: "No change needed"` and `proposedSteps: []` — an empty block is a *conclusion*, silence is
> an *omission*. `report.ps1 render` prints a visible **⚠ MISSING** warning when the block is absent.

Use the proposed-steps block to suggest **clearer wording without editing the ADO test case**. It is
authored **once at case level** and must be **generic across every test point** — never mention ECS/Local or a
specific point. Rendered at the **end** of the report in the ADO **Steps** format:

- `proposedSteps[]` → a `# / Action / Expected result / Attachments` table. Each step is
  `{ n, action, expected, attachment?, automation? }`. It must be **fully self-contained**: fold every
  prerequisite (account creation, clean app install, browser state) into the **first numbered steps** — there
  is no separate "preconditions" block, exactly like the ADO Steps editor.
- `attachment` → fills the **Attachments** cell for that step (a relative screenshot path such as
  `ecs/iter1/07_token.png`, or a URL). Leave it out when there's nothing to link.
- `automation` → a skill-only hint (adb tricks, field ids, gotchas). It is rendered in a **separate**
  "Automation notes" list so the paste-ready ADO steps stay clean.
- `proposedScope` → one line on how big the change is. `proposedMinimalEdits[]` → the smallest high-value edits
  if you'd rather not rewrite the whole case.

## Worked example

This is the **single-point** form (the run object *is* the one point). For a multi-point case, wrap these
point-level fields in a `testPoints[]` array under a case-level header — see
[Multiple test points](#multiple-test-points--one-consolidated-report-per-case).

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
  "account": { "upn": "Locked_xxx@ID4SLab2.onmicrosoft.com", "usertype": "GlobalMFA",
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

**Layout it expects.** Put each **case** in its own `tc<id>\` subfolder of one batch folder, each with its own
`run.json` (and rendered `TestReport.html`). A case with **multiple test points** carries them as a
`testPoints[]` array in that one `run.json` (screenshots in point-scoped subfolders); the summary expands them
into one row per point automatically:
```
android-e2e-runs\<suite>-<yyyyMMdd_HHmmss>\
  tc831570\   run.json  TestReport.html  TestReport.md  ecs\iter1\...  local\iter1\...  # 2 test points → 2 rows
  tc833550\   run.json  TestReport.html  TestReport.md  iter1\...                        # single test point → 1 row
  ...
```
(Legacy per-point subfolders `tc<id>-<local|ecs>\` — one `run.json` each — are still scanned and still render
as separate rows, so older batches keep working.)

**Render it** — point `summary` at the batch folder (it recurses for every `run.json`):
```powershell
./scripts/report.ps1 summary -In C:\Users\<you>\android-e2e-runs\<suite>-<ts> -Title "<suite name>"
# → writes SUMMARY.html + SUMMARY.md into that folder
```
The summary reads each run's `title`, `verdict`, `verdictNote`, `ado.testCaseId`, `device.serial`, and
`ado.configuration`/`ado.buildSource`, and links each row to that run's `TestReport.html`. **For a case with a
`testPoints[]` array it emits one row per point** — reading that point's `verdict`/`configuration`/`buildSource`/
`device.serial`, all linked to the case's single `TestReport.html`. It adds a **Config** column (e.g.
`ECS — RC MSAL - RC Broker`) so a case's two test points are easy to tell apart. Rows are ordered
**problems-first** (FAIL → BLOCKED → PARTIAL → PASS), then by test-case id, then ECS-before-Local. The
**overall** verdict is `FAIL` if any run failed, `PASS` if all passed, else `PARTIAL`, shown with per-verdict
counts (each test point counts as its own row). No extra schema is needed — it reuses the per-case run-JSON
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
