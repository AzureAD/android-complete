// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { FactoryError, parseRequirements, parseSuccessCriteria } from "../core/contracts.ts";
import { parseIntakeProposal, type Execution, type Json } from "../core/lifecycle-contracts.ts";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { designRun, intakeRun, type FactoryContext } from "../workflows/lifecycle.ts";
import { actor, fixture, transportFixture, requirements } from "./helpers.ts";

test("success criteria accept plain language, bullets and the pasted colon example with wrapped lines", () => {
    assert.deepEqual(parseSuccessCriteria(
        "R1: Document the prerequisites and commands for running Common's\n" +
        "    unit tests locally, verified against the current repository.\r\n" +
        "R2: Do not change production code, dependencies, or CI pipelines.",
    ), [
        { id: "R1", text: "Document the prerequisites and commands for running Common's unit tests locally, verified against the current repository." },
        { id: "R2", text: "Do not change production code, dependencies, or CI pipelines." },
    ]);
    assert.deepEqual(parseSuccessCriteria("- Preserve the response format.\n- Keep cancellation behavior.\n\n3. Update the documentation."), [
        { id: "R1", text: "Preserve the response format." }, { id: "R2", text: "Keep cancellation behavior." }, { id: "R3", text: "Update the documentation." },
    ]);
    assert.deepEqual(parseSuccessCriteria("Existing clients continue working.\nNo dependencies change."), [
        { id: "R1", text: "Existing clients continue working." }, { id: "R2", text: "No dependencies change." },
    ]);
    assert.deepEqual(parseSuccessCriteria("R1 | Original syntax still works."), [{ id: "R1", text: "Original syntax still works." }]);
    assert.throws(() => parseSuccessCriteria("R1: First\nR1: Second"), /must be unique/);
});

test("automatic criterion IDs preserve unchanged outcomes across reordering and avoid reused IDs", () => {
    const previous = [{ id: "R1", text: "First outcome." }, { id: "R2", text: "Second outcome." }];
    assert.deepEqual(parseSuccessCriteria("Second outcome.\nA new outcome.\nFirst outcome.", previous), [
        previous[1], { id: "R3", text: "A new outcome." }, previous[0],
    ]);
    assert.deepEqual(parseRequirements({
        title: "Natural input", description: "Explain the local test setup.",
        requirements: [{ text: "Document the required tools." }, { text: "Do not change CI." }],
    }).requirements.map((item) => item.id), ["R1", "R2"]);
});

test("a plain-language brief can be saved but cannot approve or start design without reviewed success criteria", async () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "brief", input: {
            title: "Document local tests", description: "Help contributors run the Common tests without changing production code.",
        } }, actor);
        assert.deepEqual(feature.current.requirements, []);
        assert.equal(feature.status, "requirements_review");
        const approval = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        assert.throws(() => f.app.prepare(approval), /at least one success criterion/);
        assert.throws(() => f.app.execute(approval, actor, { granted: true, revisionId: feature.current.id, actor }), /at least one success criterion/);
        for (const kind of ["design", "lifecycle", "monitor"] as const) {
            assert.throws(() => f.app.lifecycle.begin(feature.id, kind, actor), /Review and approve/);
        }
        const start = { kind: "start_workflow", featureId: feature.id, workflow: "intake" };
        assert.match(f.app.prepare(start).confirmation!, /may consume AI credits/);
        assert.match(f.app.prepare(start).confirmation!, /Propose draft success criteria/);
        await assert.rejects(() => f.app.dispatch(start, actor), /genuine UI or chat-host confirmation/);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0, "previews never start work");
        const revised = f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { title: feature.current.title, description: "The brief changed after its execution scope was confirmed." } }, actor);
        assert.throws(() => f.app.lifecycle.begin(feature.id, "intake", actor, {}, {}, {
            requirementsRevision: feature.current.id, configurationId: null,
        }), /changed before workflow admission/);
        assert.equal(f.app.registry.get(feature.id).current.id, revised.current.id);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
        const original = f.app.execute({ kind: "create_feature", requestId: "old-input", input: requirements() }, actor);
        const unchanged = f.app.execute({ kind: "revise_requirements", featureId: original.id, expectedRevision: original.current.id,
            input: { ...requirements(), description: "" } }, actor);
        assert.equal(unchanged.current.id, original.current.id, "old revision hashes remain compatible");
    } finally { f.cleanup(); }
});

