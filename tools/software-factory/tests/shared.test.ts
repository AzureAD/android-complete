// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Application } from "../core/application.ts";
import { object, type Actor } from "../core/contracts.ts";
import { runtimeFingerprint } from "../core/evidence.ts";
import { parseSnapshot, validateTransition } from "../core/shared-contracts.ts";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { FACTORY_READER } from "../runtime/reader.ts";
import { awaitWorkflowRegistration } from "../runtime/registration.ts";
import { TeamProvider, teamConfig } from "./team-provider.ts";
import type { Provider } from "../providers/provider.ts";
import type { WorkItem } from "../core/lifecycle-contracts.ts";
import { actor, requirements } from "./helpers.ts";
import { nextStep } from "../core/progress.ts";
import { startServer } from "../server/server.ts";


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

test("shared reference sources preserve local mappings without granting delivery authority", async () => {
    const f = teamFixture();
    try {
        const first = f.create("reference-owner");
        const configuration = first.lifecycle.configuration();
        first.lifecycle.configure({ ...configuration, referenceRepositories: {
            consumer: { ...configuration.repositories.common!, slug: "example/consumer", localPath: "owner-consumer", account: "owner-reader" },
        } }, actor);
        const draft = first.execute({ kind: "create_feature", requestId: "reference-draft", input: requirements() }, actor);
        await submit(first, { kind: "publish_feature", featureId: draft.id, target });
        const shared = (await f.provider.read(target))!.snapshot;
        assert.equal(shared.configuration.value.referenceRepositories?.consumer?.localPath, ".");
        assert.equal(shared.configuration.value.referenceRepositories?.consumer?.account, "local-account-required");
        const second = f.create("reference-reader");
        await assert.rejects(() => second.shared.open(target), /matching repository/);
        second.lifecycle.configure({ ...second.lifecycle.configuration(), referenceRepositories: {
            consumer: { ...configuration.repositories.common!, slug: "example/consumer", localPath: "reader-consumer", account: "reader-account" },
        } }, actor);
        await second.shared.open(target);
        const local = second.lifecycle.configuration();
        assert.equal(local.referenceRepositories?.consumer?.localPath, "reader-consumer");
        assert.equal(local.referenceRepositories?.consumer?.account, "reader-account");
        assert.equal(local.repositories.consumer, undefined, "opening shared research scope must not promote a reference into a delivery target");
        const priorId = second.lifecycle.store.configuration()!.id;
        const remapped = structuredClone(local);
        remapped.referenceRepositories!.consumer!.localPath = "moved-consumer";
        remapped.referenceRepositories!.consumer!.account = "new-reader";
        second.lifecycle.configure(remapped, actor);
        assert.equal(second.lifecycle.store.configuration()!.id, priorId, "local paths/accounts do not change shared identity");
        assert.equal(second.lifecycle.configuration().referenceRepositories?.consumer?.localPath, "moved-consumer");
        assert.equal(second.lifecycle.configuration().referenceRepositories?.consumer?.account, "new-reader");
        const promoted = { ...local, repositories: { ...local.repositories, consumer: local.referenceRepositories!.consumer! } };
        delete promoted.referenceRepositories;
        second.lifecycle.configure(promoted, actor);
        await assert.rejects(() => second.shared.open(target), /matching repository/);
    } finally { f.cleanup(); }
});

test("confirmed local reference changes keep shared history readable but require separate publication consent", async () => {
    const f = teamFixture();
    try {
        const app = f.create("reference-transition");
        const draft = app.execute({ kind: "create_feature", requestId: "transition", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const run = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, run);
        const packet = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "needs clarification", reasons: ["Missing consumer source"] });
        await app.shared.finishRun(feature.id, run.id);
        app.lifecycle.store.updateExecution(feature.id, run.id, { status: "completed" });
        const before = (await f.provider.read(target))!;
        const configuration = app.lifecycle.configuration();
        await submit(app, { kind: "configure", configuration: { ...configuration,
            referenceRepositories: { consumer: { ...configuration.repositories.common!, slug: "example/consumer", localPath: "consumer" } },
        } });
        const localConfigId = app.lifecycle.store.configuration()!.id;
        const read = await app.feature(feature.id);
        assert.equal(read.configurationMismatch?.code, "shared_mapping_required");
        assert.equal(nextStep(read, []).action?.command?.kind, "update_shared_references");
        assert.equal(read.factory.artifacts[0]?.id, packet.id);
        assert.equal(read.factory.executions[0]?.id, run.id);
        assert.deepEqual(read.approvals, before.snapshot.feature.approvals);
        assert.equal(app.lifecycle.store.configuration()!.id, localConfigId, "reading must not revert the confirmed local connection scope");
        assert.equal((await f.provider.read(target))!.digest, before.digest, "local configuration and reads must not publish to ADO");
        await submit(app, { kind: "report_progress", featureId: feature.id, expectedRevision: feature.current.id, requestId: "transition-progress",
            progress: { stage: "research", status: "blocked", summary: "Review reference sources.", expectedOutput: "Confirmed shared reference scope.", nextStep: "Review shared publication." } });
        assert.equal(app.cachedFeature(feature.id).currentProgress?.requestId, "transition-progress");
        await assert.rejects(() => app.preview({ kind: "start_workflow", featureId: feature.id, workflow: "design" }), /matching repository/);

        const host: ChatHost = { sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } }, ui: { confirm: async () => false } };
        const tools = createChatTools(() => app, () => host);
        const gates = await tools.find((tool) => tool.name === "software_factory_get_gates")!.handler({ featureId: feature.id },
            { sessionId: host.sessionId, toolCallId: "mismatched-gates" });
        assert.ok(Array.isArray(gates) && gates.every((gate) => gate.available === false));
        const command = { kind: "update_shared_references", featureId: feature.id };
        const preview = await app.preview(command);
        assert.match(preview.confirmation!, /Publish.*read-only reference-source scope/);
        await assert.rejects(() => app.submit(command, actor), /Confirm.*reference/);
        await tools.find((tool) => tool.name === "software_factory_command")!.handler({ command },
            { sessionId: host.sessionId, toolCallId: "declined-publication" });
        assert.equal((await f.provider.read(target))!.digest, before.digest);
        const published = await submit(app, command);
        assert.equal(published.outcome, "shared_references_updated");
        const after = (await f.provider.read(target))!;
        assert.equal(after.snapshot.configuration.value.referenceRepositories?.consumer?.slug, "example/consumer");
        assert.deepEqual(after.snapshot.feature, before.snapshot.feature);
        assert.deepEqual(after.snapshot.state.artifacts, before.snapshot.state.artifacts);
        assert.deepEqual(after.snapshot.state.decisions, before.snapshot.state.decisions);
        assert.equal((await app.feature(feature.id)).configurationMismatch, undefined);
        assert.equal(app.lifecycle.store.latest(feature.id, "design"), null, "old-source evidence must not become a current design");
        assert.equal(app.lifecycle.store.state(feature.id).executions.length, 1, "publication starts no run");
        assert.equal(app.lifecycle.store.configuration()!.id, localConfigId);
    } finally { f.cleanup(); }
});

