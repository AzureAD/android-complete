// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import test from "node:test";
import {
    rubric, sections, runDesignFeature, validateStage0, validateDesign,
    validateEvidence, validateReview, workflowDefinition,
} from "./workflow.mjs";

const stage0 = () => ({
    title: "Example feature", revision: "requirements-v3",
    approval: { approvedRevision: "requirements-v3", approvedBy: "human-owner", reference: "review/123" },
    requirements: [{ id: "R1", text: "Preserve compatibility." }], blockingUnknowns: [],
});
const evidence = () => ({
    approvalVerification: {
        verified: true, revision: "requirements-v3", reference: "review/123",
        rationale: "The test approval record explicitly approves requirements-v3.",
    },
    references: [{ id: "E1", repository: "Common", revision: "commit-abc", path: "Example.kt", locator: "Example" }],
    facts: [{ statement: "Example has an existing contract.", referenceIds: ["E1"] }],
    conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
});
const design = () => ({
    stage0Revision: "requirements-v3",
    sections: Object.fromEntries(sections.map((key) => [key, `Content for ${key}.`])),
    traceability: [{ requirementId: "R1", sectionIds: ["requirements"], evidenceIds: ["E1"], verification: "Run compatibility test." }],
    assumptions: [], openDecisions: [], blockingUnknowns: [],
});
const review = () => ({
    criteria: rubric.map((id) => ({ id, pass: true })), findings: [], blockingUnknowns: [],
});
function context(results, requirements = stage0()) {
    const calls = [];
    const keys = [];
    return {
        args: { stage0: requirements }, calls, keys,
        agent: async (prompt, options) => {
            calls.push({ prompt, options });
            assert.ok(results.length, "Unexpected agent call");
            return results.shift();
        },
        step: async (key, producer) => {
            assert.ok(!keys.includes(key), "Step keys must be unique within a run");
            keys.push(key);
            return producer();
        },
    };
}

test("missing, stale and incomplete Stage 0 approval requires clarification without spawning", async () => {
    for (const input of [undefined, {}, { ...stage0(), approval: undefined },
        { ...stage0(), approval: { ...stage0().approval, approvedRevision: "old" } },
        { ...stage0(), blockingUnknowns: ["Who owns the API?"] },
        { ...stage0(), requirements: [...stage0().requirements, ...stage0().requirements] }]) {
        const ctx = context([], input);
        ctx.args = { stage0: input };
        assert.ok(validateStage0(ctx.args).length);
        assert.equal((await runDesignFeature(ctx)).recommendation, "needs clarification");
        assert.equal(ctx.calls.length, 0);
    }
});

test("valid design returns a recommendation, never human approval", async () => {
    const ctx = context([evidence(), design(), review()]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.fixIterations, 0);
    assert.deepEqual(packet.stage0, stage0());
    assert.equal(ctx.calls.length, 3);
    assert.equal(ctx.calls[2].options.label, "Independent design reviewer");
    assert.match(ctx.calls[2].prompt, /Do not edit the design or requirements/);
});

test("evidence is revision-bound and facts cannot cite unknown sources", () => {
    const missingRevision = evidence();
    missingRevision.references[0].revision = "";
    assert.ok(validateEvidence(missingRevision, stage0()).length);
    const badFact = evidence();
    badFact.facts[0].referenceIds = ["missing"];
    assert.ok(validateEvidence(badFact, stage0()).length);
    assert.ok(validateEvidence({ ...evidence(), conflicts: ["Contract conflict"] }, stage0()).length);
    const unverified = evidence();
    unverified.approvalVerification.verified = false;
    assert.ok(validateEvidence(unverified, stage0()).length);
    const staleApproval = evidence();
    staleApproval.approvalVerification.revision = "old";
    assert.ok(validateEvidence(staleApproval, stage0()).length);
});

test("structural checks reject missing sections, changed revision and invalid traceability", () => {
    for (const mutate of [
        (d) => { d.sections.requirements = " "; },
        (d) => { d.stage0Revision = "changed"; },
        (d) => { d.traceability = []; },
        (d) => { d.traceability[0].evidenceIds = ["unknown"]; },
        (d) => { d.traceability[0].sectionIds = ["unknown"]; },
        (d) => { d.traceability[0].verification = ""; },
    ]) {
        const d = design();
        mutate(d);
        assert.ok(validateDesign(d, stage0(), evidence()).length);
    }
});

