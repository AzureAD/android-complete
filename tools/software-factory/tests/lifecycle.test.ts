// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { Application } from "../core/application.ts";
import { FactoryError } from "../core/contracts.ts";
import { parsePlan, type Configuration, type Execution, type Json, type Plan, type PlannedItem, type PullRequest, type RepositoryTarget, type WorkItem } from "../core/lifecycle-contracts.ts";
import type { Provider } from "../providers/provider.ts";
import { designRun, designWorkflow, intakeWorkflow, lifecycleRun, lifecycleWorkflow, monitorRun, monitorWorkflow, type FactoryContext } from "../workflows/lifecycle.ts";
import { rubric, sections, workflowDefinition as standaloneDesign } from "../../../.github/extensions/DevDesignDoc/workflow.mjs";
import { actor, fixture, requirements } from "./helpers.ts";

const config = (): Configuration => ({
    repositories: { common: { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev", account: "test-account", localPath: "." } },
    boards: { organization: "example", project: "Engineering", workItemType: "Product Backlog Item", areaPath: "Engineering\\Client", iterationPath: "Engineering\\Current", assignee: "", parentId: null },
});
const plan = (): Plan => ({
    summary: "Implement all approved compatibility requirements.",
    items: ["A", "B", "C"].map((id) => ({
        id, title: `Implement ${id}`, repository: "common", description: `Self-contained implementation specification for ${id}.`,
        requirementIds: ["R1"], acceptanceCriteria: [`${id} preserves the response contract.`],
        dependsOn: id === "C" ? ["A", "B"] : [], files: [`${id}.kt`], tests: [`${id} compatibility test`],
    })),
    blockingUnknowns: [],
});

test("Factory declares no AI-credit cap while preserving independent design bounds", () => {
    for (const workflow of [intakeWorkflow, designWorkflow, lifecycleWorkflow, monitorWorkflow]) {
        assert.equal(workflow.meta.limits?.maxAiCredits, undefined, workflow.meta.name);
    }
    assert.deepEqual(designWorkflow.meta.limits, {
        maxConcurrentSubagents: standaloneDesign.meta.limits.maxConcurrentSubagents,
        maxTotalSubagents: standaloneDesign.meta.limits.maxTotalSubagents,
        timeoutSeconds: standaloneDesign.meta.limits.timeoutSeconds,
    });
    assert.equal(standaloneDesign.meta.limits.maxAiCredits, 20, "The standalone workflow is not changed by Factory's override.");
});

class FakeProvider implements Provider {
    items = new Map<string, WorkItem>();
    dispatches = new Map<string, Json>();
    prs = new Map<string, PullRequest>();
    creations = 0;
    sends = 0;
    comments = 0;
    failAfterCreate = false;
    afterCreate?: () => void;
    async findWorkItem(_config: Configuration, marker: string) { return this.items.get(marker) || null; }
    async createWorkItem(_config: Configuration, item: PlannedItem, marker: string) {
        const result: WorkItem = { key: item.id, adoId: 1000 + ++this.creations, title: item.title, repository: item.repository, url: `https://dev.azure.com/example/Engineering/_workitems/edit/${1000 + this.creations}`, status: "Committed" };
        this.items.set(marker, result);
        this.afterCreate?.();
        if (this.failAfterCreate) { this.failAfterCreate = false; throw new Error("Connection lost after remote success"); }
        return result;
    }
    async linkWorkItem(_config: Configuration, item: WorkItem) { return { adoId: item.adoId, linked: true }; }
    async inspectWorkItem(_config: Configuration, item: WorkItem) { return { ...item, status: this.prs.get(item.key)?.status === "merged" ? "Resolved" : "Active" }; }
    async dispatch(_config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string) {
        this.sends++;
        const result = { submitted: true, marker, itemId: item.id };
        this.dispatches.set(marker, result);
        this.prs.set(item.id, {
            key: item.id, repository: "example/common", number: workItem.adoId, url: `https://github.com/example/common/pull/${workItem.adoId}`,
            status: "open", head: `sha-${item.id}`, title: item.title, checks: "passing", baseBranch: "dev", workItemIds: [workItem.adoId],
        });
        return result;
    }
    async findDispatch(_config: Configuration, _item: PlannedItem, _workItem: WorkItem, marker: string) { return this.dispatches.get(marker) || null; }
    async inspectPr(_repo: unknown, number: number) {
        const pr = [...this.prs.values()].find((item) => item.number === number);
        if (!pr) throw new Error("PR not found");
        return pr;
    }
    async findPr(_config: Configuration, item: PlannedItem) { return this.prs.get(item.id) || null; }
    async diff() { return "Verified implementation and tests for the exact acceptance criterion."; }
    async preflight() { return { verified: true }; }
    async findFeedback(_repo: RepositoryTarget, _number: number, _marker: string): Promise<Json | null> { return null; }
    async postFeedback() { this.comments++; return { posted: true, commentId: this.comments }; }
    async markDispatched(_config: Configuration, item: WorkItem) { return { adoId: item.adoId, state: "Active", tagged: true }; }
    async review() { return { comments: [] }; }
}

class Checkpoint extends Error {}

function harness(app: Application, execution: Execution) {
    const journal = new Map<string, Json>();
    const labels: string[] = [];
    const checkpoints: string[] = [];
    const stage0 = app.lifecycle.stage0(app.registry.list()[0]!.id);
    const context: FactoryContext = {
        args: { featureId: app.registry.list()[0]!.id, executionId: execution.id }, runId: randomUUID(),
        session: { sessionId: execution.sessionId },
        phase: () => undefined, log: () => undefined,
        pause: async (key) => { checkpoints.push(key); throw new Checkpoint(key); },
        step: async (key, producer) => {
            if (!journal.has(key)) journal.set(key, await producer());
            return journal.get(key)!;
        },
        agent: async (prompt, options) => {
            const label = options?.label || "";
            labels.push(label);
            if (label.endsWith("Repository evidence") || label.endsWith("Design author") || label.endsWith("Independent design reviewer")) {
                for (const task of stage0.designTasks || []) assert.ok(prompt.includes(task), "approved design obligations must reach every design worker and reviewer");
            }
            if (label.endsWith("Repository evidence")) return {
                approvalVerification: { verified: true, revision: stage0.revision, reference: stage0.approval.reference, rationale: "Fixture read the actual approval record." },
                references: [{ id: "E1", repository: "common", revision: "source-sha", path: "A.kt", locator: "A" }],
                facts: [{ statement: "The compatibility contract exists.", referenceIds: ["E1"] }],
                conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
            };
            if (label.endsWith("Design author") || label.endsWith("Design revision")) return {
                stage0Revision: stage0.revision,
                sections: Object.fromEntries(sections.map((key: string) => [key, `Evidence-backed content for ${key}.`])),
                traceability: [{ requirementId: "R1", sectionIds: ["requirements"], evidenceIds: ["E1"], verification: "Run the compatibility test." }],
                assumptions: [], openDecisions: [], blockingUnknowns: [],
            };
            if (label.endsWith("Independent design reviewer")) return { criteria: rubric.map((id: string) => ({ id, pass: true })), findings: [], blockingUnknowns: [] };
            if (label.includes(":planner:")) return plan();
            if (label.includes(":plan-review:")) return { pass: true, findings: [] };
            if (label.includes(":validate:")) {
                const id = label.split(":")[2];
                const item = plan().items.find((entry) => entry.id === id)!;
                return { criteria: item.acceptanceCriteria.map((criterion) => ({ criterion, status: "addressed", evidence: `${item.files[0]} and compatibility tests.` })) };
            }
            throw new Error(`Unexpected model task: ${label}`);
        },
    };
    return { context, labels, checkpoints };
}

async function finished(app: Application, featureId: string, execution: Execution, action: () => Promise<Json>) {
    const result = await action();
    app.lifecycle.store.updateExecution(featureId, execution.id, { status: "completed", outcome: result });
    return result;
}

function approve(app: Application, featureId: string, stage: "design" | "plan" | "backlog" | "dispatch" | "complete") {
    const preview = app.lifecycle.gate(featureId, stage);
    return app.lifecycle.decide(featureId, stage, preview.scopeHash, true, { ...actor, reference: randomUUID() });
}

async function feedbackFeature(app: Application) {
    app.lifecycle.configure(config(), actor);
    const feature = app.execute({ kind: "create_feature", requestId: "feedback", input: requirements() }, actor);
    app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
    const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
    approve(app, feature.id, "design");
    const artifact = app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, plan(), design.id);
    approve(app, feature.id, "plan"); approve(app, feature.id, "backlog");
    const execution = app.lifecycle.begin(feature.id, "lifecycle", actor);
    await app.lifecycle.backlog(feature.id, execution.id);
    approve(app, feature.id, "dispatch");
    await app.lifecycle.dispatch(feature.id, execution.id);
    app.lifecycle.store.artifact(feature.id, "monitor", feature.current.id, await app.lifecycle.observe(feature.id), artifact.id);
    return feature;
}

test("the actual workflow functions complete design -> plan -> backlog -> dependency waves -> validation -> completion", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        app.lifecycle.configure(config(), actor);
        const feature = app.execute({ kind: "create_feature", requestId: "lifecycle",
            input: { ...requirements(), designTasks: ["Trace IPC compatibility and specify request-context propagation."] } }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.begin(feature.id, "design", actor);
        const d = harness(app, design);
        await assert.rejects(() => designRun(d.context, { provider }), Checkpoint);
        assert.equal(d.labels.length, 3);
        await assert.rejects(() => designRun(d.context, { provider }), Checkpoint);
        assert.equal(d.labels.length, 3, "journaled guarded design must not launch more agents");
        assert.notEqual(d.checkpoints[0], d.checkpoints[1], "resuming without consent must pause at a new checkpoint");
        approve(app, feature.id, "design");
        await finished(app, feature.id, design, () => designRun(d.context, { provider }));

        const delivery = app.lifecycle.begin(feature.id, "lifecycle", actor);
        const l = harness(app, delivery);
        await assert.rejects(() => lifecycleRun(l.context, { provider }), Checkpoint);
        assert.equal(provider.creations, 0);
        approve(app, feature.id, "plan");
        await assert.rejects(() => lifecycleRun(l.context, { provider }), Checkpoint);
        assert.equal(provider.creations, 0, "plan approval is not backlog authorization");
        approve(app, feature.id, "backlog");

        await assert.rejects(() => lifecycleRun(l.context, { provider }), Checkpoint);
        assert.equal(provider.creations, 3);
        approve(app, feature.id, "dispatch");
        await finished(app, feature.id, delivery, () => lifecycleRun(l.context, { provider }));
        assert.equal(provider.sends, 2, "one approval must cover both independent roots, not only the first");
        assert.equal(app.lifecycle.gate(feature.id, "dispatch").ready, false);

        const review = app.lifecycle.begin(feature.id, "monitor", actor);
        const r = harness(app, review);
        await finished(app, feature.id, review, () => monitorRun(r.context, { provider }));
        await app.lifecycle.attachPr(feature.id, "A", provider.prs.get("A")!.number, actor);
        await assert.rejects(() => app.lifecycle.attachPr(feature.id, "A", provider.prs.get("B")!.number, actor), /exact work item/);
        await app.lifecycle.postFeedback(feature.id, "A", "feedback-1", "@copilot Please address the reviewed compatibility gap.", actor);
        await app.lifecycle.postFeedback(feature.id, "A", "feedback-1", "@copilot Please address the reviewed compatibility gap.", actor);
        assert.equal(provider.comments, 1, "posting retries must return the original receipt");
        assert.throws(() => app.lifecycle.feedbackScope(feature.id, "A", "feedback-1", "Different body"), /different body/);

        for (const id of ["A", "B"]) provider.prs.get(id)!.status = "merged";
        const monitor = app.lifecycle.begin(feature.id, "monitor", actor);
        const m = harness(app, monitor);
        await finished(app, feature.id, monitor, () => monitorRun(m.context, { provider }));
        assert.equal(app.lifecycle.gate(feature.id, "dispatch").ready, true);
        approve(app, feature.id, "dispatch");
        const wave = app.lifecycle.begin(feature.id, "lifecycle", actor);
        const w = harness(app, wave);
        await finished(app, feature.id, wave, () => lifecycleRun(w.context, { provider }));
        assert.equal(provider.creations, 3);
        assert.equal(provider.sends, 3);
        assert.equal(w.labels.length, 0, "later waves reuse the approved plan instead of regenerating it");
        provider.prs.get("C")!.status = "merged";
        const final = app.lifecycle.begin(feature.id, "monitor", actor);
        const finalContext = harness(app, final);
        const agent = finalContext.context.agent;
        finalContext.context.agent = async (prompt, options) => {
            if (options?.label?.includes(":validate:C:")) return {
                criteria: [{ criterion: plan().items[2]!.acceptanceCriteria[0]!, status: "unknown", evidence: "Requires a separately reviewed E2E receipt." }],
            };
            return agent(prompt, options);
        };
        await finished(app, feature.id, final, () => monitorRun(finalContext.context, { provider }));
        assert.equal(app.lifecycle.gate(feature.id, "complete").ready, false);
        app.lifecycle.waive(feature.id, app.lifecycle.store.latest(feature.id, "monitor")!.id, "C",
            plan().items[2]!.acceptanceCriteria[0]!, "Operator reviewed the required E2E receipt.", actor);
        const preview = app.lifecycle.gate(feature.id, "complete");
        const completion = { kind: "approve_stage", featureId: feature.id, stage: "complete", expectedScopeHash: preview.scopeHash, approved: true };
        const consent = { granted: true as const, revisionId: app.prepare(completion).consentKey!, actor };
        provider.prs.get("C")!.checks = "failing";
        await assert.rejects(() => app.dispatch(completion, actor, consent), /delivery state changed/);
        provider.prs.get("C")!.checks = "passing";
        await app.dispatch(completion, actor, consent);
        assert.equal(app.lifecycle.store.view(feature.id).lifecycleStatus, "completed");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("plan semantics reject cycles, missing coverage and unknown repositories", () => {
    const cyclic = plan();
    cyclic.items[0]!.dependsOn = ["C"];
    assert.throws(() => parsePlan(cyclic, config(), ["R1"]), /cycle/);
    assert.throws(() => parsePlan(plan(), config(), ["R1", "R2"]), /Every approved requirement/);
    const unknown = plan();
    unknown.items[0]!.repository = "toString";
    assert.throws(() => parsePlan(unknown, config(), ["R1"]), /Unknown repository/);
});

test("external success followed by connection loss is reconciled, never duplicated", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    try {
        app.lifecycle.configure(config(), actor);
        const feature = app.execute({ kind: "create_feature", requestId: "reconcile", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, plan(), design.id);
        approve(app, feature.id, "plan"); approve(app, feature.id, "backlog");
        const run = app.lifecycle.begin(feature.id, "lifecycle", actor);
        provider.failAfterCreate = true;
        await assert.rejects(() => app.lifecycle.backlog(feature.id, run.id), /Connection lost/);
        assert.equal(provider.creations, 1);
        assert.equal(app.lifecycle.store.state(feature.id).operations[0]?.status, "reconciliation_required");
        await app.lifecycle.backlog(feature.id, run.id);
        assert.equal(provider.creations, 3);
        const evidence = app.lifecycle.stage0(feature.id).approval.reference;
        writeFileSync(evidence, "{}");
        assert.throws(() => app.lifecycle.stage0(feature.id), /modified/);
        assert.equal(readFileSync(evidence, "utf8"), "{}");
    } finally { app.close(); f.cleanup(); }
});

test("revoking a parent approval stops future sends and invalidates downstream approval lineage", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    try {
        app.lifecycle.configure(config(), actor);
        const feature = app.execute({ kind: "create_feature", requestId: "revocation", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, plan(), design.id);
        approve(app, feature.id, "plan"); approve(app, feature.id, "backlog");
        const execution = app.lifecycle.begin(feature.id, "lifecycle", actor);
        provider.afterCreate = () => {
            provider.afterCreate = undefined;
            const preview = app.lifecycle.gate(feature.id, "design");
            app.lifecycle.decide(feature.id, "design", preview.scopeHash, false, { ...actor, reference: randomUUID() });
        };
        await assert.rejects(() => app.lifecycle.backlog(feature.id, execution.id), /approval was revoked/);
        assert.equal(provider.creations, 1, "an already-admitted send is retained, but no later item is created");
        assert.equal(app.lifecycle.gate(feature.id, "plan").approved, false);
        approve(app, feature.id, "design");
        assert.equal(app.lifecycle.gate(feature.id, "plan").approved, false, "renewing a parent does not silently renew its descendants");
    } finally { app.close(); f.cleanup(); }
});

test("PR feedback rechecks configuration, requirements, cancellation, binding and live head after asynchronous reads", async () => {
    const cases = { configuration: "monitor_required", requirements: "plan_required", cancellation: "feature_cancelled", binding: "monitor_required", head: "stale_pr" };
    for (const [change, code] of Object.entries(cases)) {
        const f = fixture();
        const provider = new FakeProvider();
        const app = new Application({ ...f.options, provider });
        try {
            const feature = await feedbackFeature(app);
            const command = { kind: "post_pr_feedback", featureId: feature.id, itemId: "A", requestId: "racing-feedback", body: "@copilot Address this reviewed gap." };
            const consent = { granted: true as const, revisionId: app.prepare(command).consentKey!, actor };
            provider.findFeedback = async () => {
                if (change === "configuration") {
                    const updated = config();
                    updated.repositories.common!.account = "new-account";
                    app.lifecycle.configure(updated, actor);
                } else if (change === "requirements") {
                    app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
                        input: { ...requirements(), title: "Revised requirements" } }, actor);
                } else if (change === "cancellation") {
                    app.lifecycle.store.change(feature.id, (state) => { state.cancelled = true; });
                } else if (change === "binding") {
                    const replacement = { ...provider.prs.get("A")!, number: 9001, url: "https://github.com/example/common/pull/9001" };
                    provider.prs.set("replacement", replacement);
                    await app.lifecycle.attachPr(feature.id, "A", replacement.number, actor);
                } else provider.prs.get("A")!.head = "new-head";
                return null;
            };
            await assert.rejects(() => app.dispatch(command, actor, consent),
                (error: unknown) => error instanceof FactoryError && error.code === code, change);
            assert.equal(provider.comments, 0, `${change} must stop feedback before the provider send`);
            assert.equal(app.lifecycle.store.state(feature.id).operations.find((entry) => entry.kind === "feedback")?.status, "failed");
        } finally { app.close(); f.cleanup(); }
    }
});

test("uncertain feedback reconciles its original target after canonical PR and configuration changes without reposting", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const lookups: { slug: string; number: number }[] = [];
    let remoteReceipt: Json | null = null;
    try {
        const feature = await feedbackFeature(app);
        const original = provider.prs.get("A")!;
        provider.findFeedback = async (repo, number) => {
            lookups.push({ slug: repo.slug, number });
            return remoteReceipt;
        };
        provider.postFeedback = async () => {
            provider.comments++;
            remoteReceipt = { commentId: 71, url: `${original.url}#issuecomment-71` };
            throw new Error("Connection lost after posting");
        };
        const command = { kind: "post_pr_feedback", featureId: feature.id, itemId: "A", requestId: "uncertain-feedback", body: "@copilot Address this reviewed gap." };
        await assert.rejects(() => app.dispatch(command, actor, { granted: true, revisionId: app.prepare(command).consentKey!, actor }), /Connection lost/);
        const operation = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.kind === "feedback")!;
        assert.equal(operation.status, "reconciliation_required");
        const replacement = { ...original, number: 9002, url: "https://github.com/example/common/pull/9002" };
        provider.prs.set("replacement", replacement);
        await app.lifecycle.attachPr(feature.id, "A", replacement.number, actor);
        const updated = config();
        updated.repositories.common!.slug = "example/replacement";
        app.lifecycle.configure(updated, actor);
        const reconcile = { kind: "reconcile_operation", featureId: feature.id, operationKey: operation.key };
        await app.dispatch(reconcile, actor, { granted: true, revisionId: app.prepare(reconcile).consentKey!, actor });
        assert.deepEqual(lookups.at(-1), { slug: "example/common", number: original.number });
        assert.equal(provider.comments, 1, "reconciliation only reads the original target");
        assert.equal(app.lifecycle.store.state(feature.id).operations.find((entry) => entry.key === operation.key)?.status, "completed");
    } finally { app.close(); f.cleanup(); }
});
