<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->
# Requirements: [Feature name]

Template version: 1
Owner: [Person/team]
Feature/work-item link: [Link]
Status: Draft
Target milestone: [Date/release, or Unknown]

## How to use this template

Describe what must happen, what must not change, and known constraints.
The design process should propose how to implement the feature, including
architecture, API options, tradeoffs, and a detailed test plan.

Start with sections 1-4 and any non-negotiable constraints. Write **Unknown**
rather than guessing, and mark genuinely irrelevant sections **N/A**.
Keep confirmed requirements separate from assumptions and proposed solutions.

## 1. Problem and desired outcome

### Current problem

[Who experiences the problem? What happens today? Why is that insufficient?]

### Desired outcome

[What should users or calling applications be able to accomplish?]

### Success measures

[How will we know the feature is useful and working?
Include known targets; leave unconfirmed numbers as Unknown.]

## 2. Scope

### In scope for the first release

- [Supported scenario, operation, account type, device type, etc.]

### Out of scope

- [Explicit exclusions.]

### Existing behavior that must not regress

- [Existing flows, clients, platforms, or contracts to preserve.]

## 3. User and caller scenarios

| ID | Starting condition / action | Required observable result |
|---|---|---|
| S1 | [Primary successful scenario] | [Expected outcome] |
| S2 | [User cancels or declines] | [Expected outcome] |
| S3 | [Required component/service is unavailable] | [Error or fallback behavior] |
| S4 | [Existing user upgrades or migrates] | [What is preserved or changed] |

[Add other relevant scenarios. Do not invent behavior that needs a decision.]

## 4. Requirements and acceptance criteria

Use **MUST** for release-blocking requirements and **SHOULD** for desired behavior.
Give each requirement a stable ID so the design and tests can reference it.

| ID | Priority | Requirement | Acceptance criterion | Source |
|---|---|---|---|---|
| REQ-1 | MUST | [Required behavior] | Given [condition], when [action], then [observable result]. | [Work item, agreement, or owner decision] |
| REQ-2 | MUST | [Required restriction or non-regression] | [How compliance can be checked] | [Source] |
| REQ-3 | SHOULD | [Desired behavior] | [Observable result] | [Source] |

## 5. Compatibility, dependencies, and responsibility boundaries

- Supported Android versions: [Minimum/range, or Unknown]
- Device/build variants: [GMS/nGMS, managed/unmanaged, etc., if relevant]
- Supported account/user types: [List]
- Affected applications, SDKs, services, or teams: [List]
- Older-client/component behavior: [Required result, or Unknown]
- Missing/incompatible dependency behavior: [Required result, or Unknown]
- External dependencies or agreements: [What another team must provide]
- Fixed responsibility boundaries: [What must remain inside/outside our component]

[Record agreed constraints here, not an architecture the design agent must invent.]

## 6. State, security, privacy, and user interaction

Complete only the applicable rows.

| Condition / concern | Required behavior or restriction |
|---|---|
| Feature disabled or user not eligible | [Existing behavior, fallback, or error] |
| Existing cached/restored/migrated data | [What may be reused, cleared, or require user action] |
| Feature disabled during an operation | [Cancellation, completion, cleanup, or Unknown] |
| Sign-out, account removal, or uninstall | [Required cleanup/preservation] |
| Interactive versus silent operation | [When UI is allowed or forbidden] |
| Caller identity and trusted service/origin | [Required trust restrictions] |
| Sensitive data | [Storage, backup, transfer, and logging restrictions] |

## 7. Operational and release requirements

- Required signals: [How to distinguish eligibility, actual execution, success,
  failure, cancellation, and fallback where applicable.]
- Diagnostic correlation: [What must be traceable across components without
  exposing sensitive data.]
- Performance/reliability constraints: [Known limits or Unknown.]
- Activation and rollout: [Required controls/cohorts, or ask the design agent
  to propose them.]
- Stop and rollback expectations: [What must remain safe and functional if
  rollout is stopped or reversed.]

## 8. Open questions and evidence

### Decisions still needed

| Question | Why it matters | Decision owner | Blocks design or release? |
|---|---|---|---|
| [Unresolved requirement] | [Affected behavior] | [Owner/Unknown] | [Design/Release/Neither/Unknown] |

### Supporting material

- Authoritative requirements: [Links]
- Confirmed cross-team agreements: [Links]
- Existing behavior/contracts: [Links]
- Historical designs for context only: [Links]

### Assumptions requiring confirmation

- [Assumption, not yet an agreed requirement.]
