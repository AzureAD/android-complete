// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { startServer } from "../server/server.ts";
import { MAX_COMMAND_BYTES } from "../core/contracts.ts";
import { actor, transportFixture as fixture, requirements } from "./helpers.ts";

async function setup() {
    const f = fixture();
    const assets = path.join(f.directory, "assets");
    mkdirSync(assets);
    writeFileSync(path.join(assets, "index.html"), "<!doctype html><title>Software Factory</title>");
    const server = await startServer({ application: f.app, assets });
    const bootstrap = await fetch(`${server.url}/api/session`);
    const cookie = bootstrap.headers.get("set-cookie")?.split(";")[0];
    assert.ok(cookie);
    const session = await bootstrap.json() as { csrf: string };
    const headers = { Cookie: cookie, Origin: server.url, "Content-Type": "application/json", "X-Software-Factory-CSRF": session.csrf };
    return { ...f, server, headers, cleanup: async () => { await server.close(); f.cleanup(); } };
}

test("UI HTTP commands and chat/core reads share exact revisions and approval rules", async () => {
    const f = await setup();
    try {
        const response = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers,
            body: JSON.stringify({ kind: "create_feature", requestId: "web-request", input: requirements() }),
        });
        assert.equal(response.status, 200);
        const feature = f.app.registry.list()[0];
        assert.ok(feature);
        const command = { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id };
        const preview = f.app.prepare(command);
        const approval = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: { ...f.headers, "X-Software-Factory-Scope": preview.consentKey! },
            body: JSON.stringify(command),
        });
        assert.equal(approval.status, 200);
        assert.equal(f.app.registry.get(feature.id).status, "ready_for_design");
        f.app.execute({
            kind: "revise_requirements", featureId: feature.id, expectedRevision: feature.current.id,
            input: { ...requirements(), title: "Changed in chat" },
        }, actor);
        const stale = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers,
            body: JSON.stringify({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }),
        });
        assert.equal(stale.status, 409);
        const result = await fetch(`${f.server.url}/api/features`, { headers: f.headers });
        const payload = await result.json() as { features: unknown[] };
        assert.deepEqual(payload.features, f.app.registry.list().map((entry) => f.app.lifecycle.store.view(entry.id)));
    } finally { await f.cleanup(); }
});

test("HTTP requires local host/origin, session cookie and anti-forgery token", async () => {
    const f = await setup();
    try {
        const body = JSON.stringify({ kind: "create_feature", requestId: "rejected", input: requirements() });
        const cases = [
            { ...f.headers, Origin: "https://example.com" },
            { ...f.headers, "X-Software-Factory-CSRF": "" },
            { ...f.headers, Cookie: "" },
        ];
        for (const headers of cases) {
            const response = await fetch(`${f.server.url}/api/commands`, { method: "POST", headers, body });
            assert.ok([401, 403].includes(response.status));
        }
        const wrongHost = await new Promise<number>((resolve, reject) => {
            const request = http.request(`${f.server.url}/api/features`, {
                headers: { ...f.headers, Host: "attacker.example" },
            }, (response) => { response.resume(); resolve(response.statusCode || 0); });
            request.on("error", reject);
            request.end();
        });
        assert.equal(wrongHost, 403);
        assert.equal(f.app.registry.list().length, 0);
        const forged = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers, body: JSON.stringify({ kind: "create_feature", requestId: "forged", input: requirements(), actor: { channel: "chat" } }),
        });
        assert.equal(forged.status, 400);
        const malformed = await fetch(`${f.server.url}/api/commands`, { method: "POST", headers: f.headers, body: "{" });
        assert.equal(malformed.status, 400);
        const crossSite = await fetch(`${f.server.url}/api/session`, { headers: { "Sec-Fetch-Site": "cross-site" } });
        assert.equal(crossSite.status, 403);
        const unauthenticated = await fetch(`${f.server.url}/api/features`);
        assert.equal(unauthenticated.status, 401);
        const wrongType = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: { ...f.headers, "Content-Type": "application/jsonp" }, body,
        });
        assert.equal(wrongType.status, 415);
        const oversized = await fetch(`${f.server.url}/api/commands`, {
            method: "POST", headers: f.headers,
            body: JSON.stringify({ kind: "create_feature", requestId: "oversized", input: { ...requirements(), title: "x".repeat(MAX_COMMAND_BYTES) } }),
        });
        assert.equal(oversized.status, 413);
        assert.equal(f.app.registry.list().length, 0);
    } finally { await f.cleanup(); }
});

