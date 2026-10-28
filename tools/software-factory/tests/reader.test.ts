// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { writeFileSync, symlinkSync, mkdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import type { CopilotSession } from "@github/copilot-sdk";
import { createReaderTools, readerAgent, readerAgents, readerToolNames } from "../runtime/reader.ts";
import { blankState } from "../core/lifecycle-store.ts";
import { actor, fixture, requirements } from "./helpers.ts";

test("workers get only scoped native read tools; host denial is never replaced with filesystem access", async () => {
    const f = fixture();
    const calls: Parameters<CopilotSession["rpc"]["tools"]["execute"]>[0][] = [];
    let denied = false;
    let offered = ["view", "glob", "grep"];
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "reader", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const execution = f.app.lifecycle.begin(feature.id, "check", actor);
        const session = { sessionId: execution.sessionId, rpc: { tools: {
            initializeAndValidate: async () => ({ toolNames: [] }),
            getCurrentMetadata: async () => ({ tools: offered.map((name) => ({ name, description: "fixture" })) }),
            execute: async (args: Parameters<CopilotSession["rpc"]["tools"]["execute"]>[0]) => {
            calls.push(args);
            return denied ? { resultType: "failure" as const, textResultForLlm: "Managed/content policy denied the read." } : "native result";
        } } } };
        const tools = createReaderTools(() => f.app, () => session);
        assert.deepEqual(readerAgent.tools, readerToolNames);
        assert.ok(readerAgents.every((agent) => JSON.stringify(agent.tools) === JSON.stringify(readerToolNames)));
        assert.ok(!readerAgent.tools?.some((name) => /shell|write|approve|command|task/.test(name)));
        const file = path.join(f.workspace, "A.kt");
        writeFileSync(file, "needle");
        const scope = { featureId: feature.id, executionId: execution.id, path: file };
        const read = tools[0]!;
        assert.equal(await read.handler(scope), "native result");
        assert.equal(calls[0]?.name, "view");
        denied = true;
        await assert.rejects(() => read.handler(scope), /Managed\/content policy denied/);
        assert.equal(calls.length, 2, "A denied native read must not fall back or retry.");
        await assert.rejects(() => read.handler({ ...scope, path: f.options.database }), /approved source/);
        await assert.rejects(() => read.handler({ ...scope, view_range: [1, 301] }), /at most 300/);
        await assert.rejects(() => read.handler({ ...scope, forceReadLargeFiles: true }), /Unknown|Unsupported|Unexpected/i);
        await assert.rejects(() => tools[1]!.handler({ ...scope, path: f.workspace, pattern: "..\\*" }), /relative glob/);
        const outside = path.join(f.directory, "outside");
        mkdirSync(outside);
        symlinkSync(outside, path.join(f.workspace, "escape"), process.platform === "win32" ? "junction" : "dir");
        await assert.rejects(() => read.handler({ ...scope, path: path.join(f.workspace, "escape") }), /approved source/);
        session.sessionId = "another-session";
        await assert.rejects(() => read.handler(scope), /another execution session/);
        session.sessionId = execution.sessionId;
        offered = [];
        await assert.rejects(() => read.handler(scope), /does not offer the native/);
        assert.equal(calls.length, 2);
    } finally { f.cleanup(); }
});

test("an uncertain native admission cannot automatically become another paid submission", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "uncertain", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const execution = f.app.lifecycle.begin(feature.id, "check", actor);
        f.app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "error", admissionClaim: "first-controller" });
        assert.throws(() => f.app.lifecycle.begin(feature.id, "check", actor), /previous submission/i);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 1);
    } finally { f.cleanup(); }
});

test("legacy state upgrades without changing history and only one live controller claims a chat", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "legacy-owner", input: requirements() }, actor);
        const legacy = { ...blankState(), version: 1 };
        f.app.registry.connection.prepare("INSERT INTO factory_state VALUES (?,1,?)").run(feature.id, JSON.stringify(legacy));
        assert.equal(f.app.lifecycle.store.state(feature.id).version, 2);
        assert.equal(JSON.parse(String(f.app.registry.connection.prepare("SELECT content_json FROM factory_state WHERE feature_id=?").get(feature.id)!.content_json)).version, 1);
        f.app.lifecycle.store.change(feature.id, (state) => { state.sessionBinding = { sessionId: "original-chat", kind: "chat", established: true }; });
        assert.equal(f.app.registry.get(feature.id).current.id, feature.current.id);
        const store = f.app.lifecycle.store;
        store.heartbeat("original-chat", "owner-A");
        assert.equal(store.ownerOnline("original-chat"), true);
        assert.equal(store.ownsSession("original-chat", "owner-A"), true);
        assert.throws(() => store.heartbeat("original-chat", "owner-B"), /Another resident controller/);
        store.releaseOwner("original-chat", "owner-A");
        assert.equal(store.ownerOnline("original-chat"), false);
        store.heartbeat("original-chat", "owner-B");
        assert.equal(store.ownsSession("original-chat", "owner-B"), true);
    } finally { f.cleanup(); }
});

test("a changed session owner invalidates execution consent rather than moving the work silently", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "owner-consent", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor,
            { granted: true, revisionId: feature.current.id, actor });
        const command = { kind: "start_workflow", featureId: feature.id, workflow: "design" };
        const before = f.app.prepare(command).consentKey!;
        f.app.lifecycle.store.change(feature.id, (state) => { state.sessionBinding = { sessionId: "original-chat", kind: "chat", established: true }; });
        assert.notEqual(f.app.prepare(command).consentKey, before);
        await assert.rejects(() => f.app.dispatch(command, actor, { granted: true, revisionId: before, actor }), /confirmation|scope/i);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally { f.cleanup(); }
});
