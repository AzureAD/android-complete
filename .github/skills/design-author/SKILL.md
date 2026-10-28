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
