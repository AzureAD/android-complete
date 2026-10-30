// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import test from "node:test";
import {
    rubric, sections, runDesignFeature, validateStage0, validateDesign,
    validateEvidence, validateReview, workflowDefinition, digest,
} from "./workflow.mjs";

const stage0 = () => ({
    title: "Example feature", revision: "requirements-v3",
    approval: { approvedRevision: "requirements-v3", approvedBy: "human-owner", reference: "review/123" },
    requirements: [{ id: "R1", text: "Preserve compatibility." }], blockingUnknowns: [],
});
const sourceRevision = "0123456789abcdef0123456789abcdef01234567";
const newerSourceRevision = "89abcdef0123456789abcdef0123456789abcdef";
const sourceManifest = () => [{ repository: "Common", revision: sourceRevision }];
const evidence = () => ({
    approvalVerification: {
        verified: true, revision: "requirements-v3", reference: "review/123",
        rationale: "The test approval record explicitly approves requirements-v3.",
    },
    references: [{ id: "E1", repository: "Common", revision: sourceRevision, path: "Example.kt", locator: "Example" }],
    facts: [{ statement: "Example has an existing contract.", referenceIds: ["E1"] }],
    sourceVerification: [{ ...sourceManifest()[0], verified: true, rationale: "Inspected pinned test fixture." }],
    scenarioTraces: [{
        id: "T1", requirementIds: ["R1"], scenario: "Existing caller", trigger: "Call Example",
        conditions: "Supported caller", steps: [{ behavior: "Example accepts the existing request.", referenceIds: ["E1"] }],
        terminalOutcome: { behavior: "Existing response remains compatible.", referenceIds: ["E1"] },
        status: "verified", unknowns: [],
    }],
    conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
});
const design = () => ({
    stage0Revision: "requirements-v3",
    evidenceDigest: digest(evidence()),
    sections: Object.fromEntries(sections.map((key) => [key, `Content for ${key}.`])),
    traceability: [{ requirementId: "R1", sectionIds: ["requirements"], evidenceIds: ["E1"], verification: "Run compatibility test." }],
    changes: [{
        id: "C1", requirementIds: ["R1"], sectionIds: ["requirements"], traceIds: ["T1"], evidenceIds: ["E1"],
        classification: "preserve-existing", implementation: "regression-only",
        baselineBehavior: "Existing contract is compatible.", requestedDelta: "None; preserve behavior.",
        necessity: "Compatibility already exists; regression coverage only.", verification: "Run compatibility test.",
        counterevidence: {
            referenceIds: ["E1"], searchSummary: "Inspected Example and caller completion.",
            conclusion: "Existing flow handles this contract.", result: "already-handled",
        },
    }],
    assumptions: [], openDecisions: [], blockingUnknowns: [],
});
const review = (d = design()) => ({
    designDigest: digest(d),
    changeChecks: d.changes.map((c) => ({
        changeId: c.id, baselineVerified: true, necessityConfirmed: true, counterevidenceVerified: true,
        referenceIds: ["E1"], rationale: "Independently inspected the existing terminal path.",
    })),
    criteria: rubric.map((id) => ({ id, pass: true })), findings: [], blockingUnknowns: [],
});
function context(results, requirements = stage0()) {
    const calls = [];
    const keys = [];
    return {
        args: { stage0: requirements, sourceManifest: sourceManifest() }, calls, keys,
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
        ctx.args.stage0 = input;
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
    assert.ok(validateEvidence(missingRevision, stage0(), sourceManifest()).length);
    const badFact = evidence();
    badFact.facts[0].referenceIds = ["missing"];
    assert.ok(validateEvidence(badFact, stage0(), sourceManifest()).length);
    assert.ok(validateEvidence({ ...evidence(), conflicts: ["Contract conflict"] }, stage0(), sourceManifest()).length);
    const unverified = evidence();
    unverified.approvalVerification.verified = false;
    assert.ok(validateEvidence(unverified, stage0(), sourceManifest()).length);
    const staleApproval = evidence();
    staleApproval.approvalVerification.revision = "old";
    assert.ok(validateEvidence(staleApproval, stage0(), sourceManifest()).length);
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
    assert.equal(validateReview(review(), design(), evidence()), true);
    assert.equal(validateReview({ ...review(), criteria: [] }, design(), evidence()), false);
    assert.equal(validateReview({ ...review(), findings: [{ id: "F1" }] }, design(), evidence()), false);
    const duplicate = review();
    duplicate.criteria[0] = duplicate.criteria[1];
    assert.equal(validateReview(duplicate, design(), evidence()), false);
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

test("missing or malformed source pins stop before spawning", async () => {
    for (const sources of [undefined, [], [null], sourceManifest().concat(sourceManifest())]) {
        const ctx = context([]);
        ctx.args.sourceManifest = sources;
        assert.equal((await runDesignFeature(ctx)).recommendation, "needs clarification");
        assert.equal(ctx.calls.length, 0);
        assert.equal(ctx.keys.length, 0);
    }
});

test("mutable refs and abbreviated or malformed source hashes stop before agents and checkpoints", async () => {
    const schema = workflowDefinition.meta.argsSchema.properties.sourceManifest.items.properties.revision;
    for (const revision of [
        "main", "HEAD", "refs/heads/dev", "refs/tags/v1.0", "v1.0",
        "0123456", sourceRevision.slice(0, -1), `${sourceRevision}0`,
        "a".repeat(63), "a".repeat(65), "g".repeat(40),
        ` ${sourceRevision}`, `${sourceRevision}\n`, "", null, 123,
    ]) {
        const ctx = context([]);
        ctx.args.sourceManifest[0].revision = revision;
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "needs clarification", String(revision));
        assert.equal(packet.reviewReady, false);
        assert.match(packet.reasons.join(" "), /full Git commit object ID/);
        assert.equal(ctx.calls.length, 0);
        assert.equal(ctx.keys.length, 0);
        if (typeof revision === "string") assert.equal(new RegExp(schema.pattern).test(revision), false);
    }
});

test("full SHA-1 and SHA-256 source hashes allow checkpoints and GO without restricting Stage 0 labels", async () => {
    const schema = workflowDefinition.meta.argsSchema.properties.sourceManifest.items.properties.revision;
    for (const revision of [sourceRevision, "0123456789abcdef".repeat(4), sourceRevision.toUpperCase()]) {
        const e = evidence();
        e.references[0].revision = e.sourceVerification[0].revision = revision;
        const d = design();
        d.evidenceDigest = digest(e);
        const ctx = context([e, d, review(d)]);
        ctx.args.sourceManifest[0].revision = revision;
        assert.deepEqual(validateStage0(ctx.args), []);
        assert.equal(new RegExp(schema.pattern).test(revision), true);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "GO to Planning");
        assert.equal(packet.stage0.revision, "requirements-v3");
        assert.equal(ctx.calls.length, 3);
        assert.equal(ctx.keys.length, 3);
    }
});

