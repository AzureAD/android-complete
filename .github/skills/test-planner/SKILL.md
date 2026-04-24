---
name: test-planner
description: "Create, manage, and export E2E test plans for Android Auth features. Use this skill when asked to: write test cases, create a test plan, add tests to ADO, export tests as a document, review existing test plans, or create manual test cases for sign-off. Triggers include 'write test cases for', 'create a test plan', 'add tests to ADO', 'export test plan', 'create E2E tests for', or any request to produce manual test cases for an Android Auth feature."
---

# Test Planner

Create comprehensive E2E test plans for Android Auth features — from discovery through ADO creation to shareable exports.

## Workflow Overview

The skill operates in 4 phases. Execute only the phases the user requests:

- **Full test plan**: Run all 4 phases
- **Single test case**: Skip Phase 1 (unless format unknown), run Phase 2 for just the requested scenario, optionally Phase 3/4
- **Export only**: Skip to Phase 4
- **ADO publish only**: Skip to Phase 3 with provided test cases

```
Phase 1: Discover  →  Phase 2: Author  →  Phase 3: Publish  →  Phase 4: Export
(ADO research)        (draft test cases)   (create in ADO)      (Word/PDF doc)
```

## Phase 1: Discover Existing Patterns

**Goal:** Understand the team's test case format, naming conventions, and step granularity before authoring new tests.

1. Query the master test plan (ID: `2007357`) and monthly release plan (ID: `3504766`) to find relevant suites
2. Read 2-3 existing test cases from related suites (e.g., Broker, MDM, MAM) to learn:
   - Step granularity (how explicit steps are)
   - Action vs Validate step patterns
   - Tag conventions
   - Naming conventions (e.g., `[Joined][MDM] description`)
3. Note the parent suite ID for "Manual Tests (Android Broker)" in both plans:
   - Master plan: suite `2008656`
   - Monthly plan: suite `3504780`

**ADO API patterns:** See [references/ado-test-api.md](references/ado-test-api.md) for API calls.

## Phase 2: Author Test Cases

**Goal:** Draft focused, explicit test cases matching the team's format.

### Guidelines

- **Target 10-15 test cases** covering core scenarios. Don't over-test.
- **Use real web apps** (e.g., `outlook.com`, `portal.office.com`) instead of raw login URLs.
- **Clean state = uninstall broker apps + clear Chrome cookies**, not factory reset.
- **SSO experience = account picker → tap account → signed in** (not "automatically signed in").
- **Steps should be explicit** — spell out what to tap, what to navigate to, what to observe.
- **Each step has an Action and Expected Result.** Use "ActionStep" for setup steps (no validation needed) and "ValidateStep" for steps with observable outcomes.
- **Tags:** Include `Android; Broker; FeatureName` plus scenario-specific tags (MFA, MDM, MAM, SDM, etc.)
- **Naming:** `[Browser SSO] Description` or `[Feature][Scenario] Description`

### Prioritize These Scenario Categories

For any broker feature, cover (in order of priority):

1. **Happy path** (basic single-account flow) — P0
2. **Multi-account** — P0
3. **No accounts / empty state** — P1
4. **MFA account** — P0
5. **MDM enrolled device** — P1
6. **TrueMAM (app protection)** — P1
7. **Work Profile** — P1
8. **Shared Device Mode (sign-in + sign-out)** — P1
9. **Sovereign cloud (GCC-H)** — P2
10. **Negative: non-allowed caller** — P1
11. **Negative: feature disabled / flight off** — P1
12. **Lifecycle: account removal** — P1

Not every feature needs all categories. Use judgment based on what the feature touches.

For **Authenticator app features**, also consider:
- **MFA push approval** (accept/deny notification) — P0
- **Passwordless sign-in** (NGC key registration, phone sign-in) — P0
- **Passkey/FIDO2** (registration, assertion, cross-device) — P0
- **QR code scanning** — P1
- **Verified ID** (credential issuance, presentation) — P1
- **Account management** (add/remove accounts, MSA + AAD) — P1
- **Device policy** (rooted device detection, conditional access) — P1
- **Biometric/PIN unlock** — P1

### Get Feature Context

If the user hasn't provided feature details, gather them by:
- Reading the design spec (check `design-docs/` folder)
- Researching the codebase (check implementation files)
- Asking the user for clarification

## Phase 3: Publish to ADO

**Goal:** Create a test suite and test cases in ADO under the master test plan.

1. Create a new static test suite under "Manual Tests (Android Broker)" in the master plan
2. Create Test Case work items with proper steps XML, tags, and area path
3. Add test cases to the suite

**API details and XML format:** See [references/ado-test-api.md](references/ado-test-api.md).

**Important:**
- Area path: `Engineering\Auth Client\Broker\Android`
- Master plan ID: `2007357`, parent suite "Manual Tests (Android Broker)": `2008656`
- Use `System.Web.HttpUtility.HtmlEncode` for step text in XML

## Phase 4: Export as Document

**Goal:** Produce a polished, shareable HTML document (openable in Word, printable to PDF).

Use the HTML template at [assets/test-plan-template.html](assets/test-plan-template.html). The template provides:
- Professional styling with Microsoft branding colors
- Properly formatted step tables
- Priority tags and scenario labels
- Overview section, prerequisites, allowed domains/browsers reference
- Print-friendly layout

To use: read the template, replace the placeholder sections with actual test case content, and save as `.html` in the workspace root.
