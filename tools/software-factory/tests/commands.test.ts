// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { writeFileSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { createCommands, type CommandHost } from "../chat/commands.ts";
import { actor, fixture, requirements } from "./helpers.ts";
import { sessionConfiguration } from "../runtime/host.ts";
import { FactoryError, object } from "../core/contracts.ts";
import { Application } from "../core/application.ts";

test("explicit user slash confirmation supports chat-only operation without elicitation or a browser", async () => {
    const f = fixture();
    const logs: string[] = [];
    const sent: string[] = [];
    const host: CommandHost = {
        sessionId: "owning-session", capabilities: {},
        ui: { confirm: async () => { throw new Error("No dialog is available in this host."); } },
        log: async (message) => { logs.push(message); },
        send: async (message) => { sent.push(message.prompt); },
    };
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "one", input: requirements() }, actor);
        const input = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        const handler = createCommands(() => f.app, () => host)[0]!.handler;
        const context = (args: string) => ({ sessionId: host.sessionId, commandName: "software-factory", command: `/software-factory ${args}`, args });
        await handler(context(`preview ${JSON.stringify(input)}`));
        const preview: { consentKey: string } = JSON.parse(logs.at(-1)!);
        assert.equal(f.app.registry.get(feature.id).status, "requirements_review");
        await assert.rejects(async () => handler(context(`confirm ${"0".repeat(64)} ${JSON.stringify(input)}`)), /scope hash/);
        await handler(context(`confirm ${preview.consentKey} ${JSON.stringify(input)}`));
        assert.equal(f.app.registry.get(feature.id).status, "ready_for_design");
        const alias = createCommands(() => f.app, () => host).find((command) => command.name === "feature-plan")!;
        await alias.handler({ ...context(feature.id), commandName: "feature-plan" });
        assert.match(sent[0]!, /native Dynamic Workflows/);
    } finally { f.cleanup(); }
});

test("historical import is read-only until confirmed and never trusts legacy approval", async () => {
    const f = fixture();
    const file = path.join(f.directory, "legacy.json");
    const original = JSON.stringify({ features: [{ id: "old", name: "Legacy feature", artifacts: { designSpec: { status: "approved" } } }] });
    writeFileSync(file, original);
    try {
        const preview = await f.app.dispatch({ kind: "preview_import", path: file }, actor);
        assert.equal(f.app.registry.list().length, 0);
        const command = { kind: "import_legacy", path: file, expectedHash: preview.hash, mappings: [{ legacyId: "old", input: requirements() }] };
        const consent = f.app.prepare(command).consentKey!;
        await assert.rejects(() => f.app.dispatch(command, actor), /confirmation/);
        await f.app.dispatch(command, actor, { granted: true, revisionId: consent, actor });
        assert.equal(f.app.registry.list()[0]?.status, "requirements_review");
        assert.equal(f.app.registry.list()[0]?.approvals.length, 0);
        assert.equal(readFileSync(file, "utf8"), original);
        writeFileSync(file, `${original}\n`);
        assert.throws(() => f.app.prepare(command), /changed/);
    } finally { f.cleanup(); }
});

test("the native worker policy denies mutation, managed approval and out-of-scope reads", async () => {
    const f = fixture();
    try {
        const policy = sessionConfiguration(f.workspace, "Policy test").onPermissionRequest!;
        const invocation = { sessionId: "policy-session" };
        const inside = path.join(f.workspace, "A.kt");
        writeFileSync(inside, "// fixture");
        const allowed = await policy({ kind: "read", path: inside, intention: "Read approved code" }, invocation);
        assert.equal(allowed.kind, "approve-once");
        const denied = await policy({ kind: "read", path: f.options.database, intention: "Read outside scope" }, invocation);
        assert.equal(denied.kind, "reject");
        const managed = await policy({ kind: "read", path: inside, intention: "Managed read", managedApprovalRequired: true }, invocation);
        assert.equal(managed.kind, "reject");
        const bypass = await policy({ kind: "read", path: inside, intention: "Bypass", requestSandboxBypass: true }, invocation);
        assert.equal(bypass.kind, "reject");
    } finally { f.cleanup(); }
});

test("command scope cannot be replayed after its requirements revision changes", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "one", input: requirements() }, actor);
        const command = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        const scope = f.app.prepare(command).consentKey!;
        f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id, input: { ...requirements(), title: "Changed" } }, actor);
        await assert.rejects(() => f.app.dispatch(command, actor, { granted: true, revisionId: scope, actor }),
            (error: unknown) => error instanceof FactoryError && error.code === "stale_revision");
    } finally { f.cleanup(); }
});