test("shared reference publication rejects stale confirmation, delivery target changes and active claims", async () => {
    const f = teamFixture();
    try {
        const app = f.create("reference-cas");
        const draft = app.execute({ kind: "create_feature", requestId: "cas", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const initial = (await f.provider.read(target))!;
        const id = initial.snapshot.feature.id;
        const configuration = app.lifecycle.configuration();
        const references = { consumer: { ...configuration.repositories.common!, slug: "example/consumer", localPath: "consumer" } };
        app.lifecycle.configure({ ...configuration, referenceRepositories: references }, actor);
        const command = { kind: "update_shared_references", featureId: id };
        const preview = await app.preview(command);
        const remote = structuredClone(initial.snapshot);
        remote.state.events.push({ id: "concurrent-source-review", kind: "external_update", reference: "other", createdAt: new Date().toISOString() });
        await f.provider.write(target, initial, remote, f.provider.user);
        await assert.rejects(() => app.submit(command, actor, { granted: true, revisionId: preview.consentKey!, actor }), /Confirm.*reference/);
        assert.equal((await f.provider.read(target))!.snapshot.configuration.value.referenceRepositories, undefined);
        app.lifecycle.configure({ ...configuration, referenceRepositories: references, boards: { ...configuration.boards, parentId: 999 } }, actor);
        await assert.rejects(() => app.preview(command), /only read-only reference sources/);
        app.lifecycle.configure({ ...configuration, referenceRepositories: references }, actor);
        const current = (await f.provider.read(target))!;
        await f.provider.write(target, current, { ...current.snapshot,
            lock: { id: "another-run", kind: "design", actor: f.provider.user, createdAt: new Date().toISOString() } }, f.provider.user);
        await assert.rejects(() => app.preview(command), /active shared claims/);
    } finally { f.cleanup(); }
});

test("a lost shared reference publication response uses explicit recovery without resending or losing history", async () => {
    const f = teamFixture();
    try {
        const app = f.create("reference-receipt");
        const draft = app.execute({ kind: "create_feature", requestId: "receipt", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const record = (await f.provider.read(target))!;
        const id = record.snapshot.feature.id;
        const configuration = app.lifecycle.configuration();
        app.lifecycle.configure({ ...configuration,
            referenceRepositories: { consumer: { ...configuration.repositories.common!, localPath: "consumer" } },
        }, actor);
        const write = f.provider.write.bind(f.provider);
        let writes = 0;
        f.provider.write = async (...args) => {
            writes++;
            const result = await write(...args);
            throw new Error(`Lost reference response ${result.revision}`);
        };
        const command = { kind: "update_shared_references", featureId: id };
        await assert.rejects(() => submit(app, command), /Lost reference response/);
        assert.equal((await app.feature(id)).recovery?.code, "shared_completion_reconciliation_required");
        await assert.rejects(() => submit(app, command), /reconcile/);
        await submit(app, { kind: "reconcile_shared_completion", featureId: id });
        assert.equal(writes, 1);
        assert.equal((await app.feature(id)).configurationMismatch, undefined);
        assert.equal(app.cachedFeature(id).recovery, undefined);
        assert.deepEqual((await f.provider.read(target))!.snapshot.feature, record.snapshot.feature);
    } finally { f.cleanup(); }
});

test("reference publication CAS preserves an independent edit and blocks automatic retries", async () => {
    const f = teamFixture();
    try {
        const app = f.create("reference-interleaving");
        const draft = app.execute({ kind: "create_feature", requestId: "interleaving", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const record = (await f.provider.read(target))!;
        const id = record.snapshot.feature.id;
        const configuration = app.lifecycle.configuration();
        app.lifecycle.configure({ ...configuration,
            referenceRepositories: { consumer: { ...configuration.repositories.common!, localPath: "consumer" } },
        }, actor);
        const write = f.provider.write.bind(f.provider);
        let attempts = 0;
        f.provider.write = async (targetValue, expected, snapshot, user) => {
            attempts++;
            const current = (await f.provider.read(targetValue))!;
            const concurrent = structuredClone(current.snapshot);
            concurrent.state.events.push({ id: "reference-race", kind: "external_update", reference: "other", createdAt: new Date().toISOString() });
            await write(targetValue, current, concurrent, user);
            return write(targetValue, expected, snapshot, user);
        };
        const command = { kind: "update_shared_references", featureId: id };
        await assert.rejects(() => submit(app, command), /Concurrent ADO edit/);
        assert.equal((await f.provider.read(target))!.snapshot.configuration.value.referenceRepositories, undefined);
        assert.equal((await f.provider.read(target))!.snapshot.state.events.at(-1)?.id, "reference-race");
        assert.equal(app.lifecycle.configuration().referenceRepositories?.consumer?.localPath, "consumer");
        await assert.rejects(() => submit(app, command), /reconcile/);
        await assert.rejects(() => submit(app, { kind: "reconcile_shared_completion", featureId: id }), /changed independently/);
        assert.equal(attempts, 1);
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

test("workflow checkpoints retain the claim, preserve local work across reads, and reject concurrent shared edits", async () => {
    const f = teamFixture();
    try {
        const app = f.create("checkpoint");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const execution = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, execution);
        const artifact = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });

        await app.feature(feature.id);
        assert.equal(app.lifecycle.store.latest(feature.id, "design")?.id, artifact.id,
            "a polling read must not replace uncheckpointed local workflow state");
        await app.shared.open(target);
        assert.equal(app.lifecycle.store.latest(feature.id, "design")?.id, artifact.id,
            "reopening the shared feature must not overwrite an active owner's local work");
        await app.shared.checkpointRun(feature.id, execution.id);
        const checkpoint = (await f.provider.read(target))!;
        assert.equal(checkpoint.snapshot.lock?.id, execution.id);
        assert.equal(checkpoint.snapshot.state.artifacts.at(-1)?.id, artifact.id);
        await app.shared.assertRun(feature.id, execution.id);

        const concurrent = structuredClone(checkpoint.snapshot);
        concurrent.state.events.push({ id: "external", kind: "external_update", reference: "other", createdAt: new Date().toISOString() });
        await f.provider.write(target, checkpoint, concurrent, f.provider.user);
        await assert.rejects(() => app.feature(feature.id), /claim or approved inputs changed/);
        await assert.rejects(() => app.shared.open(target), /claim or approved inputs changed/);
        await assert.rejects(() => app.shared.checkpointRun(feature.id, execution.id), /claim or approved inputs changed/);
        assert.equal((await f.provider.read(target))!.snapshot.state.events.at(-1)?.id, "external");
    } finally { f.cleanup(); }
});

test("completion conflicts survive restart and require explicit, revision-bound reconciliation", async () => {
    const f = teamFixture();
    let reopened: Application | undefined;
    try {
        const app = f.create("completion-conflict");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const execution = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, execution);
        app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });

        const write = f.provider.write.bind(f.provider);
        let changed = false;
        f.provider.write = async (targetValue, expected, snapshot, user) => {
            const result = await write(targetValue, expected, snapshot, user);
            if (!changed && snapshot.lock === null) {
                changed = true;
                app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "NO-GO", reason: "Late evidence" });
            }
            return result;
        };
        await assert.rejects(() => app.shared.finishRun(feature.id, execution.id), /changed during completion/);
        const database = app.registry.databasePath;
        const workspace = app.registry.project.workspace;
        app.close();
        reopened = new Application({ database, workspace, sharedProvider: f.provider });
        const blocked = await reopened.feature(feature.id);
        assert.equal(blocked.recovery?.code, "shared_completion_reconciliation_required");
        assert.deepEqual(blocked.recovery?.command, { kind: "reconcile_shared_completion", featureId: feature.id });
        assert.equal(blocked.factory.artifacts.length, 2, "failed completion must leave its exact local evidence inspectable");
        assert.equal((await reopened.features()).find((entry) => entry.id === feature.id)?.recovery?.code, blocked.recovery?.code);
        assert.deepEqual(reopened.cachedFeature(feature.id).recovery, blocked.recovery);
        assert.equal(nextStep(blocked, []).action?.command?.kind, "reconcile_shared_completion");
        await assert.rejects(() => reopened!.shared.pull(feature.id), /reconcile/);
        await assert.rejects(() => reopened!.shared.open(target), /reconcile/);
        await assert.rejects(() => reopened!.preview({ kind: "start_workflow", featureId: feature.id, workflow: "plan" }), /reconcile/);
        assert.equal(reopened.lifecycle.store.state(feature.id).artifacts.length, 2);
        assert.equal((await f.provider.read(target))!.snapshot.state.artifacts.length, 1);

        const command = { kind: "reconcile_shared_completion", featureId: feature.id };
        const preview = await reopened.preview(command);
        await assert.rejects(() => reopened!.submit(command, actor), /Confirm/);
        await reopened.submit(command, actor, { granted: true, revisionId: preview.consentKey!, actor });
        assert.equal((await f.provider.read(target))!.snapshot.state.artifacts.length, 2);
        await reopened.feature(feature.id);
        assert.equal(reopened.lifecycle.store.state(feature.id).artifacts.length, 2);
    } finally { reopened?.close(); f.cleanup(); }
});

test("local execution measurements, traces and conversational progress cannot invalidate shared completion", async () => {
    const f = teamFixture();
    try {
        const app = f.create("completion-local-observations");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const execution = app.lifecycle.begin(feature.id, "design", actor);
        app.lifecycle.store.updateExecution(feature.id, execution.id, { runtimeId: "native-run", status: "running" });
        await app.shared.claimRun(feature.id, execution);
        const artifact = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        const updatedAt = "2026-01-01T00:00:00.000Z";
        app.registry.connection.prepare("UPDATE features SET updated_at=? WHERE id=?").run(updatedAt, feature.id);
        const snapshot = app.shared.snapshot(app, feature.id);
        let updates = 0;
        const observe = () => {
            updates++;
            app.lifecycle.reportProgress(feature.id, feature.current.id, `handoff-${updates}`, {
                stage: "design", status: "idle", summary: "Native design is running.", expectedOutput: "A reviewed design.",
                nextStep: "Inspect the owning native run.",
            }, actor);
            app.lifecycle.store.updateExecution(feature.id, execution.id, {
                phase: `Observation ${updates}`, usage: {
                    observedAt: Date.now(), activeMs: updates, subagents: 1, nanoAiu: 7380000000,
                    declaredLimits: {}, approvedLimits: null, agents: [],
                },
            });

            app.lifecycle.store.recordTrace(feature.id, execution.id, {
                id: `last-read-${updates}`, kind: "tool_call", timestamp: new Date().toISOString(),
                tool: "software_factory_read", target: "common\\A.kt", status: "completed",
            });
            assert.deepEqual(app.shared.snapshot(app, feature.id), snapshot);
        };
        const identity = f.provider.identity.bind(f.provider);
        f.provider.identity = async () => { observe(); return identity(); };
        const write = f.provider.write.bind(f.provider);
        f.provider.write = async (...args) => { const result = await write(...args); observe(); return result; };
        await app.shared.finishRun(feature.id, execution.id);
        assert.equal(updates, 2, "exercise both the pre-send and in-flight write windows");
        assert.equal(app.registry.get(feature.id).updatedAt, updatedAt);
        assert.equal(app.lifecycle.store.latest(feature.id, "design")?.id, artifact.id);
        assert.equal(app.lifecycle.store.state(feature.id).progress.length, 2);
        assert.equal(app.lifecycle.store.execution(feature.id, execution.id).trace?.length, 2);
        assert.equal((await f.provider.read(target))!.snapshot.lock, null);
        assert.equal((await app.feature(feature.id)).recovery, undefined);
        assert.throws(() => app.shared.completionScope(feature.id), /no pending shared completion/i);
    } finally { f.cleanup(); }
});

test("recovery review inventories large snapshots without weakening exact-scope confirmation", async () => {
    const f = teamFixture();
    try {
        const app = f.create("recovery-review");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const execution = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, execution);
        const rawEvidence = "Raw research evidence retained for inspection. ".repeat(6000);
        const artifact = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, {
            recommendation: "NO-GO", research: rawEvidence, reason: "Wrong-source research must not become an approved design.",
        });
        const write = f.provider.write.bind(f.provider);
        f.provider.write = async () => { throw new Error("Uncertain completion"); };
        await assert.rejects(() => app.shared.finishRun(feature.id, execution.id), /Uncertain completion/);
        f.provider.write = write;
        const command = { kind: "reconcile_shared_completion", featureId: feature.id };
        const preview = await app.preview(command);
        assert.ok(JSON.stringify(preview.scope).length > 150_000);
        const review = preview.decision!.review;
        assert.ok(review.length < 12_000, `Expected a readable recovery review, got ${review.length} characters`);
        assert.match(review, /does not validate the evidence, approve a design/);
        assert.match(review, /not an accepted design/);
        assert.ok(review.includes(artifact.id) && review.includes(artifact.hash) && review.includes("NO-GO"));
        assert.ok(review.includes(execution.id) && review.includes("example/Engineering / ADO Feature 123"));
        assert.ok(!review.includes(rawEvidence));
        const preserved = object(preview.scope, "Recovery scope");
        const local = object(preserved.local, "Local snapshot");
        const state = object(local.state, "Local lifecycle");
        assert.equal(object(object((state.artifacts as unknown[])[0], "Artifact").content, "Artifact content").research, rawEvidence);
        let accept = false;
        let forms = 0;
        const host: ChatHost = { sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
            ui: { confirm: async () => false, elicitation: async () => {
                forms++; return { action: "accept", content: { decision: accept ? "approve" : "review" } };
            } } };
        const tools = createChatTools(() => app, () => host);
        const request = tools.find((tool) => tool.name === "software_factory_command")!;
        const decide = tools.find((tool) => tool.name === "software_factory_decide_after_review")!;
        const result = object(await request.handler({ command }, { sessionId: host.sessionId, toolCallId: "recovery-review" }), "Review result");
        assert.equal(result.outcome, "review_required");
        assert.equal(result.review, review);
        assert.ok(app.cachedFeature(feature.id).recovery, "requesting the review cannot clear recovery");
        const oldAction = object(result.nextAction, "Next action");
        app.lifecycle.store.artifact(feature.id, "design", feature.current.id, {
            recommendation: "NO-GO", research: rawEvidence, reason: "New evidence arrived after review.",
        });
        accept = true;
        await assert.rejects(async () => decide.handler(oldAction.arguments, { sessionId: host.sessionId, toolCallId: "stale-recovery-review" }), /reviewed scope changed/);
        assert.equal(forms, 1, "stale evidence must not even open an approval form");
        accept = false;
        const fresh = object(await request.handler({ command }, { sessionId: host.sessionId, toolCallId: "fresh-recovery-review" }), "Fresh review");
        accept = true;
        const action = object(fresh.nextAction, "Fresh action");
        await decide.handler(action.arguments, { sessionId: host.sessionId, toolCallId: "confirmed-recovery" });
        assert.equal(app.cachedFeature(feature.id).recovery, undefined);
        assert.equal(app.lifecycle.store.state(feature.id).executions.length, 1, "recovery cannot start another run");
        assert.equal(app.lifecycle.gate(feature.id, "design").approved, false, "preserving NO-GO evidence cannot approve design");
    } finally { f.cleanup(); }
});