test("ordinary agent failures explicitly fail closed at each stage", async () => {
    for (const [results, expected] of [
        [[null], "needs clarification"],
        [[evidence(), null], "NO-GO"],
        [[evidence(), design(), null], "NO-GO"],
        [[evidence(), design(), {}], "NO-GO"],
    ]) {
        assert.equal((await runDesignFeature(context(results))).recommendation, expected);
    }
});

test("review must cover the entire fixed rubric with valid structured findings", () => {
    assert.equal(validateReview(review()), true);
    assert.equal(validateReview({ ...review(), criteria: [] }), false);
    assert.equal(validateReview({ ...review(), findings: [{ id: "F1" }] }), false);
    const duplicate = review();
    duplicate.criteria[0] = duplicate.criteria[1];
    assert.equal(validateReview(duplicate), false);
});

test("two failed fix/review iterations stop with NO-GO and exactly seven agents", async () => {
    const failedReview = () => ({
        ...review(),
        findings: [{
            id: "F1", rubricId: rubric[0], severity: "major", location: "requirements",
            evidence: "R1 is not addressed substantively.", requiredChange: "Address R1 without changing it.",
        }],
    });
    const ctx = context([evidence(), design(), failedReview(), design(), failedReview(), design(), failedReview()]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.fixIterations, 2);
    assert.equal(packet.reviews.length, 3);
    assert.equal(ctx.calls.length, 7);
});

test("a fix is independently reviewed and can become a GO recommendation", async () => {
    const failed = review();
    failed.criteria[0].pass = false;
    const ctx = context([evidence(), design(), failed, design(), review()]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.fixIterations, 1);
    assert.deepEqual(packet.rubric, [...rubric]);
});

test("blocking decisions from authors and reviewers require clarification", async () => {
    for (const results of [
        [evidence(), { ...design(), blockingUnknowns: ["Need latency target"] }],
        [evidence(), design(), { ...review(), blockingUnknowns: ["Need API owner"] }],
    ]) {
        assert.equal((await runDesignFeature(context(results))).recommendation, "needs clarification");
    }
});

test("hard failures and cancellation propagate instead of becoming a success packet", async () => {
    const ctx = context([]);
    ctx.agent = async () => { throw new Error("cancelled"); };
    await assert.rejects(runDesignFeature(ctx), /cancelled/);
});

test("structural validation failures are repaired before independent review", async () => {
    const incomplete = design();
    delete incomplete.sections.requirements;
    const ctx = context([evidence(), incomplete, design(), review()]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.fixIterations, 1);
    assert.ok(packet.reviews[0].validationErrors.length);
    assert.equal(ctx.calls[2].options.label, "Design revision");
});

test("a failed fix never bypasses validation or spawns another reviewer", async () => {
    const failed = review();
    failed.criteria[0].pass = false;
    const ctx = context([evidence(), design(), failed, null]);
    assert.equal((await runDesignFeature(ctx)).recommendation, "NO-GO");
    assert.equal(ctx.calls.length, 4);
});

test("mutating input during review cannot produce a GO or overwrite the packet snapshot", async () => {
    const ctx = context([evidence(), design(), review()]);
    const agent = ctx.agent;
    ctx.agent = async (prompt, options) => {
        if (options.label === "Independent design reviewer") ctx.args.stage0.requirements[0].text = "Changed";
        return agent(prompt, options);
    };
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.deepEqual(packet.stage0, stage0());
});

test("resuming from journaled steps reuses output without spawning duplicate agents", async () => {
    const journal = new Map();
    const step = async (key, producer) => {
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    const initial = context([evidence(), design(), review()]);
    initial.step = step;
    await runDesignFeature(initial);
    const resumed = context([]);
    resumed.step = step;
    assert.equal((await runDesignFeature(resumed)).recommendation, "GO to Planning");
    assert.equal(resumed.calls.length, 0);
});

test("checkpoint keys bind input revision and workflow has finite explicit limits", async () => {
    const a = context([null]);
    const input = stage0();
    input.revision = input.approval.approvedRevision = "requirements-v4";
    const b = context([null], input);
    await runDesignFeature(a);
    await runDesignFeature(b);
    assert.notEqual(a.keys[0], b.keys[0]);
    assert.equal(workflowDefinition.meta.name, "design-feature");
    assert.deepEqual(workflowDefinition.meta.limits, {
        maxConcurrentSubagents: 1, maxTotalSubagents: 7, timeoutSeconds: 1800, maxAiCredits: 20,
    });
});