test("source verification and complete scenario traces are mandatory before authoring", async () => {
    for (const mutate of [
        (e) => { e.sourceVerification = []; },
        (e) => { e.sourceVerification[0].verified = false; },
        (e) => { e.references[0].revision = "unpinned"; },
        (e) => { e.references[0].repository = "Comon"; },
        (e) => { e.scenarioTraces = []; },
        (e) => { e.scenarioTraces.push(e.scenarioTraces[0]); },
        (e) => { e.scenarioTraces[0].steps = []; },
        (e) => { e.scenarioTraces[0].terminalOutcome.referenceIds = ["unknown"]; },
        (e) => { e.scenarioTraces[0].status = "unresolved"; },
        (e) => { e.scenarioTraces[0].unknowns = ["Observer not inspected"]; },
        (e) => { e.scenarioTraces[0].requirementIds = ["unknown"]; },
    ]) {
        const e = evidence();
        mutate(e);
        const ctx = context([e]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "needs clarification");
        assert.equal(packet.reviewReady, false);
        assert.equal(ctx.calls.length, 1);
    }
});

test("a trace cannot splice different revisions into a fictional execution path", () => {
    const sources = [...sourceManifest(), { repository: "Common", revision: newerSourceRevision }];
    const e = evidence();
    e.sourceVerification.push({ ...sources[1], verified: true, rationale: "Inspected newer test fixture." });
    e.references.push({ ...e.references[0], id: "E2", revision: newerSourceRevision });
    e.scenarioTraces[0].terminalOutcome.referenceIds = ["E2"];
    assert.match(validateEvidence(e, stage0(), sources).join(" "), /cannot mix revisions/);
});