test("lost completion receipts reconcile without a duplicate write and reject independent shared changes", async () => {
    const f = teamFixture();
    try {
        const app = f.create("completion-receipt");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const execution = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, execution);
        app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        const write = f.provider.write.bind(f.provider);
        let writes = 0;
        f.provider.write = async (targetValue, expected, snapshot, user) => {
            const result = await write(targetValue, expected, snapshot, user);
            writes++;
            if (writes === 1) throw new Error("Response lost after completion.");
            return result;
        };
        await assert.rejects(() => app.shared.finishRun(feature.id, execution.id), /Response lost/);
        await submit(app, { kind: "reconcile_shared_completion", featureId: feature.id });
        assert.equal(writes, 1, "an attested completed write must not be repeated");

        app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "completed" });
        const next = app.lifecycle.begin(feature.id, "intake", actor);
        await app.shared.claimRun(feature.id, next);
        const originalWrite = f.provider.write.bind(f.provider);
        let loseResponse = true;
        f.provider.write = async (targetValue, expected, snapshot, user) => {
            const result = await originalWrite(targetValue, expected, snapshot, user);
            if (loseResponse && snapshot.lock === null) { loseResponse = false; throw new Error("Lost another response."); }
            return result;
        };
        await assert.rejects(() => app.shared.finishRun(feature.id, next.id), /Lost another response/);
        const remote = (await f.provider.read(target))!;
        const changed = structuredClone(remote.snapshot);
        changed.state.events.push({ id: "external-completion", kind: "external_update", reference: "other", createdAt: new Date().toISOString() });
        await write(target, remote, changed, f.provider.user);
        await assert.rejects(() => submit(app, { kind: "reconcile_shared_completion", featureId: feature.id }), /changed independently/);
        await assert.rejects(() => app.shared.pull(feature.id), /reconcile/);
        assert.equal((await f.provider.read(target))!.snapshot.state.events.at(-1)?.id, "external-completion");
    } finally { f.cleanup(); }
});