test("GET /api/executions/:featureId/:executionId/transcript indexes full subagent prompts, reasoning thoughts, messages, and tool calls from session state", async () => {
    const f = await setup();
    const previousStateRoot = process.env.SOFTWARE_FACTORY_SESSION_STATE_ROOT;
    const stateRoot = path.join(f.directory, "session-state");
    process.env.SOFTWARE_FACTORY_SESSION_STATE_ROOT = stateRoot;
    try {
        const { DatabaseSync } = await import("node:sqlite");
        const feature = f.app.execute({ kind: "create_feature", requestId: "tx-feature", input: requirements() }, actor);
        f.app.execute(
            { kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id },
            actor,
            { granted: true, revisionId: feature.current.id, actor },
        );
        const run = f.app.lifecycle.begin(feature.id, "design", actor);
        const sessionId = "session-tx-test-1";
        const runtimeId = "wf-run-design-1";
        f.app.lifecycle.store.updateExecution(feature.id, run.id, {
            runtimeId,
            sessionId,
            status: "completed",
            phase: "Ready for human design review",
        });

        const sessionDir = path.join(stateRoot, sessionId);
        mkdirSync(sessionDir, { recursive: true });
        const db = new DatabaseSync(path.join(sessionDir, "session.db"));
        db.exec(`
            CREATE TABLE workflow_runs (run_id TEXT PRIMARY KEY, parent_run_id TEXT, root_run_id TEXT, workflow_name TEXT);
            CREATE TABLE workflow_agents (run_id TEXT, agent_id TEXT, label TEXT, agent_type TEXT, status TEXT, requested_model TEXT, resolved_model TEXT, started_at_ms INTEGER, completed_at_ms INTEGER, active_ms INTEGER);
            CREATE TABLE workflow_agent_observability (run_id TEXT, agent_id TEXT, resolved_reasoning_effort TEXT);
            CREATE TABLE workflow_credit_charges (run_id TEXT, charge_id TEXT PRIMARY KEY, nano_aiu INTEGER);
            CREATE TABLE workflow_credit_charge_attribution (charge_id TEXT, run_id TEXT, agent_id TEXT);
            CREATE TABLE workflow_progress (run_id TEXT, seq INTEGER, kind TEXT, text TEXT, recorded_at INTEGER);
        `);
        db.prepare("INSERT INTO workflow_runs VALUES (?, NULL, ?, 'design')").run(runtimeId, runtimeId);
        db.prepare("INSERT INTO workflow_agents VALUES (?, 'ag-1', 'Design author', 'software-factory-design-author', 'completed', NULL, 'gemini-4-carbon', 1000, 9000, 8000)").run(runtimeId);
        db.prepare("INSERT INTO workflow_agent_observability VALUES (?, 'ag-1', 'high')").run(runtimeId);
        db.prepare("INSERT INTO workflow_credit_charges VALUES (?, 'chg-1', 62700000000)").run(runtimeId);
        db.prepare("INSERT INTO workflow_credit_charge_attribution VALUES ('chg-1', ?, 'ag-1')").run(runtimeId);
        db.prepare("INSERT INTO workflow_progress VALUES (?, 1, 'log', ?, 1763003460000)").run(
            runtimeId,
            `[workflow-milestone-v1]${JSON.stringify({ title: "Design draft completed", outcome: "success", summary: "10 sections authored." })}`,
        );
        db.close();

        const events = [
            { type: "user.message", agentId: "ag-1", timestamp: "2026-11-13T03:10:00.000Z", data: { content: "Author complete Android design.\n\nDATA:\n{\"stage0\":\"R1\"}" } },
            { type: "assistant.message", agentId: "ag-1", timestamp: "2026-11-13T03:10:05.000Z", data: { interactionId: "ix-1", turnId: "0", reasoningText: "**Analyzing IPC Envelope**\n\nChecking BrokerController wire compatibility first.", content: "I will inspect BrokerController before authoring the specification." } },
            { type: "session.usage_record", agentId: "ag-1", timestamp: "2026-11-13T03:10:05.100Z", data: { interactionId: "ix-1", usage: { inputTokens: 4200, reasoningTokens: 580, outputTokens: 120, duration: 4800 } } },
            { type: "tool.execution_start", agentId: "ag-1", timestamp: "2026-11-13T03:10:06.000Z", data: { toolCallId: "tc-1", toolName: "software_factory_read", arguments: { repository: "common", path: "src/BrokerController.kt", startLine: 1, endLine: 40 } } },
            { type: "tool.execution_complete", agentId: "ag-1", timestamp: "2026-11-13T03:10:06.400Z", data: { toolCallId: "tc-1", success: true, result: { content: "[Application source receipt]\nrepository: common\n\nclass BrokerController { fun execute() {} }" } } },
            { type: "subagent.completed", agentId: "ag-1", timestamp: "2026-11-13T03:10:15.000Z", data: { totalToolCalls: 1, totalTokens: 4900, durationMs: 8000 } },
        ];
        writeFileSync(path.join(sessionDir, "events.jsonl"), events.map((e) => JSON.stringify(e)).join("\n") + "\n");

        const res = await fetch(`${f.server.url}/api/executions/${feature.id}/${run.id}/transcript`, { headers: f.headers });
        assert.equal(res.status, 200);
        const body = await res.json() as {
            source: string;
            selectedAgentId: string;
            agents: Array<{ agentId: string; thoughtCount: number; messageCount: number; toolCallCount: number; nanoAiu: number; reasoningEffort: string }>;
            progress: Array<{ title: string; summary?: string }>;
            transcript: Array<{ kind: string; content: string; attachedDataPreview?: string; toolOutput?: string; tokens?: { reasoning: number } }>;
        };
        assert.equal(body.source, "session_events");
        assert.equal(body.selectedAgentId, "ag-1");
        assert.equal(body.agents.length, 1);
        assert.equal(body.agents[0]?.thoughtCount, 1);
        assert.equal(body.agents[0]?.messageCount, 1);
        assert.equal(body.agents[0]?.toolCallCount, 1);
        assert.equal(body.agents[0]?.nanoAiu, 62700000000);
        assert.equal(body.agents[0]?.reasoningEffort, "high");
        assert.equal(body.progress[0]?.title, "Design draft completed");
        assert.deepEqual(body.transcript.map((i) => i.kind), ["prompt", "thought", "assistant", "tool_call", "lifecycle"]);
        assert.equal(body.transcript[0]?.content, "Author complete Android design.");
        assert.ok(body.transcript[0]?.attachedDataPreview?.includes("stage0"));
        assert.ok(body.transcript[1]?.content.includes("Analyzing IPC Envelope"));
        assert.equal(body.transcript[1]?.tokens?.reasoning, 580);
        assert.equal(body.transcript[2]?.content, "I will inspect BrokerController before authoring the specification.");
        assert.equal(body.transcript[3]?.toolOutput, "class BrokerController { fun execute() {} }");
    } finally {
        if (previousStateRoot === undefined) delete process.env.SOFTWARE_FACTORY_SESSION_STATE_ROOT;
        else process.env.SOFTWARE_FACTORY_SESSION_STATE_ROOT = previousStateRoot;
        await f.cleanup();
    }
});
