// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import type { ElicitationParams, ElicitationResult } from "@github/copilot-sdk";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { requestDecision } from "../chat/confirmation.ts";
import { object } from "../core/contracts.ts";
import { decisionMessage, describeDecision } from "../core/decisions.ts";
import { actor, transportFixture as fixture, requirements } from "./helpers.ts";

test("publishing confirmation says plainly what will be shared and where", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "publish-copy", input: requirements() }, actor);
        const decision = describeDecision(f.app, { kind: "publish_feature", featureId: feature.id }, {
            feature: { title: feature.current.title, number: feature.current.number },
            target: { organization: "IdentityDivision", project: "Engineering", workItemId: 3802483 },
            effect: "Internal scope metadata.",
        });
        const message = decisionMessage(decision);
        assert.match(message, /^Publish this draft\?/);
        assert.match(message, /Draft revision 1/);
        assert.match(message, /Save this draft to ADO Feature 3802483 \(IdentityDivision\/Engineering\) so your team can access it\./);
        assert.match(message, /does not approve the scope or start design/i);
        assert.ok(!message.includes("successor"));
        assert.ok(!message.includes("historical approval"));
    } finally { f.cleanup(); }
});

test("connection confirmation shows repository and ADO targets in readable form", () => {
    const f = fixture();
    try {
        const decision = describeDecision(f.app, { kind: "configure" }, {
            repositories: {
                broker: { hostname: "msft.ghe.com", slug: "security/ad-accounts-for-android", baseBranch: "dev", account: "shjameel", localPath: "broker" },
                common: { hostname: "github.com", slug: "AzureAD/microsoft-authentication-library-common-for-android", baseBranch: "dev", account: "shahzaibj", localPath: "common" },
            },
            boards: { organization: "IdentityDivision", project: "Engineering", areaPath: "AndroidM" },
        });
        const message = decisionMessage(decision);
        assert.match(message, /^Save these project connections\?/);
        assert.match(message, /broker: msft\.ghe\.com\/security\/ad-accounts-for-android; branch dev; account shjameel; local folder broker/);
        assert.match(message, /common: github\.com\/AzureAD\/microsoft-authentication-library-common-for-android; branch dev; account shahzaibj; local folder common/);
        assert.match(message, /ADO backlog: IdentityDivision\/Engineering; area AndroidM/);
        assert.match(message, /No credentials or ADO work items are changed/);
    } finally { f.cleanup(); }
});

test("scope confirmation is short and labeled; full requirements stay available without approval", async () => {
    const f = fixture();
    let prompt: ElicitationParams | undefined;
    let response: ElicitationResult = { action: "accept", content: { decision: "review" } };
    const host: ChatHost = {
        sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
        ui: {
            confirm: async () => { throw new Error("The generic confirmed checkbox must not be used by a form-capable host."); },
            elicitation: async (request) => { prompt = request; return response; },
        },
    };
    try {
        const criteria = Array.from({ length: 14 }, (_, index) => ({ id: `R${index + 1}`, text: `Criterion ${index + 1}: ${"Preserve the reviewed compatibility constraint. ".repeat(6)}` }));
        const feature = f.app.execute({ kind: "create_feature", requestId: "readable-decision",
            input: { ...requirements(), requirements: criteria, designTasks: ["Design a bounded cache without dropping active cooldowns."] } }, actor);
        const toolsForReview = createChatTools(() => f.app, () => host);
        const tool = toolsForReview.find((entry) => entry.name === "software_factory_approve_requirements")!;
        const args = { featureId: feature.id, expectedRevision: feature.current.id };
        const result = await tool.handler(args, { sessionId: host.sessionId, toolCallId: "scope-review" });
        assert.ok(prompt);
        assert.match(prompt.message, /^Approve feature scope/);
        assert.match(prompt.message, /Scope revision 1/);
        assert.ok(prompt.message.length < 900);
        assert.ok(!prompt.message.includes(feature.id) && !prompt.message.includes(feature.current.id));
        assert.ok(!prompt.message.includes("Scope hash") && !prompt.message.includes('"requirements"'));
        const decision = prompt.requestedSchema.properties.decision;
        assert.ok(decision && decision.type === "string" && "oneOf" in decision);
        assert.equal(decision.title, "Your decision");
        assert.equal(decision.default, "not_now");
        assert.ok(decision.oneOf.some((option) => option.title === "Approve scope"));
        assert.ok(decision.oneOf.some((option) => option.title === "Review full details in chat"));
        assert.deepEqual(Object.keys(prompt.requestedSchema.properties), ["decision"]);
        assert.ok(result && typeof result === "object" && "outcome" in result && result.outcome === "review_required" && "review" in result);
        assert.equal(typeof result.review, "string");
        for (const criterion of feature.current.requirements) assert.ok(String(result.review).includes(criterion.text), "the opt-in review may not truncate scope");
        assert.ok(String(result.review).includes("Design a bounded cache"));
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        const nextAction = object(object(result, "Review result").nextAction, "Next action");
        assert.equal(nextAction.tool, "software_factory_decide_after_review");
        assert.equal(f.app.lifecycle.store.view(feature.id).currentProgress?.status, "waiting_for_user");
        response = { action: "accept", content: { decision: "approve" } };
        const decisionTool = toolsForReview.find((entry) => entry.name === "software_factory_decide_after_review")!;
        await decisionTool.handler(nextAction.arguments, { sessionId: host.sessionId, toolCallId: "scope-approval" });
        assert.equal(f.app.registry.get(feature.id).approvals[0]?.actor.reference, "scope-approval");
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally { f.cleanup(); }
});

test("cancel, decline and Not now cannot authorize a scope, and malformed host data fails explicitly", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "decision-options", input: requirements() }, actor);
        const decision = f.app.prepare({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }).decision!;
        for (const result of [
            { action: "cancel" }, { action: "decline", content: { decision: "approve" } },
            { action: "accept", content: { decision: "not_now" } },
        ] as ElicitationResult[]) {
            assert.equal(await requestDecision({ confirm: async () => true, elicitation: async () => result }, decision), "not_now");
        }
        await assert.rejects(() => requestDecision({
            confirm: async () => true, elicitation: async () => ({ action: "accept", content: { confirmed: true } }),
        }, decision), /no valid user decision/);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
    } finally { f.cleanup(); }
});