test("pre-write failures preserve evidence and stale recovery consent cannot publish it", async () => {
    const f = teamFixture();
    try {
        const app = f.create("completion-prewrite");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const execution = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, execution);
        app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        const write = f.provider.write.bind(f.provider);
        f.provider.write = async () => { throw new Error("Write failed before sending."); };
        await assert.rejects(() => app.shared.finishRun(feature.id, execution.id), /before sending/);
        f.provider.write = write;

        const command = { kind: "reconcile_shared_completion", featureId: feature.id };
        const preview = await app.preview(command);
        app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "NO-GO", reason: "New local evidence" });
        await assert.rejects(() => app.submit(command, actor, { granted: true, revisionId: preview.consentKey!, actor }), /Confirm/);
        assert.equal((await f.provider.read(target))!.snapshot.state.artifacts.length, 0);
        await submit(app, command);
        assert.equal((await f.provider.read(target))!.snapshot.state.artifacts.length, 2);
    } finally { f.cleanup(); }
});

test("recovery compare-and-swap rejects a concurrent shared write without losing either version", async () => {
    const f = teamFixture();
    try {
        const app = f.create("completion-interleaving");
        const draft = app.execute({ kind: "create_feature", requestId: "draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const execution = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, execution);
        const artifact = app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        const write = f.provider.write.bind(f.provider);
        f.provider.write = async () => { throw new Error("Write failed before sending."); };
        await assert.rejects(() => app.shared.finishRun(feature.id, execution.id), /before sending/);
        f.provider.write = async (targetValue, expected, snapshot, user) => {
            const current = (await f.provider.read(targetValue))!;
            const concurrent = structuredClone(current.snapshot);
            concurrent.state.events.push({ id: "concurrent-recovery", kind: "external_update", reference: "other", createdAt: new Date().toISOString() });
            await write(targetValue, current, concurrent, user);
            return write(targetValue, expected, snapshot, user);
        };
        await assert.rejects(() => submit(app, { kind: "reconcile_shared_completion", featureId: feature.id }), /Concurrent ADO edit/);
        assert.equal(app.lifecycle.store.latest(feature.id, "design")?.id, artifact.id);
        assert.equal((await f.provider.read(target))!.snapshot.state.events.at(-1)?.id, "concurrent-recovery");
        f.provider.write = write;
        await assert.rejects(() => submit(app, { kind: "reconcile_shared_completion", featureId: feature.id }), /changed independently/);
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
        const attempts = readdirSync(path.dirname(app.registry.databasePath)).filter((name) => name.startsWith("shared-attempt-"));
        assert.deepEqual(attempts, [], "successful shared commands must remove temporary attempt directories");
        const versionBefore = Number(app.registry.connection.prepare("SELECT version FROM factory_state WHERE feature_id=?").get(feature.id)!.version);
        await app.feature(feature.id);
        const versionAfter = Number(app.registry.connection.prepare("SELECT version FROM factory_state WHERE feature_id=?").get(feature.id)!.version);
        assert.equal(versionAfter, versionBefore, "unchanged shared pulls must not churn factory_state versions");
    } finally {
        await appRef?.shutdown();
        f.cleanup();
    }
});

test("pre-admission startup failure automatically releases its unstarted ADO shared claim", async () => {
    const f = teamFixture();
    let appRef: Application | undefined;
    try {
        const app = f.create("pre-admission-fail");
        const ws = app.registry.project.workspace;
        app.lifecycle.configure(teamConfig(".", "pre-admission-fail"), actor);
        execFileSync("git", ["init", "--quiet", ws]);
        execFileSync("git", ["-C", ws, "config", "user.email", "test@example.com"]);
        execFileSync("git", ["-C", ws, "config", "user.name", "Test"]);
        writeFileSync(path.join(ws, "README.md"), "fixture\n");
        execFileSync("git", ["-C", ws, "add", "README.md"]);
        execFileSync("git", ["-C", ws, "commit", "--quiet", "-m", "init"]);
        appRef = app;
        const draft = app.execute({ kind: "create_feature", requestId: "pre-admission-fail", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });
        const session = {
            sessionId: actor.sessionId!,
            rpc: {
                extensions: { list: async () => ({ extensions: [{ id: "project:SoftwareFactory", name: "SoftwareFactory", source: "project" as const, status: "disabled" as const }] }), reload: async () => {} },
                tools: {
                    initializeAndValidate: async () => ({ toolNames: [] }),
                    getCurrentMetadata: async () => ({ tools: [] }),
                    execute: async () => { throw new Error("unexpected"); },
                },
            },
            workflow: {
                run: async () => { throw new Error("unexpected"); },
                resume: async () => { throw new Error("unexpected"); },
                pause: async () => { throw new Error("unexpected"); },
                cancel: async () => { throw new Error("unexpected"); },
                getRun: async () => { throw new Error("unexpected"); },
                getRunDetail: async () => { throw new Error("unexpected"); },
            },
        };
        await app.bindChatSession(session);
        await submit(app, { kind: "start_workflow", featureId: feature.id, workflow: "design" });
        const deadline = Date.now() + 10_000;
        while (Date.now() < deadline) {
            const current = app.lifecycle.store.state(feature.id).executions.at(-1)!;
            const shared = await f.provider.read(target);
            if (current.status === "error" && shared?.snapshot.lock === null) break;
            await new Promise((resolve) => setTimeout(resolve, 50));
        }
        const failed = app.lifecycle.store.state(feature.id).executions.at(-1)!;
        assert.equal(failed.status, "error");
        assert.equal(failed.runtimeId, null);
        assert.equal((await f.provider.read(target))!.snapshot.lock, null, "unstarted failed execution must release its ADO lock");
    } finally {
        await appRef?.shutdown();
        f.cleanup();
    }
});

test("combined publish_feature can create an ADO Feature, record authenticated scope approval, and start design in one confirmed action", async () => {
    const f = teamFixture();
    let appRef: Application | undefined;
    try {
        const app = f.create("combined-publish");
        const ws = app.registry.project.workspace;
        app.lifecycle.configure(teamConfig(".", "combined-publish"), actor);
        execFileSync("git", ["init", "--quiet", ws]);
        execFileSync("git", ["-C", ws, "config", "user.email", "test@example.com"]);
        execFileSync("git", ["-C", ws, "config", "user.name", "Test"]);
        writeFileSync(path.join(ws, "README.md"), "fixture\n");
        execFileSync("git", ["-C", ws, "add", "README.md"]);
        execFileSync("git", ["-C", ws, "commit", "--quiet", "-m", "init"]);
        appRef = app;
        const draft = app.execute({ kind: "create_feature", requestId: "combined-draft", input: requirements() }, actor);
        const sessionHash = createHash("sha256").update(actor.sessionId!).digest("hex");
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
                run: async () => { nativeRuns++; return { runId: "combined-run-1", status: "completed" as const, result: { ok: true } }; },
                resume: async () => { throw new Error("unexpected"); },
                pause: async () => { throw new Error("unexpected"); },
                cancel: async () => { throw new Error("unexpected"); },
                getRun: async () => { throw new Error("unexpected"); },
                getRunDetail: async () => ({
                    runId: "combined-run-1", workflowName: "software-factory-design", description: "Design", status: "completed" as const,
                    revision: 1, createdAt: Date.now(), startedAt: Date.now(), updatedAt: Date.now(), completedAt: Date.now(),
                    currentPhase: null, declaredPhaseCount: 1, liveAgentCount: 0, totalSpawnedAgentCount: 0,
                    consumed: { subagents: 0, activeMs: 1, nanoAiu: 0 },
                    declaredLimits: {}, approved: null, observedAt: Date.now(), activeSegmentStartedAt: null,
                    terminal: null, canResume: false, phases: [], agents: [],
                    progress: { records: [], oldestSeq: null, newestSeq: null, hasMoreOlder: false, hasMoreNewer: false, revision: 1 },
                }),
            },
        };
        await app.bindChatSession(session);
        const result = await submit(app, {
            kind: "publish_feature",
            featureId: draft.id,
            target: { create: true },
            approve: true,
            startDesign: true,
        });
        assert.equal(result.outcome, "workflow_requested");
        const published = result.feature as { id: string; status: string; shared: { target: { organization: string; project: string; workItemId: number }; revision: number } };
        assert.equal(published.status, "ready_for_design");
        assert.ok(published.shared.target.workItemId >= 1001);
        const deadline = Date.now() + 10_000;
        while (Date.now() < deadline) {
            const current = app.lifecycle.store.state(published.id).executions.at(-1)!;
            if (current.status === "completed" || current.status === "error") break;
            await new Promise((resolve) => setTimeout(resolve, 50));
        }
        const finalExecution = app.lifecycle.store.state(published.id).executions.at(-1)!;
        assert.equal(finalExecution.status, "completed", finalExecution.error || finalExecution.status);
        assert.equal(nativeRuns, 1);
        const adoRecord = await f.provider.read(published.shared.target);
        assert.equal(adoRecord?.snapshot.feature.approvals.length, 1);
        assert.equal(adoRecord?.snapshot.feature.approvals[0]?.actor.ado?.id, "alice");

        // Continuing the feature in a new chat session on the same registry rebinds ownership to the new chat instead of queuing to the old chat.
        const secondApp = new Application({ database: app.registry.databasePath, workspace: ws, sharedProvider: f.provider });
        let secondChatRuns = 0;
        const secondSessionId = "second-chat-session";
        const secondHash = createHash("sha256").update(secondSessionId).digest("hex");
        const secondSession = {
            ...session,
            sessionId: secondSessionId,
            rpc: {
                ...session.rpc,
                tools: {
                    ...session.rpc.tools,
                    execute: async () => ({
                        resultType: "success" as const,
                        textResultForLlm: JSON.stringify({
                            ready: true, sessionId: "******", sessionIdHash: secondHash,
                            workflowNames: ["software-factory-check"], runtimeFingerprint: runtimeFingerprint(), readerAgent: FACTORY_READER,
                        }),
                    }),
                },
            },
            workflow: {
                ...session.workflow,
                run: async () => { secondChatRuns++; return { runId: "second-chat-run-1", status: "completed" as const, result: { ok: true } }; },
            },
        };
        try {
            assert.equal(secondApp.lifecycle.store.state(published.id).sessionBinding?.sessionId, actor.sessionId);
            await secondApp.bindChatSession(secondSession);
            assert.deepEqual(secondApp.executionOwner(published.id), { sessionId: secondSessionId, kind: "chat" });
            const checkRun = await (await secondApp.runtime()).start(published.id, "check", {
                channel: "chat", reference: "continue-in-new-chat", sessionId: secondSessionId,
            });
            assert.equal(checkRun.sessionId, secondSessionId);
            const untilSecond = Date.now() + 10_000;
            while (Date.now() < untilSecond) {
                const current = secondApp.lifecycle.store.execution(published.id, checkRun.id);
                if (current.status === "completed" || current.status === "error") break;
                await new Promise((resolve) => setTimeout(resolve, 50));
            }
            const completedSecond = secondApp.lifecycle.store.execution(published.id, checkRun.id);
            assert.equal(completedSecond.status, "completed", completedSecond.error || completedSecond.status);
            assert.equal(secondChatRuns, 1);
            assert.equal(secondApp.lifecycle.store.state(published.id).sessionBinding?.sessionId, secondSessionId);
        } finally {
            await secondApp.shutdown();
        }
    } finally {
        await appRef?.shutdown();
        f.cleanup();
    }
});

