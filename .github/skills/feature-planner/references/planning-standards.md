<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android Implementation Planning Standards

Use the supplied approved requirements, design/evidence packet, repository
configuration, feedback, and schema. This reference is domain guidance only.

## Repository targeting

The supplied configured repository keys, hosts, accounts, and base branches are
authoritative. These architectural hints do not authorize an unconfigured target:

| Layer | Usual target | Responsibilities |
|---|---|---|
| Common/common4j | microsoft-authentication-library-common-for-android | IPC contracts, shared models, commands, cache, crypto, telemetry primitives, pure JVM shared code |
| MSAL | microsoft-authentication-library-for-android | Client APIs, public configuration, client token acquisition and controllers |
| Broker/broker4j | ad-accounts-for-android | PRT, device registration, broker processing, eSTS, broker IPC entry points, Protobuf and pure JVM broker logic |
| ADAL | azure-activedirectory-library-for-android | Explicitly scoped legacy maintenance/compatibility work only |
| Authenticator | Configured Authenticator target | App-specific MFA, UX, registration, and other app changes |
| 1ES-Pipelines | AuthClientAndroidPipelines | Production pipeline YAML, release/hotfix templates, validation and publishing scripts |

Common changes usually precede consuming MSAL/Broker changes. Do not confuse the
legacy `azure-pipelines/` folder with production `1ES-Pipelines/`.

## PBI contracts

- Each PBI targets exactly one repository. A repository may have multiple PBIs;
  never combine unrelated changes just to produce one item per repository.
- Right-size work for a coding session while keeping coherent behavior and tests
  together. Do not omit necessary files to satisfy an arbitrary line-count cap.
- Cover every approved requirement with its exact ID. Preserve constraints and
  exclusions. Do not expand rollout to excluded caller families or platforms.
- Use stable local item IDs (for example `PBI-1`) for dependency references.
  Never invent AB# IDs. Form an acyclic graph with all dependencies explicit.
- Return self-contained Markdown descriptions using
  `.github/skills/feature-planner/references/pbi-template.md`. Inline essential
  design context: coding agents only have the target repository.
- Give repository-relative source/test paths, never local absolute paths or
  dependencies on `design-docs/` or another checkout being present.
- Include concrete test cases and verification steps for implementation behavior,
  default-off flags, configuration-failure behavior, accessibility verification,
  compatibility, errors, telemetry, concurrency, and recovery as applicable.
- Carry the approved default-off flag and entry-point isolation contract verbatim
  into affected coding PBIs (Boolean `false` defaults, zero disabled-path side
  effects, and rollback coverage); do not invent a flag or impose one when the
  approved design justifies non-runtime scope.
- When the approved design specifies a post-implementation pre-mortem authoring
  brief, include or link the dedicated `documentation-review` pre-mortem child
  PBI depending on all implementation PBIs so full pre-mortem and KQL authoring
  runs against merged, pinned implementation sources before release enablement.
- Include cross-team coordination requirements such as OneAuth API changes.
- Report genuine unresolved scope/product decisions in the supplied fields.
  Do not convert an ordinary implementation detail into an unanswered question.

Return the caller's schema, not a Markdown summary-table envelope or handoff
prompt. Plan output never creates work items or grants implementation authority.