test("necessity records reject omitted, unlinked, stale, unresolved and inconsistent mandates", () => {
    for (const mutate of [
        (d) => { delete d.changes; },
        (d) => { d.changes = []; },
        (d) => { d.changes.push(d.changes[0]); },
        (d) => { d.evidenceDigest = "old"; },
        (d) => { d.changes[0].traceIds = ["missing"]; },
        (d) => { d.changes[0].counterevidence.referenceIds = ["missing"]; },
        (d) => { d.changes[0].counterevidence.searchSummary = ""; },
        (d) => { d.changes[0].counterevidence.result = "unresolved"; },
        (d) => { d.changes[0].classification = "unresolved"; },
        (d) => { d.changes[0].implementation = "required"; },
        (d) => { d.changes[0].classification = "verified-defect"; },
        (d) => { d.changes[0].classification = "optional-improvement"; d.changes[0].implementation = "required"; },
    ]) {
        const d = design();
        mutate(d);
        assert.ok(validateDesign(d, stage0(), evidence()).length);
    }
    const optional = design();
    optional.changes[0].classification = "optional-improvement";
    optional.changes[0].implementation = "deferred";
    assert.deepEqual(validateDesign(optional, stage0(), evidence()), []);
});

test("review checks must cover each change and bind the exact design and inspected sources", () => {
    for (const mutate of [
        (r) => { r.designDigest = "old"; },
        (r) => { r.changeChecks = []; },
        (r) => { r.changeChecks.push(r.changeChecks[0]); },
        (r) => { r.changeChecks[0].changeId = "unknown"; },
        (r) => { r.changeChecks[0].referenceIds = ["unknown"]; },
        (r) => { r.changeChecks[0].rationale = ""; },
    ]) {
        const r = review();
        mutate(r);
        assert.equal(validateReview(r, design(), evidence()), false);
    }
});

test("a failed necessity check cannot be hidden behind a passing rubric", async () => {
    const failed = review();
    failed.changeChecks[0].necessityConfirmed = false;
    const ctx = context([evidence(), design(), failed, design(), failed, design(), failed]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.reviewReady, false);
    assert.equal(ctx.calls.length, 7);
});

// Synthetic fixture only: no private application source or incident details.
function completionFixture(hasCompletionObserver) {
    const events = [];
    const onTelemetry = () => events.push("policy-callback");
    const completeAttempt = (results) => {
        if (results.failed && results.partial) events.push("recovery-error");
    };
    const postCompletion = (results) => {
        events.push("completion-posted");
        if (hasCompletionObserver) completeAttempt(results);
    };
    const handleFailure = () => {
        events.push("failed-results");
        postCompletion({ failed: true, partial: true });
    };
    onTelemetry();
    handleFailure();
    return events;
}

function completionEvidence(hasCompletionObserver) {
    const e = evidence();
    const behavior = hasCompletionObserver
        ? "Completion observer consumes failed results; still-partial row ends recovery Error."
        : "Failed results are posted but no completion observer consumes them; recovery remains open.";
    e.scenarioTraces[0] = {
        ...e.scenarioTraces[0], scenario: "Post-authentication policy failure",
        trigger: "Policy failure after authentication Success", conditions: "Selected row is still partial",
        steps: [
            { behavior: "Telemetry callback runs, then failure handler constructs failed results.", referenceIds: ["E1"] },
            { behavior: "Handler posts completion with failed results.", referenceIds: ["E1"] },
        ],
        terminalOutcome: { behavior, referenceIds: ["E1"] },
    };
    return e;
}

