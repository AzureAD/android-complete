// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { Application } from "../core/application.ts";
import { FactoryError, object } from "../core/contracts.ts";
import { analysisConfiguration, jsonValue, parseConfiguration, parsePlan, sharedConfiguration, type Configuration, type Execution, type Json, type LocalPublicationBinding, type ObserverEffectPlan, type ObserverMutation, type Operation, type Plan, type PlannedItem, type PullRequest, type RepositoryTarget, type WorkItem } from "../core/lifecycle-contracts.ts";
import { nextStep } from "../core/progress.ts";
import { hash } from "../core/lifecycle-store.ts";
import type { LocalImplementationInput, ObserverEffectRequest, Provider, RepairPublicationInput } from "../providers/provider.ts";
import { applyLocalFiles, buildDesignSourceManifest, designRun, designWorkflow, implementRun, implementWorkflow, intakeWorkflow, planRun, planWorkflow, monitorRun, monitorWorkflow, restoreLocalFiles, type FactoryContext, type LocalBackup } from "../workflows/lifecycle.ts";
import { repositorySourceRoot, sourcePins } from "../runtime/controller.ts";
import { digest, rubric, sections, workflowDefinition as standaloneDesign } from "../../../.github/extensions/DevDesignDoc/workflow.mjs";
import { rubric as codingRubric } from "../../../.github/extensions/CodeFeature/workflow.mjs";
import { renderObserverDescription, runPrObserver } from "../../../.github/extensions/CodeFeature/observer.mjs";
import { factoryObserver } from "../workflows/observer.ts";
import { actor, adoObserverFixture, fixture, requirements } from "./helpers.ts";

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

function initializeRepository(root: string, content = "// baseline\n") {
    execFileSync("git", ["init", "--quiet", root]);
    writeFileSync(path.join(root, "A.kt"), content);
    execFileSync("git", ["add", "A.kt"], { cwd: root });
    execFileSync("git", ["-c", "user.name=Factory Test", "-c", "user.email=factory@example.invalid",
        "commit", "--quiet", "-m", "Fixture"], { cwd: root });
    execFileSync("git", ["update-ref", "refs/remotes/origin/dev", "HEAD"], { cwd: root });
    return execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
}

