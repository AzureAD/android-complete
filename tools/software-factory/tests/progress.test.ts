// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { FactoryError } from "../core/contracts.ts";
import { runtimeHealth } from "../core/runtime-health.ts";
import { nextStep, type AvailableGate } from "../core/progress.ts";
import { actor, fixture, requirements } from "./helpers.ts";

test("installation diagnostics reject missing, mismatched and incomplete platform runtimes", () => {
    const f = fixture();
    try {
        const root = path.join(f.directory, "fake-sdk");
        mkdirSync(root);
        const target = `${process.platform}-${process.arch}`;
        const name = `@github/copilot-sdk-${target}`;
        writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "fixture", optionalDependencies: { [name]: "1.2.3" } }));
        const missing = runtimeHealth(root);
        assert.equal(missing.ready, false);
        assert.match(missing.recoveryCommand!, /--include=optional/);
        const runtime = path.join(root, "node_modules", ...name.split("/"));
        mkdirSync(runtime, { recursive: true });
        writeFileSync(path.join(runtime, "package.json"), JSON.stringify({ version: "9.0.0" }));
        assert.match(runtimeHealth(root).message, /must match/);
        writeFileSync(path.join(runtime, "package.json"), JSON.stringify({ version: "1.2.3" }));
        assert.equal(runtimeHealth(root).ready, false);
        const binaries = path.join(runtime, "prebuilds", target);
        mkdirSync(binaries, { recursive: true });
        writeFileSync(path.join(binaries, process.platform === "win32" ? "copilot-runtime.exe" : "copilot-runtime"), "fixture");
        writeFileSync(path.join(binaries, "runtime.node"), "");
        assert.equal(runtimeHealth(root).ready, false);
        writeFileSync(path.join(binaries, "runtime.node"), "fixture");
        assert.equal(runtimeHealth(root).ready, true);
    } finally { f.cleanup(); }
});

test("a runtime loader failure is a durable chat-origin failure, never an invisible or successful start", async (t) => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "startup-failure",
            input: { title: "Broker throttling", description: "Honor Retry-After for matching silent requests." } }, actor);
        t.mock.method(f.app, "runtime", async () => { throw new FactoryError("runtime_unavailable", "The Windows platform runtime package is missing.", 503); });
        const command = { kind: "start_workflow", featureId: feature.id, workflow: "intake" };
        await assert.rejects(() => f.app.dispatch(command, actor), /confirmation/);
        assert.equal(f.app.lifecycle.store.state(feature.id).executions.length, 0);
        const result = await f.app.dispatch(command, actor, { granted: true, revisionId: f.app.prepare(command).consentKey!, actor });
        assert.equal(result.outcome, "workflow_failed");
        const view = f.app.lifecycle.store.view(feature.id);
        assert.equal(view.factory.executions[0]?.status, "error");
        assert.equal(view.factory.executions[0]?.runtimeId, null);
        assert.equal(view.factory.executions[0]?.requestedBy?.channel, "chat");
        assert.equal(view.approvals.length, 0);
        assert.ok(view.activity.some((entry) => entry.source === "Copilot chat" && entry.detail.includes("Windows platform")));
        const step = nextStep(view, []);
        assert.equal(step.failed, true);
        assert.equal(step.action?.label, "Try again");
        assert.deepEqual(step.action?.command, command);
        assert.equal(view.current.id, feature.current.id);
    } finally { f.cleanup(); }
});

test("startup source inspection errors remain visible after admission without starting any agent", async () => {
    const f = fixture();
    let stopped = false;
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "bad-source",
            input: { title: "Draft", description: "Document local tests." } }, actor);
        const runtime = await f.app.runtime();
        const execution = await runtime.start(feature.id, "intake", actor);
        await f.app.shutdown();
        stopped = true;
        const { Application } = await import("../core/application.ts");
        const reopened = new Application(f.options);
        try {
            const failed = reopened.lifecycle.store.execution(feature.id, execution.id);
            assert.equal(failed.status, "error");
            assert.equal(failed.runtimeId, null);
            assert.ok(failed.error);
            assert.equal(reopened.registry.get(feature.id).approvals.length, 0);
        } finally { reopened.close(); }
    } finally { if (!stopped) await f.app.shutdown(); f.cleanup(); }
});

test("concurrent runtime access shares one control owner", async () => {
    const f = fixture();
    try {
        const [first, second] = await Promise.all([f.app.runtime(), f.app.runtime()]);
        assert.equal(first, second);
    } finally { await f.app.shutdown(); f.cleanup(); }
});

test("the guided view prioritizes active work, the current approval and explicit continuation", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "progress", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const run = f.app.lifecycle.begin(feature.id, "design", actor);
        f.app.lifecycle.store.updateExecution(feature.id, run.id, { status: "running", phase: "Independent design reviewer" });
        let view = f.app.lifecycle.store.view(feature.id);
        assert.match(nextStep(view, []).description, /Started in Copilot chat/);
        assert.match(nextStep(view, []).description, /Independent design reviewer/);
        f.app.lifecycle.store.updateExecution(feature.id, run.id, { status: "paused", phase: "design approval" });
        view = f.app.lifecycle.store.view(feature.id);
        const gate: AvailableGate = { available: true, stage: "design", ready: true, approved: false, scopeHash: "exact-scope", blockers: [] };
        assert.equal(nextStep(view, [gate]).gate?.stage, "design");
        assert.equal(nextStep(view, [gate]).action, undefined, "review must precede continuation");
        const approved = nextStep(view, [{ ...gate, approved: true }]);
        assert.equal(approved.action?.label, "Continue workflow");
        assert.deepEqual(approved.action?.command, { kind: "control_run", featureId: feature.id, executionId: run.id, action: "resume" });
        assert.ok(view.activity.some((entry) => entry.detail.includes("Independent design reviewer")));
        assert.ok(view.activity.some((entry) => entry.detail.includes("design approval")));
    } finally { f.cleanup(); }
});

test("the guided view does not skip blocked backlog authority or hide an approved dispatch wave", () => {
    const f = fixture();
    try {
        const feature = f.app.execute({ kind: "create_feature", requestId: "delivery-progress", input: requirements() }, actor);
        f.app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id }, actor, { granted: true, revisionId: feature.current.id, actor });
        const view = f.app.lifecycle.store.view(feature.id);
        view.currentArtifacts.design = "fixture-design";
        view.currentArtifacts.plan = "fixture-plan";
        const approved = (stage: AvailableGate["stage"]): AvailableGate => ({ available: true, stage, ready: true, approved: true, blockers: [] });
        const blocked: AvailableGate = { available: true, stage: "backlog", ready: false, approved: false, blockers: ["Choose the ADO area path."] };
        const step = nextStep(view, [approved("design"), approved("plan"), blocked]);
        assert.equal(step.gate?.stage, "backlog");
        assert.match(step.description, /Choose the ADO area/);
        assert.equal(step.action, undefined, "a monitor run is not the next step when backlog is blocked");
        const dispatch = nextStep(view, [approved("design"), approved("plan"), approved("backlog"), approved("dispatch")]);
        assert.equal(dispatch.action?.label, "Dispatch approved work");
        assert.equal(dispatch.action?.command?.kind, "dispatch_work");
    } finally { f.cleanup(); }
});
