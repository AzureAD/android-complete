// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { startServer } from "../server/server.ts";
import type { QaResponder } from "../server/qa.ts";
import { MAX_COMMAND_BYTES } from "../core/contracts.ts";
import { actor, transportFixture as fixture, requirements } from "./helpers.ts";

async function setup(qaResponder?: QaResponder) {
    const f = fixture();
    const assets = path.join(f.directory, "assets");
    mkdirSync(assets);
    writeFileSync(path.join(assets, "index.html"), "<!doctype html><title>Software Factory</title>");
    const server = await startServer({ application: f.app, assets, qaResponder });
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

test("Studio AI Q&A streams read-only multi-turn answers, tracks pinned context, blocks concurrent turns per feature, supports session reset, and never mutates Factory state", async () => {
    let releaseFirstTurn: (() => void) | undefined;
    const firstTurnStarted = new Promise<void>((resolve) => {
        releaseFirstTurn = () => resolve();
    });
    let gateFirstTurn = new Promise<void>(() => {});
    gateFirstTurn = new Promise<void>((resolve) => {
        const prev = releaseFirstTurn;
        releaseFirstTurn = () => {
            prev?.();
            resolve();
        };
    });

    const seenTurns: Array<{ sessionId: string; turnIndex: number; question: string; contextTitle?: string; contextPack: string }> = [];
    let notifyTurn1Entered: (() => void) | undefined;
    const turn1Entered = new Promise<void>((resolve) => { notifyTurn1Entered = resolve; });

    const responder: QaResponder = async (turn) => {
        seenTurns.push({
            sessionId: turn.sessionId,
            turnIndex: turn.turnIndex,
            question: turn.question,
            contextTitle: turn.context?.title,
            contextPack: turn.contextPack,
        });
        turn.onPhase("Inspecting design and source files…");
        turn.onStepStart("call-1", "grep", "LastRequestTelemetry in common");
        turn.onDelta("Analyzing `LastRequestTelemetry`… ");
        if (turn.turnIndex === 1) {
            notifyTurn1Entered?.();
            await gateFirstTurn;
        }
        turn.onStepComplete("call-1", true);
        const finalAnswer = `Analyzing \`LastRequestTelemetry\`… Grounded answer for turn #${turn.turnIndex} (${turn.context?.title ?? "general"}).`;
        return finalAnswer;
    };

    const f = await setup(responder);
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "qa-feature", input: requirements() }, actor);
        const beforeSnapshot = f.app.lifecycle.store.view(feature.id);

        // CSRF enforcement on POST /api/features/:featureId/qa
        const missingCsrf = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, {
            method: "POST",
            headers: { ...f.headers, "X-Software-Factory-CSRF": "" },
            body: JSON.stringify({ action: "ask", question: "Should fail CSRF" }),
        });
        assert.equal(missingCsrf.status, 403);

        // Initial GET returns empty Q&A state + summary
        const initialRes = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, { headers: f.headers });
        assert.equal(initialRes.status, 200);
        const initialQa = await initialRes.json() as {
            activeSession: unknown;
            entries: unknown[];
            contextSummary: { requirementsCount: number };
        };
        assert.equal(initialQa.activeSession, null);
        assert.equal(initialQa.entries.length, 0);
        assert.equal(initialQa.contextSummary.requirementsCount, beforeSnapshot.current.requirements.length);

        // Turn 1 with pinned context starts asynchronously and exposes live partial delta + running step
        const ask1Res = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, {
            method: "POST",
            headers: f.headers,
            body: JSON.stringify({
                action: "ask",
                question: "Why did we choose a cap of 25 failed requests?",
                context: {
                    kind: "design-section",
                    title: "5. State, Failures & Reversibility",
                    snippet: "Cap failed entries at 25 to respect HTTP header limits.",
                },
            }),
        });
        assert.equal(ask1Res.status, 202);
        await turn1Entered;

        const midFlightRes = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, { headers: f.headers });
        const midFlightQa = await midFlightRes.json() as {
            entries: Array<{ status: string; answer: string; livePhase: string | null; liveSteps: Array<{ toolName: string; status: string }> }>;
        };
        assert.equal(midFlightQa.entries.length, 1);
        assert.equal(midFlightQa.entries[0]?.status, "running");
        assert.ok(midFlightQa.entries[0]?.answer.includes("Analyzing `LastRequestTelemetry`"));
        assert.equal(midFlightQa.entries[0]?.liveSteps[0]?.toolName, "grep");
        assert.equal(midFlightQa.entries[0]?.liveSteps[0]?.status, "running");

        // Concurrent question on the same feature returns 409 qa_busy instead of corrupting turn order
        const busyRes = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, {
            method: "POST",
            headers: f.headers,
            body: JSON.stringify({ action: "ask", question: "Concurrent question while running" }),
        });
        assert.equal(busyRes.status, 409);

        // Release Turn 1 and poll until completed
        releaseFirstTurn?.();
        let completedQa: {
            activeSession: { sessionId: string; turnCount: number } | null;
            entries: Array<{ id: string; sessionId: string; turnIndex: number; status: string; answer: string; liveSteps: Array<{ status: string }> }>;
        } | undefined;
        for (let attempt = 0; attempt < 40; attempt += 1) {
            const poll = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, { headers: f.headers });
            const body = await poll.json() as NonNullable<typeof completedQa>;
            if (body.entries[0]?.status === "completed") {
                completedQa = body;
                break;
            }
            await new Promise((resolve) => setTimeout(resolve, 25));
        }
        assert.ok(completedQa?.activeSession);
        const firstSessionId = completedQa.activeSession.sessionId;
        assert.equal(completedQa.activeSession.turnCount, 1);
        assert.ok(completedQa.entries[0]?.answer.includes("Grounded answer for turn #1"));
        assert.equal(completedQa.entries[0]?.liveSteps[0]?.status, "completed");

        // Turn 2 reuses the same multi-turn sessionId
        const ask2Res = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, {
            method: "POST",
            headers: f.headers,
            body: JSON.stringify({ action: "ask", question: "What happens if an older app sends pipes?" }),
        });
        assert.equal(ask2Res.status, 202);
        for (let attempt = 0; attempt < 40; attempt += 1) {
            const poll = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, { headers: f.headers });
            const body = await poll.json() as NonNullable<typeof completedQa>;
            if (body.entries[1]?.status === "completed") {
                completedQa = body;
                break;
            }
            await new Promise((resolve) => setTimeout(resolve, 25));
        }
        assert.equal(completedQa?.activeSession?.sessionId, firstSessionId);
        assert.equal(completedQa?.activeSession?.turnCount, 2);
        assert.equal(seenTurns.length, 2);
        assert.equal(seenTurns[0]?.sessionId, firstSessionId);
        assert.equal(seenTurns[1]?.sessionId, firstSessionId);
        assert.ok(seenTurns[0]?.contextPack.includes(feature.current.title));

        // Reset closes active session while keeping prior Q&A history visible, and Turn 3 starts a fresh sessionId
        const resetRes = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, {
            method: "POST",
            headers: f.headers,
            body: JSON.stringify({ action: "reset" }),
        });
        assert.equal(resetRes.status, 200);
        const afterReset = await resetRes.json() as NonNullable<typeof completedQa>;
        assert.equal(afterReset.activeSession, null);
        assert.equal(afterReset.entries.length, 2);

        const ask3Res = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, {
            method: "POST",
            headers: f.headers,
            body: JSON.stringify({ action: "ask", question: "Fresh session question" }),
        });
        assert.equal(ask3Res.status, 202);
        for (let attempt = 0; attempt < 40; attempt += 1) {
            const poll = await fetch(`${f.server.url}/api/features/${feature.id}/qa`, { headers: f.headers });
            const body = await poll.json() as NonNullable<typeof completedQa>;
            if (body.entries[2]?.status === "completed") {
                completedQa = body;
                break;
            }
            await new Promise((resolve) => setTimeout(resolve, 25));
        }
        assert.ok(completedQa?.activeSession);
        assert.notEqual(completedQa.activeSession.sessionId, firstSessionId);
        assert.equal(completedQa.activeSession.turnCount, 1);
        assert.equal(completedQa.entries.length, 3);

        // Verify zero mutation of FactoryFeature revisions, approvals, artifacts, executions, or status
        const afterSnapshot = f.app.lifecycle.store.view(feature.id);
        assert.deepEqual(afterSnapshot, beforeSnapshot);
    } finally {
        releaseFirstTurn?.();
        await f.cleanup();
    }
});