test("Factory declares no AI-credit cap while preserving independent design bounds", () => {
    for (const workflow of [intakeWorkflow, designWorkflow, planWorkflow, implementWorkflow, monitorWorkflow]) {
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
    localPublications = new Map<string, LocalImplementationInput>();
    prs = new Map<string, PullRequest>();
    creations = 0;
    sends = 0;
    localPreparations = 0;
    localPublishes = 0;
    comments = 0;
    prBodies = new Map<number, string>();
    statusUpdates = 0;
    failAfterCreate = false;
    afterCreate?: () => void;
    onFeedbackPosted?: (repo: RepositoryTarget, number: number, body: string) => void;
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
    async prepareLocalPublication(config: Configuration, item: PlannedItem, _workItem: WorkItem, _marker: string, implementation: LocalImplementationInput): Promise<LocalPublicationBinding> {
        this.localPreparations++;
        const repo = config.repositories[item.repository]!;
        return {
            branch: implementation.branch, headSha: implementation.sourceBinding.tree, source: implementation.sourceBinding,
            target: { provider: repo.provider, hostname: repo.hostname, slug: repo.slug, baseBranch: repo.baseBranch,
                publisherId: "1", publisherAccount: repo.account },
        };
    }
    async publishLocal(_config: Configuration, item: PlannedItem, workItem: WorkItem, marker: string, implementation: LocalImplementationInput, binding: LocalPublicationBinding) {
        this.localPublishes++;
        this.localPublications.set(item.id, implementation);
        const result = {
            submitted: true, mode: "local", provider: "github", marker,
            branch: binding.branch, headSha: binding.headSha, prNumber: workItem.adoId,
            url: `https://github.com/example/common/pull/${workItem.adoId}`,
        };
        this.dispatches.set(marker, result);
        this.prs.set(item.id, {
            key: item.id, repository: "example/common", number: workItem.adoId, url: `https://github.com/example/common/pull/${workItem.adoId}`,
            status: "open", head: binding.headSha, title: item.title, checks: "passing", baseBranch: "dev", workItemIds: [workItem.adoId],
        });
        return result;
    }
    async findLocalPublication(_config: Configuration, _item: PlannedItem, _workItem: WorkItem, marker: string, _binding: LocalPublicationBinding) {
        return this.dispatches.get(marker) || null;
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
    async postFeedback(repo: RepositoryTarget, number: number, body: string) {
        this.comments++;
        this.onFeedbackPosted?.(repo, number, body);
        return { posted: true, commentId: this.comments };
    }
    async markDispatched(_config: Configuration, item: WorkItem) { return { adoId: item.adoId, state: "Active", tagged: true }; }
    async review(_repo?: RepositoryTarget, number?: number) {
        return { body: this.prBodies.get(number || 0) || "Human-authored PR description.", comments: [],
            reviewThreadsComplete: true, unresolvedReviewThreadCount: 0, activeReviewThreads: [] };
    }
    async prepareObserverEffect(_repo: RepositoryTarget, number: number, snapshot: Json, request: ObserverEffectRequest): Promise<ObserverEffectPlan> {
        if (request.kind !== "status") throw new Error("This fixture expects status publication only.");
        const value = object(snapshot, "Snapshot");
        const ci = object(request.ci, "CI");
        const before = this.prBodies.get(number) || "Human-authored PR description.";
        const body = renderObserverDescription({ provider: "github" }, { ...value, body: before }, request.block, {
            status: String(ci.status), headSha: ci.headSha, evidence: Array.isArray(ci.evidence) ? ci.evidence : [],
        });
        return { result: body, mutations: [{ kind: "status" as const, prNumber: number, headSha: String(value.headRefOid), before, body }] };
    }
    async sendObserverMutation(_repo: RepositoryTarget, mutation: ObserverMutation, _publisher: LocalPublicationBinding["target"], beforeSend: () => Promise<void>): Promise<Json> {
        await beforeSend();
        if (mutation.kind !== "status") throw new Error("Unexpected fixture mutation.");
        this.prBodies.set(mutation.prNumber, mutation.body);
        this.statusUpdates++;
        return { accepted: true };
    }
    async findObserverMutation(_repo: RepositoryTarget, mutation: ObserverMutation): Promise<Json | null> {
        return mutation.kind === "status" && this.prBodies.get(mutation.prNumber) === mutation.body ? { accepted: true, reconciled: true } : null;
    }
}

class Checkpoint extends Error {}

function harness(app: Application, execution: Execution) {
    const journal = new Map<string, Json>();
    const labels: string[] = [];
    const checkpoints: string[] = [];
    const stage0 = app.lifecycle.stage0(app.registry.list()[0]!.id);
    const revision = Object.values(execution.sourcePins)[0]?.split(":")[0] || "";
    const context: FactoryContext = {
        args: { featureId: app.registry.list()[0]!.id, executionId: execution.id }, runId: randomUUID(),
        session: { sessionId: execution.sessionId },
        phase: () => undefined, log: () => undefined,
        pause: async (key) => { checkpoints.push(key); throw new Checkpoint(key); },
        step: async (key, producer, options) => {
            if (options?.volatile) return producer();
            if (!journal.has(key)) journal.set(key, await producer());
            return journal.get(key)!;
        },
        agent: async (prompt, options) => {
            const label = options?.label || "";
            labels.push(label);
            if (label.endsWith("Repository evidence") || label.endsWith("Design author") || label.endsWith("Independent design reviewer")) {
                for (const task of stage0.designTasks || []) assert.ok(prompt.includes(task), "approved design obligations must reach every design worker and reviewer");
            }
            if (label.endsWith("Repository evidence")) {
                const rev = revision;
                return {
                    approvalVerification: { verified: true, revision: stage0.revision, reference: stage0.approval.reference, rationale: "Fixture read the actual approval record." },
                    references: [{ id: "E1", repository: "common", revision: rev, path: "A.kt", locator: "A" }],
                    facts: [{ statement: "The compatibility contract exists.", referenceIds: ["E1"] }],
                    sourceVerification: [{ repository: "common", revision: rev, verified: true, rationale: "Inspected pinned test fixture." }],
                    scenarioTraces: [{
                        id: "T1",
                        requirementIds: ["R1"],
                        scenario: "Compatibility",
                        trigger: "Caller request",
                        conditions: "Supported caller",
                        steps: [{ behavior: "A accepts the request.", referenceIds: ["E1"] }],
                        terminalOutcome: { behavior: "A returns compatible response.", referenceIds: ["E1"] },
                        status: "verified",
                        unknowns: [],
                    }],
                    conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
                };
            }
            if (label.endsWith("Design author") || label.endsWith("Design revision")) {
                const rev = revision;
                const ev = {
                    approvalVerification: { verified: true, revision: stage0.revision, reference: stage0.approval.reference, rationale: "Fixture read the actual approval record." },
                    references: [{ id: "E1", repository: "common", revision: rev, path: "A.kt", locator: "A" }],
                    facts: [{ statement: "The compatibility contract exists.", referenceIds: ["E1"] }],
                    sourceVerification: [{ repository: "common", revision: rev, verified: true, rationale: "Inspected pinned test fixture." }],
                    scenarioTraces: [{
                        id: "T1",
                        requirementIds: ["R1"],
                        scenario: "Compatibility",
                        trigger: "Caller request",
                        conditions: "Supported caller",
                        steps: [{ behavior: "A accepts the request.", referenceIds: ["E1"] }],
                        terminalOutcome: { behavior: "A returns compatible response.", referenceIds: ["E1"] },
                        status: "verified",
                        unknowns: [],
                    }],
                    conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
                };
                return {
                    stage0Revision: stage0.revision,
                    evidenceDigest: digest(ev),
                    sections: Object.fromEntries(sections.map((key: string) => [key, `Evidence-backed content for ${key}.`])),
                    traceability: [{ requirementId: "R1", sectionIds: ["requirements"], evidenceIds: ["E1"], verification: "Run the compatibility test." }],
                    changes: [{
                        id: "C1",
                        requirementIds: ["R1"],
                        sectionIds: ["requirements"],
                        traceIds: ["T1"],
                        evidenceIds: ["E1"],
                        classification: "new-feature",
                        implementation: "required",
                        baselineBehavior: "Existing baseline contract.",
                        requestedDelta: "Implement R1 compatibility.",
                        necessity: "Required by R1.",
                        verification: "Run the compatibility test.",
                        counterevidence: {
                            referenceIds: ["E1"],
                            searchSummary: "Inspected A.kt and callers.",
                            conclusion: "Confirmed feature delta.",
                            result: "confirmed-gap",
                        },
                    }],
                    assumptions: [], openDecisions: [], blockingUnknowns: [],
                };
            }
            if (label.endsWith("Independent design reviewer")) {
                const rev = revision;
                const ev = {
                    approvalVerification: { verified: true, revision: stage0.revision, reference: stage0.approval.reference, rationale: "Fixture read the actual approval record." },
                    references: [{ id: "E1", repository: "common", revision: rev, path: "A.kt", locator: "A" }],
                    facts: [{ statement: "The compatibility contract exists.", referenceIds: ["E1"] }],
                    sourceVerification: [{ repository: "common", revision: rev, verified: true, rationale: "Inspected pinned test fixture." }],
                    scenarioTraces: [{
                        id: "T1",
                        requirementIds: ["R1"],
                        scenario: "Compatibility",
                        trigger: "Caller request",
                        conditions: "Supported caller",
                        steps: [{ behavior: "A accepts the request.", referenceIds: ["E1"] }],
                        terminalOutcome: { behavior: "A returns compatible response.", referenceIds: ["E1"] },
                        status: "verified",
                        unknowns: [],
                    }],
                    conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
                };
                const d = {
                    stage0Revision: stage0.revision,
                    evidenceDigest: digest(ev),
                    sections: Object.fromEntries(sections.map((key: string) => [key, `Evidence-backed content for ${key}.`])),
                    traceability: [{ requirementId: "R1", sectionIds: ["requirements"], evidenceIds: ["E1"], verification: "Run the compatibility test." }],
                    changes: [{
                        id: "C1",
                        requirementIds: ["R1"],
                        sectionIds: ["requirements"],
                        traceIds: ["T1"],
                        evidenceIds: ["E1"],
                        classification: "new-feature",
                        implementation: "required",
                        baselineBehavior: "Existing baseline contract.",
                        requestedDelta: "Implement R1 compatibility.",
                        necessity: "Required by R1.",
                        verification: "Run the compatibility test.",
                        counterevidence: {
                            referenceIds: ["E1"],
                            searchSummary: "Inspected A.kt and callers.",
                            conclusion: "Confirmed feature delta.",
                            result: "confirmed-gap",
                        },
                    }],
                    assumptions: [], openDecisions: [], blockingUnknowns: [],
                };
                return {
                    designDigest: digest(d),
                    evidenceDigest: digest(ev),
                    references: [],
                    changeChecks: [{
                        changeId: "C1",
                        baselineVerified: true,
                        necessityConfirmed: true,
                        counterevidenceVerified: true,
                        referenceIds: ["E1"],
                        rationale: "Independently verified.",
                    }],
                    criteria: rubric.map((id: string) => ({ id, pass: true })),
                    findings: [],
                    blockingUnknowns: [],
                };
            }
            if (label.includes(":planner:")) return plan();
            if (label.includes(":plan-review:")) return { pass: true, findings: [] };
            if (label.includes(":validate:")) {
                const id = label.split(":")[2];
                const item = plan().items.find((entry) => entry.id === id)!;
                return { criteria: item.acceptanceCriteria.map((criterion) => ({ criterion, status: "addressed", evidence: `${item.files[0]} and compatibility tests.` })) };
            }
            if (label.startsWith("Observe PR #")) {
                return {
                    verdict: "ready",
                    repairKind: "none",
                    summary: "All CI checks passing and no active review threads.",
                    evidence: ["CI green"],
                    actionableFindings: [],
                };
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

// Keep the scenario-specific fixtures below while speaking the real shared engine's schemas.
function useCodeFeatureFixtures(h: ReturnType<typeof harness>) {
    const scenario = h.context.agent;
    h.context.agent = async (prompt, options) => {
        if (/^(Observe PR|Tactical repair|Verify tactical repair)/.test(options?.label || "")) return scenario(prompt, options);
        assert.doesNotMatch(prompt, /\/SKILL\.md/, "shared preparation must keep Factory workers on domain references, not phase launchers");
        const data = JSON.parse(prompt.split("\nDATA:\n").at(-1)!.split("\nREAD-ONLY TOOL SCOPE")[0]!);
        const task = data.task;
        const target = task.targets[0];
        if (options?.label === "Repository evidence") return {
            approvalVerification: { verified: true, revision: task.revision, reference: task.approval.reference,
                rationale: "Fixture checked the supplied exact Factory dispatch." },
            references: [{ id: "E1", repository: target.repository, revision: "fixture", path: "A.kt", locator: "A" }],
            facts: [{ statement: "A has the baseline response contract.", referenceIds: ["E1"] }],
            conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
        };
        const builder = options?.agent === "software-factory-coder";
        const round = data.round;
        const result = object(await scenario(prompt, { ...options,
            label: `Factory:${builder ? "coder" : "code-reviewer"}:${target.id}:round-${round}` }), "Scenario response");
        if (builder) {
            const files = (result.files as Record<string, Json>[]).map((file) => ({
                path: file.path, content: file.content, action: file.action === "delete" ? "delete" : "modify", summary: "Fixture change.",
            }));
            return {
                taskRevision: task.revision,
                targets: [{
                    targetId: target.id, repository: target.repository, summary: result.summary, files,
                    testsAddedOrUpdated: result.testsAddedOrUpdated, verificationCommandsRun: [],
                    pr: { title: task.title, summary: result.summary, testPlan: "Source-only test fixture; commands unrun.",
                        breakingChanges: "None.", rolloutAndFlag: "Preserve baseline compatibility." },
                }],
                traceability: task.requirements.map((requirement: { id: string }) => ({
                    requirementId: requirement.id, targetIds: [target.id], filePaths: files.map((file) => file.path),
                    testLocations: result.testsAddedOrUpdated, verification: "Fixture source assertions.",
                })),
                assumptions: [], openDecisions: [], blockingUnknowns: result.blockingUnknowns,
            };
        }
        return {
            criteria: codingRubric.map((id: string) => ({ id, pass: result.pass })),
            acceptanceCriteria: (result.criteria as Record<string, Json>[]).map((criterion) => ({
                targetId: target.id, criterion: criterion.criterion, passed: criterion.status === "addressed", evidence: criterion.evidence,
            })),
            findings: (result.findings as Record<string, Json>[]).map((finding) => ({
                ...finding, targetId: target.id, rubricId: "correctnessAndEdgeCases",
            })),
            blockingUnknowns: [],
        };
    };
}

function approve(app: Application, featureId: string, stage: "design" | "plan" | "backlog" | "dispatch" | "complete") {
    const preview = app.lifecycle.gate(featureId, stage);
    return app.lifecycle.decide(featureId, stage, preview.scopeHash, true, { ...actor, reference: randomUUID() });
}

async function approvedDispatchWave(app: Application, mode: "local" | "remote", planned = plan(), configuration = config()) {
    app.lifecycle.configure({ ...configuration, delivery: { mode, maxReviewRounds: 3 } }, actor);
    const feature = app.execute({ kind: "create_feature", requestId: randomUUID(), input: requirements() }, actor);
    app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
        { granted: true, revisionId: feature.current.id, actor });
    const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
    approve(app, feature.id, "design");
    app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, planned, design.id);
    approve(app, feature.id, "plan");
    approve(app, feature.id, "backlog");
    await app.lifecycle.backlog(feature.id);
    const decision = approve(app, feature.id, "dispatch");
    const approved = object(decision.scope, "Approved dispatch");
    const eligible = approved.eligible as Record<string, unknown>[];
    return { feature, decision, item: app.lifecycle.plan(feature.id).value.items[0]!, entry: eligible[0]! };
}

test("direct dispatch and local publication enforce the approved mode and exact work set", async () => {
    for (const mode of ["local", "remote"] as const) {
        const f = fixture();
        const provider = new FakeProvider();
        const app = new Application({ ...f.options, provider });
        try {
            const { feature, decision, item, entry } = await approvedDispatchWave(app, mode);
            const implementation: LocalImplementationInput = {
                branch: "factory/approved", summary: "Reviewed implementation.",
                files: [{ path: "A.kt", action: "write", content: "reviewed" }],
                roundsCompleted: 1, maxRounds: 3, reviewHistory: [],
                sourceBinding: { head: "a".repeat(40), indexTree: "b".repeat(40), base: "a".repeat(40), tree: "c".repeat(40) },
                verification: {},
            };
            const rejectsChangedScope = (error: unknown) => error instanceof FactoryError && error.code === "dispatch_scope_changed";
            if (mode === "local") {
                await assert.rejects(() => app.lifecycle.dispatch(feature.id), rejectsChangedScope);
                await assert.rejects(() => app.lifecycle.dispatchItem(feature.id, item, entry.dependencyContext, decision), rejectsChangedScope);
                await assert.rejects(() => app.lifecycle.publishLocalItem(feature.id, item, entry.dependencyContext,
                    { ...implementation, maxRounds: 2 }, decision), rejectsChangedScope);
                const execution = app.lifecycle.begin(feature.id, "implement", actor);
                await assert.rejects(() => app.lifecycle.publishLocalItem(feature.id, item, entry.dependencyContext,
                    implementation, decision, execution.id),
                (error: unknown) => error instanceof FactoryError && error.code === "review_evidence_required");
            } else {
                await assert.rejects(() => app.lifecycle.publishLocalItem(feature.id, item, entry.dependencyContext,
                    implementation, decision), rejectsChangedScope);
                await assert.rejects(() => app.lifecycle.dispatchItem(feature.id, { ...item, description: "Unapproved change." },
                    entry.dependencyContext, decision), rejectsChangedScope);
                await assert.rejects(() => app.lifecycle.dispatchItem(feature.id, app.lifecycle.plan(feature.id).value.items[2]!,
                    [], decision), rejectsChangedScope);
                await assert.rejects(() => app.lifecycle.dispatchItem(feature.id, item, undefined, decision), rejectsChangedScope);
                await assert.rejects(() => app.lifecycle.dispatchItem(feature.id, item, entry.dependencyContext,
                    { ...decision, scopeHash: "changed" }),
                (error: unknown) => error instanceof FactoryError && error.code === "approval_lineage_changed");
            }
            assert.equal(provider.sends, 0);
            assert.equal(provider.localPreparations, 0, "a rejected scope must not prepare a commit");
            assert.equal(provider.localPublishes, 0);
        } finally { app.close(); f.cleanup(); }
    }
});

test("local publication requires matching review evidence from its active execution", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    try {
        const { feature, decision, item, entry } = await approvedDispatchWave(app, "local");
        const execution = app.lifecycle.begin(feature.id, "implement", actor);
        const source = { head: "a".repeat(40), indexTree: "b".repeat(40), base: "a".repeat(40), tree: "c".repeat(40) };
        const verification = { passed: [], failed: [], unrun: item.tests };
        const round = {
            round: 1, mode: "local", builderSummary: "Reviewed implementation.", files: ["A.kt"], verification, sourceBinding: source,
            review: { pass: true, criteria: [{ criterion: item.acceptanceCriteria[0]!, status: "addressed", evidence: "Reviewed A.kt." }], findings: [] },
        };
        const implementation: LocalImplementationInput = {
            branch: "factory/approved", summary: round.builderSummary,
            files: [{ path: "A.kt", action: "write", content: "reviewed" }],
            roundsCompleted: 1, maxRounds: 3, reviewHistory: [round], sourceBinding: source, verification,
        };
        const planId = app.lifecycle.plan(feature.id).artifact.id;
        const saved = {
            itemId: item.id, mode: "local", recommendation: "REVIEWED", branch: implementation.branch,
            maxRounds: 3, roundsCompleted: 1, verification, sourceBinding: source, reviewHistory: [round],
        };
        const rejectsReview = (error: unknown) => error instanceof FactoryError && error.code === "review_evidence_required";
        const first = app.lifecycle.store.artifact(feature.id, "implementation_attempt", feature.current.id, saved, planId, execution.id);
        for (const changed of [
            { ...implementation, branch: "factory/other" },
            { ...implementation, summary: "Different summary." },
            { ...implementation, files: [{ path: "other.kt", action: "write" as const, content: "reviewed" }] },
            { ...implementation, sourceBinding: { ...source, tree: "d".repeat(40) } },
            { ...implementation, verification: { ...verification, unrun: [] } },
            { ...implementation, reviewHistory: [] },
        ]) {
            await assert.rejects(() => app.lifecycle.publishLocalItem(feature.id, item, entry.dependencyContext, changed, decision, execution.id), rejectsReview);
        }
        const failedRound = { ...round, review: { ...round.review, pass: false } };
        app.lifecycle.store.artifact(feature.id, "implementation_attempt", feature.current.id,
            { ...saved, reviewHistory: [failedRound] }, planId, execution.id);
        await assert.rejects(() => app.lifecycle.publishLocalItem(feature.id, item, entry.dependencyContext,
            { ...implementation, reviewHistory: [failedRound] }, decision, execution.id), rejectsReview);
        const partialRound = { ...round, review: { ...round.review, criteria: [{ ...round.review.criteria[0]!, status: "partial" }] } };
        app.lifecycle.store.artifact(feature.id, "implementation_attempt", feature.current.id,
            { ...saved, reviewHistory: [partialRound] }, planId, execution.id);
        await assert.rejects(() => app.lifecycle.publishLocalItem(feature.id, item, entry.dependencyContext,
            { ...implementation, reviewHistory: [partialRound] }, decision, execution.id), rejectsReview);

        app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "paused" });
        await assert.rejects(() => app.lifecycle.publishLocalItem(feature.id, item, entry.dependencyContext,
            implementation, decision, execution.id),
        (error: unknown) => error instanceof FactoryError && error.code === "execution_not_active");
        app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "cancelled" });
        const successor = app.lifecycle.begin(feature.id, "implement", actor);
        const successorArtifact = app.lifecycle.store.artifact(feature.id, "implementation_attempt", feature.current.id, saved, planId, successor.id);
        assert.notEqual(successorArtifact.id, first.id, "identical review evidence must retain its execution owner");
        assert.equal(successorArtifact.executionId, successor.id);
        assert.equal(provider.localPreparations, 0);
        assert.equal(provider.localPublishes, 0);
    } finally { app.close(); f.cleanup(); }
});

