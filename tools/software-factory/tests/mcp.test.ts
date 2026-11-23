// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport, getDefaultEnvironment } from "@modelcontextprotocol/sdk/client/stdio.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { ElicitRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { createFactoryMcp } from "../server/mcp.ts";
import { actor, fixture, requirements, transportFixture } from "./helpers.ts";
import { object } from "../core/contracts.ts";

for (const forms of [false, true]) test(`MCP shares Factory state and requires actual host confirmation (forms=${forms})`, async () => {
    const f = transportFixture();
    const server = createFactoryMcp(f.app);
    const client = new Client({ name: "VS Code protocol fixture", version: "1" },
        { capabilities: forms ? { elicitation: { form: {} } } : {} });
    const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
    let decision = "not_now";
    let formsShown = 0;
    if (forms) client.setRequestHandler(ElicitRequestSchema, async (request) => {
        formsShown++;
        assert.equal(request.params.mode, "form");
        return { action: "accept" as const, content: { decision } };
    });
    try {
        await server.connect(serverTransport);
        await client.connect(clientTransport);
        assert.ok((await client.listTools()).tools.some((tool) => tool.name === "software_factory_command"));
        const call = async (name: string, args: Record<string, unknown>) => {
            const result = await client.callTool({ name, arguments: args });
            const content = result.content as { type: string; text: string }[];
            return { error: result.isError, data: object(JSON.parse(content[0]!.text), "MCP result") };
        };
        const created = await call("software_factory_create_feature", { requestId: "mcp-feature", input: requirements() });
        const id = String(created.data.id);
        const feature = f.app.registry.get(id);
        assert.equal(feature.current.title, requirements().title);
        const doctor = await call("software_factory_doctor", {});
        assert.equal(doctor.data.nativeSessionJoined, false);
        assert.equal(doctor.data.confirmationSupported, forms);
        const approval = { featureId: id, expectedRevision: feature.current.id };
        const declined = await call("software_factory_approve_requirements", approval);
        assert.equal(f.app.registry.get(id).approvals.length, 0);
        if (forms) {
            assert.equal(declined.data.outcome, "not_approved");
            decision = "approve";
            await call("software_factory_approve_requirements", approval);
            assert.equal(f.app.registry.get(id).approvals.length, 1);
            assert.equal(formsShown, 2);
            assert.equal(f.app.lifecycle.store.state(id).sessionBinding, undefined, "MCP must never bind its transport identity as a native owner");
        } else {
            assert.equal(declined.error, true);
            assert.equal(object(declined.data.error, "Error").code, "confirmation_unavailable");
        }
        await assert.rejects(() => call("software_factory_approve_requirements", { ...approval, approved: true }),
            /Invalid|invalid|additional/i);
    } finally { await client.close(); await server.close(); f.cleanup(); }
});

test("MCP rejects a confirmation whose scope changed while the form was open", async () => {
    const f = transportFixture();
    const server = createFactoryMcp(f.app);
    const client = new Client({ name: "MCP test", version: "1" }, { capabilities: { elicitation: { form: {} } } });
    const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
    const feature = f.app.execute({ kind: "create_feature", requestId: "stale-mcp", input: requirements() }, actor);
    client.setRequestHandler(ElicitRequestSchema, async () => {
        f.app.execute({ kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Changed while awaiting confirmation" } }, actor);
        return { action: "accept" as const, content: { decision: "approve" } };
    });
    try {
        await server.connect(serverTransport);
        await client.connect(clientTransport);
        const result = await client.callTool({ name: "software_factory_approve_requirements",
            arguments: { featureId: feature.id, expectedRevision: feature.current.id } });
        assert.equal(result.isError, true);
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
    } finally { await client.close(); await server.close(); f.cleanup(); }
});

test("MCP cancellation invalidates a late form response without approving", async () => {
    const f = transportFixture();
    const server = createFactoryMcp(f.app);
    const client = new Client({ name: "MCP test", version: "1" }, { capabilities: { elicitation: { form: {} } } });
    const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
    const feature = f.app.execute({ kind: "create_feature", requestId: "cancel-mcp", input: requirements() }, actor);
    let formOpened!: () => void;
    let respond!: (value: { action: "accept"; content: { decision: string } }) => void;
    const opened = new Promise<void>((resolve) => { formOpened = resolve; });
    const response = new Promise<{ action: "accept"; content: { decision: string } }>((resolve) => { respond = resolve; });
    const controller = new AbortController();
    client.setRequestHandler(ElicitRequestSchema, async () => { formOpened(); return response; });
    try {
        await server.connect(serverTransport);
        await client.connect(clientTransport);
        const call = client.callTool({ name: "software_factory_approve_requirements",
            arguments: { featureId: feature.id, expectedRevision: feature.current.id } }, undefined, { signal: controller.signal });
        const rejected = assert.rejects(call, /abort|cancel/i);
        await opened;
        controller.abort();
        await rejected;
        await new Promise((resolve) => setTimeout(resolve, 20));
        respond({ action: "accept", content: { decision: "approve" } });
        await new Promise((resolve) => setTimeout(resolve, 20));
        assert.equal(f.app.registry.get(feature.id).approvals.length, 0);
    } finally { await client.close(); await server.close(); f.cleanup(); }
});

test("stdio MCP entry point exposes the shared registry without launching native work", async () => {
    const f = fixture();
    const client = new Client({ name: "stdio fixture", version: "1" });
    const transport = new StdioClientTransport({
        command: process.execPath,
        args: [fileURLToPath(new URL("../mcp.ts", import.meta.url)), "--workspace", f.workspace],
        cwd: f.workspace, env: { ...getDefaultEnvironment(), SOFTWARE_FACTORY_HOME: f.directory },
        stderr: "pipe",
    });
    try {
        await client.connect(transport);
        await client.callTool({ name: "software_factory_create_feature",
            arguments: { requestId: "stdio", input: requirements() } });
        const saved = f.app.registry.list();
        assert.equal(saved.length, 1);
        assert.equal(f.app.lifecycle.store.state(saved[0]!.id).executions.length, 0);
        assert.equal(f.app.registry.databasePath, path.join(f.directory, "registry.sqlite"));
    } finally { await client.close(); f.cleanup(); }
});