test("terminal non-resumable workflow errors release and reclaim clean ADO locks while preserving resumable limit locks", async () => {
    const f = teamFixture();
    let appRef: Application | undefined;
    try {
        const app = f.create("terminal-error-release");
        app.lifecycle.configure(teamConfig(".", "terminal-error-release"), actor);
        appRef = app;
        const draft = app.execute({ kind: "create_feature", requestId: "terminal-error-draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });

        // 1. Pre-existing settled terminal error lock (e.g., local_base_changed) is reclaimed cleanly by claimRun.
        const failedExec = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, failedExec);
        app.lifecycle.store.updateExecution(feature.id, failedExec.id, {
            runtimeId: "run-local-base-changed",
            status: "error",
            errorCode: "local_base_changed",
            error: "The live base branch changed after review; a new review is required.",
        });
        assert.equal((await f.provider.read(target))!.snapshot.lock?.id, failedExec.id);

        const retryExec = app.lifecycle.begin(feature.id, "design", actor);
        await app.shared.claimRun(feature.id, retryExec);
        const afterReclaim = (await f.provider.read(target))!;
        assert.equal(afterReclaim.snapshot.lock?.id, retryExec.id);
        assert.ok(afterReclaim.snapshot.state.events.some((e) => e.kind === "shared_claim_released_failed" && e.reference === failedExec.id));

        // 2. Resumable workflow_limit_reached error MUST keep its ADO lock so resume remains guarded.
        app.lifecycle.store.updateExecution(feature.id, retryExec.id, {
            runtimeId: "run-limit-reached",
            status: "error",
            error: JSON.stringify({ type: "workflow_limit_reached", limit: "maxTotalSubagents" }),
        });
        const blockedSuccessor = app.lifecycle.begin(feature.id, "design", actor);
        await assert.rejects(() => app.shared.claimRun(feature.id, blockedSuccessor), /Another shared operation owns this feature/);
        assert.equal((await f.provider.read(target))!.snapshot.lock?.id, retryExec.id);
    } finally {
        await appRef?.shutdown();
        f.cleanup();
    }
});

test("reconnected chat controller automatically resumes an idle-evicted halted workflow", async () => {
    const f = teamFixture();
    let appRef: Application | undefined;
    try {
        const app = f.create("reconnect-auto-resume");
        const ws = app.registry.project.workspace;
        app.lifecycle.configure(teamConfig(".", "reconnect-auto-resume"), actor);
        execFileSync("git", ["init", "--quiet", ws]);
        execFileSync("git", ["-C", ws, "config", "user.email", "test@example.com"]);
        execFileSync("git", ["-C", ws, "config", "user.name", "Test"]);
        writeFileSync(path.join(ws, "README.md"), "fixture\n");
        execFileSync("git", ["-C", ws, "add", "README.md"]);
        execFileSync("git", ["-C", ws, "commit", "--quiet", "-m", "init"]);
        appRef = app;

        const draft = app.execute({ kind: "create_feature", requestId: "reconnect-draft", input: requirements() }, actor);
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const feature = (await f.provider.read(target))!.snapshot.feature;
        await submit(app, { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id });

        const execution = app.lifecycle.begin(
            feature.id, "design", actor, {}, {}, undefined,
            { sessionId: actor.sessionId!, kind: "chat", established: true },
            "PR observer (PBI-1): Observe",
        );
        await app.shared.claimRun(feature.id, execution);
        app.lifecycle.store.updateExecution(feature.id, execution.id, {
            runtimeId: "halted-run-1",
            status: "running",
            phase: "PR observer (PBI-1): Observe",
        });

        const sessionHash = createHash("sha256").update(actor.sessionId!).digest("hex");
        let resumeCount = 0;
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
                run: async () => { throw new Error("unexpected run"); },
                resume: async (runId: string) => {
                    assert.equal(runId, "halted-run-1");
                    resumeCount++;
                    return { runId, status: "completed" as const, result: { resumed: true } };
                },
                pause: async () => { throw new Error("unexpected pause"); },
                cancel: async () => { throw new Error("unexpected cancel"); },
                getRun: async () => ({ runId: "halted-run-1", status: "halted" as const }),
                getRunDetail: async () => ({
                    runId: "halted-run-1", workflowName: "software-factory-design", description: "Design", status: "completed" as const,
                    revision: 2, createdAt: Date.now(), startedAt: Date.now(), updatedAt: Date.now(), completedAt: Date.now(),
                    currentPhase: null, declaredPhaseCount: 1, liveAgentCount: 0, totalSpawnedAgentCount: 0,
                    consumed: { subagents: 1, activeMs: 10, nanoAiu: 0 },
                    declaredLimits: {}, approved: null, observedAt: Date.now(), activeSegmentStartedAt: null,
                    terminal: null, canResume: false, phases: [], agents: [],
                    progress: { records: [], oldestSeq: null, newestSeq: null, hasMoreOlder: false, hasMoreNewer: false, revision: 2 },
                }),
            },
        };

        await app.bindChatSession(session);
        const deadline = Date.now() + 10_000;
        while (Date.now() < deadline) {
            const current = app.lifecycle.store.execution(feature.id, execution.id);
            if (current.status === "completed" || current.status === "error") break;
            await new Promise((resolve) => setTimeout(resolve, 50));
        }
        const settled = app.lifecycle.store.execution(feature.id, execution.id);
        assert.equal(settled.status, "completed", settled.error || settled.status);
        assert.equal(resumeCount, 1);
    } finally {
        await appRef?.shutdown();
        f.cleanup();
    }
});

