// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { FactoryError } from "../core/contracts.ts";
import { actor, transportFixture as fixture, requirements } from "./helpers.ts";

test("chat uses the shared registry without a server or frontend", async () => {
    const f = fixture();
    let prompt = "";
    const host: ChatHost = {
        sessionId: "owning-session", capabilities: { ui: { elicitation: true } },
        ui: { confirm: async (message) => { prompt = message; return true; } },
    };
    const tools = createChatTools(() => f.app, () => host);
    const call = (name: string, args: unknown) => {
        const tool = tools.find((item) => item.name === name);
        assert.ok(tool);
        return tool.handler(args, { sessionId: host.sessionId, toolCallId: "host-call" });
    };
    try {
        await call("software_factory_create_feature", { requestId: "chat-request", input: requirements() });
        const feature = f.app.registry.list()[0];
        assert.ok(feature);
        await call("software_factory_approve_requirements", { featureId: feature.id, expectedRevision: feature.current.id });
        assert.ok(!prompt.includes(feature.current.id), "machine revision IDs do not belong in the human-facing decision");
        assert.match(prompt, /Scope revision 1/);
        assert.match(prompt, /Does not start AI work/);
        const approved = f.app.registry.get(feature.id);
        assert.equal(approved.status, "ready_for_design");
        assert.equal(approved.approvals[0]?.actor.reference, "host-call");
        assert.equal(approved.approvals[0]?.actor.sessionId, host.sessionId);
    } finally { f.cleanup(); }
});

test("first-run chat reports missing configuration and a blocked preview without hiding the cause", async () => {
    const f = fixture();
    const host: ChatHost = {
        sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
        ui: { confirm: async () => true },
    };
    const tools = createChatTools(() => f.app, () => host);
    const call = (name: string, args: unknown) => tools.find((item) => item.name === name)!.handler(args, {
        sessionId: host.sessionId, toolCallId: "first-run",
    });
    try {
        const status = await call("software_factory_configuration", {});
        assert.deepEqual(status, {
            configured: false,
            configuration: null,
            message: "Software Factory has no repository or ADO board configuration in this local registry.",
            nextStep: "Research the current repository and ask only for missing or ambiguous repository, account, branch, or ADO values. Then preview and request genuine confirmation for a configure command without a separate preliminary yes/no prompt. Do not infer missing account or branch values or publish before configuration succeeds.",
        });
        const feature = f.app.execute({ kind: "create_feature", requestId: "first-run", input: requirements() }, actor);
        const preview = await call("software_factory_preview", { command: {
            kind: "publish_feature", featureId: feature.id,
            target: { organization: "IdentityDivision", project: "Engineering", workItemId: 3802483 },
        } });
        assert.deepEqual(preview, {
            outcome: "blocked",
            executed: false,
            error: { code: "configuration_required", message: "Configure repository/account mappings and ADO board settings first." },
            nextStep: "Read software_factory_configuration, gather the missing repository and board mappings, and preview a configure command. Do not retry this preview until configuration is confirmed.",
        });
        assert.equal(f.app.lifecycle.store.configuration(), null);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        assert.equal(f.app.shared.cached(feature.id), null);
    } finally { f.cleanup(); }
});

test("unsupported hosts and declined confirmations cannot manufacture approval", async () => {
    const f = fixture();
    let confirmations = 0;
    const host: ChatHost = {
        sessionId: "owning-session", capabilities: {},
        ui: { confirm: async () => { confirmations++; return false; } },
    };
    const tool = createChatTools(() => f.app, () => host).find((item) => item.name === "software_factory_approve_requirements");
    assert.ok(tool);
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "one", input: requirements() }, actor);
        const args = { featureId: feature.id, expectedRevision: feature.current.id };
        const invocation = { sessionId: host.sessionId, toolCallId: "approval-call" };
        await assert.rejects(async () => tool.handler(args, invocation),
            (error: unknown) => error instanceof FactoryError && error.code === "confirmation_unavailable");
        assert.equal(confirmations, 0);
        host.capabilities.ui = { elicitation: true };
        const outcome = await tool.handler(args, invocation);
        assert.ok(outcome && typeof outcome === "object" && "outcome" in outcome && outcome.outcome === "not_approved");
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        await assert.rejects(async () => tool.handler({ ...args, approved: true }, invocation), /unsupported fields/);
        await assert.rejects(async () => tool.handler(args, { ...invocation, sessionId: "other-session" }), /owning session/);
        await assert.rejects(async () => tool.handler(args, { ...invocation, agentId: "worker" }), /Background workers/);
    } finally { f.cleanup(); }
});

test("an edit while the host confirmation is open invalidates the consent", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "one", input: requirements() }, actor);
        const host: ChatHost = {
            sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
            ui: { confirm: async () => {
                f.app.execute({
                    kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
                    input: { ...requirements(), title: "Changed during confirmation" },
                }, actor);
                return true;
            } },
        };
        const tool = createChatTools(() => f.app, () => host).find((item) => item.name === "software_factory_approve_requirements");
        assert.ok(tool);
        await assert.rejects(async () => tool.handler(
            { featureId: feature.id, expectedRevision: feature.current.id },
            { sessionId: host.sessionId, toolCallId: "stale-call" },
        ), (error: unknown) => error instanceof FactoryError && error.code === "stale_revision");
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
    } finally { f.cleanup(); }
});
