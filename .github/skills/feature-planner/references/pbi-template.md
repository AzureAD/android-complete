<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# PBI Description Template

Use the following Markdown sections inside each planned item's `description`.
Keep item metadata, requirement IDs, dependencies, files, and tests in the
caller's structured fields as well. The provider handles remote formatting.
No HTML envelope, summary-table parser, or existing AB# IDs are required.

## Objective

Describe the behavior to implement, affected module, and expected outcome.

## Target Repository

Use the supplied repository identity and configured base branch. Specify modules.

## Context

Explain the problem and inline the design decisions needed to implement the
item without another checkout, a local spec file, or conversation history.

## Technical Requirements

Give APIs/contracts, relevant existing patterns, error handling, compatibility,
security/privacy, telemetry, and flag/rollout requirements from the design.

## Scope

State inclusions and exclusions. Preserve approved caller/platform boundaries.

## Files and Tests

List known repository-relative source and test paths. Specify concrete behavior,
failure, compatibility, and disabled-path cases plus appropriate build/test commands.

## Acceptance Criteria

Use verifiable criteria tied to the approved requirement IDs. Include necessary
test coverage and avoid unsupported quantitative targets.

## Dependencies and Coordination

Use the plan's local item IDs and explain what each dependency supplies. Include
cross-team notifications or integration work. Use "None" when independent.