test("a scope edit while the labeled form is open invalidates the response", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "stale-readable-decision", input: requirements() }, actor);
        const host: ChatHost = {
            sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
            ui: { confirm: async () => { throw new Error("Use the form."); }, elicitation: async () => {
                f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id, input: { ...requirements(), title: "Changed during review" } }, actor);
                return { action: "accept", content: { decision: "approve" } };
            } },
        };
        const tool = createChatTools(() => f.app, () => host).find((entry) => entry.name === "software_factory_approve_requirements")!;
        await assert.rejects(async () => tool.handler({ featureId: feature.id, expectedRevision: feature.current.id },
            { sessionId: host.sessionId, toolCallId: "stale-form" }), /current requirements revision/);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
    } finally { f.cleanup(); }
});

test("an unchanged approved scope is not re-confirmed and execution permission remains separate", async () => {
    const f = fixture();
    let confirmations = 0;
    const host: ChatHost = {
        sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } },
        ui: { confirm: async () => { confirmations++; return true; } },
    };
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "scope-once", input: requirements() }, actor);
        const tools = createChatTools(() => f.app, () => host);
        const approve = tools.find((entry) => entry.name === "software_factory_approve_requirements")!;
        const args = { featureId: feature.id, expectedRevision: feature.current.id };
        await approve.handler(args, { sessionId: host.sessionId, toolCallId: "approve-once" });
        const repeated = await approve.handler(args, { sessionId: host.sessionId, toolCallId: "approve-again" });
        assert.ok(repeated && typeof repeated === "object" && "outcome" in repeated && repeated.outcome === "already_approved");
        assert.equal(confirmations, 1);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 1);
        const start = f.app.prepare({ kind: "start_workflow", featureId: feature.id, workflow: "design" });
        assert.ok(start.consentKey);
        assert.equal(start.decision?.title, "Start design work");
        assert.match(start.confirmation!, /Copilot allowance/);
        assert.match(start.confirmation!, /Does not change code/);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally { f.cleanup(); }
});

test("repeating an approved design gate is a receipt read, not another confirmation or a new decision", async () => {
    const f = fixture();
    let confirmations = 0;
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "design-once", input: requirements() }, actor);
        f.app.lifecycle.store.artifact(feature.id, "design", feature.current.id, { recommendation: "GO to Planning" });
        const gate = f.app.lifecycle.gate(feature.id, "design");
        const host: ChatHost = { sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } }, ui: { confirm: async () => { confirmations++; return true; } } };
        const tool = createChatTools(() => f.app, () => host).find((entry) => entry.name === "software_factory_command")!;
        const input = { command: { kind: "approve_stage", featureId: feature.id, stage: "design", approved: true, expectedScopeHash: gate.scopeHash } };
        await tool.handler(input, { sessionId: host.sessionId, toolCallId: "approve-design-once" });
        const repeated = await tool.handler(input, { sessionId: host.sessionId, toolCallId: "read-design-approval" });
        assert.ok(repeated && typeof repeated === "object" && "outcome" in repeated && repeated.outcome === "already_approved");
        assert.equal(confirmations, 1);
        assert.equal(f.app.lifecycle.store.state(feature.id).decisions.length, 1);
    } finally { f.cleanup(); }
});

test("native SDK elicitation carries the readable choice schema without starting agents or granting approval", async () => {
    const f = fixture();
    const { createClient, sessionConfiguration } = await import("../runtime/host.ts");
    const client = createClient(f.workspace, f.directory);
    let timer: NodeJS.Timeout | undefined;
    try {
        await client.start();
        let captured: ElicitationParams | undefined;
        const session = await client.createSession({
            ...sessionConfiguration(f.workspace, "Readable decision transport fixture"),
            availableTools: [], requestExtensions: false, enableConfigDiscovery: false,
            onElicitationRequest: (request) => {
                captured = { message: request.message, requestedSchema: request.requestedSchema! };
                return { action: "accept", content: { decision: "review" } };
            },
        });
        const feature = f.app.execute({ kind: "create_feature", requestId: "native-readable-form", input: requirements() }, actor);
        const decision = f.app.prepare({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }).decision!;
        const result = await Promise.race([
            requestDecision(session.ui, decision),
            new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("Native decision transport timed out.")), 20_000); }),
        ]);
        assert.equal(result, "review");
        assert.ok(captured);
        assert.match(captured.message, /^Approve feature scope/);
        assert.deepEqual(Object.keys(captured.requestedSchema.properties), ["decision"]);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally {
        if (timer) clearTimeout(timer);
        const errors = await client.stop();
        f.cleanup();
        if (errors.length) throw new AggregateError(errors, "Decision transport fixture cleanup failed.");
    }
});
