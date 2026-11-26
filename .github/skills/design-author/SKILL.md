---
name: design-author
description: Start or continue Software Factory's guarded Android feature design. Use for "design this feature", "create a design spec", "write a design doc", or preparing a feature for design review.
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Software Factory Design

Follow `.github/agents/software-factory.agent.md`. Software Factory is the single
feature lifecycle; this skill is an entry point, not a phase coordinator.

1. Read the actual host capabilities and resolve the stable feature ID, or save
   the new brief with `software_factory_create_feature`.
2. Review requirements and engineering design tasks. Save chosen criteria and
   explicitly publish to an existing ADO Feature, then obtain genuine approval
   of the exact shared scope with the requirements approval tool.
3. Preview `start_workflow` with `workflow: "design"` and obtain the separate
   execution confirmation. Continue only through the owning native run.
4. The design run ends before approval. Present its saved ADO-backed artifact and
   genuine design approval gate. Approval allows a separate planning run, not resume.

Never manually launch a research/design agent, write a local spec as the canonical
artifact, open an ADO design PR, or infer approval from chat. Report missing host
capabilities instead of falling back to another orchestrator.

The workflow's workers use [design standards](references/design-standards.md).
Those standards are domain guidance, not an alternative execution workflow.

## Mandatory Design Contracts

Software Factory's design workflow enforces these required Android-complete
contracts alongside `references/design-standards.md`:

### Human design approval surface

For the Android-complete Dev Design Dynamic Workflow, generate and publish a
separate **Human Design Review Brief** alongside the authoritative detailed design
for the same revision. Use the
[paired-document contract](../../extensions/DevDesignDoc/README.md#primary-human-design-review-brief-and-paired-publication):
decisions needed first, customer outcome/scope, plain-language architecture/flow,
recommendations/alternatives/tradeoffs, evidence versus assumptions, consequential
risks/safeguards, and accountable owners/blockers. The brief is the primary human
approval surface; reviewers drill into linked detail rather than reading the entire
spec. Preserve the full specification and existing gates.

Independently check consistency and completeness; target ten minutes within the
complete rendered 1,800-word / 16,000-character budget without silently omitting
critical information. Publish both artifacts together and bind human approval to
their exact paired revision. Regenerate and re-review both after substantive edits
and highlight changes since the verified prior human review; never reuse stale
approval. Design approval is not rollout permission. This human brief is distinct
from the design-time pre-mortem authoring brief and the later post-implementation
pre-mortem.

### Required default-off feature flags

Every runtime feature, including small and single-repo features, needs the
[default-off contract](../feature-planner/references/pbi-template.md#required-default-off-feature-contract).
Retain a **Feature flags and isolation** section: source-verified owning framework,
flag key/owner, Boolean `false` code/configuration-failure/production defaults,
all guarded entry points, zero new off-path side effects, override/cache behavior,
approved enabled E2E cohort, rollback including active attempts, and owned
verification. Map each runtime change to its flag(s); documentation,
regression-only, or deferred work needs an explicit no-runtime-impact reason.
Carry the contract into coding PBI acceptance criteria. Do not invent APIs or
inspect/change live production configuration; design approval is not code
verification or activation permission.

### Required pre-mortem and PBI linkage

For every Android-complete feature, including non-runtime features, add a
**Pre-mortem and operational readiness** section containing a design-time authoring
brief, not the pre-mortem draft. Capture feature-specific requirements/source
evidence, known risks, telemetry/rollback inputs, open gaps, and proposed owners
for the later authoring PBI. Follow the content and acceptance contract in
[Required Feature Pre-mortem PBI](../feature-planner/references/pbi-template.md#required-feature-pre-mortem-pbi):
plan coverage of failure scenarios/customer impact; prevention/detection/response;
monitoring signals and later KQL drafts with contracts, cohorts, denominators, and
coverage gaps; monitoring owner/cadence/stop criteria; rollback including active
attempts; and validation and release prerequisites with owners.

Seed **Healthy operation and failure detection** with feature-specific customer
completion and known measurement inputs. Inline the linked contract's requirement
for post-implementation happy-path/failure scenarios and actual scenario-mapped
Kusto drafts: eligibility/exposure, attempts/completions, cancellation/abandonment,
latency, denominators/cohorts, missing telemetry, and owned investigation/stop/
rollback actions. API success or absence of errors alone does not establish
customer success; preserve coverage gaps and per-measure non-runtime applicability
with alternative verification.

Include a **Pre-mortem child PBI** field (`Pending feature planning` before
backlog creation, or the existing AB# ID and durable URL when available) and record
**Pre-mortem artifact: Pending implementation completion and PBI dispatch**.
Feature planning includes (or reuses) a dedicated child PBI dependent on all
feature implementation PBIs. Only after all feature implementation is complete may
that PBI be dispatched for agent drafting against implemented, pinned sources,
followed by named human review before release/production enablement. Agent
completion or design approval is not pre-mortem authoring/review acceptance.
Separate document review from query validation (against an approved enabled E2E
test cohort) and release approval.

### Accessibility design (required)

Retain an **Accessibility design** section in every spec, including when an
external template lacks it. For new or changed Authenticator UI, map the stable
requirement IDs from `docs/Templates/requirements-template.md` section 3.6 and
section 4 to concrete design decisions. For features without affected
Authenticator UI, retain the section and mark it N/A with a reason.

Use Authenticator's `.github/skills/android-ui-development/SKILL.md` and
`.github/skills/android-ui-development/references/accessibility.md` in the owning
checkout as mandatory baselines. Record missing access as an evidence gap.
Resolve only ambiguous feature-specific behavior with the owner; do not reconfirm
established standards as optional choices or invent missing requirements.

Cover TalkBack (localized names, roles, states, headings, dynamic values,
nonduplicated semantics, reading order, focus placement/announcements/restoration),
Keyboard/D-pad reachability and visible focus from entry without touch, text
scaling/reflow across font/display sizes, short viewports, landscape, and
light/dark themes, at least 48dp by 48dp touch targets for every interactive
control, and contrast/non-color cues across applicable loading, error, success,
cancellation, Back/Close, handoff/return, retry/re-entry, and rotation states
(including feature-on/off paths). Cite inspected revisions for reused components
and include a **Testing strategy** verification matrix referenced from **Rollout
plan**. Missing applicable design coverage blocks readiness; planned runtime
evidence remains explicitly `Unverified` during design, and design approval never
establishes runtime accessibility acceptance or rollout approval.
