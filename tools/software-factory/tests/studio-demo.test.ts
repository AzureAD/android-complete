// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { Application } from "../core/application.ts";
import {
    computeFeatureScorecard, computeWorkspaceAnalytics, dedupeExecutions, findSupersededLocalDraftIds, nanoToCredits,
} from "../core/analytics.ts";
import type { FactoryError } from "../core/contracts.ts";
import {
    buildDiffHunks, buildSplitRows, diffDesignSnapshots, diffPlanSnapshots, diffRequirementsRevisions, diffText, diffWords,
} from "../core/diff.ts";
import type { Artifact, Configuration, Execution, FactoryFeature, Json, LocalPublicationBinding, ObserverEffectPlan, ObserverMutation, Plan, PlannedItem, PullRequest, RepositoryTarget, WorkItem } from "../core/lifecycle-contracts.ts";
import type { LocalImplementationInput, ObserverEffectRequest, Provider } from "../providers/provider.ts";
import { normalizeGitRemote, resolveWorktreeInfo } from "../core/runtime-health.ts";
import { DEFAULT_STUDIO_PORT, startServer } from "../server/server.ts";
import type { QaResponder } from "../server/qa.ts";
import { actor, transportFixture, requirements } from "./helpers.ts";

test("AB#3822859: normalizeGitRemote unifies HTTPS and SSH remotes and unifies legacy split worktree rows with provenance", () => {
    assert.equal(
        normalizeGitRemote("https://github.com/AzureAD/android-complete.git"),
        "github.com/azuread/android-complete",
    );
    assert.equal(
        normalizeGitRemote("git@github.com:AzureAD/android-complete.git/"),
        "github.com/azuread/android-complete",
    );
    assert.equal(DEFAULT_STUDIO_PORT, 49629);

    const root = mkdtempSync(path.join(os.tmpdir(), "sf-wt-demo-"));
    try {
        const dbPath = path.join(root, "registry.sqlite");
        const repoA = path.join(root, "clone-a");
        const repoB = path.join(root, "clone-b");
        const unrelatedRepo = path.join(root, "unrelated-repo");
        mkdirSync(repoA, { recursive: true });
        mkdirSync(repoB, { recursive: true });
        mkdirSync(unrelatedRepo, { recursive: true });

        execFileSync("git", ["init", "--quiet", "-b", "feature/alpha", repoA]);
        execFileSync("git", ["-C", repoA, "remote", "add", "origin", "https://github.com/AzureAD/android-complete.git"]);
        execFileSync("git", ["init", "--quiet", "-b", "feature/beta", repoB]);
        execFileSync("git", ["-C", repoB, "remote", "add", "origin", "git@github.com:AzureAD/android-complete.git"]);
        execFileSync("git", ["init", "--quiet", "-b", "main", unrelatedRepo]);
        execFileSync("git", ["-C", unrelatedRepo, "remote", "add", "origin", "https://github.com/AzureAD/other-repo.git"]);

        const infoA = resolveWorktreeInfo(repoA);
        assert.equal(infoA.branch, "feature/alpha");
        assert.equal(infoA.remoteOrigin, "github.com/azuread/android-complete");

        const appA = new Application({ database: dbPath, workspace: repoA });
        const appB = new Application({ database: dbPath, workspace: repoB });
        const appOther = new Application({ database: dbPath, workspace: unrelatedRepo });
        try {
            const createdInA = appA.registry.create("wt-req-1", requirements(), actor);
            // Clone B shares normalized remote origin, so it discovers Clone A's features & provenance seamlessly.
            assert.equal(appB.registry.list().length, 1);
            assert.equal(appB.registry.get(createdInA.id).id, createdInA.id);
            const prov = appB.registry.provenance(createdInA.id);
            assert.ok(prov);
            assert.equal(prov.originBranch, "feature/alpha");
            assert.equal(prov.originWorktree, "clone-a");

            // Revising from Clone B updates updatedBranch / updatedWorktree while preserving originWorktree.
            appB.registry.revise(
                createdInA.id,
                createdInA.current.id,
                { ...requirements(), title: "Revised from clone B" },
                actor,
            );
            const updatedProv = appA.registry.provenance(createdInA.id);
            assert.ok(updatedProv);
            assert.equal(updatedProv.originWorktree, "clone-a");
            assert.equal(updatedProv.updatedWorktree, "clone-b");
            assert.equal(updatedProv.updatedBranch, "feature/beta");

            // Unrelated repository remains strictly isolated.
            assert.equal(appOther.registry.list().length, 0);
        } finally {
            appA.close();
            appB.close();
            appOther.close();
        }
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

class MinimalBoardProvider implements Provider {
    items = new Map<string, WorkItem>();
    prs = new Map<string, PullRequest>();
    async createWorkItem(_config: Configuration, item: PlannedItem, marker: string): Promise<WorkItem> {
        const wi: WorkItem = { key: item.id, adoId: 5001, url: "https://dev.azure.com/example/Engineering/_workitems/edit/5001", title: item.title, repository: item.repository, status: "New" };
        this.items.set(marker, wi);
        return wi;
    }
    async findWorkItem(_config: Configuration, marker: string): Promise<WorkItem | null> { return this.items.get(marker) || null; }
    async linkWorkItem(): Promise<Json> { return { linked: true }; }
    async inspectWorkItem(_config: Configuration, item: WorkItem): Promise<WorkItem> { return item; }
    async dispatch(): Promise<Json> { return { submitted: true }; }
    async findDispatch(): Promise<Json | null> { return null; }
    async prepareLocalPublication(config: Configuration, item: PlannedItem, _workItem: WorkItem, _marker: string, implementation: LocalImplementationInput): Promise<LocalPublicationBinding> {
        const repo = config.repositories[item.repository]!;
        return {
            branch: implementation.branch, headSha: implementation.sourceBinding.tree, source: implementation.sourceBinding,
            target: { provider: repo.provider, hostname: repo.hostname, slug: repo.slug, baseBranch: repo.baseBranch, publisherId: "1", publisherAccount: repo.account },
        };
    }
    async publishLocal(): Promise<Json> { return { submitted: true }; }
    async findLocalPublication(): Promise<Json | null> { return null; }
    async inspectPr(_repo: RepositoryTarget, number: number): Promise<PullRequest> { return [...this.prs.values()].find((p) => p.number === number)!; }
    async findPr(_config: Configuration, item: PlannedItem): Promise<PullRequest | null> { return this.prs.get(item.id) || null; }
    async diff(): Promise<string> { return ""; }
    async preflight(): Promise<Json> { return { verified: true }; }
    async findFeedback(): Promise<Json | null> { return null; }
    async postFeedback(): Promise<Json> { return { posted: true }; }
    async markDispatched(_config: Configuration, item: WorkItem): Promise<Json> { return { adoId: item.adoId, state: "Active" }; }
    async review(): Promise<Json> { return { body: "", comments: [], reviewThreadsComplete: true, unresolvedReviewThreadCount: 0, activeReviewThreads: [] }; }
    async prepareObserverEffect(_repo: RepositoryTarget, _number: number, _snapshot: Json, _request: ObserverEffectRequest): Promise<ObserverEffectPlan> { return { mutations: [], result: null }; }
    async sendObserverMutation(_repo: RepositoryTarget, _mutation: ObserverMutation, _publisher: LocalPublicationBinding["target"], beforeSend: () => Promise<void>): Promise<Json> { await beforeSend(); return { accepted: true }; }
    async findObserverMutation(): Promise<Json | null> { return null; }
}

test("AB#3822859: cross_worktree_checkout_conflict blocks local implement when sibling worktree has unpushed implementation_attempt", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "sf-wt-conflict-"));
    try {
        const dbPath = path.join(root, "registry.sqlite");
        const repoA = path.join(root, "wt-a");
        const repoB = path.join(root, "wt-b");
        mkdirSync(repoA, { recursive: true });
        mkdirSync(repoB, { recursive: true });
        execFileSync("git", ["init", "--quiet", "-b", "branch-a", repoA]);
        execFileSync("git", ["-C", repoA, "remote", "add", "origin", "https://github.com/AzureAD/android-complete.git"]);
        execFileSync("git", ["init", "--quiet", "-b", "branch-b", repoB]);
        execFileSync("git", ["-C", repoB, "remote", "add", "origin", "https://github.com/AzureAD/android-complete.git"]);

        const provider = new MinimalBoardProvider();
        const appA = new Application({ database: dbPath, workspace: repoA, provider });
        const appB = new Application({ database: dbPath, workspace: repoB, provider });
        try {
            const cfg: Configuration = {
                repositories: { common: { provider: "github", hostname: "github.com", slug: "AzureAD/common", baseBranch: "dev", account: "shahzaibj", localPath: "." } },
                boards: { organization: "IdentityDivision", project: "Engineering", workItemType: "Product Backlog Item", areaPath: "Engineering\\Android", iterationPath: "Engineering\\Current", assignee: "", parentId: null },
                delivery: { mode: "local", maxReviewRounds: 3 },
            };
            appA.lifecycle.configure(cfg, actor);
            const created = appA.execute({ kind: "create_feature", requestId: "conflict-req", input: requirements() }, actor);
            appA.execute(
                { kind: "approve_requirements", featureId: created.id, expectedRevision: created.current.id },
                actor,
                { granted: true, revisionId: created.current.id, actor },
            );
            const design = appA.lifecycle.store.artifact(created.id, "design", created.current.id, { recommendation: "GO to Planning" });
            const approveStage = (stage: "design" | "plan" | "backlog" | "dispatch") => {
                const g = appA.lifecycle.gate(created.id, stage);
                return appA.lifecycle.decide(created.id, stage, g.scopeHash, true, { ...actor, reference: randomUUID() });
            };
            approveStage("design");
            const planned: Plan = {
                summary: "Implement PBI-1",
                items: [{
                    id: "PBI-1", title: "Add ThrottleCache", repository: "common", description: "Spec",
                    requirementIds: ["R1"], acceptanceCriteria: ["Honor Retry-After"], dependsOn: [], files: ["ThrottleCache.kt"], tests: ["ThrottleCacheTest"],
                }],
                blockingUnknowns: [],
            };
            appA.lifecycle.store.artifact(created.id, "plan", created.current.id, planned, design.id);
            approveStage("plan");
            approveStage("backlog");
            await appA.lifecycle.backlog(created.id);
            approveStage("dispatch");

            // Simulate local coding attempt bound in wt-a without a PR publication receipt yet.
            const execA = appA.lifecycle.begin(created.id, "implement", actor);
            appA.registry.recordCodingProvenance(created.id);
            appA.lifecycle.store.artifact(created.id, "implementation_attempt", created.current.id, {
                itemId: "PBI-1",
                roundsCompleted: 1,
            }, null, execA.id);
            appA.lifecycle.store.updateExecution(created.id, execA.id, { status: "error", error: "Interrupted before push" });
            assert.equal(appB.registry.provenance(created.id)?.codingWorktree, "wt-a");

            // Attempting to start local implement from sibling worktree wt-b fails with cross_worktree_checkout_conflict.
            assert.throws(
                () => appB.lifecycle.begin(created.id, "implement", actor),
                (err: unknown) => (err as FactoryError)?.code === "cross_worktree_checkout_conflict",
            );

            // Once wt-a clears coding provenance (e.g., reconciled or pushed), wt-b can proceed cleanly.
            appA.registry.recordCodingProvenance(created.id, true);
            const execB = appB.lifecycle.begin(created.id, "implement", actor);
            assert.equal(execB.status, "starting");
            appB.lifecycle.store.updateExecution(created.id, execB.id, { status: "completed" });
        } finally {
            appA.close();
            appB.close();
        }
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

test("AB#3822859: startServer fallbackOnBusy falls back cleanly when requested port is occupied", async () => {
    const f = transportFixture();
    const blocker = net.createServer();
    await new Promise<void>((resolve) => blocker.listen(0, "127.0.0.1", resolve));
    const occupiedPort = (blocker.address() as net.AddressInfo).port;
    try {
        await assert.rejects(() => startServer({ application: f.app, port: occupiedPort, fallbackOnBusy: false }));
        const fallbackServer = await startServer({ application: f.app, port: occupiedPort, fallbackOnBusy: true });
        try {
            const assignedPort = Number(new URL(fallbackServer.url).port);
            assert.notEqual(assignedPort, occupiedPort);
            assert.ok(assignedPort > 0);
            const res = await fetch(`${fallbackServer.url}/api/session`);
            assert.equal(res.status, 200);
        } finally {
            await fallbackServer.close();
        }
    } finally {
        await new Promise<void>((resolve) => blocker.close(() => resolve()));
        f.cleanup();
    }
});

test("Double-feature deduplication: findSupersededLocalDraftIds hides pre-publish local drafts once published ADO successor exists", () => {
    const f = transportFixture();
    try {
        const draftCreated = f.app.registry.create("draft-req-1", requirements(), actor);
        const independentCreated = f.app.registry.create("independent-req-2", { ...requirements(), title: "Independent draft" }, actor);
        const localDraft: FactoryFeature = f.app.lifecycle.store.view(draftCreated.id);
        const independentDraft: FactoryFeature = f.app.lifecycle.store.view(independentCreated.id);
        const publishedFeature: FactoryFeature = {
            ...structuredClone(localDraft),
            id: "shared-feature-a735aff7",
            shared: {
                target: { organization: "https://dev.azure.com/IdentityDivision", project: "Engineering", workItemId: 3822464 },
                revision: 45,
                digest: "sha256:published",
                lock: null,
            },
        };

        const hidden = findSupersededLocalDraftIds([localDraft, publishedFeature, independentDraft]);
        assert.deepEqual([...hidden], [localDraft.id]);
    } finally {
        f.cleanup();
    }
});

test("AB#3822862: pure TS diff engine computes word, hunk, split, requirements, design, and plan revision diffs + hydrates journal candidates", async () => {
    const words = diffWords("Cache throttle for 60s", "Cache throttle for 120s strictly");
    assert.ok(words.some((w) => w.kind === "remove" && w.text === "60s"));
    assert.ok(words.some((w) => w.kind === "add" && w.text.includes("120s")));

    const textDiff = diffText("line1\nline2\nline3\nline4\nline5\nold6\nline7", "line1\nline2\nline3\nline4\nline5\nnew6\nline7");
    const hunks = buildDiffHunks(textDiff.rows, 1);
    assert.ok(hunks.some((h) => h.type === "collapsed" && h.hiddenCount >= 3));
    const split = buildSplitRows(textDiff.rows);
    assert.ok(split.some((r) => r.left.kind === "remove" && r.right.kind === "add"));

    // Stage 1 Requirements diff
    const reqDiff = diffRequirementsRevisions(
        {
            id: "rev-1",
            number: 1,
            hash: "h1",
            createdAt: "2026-04-17T00:00:00Z",
            title: "Broker throttling",
            description: "Initial brief",
            requirements: [{ id: "R1", text: "Cache 429." }, { id: "R2", text: "Drop legacy." }],
            designTasks: ["Check IPC"],
            blockingUnknowns: ["Max TTL?"],
        },
        {
            id: "rev-2",
            number: 2,
            hash: "h2",
            createdAt: "2026-04-17T00:05:00Z",
            title: "Broker token throttling",
            description: "Updated brief with Retry-After",
            requirements: [{ id: "R1", text: "Cache 429 with Retry-After." }, { id: "R3", text: "Emit telemetry." }],
            designTasks: ["Check IPC", "Check telemetry"],
            blockingUnknowns: [],
        },
    );
    assert.equal(reqDiff.changed, true);
    assert.equal(reqDiff.summaryCounts.criteriaModified, 1);
    assert.equal(reqDiff.summaryCounts.criteriaRemoved, 1);
    assert.equal(reqDiff.summaryCounts.criteriaAdded, 1);

    // Stage 2 Design diff + Reviewer Finding Resolution tracking
    const round1Design = {
        sections: {
            summary: "Draft throttling design",
            architecture: "In-memory map keyed by clientId",
            migrationAndCompatibility: "Backward compatible",
        },
        changes: [
            { id: "CHG-1", repository: "common", path: "ThrottleCache.kt", kind: "create", summary: "Create cache", verification: "Unit test" },
        ],
        traceability: [{ requirementId: "R1", sectionIds: ["architecture"], evidenceIds: ["E1"], verification: "ThrottleTest" }],
    };
    const round2Design = {
        sections: {
            summary: "Hardened throttling design",
            architecture: "Bounded LRU cache keyed by clientId + authority + scopes",
            migrationAndCompatibility: "Backward compatible across IPC v4+",
        },
        changes: [
            { id: "CHG-1", repository: "common", path: "ThrottleCache.kt", kind: "create", summary: "Create bounded LRU cache", verification: "ThrottleLruTest" },
            { id: "CHG-2", repository: "common", path: "Telemetry.kt", kind: "modify", summary: "Record span attribute", verification: "SpanTest" },
        ],
        traceability: [{ requirementId: "R1", sectionIds: ["architecture", "summary"], evidenceIds: ["E1"], verification: "ThrottleLruTest" }],
    };
    const designDiff = diffDesignSnapshots(
        round1Design,
        round2Design,
        { findings: [{ id: "F-1", severity: "blocking", location: "CHG-1", summary: "Cache key omits scopes" }] },
        { findings: [] },
    );
    assert.equal(designDiff.changed, true);
    assert.equal(designDiff.changes.find((u) => u.id === "CHG-1")?.status, "modified");
    assert.equal(designDiff.changes.find((u) => u.id === "CHG-2")?.status, "added");
    assert.equal(designDiff.reviewDelta?.findingsResolved.length, 1);
    assert.equal(designDiff.reviewDelta?.findingsRemaining.length, 0);

    // Stage 3 Plan diff
    const planDiff = diffPlanSnapshots(
        {
            summary: "Initial plan",
            items: [{ id: "PBI-1", repository: "common", title: "Add cache", description: "v1", requirementIds: ["R1"], acceptanceCriteria: ["R1"], files: ["Cache.kt"], tests: ["CacheTest"], dependsOn: [] }],
        },
        {
            summary: "Refined two-step plan",
            items: [
                { id: "PBI-1", repository: "common", title: "Add bounded cache", description: "v2", requirementIds: ["R1"], acceptanceCriteria: ["R1"], files: ["Cache.kt"], tests: ["CacheLruTest"], dependsOn: [] },
                { id: "PBI-2", repository: "msal", title: "Wire MSAL caller", description: "caller", requirementIds: ["R1"], acceptanceCriteria: ["R1"], files: ["Msal.kt"], tests: ["MsalTest"], dependsOn: ["PBI-1"] },
            ],
        },
    );
    assert.equal(planDiff.changed, true);
    assert.equal(planDiff.items.find((i) => i.id === "PBI-1")?.status, "modified");
    assert.equal(planDiff.items.find((i) => i.id === "PBI-2")?.status, "added");

    // Verify historical workflow_journal candidate hydration on GET /api/features
    const f = transportFixture();
    const server = await startServer({ application: f.app });
    const tempSessionId = `sf_test_journal_${randomUUID().replace(/-/g, "")}`;
    const sessionDir = path.join(os.homedir(), ".copilot", "session-state", tempSessionId);
    mkdirSync(sessionDir, { recursive: true });
    try {
        const created = f.app.execute({ kind: "create_feature", requestId: "journal-hydrate-req", input: requirements() }, actor);
        f.app.execute(
            { kind: "approve_requirements", featureId: created.id, expectedRevision: created.current.id },
            actor,
            { granted: true, revisionId: created.current.id, actor },
        );
        const exec = f.app.lifecycle.begin(created.id, "design", actor, {}, {}, undefined, { sessionId: tempSessionId, kind: "owned", established: true });
        const nativeRunId = `run_${randomUUID()}`;
        f.app.lifecycle.store.updateExecution(created.id, exec.id, { runtimeId: nativeRunId, status: "running" });

        // Seed synthetic workflow_journal in ~/.copilot/session-state/<tempSessionId>/session.db
        const journalDb = new DatabaseSync(path.join(sessionDir, "session.db"));
        try {
            journalDb.exec(`
                CREATE TABLE workflow_journal (
                    root_run_id TEXT NOT NULL,
                    journal_key TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
            `);
            journalDb.prepare("INSERT INTO workflow_journal (root_run_id, journal_key, result_json) VALUES (?, ?, ?)")
                .run(nativeRunId, "design-round-1", JSON.stringify(round1Design));
            journalDb.prepare("INSERT INTO workflow_journal (root_run_id, journal_key, result_json) VALUES (?, ?, ?)")
                .run(nativeRunId, "design-round-2", JSON.stringify(round2Design));
        } finally {
            journalDb.close();
        }

        f.app.lifecycle.store.artifact(created.id, "design", created.current.id, {
            ...round2Design,
            recommendation: "GO to Planning",
            reviews: [
                { round: 1, status: "changes_requested", summary: "Fix cache key", findings: [] },
                { round: 2, status: "approved", summary: "LGTM", findings: [] },
            ],
        }, null, exec.id);
        f.app.lifecycle.store.updateExecution(created.id, exec.id, { status: "completed" });

        const boot = await fetch(`${server.url}/api/session`);
        const cookie = boot.headers.get("set-cookie")!.split(";")[0]!;
        const featuresRes = await fetch(`${server.url}/api/features`, { headers: { Cookie: cookie } });
        const payload = await featuresRes.json() as {
            features: Array<{ factory: { artifacts: Array<{ kind: string; content: { reviews?: Array<{ round: number; candidate?: { sections?: { summary?: string } } }> } }> } }>;
        };
        const hydratedDesign = payload.features[0]?.factory.artifacts.find((a) => a.kind === "design");
        assert.equal(hydratedDesign?.content.reviews?.[0]?.candidate?.sections?.summary, "Draft throttling design");
        assert.equal(hydratedDesign?.content.reviews?.[1]?.candidate?.sections?.summary, "Hardened throttling design");
    } finally {
        rmSync(sessionDir, { recursive: true, force: true });
        await server.close();
        f.cleanup();
    }
});

test("AB#3822858: computeFeatureScorecard, dedupeExecutions, and GET /api/analytics compute exact KPIs without double-counting", async () => {
    assert.equal(nanoToCredits(440_900_000_000), 440.9);

    const execPrimary: Execution = {
        id: "exec-primary",
        kind: "design",
        requirementsRevision: "rev-1",
        configurationId: null,
        sessionId: "sess-1",
        runtimeId: "native-1",
        parentSessionId: null,
        status: "completed",
        phase: "Completed",
        generation: { design: 0, plan: 0 },
        sourcePins: {},
        runtimeFingerprint: "fp-1",
        limits: {},
        usage: {
            observedAt: 1,
            activeMs: 120_000,
            subagents: 5,
            nanoAiu: 440_900_000_000,
            declaredLimits: {},
            approvedLimits: null,
            agents: [
                { label: "Design author", status: "completed", requestedModel: null, resolvedModel: "gpt-5.4", activeMs: 80_000 },
                { label: "Independent design reviewer", status: "completed", requestedModel: null, resolvedModel: "claude-opus-4.6", activeMs: 40_000 },
            ],
        },
        checkpoints: {},
        outcome: null,
        error: null,
        createdAt: "2026-04-17T00:00:00Z",
        updatedAt: "2026-04-17T00:02:00Z",
    };
    const execMirror: Execution = {
        ...execPrimary,
        id: "exec-joined-mirror",
    };
    const deduped = dedupeExecutions([execPrimary, execMirror]);
    assert.equal(deduped.length, 1);
    assert.equal(deduped[0]!.usage?.nanoAiu, 440_900_000_000);

    const execImplement: Execution = {
        ...execPrimary,
        id: "exec-impl",
        kind: "implement",
        runtimeId: "native-impl",
        usage: {
            observedAt: 2,
            activeMs: 90_000,
            subagents: 3,
            nanoAiu: 205_000_000_000,
            declaredLimits: {},
            approvedLimits: null,
            agents: [
                { label: "Factory:coder:PBI-1:round-1", status: "completed", requestedModel: null, resolvedModel: "gpt-5.4", activeMs: 60_000 },
                { label: "Factory:code-reviewer:PBI-1:round-1", status: "completed", requestedModel: null, resolvedModel: "claude-opus-4.6", activeMs: 30_000 },
            ],
        },
    };

    const f = transportFixture();
    const server = await startServer({ application: f.app });
    try {
        const created = f.app.registry.create("analytics-req", requirements(), actor);
        const baseView = f.app.lifecycle.store.view(created.id);
        const revId = created.current.id;
        const artifacts: Artifact[] = [
            {
                id: "art-d",
                kind: "design",
                requirementsRevision: revId,
                parentId: null,
                generation: 0,
                configurationId: null,
                analysisHash: "ah",
                executionId: "exec-primary",
                hash: "h-d",
                content: { reviews: [{ round: 1, status: "changes_requested" }, { round: 2, status: "approved" }], fixIterations: 1 },
                createdAt: "2026-04-17T00:02:00Z",
            },
            {
                id: "art-impl",
                kind: "implementation",
                requirementsRevision: revId,
                parentId: null,
                generation: 0,
                configurationId: null,
                analysisHash: "ah",
                executionId: "exec-impl",
                hash: "h-i",
                content: {
                    items: [{
                        itemId: "PBI-1",
                        roundsCompleted: 1,
                        repairAttempts: 0,
                        observerStatus: "awaiting-human-review",
                        receipt: { prNumber: 3354, url: "https://github.com/AzureAD/common/pull/3354" },
                    }],
                },
                createdAt: "2026-04-17T00:05:00Z",
            },
            {
                id: "art-mon",
                kind: "monitor",
                requirementsRevision: revId,
                parentId: null,
                generation: 0,
                configurationId: null,
                analysisHash: "ah",
                executionId: "exec-mon",
                hash: "h-m",
                content: {
                    prs: [{ key: "PBI-1", number: 3354, url: "https://github.com/AzureAD/common/pull/3354", checks: "passing" }],
                    validation: [
                        { itemId: "PBI-1", criterion: "R1", status: "addressed", evidence: "Verified" },
                        { itemId: "PBI-1", criterion: "R2", status: "addressed", evidence: "Verified" },
                    ],
                    errors: [],
                },
                createdAt: "2026-04-17T00:06:00Z",
            },
        ];
        const syntheticView: FactoryFeature = {
            ...baseView,
            currentArtifacts: { intake: null, design: "art-d", plan: null, monitor: "art-mon" },
            factory: {
                ...baseView.factory,
                executions: [execPrimary, execMirror, execImplement],
                artifacts,
            },
        };

        const scorecard = computeFeatureScorecard(syntheticView);
        assert.equal(scorecard.totalCredits, 645.9);
        assert.equal(scorecard.totalSubagents, 8);
        assert.equal(scorecard.prCount, 1);
        assert.equal(scorecard.firstPushCiGreenCount, 1);
        assert.equal(scorecard.monitorCriteriaAddressed, 2);
        assert.equal(scorecard.monitorCriteriaTotal, 2);
        assert.equal(scorecard.monitorPass, true);

        const ws = computeWorkspaceAnalytics([syntheticView]);
        assert.equal(ws.featureCount, 1);
        assert.equal(ws.totalCredits, 645.9);
        assert.equal(ws.convergence.firstPushCiGreenRatePct, 100);
        assert.equal(ws.convergence.monitorCriteriaPassRatePct, 100);
        assert.ok(ws.specialistRoles.some((r) => r.roleKey === "design-reviewer"));
        assert.ok(ws.specialistRoles.some((r) => r.roleKey === "code-reviewer"));

        const boot = await fetch(`${server.url}/api/session`);
        const cookie = boot.headers.get("set-cookie")!.split(";")[0]!;
        const analyticsRes = await fetch(`${server.url}/api/analytics`, { headers: { Cookie: cookie } });
        assert.equal(analyticsRes.status, 200);
        const payload = await analyticsRes.json() as { analytics: { featureCount: number } };
        assert.equal(payload.analytics.featureCount, 1);
    } finally {
        await server.close();
        f.cleanup();
    }
});

test("AB#3822864: GET /api/events streams connected, live state-changed pushes, and token-by-token Ask Copilot Q&A deltas", async () => {
    const qaResponder: QaResponder = async (input) => {
        input.onStepStart("tc-1", "view", "common/ThrottleCache.kt");
        input.onStepComplete("tc-1", true);
        input.onDelta("The cache uses ");
        input.onDelta("bounded LRU eviction.");
        return "The cache uses bounded LRU eviction.";
    };

    const f = transportFixture();
    const server = await startServer({ application: f.app, qaResponder });
    try {
        // Unauthenticated SSE connection is rejected.
        const unauth = await fetch(`${server.url}/api/events`);
        assert.equal(unauth.status, 401);

        const boot = await fetch(`${server.url}/api/session`);
        const cookie = boot.headers.get("set-cookie")!.split(";")[0]!;
        const { csrf } = await boot.json() as { csrf: string };
        const headers = { Cookie: cookie, Origin: server.url, "Content-Type": "application/json", "X-Software-Factory-CSRF": csrf };

        // Open real streaming SSE reader against /api/events.
        const abort = new AbortController();
        const sseRes = await fetch(`${server.url}/api/events`, {
            headers: { Cookie: cookie },
            signal: abort.signal,
        });
        assert.equal(sseRes.status, 200);
        assert.match(sseRes.headers.get("content-type") || "", /text\/event-stream/);

        const receivedEvents: Array<{ type: string; [key: string]: unknown }> = [];
        const decoder = new TextDecoder();
        let buffer = "";
        const reader = sseRes.body!.getReader();
        const pump = (async () => {
            try {
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    let sep = buffer.indexOf("\n\n");
                    while (sep >= 0) {
                        const frame = buffer.slice(0, sep);
                        buffer = buffer.slice(sep + 2);
                        let eventName = "message";
                        let dataPayload = "";
                        for (const line of frame.split("\n")) {
                            if (line.startsWith("event: ")) eventName = line.slice(7).trim();
                            else if (line.startsWith("data: ")) dataPayload = line.slice(6);
                        }
                        if (dataPayload) {
                            const parsed = JSON.parse(dataPayload) as Record<string, unknown>;
                            receivedEvents.push({ ...parsed, type: typeof parsed.type === "string" ? parsed.type : eventName });
                        }
                        sep = buffer.indexOf("\n\n");
                    }
                }
            } catch {
                // Expected on abort.abort() cleanup.
            }
        })();

        const waitFor = async (predicate: () => boolean, timeoutMs = 4_000) => {
            const deadline = Date.now() + timeoutMs;
            while (Date.now() < deadline) {
                if (predicate()) return;
                await new Promise((r) => setTimeout(r, 20));
            }
            assert.fail(`Timed out waiting for SSE events. Received: ${JSON.stringify(receivedEvents)}`);
        };

        await waitFor(() => receivedEvents.some((e) => e.type === "connected"));

        // Mutating feature state pushes live `state-changed` over SSE without polling!
        const createRes = await fetch(`${server.url}/api/commands`, {
            method: "POST",
            headers,
            body: JSON.stringify({ kind: "create_feature", requestId: "sse-feat-1", input: requirements() }),
        });
        assert.equal(createRes.status, 200);
        const createdFeature = f.app.registry.list()[0]!;
        await waitFor(() => receivedEvents.some((e) => e.type === "state-changed"));

        // Asking Copilot streams qa-started -> qa-step -> qa-delta -> qa-complete live over SSE!
        const askRes = await fetch(`${server.url}/api/features/${encodeURIComponent(createdFeature.id)}/qa`, {
            method: "POST",
            headers,
            body: JSON.stringify({ question: "How does cache eviction work?", context: { kind: "design-section", title: "Architecture" } }),
        });
        assert.equal(askRes.status, 202);

        await waitFor(() => receivedEvents.some((e) => e.type === "qa-complete"));
        assert.ok(receivedEvents.some((e) => e.type === "qa-started"));
        assert.ok(receivedEvents.some((e) => e.type === "qa-step"));
        const deltas = receivedEvents.filter((e) => e.type === "qa-delta").map((e) => String(e.delta)).join("");
        assert.equal(deltas, "The cache uses bounded LRU eviction.");

        abort.abort();
        await pump;
    } finally {
        await server.close();
        f.cleanup();
    }
});