test("feature listings hide superseded pre-publish local drafts and uncommitted publish attempts while surfacing new local edits", async () => {
    const f = teamFixture();
    try {
        const app = f.create("dedupe-published-drafts");
        const draft = app.execute({
            kind: "create_feature",
            requestId: "draft-1",
            input: { ...requirements(), title: "Duplicate Draft Test" },
        }, actor);
        assert.deepEqual(app.cachedFeatures().map((entry) => entry.id), [draft.id]);
        assert.deepEqual((await app.features()).map((entry) => entry.id), [draft.id]);

        // A failed publish attempt leaves an uncommitted publish:... row without hiding the draft.
        f.provider.canEdit = false;
        const failedTarget = { ...target, workItemId: 999 };
        await assert.rejects(() => app.shared.publish(draft.id, failedTarget, actor), /permission denied/i);
        assert.ok(app.registry.list().length >= 2, "failed publish created an uncommitted publish:... row in registry");
        assert.deepEqual(app.cachedFeatures().map((entry) => entry.id), [draft.id]);
        assert.deepEqual((await app.features()).map((entry) => entry.id), [draft.id]);

        // Publishing to ADO hides the untouched pre-publish local draft and surfaces only the ADO-backed feature.
        f.provider.canEdit = true;
        await submit(app, { kind: "publish_feature", featureId: draft.id, target });
        const published = (await f.provider.read(target))!.snapshot.feature;
        assert.notEqual(published.id, draft.id);
        assert.deepEqual(app.cachedFeatures().map((entry) => entry.id), [published.id]);
        assert.deepEqual((await app.features()).map((entry) => entry.id), [published.id]);
        assert.equal(app.registry.get(draft.id).id, draft.id, "historical draft remains directly addressable for audit");

        const assets = mkdtempSync(path.join(os.tmpdir(), "dedupe-assets-"));
        writeFileSync(path.join(assets, "index.html"), "<!doctype html><title>Software Factory</title>");
        const server = await startServer({ application: app, assets });
        try {
            const sessionRes = await fetch(`${server.url}/api/session`);
            const cookie = sessionRes.headers.get("set-cookie")?.split(";")[0] || "";
            const fullRes = await fetch(`${server.url}/api/features`, { headers: { Cookie: cookie } });
            const cachedRes = await fetch(`${server.url}/api/features?cached=1`, { headers: { Cookie: cookie } });
            const fullPayload = await fullRes.json() as { features: Array<{ id: string }> };
            const cachedPayload = await cachedRes.json() as { features: Array<{ id: string }> };
            assert.deepEqual(fullPayload.features.map((entry) => entry.id), [published.id]);
            assert.deepEqual(cachedPayload.features.map((entry) => entry.id), [published.id]);
        } finally {
            await server.close();
            rmSync(assets, { recursive: true, force: true });
        }

        // If the local draft is explicitly revised later, it resurfaces so unsaved local edits are never hidden.
        const revised = app.execute({
            kind: "revise_requirements",
            featureId: draft.id,
            expectedRevision: draft.current.id,
            input: { ...requirements(), title: "Duplicate Draft Test (local fork)" },
        }, actor);
        assert.notEqual(revised.current.id, draft.current.id);
        assert.deepEqual(new Set(app.cachedFeatures().map((entry) => entry.id)), new Set([published.id, draft.id]));
        assert.deepEqual(new Set((await app.features()).map((entry) => entry.id)), new Set([published.id, draft.id]));
    } finally {
        f.cleanup();
    }
});
