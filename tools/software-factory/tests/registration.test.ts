// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import test from "node:test";
import type { CopilotSession } from "@github/copilot-sdk";
import { awaitWorkflowRegistration, REGISTRATION_TOOL } from "../runtime/registration.ts";
import { ownedRuntimeEnvironment } from "../runtime/host.ts";
import { FactoryError } from "../core/contracts.ts";

function fixture() {
    const state = {
        status: "running" as "running" | "starting" | "failed" | "disabled" | null,
        reportedSession: "owned-session", fingerprint: "expected-code",
        names: ["software-factory-check", "software-factory-design"], toolCalls: 0, reloads: 0,
        repairOnReload: false, pendingTool: false, invalidJson: false,
    };
    const session = {
        sessionId: "owned-session",
        rpc: {
            extensions: {
                list: async () => ({ extensions: state.status ? [{ id: "project:SoftwareFactory", name: "SoftwareFactory", source: "project" as const, status: state.status }] : [] }),
                reload: async () => { state.reloads++; if (state.repairOnReload) state.status = "running"; },
            },
            tools: {
                execute: async (request: Parameters<CopilotSession["rpc"]["tools"]["execute"]>[0]) => {
                    assert.equal(request.name, REGISTRATION_TOOL);
                    state.toolCalls++;
                    if (state.pendingTool && state.toolCalls === 1) throw new Error("Tool not found while the extension joins.");
                    return state.invalidJson ? "invalid JSON" : JSON.stringify({
                        ready: true, sessionId: state.reportedSession, workflowNames: state.names, runtimeFingerprint: state.fingerprint,
                    });
                },
            },
        },
    };
    return { state, session };
}

test("registration is attested by the actual owning extension before execution", async () => {
    const f = fixture();
    const result = await awaitWorkflowRegistration(f.session, "software-factory-design", "expected-code");
    assert.equal(result.sessionId, f.session.sessionId);
    assert.ok(result.workflowNames.includes("software-factory-design"));
    assert.equal(f.state.toolCalls, 1);
    assert.equal(f.state.reloads, 0, "healthy registrations are not restarted");
});

test("pending registration is awaited and a failed activation can be reloaded once without retrying work", async () => {
    const pending = fixture();
    pending.state.pendingTool = true;
    await awaitWorkflowRegistration(pending.session, "software-factory-design", "expected-code");
    assert.equal(pending.state.toolCalls, 2);
    const failed = fixture();
    failed.state.status = "failed";
    failed.state.repairOnReload = true;
    await awaitWorkflowRegistration(failed.session, "software-factory-design", "expected-code");
    assert.equal(failed.state.reloads, 1);
    assert.equal(failed.state.toolCalls, 1);
});

test("disabled or missing extensions fail explicitly and are never silently enabled", async () => {
    const disabled = fixture();
    disabled.state.status = "disabled";
    await assert.rejects(() => awaitWorkflowRegistration(disabled.session, "software-factory-design", "expected-code"),
        (error: unknown) => error instanceof FactoryError && error.code === "workflow_extension_disabled");
    assert.equal(disabled.state.reloads, 0);
    assert.equal(disabled.state.toolCalls, 0);
    const missing = fixture();
    missing.state.status = null;
    await assert.rejects(() => awaitWorkflowRegistration(missing.session, "software-factory-design", "expected-code"), /No agents were started/);
    assert.equal(missing.state.reloads, 1);
    assert.equal(missing.state.toolCalls, 0);
});

test("wrong-session, missing-workflow, stale-code and malformed attestations cannot pass admission", async () => {
    for (const kind of ["session", "workflow", "fingerprint", "json"]) {
        const f = fixture();
        if (kind === "session") f.state.reportedSession = "another-session";
        if (kind === "workflow") f.state.names = ["software-factory-check"];
        if (kind === "fingerprint") f.state.fingerprint = "old-code";
        if (kind === "json") f.state.invalidJson = true;
        await assert.rejects(() => awaitWorkflowRegistration(f.session, "software-factory-design", "expected-code"),
            (error: unknown) => error instanceof FactoryError, kind);
        assert.equal(f.state.toolCalls, 1);
    }
});

test("owned runtime isolation removes only documented parent-extension bindings", () => {
    const parent = Object.freeze({
        SESSION_ID: "parent-session", COPILOT_SDK_PATH: "parent-sdk", COPILOT_EXTENSION_PARENT_PID: "123",
        PATH: "fixture-path", COPILOT_HOME: "fixture-home", ORG_POLICY_REQUIRED: "1",
        COPILOT_SDK_DEFAULT_CONNECTION: "inprocess",
    });
    const child = ownedRuntimeEnvironment(parent);
    for (const name of ["SESSION_ID", "COPILOT_SDK_PATH", "COPILOT_EXTENSION_PARENT_PID"]) assert.equal(child[name], undefined);
    assert.equal(child.PATH, parent.PATH);
    assert.equal(child.COPILOT_HOME, parent.COPILOT_HOME);
    assert.equal(child.ORG_POLICY_REQUIRED, "1", "security and other user/host settings are preserved");
    assert.equal(child.COPILOT_SDK_DEFAULT_CONNECTION, "inprocess", "transport is pinned through the SDK connection, not by stripping preferences");
    assert.equal(parent.SESSION_ID, "parent-session", "the shared parent environment is never mutated");
});