test("consistent backups restore only into an isolated clone and disable scheduled execution", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "backup", input: requirements() }, actor);
        f.app.lifecycle.store.change(feature.id, (state) => {
            state.schedule = { minutes: 1, nextAt: new Date().toISOString(), actor, scopeHash: "fixture-scope" };
            state.sessionBinding = { sessionId: "original-chat", kind: "chat", established: true };
        });
        f.app.lifecycle.store.heartbeat("original-chat", "original-owner");

        const command = { kind: "backup_registry" };
        const backup = await f.app.dispatch(command, actor, { granted: true, revisionId: f.app.prepare(command).consentKey!, actor });
        assert.ok(backup.backup && typeof backup.backup === "object" && !Array.isArray(backup.backup));
        const snapshot = backup.backup;
        assert.equal(typeof snapshot.path, "string");
        assert.equal(typeof snapshot.hash, "string");
        f.app.execute({
            kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Changes after backup" },
        }, actor);
        const restore = { kind: "restore_backup_clone", path: snapshot.path, expectedHash: snapshot.hash, originalHostsStopped: true };
        const restored = await f.app.dispatch(restore, actor, { granted: true, revisionId: f.app.prepare(restore).consentKey!, actor });
        assert.ok(restored.restored && typeof restored.restored === "object" && !Array.isArray(restored.restored));
        assert.equal(restored.restored.liveRegistryReplaced, false);
        assert.equal(typeof restored.restored.registryPath, "string");
        const clone = new Application({ database: String(restored.restored.registryPath), workspace: f.workspace });
        try {
            assert.equal(clone.registry.get(feature.id).current.title, feature.current.title);
            assert.equal(clone.lifecycle.store.state(feature.id).schedule, null);
            assert.equal(clone.lifecycle.store.state(feature.id).sessionBinding, null);
            assert.equal(clone.lifecycle.store.ownerOnline("original-chat"), false);
            assert.equal(f.app.registry.get(feature.id).current.title, "Changes after backup");
        } finally { clone.close(); }
    } finally { f.cleanup(); }
});

test("late runtime callbacks cannot revive cancelled or superseded execution authority", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "authority", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const execution = f.app.lifecycle.begin(feature.id, "design", actor);
        f.app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "cancelled" });
        assert.throws(() => f.app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "running" }), /cannot be reactivated/);
        assert.throws(() => f.app.lifecycle.assertFresh(feature.id, execution.id), /changed/);
        assert.throws(() => f.app.lifecycle.store.artifact(feature.id, "design", feature.current.id,
            { recommendation: "GO to Planning" }, null, execution.id), /authority changed/);
        assert.equal(f.app.lifecycle.store.execution(feature.id, execution.id).status, "cancelled");
    } finally { f.cleanup(); }
});

test("credit ceilings are opt-in and explicit uncapping remains an exact protected command", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "uncapped", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id },
            actor, { granted: true, revisionId: feature.current.id, actor });
        const start = { kind: "start_workflow", featureId: feature.id, workflow: "design" };
        const uncapped = f.app.prepare(start);
        assert.deepEqual(object(uncapped.scope, "Execution scope").userRequestedLimits, {});
        assert.equal(object(uncapped.scope, "Execution scope").effectiveAiCreditLimit, null);
        assert.ok(uncapped.decision!.facts.some((fact) => fact.includes("No AI-credit cap")));
        const capped = f.app.prepare({ ...start, limits: { maxAiCredits: 5 } });
        assert.deepEqual(object(capped.scope, "Execution scope").userRequestedLimits, { maxAiCredits: 5 });
        assert.equal(object(capped.scope, "Execution scope").effectiveAiCreditLimit, 5);
        assert.notEqual(capped.consentKey, uncapped.consentKey);
        const cleared = f.app.prepare({ ...start, limits: { maxAiCredits: null } });
        assert.deepEqual(object(cleared.scope, "Execution scope").userRequestedLimits, { maxAiCredits: null });
        assert.ok(cleared.decision!.facts.some((fact) => fact.includes("No AI-credit cap")));
        await assert.rejects(() => f.app.dispatch(start, actor), /confirmation/);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);

        const execution = f.app.lifecycle.begin(feature.id, "design", actor);
        assert.equal(execution.limits.maxAiCredits, null);
        f.app.lifecycle.store.updateExecution(feature.id, execution.id, { runtimeId: "zero-agent-fixture", status: "error" });
        const resume = { kind: "control_run", featureId: feature.id, executionId: execution.id, action: "resume", limits: { maxAiCredits: null } };
        assert.deepEqual(object(f.app.prepare(resume).scope, "Resume scope").limits, { maxAiCredits: null });
        await assert.rejects(() => f.app.dispatch(resume, actor), /confirmation/);
        assert.equal(f.app.lifecycle.store.state(feature.id).controls.length, 0);
        for (const value of [0, -1, "unlimited", false]) {
            assert.throws(() => f.app.prepare({ ...start, limits: { maxAiCredits: value } }), /positive finite/);
        }
        assert.throws(() => f.app.prepare({ ...start, limits: { maxTotalSubagents: null } }), /positive finite/);
    } finally { f.cleanup(); }
});
