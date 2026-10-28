// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { parseRequirements } from "../core/contracts.ts";
import { parseIntakeProposal } from "../core/lifecycle-contracts.ts";
import { intakeRun, type FactoryContext } from "../workflows/lifecycle.ts";
import { validateStage0 } from "../../../.github/extensions/DevDesignDoc/workflow.mjs";
import { actor, fixture, requirements } from "./helpers.ts";

const tasks = ["Trace supported IPC contracts without changing their caller-visible error behavior.", "Design a bounded cache policy that preserves active cooldowns."];

test("engineering design obligations do not require a completed design before scope approval", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "design-work", input: { ...requirements(), designTasks: tasks } }, actor);
        assert.equal(feature.status, "requirements_review");
        const command = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        assert.match(f.app.prepare(command).decision!.review, /Design a bounded cache policy/);
        f.app.execute(command, actor, { granted: true, revisionId: feature.current.id, actor });
        const stage0 = f.app.lifecycle.stage0(feature.id);
        assert.deepEqual(stage0.designTasks, tasks);
        assert.deepEqual(validateStage0({ stage0 }), [], "guarded design accepts scoped but not-yet-designed engineering work");
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0, "scope approval is still not execution consent");
        const revised = f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), designTasks: [...tasks, "Preserve interactive recovery through shared PRT helpers."] } }, actor);
        assert.equal(revised.status, "requirements_review");
        assert.deepEqual(revised.history[0]?.designTasks, tasks);
    } finally { f.cleanup(); }
});

test("genuine scope blockers still require a human decision and empty design-task edits preserve old hashes", () => {
    const f = fixture();
    try {
        assert.deepEqual(parseRequirements({ ...requirements(), designTasks: [] }), requirements());
        assert.throws(() => parseRequirements({ ...requirements(), designTasks: "Guess a cache size." }), /must be a list/);
        const feature = f.app.execute({ kind: "create_feature", requestId: "scope-blocker",
            input: { ...requirements(), designTasks: tasks, blockingUnknowns: ["Which caller families are in the first rollout?"] } }, actor);
        assert.throws(() => f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id },
            actor, { granted: true, revisionId: feature.current.id, actor }), /Resolve the open questions/);
    } finally { f.cleanup(); }
});

test("intake can propose reclassifying technical blockers without dropping scope questions or changing saved requirements", async () => {
    const f = fixture();
    const home = process.env.SOFTWARE_FACTORY_HOME;
    const workspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        const scopeQuestion = "Which caller families are in the first rollout?";
        const feature = f.app.execute({ kind: "create_feature", requestId: "old-draft",
            input: { ...requirements(), blockingUnknowns: [scopeQuestion, tasks[1]] } }, actor);
        const execution = f.app.lifecycle.begin(feature.id, "intake", actor);
        const ctx: FactoryContext = {
            args: { featureId: feature.id, executionId: execution.id }, runId: randomUUID(), session: { sessionId: execution.sessionId },
            phase: () => undefined, log: () => undefined, pause: async () => { throw new Error("Intake must not approve or start design."); },
            step: async (_key, action) => action(),
            agent: async (prompt) => {
                assert.match(prompt, /engineering work rather than an unanswered scope decision/);
                return { summary: "Keep the rollout question; assign cache policy to design.", requirements: requirements().requirements,
                    blockingUnknowns: [], designTasks: [tasks[1]] };
            },
        };
        await intakeRun(ctx);
        const proposal = parseIntakeProposal(f.app.lifecycle.store.latest(feature.id, "intake")!.content);
        assert.deepEqual(proposal.blockingUnknowns, [scopeQuestion]);
        assert.deepEqual(proposal.designTasks, [tasks[1]]);
        assert.deepEqual(f.app.registry.get(feature.id).current.blockingUnknowns, [scopeQuestion, tasks[1]]);
        assert.equal(f.app.registry.get(feature.id).current.id, feature.current.id);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
    } finally {
        if (home === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = home;
        if (workspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = workspace;
        f.cleanup();
    }
});
