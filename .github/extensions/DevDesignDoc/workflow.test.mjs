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
    assumptions: [], openDecisions: [], blockingUnknowns: [],
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
    return d;
};
const review = (d = design()) => ({
    designDigest: digest(d), evidenceDigest: d.evidenceDigest, references: [],
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
    assert.equal((await runDesignFeature(ctx)).recommendation, "GO to Planning");
    assert.match(ctx.calls[2].prompt.split("\nDATA:\n")[0], /challenge.*non-runtime/i);
});

test("default-off contracts reach the human packet and author/reviewer schemas without activation", async () => {
    const d = runtimeDesign();
    const ctx = context([evidence(), d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.humanApproval, "pending");
    assert.deepEqual(packet.design.featureFlags, d.featureFlags);
    const schema = ctx.calls[1].options.schema;
    assert.ok(schema.required.includes("featureFlags"));
    assert.ok(schema.properties.sections.required.includes("featureFlagsAndIsolation"));
    for (const key of ["defaultValue", "configurationFailureValue", "productionDefaultValue"]) {
        assert.equal(schema.properties.featureFlags.items.properties[key].type, "boolean");
        assert.equal(schema.properties.featureFlags.items.properties[key].const, false);
    }
    const author = ctx.calls[1].prompt.split("\nDATA:\n")[0];
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
    assert.deepEqual(validateDesign(d, stage0(), e), []);
    const second = structuredClone(d.featureFlags[0]);
    Object.assign(second, { id: "F2", repository: "Broker", evidenceIds: ["E2"] });
    Object.assign(second.entryPoints[0], { repository: "Broker", evidenceIds: ["E2"] });
    d.featureFlags.push(second);
    d.changes[1].flagIds = ["F2"];
    const ctx = context([e, d, review(d)]);
    ctx.args.sourceManifest.push({ ...sourceManifest()[0], repository: "Broker" });
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
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
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.fixIterations, 1);
    assert.equal(ctx.calls[3].options.label, "Design revision");
    assert.match(ctx.calls[3].prompt.split("\nDATA:\n")[0], /runtimeBehaviorChange/);
    const data = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
    assert.deepEqual(data.design.featureFlags, corrected.featureFlags);
    assert.equal(data.designDigest, digest(corrected));
});

