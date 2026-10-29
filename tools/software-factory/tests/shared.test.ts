// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Application } from "../core/application.ts";
import { type Actor } from "../core/contracts.ts";
import { runtimeFingerprint } from "../core/evidence.ts";
import { parseSnapshot, validateTransition } from "../core/shared-contracts.ts";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { FACTORY_READER } from "../runtime/reader.ts";
import { awaitWorkflowRegistration } from "../runtime/registration.ts";
import { TeamProvider, teamConfig } from "./team-provider.ts";
import type { Provider } from "../providers/provider.ts";
import type { WorkItem } from "../core/lifecycle-contracts.ts";
import { actor, requirements } from "./helpers.ts";


test("real chat boundary requires confirmation and persists the authenticated approval in ADO", async () => {
    const f = teamFixture();
    try {
        const app = f.create("chat");
        const draft = app.execute({ kind: "create_feature", requestId: "chat", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        let accept = false;
        let confirmations = 0;
        const host: ChatHost = { sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
            ui: { confirm: async () => { confirmations++; return accept; } } };
        const tool = createChatTools(() => app, () => host).find((entry) => entry.name === "software_factory_approve_requirements")!;
        const args = { featureId: feature.id, expectedRevision: feature.current.id };
        const context = { sessionId: actor.sessionId!, toolCallId: "real-confirmation" };
        await tool.handler(args, context);
        assert.equal((await f.provider.read(target))!.snapshot.feature.approvals.length, 0);
        accept = true;
        await tool.handler(args, context);
        const approval = (await f.provider.read(target))!.snapshot.feature.approvals[0]!;
        assert.equal(confirmations, 2);
        assert.equal(approval.actor.ado?.id, "alice");
        assert.equal(approval.actor.reference, "real-confirmation");
        assert.equal(app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally { f.cleanup(); }
});

test("snapshot validation rejects rewritten history and impersonated approval provenance", async () => {
    const f = teamFixture();
    try {
        const app = f.create("validation");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const original = (await f.provider.read(target))!;
        await submit(app, { kind: "approve_requirements", featureId: original.snapshot.feature.id, expectedRevision: original.snapshot.feature.current.id });
        const approved = (await f.provider.read(target))!.snapshot;
        assert.throws(() => validateTransition(original.snapshot, approved, { id: "mallory", displayName: "Mallory" }), /authenticated/);
        const rewritten = structuredClone(approved);
        rewritten.feature.audit[0]!.kind = "forged";
        assert.throws(() => validateTransition(approved, rewritten, f.provider.user), /rewritten/);
        rewritten.feature.current.hash = "bad";
        assert.throws(() => parseSnapshot(rewritten), /stored hash|immutable revision/);
        const local = structuredClone(approved);
        local.state.sessionBinding = { sessionId: "stolen", kind: "chat", established: true };
        assert.throws(() => parseSnapshot(local), /remain local/);
    } finally { f.cleanup(); }
});
const target = { organization: "example", project: "Engineering", workItemId: 123 };

export function teamFixture() {
    const root = mkdtempSync(path.join(os.tmpdir(), "factory-team-test-"));
    const provider = new TeamProvider();
    const applications: Application[] = [];
    const create = (name: string, external?: Provider) => {
        const workspace = path.join(root, name);
        mkdirSync(workspace);
        const app = new Application({ database: path.join(root, `${name}.sqlite`), workspace, sharedProvider: provider, provider: external });
        app.lifecycle.configure(teamConfig(name, name), actor);
        applications.push(app);
        return app;
    };
    return { provider, create, cleanup() { for (const app of applications) app.close(); rmSync(root, { recursive: true, force: true }); } };
}

async function submit(app: Application, command: Record<string, unknown>, who: Actor = actor) {
    const preview = await app.preview(command);
    return app.submit(command, who, preview.consentKey ? { granted: true, revisionId: preview.consentKey, actor: who } : undefined);
}

test("team feature survives another machine, separate accounts, approvals, revision and restart", async () => {
    const f = teamFixture();
    try {
        const first = f.create("first");
        const draft = first.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await assert.rejects(() => first.preview({ kind: "approve_requirements", featureId: draft.id, expectedRevision: draft.current.id }), /Publish/);
        const result = await submit(first, { kind: "publish_feature", featureId: draft.id, target });
        assert.ok(result.feature);
        const record = await f.provider.read(target);
        const featureId = record!.snapshot.feature.id;
        assert.notEqual(featureId, draft.id);
        assert.equal(first.registry.get(draft.id).status, "requirements_review");
        const revision = first.registry.get(featureId).current.id;
        await submit(first, { kind: "approve_requirements", featureId, expectedRevision: revision });
        assert.equal(first.registry.get(featureId).approvals[0]!.actor.ado?.id, "alice");
        const second = f.create("second");
        f.provider.user = { id: "bob", displayName: "Bob" };
        await second.shared.open(target);
        assert.equal(second.registry.get(featureId).status, "ready_for_design");
        assert.equal(second.lifecycle.configuration().repositories.common!.account, "second");
        assert.equal(second.lifecycle.configuration().repositories.common!.localPath, "second");
        const before = await first.preview({ kind: "approve_requirements", featureId, expectedRevision: revision });
        await submit(second, { kind: "revise_requirements", featureId, expectedRevision: revision,
            input: { ...requirements(), requirements: [{ id: "R1", text: "Preserve the amended compatibility behavior." }] } });
        await assert.rejects(() => first.submit(before.command, actor), /current requirements|current revision/i);
        const updated = await first.feature(featureId);
        assert.equal(updated.status, "requirements_review");
        assert.equal(updated.history.length, 2);
        assert.equal(updated.approvals.length, 1, "old approval history remains, but cannot approve a successor");
        const third = f.create("restart");
        await third.shared.open(target);
        assert.equal(third.registry.get(featureId).current.id, updated.current.id);
        assert.equal(third.lifecycle.store.state(featureId).sessionBinding, null, "team continuation does not steal another user's chat");
    } finally { f.cleanup(); }
});

test("ADO outage and denied edit never become local approvals; draft editing remains available", async () => {
    const f = teamFixture();
    try {
        const app = f.create("owner");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const id = (await f.provider.read(target))!.snapshot.feature.id;
        const command = { kind: "approve_requirements", featureId: id, expectedRevision: app.registry.get(id).current.id };
        f.provider.online = false;
        await assert.rejects(() => submit(app, command), /offline/);
        const local = app.execute({ kind: "revise_requirements", featureId: draft.id, expectedRevision: draft.current.id,
            input: { ...requirements(), title: "Offline scratch work" } }, actor);
        assert.equal(local.current.title, "Offline scratch work");
        f.provider.online = true;
        f.provider.canEdit = false;
        await assert.rejects(() => submit(app, command), /permission denied/);
        assert.equal(app.registry.get(id).approvals.length, 0);
    } finally { f.cleanup(); }
});

test("shared claims serialize writers, require explicit recovery, and revoke late agent output", async () => {
    const f = teamFixture();
    try {
        const app = f.create("owner");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const id = (await f.provider.read(target))!.snapshot.feature.id;
        await submit(app, { kind: "approve_requirements", featureId: id, expectedRevision: app.registry.get(id).current.id });
        const execution = app.lifecycle.begin(id, "design", actor);
        await app.shared.claimRun(id, execution);
        const other = f.create("reviewer");
        await other.shared.open(target);
        await assert.rejects(() => submit(other, { kind: "revise_requirements", featureId: id,
            expectedRevision: other.registry.get(id).current.id, input: { ...requirements(), title: "Changed" } }), /active/);
        const command = { kind: "release_shared_claim", featureId: id, claimId: execution.id, reason: "Owner stopped the native run; no provider sends occurred." };
        await submit(other, command);
        await assert.rejects(() => app.shared.finishRun(id, execution.id), /claim|inputs changed/);
    } finally { f.cleanup(); }
});

test("shared backlog sends persist inflight authority and reconcile a lost receipt without duplicate work", async () => {
    const f = teamFixture();
    const items = new Map<string, WorkItem>();
    let sends = 0;
    let fail = true;
    const blocked = async (): Promise<never> => { throw new Error("Unexpected external call in shared backlog fixture."); };
    const provider: Provider = {
        findWorkItem: async (_config, marker) => items.get(marker) || null,
        createWorkItem: async (_config, item, marker) => {
            const shared = (await f.provider.read(target))!;
            assert.ok(shared.snapshot.lock);
            assert.equal(shared.snapshot.state.operations.at(-1)?.status, "inflight");
            sends++;
            const receipt = { key: item.id, adoId: 456, url: "https://dev.azure.com/example/Engineering/_workitems/edit/456", title: item.title, repository: "common", status: "New" };
            items.set(marker, receipt);
            if (fail) { fail = false; throw new Error("Lost response after creation."); }
            return receipt;
        },
        linkWorkItem: async () => ({ linked: true }), inspectWorkItem: blocked, dispatch: blocked,
        findDispatch: blocked, inspectPr: blocked, findPr: blocked, diff: blocked, preflight: blocked,
        findFeedback: blocked, postFeedback: blocked, markDispatched: blocked, review: blocked,
    };
    try {
        const app = f.create("delivery", provider);
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const approve = async (stage: string) => submit(app, { kind: "approve_stage", featureId: feature.id, stage,
            expectedScopeHash: app.lifecycle.gate(feature.id, stage as "design" | "plan" | "backlog").scopeHash, approved: true });
        const designRun = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, designRun);
        const design = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        await app.shared.finishRun(feature.id, designRun.id);
        app.lifecycle.store.updateExecution(feature.id, designRun.id, { status: "completed" });
        await approve("design");
        const planRun = app.lifecycle.begin(feature.id, "plan", actor);
        await app.shared.claimRun(feature.id, planRun);
        app.lifecycle.store.artifact(feature.id, "plan", feature.current.id, { summary: "Implement compatibility.", blockingUnknowns: [],
            items: [{ id: "A", title: "Keep compatibility", repository: "common", description: "Preserve the existing response shape.",
                requirementIds: ["R1"], acceptanceCriteria: ["Old callers keep working."], dependsOn: [], files: ["A.kt"], tests: ["Compatibility test"] }] }, design.id);
        await app.shared.finishRun(feature.id, planRun.id);
        app.lifecycle.store.updateExecution(feature.id, planRun.id, { status: "completed" });
        await approve("plan");
        await approve("backlog");
        await assert.rejects(() => submit(app, { kind: "create_backlog", featureId: feature.id }), /Inspect the ADO claim/);
        const uncertain = (await f.provider.read(target))!;
        assert.equal(uncertain.snapshot.state.operations[0]!.status, "reconciliation_required");
        await submit(app, { kind: "release_shared_claim", featureId: feature.id, claimId: uncertain.snapshot.lock!.id,
            reason: "Provider lookup confirms the created item; reconcile its receipt rather than resending." });
        await submit(app, { kind: "reconcile_operation", featureId: feature.id, operationKey: uncertain.snapshot.state.operations[0]!.key });
        await submit(app, { kind: "create_backlog", featureId: feature.id });
        assert.equal(sends, 1);
        const completed = (await f.provider.read(target))!;
        assert.equal(completed.snapshot.lock, null);
        assert.ok(completed.snapshot.state.operations.every((operation) => operation.status === "completed"));
    } finally { f.cleanup(); }
});

test("slow shared claim acquisition waits for ADO lock and accepts redacted sessionId with matching hash", async () => {
    const f = teamFixture();
        let appRef: Application | undefined;
    try {
        const app = f.create("claim-race");
        const ws = app.registry.project.workspace;
        app.lifecycle.configure(teamConfig(".", "claim-race"), actor);
        execFileSync("git", ["init", "--quiet", ws]);
        execFileSync("git", ["-C", ws, "config", "user.email", "test@example.com"]);
        execFileSync("git", ["-C", ws, "config", "user.name", "Test"]);
        writeFileSync(path.join(ws, "README.md"), "fixture\n");
        execFileSync("git", ["-C", ws, "add", "README.md"]);
        execFileSync("git", ["-C", ws, "commit", "--quiet", "-m", "init"]);
        appRef = app;
        const draft = app.execute({ kind: "create_feature", requestId: "claim-race", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const sessionHash = createHash("sha256").update(actor.sessionId!).digest("hex");
        await assert.rejects(
            () => awaitWorkflowRegistration({
                sessionId: actor.sessionId!,
                rpc: {
                    extensions: { list: async () => ({ extensions: [{ id: "project:SoftwareFactory", name: "SoftwareFactory", source: "project" as const, status: "running" as const }] }), reload: async () => {} },
                    tools: {
                        execute: async () => ({
                            resultType: "success" as const,
                            textResultForLlm: JSON.stringify({
                                ready: true, sessionId: "******", sessionIdHash: "wrong-hash",
                                workflowNames: ["software-factory-design"], runtimeFingerprint: runtimeFingerprint(), readerAgent: FACTORY_READER,
                            }),
                        }),
                    },
                },
            }, "software-factory-design", runtimeFingerprint()),
            /different session/,
        );
        let nativeRuns = 0;
        const session = {
            sessionId: actor.sessionId!,
            rpc: {
                extensions: { list: async () => ({ extensions: [{ id: "project:SoftwareFactory", name: "SoftwareFactory", source: "project" as const, status: "running" as const }] }), reload: async () => {} },
                tools: {
                    initializeAndValidate: async () => ({ toolNames: [] }),
                    getCurrentMetadata: async () => ({ tools: [{ name: "view", description: "v" }, { name: "glob", description: "g" }, { name: "grep", description: "s" }] }),
                    execute: async () => ({
                        resultType: "success" as const,
                        textResultForLlm: JSON.stringify({
                            ready: true, sessionId: "******", sessionIdHash: sessionHash,
                            workflowNames: ["software-factory-design"], runtimeFingerprint: runtimeFingerprint(), readerAgent: FACTORY_READER,
                        }),
                    }),
                },
            },
            workflow: {
                run: async () => { nativeRuns++; return { runId: "race-run-1", status: "completed" as const, result: { ok: true } }; },
                resume: async () => { throw new Error("unexpected"); },
                pause: async () => { throw new Error("unexpected"); },
                cancel: async () => { throw new Error("unexpected"); },
                getRun: async () => { throw new Error("unexpected"); },
                getRunDetail: async () => ({
                    runId: "race-run-1", workflowName: "software-factory-design", description: "Design", status: "completed" as const,
                    revision: 1, createdAt: Date.now(), startedAt: Date.now(), updatedAt: Date.now(), completedAt: Date.now(),
                    currentPhase: null, declaredPhaseCount: 1, liveAgentCount: 0, totalSpawnedAgentCount: 0,
                    consumed: { subagents: 0, activeMs: 1, nanoAiu: 0 },
                    declaredLimits: {}, approved: null, observedAt: Date.now(), activeSegmentStartedAt: null,
                    terminal: null, canResume: false, phases: [], agents: [],
                    progress: { records: [], oldestSeq: null, newestSeq: null, hasMoreOlder: false, hasMoreNewer: false, revision: 1 },
                }),
            },
        };
        const originalWrite = f.provider.write.bind(f.provider);
        f.provider.write = async (writeTarget, expected, snapshot, identity) => {
            if (snapshot.lock?.kind === "design") {
                await new Promise((resolve) => setTimeout(resolve, 1400));
                const pending = app!.lifecycle.store.state(feature.id).executions.at(-1)!;
                assert.equal(pending.status, "starting");
                assert.equal(pending.phase, "Claiming shared execution");
            }
            return originalWrite(writeTarget, expected, snapshot, identity);
        };
        await app.bindChatSession(session);
        const started = await submit(app, { kind: "start_workflow", featureId: feature.id, workflow: "design" });
        assert.equal(started.outcome, "workflow_requested");
        const deadline = Date.now() + 10_000;
        while (Date.now() < deadline) {
            const current = app.lifecycle.store.state(feature.id).executions.at(-1)!;
            if (current.status === "completed" || current.status === "error") break;
            await new Promise((resolve) => setTimeout(resolve, 50));
        }
        const finalExecution = app.lifecycle.store.state(feature.id).executions.at(-1)!;
        assert.equal(finalExecution.status, "completed", finalExecution.error || finalExecution.status);
        assert.equal(nativeRuns, 1);
    } finally {
        await appRef?.shutdown();
        f.cleanup();
    }
});