test("dispatch rechecks authorization after the final shared checkpoint", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    try {
        const { feature, decision, item, entry } = await approvedDispatchWave(app, "remote");
        let checkpoints = 0;
        app.lifecycle.checkpoint = async () => {
            if (++checkpoints === 2) {
                app.lifecycle.configure({
                    ...config(),
                    repositories: { common: { ...config().repositories.common!, account: "changed-account" } },
                    delivery: { mode: "remote", maxReviewRounds: 3 },
                }, actor);
            }
        };
        await assert.rejects(() => app.lifecycle.dispatchItem(feature.id, item, entry.dependencyContext, decision),
            (error: unknown) => error instanceof FactoryError && error.code === "stale_scope");
        assert.equal(provider.sends, 0);
        assert.equal(app.lifecycle.store.state(feature.id).operations.find((operation) => operation.kind === "dispatch")?.status, "failed");
    } finally { app.close(); f.cleanup(); }
});

test("dispatch does not send after its execution is paused or cancelled at the final checkpoint", async () => {
    for (const status of ["paused", "cancelled"] as const) {
        const f = fixture();
        const provider = new FakeProvider();
        const app = new Application({ ...f.options, provider });
        try {
            const { feature, decision, item, entry } = await approvedDispatchWave(app, "remote");
            const execution = app.lifecycle.begin(feature.id, "implement", actor);
            let checkpoints = 0;
            app.lifecycle.checkpoint = async () => {
                if (++checkpoints === 2) app.lifecycle.store.updateExecution(feature.id, execution.id, { status });
            };
            await assert.rejects(() => app.lifecycle.dispatchItem(feature.id, item, entry.dependencyContext, decision, execution.id),
                (error: unknown) => error instanceof FactoryError &&
                    error.code === (status === "paused" ? "execution_not_active" : "execution_stale"));
            assert.equal(provider.sends, 0);
            assert.equal(app.lifecycle.store.state(feature.id).operations.find((operation) => operation.kind === "dispatch")?.status, "failed");
        } finally { app.close(); f.cleanup(); }
    }
});

test("automatic review feedback rechecks run and canonical PR authority before posting", async () => {
    for (const change of ["cancel-during-lookup", "head-changed", "cancel-at-checkpoint", "canonical-changed"] as const) {
        const f = fixture();
        const provider = new FakeProvider();
        const app = new Application({ ...f.options, provider });
        try {
            const { feature, decision, item, entry } = await approvedDispatchWave(app, "remote");
            const execution = app.lifecycle.begin(feature.id, "implement", actor);
            await app.lifecycle.dispatchItem(feature.id, item, entry.dependencyContext, decision, execution.id);
            const pr = structuredClone(provider.prs.get(item.id)!);
            provider.findFeedback = async () => {
                if (change === "cancel-during-lookup") app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "cancelled" });
                if (change === "head-changed") provider.prs.get(item.id)!.head = "new-head";
                return null;
            };
            let checkpoints = 0;
            app.lifecycle.checkpoint = async () => {
                if (++checkpoints !== 2) return;
                if (change === "cancel-at-checkpoint") app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "cancelled" });
                if (change === "canonical-changed") {
                    app.lifecycle.store.change(feature.id, (state) => state.prBindings.push({
                        planId: app.lifecycle.plan(feature.id).artifact.id, itemId: item.id, number: pr.number + 1, actor,
                    }));
                }
            };
            await assert.rejects(() => app.lifecycle.postReviewRoundFeedback(feature.id, item, pr, 1, "Fix the reviewed issue.", actor, execution.id),
                (error: unknown) => error instanceof FactoryError &&
                    error.code === (change.startsWith("cancel") ? "execution_stale" : "stale_pr"));
            assert.equal(provider.comments, 0, `${change} must not post feedback`);
        } finally { app.close(); f.cleanup(); }
    }
});

test("uncertain automatic review feedback is reconciled without reposting", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    try {
        const { feature, decision, item, entry } = await approvedDispatchWave(app, "remote");
        const execution = app.lifecycle.begin(feature.id, "implement", actor);
        await app.lifecycle.dispatchItem(feature.id, item, entry.dependencyContext, decision, execution.id);
        const pr = structuredClone(provider.prs.get(item.id)!);
        provider.postFeedback = async () => { provider.comments++; throw new Error("Response lost after posting."); };
        const post = (body = "Fix the reviewed issue.") =>
            app.lifecycle.postReviewRoundFeedback(feature.id, item, pr, 1, body, actor, execution.id);
        await assert.rejects(() => post(), /Response lost/);
        const operation = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.kind === "feedback")!;
        assert.equal(operation.status, "reconciliation_required");
        await assert.rejects(() => post("A different issue."),
            (error: unknown) => error instanceof FactoryError && error.code === "request_conflict");
        await assert.rejects(() => post(),
            (error: unknown) => error instanceof FactoryError && error.code === "reconciliation_required");
        assert.equal(provider.comments, 1);
        provider.findFeedback = async () => ({ commentId: "original" });
        assert.deepEqual(await post(), { commentId: "original" });
        assert.equal(provider.comments, 1);
        assert.equal(app.lifecycle.store.state(feature.id).operations.find((entry) => entry.key === operation.key)?.status, "completed");
    } finally { app.close(); f.cleanup(); }
});

async function feedbackFeature(app: Application) {
    app.lifecycle.configure({ ...config(), delivery: { mode: "remote", maxReviewRounds: 3 } }, actor);
    const feature = app.execute({ kind: "create_feature", requestId: "feedback", input: requirements() }, actor);
    app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
    const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
    approve(app, feature.id, "design");
    const artifact = app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, plan(), design.id);
    approve(app, feature.id, "plan"); approve(app, feature.id, "backlog");
    await app.lifecycle.backlog(feature.id);
    approve(app, feature.id, "dispatch");
    await app.lifecycle.dispatch(feature.id);
    app.lifecycle.store.artifact(feature.id, "monitor", feature.current.id, await app.lifecycle.observe(feature.id), artifact.id);
    return feature;
}