test("missing or blank pre-mortems cannot produce a review-ready packet", async () => {
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

test("runtime and non-runtime designs return the generated pre-mortem in the human gate packet", async () => {
    for (const flagged of [true, false]) {
        const d = flagged ? runtimeDesign() : design();
        d.sections.preMortemAndOperationalReadiness = `# Pre-mortem and operational readiness
R1 / T1: a supported caller receives an incompatible response; all eligible callers are affected.
Prevent with compatibility tests; detect incompatible outcomes; response owner: feature owner.
Monitoring: eligible/executed/baseline cohorts; failed attempts / all executed attempts per hour.
Contract: proposed attemptId, outcome and featureVersion; mappings remain unverified.
Deduplicate attempts; sampling may hide failures; missing instrumentation is not healthy behavior.
\`\`\`kql
// UNEXECUTED PRE-FLIGHT DRAFT: table/property mapping requires validation.
ProposedAttempts | summarize total=count(), failed=countif(outcome == "incompatible")
\`\`\`
Monitoring owner: telemetry owner; hourly cadence during the agreed observation window.
Feature owner must confirm thresholds/stop criteria and escalation before rollout.
Rollback owner: release owner; ${flagged ? "disable the feature flag" : "revert to the prior release"}.
Active/in-flight attempts finish under their admitted version; verify persisted state recovery.
Validation owner: telemetry owner; validate contracts, cohorts and denominators using an
approved enabled E2E test cohort and attach evidence before relying on monitoring for rollout.
Release owner resolves release-blocking gaps and approves rollback readiness.
Pre-mortem child PBI: Pending feature planning; reuse a suitable existing child if verified.
Document authoring/review is not query validation, activation permission or rollout approval.`;
        const ctx = context([evidence(), d, review(d)]);
        const packet = await runDesignFeature(ctx);
        assert.equal(packet.recommendation, "GO to Planning");
        assert.equal(packet.humanApproval, "pending");
        assert.equal(packet.design.sections.preMortemAndOperationalReadiness, d.sections.preMortemAndOperationalReadiness);
        assert.deepEqual(packet.stage0, stage0());
        assert.equal(ctx.calls.length, 3);
        assert.ok(ctx.calls[1].options.schema.properties.sections.required.includes("preMortemAndOperationalReadiness"));
        const task = ctx.calls[1].prompt.split("\nDATA:\n")[0];
        assert.match(task, /every feature, including single-repo and non-runtime/);
        assert.match(task, /actual KQL draft text/);
        assert.match(task, /active\/in-flight attempts/);
        assert.match(task, /Pending feature planning/);
        assert.match(ctx.calls[2].prompt.split("\nDATA:\n")[0], /preMortemAndReleaseGates/);
    }
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

test("pre-mortem review findings require repair and a fresh independent review", async () => {
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
        "Telemetry owner validates the draft against an approved enabled E2E test cohort before monitoring is relied on.";
    const ctx = context([evidence(), d, failed, corrected, review(corrected)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.fixIterations, 1);
    assert.equal(packet.reviews.length, 2);
    assert.equal(ctx.calls.length, 5);
    const revision = ctx.calls[3];
    assert.equal(revision.options.label, "Design revision");
    assert.ok(revision.options.schema.properties.sections.required.includes("preMortemAndOperationalReadiness"));
    assert.match(revision.prompt.split("\nDATA:\n")[0], /actual KQL draft text/);
    assert.equal(ctx.calls[4].options.label, "Independent design reviewer");
    const data = JSON.parse(ctx.calls[4].prompt.split("\nDATA:\n")[1]);
    assert.equal(data.design.sections.preMortemAndOperationalReadiness, corrected.sections.preMortemAndOperationalReadiness);
    assert.equal(data.designDigest, digest(corrected));
});

test("owned pre-flight mapping gaps and absent production data do not require activation to author", async () => {
    const e = evidence();
    e.unknowns = ["Proposed telemetry mappings require enabled-E2E validation by the telemetry owner."];
    const d = design();
    d.evidenceDigest = digest(e);
    d.sections.preMortemAndOperationalReadiness =
        "UNEXECUTED PRE-FLIGHT DRAFT; table/property mappings remain unresolved. " +
        "No production data while disabled; telemetry owner validates with an approved enabled E2E cohort. " +
        "Release owner separately approves release prerequisites; document review is not rollout approval.";
    const ctx = context([e, d, review(d)]);
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.humanApproval, "pending");
    assert.deepEqual(packet.evidence.unknowns, e.unknowns);
    assert.match(ctx.calls[0].prompt.split("\nDATA:\n")[0], /Missing production data while disabled is expected/);
    assert.match(ctx.calls[1].prompt.split("\nDATA:\n")[0], /Broad production enablement is not required/);
    assert.match(ctx.calls[2].prompt.split("\nDATA:\n")[0], /not rollout approval/);
});

test("default-off generation does not reuse v5, accessibility-only v6 or pre-mortem-only v7 outputs", async () => {
    const legacyDesign = design();
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
    for (const version of ["v5", "v6", "v7"]) {
        const prefix = `design-feature-${version}-${digest(ctx.args)}`;
        journal.set(`${prefix}-evidence`, evidence());
        journal.set(`${prefix}-${digest(evidence())}-design`, legacyDesign);
        journal.set(`${prefix}-${digest(evidence())}-independent-review-0-${digest(legacyDesign)}-${digest([])}`, legacyReview);
    }
    ctx.step = async (key, producer) => {
        ctx.keys.push(key);
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    const packet = await runDesignFeature(ctx);
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(ctx.calls.length, 3);
    assert.ok(ctx.keys.every((key) => key.startsWith("design-feature-v8-")));
    assert.ok(packet.design.sections.preMortemAndOperationalReadiness);
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
        referenceDiscoveries: discoveries,
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
    assert.equal(packet.recommendation, "GO to Planning");
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
    assert.equal(packet.recommendation, "GO to Planning");
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
    assert.equal(packet.recommendation, "GO to Planning");
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
    assert.equal((await runDesignFeature(ctx)).recommendation, "GO to Planning");
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
    assert.equal(packet.recommendation, "GO to Planning");
    assert.equal(packet.design.changes[0].implementation, "regression-only");
});

test("checkpoint identity includes pinned sources, context, evidence, and schema version", async () => {
    const initial = context([evidence(), design(), review()]);
    await runDesignFeature(initial);
    assert.match(initial.keys[0], /^design-feature-v8-/);
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
