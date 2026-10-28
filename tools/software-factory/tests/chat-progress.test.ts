// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { Application } from "../core/application.ts";
import { nextStep, parseProgress } from "../core/progress.ts";
import { actor, fixture, requirements } from "./helpers.ts";

const research = () => ({
    stage: "research", status: "working",
    summary: "Inspecting Broker/Common and existing throttling behavior before recommending scope.",
    expectedOutput: "An evidence-backed brief and success criteria, not implementation.",
    nextStep: "Present the recommended scope for review before design.",
});

test("conversational progress is durable and descriptive, never workflow or approval authority", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "chat-progress", input: requirements() }, actor);
        const command = { kind: "report_progress", featureId: feature.id, expectedRevision: feature.current.id, requestId: "research-1", progress: research() };
        assert.equal(f.app.prepare(command).consentKey, null);
        await f.app.dispatch(command, actor);
        const saved = f.app.lifecycle.store.view(feature.id);
        assert.equal(saved.current.id, feature.current.id);
        assert.equal(saved.currentProgress?.stage, "research");
        assert.equal(saved.factory.progress[0]?.actor.sessionId, actor.sessionId);
        assert.equal(saved.history.length, 1);
        assert.deepEqual(saved.approvals, []);
        assert.deepEqual(saved.factory.executions, []);
        assert.deepEqual(saved.factory.operations, []);
        assert.deepEqual(saved.factory.decisions, []);
        assert.equal(saved.status, "requirements_review");
        assert.equal(nextStep(saved, []).report?.expectedOutput, research().expectedOutput);
        assert.ok(saved.activity.some((entry) => entry.source === "Copilot chat" && entry.detail.includes("Expected output:")));
        const reopened = new Application(f.options);
        try { assert.deepEqual(reopened.lifecycle.store.view(feature.id).currentProgress, saved.currentProgress); }
        finally { reopened.close(); }
    } finally { f.cleanup(); }
});

test("pending questions require a rationale and recommendation, not an unexplained questionnaire", () => {
    const waiting = { ...research(), status: "waiting_for_user" };
    assert.throws(() => parseProgress(waiting), /Question and rationale/);
    assert.throws(() => parseProgress({ ...waiting, question: { text: "Which callers?", whyNeeded: "", recommendation: "Use the existing supported callers." } }), /Why this needs the user/);
    const progress = parseProgress({ ...waiting, question: {
        text: "Does the first rollout include legacy ADAL callers?",
        whyNeeded: "Legacy callers may not carry the existing retry metadata; this changes compatibility scope.",
        recommendation: "Keep the first rollout scoped to callers with supported retry metadata.",
    } });
    assert.equal(progress.question?.text, "Does the first rollout include legacy ADAL callers?");
    assert.throws(() => parseProgress({ ...research(), approved: true }), /unsupported fields/);
    assert.throws(() => parseProgress({ ...research(), status: "approved" }), /known stage/);
    assert.throws(() => parseProgress({ ...research(), summary: "x".repeat(2001) }), /at most 2000/);
    assert.throws(() => parseProgress({ ...research(), question: progress.question }), /Only a waiting/);
});

test("retries are idempotent and scope changes retire old conversational status", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "idempotent-progress", input: requirements() }, actor);
        const command = { kind: "report_progress", featureId: feature.id, expectedRevision: feature.current.id, requestId: "stable-research", progress: research() };
        await f.app.dispatch(command, actor);
        await f.app.dispatch(command, actor);
        assert.equal(f.app.lifecycle.store.state(feature.id).progress.length, 1);
        await assert.rejects(() => f.app.dispatch({ ...command, progress: { ...research(), summary: "A different update." } }, actor), /different update/);
        const changed = f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Changed after the chat update" } }, actor);
        assert.equal(f.app.lifecycle.store.view(feature.id).currentProgress, null);
        await assert.rejects(() => f.app.dispatch(command, actor), /current feature/);
        await f.app.dispatch({ ...command, expectedRevision: changed.current.id, requestId: "new-research" }, actor);
        assert.ok(f.app.lifecycle.store.view(feature.id).currentProgress);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: changed.current.id },
            actor, { granted: true, revisionId: changed.current.id, actor });
        assert.equal(f.app.lifecycle.store.view(feature.id).currentProgress, null, "an older question cannot override a newer approval");
        assert.equal(f.app.lifecycle.store.state(feature.id).progress.length, 2, "historical status remains inspectable");
    } finally { f.cleanup(); }
});

test("the top-level chat reports progress with real provenance; workers and other sessions cannot impersonate it", async () => {
    const f = fixture();
    const host: ChatHost = { sessionId: actor.sessionId!, capabilities: {}, ui: { confirm: async () => { throw new Error("No confirmation is needed for descriptive status."); } } };
    const tools = createChatTools(() => f.app, () => host);
    const report = tools.find((tool) => tool.name === "software_factory_report_progress")!;
    const generic = tools.find((tool) => tool.name === "software_factory_command")!;
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "reported-progress", input: requirements() }, actor);
        const input = { featureId: feature.id, expectedRevision: feature.current.id, requestId: "report-tool", progress: research() };
        const invocation = { sessionId: host.sessionId, toolCallId: "actual-host-call" };
        await assert.rejects(async () => report.handler(input, { ...invocation, sessionId: "other-session" }), /owning session/);
        await assert.rejects(async () => report.handler(input, { ...invocation, agentId: "worker" }), /Conversational progress/);
        await assert.rejects(async () => generic.handler({ command: { kind: "report_progress", ...input } }, { ...invocation, agentId: "worker" }), /Conversational progress/);
        await report.handler(input, invocation);
        const progress = f.app.lifecycle.store.view(feature.id).currentProgress!;
        assert.equal(progress.actor.reference, "actual-host-call");
        assert.equal(progress.actor.sessionId, host.sessionId);
        const run = f.app.lifecycle.begin(feature.id, "intake", actor);
        const view = f.app.lifecycle.store.view(feature.id);
        assert.equal(nextStep(view, []).run?.id, run.id, "native execution remains authoritative over conversational intent");
        assert.equal(nextStep(view, []).report, undefined);
        const legacy = f.app.lifecycle.store.state(feature.id);
        Reflect.deleteProperty(legacy, "progress");
        f.app.registry.connection.prepare("UPDATE factory_state SET content_json = ? WHERE feature_id = ?").run(JSON.stringify(legacy), feature.id);
        assert.deepEqual(f.app.lifecycle.store.state(feature.id).progress, [], "old registries need no destructive migration");
    } finally { f.cleanup(); }
});
