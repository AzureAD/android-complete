// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { workflowDefinition } from "./workflow.mjs";

const read = (file) => readFileSync(new URL(file, import.meta.url), "utf8");
const launcher = read("../../skills/code-feature/SKILL.md");
const author = read("../../skills/code-author/SKILL.md");
const readme = read("./README.md");
const instructions = read("../../copilot-instructions.md");
const coreRules = author.slice(author.indexOf("## Core Implementation Rules"));
const frontmatter = launcher.match(/^---\r?\n([\s\S]*?)\r?\n---/)[1];

// These assert the installed instruction contract, not a deterministic natural-language classifier.
test("dedicated launcher discovery advertises only explicit, experimental per-request opt-in", () => {
    assert.match(frontmatter, /^name: code-feature$/m);
    assert.match(frontmatter, /description: Experimental, explicit opt-in/);
    assert.match(frontmatter, /\/code-feature/);
    assert.match(frontmatter, /Do not use for ordinary implementation prompts/);
    assert.match(frontmatter, /Opt-in applies to this request only/);
    assert.match(launcher, /current actionable request/);
    assert.match(launcher, /No tool-name syntax, JSON, or configuration toggle/);
    assert.match(workflowDefinition.meta.description, /Experimental opt-in only/);
    assert.match(workflowDefinition.meta.description, /Never the default for ordinary implementation prompts/);
    assert.match(instructions, /\*\*code-feature\*\*.*Experimental opt-in only/);
});

for (const phrase of [
    "Use the coding workflow to implement PBI 3804404",
    "Use the dynamic workflow to implement PBI 3804404",
    "Use the coding dynamic workflow to implement PBI 3804404",
    "Use the coding & PR workflow to implement PBI 3804404",
    "Use the coding and PR workflow to implement PBI 3804404",
    "Use the coding & PR dynamic workflow to implement PBI 3804404",
]) {
    test(`explicit invocation contract includes: ${phrase}`, () => {
        assert.ok(launcher.includes(`\`${phrase}\``));
        assert.ok(readme.split(/\r?\n/).includes(phrase));
    });
}

test("launcher includes slash shorthand, equivalent wording and unchanged task forwarding", () => {
    assert.match(readme, /^\/code-feature implement PBI 3804404$/m);
    assert.ok(launcher.includes("`Use the coding and PR dynamic workflow to implement PBI 3804404`"));
    assert.ok(launcher.includes("`Implement PBI 3804404 using the coding and PR dynamic workflow`"));
    assert.match(launcher, /case and normal whitespace do not matter/);
    assert.match(launcher, /task may be another PBI, a design doc/);
    assert.ok(launcher.includes('run_dynamic_workflow({ name: "code-feature", args: { prompt: "<current user request verbatim>" } })'));
    assert.match(launcher, /do not substitute\s+one of the examples/);
    assert.match(launcher, /explicitly invoked with no task, ask for the task/);
    assert.match(launcher, /registered workflow\/tool is unavailable, explain/);
});

test("ordinary authoring retains standards without a mandatory workflow launch", () => {
    assert.match(author, /## Normal coding is the default/);
    assert.match(author, /Implement ordinary requests directly/);
    assert.match(author, /"implement PBI 3804404".*do not authorize a/);
    assert.match(author, /Invoking `\/code-author` also\s+selects coding standards, not orchestration/);
    assert.match(author, /Do not automatically launch or offer/);
    assert.doesNotMatch(author, /Default Workflow Execution|Your VERY FIRST action|must be to call `run_dynamic_workflow/);
    assert.match(author, /\[implementation standards\]\(references\/implementation-standards.md\) for direct/);
    assert.match(coreRules, /Strict Flight Isolation/);
    assert.match(coreRules, /Regression-Attribution Telemetry/);
    assert.match(coreRules, /Java\/Kotlin ABI/);
    assert.match(coreRules, /Local Gradle vs\. CI pipeline verification/);
    assert.match(instructions, /\*\*code-author\*\*.*not an automatic dynamic-workflow launch/);
});

test("the invocation contract excludes negated, quoted, explanatory, configuration and delegated requests", () => {
    for (const example of [
        "Implement PBI 3804404",
        "Fix this bug",
        "Code and open a PR",
        "/code-author implement PBI 3804404",
        "Do not use the dynamic workflow; implement PBI 3804404 directly",
        "How does the coding workflow work?",
        "Can it implement PBI 3804404?",
        "What happened to the workflow run?",
        'Add "Use the coding workflow to implement PBI 3804404" as an opt-in phrase',
    ]) assert.ok(launcher.includes(`\`${example}\``), `Missing negative example: ${example}`);
    assert.match(launcher, /Do \*\*not\*\* launch for any of the following/);
    assert.match(launcher, /Quoted examples, code blocks, pasted history, or configuration discussions/);
    assert.match(launcher, /prior request's opt-in or a completed milestone\/outcome receipt/);
    assert.match(launcher, /Never recursively launch a workflow/);
    assert.match(author, /Do not launch a new workflow from a builder, reviewer, evidence worker/);
    assert.match(launcher, /explain, configure, or edit the workflow itself, handle that\s+request directly/);
});

test("explicit launch keeps milestone, recovery and budget safeguards available in one launcher", () => {
    for (const boundary of [
        "Never infer success", "[workflow-milestone:...]", "code_feature_workflow_status",
        "Resume has no answer/environment argument", "never store credentials",
        "Preserve remaining repair budgets, including zero", "Human approvals",
    ]) assert.ok(launcher.includes(boundary), `Missing runtime safeguard: ${boundary}`);
    assert.match(launcher, /Omit `options` by default/);
    assert.match(launcher, /Do not set model, effort, context, or resource ceilings without explicit user instructions/);
    assert.match(launcher, /end the caller turn without `Start-Sleep` or inspect-run polling loops/);
});
