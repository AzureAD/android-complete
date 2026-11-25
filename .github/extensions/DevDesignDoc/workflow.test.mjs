// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
    rubric, sections, designSectionHeadings, evidenceSchema, designSchema, reviewSchema,
    humanDecisionSchema, continuationSchema,
    decisionCategories, collectDecisionRegister, summarizeDecisions, validateDecisionRegister,
    validateHumanDecisions, normalizeHumanDecisions, buildReviewerPayload,
    readyRecommendation, runDesignFeature, validateStage0, validateDesign,
    validateEvidence, validateReview, workflowDefinition, digest,
    pairRevision, renderHumanReviewBrief, renderPairedDocuments, briefWordBudget, briefCharacterBudget, riskCategories,
    validatePairedDocuments, formatDesignMarkdown, buildDesignReviewDelta,
    extractTrailingJsonBlock, parseDesignSections, parseDesignOutput, parseEvidenceOutput,
} from "./workflow.mjs";

const stage0 = () => ({
    title: "Example feature", revision: "requirements-v3",
    approval: { approvedRevision: "requirements-v3", approvedBy: "human-owner", reference: "review/123" },
    requirements: [{ id: "R1", text: "Preserve compatibility." }], blockingUnknowns: [],
});
const sourceRevision = "0123456789abcdef0123456789abcdef01234567";
const newerSourceRevision = "89abcdef0123456789abcdef0123456789abcdef";
const sourceManifest = () => [{ repository: "Common", revision: sourceRevision }];
const decision = (overrides = {}) => ({
    id: "D1", category: "design-acceptance", status: "open",
    question: "Preserve the current contract?",
    owner: "Common design owner",
    requirementIds: ["R1"], changeIds: ["C1"],
    sectionIds: ["alternativesAndDecision", "requirements"], evidenceIds: ["E1"],
    recommendation: "Keep behavior and add regression coverage.",
    rejectedAlternatives: ["Changing the existing response would break compatibility."],
    tradeoffs: "Regression tests preserve existing behavior but do not add capabilities.",
    resolution: "", resolutionReference: "",
    ...overrides,
});
const evidence = () => ({
    approvalVerification: {
        verified: true, revision: "requirements-v3", reference: "review/123",
        rationale: "The test approval record explicitly approves requirements-v3.",
    },
    priorReviewVerification: null,
    references: [{ id: "E1", repository: "Common", revision: sourceRevision, path: "Example.kt", locator: "Example" }],
    facts: [{ statement: "Example has an existing contract.", referenceIds: ["E1"] }],
    sourceVerification: [{ ...sourceManifest()[0], verified: true, rationale: "Inspected pinned test fixture." }],
    scenarioTraces: [{
        id: "T1", requirementIds: ["R1"], scenario: "Existing caller", trigger: "Call Example",
        conditions: "Supported caller", steps: [{ behavior: "Example accepts the existing request.", referenceIds: ["E1"] }],
        terminalOutcome: { behavior: "Existing response remains compatible.", referenceIds: ["E1"] },
        status: "verified", uncertaintyIds: [],
    }],
    assumptions: [], decisionRegister: [],
});
const design = () => ({
    stage0Revision: "requirements-v3",
    evidenceDigest: digest(evidence()),
    sections: Object.fromEntries(sections.map((key) => [key, `Content for ${key}.`])),
    featureFlags: [],
    traceability: [{ requirementId: "R1", sectionIds: ["requirements"], evidenceIds: ["E1"], verification: "Run compatibility test." }],
    changes: [{
        id: "C1", requirementIds: ["R1"], sectionIds: ["requirements"], traceIds: ["T1"], evidenceIds: ["E1"],
        classification: "preserve-existing", implementation: "regression-only",
        runtimeBehaviorChange: false, flagIds: [],
        noFlagReason: "Regression tests only; no runtime behavior changes.",
        baselineBehavior: "Existing contract is compatible.", requestedDelta: "None; preserve behavior.",
        necessity: "Compatibility already exists; regression coverage only.", verification: "Run compatibility test.",
        counterevidence: {
            referenceIds: ["E1"], searchSummary: "Inspected Example and caller completion.",
            conclusion: "Existing flow handles this contract.", result: "already-handled",
        },
    }],
    assumptions: [], decisionRegister: [decision()],
    humanReviewBrief: {
        outcomeAndScope: {
            text: "Preserve the existing customer response. No runtime changes; scope is compatibility regression coverage, not new APIs.",
            sectionIds: ["problemAndScope", "requirements"], evidenceIds: ["E1"],
        },
        behaviorSummary: [
            { situation: "An existing caller requests the current contract", behavior: "The compatible response is returned unchanged and protected by regression tests." },
        ],
        architecture: {
            text: "The existing caller uses Example and receives its compatible response. Keep that handoff; tests protect it.",
            sectionIds: ["architectureAndDataControlFlow"], evidenceIds: ["E1"],
        },
        flowDiagram: "```mermaid\nflowchart LR\nCaller --> Example --> Response\n```",
        evidenceAndUncertainty: {
            text: "E1 supports the existing contract; future test execution remains unverified. No product decisions are unresolved.",
            sectionIds: ["testsAndEvals", "dependenciesAndOpenDecisions"], evidenceIds: ["E1"],
        },
        risks: riskCategories.map((category) => ({
            category, applicability: "not-applicable",
            impact: "N/A: regression-only coverage changes no runtime paths, UI, authentication, configuration or stored state.",
            safeguards: "Retain the existing contract; implement and review the compatibility tests before accepting the deliverable.",
            owner: "Common design owner", sectionIds: ["requirements", "testsAndEvals"], evidenceIds: ["E1"],
        })),
        changesSincePriorReview: "Initial paired human review; no approval is inherited.",
    },
});
const runtimeDesign = () => {
    const d = design();
    Object.assign(d.changes[0], {
        classification: "new-feature", implementation: "required",
        runtimeBehaviorChange: true, flagIds: ["F1"], noFlagReason: "",
        requestedDelta: "Add an opt-in response format for the new feature.",
        necessity: "The existing format does not support the approved new feature.",
        counterevidence: { ...d.changes[0].counterevidence, result: "confirmed-gap" },
    });
    d.featureFlags = [{
        id: "F1", key: "EXAMPLE_FEATURE", repository: "Common", framework: "Existing flight framework",
        owner: "Feature owner", defaultValue: false, configurationFailureValue: false,
        productionDefaultValue: false,
        entryPoints: [{
            repository: "Common", entryPoint: "Example", guardPlacement: "Before new response allocation",
            disabledBehavior: "Return the existing response without new state or telemetry.",
            evidenceIds: ["E1"],
        }],
        evidenceIds: ["E1"],
        configurationPolicy: "Missing, invalid, unavailable or expired configuration resolves false.",
        productionEnablement: "Production stays off until separate release-owner approval.",
        testCohort: "Only an approved enabled E2E test cohort may exercise the new path.",
        rollback: "Feature owner disables the flag; admitted attempts finish safely and persisted state is verified.",
        verification: "Implementation owner attaches default/configuration, off/on and rollback test results before release.",
    }];
    d.humanReviewBrief.outcomeAndScope.text =
        "Add an opt-in response format; keep the current response unchanged when disabled. Scope excludes broad production enablement.";
    d.humanReviewBrief.behaviorSummary = [
        { situation: "Feature flag is OFF (default or config unavailable)", behavior: "Callers receive the existing response with no new allocations, state writes, or telemetry." },
        { situation: "Feature flag is ON for an approved test cohort", behavior: "Callers receive the new opt-in response format." },
    ];
    Object.assign(d.decisionRegister[0], {
        question: "Add the opt-in response behind a default-off flag?",
        recommendation: "Use the verified flag framework and preserve the disabled contract.",
        rejectedAlternatives: ["Replacing the response unconditionally breaks compatibility."],
        tradeoffs: "A guarded format enables the feature while adding configuration and rollback responsibilities.",
    });
    for (const risk of d.humanReviewBrief.risks) {
        if (["defaultOff", "rollback", "compatibilityCrossRepo"].includes(risk.category)) {
            Object.assign(risk, {
                applicability: "applicable",
                impact: "Incorrect configuration or premature enablement could break the existing response contract.",
                safeguards: "Default/configuration-failure/production values remain false. Verify off/on behavior and safe active-attempt rollback before separate release approval.",
                sectionIds: ["featureFlagsAndIsolation", "stateFailuresRecoveryReversibility"],
            });
        }
    }
    return d;
};
const review = (d = design()) => ({
    designDigest: digest(d), evidenceDigest: d.evidenceDigest, references: [],
    pairRevision: pairRevision(d),
    changeChecks: d.changes.map((c) => ({
        changeId: c.id, baselineVerified: true, necessityConfirmed: true, counterevidenceVerified: true,
        referenceIds: ["E1"], rationale: "Independently inspected the existing terminal path.",
    })),
    criteria: rubric.map((id) => ({ id, pass: true })), findings: [], decisionRegister: [],
    briefChecks: {
        consistent: true, complete: true, risksVisible: true, evidenceDistinguished: true,
        revisionChangesAccurate: true, rationale: "Independently compared the full paired candidate and evidence.",
    },
});
const priorReview = (d = design(), priorReviewContext = null) => ({
    revision: pairRevision(d, priorReviewContext), reference: "human-review/paired-123",
    design: structuredClone(d), priorReviewContext,
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
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.planningReady, false);
    assert.equal(packet.fixIterations, 0);
    assert.deepEqual(packet.stage0, stage0());
    assert.equal(ctx.calls.length, 3);
    assert.equal(ctx.calls[2].options.label, "Independent design reviewer");
    assert.match(ctx.calls[2].prompt, /Do not edit the design or requirements/);
});

test("both publication-ready documents have one exact approval revision and resolvable same-design links", async () => {
    const packet = await runDesignFeature(context([evidence(), design(), review()]));
    const pair = packet.pairedDocuments;
    assert.equal(pair.revision, packet.approvalRevision);
    assert.equal(pair.revision, pairRevision(packet.design));
    assert.equal(pair.primaryDocument, "human-design-review-brief.md");
    assert.equal(pair.documents.length, 2);
    assert.equal(pair.appendices.length, 2);
    const [brief, detailed] = pair.documents;
    assert.match(brief.markdown, /^# Human Design Review Brief/);
    assert.ok(brief.markdown.indexOf("## Decisions needed") < brief.markdown.indexOf("## Customer outcome"));
    for (const document of pair.documents) {
        assert.ok(document.markdown.includes(pair.revision));
        assert.match(document.markdown, /Human approval: pending/);
        assert.equal(document.sha256, digestBytes(document.markdown));
    }
    for (const [, anchor] of brief.markdown.matchAll(/detailed-design\.md#([^)\s]+)/g)) {
        assert.ok(detailed.markdown.includes(`<a id="${anchor}"></a>`), `Missing supporting anchor: ${anchor}`);
    }
    assert.match(detailed.markdown, /Primary human approval surface/);
    assert.deepEqual(pair, renderPairedDocuments(packet.design, packet.availableEvidence));
    assert.equal(validatePairedDocuments(pair, packet.design, packet.availableEvidence), true);
    assert.match(packet.nextAction, /both pairedDocuments together with every hash-bound appendix/);
});

function digestBytes(value) {
    // The manifest hashes actual UTF-8 Markdown bytes, not JSON-quoted text.
    return createHash("sha256").update(value).digest("hex");
}

test("publication integrity rejects split revisions, missing documents and edited bytes even with refreshed file hashes", () => {
    const d = design();
    const pair = renderPairedDocuments(d, evidence());
    const revised = design();
    revised.sections.requirements += " A consequential new requirement.";
    const other = renderPairedDocuments(revised, evidence());
    for (const mutate of [
        (p) => { p.documents.pop(); },
        (p) => { p.documents[1] = other.documents[1]; },
        (p) => { p.revision = other.revision; },
        (p) => { p.documents[0].markdown += " A post-review edit."; },
        (p) => {
            p.documents[0].markdown += " A post-review edit.";
            p.documents[0].sha256 = digestBytes(p.documents[0].markdown);
        },
    ]) {
        const changed = structuredClone(pair);
        mutate(changed);
        assert.equal(validatePairedDocuments(changed, d, evidence()), false);
    }
    assert.equal(validatePairedDocuments(null, d, evidence()), false);
});

test("missing, unsupported and incomplete brief content is repaired before independent review", async () => {
    for (const mutate of [
        (d) => { delete d.humanReviewBrief; },
        (d) => { d.humanReviewBrief.architecture.text = " "; },
        (d) => { d.humanReviewBrief.behaviorSummary = []; },
        (d) => { d.humanReviewBrief.behaviorSummary[0].behavior = " "; },
        (d) => { d.humanReviewBrief.flowDiagram = ""; },
        (d) => { d.humanReviewBrief.outcomeAndScope.sectionIds = ["invented"]; },
        (d) => { d.humanReviewBrief.evidenceAndUncertainty.evidenceIds = ["unknown"]; },
        (d) => { d.decisionRegister = []; },
        (d) => { d.decisionRegister[0].changeIds = ["unknown"]; },
        (d) => { d.decisionRegister[0].owner = ""; },
        (d) => { d.decisionRegister[0].rejectedAlternatives = []; },
        (d) => { d.decisionRegister[0].status = "approved"; },
        (d) => { d.humanReviewBrief.risks.pop(); },
        (d) => { d.humanReviewBrief.risks[1].category = d.humanReviewBrief.risks[0].category; },
        (d) => { d.humanReviewBrief.risks[0].safeguards = ""; },
        (d) => { d.humanReviewBrief.changesSincePriorReview = ""; },
    ]) {
        const malformed = design();
        mutate(malformed);
        const ctx = context([evidence(), malformed, design(), review()]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, readyRecommendation);
        assert.equal(packet.fixIterations, 1);
        assert.equal(ctx.calls[2].options.label, "Design revision");
        assert.equal(ctx.calls.length, 4);
    }
});

test("rendered word/character budgets include all copied approval-critical content and never truncate", async () => {
    const d = design();
    const initialWords = renderHumanReviewBrief(d, evidence()).trim().split(/\s+/u).length;
    d.humanReviewBrief.outcomeAndScope.text += " word".repeat(briefWordBudget - initialWords);
    assert.equal(renderHumanReviewBrief(d, evidence()).trim().split(/\s+/u).length, briefWordBudget);
    assert.deepEqual(validateDesign(d, stage0(), evidence()), []);
    d.humanReviewBrief.outcomeAndScope.text += " excess";
    assert.match(validateDesign(d, stage0(), evidence()).join(" "), /1801 words/);
    const packet = await runDesignFeature(context([evidence(), d, d, d]));
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.pairedDocuments, null);
    assert.ok(packet.design.humanReviewBrief.outcomeAndScope.text.endsWith(" excess"));
    const unbroken = design();
    unbroken.assumptions = ["x".repeat(briefCharacterBudget)];
    assert.match(validateDesign(unbroken, stage0(), evidence()).join(" "), /characters/);
    const copied = design();
    copied.decisionRegister[0].question = "question ".repeat(briefWordBudget).trim();
    assert.match(validateDesign(copied, stage0(), evidence()).join(" "), /never truncate/);
});

test("rendering carries exact authoritative assumptions, unknowns and baseline deltas visibly", () => {
    const e = evidence();
    e.assumptions = ["Telemetry mapping is proposed, not verified; telemetry owner must validate."];
    e.decisionRegister = [decision({
        id: "L1", category: "release-check", changeIds: [],
        question: "Keyboard acceptance is Unverified; QA owner records enabled-cohort checks.",
    })];
    const d = design();
    d.evidenceDigest = digest(e);
    d.assumptions = ["Client compatibility is assumed outside the verified handoff."];
    d.decisionRegister.push(decision({
        id: "L2", category: "release-check",
        question: "Release owner must decide the enabled test cohort, not production rollout.",
    }));
    const markdown = renderHumanReviewBrief(d, e);
    for (const value of [...e.assumptions, ...e.decisionRegister.map((entry) => entry.question),
        ...d.assumptions, ...d.decisionRegister.map((entry) => entry.question),
        d.changes[0].baselineBehavior, d.changes[0].requestedDelta]) {
        assert.ok(markdown.includes(value));
    }
    assert.deepEqual(validateDesign(d, stage0(), e), []);
    assert.ok(markdown.includes(`No runtime change: ${d.changes[0].noFlagReason}`));
    assert.ok(!markdown.includes("Unchanged baseline:"));
    const runtime = runtimeDesign();
    const runtimeMarkdown = renderHumanReviewBrief(runtime, evidence());
    assert.ok(runtimeMarkdown.includes(`Default-off: ${runtime.changes[0].flagIds.join(", ")}`));
    assert.ok(runtimeMarkdown.includes("For runtime changes, disabled behavior follows the linked isolation and compatibility contracts; implementation verification is pending."));
    assert.ok(!runtimeMarkdown.includes("Disabled behavior must preserve the baseline"));
});

test("hidden or mismatched risks cannot pass solely on a passing rubric and require fresh paired review", async () => {
    for (const check of ["consistent", "complete", "risksVisible", "evidenceDistinguished", "revisionChangesAccurate"]) {
        const d = runtimeDesign();
        d.humanReviewBrief.risks.find((risk) => risk.category === "rollback").safeguards =
            "See the detailed design; rollback always cancels active work safely.";
        const failed = review(d);
        failed.briefChecks[check] = false;
        failed.briefChecks.rationale = "The brief hides active-attempt risks and contradicts the authoritative rollback plan.";
        const packet = await runDesignFeature(context([evidence(), d, failed, d, failed, d, failed]));
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(packet.pairedDocuments, null);
        assert.equal(packet.fixIterations, 2);
        const fixed = runtimeDesign();
        const ctx = context([evidence(), d, failed, fixed, review(fixed)]);
        const corrected = await runDesignFeature(ctx);
        assert.equal(corrected.recommendation, readyRecommendation);
        assert.notEqual(corrected.approvalRevision, pairRevision(d));
        const reviewerInput = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
        assert.equal(reviewerInput.designDigest, digest(fixed));
        assert.equal(reviewerInput.renderedBrief, corrected.pairedDocuments.documents[0].markdown);
    }
});

test("malformed paired checks or missing brief criterion fail closed without publishing artifacts", async () => {
    for (const mutate of [
        (r) => { delete r.briefChecks; },
        (r) => { delete r.pairRevision; },
        (r) => { r.pairRevision = "stale"; },
        (r) => { r.briefChecks.risksVisible = "true"; },
        (r) => { r.briefChecks.rationale = " "; },
        (r) => { r.criteria = r.criteria.filter((criterion) => criterion.id !== "humanDesignReviewBrief"); },
    ]) {
        const r = review();
        mutate(r);
        const packet = await runDesignFeature(context([evidence(), design(), r]));
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(packet.approvalRevision, null);
        assert.equal(packet.pairedDocuments, null);
    }
});

test("a changed brief or authoritative design cannot reuse a stale review or approval revision", async () => {
    const original = design();
    for (const mutate of [
        (d) => { d.humanReviewBrief.architecture.text += " The proposed flow changed."; },
        (d) => { d.sections.securityPrivacyPerformance += " A new authentication risk requires a safeguard."; },
    ]) {
        const revised = structuredClone(original);
        mutate(revised);
        assert.notEqual(pairRevision(original), pairRevision(revised));
        const packet = await runDesignFeature(context([evidence(), revised, review(original)]));
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(packet.pairedDocuments, null);
    }
});

test("verified prior human review regenerates a fresh pair and identifies substantive changes", async () => {
    const prior = priorReview();
    const d = runtimeDesign();
    d.sections.architectureAndDataControlFlow = "The proposed opt-in format is guarded at the shared Example entry point.";
    d.humanReviewBrief.changesSincePriorReview =
        "Added a new opt-in format, default-off ownership and active-attempt rollback safeguards; prior review covered tests only.";
    const e = evidence();
    e.priorReviewVerification = { verified: true, revision: prior.revision, reference: prior.reference,
        rationale: "Inspected the supplied human review record for this exact pair." };
    d.evidenceDigest = digest(e);
    const r = review(d);
    r.evidenceDigest = digest(e);
    r.pairRevision = pairRevision(d, prior);
    const ctx = context([e, d, r]);
    ctx.args.priorHumanReview = prior;
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.humanApproval, "pending");
    assert.notEqual(packet.approvalRevision, prior.revision);
    assert.equal(packet.approvalRevision, pairRevision(d, prior));
    const markdown = packet.pairedDocuments.documents[0].markdown;
    assert.match(markdown, /Changed detailed sections: architectureAndDataControlFlow/);
    assert.match(markdown, /Changed registers: changes, featureFlags/);
    assert.ok(markdown.includes(prior.reference));
    assert.match(markdown, /prior approval is not carried forward/);
    assert.deepEqual(packet.pairedDocuments.priorReviewContext, { revision: prior.revision, reference: prior.reference });
    // The next review snapshot can verify this pair without copying the entire history recursively.
    const nextPrior = priorReview(d, packet.pairedDocuments.priorReviewContext);
    const nextArgs = { ...ctx.args, priorHumanReview: nextPrior };
    assert.deepEqual(validateStage0(nextArgs), []);
    const anotherReference = { ...prior, reference: "human-review/different-reference" };
    assert.notEqual(pairRevision(d, prior), pairRevision(d, anotherReference));
});