test("chat captures a plain brief, assigns IDs and includes the brief in genuine approval", async () => {
    const f = transportFixture();
    let prompt = "";
    const host: ChatHost = { sessionId: actor.sessionId!, capabilities: { ui: { elicitation: true } }, ui: { confirm: async (message) => { prompt = message; return true; } } };
    const tools = createChatTools(() => f.app, () => host);
    const call = (name: string, args: unknown) => tools.find((tool) => tool.name === name)!.handler(args, { sessionId: host.sessionId, toolCallId: randomUUID() });
    try {
        await call("software_factory_create_feature", { requestId: "chat-brief", input: {
            title: "Document tests", description: "Make the local test setup understandable.", requirements: [], blockingUnknowns: [],
        } });
        const feature = f.app.registry.list()[0]!;
        await assert.rejects(async () => call("software_factory_approve_requirements", { featureId: feature.id, expectedRevision: feature.current.id }), /success criterion/);
        assert.equal(prompt, "", "empty criteria cannot even request approval");
        await call("software_factory_revise_requirements", { featureId: feature.id, expectedRevision: feature.current.id, input: {
            title: feature.current.title, description: feature.current.description,
            requirements: [{ text: "Document the verified commands." }], blockingUnknowns: [],
        } });
        const revised = f.app.registry.get(feature.id);
        assert.deepEqual(revised.current.requirements, [{ id: "R1", text: "Document the verified commands." }]);
        await call("software_factory_approve_requirements", { featureId: feature.id, expectedRevision: revised.current.id });
        assert.match(prompt, /Make the local test setup understandable/);
        assert.equal(f.app.registry.get(feature.id).status, "ready_for_design");
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
    } finally { f.cleanup(); }
});

function context(featureId: string, execution: Execution, agent: FactoryContext["agent"]): FactoryContext {
    return {
        args: { featureId, executionId: execution.id }, runId: randomUUID(), session: { sessionId: execution.sessionId },
        phase: () => undefined, log: () => undefined, pause: async () => { throw new Error("Intake must not request design approval."); },
        step: async (_key, producer) => producer(), agent,
    };
}

test("the native intake workflow only proposes a draft; human saving and later approval are separate", async () => {
    const f = fixture();
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    process.env.SOFTWARE_FACTORY_HOME = f.directory;
    process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "assisted-intake", input: {
            title: "Document local tests", description: "Explain how to run local unit tests. Do not change CI.",
            requirements: [], blockingUnknowns: ["Which supported test target should be documented?"],
        } }, actor);
        const execution = f.app.lifecycle.begin(feature.id, "intake", actor);
        let calls = 0;
        const ctx = context(feature.id, execution, async (prompt) => {
            calls++;
            assert.match(prompt, /Do not change CI/);
            assert.match(prompt, /do not invent metrics/);
            return { summary: "A proposed checklist for review.", requirements: ["Document the supported test command.", "Keep CI unchanged."], blockingUnknowns: [] };
        });
        const result = await intakeRun(ctx);
        f.app.lifecycle.store.updateExecution(feature.id, execution.id, { status: "completed", outcome: result });
        assert.equal(calls, 1);
        assert.equal(f.app.registry.get(feature.id).current.id, feature.current.id);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        const proposal = f.app.lifecycle.store.latest(feature.id, "intake")!;
        assert.equal(proposal.executionId, execution.id);
        const parsed = parseIntakeProposal(proposal.content);
        assert.deepEqual(parsed.blockingUnknowns, feature.current.blockingUnknowns, "the agent cannot silently resolve existing questions");
        assert.deepEqual(f.app.lifecycle.store.state(feature.id).operations, []);
        const revised = f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { title: feature.current.title, description: feature.current.description, requirements: parsed.requirements, blockingUnknowns: [] } }, actor);
        assert.equal(revised.status, "requirements_review", "saving chosen suggestions is not approval");
        assert.equal(f.app.lifecycle.store.latest(feature.id, "intake"), null, "an older proposal is not current after a revision");
        assert.equal(revised.approvals.length, 0);
    } finally {
        if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
        if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
        f.cleanup();
    }
});

test("missing, malformed and stale intake output cannot change or approve the brief", async () => {
    const oldHome = process.env.SOFTWARE_FACTORY_HOME;
    const oldWorkspace = process.env.SOFTWARE_FACTORY_WORKSPACE;
    for (const scenario of ["null", "empty", "stale", "wrong-kind"]) {
        const f = fixture();
        process.env.SOFTWARE_FACTORY_HOME = f.directory;
        process.env.SOFTWARE_FACTORY_WORKSPACE = f.workspace;
        try {
            const feature = f.app.execute({ kind: "create_feature", requestId: scenario, input: { title: "Brief", description: "Document the local test setup." } }, actor);
            const execution = f.app.lifecycle.begin(feature.id, "intake", actor);
            const ctx = context(feature.id, execution, async (): Promise<Json | null> => {
                if (scenario === "null") return null;
                if (scenario === "empty") return { summary: "Empty proposal", requirements: [], blockingUnknowns: [] };
                f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
                    input: { title: "Changed brief", description: "The scope changed while Factory was working." } }, actor);
                return { summary: "Old scope", requirements: ["Document the old setup."], blockingUnknowns: [] };
            });
            await assert.rejects(() => scenario === "wrong-kind" ? designRun(ctx) : intakeRun(ctx),
                (error: unknown) => error instanceof FactoryError);
            assert.equal(f.app.lifecycle.store.state(feature.id).artifacts.length, 0);
            assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
        } finally {
            if (oldHome === undefined) delete process.env.SOFTWARE_FACTORY_HOME; else process.env.SOFTWARE_FACTORY_HOME = oldHome;
            if (oldWorkspace === undefined) delete process.env.SOFTWARE_FACTORY_WORKSPACE; else process.env.SOFTWARE_FACTORY_WORKSPACE = oldWorkspace;
            f.cleanup();
        }
    }
});
