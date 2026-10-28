// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import type { ElicitationParams, ElicitationResult } from "@github/copilot-sdk";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { object } from "../core/contracts.ts";
import { actor, fixture, requirements } from "./helpers.ts";

test("full review returns an explicit next form; approving it yields the next real checkpoint without starting work", async () => {
    const f = fixture();
    const forms: ElicitationParams[] = [];
    const responses: ElicitationResult[] = [
        { action: "accept", content: { decision: "review" } },
        { action: "accept", content: { decision: "approve" } },
    ];
    const host: ChatHost = {
        sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
        ui: { confirm: async () => { throw new Error("Use native choice forms."); }, elicitation: async (form) => {
            forms.push(form);
            const response = responses.shift();
            if (!response) throw new Error("Unexpected repeated form.");
            return response;
        } },
    };
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "no-typing-handoff", input: requirements() }, actor);
        const tools = createChatTools(() => f.app, () => host);
        const approve = tools.find((entry) => entry.name === "software_factory_approve_requirements")!;
        const result = object(await approve.handler({ featureId: feature.id, expectedRevision: feature.current.id },
            { sessionId: host.sessionId, toolCallId: "request-full-review" }), "Review result");
        assert.equal(result.outcome, "review_required");
        assert.equal(result.userInputRequired, true);
        const next = object(result.nextAction, "Explicit next action");
        assert.equal(next.tool, "software_factory_decide_after_review");
        assert.match(String(result.message), /same response/);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        assert.equal(f.app.lifecycle.store.view(feature.id).currentProgress?.status, "waiting_for_user");
        const decide = tools.find((entry) => entry.name === next.tool)!;
        const approved = object(await decide.handler(next.arguments, { sessionId: host.sessionId, toolCallId: "decide-reviewed-scope" }), "Decision result");
        assert.equal(approved.outcome, "approved");
        assert.equal(forms.length, 2);
        const field = forms[1]!.requestedSchema.properties.decision;
        assert.ok(field?.type === "string" && "oneOf" in field);
        assert.equal(field.title, "How would you like to proceed?");
        assert.equal(field.default, "not_now");
        assert.deepEqual(field.oneOf.map((entry) => entry.const), ["not_now", "revise", "approve"]);
        assert.equal(f.app.registry.get(feature.id).approvals[0]?.actor.reference, "decide-reviewed-scope");
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
        const following = object(approved.nextAction, "Next protected checkpoint");
        const args = object(following.arguments, "Next checkpoint arguments");
        const command = object(args.command, "Next command");
        assert.equal(following.tool, "software_factory_command");
        assert.equal(command.kind, "start_workflow");
        assert.equal(command.workflow, "design");
        await assert.rejects(async () => decide.handler(next.arguments, { sessionId: host.sessionId, toolCallId: "replay-review" }), /unavailable/);
        assert.equal(forms.length, 2, "a review handoff is single-use");
    } finally { f.cleanup(); }
});

test("review IDs do not authorize a changed scope or work in another session", async () => {
    const f = fixture();
    let forms = 0;
    const host: ChatHost = {
        sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
        ui: { confirm: async () => { throw new Error("Use forms."); }, elicitation: async () => {
            forms++; return { action: "accept", content: { decision: "review" } };
        } },
    };
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "changed-review", input: requirements() }, actor);
        const tools = createChatTools(() => f.app, () => host);
        const generic = tools.find((entry) => entry.name === "software_factory_command")!;
        const result = object(await generic.handler({ command: { kind: "start_workflow", featureId: feature.id, workflow: "intake" } },
            { sessionId: host.sessionId, toolCallId: "review-intake" }), "Review");
        const next = object(result.nextAction, "Next action");
        const decide = tools.find((entry) => entry.name === next.tool)!;
        await assert.rejects(async () => decide.handler(next.arguments, { sessionId: "different-chat", toolCallId: "wrong-chat" }), /owning session/);
        await assert.rejects(async () => decide.handler(next.arguments, { sessionId: host.sessionId, toolCallId: "worker", agentId: "worker" }), /workers cannot present/);
        await assert.rejects(async () => decide.handler({ ...object(next.arguments, "Arguments"), approved: true },
            { sessionId: host.sessionId, toolCallId: "forged" }), /unsupported fields/);
        f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Changed after the review" } }, actor);
        await assert.rejects(async () => decide.handler(next.arguments, { sessionId: host.sessionId, toolCallId: "stale-review" }), /reviewed scope changed/);
        assert.equal(forms, 1, "stale review cannot even open a new final-confirmation form");
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally { f.cleanup(); }
});