test("stale, legacy or unverified prior-review pairing stops before claiming a reviewed delta", async () => {
    for (const mutate of [
        (p) => { p.revision = "old"; },
        (p) => { delete p.design.humanReviewBrief; },
        (p) => { p.design.sections.requirements += " Changed after review."; },
        (p) => { p.reference = ""; },
        (p) => { delete p.priorReviewContext; },
        (p) => { delete p.design.sections; p.revision = pairRevision(p.design, p.priorReviewContext); },
        (p) => { p.design.sections.requirements = ""; p.revision = pairRevision(p.design, p.priorReviewContext); },
        (p) => { delete p.design.evidenceDigest; p.revision = pairRevision(p.design, p.priorReviewContext); },
    ]) {
        const prior = priorReview();
        mutate(prior);
        const ctx = context([]);
        ctx.args.priorHumanReview = prior;
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "needs clarification");
        assert.equal(ctx.calls.length, 0);
        assert.equal(packet.pairedDocuments, null);
    }
    const ctx = context([evidence()]);
    ctx.args.priorHumanReview = priorReview();
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "needs clarification");
    assert.match(packet.reasons.join(" "), /prior human review/);
    assert.equal(ctx.calls.length, 1);
});

test("v10-v15 journal artifacts are never reused and v16 replays the exact pair without paid calls", async () => {
    const legacy = design();
    delete legacy.humanReviewBrief;
    const ctx = context([evidence(), design(), review()]);
    const journal = new Map();
    for (const version of ["v10", "v12", "v13", "v14", "v15"]) {
        const oldPrefix = `design-feature-${version}-${digest(ctx.args)}`;
        journal.set(`${oldPrefix}-evidence`, evidence());
        journal.set(`${oldPrefix}-${digest(evidence())}-design`, version === "v10" ? legacy : design());
    }
    const step = async (key, producer) => {
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    ctx.step = step;
    const initial = await runDesignFeature(ctx);
    assert.equal(ctx.calls.length, 3);
    assert.equal(initial.recommendation, readyRecommendation);
    const resumed = context([]);
    resumed.step = step;
    const replay = await runDesignFeature(resumed);
    assert.deepEqual(replay.pairedDocuments, initial.pairedDocuments);
    assert.equal(replay.approvalRevision, initial.approvalRevision);
    assert.equal(resumed.calls.length, 0);
    assert.equal(replay.humanApproval, "pending");
    assert.equal(replay.planningReady, false);
});

test("evidence is revision-bound and facts cannot cite unknown sources", () => {
    const missingRevision = evidence();
    missingRevision.references[0].revision = "";
    assert.ok(validateEvidence(missingRevision, stage0(), sourceManifest()).length);
    const badFact = evidence();
    badFact.facts[0].referenceIds = ["missing"];
    assert.ok(validateEvidence(badFact, stage0(), sourceManifest()).length);
    const conflicting = evidence();
    conflicting.decisionRegister = [decision({
        id: "S1", category: "source-uncertainty", changeIds: [], question: "Contract conflict.",
    })];
    assert.ok(validateEvidence(conflicting, stage0(), sourceManifest()).length);
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

test("runtime features require Boolean false defaults and a complete flag contract before review", async () => {
    const mutations = [
        (d) => { d.sections.featureFlagsAndIsolation = " "; },
        (d) => { delete d.featureFlags; },
        (d) => { d.featureFlags = []; },
        (d) => { d.featureFlags[0].defaultValue = true; },
        (d) => { d.featureFlags[0].defaultValue = "false"; },
        (d) => { delete d.featureFlags[0].defaultValue; },
        (d) => { d.featureFlags[0].configurationFailureValue = true; },
        (d) => { d.featureFlags[0].productionDefaultValue = true; },
        (d) => { d.featureFlags[0].owner = " "; },
        (d) => { d.featureFlags[0].framework = ""; },
        (d) => { d.featureFlags[0].entryPoints = []; },
        (d) => { d.featureFlags[0].entryPoints[0].guardPlacement = ""; },
        (d) => { d.featureFlags[0].entryPoints[0].disabledBehavior = ""; },
        (d) => { d.featureFlags[0].configurationPolicy = ""; },
        (d) => { d.featureFlags[0].productionEnablement = ""; },
        (d) => { d.featureFlags[0].testCohort = ""; },
        (d) => { d.featureFlags[0].rollback = ""; },
        (d) => { d.featureFlags[0].verification = ""; },
    ];
    for (const mutate of mutations) {
        const d = runtimeDesign();
        mutate(d);
        assert.match(validateDesign(d, stage0(), evidence()).join(" "), /feature.flag|runtime change|featureFlagsAndIsolation/i);
        const ctx = context([evidence(), d, d, d]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(packet.reviewReady, false);
        assert.equal(ctx.calls.length, 4);
        assert.ok(ctx.calls.every((call) => call.options.label !== "Independent design reviewer"));
    }
});

test("flag mappings reject unknown, duplicate, orphaned and unpinned contracts", () => {
    for (const mutate of [
        (d) => { d.changes[0].flagIds = []; },
        (d) => { d.changes[0].flagIds = ["missing"]; },
        (d) => { d.changes[0].flagIds = ["F1", "F1"]; },
        (d) => { d.featureFlags.push(structuredClone(d.featureFlags[0])); },
        (d) => { d.featureFlags.push({ ...structuredClone(d.featureFlags[0]), id: "F2" }); },
        (d) => { d.featureFlags[0].repository = "Unpinned"; },
        (d) => { d.featureFlags[0].evidenceIds = ["missing"]; },
        (d) => { d.featureFlags[0].entryPoints[0].repository = "Unpinned"; },
        (d) => { d.featureFlags[0].entryPoints[0].evidenceIds = ["missing"]; },
        (d) => { d.changes[0].runtimeBehaviorChange = "false"; },
        (d) => { delete d.changes[0].runtimeBehaviorChange; },
        (d) => { d.changes[0].noFlagReason = "Small runtime feature"; },
    ]) {
        const d = runtimeDesign();
        mutate(d);
        assert.ok(validateDesign(d, stage0(), evidence()).length);
    }
    const unmapped = runtimeDesign();
    unmapped.changes.push({ ...structuredClone(unmapped.changes[0]), id: "C2", flagIds: [] });
    assert.ok(validateDesign(unmapped, stage0(), evidence()).length);
});

test("non-runtime work requires a reasoned no-flag declaration, not an automatic exemption", async () => {
    for (const mutate of [
        (d) => { delete d.changes[0].noFlagReason; },
        (d) => { d.changes[0].noFlagReason = " "; },
        (d) => { d.changes[0].flagIds = ["F1"]; },
        (d) => { d.changes[0].runtimeBehaviorChange = true; },
    ]) {
        const d = design();
        mutate(d);
        assert.ok(validateDesign(d, stage0(), evidence()).length);
    }
    const ctx = context([evidence(), design(), review()]);
    assert.equal((await runDesignFeature(ctx)).recommendation, readyRecommendation);
    assert.match(ctx.calls[2].prompt.split("\nDATA:\n")[0], /challenge.*non-runtime/i);
});

test("default-off contracts reach the human packet and author/reviewer schemas without activation", async () => {
    const d = runtimeDesign();
    const ctx = context([evidence(), d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.humanApproval, "pending");
    assert.deepEqual(packet.design.featureFlags, d.featureFlags);
    assert.equal(ctx.calls[1].options.schema, undefined);
    assert.ok(designSchema.required.includes("featureFlags"));
    assert.ok(designSchema.properties.sections.required.includes("featureFlagsAndIsolation"));
    for (const key of ["defaultValue", "configurationFailureValue", "productionDefaultValue"]) {
        assert.equal(designSchema.properties.featureFlags.items.properties[key].type, "boolean");
        assert.equal(designSchema.properties.featureFlags.items.properties[key].const, false);
    }
    const author = ctx.calls[1].prompt.split("\nDATA:\n")[0];
    assert.match(author, /## 13\. Feature Flags and Isolation \(featureFlagsAndIsolation\)/);
    assert.match(author, /runtimeBehaviorChange/);
    assert.match(author, /zero new side effects/);
    assert.match(author, /approved enabled E2E test cohort/);
    assert.match(author, /PBI acceptance criteria/);
    assert.match(author, /Design approval is not code\s+verification or activation permission/);
    const reviewer = ctx.calls[2].prompt.split("\nDATA:\n")[0];
    assert.match(reviewer, /defaultOffFeatureIsolation/);
    assert.match(reviewer, /all relevant\s+entry points/i);
    assert.match(ctx.calls[0].prompt.split("\nDATA:\n")[0], /existing flag framework/);
});

test("author and reviewer prompts codify Android Auth Flighting Standard defaults and exception-only escalation", async () => {
    const d = runtimeDesign();
    const ctx = context([evidence(), d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);

    const author = ctx.calls[1].prompt.split("\nDATA:\n")[0];
    assert.match(author, /Android Auth Flighting Standard \(Team Defaults\)/);
    assert.match(author, /Single controlling-boundary flag by default/i);
    assert.match(author, /BrokerFlight.*CommonFlight.*EcsFlight/);
    assert.match(author, /Single boundary evaluation \(local state, no per-request network fetch\)/i);
    assert.match(author, /Immediate OFF on new\/uncommitted boundaries \(zero grace buffer\)/i);
    assert.match(author, /Do NOT invent multi-flag setups, TTL\/grace buffers or mid-flow re-checks/i);
    assert.match(author, /Exception-only human escalation/i);
    assert.match(author, /sections\.featureFlagsAndIsolation and humanReviewBrief/);

    const reviewer = ctx.calls[2].prompt.split("\nDATA:\n")[0];
    assert.match(reviewer, /defaultOffFeatureIsolation[\s\S]*Android Auth Flighting Standard \(Team Defaults\)/);
    assert.match(reviewer, /single controlling-boundary flag by\s+default/i);
    assert.match(reviewer, /single boundary evaluation of locally received\/cached ECS\/flight state/i);
    assert.match(reviewer, /immediate OFF on new\/uncommitted boundaries with zero grace\/TTL buffer/i);
    assert.match(reviewer, /exception-only human escalation/i);
    assert.match(reviewer, /Challenge unnecessary multi-flag proliferation, invented TTL\/grace buffers,[\s\S]*unprompted mid-flow\/nested re-checks as over-engineering findings/i);
    assert.match(reviewer, /explicit justification in both featureFlagsAndIsolation and humanReviewBrief for any accepted\s+deviation or off-path exception/i);
});

test("over-engineering flighting findings trigger bounded repair carrying flighting standard guidance", async () => {
    const d = runtimeDesign();
    const failed = review(d);
    const criterion = failed.criteria.find((c) => c.id === "defaultOffFeatureIsolation");
    assert.ok(criterion, "Required flag criterion is missing");
    criterion.pass = false;
    failed.findings.push({
        id: "FLAG_OVERENG", rubricId: "defaultOffFeatureIsolation", severity: "major",
        location: "sections.featureFlagsAndIsolation",
        evidence: "Design invents a 5-minute TTL grace buffer and redundant mid-flow re-checks without an approved requirement.",
        requiredChange: "Adopt the Android Auth Flighting Standard: single controlling-boundary evaluation of local state with immediate OFF and zero grace buffer.",
    });
    const corrected = runtimeDesign();
    corrected.featureFlags[0].rollback = "Immediate OFF for new/uncommitted boundaries with zero grace buffer; already-dispatched requests cannot be recalled.";
    const ctx = context([evidence(), d, failed, corrected, review(corrected)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls[3].options.label, "Design revision");
    const revisionPrompt = ctx.calls[3].prompt.split("\nDATA:\n")[0];
    assert.match(revisionPrompt, /Android Auth Flighting Standard \(Team Defaults\)/);
    assert.match(revisionPrompt, /Do NOT invent multi-flag setups, TTL\/grace buffers or mid-flow re-checks/i);
});

test("omitted or failed default-off review criterion cannot authorize planning", async () => {
    const d = runtimeDesign();
    const missing = review(d);
    missing.criteria = missing.criteria.filter((c) => c.id !== "defaultOffFeatureIsolation");
    assert.equal(validateReview(missing, d, evidence()), false);
    assert.equal((await runDesignFeature(context([evidence(), d, missing]))).recommendation, "NO-GO");
    const failed = review(d);
    const criterion = failed.criteria.find((c) => c.id === "defaultOffFeatureIsolation");
    assert.ok(criterion, "Required flag criterion is missing");
    criterion.pass = false;
    const packet = await runDesignFeature(context([evidence(), d, failed, d, failed, d, failed]));
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.fixIterations, 2);
});

test("multiple runtime changes can share a flag or use separately owned pinned flags", async () => {
    const e = evidence();
    e.references.push({ ...e.references[0], id: "E2", repository: "Broker" });
    e.sourceVerification.push({ ...e.sourceVerification[0], repository: "Broker" });
    const d = runtimeDesign();
    d.evidenceDigest = digest(e);
    d.changes.push({ ...structuredClone(d.changes[0]), id: "C2" });
    d.decisionRegister[0].changeIds.push("C2");
    assert.deepEqual(validateDesign(d, stage0(), e), []);
    const second = structuredClone(d.featureFlags[0]);
    Object.assign(second, { id: "F2", repository: "Broker", evidenceIds: ["E2"] });
    Object.assign(second.entryPoints[0], { repository: "Broker", evidenceIds: ["E2"] });
    d.featureFlags.push(second);
    d.changes[1].flagIds = ["F2"];
    const ctx = context([e, d, review(d)]);
    ctx.args.sourceManifest.push({ ...sourceManifest()[0], repository: "Broker" });
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.deepEqual(packet.design.featureFlags, d.featureFlags);
    second.evidenceIds = ["E1"];
    assert.ok(validateDesign(d, stage0(), e).length, "Flag evidence must include its owning repository");
});

test("flag-isolation findings require repair, fresh review and retained default-off contracts", async () => {
    const d = runtimeDesign();
    const failed = review(d);
    const criterion = failed.criteria.find((c) => c.id === "defaultOffFeatureIsolation");
    assert.ok(criterion, "Required flag criterion is missing");
    criterion.pass = false;
    failed.findings.push({
        id: "FLAG1", rubricId: "defaultOffFeatureIsolation", severity: "major",
        location: "featureFlags.F1.entryPoints", evidence: "A direct caller initializes new state before the guard.",
        requiredChange: "Move the guard ahead of new state creation for the direct caller.",
    });
    const corrected = runtimeDesign();
    corrected.featureFlags[0].entryPoints[0].guardPlacement = "Shared boundary before any new state creation";
    const ctx = context([evidence(), d, failed, corrected, review(corrected)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls[3].options.label, "Design revision");
    assert.match(ctx.calls[3].prompt.split("\nDATA:\n")[0], /runtimeBehaviorChange/);
    const data = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
    assert.deepEqual(data.design.featureFlags, corrected.featureFlags);
    assert.equal(data.designDigest, digest(corrected));
});

test("missing or blank pre-mortem authoring briefs cannot produce a review-ready packet", async () => {
    for (const value of [undefined, "", " \n "]) {
        const incomplete = design();
        if (value === undefined) delete incomplete.sections.preMortemAndOperationalReadiness;
        else incomplete.sections.preMortemAndOperationalReadiness = value;
        assert.match(validateDesign(incomplete, stage0(), evidence()).join(" "), /Missing design section: preMortemAndOperationalReadiness/);
        const ctx = context([evidence(), incomplete, incomplete, incomplete]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(packet.reviewReady, false);
        assert.equal(packet.fixIterations, 2);
        assert.equal(ctx.calls.length, 4);
        assert.ok(ctx.calls.every((call) => call.options.label !== "Independent design reviewer"));
    }
});

test("runtime and non-runtime designs hand off a brief for post-implementation pre-mortem authoring", async () => {
    for (const flagged of [true, false]) {
        const d = flagged ? runtimeDesign() : design();
        d.sections.preMortemAndOperationalReadiness = `# Pre-mortem authoring brief
Inputs: R1 / T1 / E1 and approved requirements-v3; compatibility risk affects supported callers.
Known monitoring contract: proposed attemptId/outcome/featureVersion; mappings require telemetry-owner follow-up.
Healthy operation and failure detection: success is a compatible response usable by the caller, not just API success.
After dispatch generate happy-path/failure scenario IDs linked to KQL query IDs with outputs/units and healthy baselines.
Later author must cover eligible/executed/baseline cohorts, failed/total attempts, sampling and no-traffic gaps.
Rollback input: ${flagged ? "default-off flag F1" : "prior-release recovery"}; cover active attempts/partial state.
Pre-mortem child PBI: Pending feature planning; reuse a suitable existing child if verified.
Pre-mortem artifact: Pending implementation completion and PBI dispatch.
Depend on all feature implementation PBIs and verify accepted/merged work and validation evidence.
After all feature implementation is complete, dispatch for agent authoring against implemented pinned sources.
Agent writes scenarios/impact, safeguards, actual KQL drafts, monitoring operations, rollback and release prerequisites.
Named human reviewer checks assumptions, completeness, owners/gaps and records approval before release.
Telemetry owner validates queries using an approved enabled E2E cohort; release owner separately approves release.
This is an authoring brief, not a pre-mortem draft or human acceptance/rollout approval.`;
        const ctx = context([evidence(), d, review(d)]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, readyRecommendation);
        assert.equal(packet.humanApproval, "pending");
        assert.equal(packet.design.sections.preMortemAndOperationalReadiness, d.sections.preMortemAndOperationalReadiness);
        assert.deepEqual(packet.stage0, stage0());
        assert.equal(ctx.calls.length, 3);
        assert.equal(ctx.calls[1].options.schema, undefined);
        assert.ok(designSchema.properties.sections.required.includes("preMortemAndOperationalReadiness"));
        const task = ctx.calls[1].prompt.split("\nDATA:\n")[0];
        assert.match(task, /## 12\. Pre-Mortem and Operational Readiness \(preMortemAndOperationalReadiness\)/);
        assert.match(task, /every feature, including single-repo and non-runtime/);
        assert.match(task, /authoring brief, not the pre-mortem draft/);
        assert.match(task, /only after all feature implementation is complete/);
        assert.match(task, /all feature implementation PBIs/);
        assert.match(task, /agent writes the complete pre-mortem/);
        assert.match(task, /actual KQL drafts/);
        assert.match(task, /human reviewer/);
        assert.doesNotMatch(task, /Generate a self-contained Markdown "Pre-mortem and operational readiness" document/);
        assert.match(task, /active\/in-flight attempts/);
        assert.match(task, /Pending feature planning/);
        assert.match(ctx.calls[0].prompt.split("\nDATA:\n")[0], /customer-visible successful outcomes/);
        for (const contract of [
            /"Healthy operation and failure detection"/,
            /happy-path AND failure\/degradation scenarios/,
            /actual Kusto \(KQL\) draft text, not just query titles or prose/,
            /query IDs mapped to\s+every scenario\/measure/,
            /expected output columns\/units/,
            /eligible\/exposed\/executing\/baseline cohorts/,
            /cancellation versus abandonment/,
            /time-window-matured abandonment/,
            /latency and telemetry\s+coverage/,
            /zero\s+denominators and low samples as insufficient evidence/,
            /never introduce\s+disabled-path telemetry/,
            /investigation triggers, stop-expansion criteria and rollback/,
            /justify each inapplicable metric\/query/,
            /controlled happy-path and failure cases plus missing\/late-terminal and zero\/low-sample cases/,
        ]) assert.match(task, contract);
        const reviewerTask = ctx.calls[2].prompt.split("\nDATA:\n")[0];
        assert.match(reviewerTask, /preMortemAndReleaseGates/);
        assert.match(reviewerTask, /not completion or acceptance of the pre-mortem/);
        assert.match(reviewerTask, /reverse dependencies/);
        assert.match(reviewerTask, /named human review before release/);
        assert.match(reviewerTask, /For healthyOperationAndFailureDetection/);
        assert.match(reviewerTask, /actual KQL draft text\/query IDs/);
        assert.match(reviewerTask, /Reject omitted happy-path coverage or blanket N\/A/);
        assert.match(reviewerTask, /controlled happy-path\/failure, missing\/late-terminal and zero\/low-sample query checks/);
    }
});

test("healthy-operation review omission or failure cannot bypass the gate", async () => {
    const missing = review();
    missing.criteria = missing.criteria.filter((criterion) => criterion.id !== "healthyOperationAndFailureDetection");
    assert.equal(validateReview(missing, design(), evidence()), false);
    const malformed = await runDesignFeature(context([evidence(), design(), missing]));
    assert.equal(malformed.recommendation, "NO-GO");
    assert.equal(malformed.reviewReady, false);

    const failed = review();
    failed.criteria.find((criterion) => criterion.id === "healthyOperationAndFailureDetection").pass = false;
    const ctx = context([evidence(), design(), failed, design(), failed, design(), failed]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.fixIterations, 2);
    assert.equal(ctx.calls.length, 7);
});

test("missing happy-path scenario/query handoff requires repair and fresh independent review", async () => {
    const d = design();
    d.sections.preMortemAndOperationalReadiness =
        "Authoring brief plans error queries only; API success is assumed healthy.";
    const failed = review(d);
    failed.criteria.find((criterion) => criterion.id === "healthyOperationAndFailureDetection").pass = false;
    failed.findings.push({
        id: "HEALTH1", rubricId: "healthyOperationAndFailureDetection", severity: "major",
        location: "sections.preMortemAndOperationalReadiness",
        evidence: "Happy-path customer completion, denominators and scenario/query mappings are missing.",
        requiredChange: "Plan the healthy-operation section with customer success and post-dispatch scenario-mapped KQL.",
    });
    const corrected = design();
    corrected.sections.preMortemAndOperationalReadiness =
        "Healthy operation and failure detection: customer success means the caller can use the compatible result. " +
        "After implementation completion and PBI dispatch, generate happy-path/failure scenario IDs and actual KQL/query IDs " +
        "for eligibility/exposure, attempts/completions, cancellation/abandonment, latency and missing telemetry; " +
        "define cohorts/denominators/windows, outputs/units, low-sample gaps and owned investigation/stop/rollback triggers. " +
        "Telemetry owner validates drafts with approved enabled-E2E evidence before rollout reliance; no production activation.";
    const ctx = context([evidence(), d, failed, corrected, review(corrected)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(packet.reviews.length, 2);
    assert.equal(packet.humanApproval, "pending");
    assert.equal(ctx.calls[3].options.label, "Design revision");
    assert.match(ctx.calls[3].prompt.split("\nDATA:\n")[0], /actual Kusto \(KQL\) draft text/);
    assert.equal(ctx.calls[4].options.label, "Independent design reviewer");
    const data = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
    assert.equal(data.designDigest, digest(corrected));
    assert.equal(data.design.sections.preMortemAndOperationalReadiness, corrected.sections.preMortemAndOperationalReadiness);
});

test("review omission or failure of the pre-mortem criterion cannot bypass its gate", async () => {
    const missing = review();
    missing.criteria = missing.criteria.filter((criterion) => criterion.id !== "preMortemAndReleaseGates");
    assert.equal(validateReview(missing, design(), evidence()), false);
    const malformed = await runDesignFeature(context([evidence(), design(), missing]));
    assert.equal(malformed.recommendation, "NO-GO");
    assert.equal(malformed.reviewReady, false);
    const failed = review();
    failed.criteria.find((criterion) => criterion.id === "preMortemAndReleaseGates").pass = false;
    const ctx = context([evidence(), design(), failed, design(), failed, design(), failed]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.fixIterations, 2);
    assert.equal(ctx.calls.length, 7);
});

test("pre-mortem authoring-brief findings require repair and a fresh independent review", async () => {
    const d = design();
    const failed = review(d);
    failed.criteria.find((criterion) => criterion.id === "preMortemAndReleaseGates").pass = false;
    failed.findings.push({
        id: "PM1", rubricId: "preMortemAndReleaseGates", severity: "major",
        location: "sections.preMortemAndOperationalReadiness",
        evidence: "Rollback omitted active attempts and telemetry validation had no owner.",
        requiredChange: "Add in-flight rollback and assign the enabled-E2E validation owner.",
    });
    const corrected = design();
    corrected.sections.preMortemAndOperationalReadiness =
        "Rollback: active/in-flight attempts retain admitted behavior; release owner verifies recovery. " +
        "Telemetry owner validates later agent-authored queries against an approved enabled E2E test cohort before monitoring is relied on.";
    const ctx = context([evidence(), d, failed, corrected, review(corrected)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(packet.reviews.length, 2);
    assert.equal(ctx.calls.length, 5);
    const revision = ctx.calls[3];
    assert.equal(revision.options.label, "Design revision");
    assert.equal(revision.options.schema, undefined);
    assert.ok(designSchema.properties.sections.required.includes("preMortemAndOperationalReadiness"));
    assert.match(revision.prompt.split("\nDATA:\n")[0], /## 12\. Pre-Mortem and Operational Readiness \(preMortemAndOperationalReadiness\)/);
    assert.match(revision.prompt.split("\nDATA:\n")[0], /only after all feature implementation is complete/);
    assert.match(revision.prompt.split("\nDATA:\n")[0], /actual KQL drafts/);
    assert.equal(ctx.calls[4].options.label, "Independent design reviewer");
    const data = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
    assert.equal(data.design.sections.preMortemAndOperationalReadiness, corrected.sections.preMortemAndOperationalReadiness);
    assert.equal(data.designDigest, digest(corrected));
});

test("owned future mapping gaps and absent production data do not require activation to plan authoring", async () => {
    const e = evidence();
    e.decisionRegister = [decision({
        id: "L1", category: "release-check", changeIds: [],
        owner: "Telemetry owner (unassigned)",
        question: "Proposed telemetry mappings require enabled-E2E validation by the telemetry owner.",
    })];
    const d = design();
    d.evidenceDigest = digest(e);
    d.sections.preMortemAndOperationalReadiness =
        "Authoring brief: future query table/property mappings remain unresolved. " +
        "No production data while disabled; telemetry owner validates later queries with an approved enabled E2E cohort. " +
        "Agent authoring follows all implementation completion and PBI dispatch, then human review before release. " +
        "Release owner separately approves release prerequisites; document review is not rollout approval.";
    const ctx = context([e, d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.humanApproval, "pending");
    assert.deepEqual(packet.evidence.decisionRegister, e.decisionRegister);
    assert.match(ctx.calls[0].prompt.split("\nDATA:\n")[0], /Missing production data while disabled is expected/);
    assert.match(ctx.calls[1].prompt.split("\nDATA:\n")[0], /Broad production enablement is not required/);
    assert.match(ctx.calls[2].prompt.split("\nDATA:\n")[0], /not rollout approval/);
});

test("healthy-operation handoff does not reuse v5-v14 outputs", async () => {
    const legacyDesign = design();
    delete legacyDesign.sections.accessibility;
    delete legacyDesign.sections.preMortemAndOperationalReadiness;
    delete legacyDesign.sections.featureFlagsAndIsolation;
    delete legacyDesign.featureFlags;
    delete legacyDesign.changes[0].runtimeBehaviorChange;
    delete legacyDesign.changes[0].flagIds;
    delete legacyDesign.changes[0].noFlagReason;
    const legacyReview = review(legacyDesign);
    legacyReview.criteria = legacyReview.criteria.filter((criterion) => criterion.id !== "preMortemAndReleaseGates");
    const ctx = context([evidence(), design(), review()]);
    const journal = new Map();
    for (const version of ["v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14"]) {
        const prefix = `design-feature-${version}-${digest(ctx.args)}`;
        const oldDesign = ["v8", "v9", "v10", "v11", "v12", "v13", "v14"].includes(version) ? design() : legacyDesign;
        if (!["v10", "v11", "v12", "v13", "v14"].includes(version)) delete oldDesign.sections.accessibility;
        const oldReview = ["v8", "v9", "v10", "v11", "v12", "v13", "v14"].includes(version) ? review(oldDesign) : legacyReview;
        oldReview.criteria = oldReview.criteria.filter((criterion) => criterion.id !== "healthyOperationAndFailureDetection");
        journal.set(`${prefix}-evidence`, evidence());
        journal.set(`${prefix}-${digest(evidence())}-design`, oldDesign);
        journal.set(`${prefix}-${digest(evidence())}-independent-review-0-${digest(oldDesign)}-${digest([])}`, oldReview);
    }
    ctx.step = async (key, producer) => {
        ctx.keys.push(key);
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(ctx.calls.length, 3);
    assert.ok(ctx.keys.every((key) => key.startsWith("design-feature-v16-")));
    assert.ok(packet.design.sections.preMortemAndOperationalReadiness);
    assert.ok(packet.design.sections.accessibility);
});

test("accessibility is a required design section and omission is repaired before review", async () => {
    for (const value of [undefined, "", " "]) {
        const incomplete = design();
        if (value === undefined) delete incomplete.sections.accessibility;
        else incomplete.sections.accessibility = value;
        assert.match(validateDesign(incomplete, stage0(), evidence()).join(" "), /Missing design section: accessibility/);
    }
    const incomplete = design();
    delete incomplete.sections.accessibility;
    const ctx = context([evidence(), incomplete, design(), review()]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls[2].options.label, "Design revision");
    assert.ok(designSchema.properties.sections.required.includes("accessibility"));
    for (const index of [1, 2]) {
        assert.equal(ctx.calls[index].options.schema, undefined);
        assert.match(ctx.calls[index].prompt.split("\nDATA:\n")[0], /## 9\. Accessibility \(accessibility\)/);
    }
});

function accessibilityFixture() {
    const s = stage0();
    s.requirements[0].text = "Keep the prompt and retry screens accessible during navigation, failure and return.";
    const e = evidence();
    e.scenarioTraces[0].scenario = "Prompt to retry screen and back";
    e.scenarioTraces[0].steps = [
        { behavior: "Prompt exposes its heading and Continue action.", referenceIds: ["E1"] },
        { behavior: "Loading disables Continue; failure presents retry actions.", referenceIds: ["E1"] },
        { behavior: "Cancellation returns to the prompt and restores its focus.", referenceIds: ["E1"] },
    ];
    const d = design();
    d.evidenceDigest = digest(e);
    d.sections.accessibility = `Applicable: changed Android UI, R1; SCREEN-PROMPT and SCREEN-RETRY.
Reuse the existing heading, localized action names/roles/states and focus restoration.
TalkBack reads the prompt before Continue and announces loading/error once; Back restores entry focus.
Keyboard-only entry reaches Continue, Retry and Back without touch or forced focus, with visible focus.
Large text/display scaling reflows both screens without clipping; all interactive targets are 48dp by 48dp.
Errors have text/non-color cues and contrast follows approved platform guidance.
Cover success, loading, error, retry, cancellation, return and restoration states on both screens.
R1 runtime checks: QA owner; private evidence location; Unverified, planned for the feature testing PBI.
Record build/commit, device/Android, TalkBack/input, font/display/theme configuration, steps and outcomes.
Execute real-app TalkBack and separate keyboard-only checks before applicable release acceptance.
Compilation and semantics/navigation assertions are supporting evidence, not runtime acceptance.`;
    d.traceability[0].sectionIds = ["accessibility", "testsAndEvals"];
    d.traceability[0].verification = "QA executes R1 TalkBack and separate keyboard-only checks on both screens; evidence planned.";
    d.changes[0].sectionIds = ["accessibility", "testsAndEvals"];
    return { s, e, d };
}

test("multi-screen UI design carries accessibility traceability and planned evidence through the real schemas", async () => {
    const { s, e, d } = accessibilityFixture();
    const ctx = context([e, d, review(d)], s);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.humanApproval, "pending");
    assert.deepEqual(packet.stage0.requirements, s.requirements);
    assert.deepEqual(packet.design.traceability, d.traceability);
    assert.equal(packet.design.sections.accessibility, d.sections.accessibility);
    assert.ok(ctx.calls[2].options.schema.properties.criteria.items.properties.id.enum.includes("accessibilityDesignAndVerification"));
    assert.match(ctx.calls[0].prompt, /owning.*accessibility guidance/);
    assert.match(ctx.calls[1].prompt, /48dp by 48dp/);
    assert.match(ctx.calls[1].prompt, /TalkBack.*keyboard-only/);
    assert.match(ctx.calls[2].prompt, /missing applicable coverage/);
    assert.match(ctx.calls[2].prompt, /Unverified.*Blocked/);
});

test("omitted accessibility review criterion fails closed even when all other checks pass", async () => {
    const r = review();
    r.criteria = r.criteria.filter((c) => c.id !== "accessibilityDesignAndVerification");
    const ctx = context([evidence(), design(), r]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.reviewReady, false);
    assert.equal(ctx.calls.length, 3);
});

test("runtime UI retains accessibility, default-off flags and post-implementation pre-mortem gates together", async () => {
    const { s, e, d } = accessibilityFixture();
    const flagged = runtimeDesign();
    d.featureFlags = flagged.featureFlags;
    d.changes = flagged.changes;
    d.sections.featureFlagsAndIsolation = flagged.sections.featureFlagsAndIsolation;
    const ctx = context([e, d, review(d)], s);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.design.sections.accessibility, d.sections.accessibility);
    assert.deepEqual(packet.design.featureFlags, flagged.featureFlags);
    assert.match(ctx.calls[1].prompt, /only after all feature implementation is complete/);
    for (const criterionId of [
        "accessibilityDesignAndVerification", "defaultOffFeatureIsolation", "preMortemAndReleaseGates",
    ]) {
        assert.ok(ctx.calls[2].options.schema.properties.criteria.items.properties.id.enum.includes(criterionId));
        const incomplete = review(d);
        incomplete.criteria = incomplete.criteria.filter((criterion) => criterion.id !== criterionId);
        assert.equal(validateReview(incomplete, d, e), false);
    }
});

test("missing applicable keyboard/state coverage cannot pass and every revision retains the accessibility contract", async () => {
    const { s, e, d } = accessibilityFixture();
    d.sections.accessibility = "Applicable: R1. Reuse accessible shared components; keyboard and return-state coverage unspecified.";
    const failed = review(d);
    failed.criteria.find((c) => c.id === "accessibilityDesignAndVerification").pass = false;
    failed.findings = [{
        id: "A11Y-1", rubricId: "accessibilityDesignAndVerification", severity: "major",
        location: "accessibility", evidence: "R1 omits keyboard entry and per-state focus decisions.",
        requiredChange: "Map keyboard and return-state behavior to R1 and owned verification.",
    }];
    const ctx = context([e, d, failed, d, failed, d, failed], s);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.reviewReady, false);
    assert.equal(packet.fixIterations, 2);
    assert.equal(ctx.calls.length, 7);
    for (const index of [3, 5]) {
        assert.match(ctx.calls[index].prompt, /Accessibility design/);
        assert.match(ctx.calls[index].prompt, /justified N\/A/);
        assert.match(ctx.calls[index].prompt, /not runtime accessibility acceptance or rollout approval/);
    }
});

test("non-UI design retains justified N/A without requiring runtime activation or invented UI requirements", async () => {
    const d = design();
    d.sections.accessibility = "N/A: R1 preserves a background library contract; no screens, UI states or navigation are affected.";
    const ctx = context([evidence(), d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.deepEqual(packet.stage0, stage0());
    assert.equal(packet.humanApproval, "pending");
    assert.match(ctx.calls[1].prompt, /N\/A.*reason/);
    assert.match(ctx.calls[1].prompt, /planned.*Unverified/);
    assert.match(ctx.calls[1].prompt, /requirements.*unchanged/);
});

test("changed accessibility schema and rubric never replay pre-accessibility v5 journal output", async () => {
    const legacy = design();
    delete legacy.sections.accessibility;
    const legacyReview = review(legacy);
    legacyReview.criteria = legacyReview.criteria.filter((c) => c.id !== "accessibilityDesignAndVerification");
    const ctx = context([evidence(), design(), review()]);
    const prefix = `design-feature-v5-${digest(ctx.args)}`;
    const journal = new Map([
        [`${prefix}-evidence`, evidence()],
        [`${prefix}-${digest(evidence())}-design`, legacy],
        [`${prefix}-${digest(evidence())}-independent-review-0-${digest(legacy)}-${digest([])}`, legacyReview],
    ]);
    ctx.step = async (key, producer) => {
        ctx.keys.push(key);
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    assert.equal((await runDesignFeature(ctx)).recommendation, readyRecommendation);
    assert.equal(ctx.calls.length, 3);
    assert.ok(ctx.keys.every((key) => key.startsWith("design-feature-v16-")));
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
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.deepEqual(packet.rubric, [...rubric]);
});

test("blocking decisions from authors and reviewers require clarification", async () => {
    const blockedDesign = design();
    blockedDesign.decisionRegister.push(decision({
        id: "Q1", category: "missing-requirement", question: "Need latency target.",
    }));
    const blockedReview = review();
    blockedReview.decisionRegister = [decision({
        id: "Q2", category: "missing-requirement", question: "Need API owner.",
    })];
    for (const results of [
        [evidence(), blockedDesign],
        [evidence(), design(), blockedReview],
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
    assert.equal(packet.recommendation, readyRecommendation);
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
    assert.equal((await runDesignFeature(resumed)).recommendation, readyRecommendation);
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
        maxConcurrentSubagents: 1, maxTotalSubagents: 7, timeoutSeconds: 7200, maxAiCredits: 20,
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
        assert.equal(packet.recommendation, readyRecommendation);
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
        (e) => { e.scenarioTraces[0].uncertaintyIds = ["missing-id"]; },
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
        (r) => { r.evidenceDigest = "old"; },
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

const discoveredReference = () => ({
    id: "E2", ...sourceManifest()[0], path: "CompletionObserver.kt", locator: "onCompletion",
});
function discoveryRepairFixture() {
    const original = evidence();
    const incorrect = runtimeDesign();
    Object.assign(incorrect.changes[0], {
        classification: "verified-defect", implementation: "required",
        counterevidence: { ...incorrect.changes[0].counterevidence, result: "confirmed-gap" },
    });
    const disputed = review(incorrect);
    disputed.references = [discoveredReference()];
    disputed.changeChecks[0].referenceIds = ["E2"];
    disputed.changeChecks[0].necessityConfirmed = false;
    disputed.findings.push({
        id: "F1", rubricId: "changeNecessityAndCounterevidence", severity: "major",
        location: "changes.C1", evidence: "E2: CompletionObserver already handles the result.",
        requiredChange: "Preserve existing behavior and cite E2 rather than adding a handler.",
    });
    const discoveries = [{ round: 0, references: disputed.references }];
    const expanded = {
        ...original, references: [...original.references, ...disputed.references],
        decisionRegister: [...original.decisionRegister],
        referenceDiscoveries: discoveries,
        decisionDiscoveries: [],
    };
    const corrected = design();
    corrected.evidenceDigest = digest(expanded);
    corrected.traceability[0].evidenceIds = ["E1", "E2"];
    corrected.changes[0].evidenceIds = ["E2"];
    corrected.changes[0].counterevidence.referenceIds = ["E2"];
    const finalReview = review(corrected);
    finalReview.changeChecks[0].referenceIds = ["E2"];
    return { original, incorrect, disputed, expanded, corrected, finalReview };
}

test("reviewer discoveries survive repair, independent re-review and checkpoint replay without overwriting evidence", async () => {
    const f = discoveryRepairFixture();
    const journal = new Map();
    const step = async (key, producer) => {
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    const ctx = context([f.original, f.incorrect, f.disputed, f.corrected, f.finalReview]);
    ctx.step = step;
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls.length, 5);
    assert.deepEqual(packet.evidence, evidence());
    assert.deepEqual(packet.availableEvidence, f.expanded);
    assert.deepEqual(packet.referenceDiscoveries, f.expanded.referenceDiscoveries);
    assert.deepEqual(packet.reviews[0].review.references, [discoveredReference()]);
    for (const index of [3, 4]) {
        const data = JSON.parse(ctx.calls[index].prompt.split("\nDATA:\n")[1]);
        assert.deepEqual(data.evidence, f.expanded);
        assert.equal(data.evidenceDigest, digest(f.expanded));
    }
    const keys = [...journal.keys()];
    assert.ok(keys[2].includes(digest(f.original)));
    assert.ok(keys[3].includes(digest(f.expanded)));
    assert.ok(keys[4].includes(digest(f.expanded)));
    assert.ok(keys[4].includes(digest(f.corrected)));
    assert.ok(keys[3].includes(digest(packet.reviews.slice(0, 1))));
    assert.ok(keys[4].includes(digest(packet.reviews.slice(0, 1))));
    assert.ok(validateDesign(f.corrected, stage0(), f.original).length);
    assert.equal(validateReview(f.disputed, f.corrected, f.expanded, sourceManifest()), false);
    const resumed = context([]);
    resumed.step = step;
    assert.deepEqual(await runDesignFeature(resumed), packet);
    assert.equal(resumed.calls.length, 0);
    assert.equal(journal.size, 5);
});

test("malformed, duplicate, colliding and unpinned reviewer references fail closed without losing original evidence", async () => {
    for (const references of [
        undefined, null, [null], [{ ...discoveredReference(), locator: "" }],
        [{ ...discoveredReference(), path: "" }], [{ ...discoveredReference(), id: "" }],
        [{ ...discoveredReference(), revision: newerSourceRevision }],
        [{ ...discoveredReference(), revision: "HEAD" }],
        [{ ...discoveredReference(), repository: "Unpinned" }],
        [discoveredReference(), discoveredReference()],
        [{ ...discoveredReference(), id: "E1" }], [evidence().references[0]],
    ]) {
        const r = review();
        r.references = references;
        const ctx = context([evidence(), design(), r]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(ctx.calls.length, 3);
        assert.deepEqual(packet.referenceDiscoveries, []);
        assert.deepEqual(packet.evidence, evidence());
        assert.deepEqual(packet.reviews, []);
    }
});

test("new references require a rebound design and fresh review even when the discovering reviewer passes", async () => {
    const f = discoveryRepairFixture();
    const passing = review();
    passing.references = [discoveredReference()];
    const ctx = context([evidence(), design(), passing, f.corrected, f.finalReview]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls.length, 5);
    const stale = context([evidence(), design(), passing, design(), design()]);
    const failed = await runDesignFeature(stale);
    assert.equal(failed.recommendation, "NO-GO");
    assert.equal(failed.fixIterations, 2);
    assert.ok(failed.reviews[1].validationErrors.some((error) => /evidence digest/.test(error)));
});

test("later reviews cannot redefine discoveries or reuse a stale evidence digest", async () => {
    for (const mutate of [
        (r) => { r.references = [discoveredReference()]; },
        (r) => { r.references = [{ ...discoveredReference(), path: "Replacement.kt" }]; },
        (r) => { r.evidenceDigest = digest(evidence()); },
    ]) {
        const f = discoveryRepairFixture();
        mutate(f.finalReview);
        const packet = await runDesignFeature(context([
            f.original, f.incorrect, f.disputed, f.corrected, f.finalReview,
        ]));
        assert.equal(packet.recommendation, "NO-GO");
        assert.deepEqual(packet.availableEvidence, f.expanded);
        assert.equal(packet.reviews.length, 1);
    }
});

test("successive review rounds accumulate discoveries within the unchanged seven-agent bound", async () => {
    const f = discoveryRepairFixture();
    const second = review(f.corrected);
    second.references = [{ ...discoveredReference(), id: "E3", path: "CompletionObserverTest.kt", locator: "handlesFailure" }];
    second.changeChecks[0].referenceIds = ["E2", "E3"];
    const expanded = {
        ...f.expanded, references: [...f.expanded.references, ...second.references],
        referenceDiscoveries: [...f.expanded.referenceDiscoveries, { round: 1, references: second.references }],
    };
    const finalDesign = structuredClone(f.corrected);
    finalDesign.evidenceDigest = digest(expanded);
    finalDesign.changes[0].counterevidence.referenceIds = ["E2", "E3"];
    const finalReview = review(finalDesign);
    finalReview.changeChecks[0].referenceIds = ["E2", "E3"];
    const journal = new Map();
    const step = async (key, producer) => {
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    const ctx = context([f.original, f.incorrect, f.disputed, f.corrected, second, finalDesign, finalReview]);
    ctx.step = step;
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 2);
    assert.equal(ctx.calls.length, 7);
    assert.deepEqual(packet.availableEvidence, expanded);
    assert.deepEqual(packet.evidence, evidence());
    const keys = [...journal.keys()];
    assert.ok(keys[5].includes(digest(expanded)));
    assert.ok(keys[6].includes(digest(expanded)));
    assert.ok(keys[6].includes(digest(finalDesign)));
    const resumed = context([]);
    resumed.step = step;
    assert.deepEqual(await runDesignFeature(resumed), packet);
    assert.equal(resumed.calls.length, 0);
    assert.equal(journal.size, 7);
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
    assert.equal(packet.recommendation, readyRecommendation);
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
    const d = runtimeDesign();
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
    assert.equal((await runDesignFeature(ctx)).recommendation, readyRecommendation);
    assert.equal(ctx.calls.length, 3);
});

test("an independently disputed gap must be corrected and re-reviewed", async () => {
    const e = completionEvidence(true);
    const incorrect = runtimeDesign();
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
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.design.changes[0].implementation, "regression-only");
});

test("checkpoint identity includes pinned sources, context, evidence, and schema version", async () => {
    const initial = context([evidence(), design(), review()]);
    await runDesignFeature(initial);
    assert.match(initial.keys[0], /^design-feature-v16-/);
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
    const withDecision = context([null]);
    withDecision.args.humanDecisions = [{
        id: "HD-1", topic: "Timeout", decision: "Use 30s timeout.", sourceReference: "review/hd-1",
    }];
    await runDesignFeature(withDecision);
    assert.notEqual(initial.keys[0], withDecision.keys[0]);
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

test("hybrid Markdown prose outputs for evidence and 13-section design author parse cleanly with host-stamped digests and paired brief", async () => {
    const evidenceMarkdown = `# Baseline Research Summary

Common already preserves compatibility via Contract.java.

## Evidence Metadata

\`\`\`json
{
  "approvalVerification": {
    "verified": true,
    "revision": "requirements-v3",
    "reference": "review/123",
    "rationale": "Inspected approval record review/123 bound to requirements-v3."
  },
  "references": [{ "id": "E1", "repository": "Common", "path": "Contract.java", "locator": "lines 10-20" }],
  "facts": [{ "statement": "Contract exists.", "referenceIds": ["E1"] }],
  "sourceVerification": [{ "repository": "Common", "verified": true, "rationale": "Verified at pinned commit." }],
  "scenarioTraces": [{
    "id": "T1", "requirementIds": ["R1"], "scenario": "Existing caller invokes Contract",
    "trigger": "Contract.call()", "conditions": "Default configuration",
    "steps": [{ "behavior": "Validates input and executes existing flow", "referenceIds": ["E1"] }],
    "terminalOutcome": { "behavior": "Returns compatible result to caller", "referenceIds": ["E1"] },
    "status": "verified"
  }],
  "decisionRegister": []
}
\`\`\``;
    const sampleRuntimeDesign = runtimeDesign();
    const designMarkdown = formatDesignMarkdown(sampleRuntimeDesign);
    assert.equal(sections.length, 13);
    for (const key of sections) {
        assert.ok(designMarkdown.includes(`## ${designSectionHeadings[key]} (${key})`));
    }
    const compactReview = {
        pairRevision: "placeholder",
        references: [],
        decisionRegister: [],
        criteria: rubric.map((id) => ({ id, pass: true })),
        changeChecks: [{
            changeId: "C1", baselineVerified: true, necessityConfirmed: true, counterevidenceVerified: true,
            referenceIds: ["E1"], rationale: "Counterevidence confirmed gap and flag isolation.",
        }],
        briefChecks: {
            consistent: true, complete: true, risksVisible: true,
            evidenceDistinguished: true, revisionChangesAccurate: true,
            rationale: "Brief matches all 13 detailed design sections and default-off flag register.",
        },
    };
    const ctx = context([evidenceMarkdown]);
    let turn = 0;
    ctx.agent = async (prompt, options) => {
        ctx.calls.push({ prompt, options });
        turn++;
        if (turn === 1) return evidenceMarkdown;
        if (turn === 2) return designMarkdown;
        const data = JSON.parse(prompt.split("\nDATA:\n")[1]);
        return { ...compactReview, pairRevision: data.pairRevision };
    };
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.reviewReady, true);
    assert.equal(packet.planningReady, false);
    assert.ok(packet.pairedDocuments);
    assert.equal(packet.pairedDocuments.appendices.length, 2);
    assert.equal(ctx.calls[0].options.schema, undefined);
    assert.equal(ctx.calls[1].options.schema, undefined);
    assert.ok(ctx.calls[2].options.schema);
    assert.equal(packet.evidence.references[0].revision, sourceRevision);
    assert.equal(packet.evidence.approvalVerification.verified, true);
    assert.equal(packet.evidence.priorReviewVerification, null);
    assert.equal(packet.design.stage0Revision, "requirements-v3");
    assert.deepEqual(packet.design.featureFlags, sampleRuntimeDesign.featureFlags);
});

test("round 2+ independent design review supplies deterministic reviewDelta and still catches new regressions", async () => {
    const first = runtimeDesign();
    const firstReview = review(first);
    firstReview.criteria = firstReview.criteria.map((c) =>
        c.id === "securityPrivacyAndPerformance" ? { ...c, pass: false } : c,
    );
    firstReview.findings.push({
        id: "F-SEC-1",
        rubricId: "securityPrivacyAndPerformance",
        severity: "major",
        location: "securityPrivacyPerformance",
        evidence: "Missing explicit PII scrubbing contract on error path.",
        requiredChange: "Specify Logger PII redaction in securityPrivacyPerformance.",
    });

    const second = runtimeDesign();
    second.sections.securityPrivacyPerformance = `${second.sections.securityPrivacyPerformance}\nScrub all PII via Logger error tags.`;
    second.humanReviewBrief.risks = second.humanReviewBrief.risks.map((risk) =>
        risk.category === "securityAuthentication"
            ? { ...risk, safeguards: `${risk.safeguards} Scrub all PII via Logger error tags.` }
            : risk,
    );

    const deltaPreview = buildDesignReviewDelta(first, second, [{ round: 0, review: firstReview }]);
    assert.deepEqual(deltaPreview.changedSections.map((s) => s.sectionId), ["securityPrivacyPerformance"]);
    assert.equal(deltaPreview.unchangedPassingSections.length, 12);
    assert.ok(deltaPreview.changedRegisters.includes("humanReviewBrief"));
    assert.equal(deltaPreview.priorOpenFindings.length, 1);
    assert.deepEqual(deltaPreview.priorFailedCriteria, ["securityPrivacyAndPerformance"]);

    const secondReviewWithRegression = review(second);
    secondReviewWithRegression.criteria = secondReviewWithRegression.criteria.map((c) =>
        c.id === "measurableTelemetryAndSuccess" ? { ...c, pass: false } : c,
    );
    secondReviewWithRegression.findings.push({
        id: "F-TEL-2",
        rubricId: "measurableTelemetryAndSuccess",
        severity: "blocker",
        location: "telemetryAndSuccessCriteria",
        evidence: "New log tag introduced without span correlation.",
        requiredChange: "Bind error tag to correlation_id in telemetryAndSuccessCriteria.",
    });

    const third = structuredClone(second);
    third.sections.telemetryAndSuccessCriteria = `${third.sections.telemetryAndSuccessCriteria}\nBind error tag to correlation_id.`;

    const ctx = context([evidence(), first, firstReview, second, secondReviewWithRegression, third, review(third)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 2);

    const round0Data = JSON.parse(ctx.calls[2].prompt.split("\nDATA:\n")[1]);
    assert.equal(round0Data.reviewDelta, undefined);

    const round1Data = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
    assert.deepEqual(round1Data.reviewDelta.changedSections.map((s) => s.sectionId), ["securityPrivacyPerformance"]);
    assert.equal(round1Data.reviewDelta.unchangedPassingSections.length, 12);
    assert.equal(round1Data.reviewDelta.priorOpenFindings[0].id, "F-SEC-1");

    const round2Data = JSON.parse(ctx.calls[6].prompt.split("\nDATA:\n")[1]);
    assert.deepEqual(round2Data.reviewDelta.changedSections.map((s) => s.sectionId), ["telemetryAndSuccessCriteria"]);
    assert.equal(round2Data.reviewDelta.priorOpenFindings[0].id, "F-TEL-2");
});

test("omitted approvalVerification in Markdown or object evidence stays fail-closed without synthesizing verification", async () => {
    const unverifiedMarkdown = `# Baseline Research Summary

## Evidence Metadata

\`\`\`json
{
  "references": [{ "id": "E1", "repository": "Common", "path": "Contract.java", "locator": "lines 10-20" }],
  "facts": [{ "statement": "Contract exists.", "referenceIds": ["E1"] }],
  "sourceVerification": [{ "repository": "Common", "verified": true, "rationale": "Verified at pinned commit." }],
  "scenarioTraces": [{
    "id": "T1", "requirementIds": ["R1"], "scenario": "Existing caller invokes Contract",
    "trigger": "Contract.call()", "conditions": "Default configuration",
    "steps": [{ "behavior": "Validates input and executes existing flow", "referenceIds": ["E1"] }],
    "terminalOutcome": { "behavior": "Returns compatible result to caller", "referenceIds": ["E1"] },
    "status": "verified"
  }],
  "decisionRegister": []
}
\`\`\``;
    const s0 = stage0();
    s0.approval.reference = "unread/nonexistent-approval-ref";
    const parsed = parseEvidenceOutput(unverifiedMarkdown, s0, sourceManifest(), undefined);
    assert.equal(parsed.approvalVerification, undefined);
    assert.deepEqual(
        validateEvidence(parsed, s0, sourceManifest(), undefined),
        ["Evidence worker could not verify the approval record for the exact Stage 0 revision."],
    );

    const ctx = context([unverifiedMarkdown], s0);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "needs clarification");
    assert.deepEqual(packet.reasons, ["Evidence worker could not verify the approval record for the exact Stage 0 revision."]);
    assert.equal(ctx.calls.length, 1);
});

test("parseDesignSections keeps ### and #### subsections inside their parent ## section even when titled like another section", () => {
    const sample = runtimeDesign();
    sample.sections.accessibility = "TalkBack announces state transitions and keeps 48dp touch targets.";
    sample.sections.testsAndEvals = [
        "### Accessibility",
        "Verify TalkBack announcements and focus order with Espresso.",
        "",
        "#### Feature Flags and Isolation",
        "Verify default-off flag F1 returns baseline response when disabled.",
    ].join("\n");
    const rawMarkdown = formatDesignMarkdown(sample);
    const { markdownBody } = extractTrailingJsonBlock(rawMarkdown);
    const parsedSections = parseDesignSections(markdownBody);

    assert.equal(parsedSections.accessibility, sample.sections.accessibility);
    assert.equal(parsedSections.testsAndEvals, sample.sections.testsAndEvals);
    assert.equal(parsedSections.featureFlagsAndIsolation, sample.sections.featureFlagsAndIsolation);
});

test("extractTrailingJsonBlock and parseDesignOutput preserve fenced code blocks with multi-token info strings before trailing metadata", () => {
    const sample = runtimeDesign();
    sample.sections.componentsInterfacesCompatibility = [
        "Updated adapter contract:",
        "",
        '```kotlin title="Example.kt" {linenos=true}',
        "fun execute(): Boolean = false",
        "```",
        "",
        "~~~kql role=\"query\" window=\"7d\"",
        "android_spans | take 5",
        "~~~",
    ].join("\n");
    const rawMarkdown = formatDesignMarkdown(sample);
    const extracted = extractTrailingJsonBlock(rawMarkdown);
    assert.ok(extracted.metadata);
    assert.deepEqual(extracted.metadata.featureFlags, sample.featureFlags);
    assert.match(extracted.markdownBody, /```kotlin title="Example\.kt" \{linenos=true\}/);

    const parsedDesign = parseDesignOutput(rawMarkdown, stage0(), evidence());
    assert.equal(parsedDesign.sections.componentsInterfacesCompatibility, sample.sections.componentsInterfacesCompatibility);
    assert.equal(validateDesign(parsedDesign, stage0(), evidence(), undefined).length, 0);
});

test("hash-bound appendices preserve the complete design and supplemental evidence without duplicate Markdown dumps", () => {
    const e = {
        ...evidence(),
        externalContextNotes: { source: "design-docs/legacy.md", note: "Historical context only." },
    };
    const d = runtimeDesign();
    d.evidenceDigest = digest(e);
    d.sections.componentsInterfacesCompatibility += '\n\n```json\n{"exampleApiField": true}\n```';
    const pair = renderPairedDocuments(d, e);
    const detailed = pair.documents[1].markdown;
    assert.equal(pair.appendices.length, 2);
    assert.deepEqual(pair.appendices.map((a) => [a.path, a.mediaType]), [
        ["appendices/design.json", "application/json"],
        ["appendices/evidence.json", "application/json"],
    ]);
    assert.equal(pair.appendices[0].content, JSON.stringify(d));
    assert.equal(pair.appendices[1].content, JSON.stringify(e));
    assert.equal(pair.appendices[0].sha256, digestBytes(pair.appendices[0].content));
    assert.equal(pair.appendices[1].sha256, digestBytes(pair.appendices[1].content));
    assert.equal(pair.appendices[0].sha256, digest(d));
    assert.equal(pair.appendices[1].sha256, digest(e));
    assert.ok(!pair.appendices[0].content.endsWith("\n"));
    assert.ok(!pair.appendices[1].content.endsWith("\n"));
    assert.deepEqual(JSON.parse(pair.appendices[0].content), d);
    assert.deepEqual(JSON.parse(pair.appendices[1].content), e);
    assert.ok(detailed.includes("## Contents"));
    assert.ok(detailed.includes("exampleApiField"));
    assert.ok(!detailed.includes('"scenarioTraces":'));
    assert.ok(!detailed.includes('"counterevidence":'));
    assert.ok(detailed.includes("| externalContextNotes | [Evidence appendix](appendices/evidence.json) |"));
    assert.ok(detailed.includes(pair.appendices[0].sha256));
    assert.ok(detailed.includes(pair.appendices[1].sha256));
});

test("readable registers retain every necessity, flag, traceability, scenario and source contract", () => {
    const d = runtimeDesign();
    const e = evidence();
    const pair = renderPairedDocuments(d, e);
    const detailed = pair.documents[1].markdown;
    for (const expected of [
        '<a id="change-necessity-register"></a>',
        '<a id="change-C1"></a>',
        d.changes[0].baselineBehavior,
        d.changes[0].requestedDelta,
        d.changes[0].necessity,
        d.changes[0].counterevidence.searchSummary,
        d.changes[0].counterevidence.conclusion,
        '<a id="feature-flag-register"></a>',
        '<a id="flag-F1"></a>',
        `${d.featureFlags[0].key} / ${d.featureFlags[0].repository}`,
        d.featureFlags[0].configurationPolicy,
        d.featureFlags[0].entryPoints[0].guardPlacement,
        d.featureFlags[0].entryPoints[0].disabledBehavior,
        '<a id="requirement-traceability"></a>',
        d.traceability[0].verification,
        '<a id="decisions-and-uncertainty"></a>',
        '<a id="decision-D1"></a>',
        d.decisionRegister[0].question,
        '<a id="source-facts"></a>',
        e.facts[0].statement,
        '<a id="scenario-traces"></a>',
        '<a id="trace-T1"></a>',
        e.scenarioTraces[0].trigger,
        e.scenarioTraces[0].terminalOutcome.behavior,
        '<a id="pinned-sources-and-references"></a>',
        '<a id="source-S1"></a>',
        '<a id="evidence-E1"></a>',
        sourceRevision,
        '<a id="approval-provenance"></a>',
        '<a id="provenance-appendices"></a>',
    ]) {
        assert.ok(detailed.includes(expected), `Missing detailed-design content: ${expected}`);
    }
});

test("table rendering safely preserves pipes, HTML and all line-ending forms", () => {
    const d = runtimeDesign();
    const special = "Line 1 | col <tag> & detail\r\nLine 2\rLine 3\nLine 4";
    d.changes[0].baselineBehavior = special;
    d.humanReviewBrief.behaviorSummary[0].behavior = special;
    const pair = renderPairedDocuments(d, evidence());
    const escaped = "Line 1 \\| col &lt;tag&gt; &amp; detail<br>Line 2<br>Line 3<br>Line 4";
    assert.ok(pair.documents[0].markdown.includes(escaped));
    assert.ok(pair.documents[1].markdown.includes(escaped));
    assert.equal(JSON.parse(pair.appendices[0].content).changes[0].baselineBehavior, special);
});

test("every generated brief, register, trace and evidence link resolves within the complete package", () => {
    const d = runtimeDesign();
    const pair = renderPairedDocuments(d, evidence());
    const [brief, detailed] = pair.documents;
    const appendixPaths = new Set(pair.appendices.map((a) => a.path));
    for (const [, anchor] of brief.markdown.matchAll(/detailed-design\.md#([^)\s]+)/g)) {
        assert.ok(detailed.markdown.includes(`<a id="${anchor}"></a>`), `Unresolved brief link: ${anchor}`);
    }
    for (const [, anchor] of detailed.markdown.matchAll(/\]\(#([^)]+)\)/g)) {
        assert.ok(detailed.markdown.includes(`<a id="${anchor}"></a>`), `Unresolved detailed link: ${anchor}`);
    }
    for (const [, target] of detailed.markdown.matchAll(/\]\((appendices\/[^)]+)\)/g)) {
        assert.ok(appendixPaths.has(target), `Unresolved appendix link: ${target}`);
    }
});

test("publication rejects missing, additional, renamed, mixed and tampered appendices even with refreshed hashes", () => {
    const d = design();
    const pair = renderPairedDocuments(d, evidence());
    const revised = design();
    revised.sections.requirements += " Revised requirement note.";
    const other = renderPairedDocuments(revised, evidence());
    for (const mutate of [
        (p) => { delete p.appendices; },
        (p) => { p.appendices.pop(); },
        (p) => { p.appendices.push({ ...p.appendices[0], path: "appendices/extra.json" }); },
        (p) => { p.appendices[0].path = "appendices/renamed.json"; },
        (p) => { p.appendices[0] = other.appendices[0]; },
        (p) => {
            p.appendices[0].content += "\n";
            p.appendices[0].sha256 = digestBytes(p.appendices[0].content);
        },
        (p) => {
            p.appendices[1].content = JSON.stringify(evidence(), null, 2);
            p.appendices[1].sha256 = digestBytes(p.appendices[1].content);
        },
    ]) {
        const changed = structuredClone(pair);
        mutate(changed);
        assert.equal(validatePairedDocuments(changed, d, evidence()), false);
    }
});

test("mismatched evidence cannot be rendered or accepted as a valid appendix package", () => {
    const otherEvidence = evidence();
    otherEvidence.facts[0].statement = "Modified fact.";
    assert.throws(() => renderPairedDocuments(design(), otherEvidence), /mismatched evidence/);
    const pair = renderPairedDocuments(design(), evidence());
    assert.equal(validatePairedDocuments(pair, design(), otherEvidence), false);
});

test("source pins are indexed once and every reference retains its exact repository and revision", () => {
    const e = evidence();
    e.references.push({ id: "E2", repository: "Common", revision: sourceRevision, path: "Helper.kt", locator: "Helper" });
    const d = design();
    d.evidenceDigest = digest(e);
    const detailed = renderPairedDocuments(d, e).documents[1].markdown;
    assert.equal((detailed.match(/<a id="source-S1"><\/a>/g) || []).length, 1);
    assert.equal((detailed.match(/\[S1\]\(#source-S1\)/g) || []).length, 2);
    assert.equal((detailed.match(new RegExp(sourceRevision, "g")) || []).length, 1);
    assert.ok(detailed.includes("Helper.kt"));
});

test("independent review receives exact brief, compact detailedDesignSummary, and hash-bound appendixDescriptors without duplicating detailed-design.md", async () => {
    const ctx = context([evidence(), runtimeDesign(), review(runtimeDesign())]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    const reviewerData = JSON.parse(ctx.calls[2].prompt.split("\nDATA:\n")[1]);
    assert.equal(reviewerData.renderedBrief, packet.pairedDocuments.documents[0].markdown);
    assert.equal(reviewerData.renderedDetailedDesign, undefined);
    assert.equal(reviewerData.detailedDesignSummary.path, "detailed-design.md");
    assert.equal(reviewerData.detailedDesignSummary.sha256, packet.pairedDocuments.documents[1].sha256);
    assert.equal(reviewerData.detailedDesignSummary.byteLength, Buffer.byteLength(packet.pairedDocuments.documents[1].markdown, "utf8"));
    assert.equal(reviewerData.detailedDesignSummary.sectionAnchors.length, sections.length);
    assert.equal(reviewerData.detailedDesignSummary.hostVerified, true);
    assert.deepEqual(reviewerData.appendixDescriptors,
        packet.pairedDocuments.appendices.map(({ path, sha256 }) => ({ path, sha256 })));
});

test("the decision/status format cannot reuse format-3 or legacy review identity", async () => {
    const d = design();
    const format3Revision = digest({
        documentFormatVersion: 3, stage0Revision: d.stage0Revision,
        evidenceDigest: d.evidenceDigest, design: d, priorReviewContext: null,
    });
    const legacyRevision = digest({
        stage0Revision: d.stage0Revision, evidenceDigest: d.evidenceDigest, design: d,
        priorReviewContext: null,
    });
    for (const staleRevision of [format3Revision, legacyRevision]) {
        assert.notEqual(pairRevision(d), staleRevision);
        const stale = review(d);
        stale.pairRevision = staleRevision;
        assert.equal(validateReview(stale, d, evidence()), false);
        const packet = await runDesignFeature(context([evidence(), d, stale]));
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(packet.pairedDocuments, null);
    }
});

test("brief support links are deduplicated reference links without losing labels or targets", () => {
    const markdown = renderHumanReviewBrief(design(), evidence());
    const definitions = [...markdown.matchAll(/^\[(d\d+)\]: (.+)$/gm)];
    assert.ok(definitions.length > 1);
    assert.equal(new Set(definitions.map((match) => match[2])).size, definitions.length);
    assert.equal(definitions.filter((match) => match[2] === "detailed-design.md#evidence-E1").length, 1);
    assert.match(markdown, /\[E1\]\[d\d+\]/);
    assert.match(markdown, /\[architecture and data control flow\]\[d\d+\]/);
    const defined = new Set(definitions.map((match) => match[1]));
    for (const match of markdown.matchAll(/\]\[(d\d+)\]/g)) assert.ok(defined.has(match[1]));
    assert.ok(markdown.includes(design().humanReviewBrief.outcomeAndScope.text));
    assert.ok(markdown.length < markdown.replace(/\[([^\]]+)\]\[(d\d+)\]/g, (_, label, id) =>
        `[${label}](${definitions.find((match) => match[1] === id)[2]})`).length);
});

test("brief renders plain-English Proposal/Tradeoff blocks, Situation | Behavior table, and compact changes table linked to detailed anchors", () => {
    const d = runtimeDesign();
    const pair = renderPairedDocuments(d, evidence());
    const brief = pair.documents[0].markdown;
    const detail = pair.documents[1].markdown;
    const dec = d.decisionRegister[0];
    assert.ok(brief.includes(`### ${dec.id}: ${dec.question}`));
    assert.ok(brief.includes(`**Proposal:** ${dec.recommendation}`));
    assert.ok(brief.includes(`**Tradeoff:** ${dec.tradeoffs}`));
    assert.ok(brief.includes(`**Rejected alternatives:** ${dec.rejectedAlternatives.join("; ")}`));
    assert.ok(brief.includes(`**Owner:** ${dec.owner} · **Status:** open (gate: planning; human approval pending) · **Changes:** C1 · **Detail:** `));
    assert.ok(brief.includes("| Situation | Behavior |"));
    for (const row of d.humanReviewBrief.behaviorSummary) {
        assert.ok(brief.includes(`| ${row.situation} | ${row.behavior} |`));
    }
    assert.ok(brief.includes("| Change | Baseline → Proposed | Flag / Scope | Detail |"));
    assert.match(brief, /\[C1 detail\]\[d\d+\]/);
    assert.match(brief, /^\[d\d+\]: detailed-design\.md#change-C1$/m);
    assert.ok(detail.includes('<a id="change-C1"></a>'));
    assert.ok(detail.includes(`| Current baseline | ${d.changes[0].baselineBehavior} |`));
    assert.ok(detail.includes(`| Counterevidence conclusion | ${d.changes[0].counterevidence.conclusion} |`));
});

test("brief rendering preserves an explicit disabled compatibility exception without asserting otherwise", () => {
    const d = runtimeDesign();
    d.changes[0].requestedDelta = "Suppress the untrusted reserved field even while disabled; preserve ordinary authentication.";
    const markdown = renderHumanReviewBrief(d, evidence());
    assert.ok(markdown.includes(d.changes[0].requestedDelta));
    assert.ok(markdown.includes("linked isolation and compatibility contracts"));
    assert.ok(!markdown.includes("Disabled behavior must preserve the baseline"));
});

test("author and reviewer prompts enforce plain-English brief guidance alongside Layer 1 flighting standards", async () => {
    const d = runtimeDesign();
    const ctx = context([evidence(), d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    const authorPrompt = ctx.calls[1].prompt.split("\nDATA:\n")[0];
    const reviewerPrompt = ctx.calls[2].prompt.split("\nDATA:\n")[0];
    assert.match(authorPrompt, /plain English with low jargon/i);
    assert.match(authorPrompt, /behaviorSummary/);
    assert.match(authorPrompt, /Decision -> Proposal -> Tradeoff/);
    assert.match(authorPrompt, /Android Auth Flighting Standard \(Team Defaults\)/);
    assert.match(reviewerPrompt, /plain-English, low-jargon readability/i);
    assert.match(reviewerPrompt, /behaviorSummary/);
    assert.match(reviewerPrompt, /Android Auth Flighting Standard \(Team Defaults\)/);
});

test("all worker schemas use canonical typed records instead of duplicate status lists", async () => {
    for (const schema of [evidenceSchema, designSchema, reviewSchema]) {
        assert.ok(schema.required.includes("decisionRegister"));
        for (const key of ["conflicts", "unknowns", "openDecisions", "blockingUnknowns"]) {
            assert.equal(Object.hasOwn(schema.properties, key), false);
        }
        const entry = schema.properties.decisionRegister.items;
        assert.deepEqual(entry.properties.category.enum,
            ["missing-requirement", "source-uncertainty", "design-acceptance", "release-check"]);
        assert.deepEqual(entry.properties.status.enum, ["open", "resolved"]);
        assert.ok(entry.required.includes("resolutionReference"));
        assert.equal(entry.additionalProperties, false);
        assert.equal(Object.hasOwn(entry.properties, "approvalBlocker"), false);
    }
    const brief = designSchema.properties.humanReviewBrief;
    assert.equal(Object.hasOwn(brief.properties, "decisions"), false);
    assert.ok(brief.required.includes("behaviorSummary"));
    const trace = evidenceSchema.properties.scenarioTraces.items;
    assert.ok(trace.required.includes("uncertaintyIds"));
    assert.equal(Object.hasOwn(trace.properties, "unknowns"), false);
    const ctx = context([evidence(), design(), review()]);
    await runDesignFeature(ctx);
    for (const call of ctx.calls) {
        assert.match(call.prompt.split("\nDATA:\n")[0], /decisionRegister/);
    }
});

test("each category blocks only its intended gate without granting human approval", async () => {
    for (const [category, gate, expectedCalls] of [
        ["missing-requirement", "drafting", 2], ["source-uncertainty", "drafting", 2],
        ["design-acceptance", "planning", 3], ["release-check", "release", 3],
    ]) {
        const d = design();
        d.decisionRegister.push(decision({ id: "Q1", category, question: `Owned ${category} obligation.` }));
        const ctx = context([evidence(), d, review(d)]);
        const packet = await runDesignFeature(ctx);
        assert.equal(ctx.calls.length, expectedCalls, category);
        assert.equal(packet.recommendation, gate === "drafting" ? "needs clarification" : readyRecommendation);
        assert.equal(packet.reviewReady, gate !== "drafting");
        assert.equal(packet.humanApproval, "pending");
        assert.equal(packet.planningReady, false);
        assert.deepEqual(packet.decisionSummary[category].filter((id) => id === "Q1"), ["Q1"]);
        assert.equal(packet.decisionRegister.find((entry) => entry.id === "Q1").blockingGate, gate);
        if (gate === "drafting") {
            assert.equal(packet.pairedDocuments, null);
            assert.match(packet.reasons.join(" "), /Q1.*Common design owner/);
        }
    }
});

test("research requirement/source blockers stop before authoring and retain owned canonical records", async () => {
    for (const category of ["missing-requirement", "source-uncertainty"]) {
        const e = evidence();
        e.decisionRegister = [decision({ id: "Q1", category, changeIds: [] })];
        const ctx = context([e]);
        const packet = await runDesignFeature(ctx);
        assert.equal(ctx.calls.length, 1);
        assert.equal(packet.recommendation, "needs clarification");
        assert.deepEqual(packet.decisionSummary[category], ["Q1"]);
        assert.equal(packet.decisionRegister[0].owner, "Common design owner");
        assert.equal(packet.decisionRegister[0].blockingGate, "drafting");
        assert.equal(packet.planningReady, false);
    }
});

test("approved Stage 0 blockers are normalized without changing the approval-bound input", async () => {
    const input = stage0();
    input.blockingUnknowns = ["Which account types are in scope?"];
    const ctx = context([], input);
    const before = structuredClone(ctx.args);
    const packet = await runDesignFeature(ctx);
    assert.equal(ctx.calls.length, 0);
    assert.equal(packet.recommendation, "needs clarification");
    assert.deepEqual(ctx.args, before);
    assert.deepEqual(packet.stage0, before.stage0);
    assert.deepEqual(packet.decisionSummary["missing-requirement"], ["stage0-1"]);
    assert.equal(packet.decisionRegister[0].question, input.blockingUnknowns[0]);
    assert.equal(packet.decisionRegister[0].owner, "Requirements owner (unassigned)");
    assert.equal(packet.decisionRegister[0].blockingGate, "drafting");
});

test("malformed states, ownership, links and closure provenance fail register validation", () => {
    const options = { stage0: stage0(), references: evidence().references, changes: design().changes };
    assert.deepEqual(validateDecisionRegister([decision()], options), []);
    for (const mutate of [
        (entry) => { delete entry.id; },
        (entry) => { entry.owner = " "; },
        (entry) => { entry.status = "approved"; },
        (entry) => { entry.category = "later"; },
        (entry) => { entry.requirementIds = []; },
        (entry) => { entry.requirementIds = ["R99"]; },
        (entry) => { entry.changeIds = ["C99"]; },
        (entry) => { entry.sectionIds = ["unknownSection"]; },
        (entry) => { entry.evidenceIds = ["E99"]; },
        (entry) => { entry.evidenceIds = ["E1", "E1"]; },
        (entry) => { entry.resolution = "Already resolved"; },
        (entry) => { entry.resolutionReference = "unverified/receipt"; },
        (entry) => { entry.blockingGate = "release"; },
        (entry) => { entry.approvalBlocker = false; },
        (entry) => { entry.category = "source-uncertainty"; entry.status = "resolved"; },
        (entry) => {
            Object.assign(entry, { category: "source-uncertainty", status: "resolved",
                resolution: "Unsupported assertion.", resolutionReference: "E99" });
        },
    ]) {
        const entry = decision();
        mutate(entry);
        assert.ok(validateDecisionRegister([entry], options).length, JSON.stringify(entry));
    }
    assert.ok(validateDecisionRegister([decision(), decision()], options).length);
});

test("resolved source discrepancies are reconciled provenance, never active conflicts", async () => {
    const e = evidence();
    e.decisionRegister = [decision({
        id: "S1", category: "source-uncertainty", status: "resolved", changeIds: [],
        question: "Stale documentation differs from the current pinned contract.",
        resolution: "E1 establishes the existing implementation contract; the old comment is stale.",
        resolutionReference: "E1",
    })];
    e.scenarioTraces[0].uncertaintyIds = ["S1"];
    assert.deepEqual(validateEvidence(e, stage0(), sourceManifest()), []);
    const d = design();
    d.evidenceDigest = digest(e);
    const packet = await runDesignFeature(context([e, d, review(d)]));
    assert.equal(packet.recommendation, readyRecommendation);
    assert.deepEqual(packet.decisionSummary["source-uncertainty"], []);
    assert.deepEqual(packet.decisionSummary.resolved, ["S1"]);
    assert.equal(packet.decisionRegister.find((entry) => entry.id === "S1").status, "resolved");
    for (const document of packet.pairedDocuments.documents) {
        assert.ok(document.markdown.includes("Resolved requirements / source facts"));
        assert.ok(document.markdown.includes("S1: Stale documentation differs"));
        assert.ok(document.markdown.includes("reference: E1"));
    }
    const detail = packet.pairedDocuments.documents[1].markdown;
    assert.ok(detail.includes(e.decisionRegister[0].resolution));
    assert.ok(detail.includes("[S1](#decision-S1)"));
    const unsupported = structuredClone(e);
    unsupported.decisionRegister[0].resolutionReference = "unverified/contract";
    assert.ok(validateEvidence(unsupported, stage0(), sourceManifest()).length);
});

test("resolved requirements need exact inspected approval provenance, not an AI assertion", async () => {
    const e = evidence();
    e.decisionRegister = [decision({
        id: "Q1", category: "missing-requirement", status: "resolved", changeIds: [],
        resolution: "The inspected approval explicitly resolves scope through R1.",
        resolutionReference: stage0().approval.reference,
    })];
    assert.deepEqual(validateEvidence(e, stage0(), sourceManifest()), []);
    const d = design();
    d.evidenceDigest = digest(e);
    const packet = await runDesignFeature(context([e, d, review(d)]));
    assert.equal(packet.recommendation, readyRecommendation);
    assert.deepEqual(packet.decisionSummary.resolved, ["Q1"]);
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.planningReady, false);
    for (const mutate of [
        (value) => { value.decisionRegister[0].resolutionReference = "invented/human-approval"; },
        (value) => { value.decisionRegister[0].requirementIds = ["unapproved-requirement"]; },
        (value) => { value.approvalVerification.verified = false; },
    ]) {
        const invalid = structuredClone(e);
        mutate(invalid);
        assert.ok(validateEvidence(invalid, stage0(), sourceManifest()).length);
        const stopped = await runDesignFeature(context([invalid]));
        assert.equal(stopped.recommendation, "needs clarification");
        assert.equal(stopped.reviewReady, false);
        assert.equal(stopped.planningReady, false);
    }
});

test("workers cannot resolve human acceptance or release gates even with a claimed receipt", async () => {
    for (const category of ["design-acceptance", "release-check"]) {
        const d = design();
        d.decisionRegister.push(decision({
            id: "Q1", category, status: "resolved",
            resolution: "The agent claims approval and completed execution.",
            resolutionReference: "claimed/human-or-test-receipt",
        }));
        const ctx = context([evidence(), d, d, d]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(packet.humanApproval, "pending");
        assert.equal(packet.planningReady, false);
        assert.equal(packet.pairedDocuments, null);
        assert.equal(packet.decisionRegister, null);
        assert.equal(packet.decisionSummary, null);
        assert.ok(ctx.calls.every((call) => call.options.label !== "Independent design reviewer"));
    }
});

test("global IDs prevent author records from shadowing immutable evidence obligations", async () => {
    const e = evidence();
    e.decisionRegister = [decision({ id: "L1", category: "release-check", changeIds: [] })];
    const d = design();
    d.evidenceDigest = digest(e);
    d.decisionRegister.push(decision({ id: "L1", owner: "Attempted replacement owner" }));
    assert.match(validateDesign(d, stage0(), e).join(" "), /globally unique/);
    const packet = await runDesignFeature(context([e, d, d, d]));
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.decisionRegister, null);
    assert.equal(packet.decisionSummary, null);
    assert.deepEqual(packet.evidence.decisionRegister, e.decisionRegister);
    assert.equal(packet.evidence.decisionRegister[0].owner, "Common design owner");
});

test("legacy status lists are rejected rather than silently copied or ignored", () => {
    for (const key of ["blockingUnknowns", "openDecisions", "conflicts", "unknowns"]) {
        const e = evidence();
        e[key] = [];
        assert.ok(validateEvidence(e, stage0(), sourceManifest()).length);
        const d = design();
        d[key] = [];
        assert.ok(validateDesign(d, stage0(), evidence()).length);
        const r = review();
        r[key] = [];
        assert.equal(validateReview(r, design(), evidence()), false);
    }
    const d = design();
    d.humanReviewBrief.decisions = [decision()];
    assert.ok(validateDesign(d, stage0(), evidence()).length);
    const e = evidence();
    e.scenarioTraces[0].unknowns = [];
    assert.ok(validateEvidence(e, stage0(), sourceManifest()).length);
});

test("unresolved baseline traces cannot be reclassified as non-blocking release checks", async () => {
    for (const status of ["verified", "unresolved"]) {
        const e = evidence();
        e.decisionRegister = [decision({ id: "L1", category: "release-check", changeIds: [] })];
        e.scenarioTraces[0].status = status;
        e.scenarioTraces[0].uncertaintyIds = ["L1"];
        const ctx = context([e]);
        const packet = await runDesignFeature(ctx);
        assert.equal(ctx.calls.length, 1);
        assert.equal(packet.recommendation, "needs clarification");
        assert.match(packet.reasons.join(" "), /uncertainty IDs.*never release checks/);
        assert.equal(packet.pairedDocuments, null);
    }
    const e = evidence();
    e.decisionRegister = [decision({ id: "S1", category: "source-uncertainty", changeIds: [] })];
    e.scenarioTraces[0].status = "unresolved";
    e.scenarioTraces[0].uncertaintyIds = ["S1"];
    const stopped = await runDesignFeature(context([e]));
    assert.deepEqual(stopped.decisionSummary["source-uncertainty"], ["S1"]);
    assert.match(stopped.reasons.join(" "), /S1.*Common design owner/);
});

test("both documents and the packet derive the same owned status instead of writable brief flags", async () => {
    const e = evidence();
    e.decisionRegister = [decision({
        id: "L1", category: "release-check", changeIds: [],
        owner: "Accessibility reviewer (unassigned)", question: "Record real-app keyboard acceptance after implementation.",
    })];
    const d = design();
    d.evidenceDigest = digest(e);
    d.decisionRegister[0].owner = "Platform design owner (unassigned)";
    d.decisionRegister[0].question = "Accept the unchanged contract and regression-only scope?";
    const canonical = collectDecisionRegister(d, e);
    const packet = await runDesignFeature(context([e, d, review(d)]));
    assert.equal(packet.recommendation, readyRecommendation);
    assert.deepEqual(packet.decisionSummary, summarizeDecisions(canonical));
    assert.deepEqual(packet.decisionRegister.map(({ blockingGate, ...entry }) => entry), canonical);
    const summaries = packet.pairedDocuments.documents.map((document) => document.markdown.split("\n")
        .filter((line) => /^\| (Missing requirements|Unresolved source facts|Pending design acceptance|Later release checks|Resolved requirements \/ source facts) \|/.test(line)));
    assert.equal(summaries[0].length, 5);
    assert.deepEqual(summaries[0], summaries[1]);
    for (const document of packet.pairedDocuments.documents) {
        for (const entry of canonical) {
            assert.ok(document.markdown.includes(entry.question));
            assert.ok(document.markdown.includes(entry.owner));
        }
        assert.ok(document.markdown.includes("Human approval: pending"));
        assert.ok(!document.markdown.includes("GO to Planning"));
    }
    assert.equal(Object.hasOwn(packet.design.humanReviewBrief, "decisions"), false);
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.planningReady, false);
});

test("new reviewer source uncertainty is retained canonically and stops for clarification", async () => {
    const r = review();
    r.references = [discoveredReference()];
    r.decisionRegister = [decision({
        id: "S1", category: "source-uncertainty", evidenceIds: ["E2"],
        owner: "Source owner (unassigned)", question: "Reconcile conflicting observer completion behavior.",
    })];
    const ctx = context([evidence(), design(), r]);
    const packet = await runDesignFeature(ctx);
    assert.equal(ctx.calls.length, 3);
    assert.equal(packet.recommendation, "needs clarification");
    assert.deepEqual(packet.evidence.decisionRegister, []);
    assert.deepEqual(packet.availableEvidence.decisionRegister, r.decisionRegister);
    assert.deepEqual(packet.decisionSummary["source-uncertainty"], ["S1"]);
    assert.equal(packet.decisionRegister.find((entry) => entry.id === "S1").blockingGate, "drafting");
    assert.equal(packet.pairedDocuments, null);
    assert.equal(packet.humanApproval, "pending");
});

test("reviewer release discoveries force digest rebinding, fresh review and durable checkpoint replay", async () => {
    const e = evidence();
    const d = design();
    const first = review(d);
    first.decisionRegister = [decision({
        id: "L1", category: "release-check", owner: "Release owner (unassigned)",
        question: "Validate the implemented rollback in an approved enabled test cohort.",
    })];
    const expanded = {
        ...e, references: [...e.references], decisionRegister: first.decisionRegister,
        referenceDiscoveries: [], decisionDiscoveries: [{ round: 0, decisions: first.decisionRegister }],
    };
    const corrected = design();
    corrected.evidenceDigest = digest(expanded);
    const journal = new Map();
    const step = async (key, producer) => {
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    const ctx = context([e, d, first, corrected, review(corrected)]);
    ctx.step = step;
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls.length, 5);
    assert.deepEqual(packet.availableEvidence, expanded);
    assert.deepEqual(packet.evidence, e);
    assert.deepEqual(packet.decisionSummary["release-check"], ["L1"]);
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.planningReady, false);
    const reviewerData = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
    assert.equal(reviewerData.evidenceDigest, digest(expanded));
    assert.ok(reviewerData.renderedBrief.includes(first.decisionRegister[0].question));
    assert.deepEqual(JSON.parse(packet.pairedDocuments.appendices[1].content), expanded);
    const replay = context([]);
    replay.step = step;
    assert.deepEqual(await runDesignFeature(replay), packet);
    assert.equal(replay.calls.length, 0);
    const stale = await runDesignFeature(context([e, d, first, d, d]));
    assert.equal(stale.recommendation, "NO-GO");
    assert.equal(stale.pairedDocuments, null);
    assert.ok(stale.reviews.some((entry) => entry.validationErrors?.some((error) => /exact validated evidence digest/.test(error))));
});

test("reviewers cannot shadow existing IDs, invent links, or resolve human gates", () => {
    for (const entry of [
        decision(),
        decision({ id: "Q1", requirementIds: ["R99"] }),
        decision({ id: "Q1", evidenceIds: ["E99"] }),
        decision({ id: "Q1", changeIds: ["C99"] }),
        decision({ id: "Q1", owner: "" }),
        decision({ id: "Q1", status: "resolved", resolution: "Agent claims acceptance", resolutionReference: "claimed/approval" }),
    ]) {
        const r = review();
        r.decisionRegister = [entry];
        assert.equal(validateReview(r, design(), evidence()), false, JSON.stringify(entry));
    }
});

test("changing a canonical owner, choice or state invalidates the exact reviewed pair", () => {
    for (const mutate of [
        (d) => { d.decisionRegister[0].owner = "New owner"; },
        (d) => { d.decisionRegister[0].recommendation = "A revised recommendation."; },
        (d) => { d.decisionRegister[0].category = "release-check"; },
    ]) {
        const original = design();
        const pair = renderPairedDocuments(original, evidence());
        const changed = structuredClone(original);
        mutate(changed);
        assert.notEqual(pairRevision(changed), pair.revision);
        assert.equal(validateReview(review(original), changed, evidence()), false);
        assert.equal(validatePairedDocuments(pair, changed, evidence()), false);
    }
});

test("malformed or absent stage registers do not become an all-clear empty status summary", async () => {
    for (const results of [
        [null],
        [{ ...evidence(), decisionRegister: undefined }],
        [evidence(), null],
        [evidence(), { ...design(), decisionRegister: null },
            { ...design(), decisionRegister: null }, { ...design(), decisionRegister: null }],
    ]) {
        const packet = await runDesignFeature(context(results));
        assert.equal(packet.reviewReady, false);
        assert.equal(packet.planningReady, false);
        assert.equal(packet.decisionRegister, null);
        assert.equal(packet.decisionSummary, null);
        assert.equal(packet.pairedDocuments, null);
    }
});

test("valid owned drafting blockers request input without spending fixes on an incomplete candidate", async () => {
    const d = design();
    d.sections.architectureAndDataControlFlow = "";
    d.changes[0].classification = "unresolved";
    d.decisionRegister.push(decision({
        id: "Q1", category: "missing-requirement", owner: "Requirements owner (unassigned)",
        question: "Clarify the required compatibility boundary before selecting the design.",
    }));
    const ctx = context([evidence(), d]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "needs clarification");
    assert.equal(ctx.calls.length, 2);
    assert.equal(packet.fixIterations, 0);
    assert.equal(packet.pairedDocuments, null);
    assert.equal(packet.humanApproval, "pending");
    assert.deepEqual(packet.decisionSummary["missing-requirement"], ["Q1"]);
});

test("contradictory narrative still requires a corrective revision and fresh independent review", async () => {
    const d = design();
    d.sections.dependenciesAndOpenDecisions = "No decisions remain; proceed directly to planning.";
    const disputed = review(d);
    disputed.briefChecks.consistent = false;
    disputed.findings = [{
        id: "F1", rubricId: "humanDesignReviewBrief", severity: "major",
        location: "sections.dependenciesAndOpenDecisions",
        evidence: "D1 is open for human acceptance, contrary to the narrative's all-clear claim.",
        requiredChange: "Reference D1 and retain the exact-revision human approval gate.",
    }];
    const corrected = design();
    corrected.sections.dependenciesAndOpenDecisions =
        "D1 awaits human design acceptance. Readiness is not planning permission.";
    const ctx = context([evidence(), d, disputed, corrected, review(corrected)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls.length, 5);
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.planningReady, false);
    assert.ok(packet.pairedDocuments.documents[1].markdown.includes(corrected.sections.dependenciesAndOpenDecisions));
    assert.ok(!packet.pairedDocuments.documents[1].markdown.includes(d.sections.dependenciesAndOpenDecisions));
    assert.match(ctx.calls[2].prompt.split("\nDATA:\n")[0], /reject stale counts or contrary status\/approval claims/);
});

test("buildDesignReviewDelta tracks decisionRegister changes alongside modified sections and registers", () => {
    const first = runtimeDesign();
    const second = runtimeDesign();
    second.decisionRegister.push(decision({
        id: "L2", category: "release-check", owner: "QA owner (unassigned)",
        question: "Verify disabled-path behavior under expired ECS configuration.",
    }));
    const delta = buildDesignReviewDelta(first, second, [{ round: 0, review: review(first) }]);
    assert.ok(delta.changedRegisters.includes("decisionRegister"));
    assert.equal(delta.changedSections.length, 0);
    assert.equal(delta.unchangedPassingSections.length, 13);
});

test("native continuation from validated evidence skips Repository evidence and runs author plus independent review", async () => {
    const savedEvidence = evidence();
    const authored = runtimeDesign();
    const ctx = context([authored, review(authored)]);
    ctx.args.continuation = { evidence: savedEvidence };
    ctx.args.humanDecisions = [{
        id: "HD-1",
        topic: "Timeout budget",
        decision: "Use a 30-second client timeout with no automatic retry.",
        sourceReference: "human-review/comment-10",
        recordedAt: "2026-11-23T12:00:00Z",
    }];
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.continuationMode, "from-evidence");
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.planningReady, false);
    assert.equal(packet.reviewReady, true);
    assert.ok(packet.pairedDocuments);
    assert.equal(ctx.calls.length, 2);
    assert.equal(ctx.calls[0].options.label, "Design author");
    assert.equal(ctx.calls[1].options.label, "Independent design reviewer");
    assert.ok(ctx.calls.every((call) => call.options.label !== "Repository evidence"));
    const authorData = JSON.parse(ctx.calls[0].prompt.split("\nDATA:\n")[1]);
    assert.deepEqual(authorData.humanDecisions.active.map((entry) => entry.id), ["HD-1"]);
    assert.deepEqual(authorData.humanDecisions.supersededIds, []);
    assert.equal(authorData.evidenceDigest, digest(savedEvidence));
});

test("native continuation from a prior design draft skips research, runs Design revision with updated humanDecisions, and requires fresh independent review", async () => {
    const savedEvidence = evidence();
    const priorDraft = runtimeDesign();
    const revised = runtimeDesign();
    revised.sections.stateFailuresRecoveryReversibility =
        `${revised.sections.stateFailuresRecoveryReversibility}\nApply HD-2 timeout policy (15s timeout).`;
    const ctx = context([revised, review(revised)]);
    ctx.args.continuation = { evidence: savedEvidence, design: priorDraft };
    ctx.args.humanDecisions = [
        {
            id: "HD-1",
            topic: "Timeout budget",
            decision: "Use a 30-second timeout.",
            sourceReference: "human-review/comment-10",
        },
        {
            id: "HD-2",
            topic: "Timeout budget",
            decision: "Tighten client timeout to 15 seconds.",
            sourceReference: "human-review/comment-11",
            supersedesIds: ["HD-1"],
        },
    ];
    ctx.args.context = "Incorporate HD-2 timeout tightening from pilot review.";
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.continuationMode, "from-draft");
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.planningReady, false);
    assert.equal(packet.fixIterations, 0);
    assert.equal(ctx.calls.length, 2);
    assert.equal(ctx.calls[0].options.label, "Design revision");
    assert.equal(ctx.calls[1].options.label, "Independent design reviewer");
    const revisionData = JSON.parse(ctx.calls[0].prompt.split("\nDATA:\n")[1]);
    assert.equal(revisionData.continuationMode, "from-draft");
    assert.deepEqual(revisionData.humanDecisions.active.map((entry) => entry.id), ["HD-2"]);
    assert.deepEqual(revisionData.humanDecisions.supersededIds, ["HD-1"]);
    assert.deepEqual(revisionData.design, priorDraft);
    const reviewerData = JSON.parse(ctx.calls[1].prompt.split("\nDATA:\n")[1]);
    assert.deepEqual(reviewerData.humanDecisions.active.map((entry) => entry.id), ["HD-2"]);
    assert.deepEqual(reviewerData.humanDecisions.supersededIds, ["HD-1"]);
    assert.equal(reviewerData.designDigest, digest(revised));
});

test("native continuation from draft rebinds cleanly when Stage 0 revision updates and still fails closed if independent review rejects", async () => {
    const oldDraft = runtimeDesign();
    const updatedStage0 = {
        ...stage0(),
        revision: "requirements-v4",
        approval: { approvedRevision: "requirements-v4", approvedBy: "human-owner", reference: "review/456" },
    };
    const updatedEvidence = evidence();
    updatedEvidence.approvalVerification = {
        verified: true,
        revision: "requirements-v4",
        reference: "review/456",
        rationale: "Verified requirements-v4 approval.",
    };
    assert.notEqual(oldDraft.evidenceDigest, digest(updatedEvidence));

    const rebound = runtimeDesign();
    rebound.stage0Revision = "requirements-v4";
    rebound.evidenceDigest = digest(updatedEvidence);
    const passingReview = review(rebound);
    const ctx = context([rebound, passingReview], updatedStage0);
    ctx.args.continuation = { evidence: updatedEvidence, design: oldDraft };
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);
    assert.equal(packet.continuationMode, "from-draft");
    assert.equal(packet.design.stage0Revision, "requirements-v4");
    assert.equal(packet.design.evidenceDigest, digest(updatedEvidence));
    assert.equal(ctx.calls.length, 2);

    const rejected = review(rebound);
    rejected.criteria = rejected.criteria.map((c) =>
        c.id === "securityPrivacyAndPerformance" ? { ...c, pass: false } : c,
    );
    rejected.findings.push({
        id: "F-CONT-1",
        rubricId: "securityPrivacyAndPerformance",
        severity: "blocker",
        location: "securityPrivacyPerformance",
        evidence: "Missing PII guard on updated path.",
        requiredChange: "Document PII scrubbing.",
    });
    const failCtx = context([rebound, rejected, rebound, rejected, rebound, rejected], updatedStage0);
    failCtx.args.continuation = { evidence: updatedEvidence, design: oldDraft };
    const failedPacket = await runDesignFeature(failCtx);
    assert.equal(failedPacket.recommendation, "NO-GO");
    assert.equal(failedPacket.continuationMode, "from-draft");
    assert.equal(failedPacket.pairedDocuments, null);
    assert.equal(failedPacket.fixIterations, 2);
});

test("fail-closed continuation rejects mismatched sourceManifest pins, design without evidence, unverified approvals, and malformed snapshots", async () => {
    const mismatchedPinEvidence = evidence();
    mismatchedPinEvidence.references[0].revision = newerSourceRevision;
    mismatchedPinEvidence.sourceVerification[0].revision = newerSourceRevision;

    const unverifiedApprovalEvidence = evidence();
    unverifiedApprovalEvidence.approvalVerification.verified = false;

    const unverifiedSourceEvidence = evidence();
    unverifiedSourceEvidence.sourceVerification[0].verified = false;

    const mismatchedDigestDraft = design();
    mismatchedDigestDraft.evidenceDigest = "f".repeat(64);

    const legacyDraft = { ...design(), openDecisions: ["legacy"] };
    const brokenSectionsDraft = { ...design(), sections: { problemAndScope: "only one section" } };

    for (const badContinuation of [
        {},
        { evidence: evidence(), extra: true },
        { design: design() },
        { evidence: "not-an-object" },
        { evidence: mismatchedPinEvidence },
        { evidence: unverifiedApprovalEvidence },
        { evidence: unverifiedSourceEvidence },
        { evidence: evidence(), design: mismatchedDigestDraft },
        { evidence: evidence(), design: legacyDraft },
        { evidence: evidence(), design: brokenSectionsDraft },
        { evidence: evidence(), design: { ...design(), evidenceDigest: "not-a-sha256" } },
    ]) {
        const ctx = context([]);
        ctx.args.continuation = badContinuation;
        assert.ok(validateStage0(ctx.args).length > 0, `Expected failure for ${JSON.stringify(badContinuation)}`);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "needs clarification");
        assert.equal(packet.pairedDocuments, null);
        assert.equal(ctx.calls.length, 0);
    }
});

test("flat humanDecisions validation rejects nested recursive blobs, duplicates, self-supersession, unknown IDs, and cycles, while normalizeHumanDecisions stays linear", async () => {
    assert.equal(workflowDefinition.meta.argsSchema.properties.humanDecisions.items.additionalProperties, false);
    assert.equal(humanDecisionSchema.additionalProperties, false);
    assert.equal(continuationSchema.additionalProperties, false);

    for (const invalid of [
        "not-an-array",
        [{ id: "HD-1", topic: "T", decision: "D", sourceReference: "ref", inheritedDecisions: [] }],
        [{ id: "HD-1", topic: "T", decision: "D", sourceReference: "ref", previousAmendment: {} }],
        [{ id: "HD-1", topic: "T", decision: "D", sourceReference: "ref", previousRequirements: [] }],
        [{ id: "HD-1", topic: "T", decision: "D", sourceReference: "ref", unexpectedField: "x" }],
        [{ id: "", topic: "T", decision: "D", sourceReference: "ref" }],
        [{ id: "HD-1", topic: "T", decision: "D", sourceReference: "ref", recordedAt: "  " }],
        [
            { id: "HD-1", topic: "T1", decision: "D1", sourceReference: "ref/1" },
            { id: "HD-1", topic: "T2", decision: "D2", sourceReference: "ref/2" },
        ],
        [{ id: "HD-1", topic: "T", decision: "D", sourceReference: "ref", supersedesIds: ["HD-1"] }],
        [{ id: "HD-1", topic: "T", decision: "D", sourceReference: "ref", supersedesIds: ["HD-UNKNOWN"] }],
        [
            { id: "HD-1", topic: "T1", decision: "D1", sourceReference: "ref/1", supersedesIds: ["HD-2"] },
            { id: "HD-2", topic: "T2", decision: "D2", sourceReference: "ref/2", supersedesIds: ["HD-1"] },
        ],
    ]) {
        assert.ok(validateHumanDecisions(invalid).length > 0, `Expected invalid humanDecisions: ${JSON.stringify(invalid)}`);
        const ctx = context([]);
        ctx.args.humanDecisions = invalid;
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "needs clarification");
        assert.equal(ctx.calls.length, 0);
    }

    const amendments = [];
    for (let i = 1; i <= 20; i++) {
        amendments.push({
            id: `HD-${i}`,
            topic: "Timeout budget",
            decision: `Revision ${i}: client timeout is ${30 + i} seconds.`,
            sourceReference: `human-review/comment-${i}`,
            ...(i > 1 ? { supersedesIds: [`HD-${i - 1}`] } : {}),
        });
    }
    amendments.push({
        id: "HD-KEEP",
        topic: "Flight ownership",
        decision: "Keep single Broker controlling-boundary flag.",
        sourceReference: "human-review/comment-keep",
    });
    assert.deepEqual(validateHumanDecisions(amendments), []);
    const normalized = normalizeHumanDecisions(amendments);
    assert.deepEqual(normalized.active.map((entry) => entry.id), ["HD-20", "HD-KEEP"]);
    assert.equal(normalized.supersededIds.length, 19);
    assert.equal(normalized.supersededIds[0], "HD-1");
    assert.equal(normalized.supersededIds[18], "HD-19");
    const serializedBytes = Buffer.byteLength(JSON.stringify(normalized), "utf8");
    assert.ok(serializedBytes < 1024, `Expected compact linear normalized ledger, got ${serializedBytes} bytes`);
});

test("buildReviewerPayload avoids duplicating detailed-design.md, strips giant supplemental evidence blobs, and compacts priorReviews", () => {
    const richEvidence = {
        ...evidence(),
        summaryMarkdown: "X".repeat(60000),
        rawResearchDump: { giant: "Y".repeat(40000) },
    };
    const bigDesign = runtimeDesign();
    bigDesign.evidenceDigest = digest(richEvidence);
    for (const key of sections) {
        bigDesign.sections[key] = `Detailed architecture narrative for ${key}: ${"A".repeat(4000)}`;
    }
    const renderedPair = renderPairedDocuments(bigDesign, richEvidence);
    const priorRoundReview = review(bigDesign);
    priorRoundReview.criteria = priorRoundReview.criteria.map((c) =>
        c.id === "securityPrivacyAndPerformance" ? { ...c, pass: false } : c,
    );
    priorRoundReview.findings.push({
        id: "F-1",
        rubricId: "securityPrivacyAndPerformance",
        severity: "major",
        location: "securityPrivacyPerformance",
        evidence: "Missing PII guard.",
        requiredChange: "Add Logger PII scrubbing note.",
    });
    const delta = buildDesignReviewDelta(bigDesign, bigDesign, [{ round: 0, review: priorRoundReview }]);
    const payload = buildReviewerPayload({
        stage0: stage0(),
        sourceManifest: sourceManifest(),
        humanDecisions: [
            { id: "HD-1", topic: "A", decision: "Old", sourceReference: "r/1" },
            { id: "HD-2", topic: "A", decision: "New", sourceReference: "r/2", supersedesIds: ["HD-1"] },
        ],
        evidence: richEvidence,
        design: bigDesign,
        renderedPair,
        priorReviews: [{ round: 0, review: priorRoundReview }],
        reviewDelta: delta,
        context: "Compact test context.",
    });

    assert.equal(payload.renderedDetailedDesign, undefined);
    assert.equal(payload.renderedBrief, renderedPair.documents[0].markdown);
    assert.deepEqual(payload.appendixDescriptors, renderedPair.appendices.map(({ path, sha256 }) => ({ path, sha256 })));
    assert.equal(payload.detailedDesignSummary.path, "detailed-design.md");
    assert.equal(payload.detailedDesignSummary.sha256, renderedPair.documents[1].sha256);
    assert.equal(payload.detailedDesignSummary.byteLength, Buffer.byteLength(renderedPair.documents[1].markdown, "utf8"));
    assert.equal(payload.detailedDesignSummary.sectionAnchors.length, sections.length);
    assert.equal(payload.detailedDesignSummary.tableAnchors.length, 9);
    assert.equal(payload.detailedDesignSummary.hostVerified, true);

    assert.equal(payload.evidence.summaryMarkdown, undefined);
    assert.equal(payload.evidence.rawResearchDump, undefined);
    assert.equal(payload.evidenceDigest, digest(richEvidence));
    assert.equal(payload.designDigest, digest(bigDesign));
    assert.equal(payload.pairRevision, renderedPair.revision);
    assert.deepEqual(payload.humanDecisions.active.map((d) => d.id), ["HD-2"]);
    assert.deepEqual(payload.humanDecisions.supersededIds, ["HD-1"]);

    assert.equal(payload.priorReviews.length, 1);
    assert.equal(payload.priorReviews[0].review, undefined);
    assert.deepEqual(payload.priorReviews[0].failedCriteria, ["securityPrivacyAndPerformance"]);
    assert.deepEqual(payload.priorReviews[0].failedChangeChecks, []);
    assert.equal(payload.priorReviews[0].findings.length, 1);
    assert.equal(payload.priorReviews[0].briefChecks.consistent, true);
    assert.deepEqual(payload.reviewDelta, delta);

    const payloadBytes = Buffer.byteLength(JSON.stringify(payload), "utf8");
    const duplicatedBytes = payloadBytes + Buffer.byteLength(renderedPair.documents[1].markdown, "utf8") + 100000;
    assert.ok(payloadBytes < duplicatedBytes - 120000, `Expected bounded reviewer payload, got ${payloadBytes} bytes`);
});

test("source-only and baseline-trace-clarity boundaries are enforced across worker prompts and docs", async () => {
    const failedReview = review(runtimeDesign());
    failedReview.criteria = failedReview.criteria.map((c) =>
        c.id === "architectureAndCompatibility" ? { ...c, pass: false } : c,
    );
    failedReview.findings.push({
        id: "F-ARCH-1",
        rubricId: "architectureAndCompatibility",
        severity: "minor",
        location: "architectureAndDataControlFlow",
        evidence: "Clarify DI graph release check.",
        requiredChange: "Record DI graph verification as a release-check item.",
    });
    const ctx = context([evidence(), runtimeDesign(), failedReview, runtimeDesign(), review(runtimeDesign())]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, readyRecommendation);

    const researchPrompt = ctx.calls[0].prompt.split("\nDATA:\n")[0];
    const authorPrompt = ctx.calls[1].prompt.split("\nDATA:\n")[0];
    const reviewerPrompt = ctx.calls[2].prompt.split("\nDATA:\n")[0];
    const revisionPrompt = ctx.calls[3].prompt.split("\nDATA:\n")[0];

    for (const prompt of [researchPrompt, authorPrompt, reviewerPrompt, revisionPrompt]) {
        assert.match(prompt, /never run Gradle builds/i);
        assert.match(prompt, /merged\s+manifests|Dagger\/DI graphs/i);
        assert.match(prompt, /scope assumption or.*release-check/i);
    }
    assert.match(researchPrompt, /Scenario traces describe CURRENT baseline code behavior ONLY/);
    assert.match(researchPrompt, /Never place future design decisions.*inside scenarioTraces as unresolved baseline blockers/s);
    assert.match(reviewerPrompt, /Stage 1 evidence\.scenarioTraces describe current baseline code behavior/);
    assert.match(reviewerPrompt, /detailedDesignSummary/);
    assert.match(reviewerPrompt, /appendixDescriptors/);

    const readmeText = readFileSync(new URL("./README.md", import.meta.url), "utf8");
    assert.match(readmeText, /continuation\.evidence/);
    assert.match(readmeText, /continuation\.design/);
    assert.match(readmeText, /humanDecisions/);
    assert.match(readmeText, /supersedesIds/);
    assert.match(readmeText, /buildReviewerPayload/);
    assert.match(readmeText, /design-feature-v16/);
    assert.match(readmeText, /never run Gradle builds/i);

    const standardsText = readFileSync(
        new URL("../../skills/design-author/references/design-standards.md", import.meta.url),
        "utf8",
    );
    assert.match(standardsText, /Strictly source-only during design/i);
    assert.match(standardsText, /never run Gradle builds/i);
    assert.match(standardsText, /Baseline trace clarity/i);
});