test("indirect completion is reuse/regression-only, not a missing direct callback fix", async () => {
    assert.deepEqual(completionFixture(true), ["policy-callback", "failed-results", "completion-posted", "recovery-error"]);
    const e = completionEvidence(true);
    const d = design();
    d.evidenceDigest = digest(e);
    d.changes[0].baselineBehavior = e.scenarioTraces[0].terminalOutcome.behavior;
    d.changes[0].counterevidence.conclusion = "Indirect completion already ends Error; no forwarding needed.";
    const ctx = context([e, d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.design.changes[0].implementation, "regression-only");
    assert.match(ctx.calls[0].prompt, /Trace beyond direct callbacks/);
    assert.match(ctx.calls[0].prompt, /NOT evidence conflicts/);
    assert.match(ctx.calls[0].prompt, /final design approval is intentionally pending/);
    assert.match(ctx.calls[0].prompt, /Only put repository source references/);
    assert.match(ctx.calls[2].prompt, /Actively seek counterevidence/);
    const bad = structuredClone(d);
    bad.changes[0].implementation = "required";
    assert.ok(validateDesign(bad, stage0(), e).length);
});

test("a genuinely missing terminal handler can justify a verified defect, but needs review", async () => {
    assert.deepEqual(completionFixture(false), ["policy-callback", "failed-results", "completion-posted"]);
    const e = completionEvidence(false);
    const d = design();
    d.evidenceDigest = digest(e);
    Object.assign(d.changes[0], {
        classification: "verified-defect", implementation: "required",
        baselineBehavior: e.scenarioTraces[0].terminalOutcome.behavior,
        requestedDelta: "Wire the existing completion event to a terminal handler.",
        necessity: "No observer consumes the posted failed results in this fixture.",
        counterevidence: {
            referenceIds: ["E1"], searchSummary: "Inspected callback, failure handler and observer registration.",
            conclusion: "No alternate completion consumer exists in this fixture.", result: "confirmed-gap",
        },
    });
    const ctx = context([e, d, review(d)]);
    assert.equal((await runDesignFeature(ctx)).recommendation, "GO to Planning");
    assert.equal(ctx.calls.length, 3);
});

test("an independently disputed gap must be corrected and re-reviewed", async () => {
    const e = completionEvidence(true);
    const incorrect = design();
    incorrect.evidenceDigest = digest(e);
    Object.assign(incorrect.changes[0], {
        classification: "verified-defect", implementation: "required",
        counterevidence: { ...incorrect.changes[0].counterevidence, result: "confirmed-gap", conclusion: "Claimed missing callback" },
    });
    const disputed = review(incorrect);
    disputed.changeChecks[0].necessityConfirmed = false;
    disputed.findings.push({
        id: "F1", rubricId: "changeNecessityAndCounterevidence", severity: "major",
        location: "changes.C1", evidence: "Completion observer already ends Error.",
        requiredChange: "Preserve indirect completion; remove the forwarding mandate.",
    });
    const corrected = design();
    corrected.evidenceDigest = digest(e);
    const ctx = context([e, incorrect, disputed, corrected, review(corrected)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.fixIterations, 1);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.design.changes[0].implementation, "regression-only");
});

test("checkpoint identity includes pinned sources, context, evidence, and schema version", async () => {
    const initial = context([evidence(), design(), review()]);
    await runDesignFeature(initial);
    assert.match(initial.keys[0], /^design-feature-v4-/);
    assert.ok(initial.keys[1].includes(digest(evidence())));
    const changed = context([null]);
    changed.args.sourceManifest[0].revision = newerSourceRevision;
    await runDesignFeature(changed);
    assert.equal(changed.keys.length, 1);
    assert.notEqual(initial.keys[0], changed.keys[0]);
    const feedback = context([null]);
    feedback.args.context = "Additional review feedback";
    await runDesignFeature(feedback);
    assert.notEqual(initial.keys[0], feedback.keys[0]);
});

test("mutation of source pins during evidence gathering stops before authoring", async () => {
    const ctx = context([evidence()]);
    const agent = ctx.agent;
    ctx.agent = async (...args) => {
        ctx.args.sourceManifest[0].revision = newerSourceRevision;
        return agent(...args);
    };
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.deepEqual(packet.sourceManifest, sourceManifest());
    assert.equal(ctx.calls.length, 1);
});
