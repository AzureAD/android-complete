<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->
# Requirements: [Feature name]

Template version: 3
Owner: [Person/team]
Feature/work-item link: [Link]
Status: Draft
Target milestone: [Date/release, or Unknown]

## How to use this template

Use this shared template for every feature's requirements, including UI and
non-UI features. Mark non-applicable sections N/A with a reason rather than
omitting them.

Describe what must happen, what must not change, and known constraints.
The design process should propose how to implement the feature, including
architecture, API options, tradeoffs, and a detailed test plan.

Start with sections 1-4 and any non-negotiable constraints. Write **Unknown**
rather than guessing, and mark genuinely irrelevant sections **N/A**.
Keep confirmed requirements separate from assumptions and proposed solutions.
For features with user interaction, complete the journey and UX evidence in
section 3 before handing requirements to the design author. A happy-path summary
or a Figma link alone is not a complete flow. For non-UI features, mark the visual
subsections N/A with a reason and describe the caller journey instead.
For new or changed Authenticator UI, including reused screens and changed
navigation flows, complete the required accessibility section 3.6. Existing
accessibility behavior alone is not an acceptance specification.

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

## 3. User and caller scenarios and complete flow

### 3.1 Scenarios

| ID | Starting condition / action | Required observable result | Flow / evidence IDs |
|---|---|---|---|
| S1 | [Primary successful scenario] | [Expected outcome] | [FLOW-1, UX-1] |
| S2 | [User cancels or declines] | [Expected outcome] | [Flow/source IDs] |
| S3 | [Required component/service is unavailable] | [Error or fallback behavior] | [Flow/source IDs] |
| S4 | [Existing user upgrades or migrates] | [What is preserved or changed] | [Flow/source IDs] |

[Add other relevant scenarios. Do not invent behavior that needs a decision.]

### 3.2 UX sources and inspection status

Provide the full relevant flow, not only an entry screen or selected happy-path
screens. Use an accessible Figma link with page/frame or prototype node IDs, or
a reviewable export/attachment. Keep private assets in their authorized location;
do not copy them into a public repository to make the template self-contained.

| Source ID | Link or attachment location | Version / snapshot identity | Relevant pages / frames | Inspected content and limits |
|---|---|---|---|---|
| UX-1 | [Figma node/prototype URL, or exported file location] | [Version ID; for an export, timestamp and hash if available] | [Stable IDs, not names alone] | [What was actually inspected; inaccessible content or ambiguous connections] |

A linked source is not proof that its contents were inspected. If access or
rendering is unavailable, obtain a reviewable export or owner-confirmed textual
flow before claiming coverage. Record source gaps as open questions. An export
node ID is not a published URL. Illustrative accounts, challenge values, and
sample text must not become production requirements.

### 3.3 Screen and state inventory

Include app screens, relevant system/service handoffs, and result states. Identify
reused versus new screens without prescribing implementation classes.

| Screen / state ID | Name and user-visible purpose | Owner and new / existing | Visible actions | Source frame / evidence | Scenario / requirement IDs |
|---|---|---|---|---|---|
| SCREEN-1 | [Entry screen or state] | [App/System/Service; New/Existing/Unknown] | [Exact button/link labels; Back, Close, dismiss where applicable] | [UX-1 and frame/node ID, or confirmed product decision] | [S1; REQ-1] |

### 3.4 End-to-end transitions and outcomes

Describe the journey from each supported entry point through intermediate screens
and external handoffs to the final observable outcome. Include explicit button
presses, conditions, and branches; a generic "user continues" is insufficient.

| Flow ID | Scenario IDs | From screen / state | Trigger / button press | Condition | Destination and observable state / result | Ownership and evidence |
|---|---|---|---|---|---|---|
| FLOW-1 | S1 | SCREEN-1 | [Exact action label, or automatic event] | [Eligibility, flag, account/device state] | [Next screen/state; selected item/data changes; terminal outcome if complete] | [App contract or illustrative external step; UX/source/connector ID or confirmed decision] |

Cover applicable success, failure, cancellation, Back/Close/dismiss, alternate
methods, retry/re-entry, and disabled/ineligible branches. Specify what remains
unchanged or partial after exit and when the journey is actually complete.
An intermediate success or an external sheet closing is not automatically success
for the overall journey.

Include a flow diagram when it clarifies branching, using these same IDs and
action labels. Keep the table, diagram, scenarios, and acceptance criteria
consistent. Identify observed connectors separately from spatially inferred
destinations or proposed transitions; seek confirmation for ambiguous routing.
Do not silently fill missing arrows. For externally owned screens, distinguish
illustrative examples from guaranteed app handoff/return behavior; do not mandate
an exact challenge order or available method list without an agreed contract.

### 3.5 Flow completeness and reconciliation

- [ ] Every in-scope scenario maps to a flow and an observable outcome.
- [ ] Relevant screens, explicit actions, conditions, and exit/retry branches are
  covered, or individually recorded as Unknown/N/A with a reason.