test("the actual workflow functions complete design -> plan -> backlog -> dependency waves -> validation -> completion", async () => {
    const f = fixture();
    initializeRepository(f.workspace);
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        app.lifecycle.configure({ ...config(), delivery: { mode: "remote", maxReviewRounds: 3 } }, actor);
        const feature = app.execute({ kind: "create_feature", requestId: "lifecycle",
            input: { ...requirements(), designTasks: ["Trace IPC compatibility and specify request-context propagation."] } }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.begin(feature.id, "design", actor, await sourcePins(app.lifecycle));
        const d = harness(app, design);
        const designResult = await finished(app, feature.id, design, () => designRun(d.context, { provider }));
        assert.equal(object(designResult, "Design result").outcome, "design_ready_for_review");
        assert.equal(d.labels.length, 3);
        assert.equal(d.checkpoints.length, 0, "business approval must not pause a workflow");
        assert.equal(app.lifecycle.gate(feature.id, "design").approved, false);
        approve(app, feature.id, "design");

        const delivery = app.lifecycle.begin(feature.id, "plan", actor);
        const l = harness(app, delivery);
        const planResult = await finished(app, feature.id, delivery, () => planRun(l.context, { provider }));
        assert.equal(object(planResult, "Plan result").outcome, "plan_ready_for_review");
        assert.equal(l.checkpoints.length, 0);
        assert.equal(provider.creations, 0);
        approve(app, feature.id, "plan");
        await assert.rejects(() => app.lifecycle.backlog(feature.id), /approval/);
        assert.equal(provider.creations, 0, "plan approval is not backlog authorization");
        approve(app, feature.id, "backlog");

        await app.lifecycle.backlog(feature.id);
        assert.equal(provider.creations, 3);
        approve(app, feature.id, "dispatch");
        await app.lifecycle.dispatch(feature.id);
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
        await app.lifecycle.dispatch(feature.id);
        assert.equal(provider.creations, 3);
        assert.equal(provider.sends, 3);
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

test("design source manifests require the corresponding checkout and pin for every repository", async () => {
    const f = fixture();
    try {
        const revision = initializeRepository(f.workspace);
        const second = path.join(f.workspace, "second");
        mkdirSync(second);
        const configuration = config();
        configuration.repositories.second = { ...configuration.repositories.common!, localPath: "second" };
        f.app.lifecycle.configure(configuration, actor);
        await assert.rejects(() => sourcePins(f.app.lifecycle),
            (error: unknown) => error instanceof FactoryError && error.code === "source_repository_missing");

        const secondRevision = initializeRepository(second, "// second repository baseline\n");
        assert.notEqual(secondRevision, revision);
        const pins = await sourcePins(f.app.lifecycle);
        assert.deepEqual(buildDesignSourceManifest(f.app.lifecycle, { sourcePins: pins }), [
            { repository: "common", revision }, { repository: "second", revision: secondRevision },
        ]);
        const withoutSecond = { ...pins };
        delete withoutSecond[Object.keys(pins).find((key) => key.toLowerCase() === second.toLowerCase())!];
        assert.throws(() => buildDesignSourceManifest(f.app.lifecycle, { sourcePins: withoutSecond }),
            (error: unknown) => error instanceof FactoryError && error.code === "source_pin_missing");
        configuration.repositories.common!.localPath = "second";
        f.app.lifecycle.configure(configuration, actor);
        assert.deepEqual(buildDesignSourceManifest(f.app.lifecycle, { sourcePins: pins }), [
            { repository: "common", revision: secondRevision }, { repository: "second", revision: secondRevision },
            { repository: "android-complete", revision },
        ], "the integration workspace must be citable when standalone and integrated dependency mappings differ");
        const unconfigured = path.join(f.workspace, "unconfigured");
        mkdirSync(unconfigured);
        initializeRepository(unconfigured, "// unconfigured repository\n");
        await assert.rejects(() => sourcePins(f.app.lifecycle),
            (error: unknown) => error instanceof FactoryError && error.code === "source_repository_unpinned");
        const otherReference = path.join(f.workspace, "other-reference");
        mkdirSync(otherReference);
        initializeRepository(otherReference, "// other consumer baseline\n");
        configuration.referenceRepositories = {
            caller: { ...configuration.repositories.common!, localPath: "unconfigured" },
            otherCaller: { ...configuration.repositories.common!, localPath: "other-reference" },
        };
        f.app.lifecycle.configure(configuration, actor);
        const referencePins = await sourcePins(f.app.lifecycle);
        assert.ok(Object.keys(referencePins).some((root) => root.toLowerCase() === unconfigured.toLowerCase()));
        assert.ok(buildDesignSourceManifest(f.app.lifecycle, { sourcePins: referencePins }).some((source) => source.repository === "caller"));
        assert.throws(() => parsePlan({ ...plan(), items: [{ ...plan().items[0], repository: "caller" }] }, configuration, ["R1"]),
            /Unknown repository: caller/, "research admission must not grant a backlog or delivery target");
        f.app.lifecycle.configure({ ...configuration,
            referenceRepositories: Object.fromEntries(Object.entries(configuration.referenceRepositories).reverse()) }, actor);
        assert.equal(JSON.stringify(await sourcePins(f.app.lifecycle)), JSON.stringify(referencePins),
            "shared canonical ordering must not look like source drift after admission");
    } finally { f.cleanup(); }
});

test("reference source configuration keeps legacy scopes stable and rejects ambiguous aliases", () => {
    const legacy = config();
    assert.deepEqual(parseConfiguration(legacy), legacy);
    assert.deepEqual(parseConfiguration({ ...legacy, referenceRepositories: {} }), legacy);
    const reference = { ...legacy.repositories.common!, localPath: "consumer", account: "local-reader" };
    const configured = parseConfiguration({ ...legacy, referenceRepositories: { consumer: reference } });
    assert.deepEqual(configured.repositories, legacy.repositories);
    assert.deepEqual(analysisConfiguration(configured), {
        repositories: { common: { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev" } },
        referenceRepositories: { consumer: { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev" } },
    });
    assert.deepEqual(sharedConfiguration(configured).referenceRepositories?.consumer,
        { ...reference, localPath: ".", account: "local-account-required" });
    assert.throws(() => parseConfiguration({ ...legacy, referenceRepositories: { common: reference } }), /distinct aliases/);
    assert.throws(() => parseConfiguration({ ...legacy, referenceRepositories: { consumer: { ...reference, hostname: "https://github.com" } } }), /hostname/i);
});

test("admitting a reference repository requires new confirmation and invalidates the previous execution scope", async () => {
    const f = fixture();
    try {
        f.app.lifecycle.configure(config(), actor);
        const feature = f.app.execute({ kind: "create_feature", requestId: "reference-scope", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const execution = f.app.lifecycle.begin(feature.id, "design", actor);
        const configuration = config();
        configuration.referenceRepositories = { consumer: { ...configuration.repositories.common!, localPath: "consumer" } };
        const command = { kind: "configure", configuration };
        const preview = f.app.prepare(command);
        assert.ok(preview.consentKey);
        await assert.rejects(() => f.app.dispatch(command, actor), /confirmation|approval|consent/i);
        await f.app.dispatch(command, actor, { granted: true, revisionId: preview.consentKey!, actor });
        assert.throws(() => f.app.lifecycle.assertFresh(feature.id, execution.id), /configuration.*changed/);
        assert.equal(f.app.lifecycle.store.execution(feature.id, execution.id).configurationId, execution.configurationId);
    } finally { f.cleanup(); }
});

test("completion cannot waive an unresolved independent review before its round limit", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    try {
        const { feature } = await approvedDispatchWave(app, "remote");
        await app.lifecycle.dispatch(feature.id);
        for (const pr of provider.prs.values()) pr.status = "merged";
        const planArtifact = app.lifecycle.plan(feature.id).artifact;
        app.lifecycle.store.artifact(feature.id, "monitor", feature.current.id,
            await app.lifecycle.observe(feature.id), planArtifact.id);
        approve(app, feature.id, "dispatch");
        await app.lifecycle.dispatch(feature.id);
        provider.prs.get("C")!.status = "merged";
        const report = await app.lifecycle.observe(feature.id);
        report.validation = plan().items.flatMap((item) => item.acceptanceCriteria.map((criterion) => ({
            itemId: item.id, criterion, status: "addressed" as const, evidence: "Verified behavior.",
        })));
        report.reviews = [{
            itemId: "A", head: provider.prs.get("A")!.head, round: 1, maxRounds: 3,
            pass: false, status: "changes_requested", suggestedFeedback: "Fix the ownership defect.",
            findings: [{ id: "F1", severity: "major", category: "concurrency", file: "A.kt",
                location: "A.call", evidence: "Ownership is lost before delivery.", requiredChange: "Retain ownership." }],
        }];
        app.lifecycle.store.artifact(feature.id, "monitor", feature.current.id, report, planArtifact.id);
        const blocked = app.lifecycle.gate(feature.id, "complete");
        assert.equal(report.delivered, true, "provider delivery alone is not source-review approval");
        assert.equal(blocked.ready, false);
        assert.match(blocked.blockers.join(" "), /independent.*review/i);
        assert.throws(() => approve(app, feature.id, "complete"), /independent.*review/i);
        report.reviews[0] = { ...report.reviews[0]!, pass: true, status: "approved", findings: [], suggestedFeedback: null };
        app.lifecycle.store.artifact(feature.id, "monitor", feature.current.id, report, planArtifact.id);
        assert.equal(app.lifecycle.gate(feature.id, "complete").ready, true);
    } finally { app.close(); f.cleanup(); }
});

test("monitor rejects PR drift during review and never saves stale validation", async () => {
    for (const change of ["head", "canonical-pr"] as const) {
        const f = fixture();
        const provider = new FakeProvider();
        const app = new Application({ ...f.options, provider });
        const oldHome = process.env.SOFTWARE_FACTORY_HOME;
        const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
        process.env.SOFTWARE_FACTORY_HOME = f.directory;
        process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
        try {
            const { feature } = await approvedDispatchWave(app, "remote");
            await app.lifecycle.dispatch(feature.id);
            const execution = app.lifecycle.begin(feature.id, "monitor", actor);
            const h = harness(app, execution);
            const agent = h.context.agent;
            h.context.agent = async (prompt, options) => {
                const result = await agent(prompt, options);
                if (options?.label?.includes(":validate:A:")) {
                    const pr = provider.prs.get("A")!;
                    provider.prs.set("A", change === "head" ? { ...pr, head: "new-head" }
                        : { ...pr, number: pr.number + 100, url: `${pr.url}00` });
                }
                return result;
            };
            await assert.rejects(() => monitorRun(h.context, { provider }),
                (error: unknown) => error instanceof FactoryError && error.code === "stale_pr");
            assert.equal(app.lifecycle.store.latest(feature.id, "monitor"), null);
        } finally {
            if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
            if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
            app.close(); f.cleanup();
        }
    }
});

test("monitor does not reuse cached validation for another PR with the same head", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        const { feature } = await approvedDispatchWave(app, "remote");
        await app.lifecycle.dispatch(feature.id);
        const first = app.lifecycle.begin(feature.id, "monitor", actor);
        await finished(app, feature.id, first, () => monitorRun(harness(app, first).context, { provider }));
        const pr = provider.prs.get("A")!;
        provider.prs.set("A", { ...pr, number: pr.number + 100, url: `${pr.url}00` });
        const second = app.lifecycle.begin(feature.id, "monitor", actor);
        const h = harness(app, second);
        await finished(app, feature.id, second, () => monitorRun(h.context, { provider }));
        assert.equal(h.labels.filter((label) => label.includes(":validate:A:")).length, 1);
        assert.equal(h.labels.filter((label) => label.includes(":validate:B:")).length, 0,
            "unchanged PR evidence still avoids another model invocation");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("source pinning never falls back to or follows a checkout outside the workspace", () => {
    const f = fixture();
    try {
        initializeRepository(f.workspace);
        const outside = path.join(f.directory, "outside");
        mkdirSync(outside);
        initializeRepository(outside);
        assert.throws(() => repositorySourceRoot(f.workspace, "../outside"),
            (error: unknown) => error instanceof FactoryError && error.code === "source_repository_outside_workspace");
        const link = path.join(f.workspace, "linked");
        symlinkSync(outside, link, process.platform === "win32" ? "junction" : "dir");
        assert.throws(() => repositorySourceRoot(f.workspace, "linked"),
            (error: unknown) => error instanceof FactoryError && error.code === "source_repository_outside_workspace");
        assert.throws(() => repositorySourceRoot(f.workspace, "missing"),
            (error: unknown) => error instanceof FactoryError && error.code === "source_repository_missing");
    } finally { f.cleanup(); }
});

test("partial local application restores only completed writes and never follows an escaping link", () => {
    const f = fixture();
    try {
        writeFileSync(path.join(f.workspace, "A.kt"), "original");
        const outside = path.join(f.directory, "outside");
        mkdirSync(outside);
        symlinkSync(outside, path.join(f.workspace, "link"), process.platform === "win32" ? "junction" : "dir");
        const backups = new Map<string, LocalBackup>();
        assert.throws(() => applyLocalFiles(f.workspace, [
            { path: "A.kt", action: "write", content: "generated" },
            { path: "link/escape.kt", action: "write", content: "blocked" },
        ], backups), /escapes repository root/);
        backups.set("untouched.kt", { content: Buffer.from("untouched"), mode: undefined, applied: false, written: null });
        restoreLocalFiles(f.workspace, backups);
        assert.equal(readFileSync(path.join(f.workspace, "A.kt"), "utf8"), "original");
    } finally { f.cleanup(); }
});

test("local restoration preserves concurrent edits while restoring unrelated files", () => {
    const f = fixture();
    try {
        writeFileSync(path.join(f.workspace, "A.kt"), "original");
        writeFileSync(path.join(f.workspace, "B.kt"), "original B");
        const backups = new Map<string, LocalBackup>();
        applyLocalFiles(f.workspace, [
            { path: "A.kt", action: "write", content: "generated A" },
            { path: "B.kt", action: "delete", content: "" },
        ], backups);
        writeFileSync(path.join(f.workspace, "A.kt"), "user edit");
        assert.throws(() => restoreLocalFiles(f.workspace, backups), /preserved/);
        assert.equal(readFileSync(path.join(f.workspace, "A.kt"), "utf8"), "user edit");
        assert.equal(readFileSync(path.join(f.workspace, "B.kt"), "utf8"), "original B");
    } finally { f.cleanup(); }
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
        provider.failAfterCreate = true;
        await assert.rejects(() => app.lifecycle.backlog(feature.id), /Connection lost/);
        assert.equal(provider.creations, 1);
        assert.equal(app.lifecycle.store.state(feature.id).operations[0]?.status, "reconciliation_required");
        await app.lifecycle.backlog(feature.id);
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
        provider.afterCreate = () => {
            provider.afterCreate = undefined;
            const preview = app.lifecycle.gate(feature.id, "design");
            app.lifecycle.decide(feature.id, "design", preview.scopeHash, false, { ...actor, reference: randomUUID() });
        };
        await assert.rejects(() => app.lifecycle.backlog(feature.id), /approval was revoked/);
        assert.equal(provider.creations, 1, "an already-admitted send is retained, but no later item is created");
        assert.equal(app.lifecycle.gate(feature.id, "plan").approved, false);
        approve(app, feature.id, "design");
        assert.equal(app.lifecycle.gate(feature.id, "plan").approved, false, "renewing a parent does not silently renew its descendants");
    } finally { app.close(); f.cleanup(); }
});

test("PR feedback rechecks configuration, requirements, cancellation, binding and live head after asynchronous reads", async () => {
    const cases = { configuration: "stale_scope", requirements: "plan_required", cancellation: "feature_cancelled", binding: "monitor_required", head: "stale_pr" };
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

test("Stage 3 defaults to local coding mode and runs the multi-round builder <-> independent reviewer loop before publishing a PR", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        initializeRepository(f.workspace);
        app.lifecycle.configure(config(), actor);
        const feature = app.execute({ kind: "create_feature", requestId: "local-loop", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");
        const singleItemPlan: Plan = {
            summary: "Implement A locally.",
            items: [plan().items[0]!],
            blockingUnknowns: [],
        };
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, singleItemPlan, design.id);
        approve(app, feature.id, "plan");
        approve(app, feature.id, "backlog");
        await app.lifecycle.backlog(feature.id);

        const dispatchGate = app.lifecycle.gate(feature.id, "dispatch");
        const dispatchScope = object(dispatchGate.scope, "Dispatch scope");
        assert.deepEqual(dispatchScope.delivery, { mode: "local", maxReviewRounds: 3 }, "local coding mode with 3 review rounds must be the default");
        approve(app, feature.id, "dispatch");
        const preparedDispatch = app.prepare({ kind: "dispatch_work", featureId: feature.id });
        assert.deepEqual(object(preparedDispatch.scope, "Prepared dispatch_work scope").delivery, { mode: "local", maxReviewRounds: 3 });

        const implementExec = app.lifecycle.begin(feature.id, "implement", actor);
        assert.deepEqual(implementExec.delivery, { mode: "local", maxReviewRounds: 3 });
        const h = harness(app, implementExec);
        const baseAgent = h.context.agent;
        h.context.agent = async (prompt, options) => {
            const label = options?.label || "";
            h.labels.push(label);
            if (label === "Factory:coder:A:round-1") {
                return {
                    summary: "Initial implementation of A.",
                    files: [{ path: "A.kt", action: "write", content: "class A { fun call() = \"v1\" }\n" }],
                    testsAddedOrUpdated: ["ATest.kt"],
                    blockingUnknowns: [],
                };
            }
            if (label === "Factory:code-reviewer:A:round-1") {
                assert.match(prompt, /\+class A \{ fun call\(\) = \\"v1\\" \}/, "independent reviewer must receive the actual diff from round 1");
                return {
                    pass: false,
                    summary: "Missing null-safety guard for legacy response shape.",
                    criteria: [{ criterion: "A preserves the response contract.", status: "partial", evidence: "A.kt omits null-check on legacy field." }],
                    findings: [{
                        id: "F1", severity: "major", category: "compatibility",
                        file: "A.kt", location: "A.call",
                        evidence: "Null legacy field causes NPE.",
                        requiredChange: "Preserve null-safe response shape in A.call.",
                    }],
                };
            }
            if (label === "Factory:coder:A:round-2") {
                assert.match(prompt, /Preserve null-safe response shape in A\.call/, "builder round 2 must receive reviewer findings from round 1");
                return {
                    summary: "Fixed null-safety guard in A.kt per independent reviewer.",
                    files: [{ path: "A.kt", action: "write", content: "class A { fun call(input: String?) = input ?: \"default\" }\n" }],
                    testsAddedOrUpdated: ["ATest.kt"],
                    blockingUnknowns: [],
                };
            }
            if (label === "Factory:code-reviewer:A:round-2") {
                assert.match(prompt, /input \?: \\"default\\"/, "independent reviewer round 2 must inspect the updated diff");
                return {
                    pass: true,
                    summary: "All findings resolved and compatibility contract verified.",
                    criteria: [{ criterion: "A preserves the response contract.", status: "addressed", evidence: "A.kt handles null inputs safely." }],
                    findings: [],
                };
            }
            return baseAgent(prompt, options);
        };

        useCodeFeatureFixtures(h);
        const result = object(await finished(app, feature.id, implementExec, () => implementRun(h.context, { provider })), "Implement result");
        assert.equal(result.outcome, "implementation_reviewed");
        assert.equal(result.mode, "local");
        assert.equal(result.maxRounds, 3);
        assert.equal(provider.localPublishes, 1, "local mode must publish the branch and open a PR after reviewer passes");
        assert.equal(provider.sends, 0, "local mode must not invoke remote coding-agent dispatch");
        assert.equal(provider.localPublications.get("A")?.roundsCompleted, 2);
        const resultItems = result.items as Record<string, unknown>[];
        assert.equal(resultItems[0]?.observerStatus, "ready-for-human-merge", "implementRun must automatically continue into runPrObserver until ready-for-human-merge");
        assert.equal(readFileSync(path.join(f.workspace, "A.kt"), "utf8"), "// baseline\n");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

for (const initialState of ["failing", "pending", "cancelled", "cancelled-review"] as const) test(`Stage 3 continues the original publication after ${initialState} observation`, async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    const controller = new AbortController();
    try {
        initializeRepository(f.workspace);
        app.lifecycle.configure(config(), actor);
        const feature = app.execute({ kind: "create_feature", requestId: "observer-continuation", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, { summary: "Implement A and observe PR.", items: [plan().items[0]!], blockingUnknowns: [] }, design.id);
        approve(app, feature.id, "plan");
        approve(app, feature.id, "backlog");
        await app.lifecycle.backlog(feature.id);
        approve(app, feature.id, "dispatch");

        const origPublishLocal = provider.publishLocal.bind(provider);
        provider.publishLocal = async (cfg, item, wi, marker, impl, binding) => {
            const res = await origPublishLocal(cfg, item, wi, marker, impl, binding);
            provider.prs.get(item.id)!.checks = initialState === "cancelled" ? "pending"
                : initialState === "cancelled-review" ? "passing" : initialState;
            if (initialState === "cancelled") controller.abort();
            return res;
        };

        const implementExec = app.lifecycle.begin(feature.id, "implement", actor);
        const h = harness(app, implementExec);
        h.context.signal = controller.signal;
        if (initialState === "cancelled-review") {
            const step = h.context.step;
            let interrupted = false;
            h.context.step = async (key, producer, options) => {
                const result = await step(key, producer, options);
                if (!interrupted && key.includes("independent-review-v2")) {
                    interrupted = true;
                    controller.abort();
                }
                return result;
            };
        }
        const baseAgent = h.context.agent;
        h.context.agent = async (prompt, options) => {
            const label = options?.label || "";
            h.labels.push(label);
            if (label === "Factory:coder:A:round-1") {
                return {
                    summary: "Initial implementation of A.",
                    files: [{ path: "A.kt", action: "write", content: "class A { fun call() = \"v1\" }\n" }],
                    testsAddedOrUpdated: ["ATest.kt"],
                    blockingUnknowns: [],
                };
            }
            if (label === "Factory:code-reviewer:A:round-1") {
                return {
                    pass: true,
                    summary: "Initial implementation looks good.",
                    criteria: [{ criterion: "A preserves the response contract.", status: "addressed", evidence: "A.kt verified." }],
                    findings: [],
                };
            }
            return baseAgent(prompt, options);
        };

        useCodeFeatureFixtures(h);
        let continuation = h.context;
        let continuedExecution = implementExec;
        if (initialState === "pending") {
            const pending = object(await finished(app, feature.id, implementExec,
                () => implementRun(h.context, { provider })), "Pending implementation");
            assert.equal(pending.outcome, "implementation_pending");
            assert.equal(object((pending.items as Json[])[0], "Pending PR").observerStatus, "waiting-for-ci");
            const step = nextStep(app.lifecycle.store.view(feature.id), []);
            assert.equal(step.failed, undefined);
            assert.equal(step.action?.label, "Refresh published PRs");
            assert.equal(step.action?.command?.workflow, "implement");
            continuedExecution = app.lifecycle.begin(feature.id, "implement", actor);
            continuation = harness(app, continuedExecution).context;
            continuation.agent = async () => { throw new Error("Observation continuation must not rebuild or review source"); };
        } else {
            await assert.rejects(() => implementRun(h.context, { provider }),
                (error: unknown) => initialState.startsWith("cancelled")
                    ? error instanceof Error && error.name === "AbortError"
                    : error instanceof FactoryError && error.code === "pr_observer_blocked");
        }
        assert.equal(provider.localPublishes, initialState === "cancelled-review" ? 0 : 1);
        assert.equal(readFileSync(path.join(f.workspace, "A.kt"), "utf8"), "// baseline\n");
        if (provider.prs.has("A")) provider.prs.get("A")!.checks = "passing";
        continuation.signal = new AbortController().signal;
        const result = object(await finished(app, feature.id, continuedExecution,
            () => implementRun(continuation, { provider })), "Implement + Observer result");
        assert.equal(result.outcome, "implementation_reviewed");
        assert.equal(provider.localPublishes, 1, "continuation must not republish or repair the PR");
        assert.equal(h.labels.filter((label) => label.startsWith("Factory:coder:")).length, 1, "continuation must not restart the builder");
        assert.equal(h.labels.filter((label) => label.startsWith("Factory:code-reviewer:")).length, 1);
        const items = result.items as Record<string, unknown>[];
        assert.equal(items[0]?.observerStatus, "ready-for-human-merge");
        const observer = object(items[0]?.observer, "Observer result");
        assert.equal(observer.status, "ready-for-human-merge");
        assert.equal(readFileSync(path.join(f.workspace, "A.kt"), "utf8"), "// baseline\n");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

for (const scenario of ["checkout", "stale", "fallback", "lost-stage", "partial-retry", "lost-policy", "lost-status", "uncertain", "revoked", "cancelled", "legacy-effects", "repeat-head"] as const) {
test(`Factory runs complete observer effects: ${scenario}`, async () => {
    const f = fixture();
    const service = adoObserverFixture(scenario === "stale" || scenario === "lost-policy" ? "stale" : scenario === "fallback" ? "fallback" : "checkout");
    if (scenario === "partial-retry") {
        service.state.stages.push({ type: "Stage", id: "another-stage", identifier: "Second", attempt: 1, state: "completed", result: "failed" });
    }
    const provider = new FakeProvider();
    provider.prepareObserverEffect = service.provider.prepareObserverEffect.bind(service.provider);
    provider.sendObserverMutation = service.provider.sendObserverMutation.bind(service.provider);
    provider.findObserverMutation = service.provider.findObserverMutation.bind(service.provider);
    const app = new Application({ ...f.options, provider });
    const abort = new AbortController();
    try {
        const { feature, decision, item } = await approvedDispatchWave(app, "local", { ...plan(), items: [plan().items[0]!] },
            { ...config(), repositories: { common: service.repository } });
        if (scenario === "legacy-effects") {
            app.lifecycle.store.change(feature.id, (state) => {
                const grant = state.decisions.find((entry) => entry.id === decision.id)!;
                const scope = object(grant.scope, "Dispatch scope");
                scope.observer = { engine: "pr-observer", maxRepairs: 2 };
                grant.scopeHash = hash(scope);
            });
        }
        const execution = app.lifecycle.begin(feature.id, "implement", actor);
        app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "running" });
        const publication: Operation = {
            key: hash("fixture-publication"), kind: "dispatch", itemId: item.id,
            planId: app.lifecycle.plan(feature.id).artifact.id, configurationId: app.lifecycle.store.configuration()!.id,
            claimId: randomUUID(), approvalId: decision.id, dispatchMode: "local", status: "completed", error: null,
            updatedAt: new Date().toISOString(),
            publicationBinding: { branch: "factory/test", headSha: service.state.head,
                source: { head: "d".repeat(40), base: "d".repeat(40), indexTree: "e".repeat(40), tree: "f".repeat(40) },
                target: { provider: "ado", hostname: service.repository.hostname, slug: service.repository.slug, baseBranch: "dev",
                    publisherId: service.state.publisher, publisherAccount: "" } },
            receipt: { mode: "local", provider: "ado", marker: `software-factory-${hash("fixture-publication")}`, branch: "factory/test",
                headSha: service.state.head, prNumber: 42, url: String(object(service.snapshot(), "PR snapshot").url) },
        };
        provider.dispatches.set(`software-factory-${publication.key}`, publication.receipt!);
        app.lifecycle.store.change(feature.id, (state) => state.operations.push(publication));
        const workItem = [...provider.items.values()].find((entry) => entry.key === item.id)!;
        const h = harness(app, execution);
        h.context.signal = abort.signal;
        let workers = 0;
        h.context.agent = async () => { workers++; throw new Error("Infrastructure recovery must not spend a source repair turn."); };
        const originalSend = provider.sendObserverMutation.bind(provider);
        let revokeAtSend = scenario === "revoked" || scenario === "cancelled";
        provider.sendObserverMutation = async (...args) => {
            if (revokeAtSend) {
                revokeAtSend = false;
                if (scenario === "cancelled") abort.abort();
                else app.lifecycle.store.change(feature.id, (state) => {
                    state.decisions.push({ ...decision, id: randomUUID(), approved: false, createdAt: new Date().toISOString() });
                });
            }
            return originalSend(...args);
        };
        const run = async () => {
            const managed = await factoryObserver({
                factory: app.lifecycle, featureId: feature.id, executionId: execution.id, publication, item, workItem,
                ctx: h.context, agent: h.context.agent, assertCurrent: async () => { h.context.signal?.throwIfAborted(); },
            });
            if (scenario === "fallback") {
                assert.equal(await managed.effects.retryFailedBuilds!(null, service.snapshot(), { stageOnly: false, iteration: 0 }), true);
            }
            return object(await runPrObserver({
                ...h.context, step: managed.step, agent: managed.agent,
                args: { prUrl: String(object(publication.receipt, "PR").url), repository: item.repository,
                    options: { workspaceRoot: f.workspace, pauseOnBlocker: false, ciPollIntervalMs: 0, maxCiPollAttempts: 2,
                        publishStatus: Boolean(managed.effects.publishStatus) } },
            }, {
                repairHost: managed.repairHost, ...managed.effects, exec: () => "", fetchPrSnapshot: service.snapshot,
                trustedRepository: { ...service.repository, repository: item.repository, headSha: service.state.head, headBranch: "factory/test" },
            }), "Observer result");
        };
        if (scenario === "lost-stage") service.state.loseResponse = "build/builds/77/stages/Failed";
        if (scenario === "partial-retry") service.state.loseResponse = "build/builds/77/stages/Second";
        if (scenario === "lost-policy") service.state.loseResponse = `policy/evaluations/${service.state.policy.evaluationId}`;
        if (scenario === "lost-status") service.state.loseResponse = "git/repositories/authenticator/pullrequests/42";
        if (scenario === "uncertain") service.state.denyWrite = true;
        if (["lost-stage", "partial-retry", "lost-policy", "uncertain", "revoked", "cancelled"].includes(scenario)) {
            await assert.rejects(run);
            if (scenario === "lost-stage" || scenario === "partial-retry" || scenario === "lost-policy") {
                assert.equal((await run()).status, "ready-for-human-merge");
            } else if (scenario === "uncertain") {
                service.state.denyWrite = false;
                await assert.rejects(run, /uncertain|not confirmed/);
                assert.equal(service.state.writes.length, 0, "a negative receipt lookup cannot authorize another retry");
            } else assert.equal(service.state.writes.length, 0);
        } else {
            const result = await run();
            if (scenario === "legacy-effects") {
                assert.equal(result.blockerKind, "ci-infrastructure");
                assert.equal(service.state.writes.length, 0);
            } else if (scenario === "lost-status") {
                assert.equal(object(result.statusPublication, "Status publication").status, "failed");
                const operation = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.observer?.purpose === "status")!;
                const writes = service.state.writes.length;
                await app.lifecycle.reconcile(feature.id, operation.key);
                assert.equal(service.state.writes.length, writes, "reconciliation must never repost the status update");
                assert.equal(app.lifecycle.store.state(feature.id).operations.find((entry) => entry.key === operation.key)!.status, "completed");
            } else {
                assert.equal(result.status, "ready-for-human-merge");
                assert.equal(object(result.statusPublication, "Status publication").status, "updated");
                assert.match(service.state.body, /^Human description\. Fixes AB#1001/);
                assert.match(service.state.body, /ready-for-human-merge/);
                if (scenario === "repeat-head") {
                    service.state.passed = false;
                    service.state.stages[1]!.result = "failed";
                    const repeated = await run();
                    assert.equal(repeated.blockerKind, "ci-infrastructure");
                }
            }
        }
        const mutations = service.state.writes.filter((target) => !target.startsWith("git/"));
        assert.ok(mutations.length <= (scenario === "partial-retry" ? 2 : 1), "each failed stage is retried once across continuations");
        if (scenario === "fallback" || scenario === "stale") assert.match(mutations[0]!, /^policy\/evaluations\//);
        assert.equal(workers, 0);
        assert.equal(app.lifecycle.store.state(feature.id).operations.filter((entry) => entry.kind === "repair").length, 0);
    } finally { app.close(); f.cleanup(); }
});
}

for (const scenario of ["success", "lost-push", "pending-after-repair", "review-exhausted", "source-drift", "merge-conflict", "cancelled-review", "legacy-approval"] as const) {
test(`Factory uses the existing observer repair engine: ${scenario}`, async () => {
    const f = fixture();
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    const git = (cwd: string, ...args: string[]) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] }).trim();
    const remote = path.join(f.directory, "origin.git");
    const provider = Object.assign(new FakeProvider(), {
        repairs: 0,
        async prepareRepair(_repo: RepositoryTarget, input: RepairPublicationInput) {
            return git(path.resolve(f.workspace, input.localPath), "-c", "user.name=Factory Test", "-c", "user.email=factory@example.invalid",
                "commit-tree", input.source.tree, "-p", input.expectedHead, ...(input.mergeParent ? ["-p", input.mergeParent] : []), "-m", input.summary);
        },
        async publishRepair(_cfg: Configuration, item: PlannedItem, _wi: WorkItem, marker: string, original: LocalPublicationBinding,
            input: RepairPublicationInput, head: string, beforeSend: () => Promise<void>) {
            await beforeSend();
            const root = path.resolve(f.workspace, input.localPath);
            assert.equal(git(root, "rev-list", "--parents", "-n", "1", head), [head, input.expectedHead, ...(input.mergeParent ? [input.mergeParent] : [])].join(" "));
            if (scenario === "merge-conflict") assert.ok(input.mergeParent, "conflict repair must preserve the target merge parent");
            git(root, "push", remote, `${head}:refs/heads/${original.branch}`);
            this.repairs++;
            provider.prs.get(item.id)!.head = head;
            provider.prs.get(item.id)!.checks = scenario === "pending-after-repair" ? "pending" : "passing";
            const result = { ...object(provider.dispatches.get(marker), "Original receipt"), headSha: head };
            provider.dispatches.set(marker, result);
            if (scenario === "lost-push" && this.repairs === 1) throw new Error("Lost repair push response");
            return result;
        },
    });
    const app = new Application({ ...f.options, provider });
    try {
        const initialHead = initializeRepository(f.workspace);
        writeFileSync(path.join(f.workspace, ".git", "info", "exclude"), ".software-factory-repairs/\n");
        const initialIndex = git(f.workspace, "write-tree");
        git(f.workspace, "init", "--bare", "--quiet", remote);
        git(f.workspace, "remote", "add", "origin", remote);
        git(f.workspace, "push", "--quiet", "origin", "HEAD:refs/heads/dev");
        const { feature } = await approvedDispatchWave(app, "local", { ...plan(), items: [plan().items[0]!] });
        if (scenario === "legacy-approval") {
            app.lifecycle.store.change(feature.id, (state) => {
                const grant = state.decisions.findLast((decision) => decision.stage === "dispatch")!;
                const scope = { ...object(grant.scope, "Legacy dispatch scope") };
                delete scope.observer;
                scope.effect = "Publish the initial reviewed implementation. Observe the PR without repairs.";
                grant.scope = jsonValue(scope);
                grant.scopeHash = hash(scope);
            });
        }
        const initialPrepare = provider.prepareLocalPublication.bind(provider);
        provider.prepareLocalPublication = async (...args) => {
            const result = await initialPrepare(...args);
            return { ...result, headSha: git(f.workspace, "-c", "user.name=Factory Test", "-c", "user.email=factory@example.invalid",
                "commit-tree", result.source.tree, "-p", result.source.head, "-m", "Initial reviewed implementation") };
        };
        const initialPublish = provider.publishLocal.bind(provider);
        provider.publishLocal = async (...args) => {
            const receipt = await initialPublish(...args);
            git(f.workspace, "push", "--quiet", "origin", `${receipt.headSha}:refs/heads/${receipt.branch}`);
            provider.prs.get("A")!.checks = scenario === "merge-conflict" ? "passing" : "failing";
            if (scenario === "merge-conflict") {
                const baseRoot = path.join(f.directory, "base-update");
                git(f.workspace, "worktree", "add", "--detach", baseRoot, initialHead);
                writeFileSync(path.join(baseRoot, "A.kt"), "class A { fun call() = 3 }\n");
                git(baseRoot, "add", "A.kt");
                git(baseRoot, "-c", "user.name=Factory Test", "-c", "user.email=factory@example.invalid", "commit", "-m", "Advance target");
                git(baseRoot, "push", "--quiet", "origin", "HEAD:refs/heads/dev");
            }
            return receipt;
        };
        provider.findLocalPublication = async (_cfg, _item, _wi, marker, binding) => {
            const receipt = provider.dispatches.get(marker);
            if (receipt && object(receipt, "Receipt").headSha !== binding.headSha) {
                throw new FactoryError("publication_identity_changed", "Unexpected remote head", 409);
            }
            return receipt || null;
        };
        const execution = app.lifecycle.begin(feature.id, "implement", actor);
        const h = harness(app, execution);
        if (scenario === "cancelled-review") {
            const controller = new AbortController();
            h.context.signal = controller.signal;
            const step = h.context.step;
            let cancelled = false;
            h.context.step = async (key, producer, options) => {
                const result = await step(key, producer, options);
                if (!cancelled && /-review-repair-0$/.test(key)) { cancelled = true; controller.abort(); }
                return result;
            };
        }
        let builders = 0;
        let repairBuilders = 0;
        let repairReviewers = 0;
        h.context.agent = async (prompt, options) => {
            const label = options?.label || "";
            if (label === "Factory:coder:A:round-1") {
                builders++;
                return { summary: "Implement A", files: [{ path: "A.kt", action: "write", content: "class A { fun call() = 1 }\n" }],
                    testsAddedOrUpdated: ["A compatibility test"], blockingUnknowns: [] };
            }
            if (label === "Factory:code-reviewer:A:round-1") return {
                pass: true, summary: "Contract retained.", findings: [],
                criteria: [{ criterion: "A preserves the response contract.", status: "addressed", evidence: "A.kt inspected." }],
            };
            if (label.startsWith("Observe PR")) return {
                verdict: "repair", repairKind: scenario === "merge-conflict" ? "merge-conflict" : "ci-failure", summary: "A.kt needs a tactical correction.",
                actionableFindings: ["Fix A.kt compile error."], evidence: ["CI compile failure in A.kt"],
            };
            if (label.startsWith("Tactical repair")) {
                repairBuilders++;
                assert.match(prompt, /read-only structured repair author/);
                return { verdict: "pass", summary: "Fix A.kt compilation", evidence: ["A.kt compile error corrected"],
                    files: [{ path: "A.kt", action: "write", content: "class A { fun call() = 2 }\n" }] };
            }
            if (label.startsWith("Verify tactical repair")) {
                repairReviewers++;
                assert.match(prompt, /sourceBinding/);
                assert.match(prompt, /fun call\(\) = 2/);
                if (scenario === "source-drift") {
                    const operation = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.kind === "repair")!;
                    writeFileSync(path.join(f.workspace, ".software-factory-repairs", operation.repair!.publicationKey, "0", "A.kt"), "// concurrent edit\n");
                }
                return { verdict: scenario === "review-exhausted" ? "repair" : "pass", summary: "Reviewed actual repair.",
                    evidence: ["A.kt source diff"], findings: scenario === "review-exhausted" ? ["A.kt still violates the response contract"] : [] };
            }
            throw new Error(`Unexpected agent ${label}`);
        };
        useCodeFeatureFixtures(h);
        const dependencies = { provider, observerSnapshot: async (): Promise<Json> => {
            const pr = provider.prs.get("A")!;
            const publication = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.kind === "dispatch")!;
            return {
                number: pr.number, url: pr.url, state: "OPEN", title: pr.title, headRefOid: pr.head,
                body: provider.prBodies.get(pr.number) || "Human-authored PR description.",
                headRefName: publication.publicationBinding!.branch, baseRefName: "dev",
                mergeStateStatus: scenario === "merge-conflict" && !provider.repairs ? "DIRTY" : "CLEAN",
                reviewThreadsComplete: true, unresolvedReviewThreadCount: 0, inlineReviewComments: [],
                statusCheckRollup: [{ name: "compileKotlin", state: pr.checks === "passing" ? "SUCCESS" : pr.checks === "pending" ? "PENDING" : "FAILURE" }],
                prBuilds: pr.checks === "failing" ? [{ id: 1, status: "completed", result: "failed", sourceVersion: pr.head,
                    failedTasks: [{ name: "compileKotlin", issues: ["A.kt:1 unresolved reference"], excerpt: ["e: A.kt:1 unresolved reference: response"] }] }] : [],
            };
        } };
        const run = () => finished(app, feature.id, execution, () => implementRun(h.context, dependencies));
        if (scenario === "lost-push" || scenario === "cancelled-review") {
            await assert.rejects(run, scenario === "lost-push" ? /Lost repair push response/ : /abort/i);
            assert.equal(provider.repairs, scenario === "lost-push" ? 1 : 0);
            h.context.signal = new AbortController().signal;
            const result = object(await run(), "Reconciled observer");
            assert.equal(result.outcome, "implementation_reviewed");
            assert.equal(repairBuilders, 1, "reconciliation must not rebuild a pushed repair");
            assert.equal(repairReviewers, 1, "review replay must reuse its exact-source result");
        } else if (scenario === "review-exhausted") {
            await assert.rejects(run, (error: unknown) => error instanceof FactoryError && error.code === "pr_observer_blocked");
            assert.equal(repairBuilders, 2);
            assert.equal(provider.repairs, 0, "failed independent reviews must never push");
            app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "completed" });
            const successor = app.lifecycle.begin(feature.id, "implement", actor);
            const next = { ...h.context, args: { featureId: feature.id, executionId: successor.id }, runId: randomUUID() };
            await assert.rejects(() => implementRun(next, dependencies), (error: unknown) => error instanceof FactoryError && error.code === "pr_observer_blocked");
            assert.equal(repairBuilders, 2, "a successor cannot reset the original two-repair allowance");
        } else if (scenario === "legacy-approval") {
            await assert.rejects(run, (error: unknown) => error instanceof FactoryError && error.code === "pr_observer_blocked");
            assert.equal(repairBuilders, 0, "historical approval cannot gain repair authority");
            assert.equal(provider.repairs, 0);
        } else if (scenario === "source-drift") {
            await assert.rejects(run, /read-only observer worker changed source|repair checkout changed/);
            assert.equal(provider.repairs, 0);
        } else {
            const result = object(await run(), "Observer result");
            assert.equal(result.outcome, scenario === "pending-after-repair" ? "implementation_pending" : "implementation_reviewed");
            if (scenario === "pending-after-repair") {
                provider.prs.get("A")!.checks = "passing";
                const successor = app.lifecycle.begin(feature.id, "implement", actor);
                const next = { ...h.context, args: { featureId: feature.id, executionId: successor.id }, runId: randomUUID() };
                assert.equal(object(await implementRun(next, dependencies), "Continuation").outcome, "implementation_reviewed");
            }
            assert.equal(repairBuilders, 1);
            assert.equal(repairReviewers, 1);
            assert.equal(provider.repairs, 1);
        }
        assert.equal(builders, 1, "the original PBI is never redispatched");
        assert.equal(provider.localPublishes, 1);
        if (provider.repairs) {
            const state = app.lifecycle.store.state(feature.id);
            const publication = state.operations.find((operation) => operation.kind === "dispatch")!;
            assert.equal(object(publication.receipt, "Original publication").headSha, publication.publicationBinding!.headSha);
            assert.notEqual(provider.prs.get("A")!.head, publication.publicationBinding!.headSha);
            const actualHead = provider.prs.get("A")!.head;
            const receipt = provider.dispatches.get(`software-factory-${publication.key}`)!;
            provider.dispatches.set(`software-factory-${publication.key}`, { ...object(receipt, "Receipt"), headSha: "f".repeat(40) });
            await assert.rejects(() => app.lifecycle.verifyRecordedLocalPublication(feature.id, plan().items[0]!,
                [...provider.items.values()].find((item) => item.key === "A")!, publication), /Unexpected remote head/);
            provider.dispatches.set(`software-factory-${publication.key}`, receipt);
            assert.equal(provider.prs.get("A")!.head, actualHead);
        }
        assert.equal(git(f.workspace, "rev-parse", "HEAD"), initialHead);
        assert.equal(git(f.workspace, "write-tree"), initialIndex);
        assert.equal(readFileSync(path.join(f.workspace, "A.kt"), "utf8"), "// baseline\n");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});
}

test("an uncertain local branch creation never restarts the builder or republishes", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        initializeRepository(f.workspace);
        const { feature } = await approvedDispatchWave(app, "local");
        let branchCreated = false;
        provider.publishLocal = async () => {
            provider.localPublishes++;
            branchCreated = true;
            throw new Error("Connection lost after branch creation.");
        };
        const execution = app.lifecycle.begin(feature.id, "implement", actor);
        const h = harness(app, execution);
        h.context.agent = async (_prompt, options) => {
            const label = options?.label || "";
            h.labels.push(label);
            if (label === "Factory:coder:A:round-1") return {
                summary: "Implement A.",
                files: [{ path: "A.kt", action: "write", content: "class A\n" }],
                testsAddedOrUpdated: ["ATest.kt"], blockingUnknowns: [],
            };
            if (label === "Factory:code-reviewer:A:round-1") return {
                pass: true, summary: "Reviewed A.",
                criteria: [{ criterion: "A preserves the response contract.", status: "addressed", evidence: "A.kt verified." }],
                findings: [],
            };
            throw new Error(`Unexpected label: ${label}`);
        };

        useCodeFeatureFixtures(h);
        await assert.rejects(() => implementRun(h.context, { provider }), /Connection lost after branch creation/);
        const operation = app.lifecycle.store.state(feature.id).operations.find((entry) => entry.kind === "dispatch" && entry.itemId === "A")!;
        assert.equal(operation.status, "reconciliation_required");
        assert.equal(branchCreated, true);
        await assert.rejects(() => app.lifecycle.reconcile(feature.id, operation.key),
            (error: unknown) => error instanceof FactoryError && error.code === "reconciliation_required");
        await assert.rejects(() => implementRun(h.context, { provider }),
            (error: unknown) => error instanceof FactoryError && error.code === "reconciliation_required");
        assert.equal(provider.localPreparations, 1);
        assert.equal(provider.localPublishes, 1);
        assert.equal(h.labels.filter((label) => label.startsWith("Factory:coder:")).length, 1);
        assert.equal(h.labels.filter((label) => label.startsWith("Factory:code-reviewer:")).length, 1);
        assert.equal(readFileSync(path.join(f.workspace, "A.kt"), "utf8"), "// baseline\n");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("Stage 3 configurable 2-round limit fails closed and restores local files when findings remain unresolved", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        initializeRepository(f.workspace, "// original baseline\n");
        app.lifecycle.configure({ ...config(), delivery: { mode: "local", maxReviewRounds: 2 } }, actor);
        const feature = app.execute({ kind: "create_feature", requestId: "two-round-cap", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, { summary: "Implement A.", items: [plan().items[0]!], blockingUnknowns: [] }, design.id);
        approve(app, feature.id, "plan");
        approve(app, feature.id, "backlog");
        await app.lifecycle.backlog(feature.id);
        approve(app, feature.id, "dispatch");

        const implementExec = app.lifecycle.begin(feature.id, "implement", actor, {}, {}, undefined, undefined, { mode: "local", maxReviewRounds: 2 });
        const h = harness(app, implementExec);
        h.context.agent = async (_prompt, options) => {
            const label = options?.label || "";
            if (label.startsWith("Factory:coder:A:")) {
                return {
                    summary: "Attempted implementation.",
                    files: [{ path: "A.kt", action: "write", content: "// incomplete implementation\n" }],
                    testsAddedOrUpdated: ["ATest.kt"],
                    blockingUnknowns: [],
                };
            }
            if (label.startsWith("Factory:code-reviewer:A:")) {
                return {
                    pass: false,
                    summary: "Still missing required regression test.",
                    criteria: [{ criterion: "A preserves the response contract.", status: "partial", evidence: "Regression test is missing." }],
                    findings: [{
                        id: "F-TEST", severity: "blocker", category: "tests",
                        file: "ATest.kt", location: "suite",
                        evidence: "No assertion for legacy error code.",
                        requiredChange: "Add legacy error code assertion.",
                    }],
                };
            }
            throw new Error(`Unexpected label: ${label}`);
        };

        useCodeFeatureFixtures(h);
        await assert.rejects(() => implementRun(h.context, { provider }),
            (error: unknown) => error instanceof FactoryError && error.code === "implementation_review_no_go");
        assert.equal(provider.localPublishes, 0, "NO-GO must never push a branch or open a PR");
        assert.equal(readFileSync(`${f.workspace}/A.kt`, "utf8"), "// original baseline\n", "failed local loop must restore original working tree file");
        const attempt = app.lifecycle.store.latest(feature.id, "implementation_attempt");
        assert.ok(attempt);
        assert.equal(object(attempt.content, "Attempt").recommendation, "NO-GO");
        assert.equal(object(attempt.content, "Attempt").maxRounds, 2);
        assert.equal(object(attempt.content, "Attempt").roundsCompleted, 2);
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("Stage 3 remote PR mode runs the independent reviewer loop and posts feedback to the remote builder across rounds", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        app.lifecycle.configure({ ...config(), delivery: { mode: "remote", maxReviewRounds: 3 } }, actor);
        const feature = app.execute({ kind: "create_feature", requestId: "remote-loop", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, { summary: "Implement A remotely.", items: [plan().items[0]!], blockingUnknowns: [] }, design.id);
        approve(app, feature.id, "plan");
        approve(app, feature.id, "backlog");
        await app.lifecycle.backlog(feature.id);
        approve(app, feature.id, "dispatch");

        const dispatchCommand = { kind: "dispatch_work", featureId: feature.id, mode: "remote" };
        const dispatchPreview = app.prepare(dispatchCommand);
        assert.match(dispatchPreview.confirmation!, /does not start independent PR review/i);
        await app.dispatch(dispatchCommand, actor, { granted: true, revisionId: dispatchPreview.consentKey!, actor });
        assert.equal(provider.sends, 1);
        assert.equal(provider.comments, 0, "remote dispatch alone must not post review feedback");
        assert.equal(app.lifecycle.store.state(feature.id).executions.length, 0, "remote dispatch alone must not start a review workflow");
        const gates = () => (["design", "plan", "backlog", "dispatch", "complete"] as const)
            .map((stage) => ({ available: true, ...app.lifecycle.gate(feature.id, stage) }));
        const next = nextStep(app.lifecycle.store.view(feature.id), gates());
        assert.deepEqual(next.action?.command, { kind: "start_workflow", featureId: feature.id, workflow: "implement", mode: "remote" });
        assert.match(next.description, /confirm|approval/i);
        const reviewPreview = app.prepare(next.action!.command!);
        assert.match(reviewPreview.confirmation!, /post feedback/i);
        assert.match(reviewPreview.confirmation!, /may trigger further coding-agent work/i);

        provider.onFeedbackPosted = () => {
            const pr = provider.prs.get("A");
            if (pr) pr.head = "sha-A-round-2";
        };

        const implementExec = app.lifecycle.begin(feature.id, "implement", actor, {}, {}, undefined, undefined, { mode: "remote", maxReviewRounds: 3 });
        const h = harness(app, implementExec);
        h.context.agent = async (_prompt, options) => {
            const label = options?.label || "";
            if (label === "Factory:code-reviewer:A:round-1") {
                return {
                    pass: false,
                    summary: "Remote PR needs thread-safety fix.",
                    criteria: [{ criterion: "A preserves the response contract.", status: "partial", evidence: "Shared map is unsynchronized." }],
                    findings: [{
                        id: "F-SYNC", severity: "major", category: "concurrency",
                        file: "A.kt", location: "cache",
                        evidence: "HashMap mutated concurrently.",
                        requiredChange: "Use ConcurrentHashMap.",
                    }],
                };
            }
            if (label === "Factory:code-reviewer:A:round-2") {
                return {
                    pass: true,
                    summary: "Remote PR fixed concurrency issue.",
                    criteria: [{ criterion: "A preserves the response contract.", status: "addressed", evidence: "ConcurrentHashMap used and tested." }],
                    findings: [],
                };
            }
            throw new Error(`Unexpected label: ${label}`);
        };

        const result = object(await finished(app, feature.id, implementExec, () => implementRun(h.context, { provider })), "Remote implement result");
        assert.equal(result.outcome, "implementation_reviewed");
        assert.equal(result.mode, "remote");
        assert.equal(provider.sends, 1, "remote mode must dispatch to the remote coding agent once");
        assert.equal(provider.comments, 1, "remote mode must post round-1 reviewer findings back to the PR");
        const items = result.items as Record<string, unknown>[];
        assert.equal(items[0]?.status, "review_passed");
        assert.equal(items[0]?.roundsCompleted, 2);
        assert.equal(nextStep(app.lifecycle.store.view(feature.id), gates()).action?.command?.workflow, "monitor");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("remote review remains pending while awaiting a PR or updated head and preserves the bounded review history", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        app.lifecycle.configure({ ...config(), delivery: { mode: "remote", maxReviewRounds: 2 } }, actor);
        const feature = app.execute({ kind: "create_feature", requestId: "remote-pending", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, { summary: "Implement A remotely.", items: [plan().items[0]!], blockingUnknowns: [] }, design.id);
        approve(app, feature.id, "plan");
        approve(app, feature.id, "backlog");
        await app.lifecycle.backlog(feature.id);
        approve(app, feature.id, "dispatch");
        await app.lifecycle.dispatch(feature.id);
        const pr = provider.prs.get("A")!;
        provider.prs.delete("A");

        const runReview = async (review?: Json) => {
            const execution = app.lifecycle.begin(feature.id, "implement", actor, {}, {}, undefined, undefined, { mode: "remote", maxReviewRounds: 2 });
            const h = harness(app, execution);
            const labels: string[] = [];
            h.context.agent = async (_prompt, options) => {
                labels.push(options?.label || "");
                if (!review) throw new Error("No independent review should run for an unchanged or missing PR.");
                return review;
            };
            const result = object(await finished(app, feature.id, execution, () => implementRun(h.context, { provider })), "Remote review result");
            return { execution, result, item: (result.items as Record<string, unknown>[])[0]!, labels };
        };
        const waitingForPr = await runReview();
        assert.equal(waitingForPr.result.outcome, "implementation_pending");
        assert.equal(waitingForPr.item.status, "dispatched_awaiting_pr");
        assert.match(app.lifecycle.store.execution(feature.id, waitingForPr.execution.id).phase, /await|waiting/i);
        assert.equal(waitingForPr.labels.length, 0);

        provider.prs.set("A", pr);
        const feedback = await runReview({
            pass: true, summary: "The test is missing.",
            criteria: [{ criterion: "A preserves the response contract.", status: "partial", evidence: "Regression test missing." }],
            findings: [{ id: "F-TEST", severity: "major", category: "tests", file: "ATest.kt", location: "suite",
                evidence: "The compatibility case is missing.", requiredChange: "Add the missing test." }],
        });
        assert.equal(feedback.result.outcome, "implementation_pending");
        assert.equal(feedback.item.status, "feedback_posted_awaiting_builder");
        assert.equal(feedback.item.roundsCompleted, 1);
        assert.ok(feedback.item.feedbackReceipt);
        assert.equal(provider.comments, 1);

        const unchanged = await runReview();
        assert.equal(unchanged.result.outcome, "implementation_pending");
        assert.equal(unchanged.item.status, "feedback_posted_awaiting_builder");
        assert.equal(unchanged.item.roundsCompleted, 1);
        assert.deepEqual(unchanged.item.feedbackReceipt, feedback.item.feedbackReceipt);
        assert.deepEqual(unchanged.item.reviewHistory, feedback.item.reviewHistory);
        assert.equal(provider.comments, 1, "an unchanged PR must not receive duplicate feedback");

        pr.head = "sha-A-round-2";
        const passed = await runReview({
            pass: true, summary: "The regression test was added.",
            criteria: [{ criterion: "A preserves the response contract.", status: "addressed", evidence: "Regression test added." }],
            findings: [],
        });
        assert.equal(passed.result.outcome, "implementation_reviewed");
        assert.equal(passed.item.status, "review_passed");
        assert.equal(passed.item.roundsCompleted, 2);
        const unchangedPass = await runReview();
        assert.equal(unchangedPass.result.outcome, "implementation_reviewed");
        assert.equal(unchangedPass.item.status, "review_passed");
        assert.equal(unchangedPass.item.prNumber, pr.number);
        assert.deepEqual(unchangedPass.item.reviewHistory, passed.item.reviewHistory);

        provider.prs.set("A", { ...pr, number: pr.number + 1, url: `${pr.url}-replacement` });
        const replacedPr = await runReview();
        assert.equal(replacedPr.result.outcome, "implementation_pending");
        assert.equal(replacedPr.item.status, "review_limit_reached", "a different PR is not reviewed even when its head matches");
        assert.match(app.lifecycle.store.execution(feature.id, replacedPr.execution.id).phase, /limit|attention/i);
        assert.equal(nextStep(app.lifecycle.store.view(feature.id), []).action?.target, "runs");
        pr.head = "sha-A-after-limit";
        provider.prs.set("A", pr);
        const newHead = await runReview();
        assert.equal(newHead.result.outcome, "implementation_pending");
        assert.equal(newHead.item.status, "review_limit_reached", "a new head is not covered by the earlier review");
        assert.deepEqual(newHead.item.reviewHistory, passed.item.reviewHistory);
        assert.equal(provider.sends, 1, "successor reviews must not redispatch");
        assert.equal(provider.comments, 1);
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("a remote wave stays pending until every dispatched item has a reviewed PR", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        const { feature } = await approvedDispatchWave(app, "remote");
        await app.lifecycle.dispatch(feature.id);
        const laterPr = provider.prs.get("B")!;
        provider.prs.delete("B");
        const labels: string[] = [];
        const runReview = async () => {
            const execution = app.lifecycle.begin(feature.id, "implement", actor, {}, {}, undefined, undefined, { mode: "remote", maxReviewRounds: 3 });
            const h = harness(app, execution);
            h.context.agent = async (_prompt, options) => {
                const label = options?.label || "";
                labels.push(label);
                const itemId = label.includes(":B:") ? "B" : "A";
                return {
                    pass: true, summary: "The PR passed review.",
                    criteria: [{ criterion: `${itemId} preserves the response contract.`, status: "addressed", evidence: "Verified." }],
                    findings: [],
                };
            };
            return object(await finished(app, feature.id, execution, () => implementRun(h.context, { provider })), "Remote wave result");
        };
        const first = await runReview();
        assert.equal(first.outcome, "implementation_pending");
        assert.deepEqual((first.items as Record<string, unknown>[]).map((item) => item.status), ["review_passed", "dispatched_awaiting_pr"]);
        assert.equal(nextStep(app.lifecycle.store.view(feature.id), []).action?.command?.workflow, "implement");

        provider.prs.set("B", laterPr);
        const second = await runReview();
        assert.equal(second.outcome, "implementation_reviewed");
        assert.deepEqual((second.items as Record<string, unknown>[]).map((item) => item.status), ["review_passed", "review_passed"]);
        assert.deepEqual(labels, ["Factory:code-reviewer:A:round-1", "Factory:code-reviewer:B:round-1"]);
        assert.equal(provider.sends, 2, "reviewing the wave again must reuse both dispatches");
        assert.equal(nextStep(app.lifecycle.store.view(feature.id), []).action?.command?.workflow, "monitor");
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("remote review cannot report a changed PR head as reviewed", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        const { feature } = await approvedDispatchWave(app, "remote");
        await app.lifecycle.dispatch(feature.id);
        const execution = app.lifecycle.begin(feature.id, "implement", actor, {}, {}, undefined, undefined, { mode: "remote", maxReviewRounds: 3 });
        const h = harness(app, execution);
        h.context.agent = async (_prompt, options) => {
            const itemId = options?.label?.includes(":B:") ? "B" : "A";
            if (itemId === "A") provider.prs.get("A")!.head = "updated-during-review";
            return {
                pass: true, summary: "The reviewed revision passed.",
                criteria: [{ criterion: `${itemId} preserves the response contract.`, status: "addressed", evidence: "Verified." }],
                findings: [],
            };
        };
        await assert.rejects(() => implementRun(h.context, { provider }),
            (error: unknown) => error instanceof FactoryError && error.code === "stale_pr");
        assert.equal(app.lifecycle.store.latest(feature.id, "implementation"), null);
        assert.equal(provider.comments, 0);

        app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "error", error: "The reviewed head changed." });
        provider.prs.get("A")!.baseBranch = "wrong-base";
        const wrongTargetExecution = app.lifecycle.begin(feature.id, "implement", actor, {}, {}, undefined, undefined, { mode: "remote", maxReviewRounds: 3 });
        const wrongTarget = harness(app, wrongTargetExecution);
        wrongTarget.context.agent = async () => { throw new Error("A mismatched PR must not be sent to a reviewer."); };
        await assert.rejects(() => implementRun(wrongTarget.context, { provider }),
            (error: unknown) => error instanceof FactoryError && error.code === "stale_pr");
        assert.equal(app.lifecycle.store.latest(feature.id, "implementation"), null);
        assert.equal(provider.comments, 0);
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("local implementation checks live remote base readiness before spending paid coding subagent turns", async () => {
    const f = fixture();
    let readinessChecked = 0;
    const provider = Object.assign(new FakeProvider(), {
        async verifyLocalReadiness(_repo: RepositoryTarget, expectedBase: string) {
            readinessChecked++;
            throw new FactoryError("local_base_changed", `Remote dev moved ahead of local ${expectedBase.slice(0, 12)}.`, 409);
        },
    });
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        initializeRepository(f.workspace);
        const { feature } = await approvedDispatchWave(app, "local");
        const execution = app.lifecycle.begin(feature.id, "implement", actor);
        const h = harness(app, execution);
        let agentCalls = 0;
        h.context.agent = async () => {
            agentCalls++;
            throw new Error("Paid coding subagents must not start when remote base readiness fails.");
        };
        useCodeFeatureFixtures(h);
        await assert.rejects(() => finished(app, feature.id, execution, () => implementRun(h.context, { provider })),
            (error: unknown) => error instanceof FactoryError && error.code === "local_base_changed");
        assert.equal(readinessChecked, 1);
        assert.equal(agentCalls, 0);
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});

test("planRun distinguishes target repositories from read-only referenceRepositories in prompts and replays journaled steps on resume", async () => {
    const f = fixture();
    const provider = new FakeProvider();
    const app = new Application({ ...f.options, provider });
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        app.lifecycle.configure({
            ...config(),
            referenceRepositories: {
                msal: { provider: "github", hostname: "github.com", slug: "example/msal", baseBranch: "dev", account: "reader", localPath: "." },
            },
        }, actor);
        const feature = app.execute({ kind: "create_feature", requestId: "plan-ref-journal", input: requirements() }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        approve(app, feature.id, "design");

        const execution = app.lifecycle.begin(feature.id, "plan", actor);
        const journal = new Map<string, Json>();
        let plannerCalls = 0;
        let reviewerCalls = 0;
        let failReviewOnce = true;

        const h = harness(app, execution);
        h.context.step = async (key, producer) => {
            if (journal.has(key)) return journal.get(key)!;
            const value = await producer();
            journal.set(key, value);
            return value;
        };
        h.context.agent = async (prompt, options) => {
            assert.match(prompt, /referenceRepositories are read-only evidence sources/);
            assert.match(prompt, /"allowedTargetRepositories":\["common"\]/);
            if (options?.label?.startsWith("Factory:planner:")) {
                plannerCalls++;
                return { ...plan(), items: [plan().items[0]!] };
            }
            if (options?.label?.startsWith("Factory:plan-review:")) {
                reviewerCalls++;
                if (failReviewOnce) {
                    failReviewOnce = false;
                    throw new Error("Simulated transient network interruption before review step journaled");
                }
                return { pass: true, findings: [] };
            }
            throw new Error(`Unexpected label ${options?.label}`);
        };
        const runAttempt = () => planRun(h.context, { provider });

        await assert.rejects(runAttempt, /Simulated transient network interruption/);
        assert.equal(plannerCalls, 1);
        assert.equal(reviewerCalls, 1);

        const completed = object(await finished(app, feature.id, execution, runAttempt), "Plan result");
        assert.equal(completed.outcome, "plan_ready_for_review");
        assert.equal(plannerCalls, 1, "journaled planner step must not rerun on resume");
        assert.equal(reviewerCalls, 2);
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        app.close(); f.cleanup();
    }
});