test("Decide later is an explicit pause and host input failures are blockers, not hidden waiting", async () => {
    const f = fixture();
    let fail = false;
    const host: ChatHost = {
        sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
        ui: { confirm: async () => false, elicitation: async () => {
            if (fail) throw new Error("Host input timed out.");
            return { action: "accept", content: { decision: "not_now" } };
        } },
    };
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "explicit-pause", input: requirements() }, actor);
        const tools = createChatTools(() => f.app, () => host);
        const approve = tools.find((entry) => entry.name === "software_factory_approve_requirements")!;
        const args = { featureId: feature.id, expectedRevision: feature.current.id };
        const paused = object(await approve.handler(args, { sessionId: host.sessionId, toolCallId: "pause-now" }), "Pause result");
        assert.equal(paused.outcome, "not_approved");
        assert.match(String(paused.message), /Paused/);
        assert.equal(f.app.lifecycle.store.view(feature.id).currentProgress?.status, "idle");
        fail = true;
        await assert.rejects(async () => approve.handler(args, { sessionId: host.sessionId, toolCallId: "input-timeout" }), /Host input timed out/);
        const state = f.app.lifecycle.store.view(feature.id);
        assert.equal(state.currentProgress?.status, "blocked");
        assert.match(state.currentProgress.summary, /No approval was granted/);
        assert.equal(state.factory.cancelled, false);
        assert.equal(state.approvals.length, 0);
        assert.equal(state.factory.executions.length, 0);
    } finally { f.cleanup(); }
});

test("cancelling requested-change feedback pauses without modifying the saved scope", async () => {
    const f = fixture();
    const responses: ElicitationResult[] = [
        { action: "accept", content: { decision: "revise" } }, { action: "cancel" },
    ];
    const host: ChatHost = {
        sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
        ui: { confirm: async () => false, elicitation: async () => {
            const response = responses.shift();
            if (!response) throw new Error("Unexpected repeated feedback request.");
            return response;
        } },
    };
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "cancel-revision-feedback", input: requirements() }, actor);
        const approve = createChatTools(() => f.app, () => host).find((entry) => entry.name === "software_factory_approve_requirements")!;
        const result = object(await approve.handler({ featureId: feature.id, expectedRevision: feature.current.id },
            { sessionId: host.sessionId, toolCallId: "cancel-feedback" }), "Feedback result");
        assert.equal(result.outcome, "not_approved");
        const view = f.app.lifecycle.store.view(feature.id);
        assert.equal(view.current.id, feature.current.id);
        assert.equal(view.approvals.length, 0);
        assert.equal(view.currentProgress?.status, "idle");
        assert.equal(view.factory.executions.length, 0);
    } finally { f.cleanup(); }
});

test("native full-review -> request-changes -> feedback handoff collects input without changing or approving scope", async () => {
    const f = fixture();
    const { createClient, sessionConfiguration } = await import("../runtime/host.ts");
    const client = createClient(f.workspace, f.directory);
    const forms: ElicitationParams[] = [];
    const responses: ElicitationResult[] = [
        { action: "accept", content: { decision: "review" } },
        { action: "accept", content: { decision: "revise" } },
        { action: "accept", content: { feedback: "Preserve interactive cancellation behavior explicitly in the draft." } },
    ];
    let timer: NodeJS.Timeout | undefined;
    try {
        await client.start();
        const session = await client.createSession({
            ...sessionConfiguration(f.workspace, "Review handoff transport fixture"),
            availableTools: [], requestExtensions: false, enableConfigDiscovery: false,
            onElicitationRequest: (request) => {
                forms.push({ message: request.message, requestedSchema: request.requestedSchema! });
                const response = responses.shift();
                if (!response) throw new Error("Unexpected input request.");
                return response;
            },
        });
        const feature = f.app.execute({ kind: "create_feature", requestId: "native-review-feedback", input: requirements() }, actor);
        const tools = createChatTools(() => f.app, () => ({ sessionId: session.sessionId, capabilities: { ui: { elicitation: true } }, ui: session.ui }));
        const flow = async () => {
            const approve = tools.find((entry) => entry.name === "software_factory_approve_requirements")!;
            const result = object(await approve.handler({ featureId: feature.id, expectedRevision: feature.current.id },
                { sessionId: session.sessionId, toolCallId: "native-full-review" }), "Native review");
            const next = object(result.nextAction, "Native handoff");
            return object(await tools.find((entry) => entry.name === next.tool)!.handler(next.arguments,
                { sessionId: session.sessionId, toolCallId: "native-request-changes" }), "Native feedback");
        };
        const result = await Promise.race([
            flow(),
            new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("Native review handoff timed out.")), 20_000); }),
        ]);
        assert.equal(result.outcome, "changes_requested");
        assert.match(String(result.feedback), /interactive cancellation/);
        assert.equal(forms.length, 3);
        assert.deepEqual(Object.keys(forms[2]!.requestedSchema.properties), ["feedback"]);
        assert.equal(f.app.registry.get(feature.id).current.id, feature.current.id);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
        assert.equal(f.app.lifecycle.store.view(feature.id).currentProgress?.status, "working");
    } finally {
        if (timer) clearTimeout(timer);
        const errors = await client.stop();
        f.cleanup();
        if (errors.length) throw new AggregateError(errors, "Review handoff transport cleanup failed.");
    }
});