- [ ] UX sources were inspected; missing access, ambiguous connectors, and conflicts
  between text and visuals have an owner and a recorded resolution or open question.
- [ ] App-owned requirements are distinguished from illustrative external behavior.
- [ ] The authoritative UX revision and any superseded material are identified.

[Record who confirmed the reconciled product flow and the confirmation reference,
or Unknown. A supplied Figma file is input, not evidence of requirements or final
design approval. Missing behavior that changes the design is a blocking question;
do not treat this checklist as approval or defer it silently to implementation.]

### 3.6 Accessibility requirements and evidence (required)

Complete this section for every new or changed Authenticator UI feature, including
reused screens, entry points, and navigation flows. For features without affected
Authenticator UI, retain the section and mark it N/A with a reason.

Use Authenticator's `.github/skills/android-ui-development/SKILL.md` and
`.github/skills/android-ui-development/references/accessibility.md` in the owning
checkout as the baseline. Established accessibility standards are mandatory
defaults, not optional product choices to reconfirm. Ask questions only about
ambiguous feature-specific behavior; record decisions and unresolved questions
in section 8. Do not invent behavior or silently defer design-blocking questions.

Describe observable requirements, not implementation classes or modifiers.
Map each applicable row to the screens, scenarios, flows, and stable requirement
IDs in section 4. "Preserve existing accessibility" or reuse of a shared component
does not replace feature-specific acceptance criteria.

| Area | Feature-specific requirements to capture | Scenario / screen / flow and requirement IDs |
|---|---|---|
| TalkBack | [Logical forward/backward reading order; true headings; localized names, roles, states, and required announcements on focused nodes; nonduplicated semantics; full accessible dynamic values; actual Links navigation where links exist.] | [S1; SCREEN-1; FLOW-1; REQ-ID] |
| Keyboard / D-pad | [Reach every control from screen entry without touch or prior navigation in another mode; logical focus order, visible focus, activation, cancellation/exit, and return behavior.] | [Applicable IDs] |
| Large text and layout | [Readable, reachable instructions and actions at large font/display size, short viewports, landscape, and light/dark themes; reflow without clipping required content.] | [Applicable IDs] |
| Touch targets | [All interactive controls retain at least 48dp by 48dp targets, including Back/Close and secondary actions.] | [Applicable IDs] |
| Other applicable interactions | [Accessible errors, loading/selection changes, destructive confirmations, or non-drag alternatives; mark individual concerns N/A with a reason.] | [Applicable IDs] |

Cover each supported entry point and applicable success, error, cancellation,
retry, restoration/rotation, and feature-on/off path. Distinguish app-owned
behavior from external system/service UI; record required handoff/return checks
without inventing external guarantees.

#### Acceptance ownership and evidence

| Requirement / scenario IDs | Runtime check and configurations | Test owner / reviewer | Evidence location and status / gap |
|---|---|---|---|
| [Applicable IDs] | [Entry steps, actions, expected results; device/Android, theme/font/display settings, feature/cohort configuration, TalkBack or keyboard setup.] | [Confirmed people/team, or Unknown] | [Planned evidence location; Passed with evidence / Unverified / Blocked with reason and follow-up owner / N/A with rationale.] |

Plan real-app TalkBack verification and separate keyboard-only verification from
screen entry. Record the tested build/commit, device/emulator and Android version,
TalkBack version/settings or keyboard/input setup, steps, and observed outcomes
with reviewable evidence. Reuse valid evidence only when its revision and scenario
coverage apply. Keep private recordings and sensitive values in authorized locations.

Compilation, screenshots, semantics assertions, navigation tests, and tests that
force focus onto a control are supporting evidence, not proof of end-to-end
accessibility. Runtime evidence may be planned and Unverified during requirements
authoring; missing device/service access must be recorded as a gap, not a pass.
Carry these requirements, owners, and evidence statuses into the developer design
and PBI acceptance matrix. Document approval does not establish accessibility
acceptance or rollout approval. Record any accepted deferral's decision owner,
rationale, tracking item, and release consequence; do not count it as passed.

## 4. Requirements and acceptance criteria

Use **MUST** for release-blocking requirements and **SHOULD** for desired behavior.
Give each requirement a stable ID so the design and tests can reference it.
For user-visible behavior, reference the scenario/flow IDs and relevant UX source
frames or an explicit owner decision. Include intermediate routing and terminal
state expectations, not only the final happy-path screen.
Include applicable accessibility requirements from section 3.6 with measurable
acceptance criteria and traceability to their scenarios and evidence plan.

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
- Authoritative UX flow: [Section 3 source IDs and exact revision/export]
- Superseded UX/mockups: [Links and what supersedes them, or N/A]
- Confirmed cross-team agreements: [Links]
- Existing behavior/contracts: [Links]
- Historical designs for context only: [Links]

### Assumptions requiring confirmation

- [Assumption, not yet an agreed requirement.]
