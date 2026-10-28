// Copyright (c) Microsoft Corporation. All rights reserved.

// Workers read domain guidance, never the user-facing skills that start workflows.
export const workerGuidance = {
    research: [
        ".github/agents/codebase-researcher.agent.md",
        ".github/skills/codebase-researcher/references/research-standards.md",
        "docs/Templates/requirements-template.md",
    ],
    design: [
        ".github/agents/design-writer.agent.md",
        ".github/skills/design-author/references/design-standards.md",
    ],
    review: [
        ".github/skills/codebase-researcher/references/research-standards.md",
        ".github/skills/design-author/references/design-standards.md",
    ],
    plan: [
        ".github/agents/feature-planner.agent.md",
        ".github/skills/feature-planner/references/planning-standards.md",
        ".github/skills/feature-planner/references/pbi-template.md",
    ],
} as const;

export const workerGuidanceFiles = [...new Set(Object.values(workerGuidance).flat())];
