// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import type { CopilotSession, WorkflowRunDetail, WorkflowRunResult } from "@github/copilot-sdk";
import { awaitWorkflowRegistration, REGISTRATION_TOOL } from "../runtime/registration.ts";
import { NativeRuntime, type NativeSession, type OwnedRuntimeClient } from "../runtime/controller.ts";
import { FACTORY_READER } from "../runtime/reader.ts";
import { loadedWorkflowFingerprint } from "../core/evidence.ts";
import { ownedRuntimeEnvironment } from "../runtime/host.ts";
import { FactoryError } from "../core/contracts.ts";
import { actor, fixture as appFixture, requirements } from "./helpers.ts";

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

function initGitWorkspace(workspace: string): void {
    const git = (...args: string[]) => execFileSync("git", args, { cwd: workspace, encoding: "utf8", stdio: "pipe" });
    git("init", "--quiet");
    git("config", "user.name", "Fixture");
    git("config", "user.email", "fixture@example.invalid");
    writeFileSync(path.join(workspace, "README.md"), "# fixture\n");
    git("add", "README.md");
    git("commit", "--quiet", "-m", "Initial commit");
}

function makeDetail(runId: string): WorkflowRunDetail {
    return {
        runId,
        workflowName: "software-factory-check",
        status: "running",
        observedAt: new Date().toISOString(),
        consumed: { totalSubagents: 1, activeExecutionMs: 25, aiCredits: 0 },
        declaredLimits: {},
        approved: null,
        agents: [{ label: "worker", status: "running", activeMs: 25 }],
    } as unknown as WorkflowRunDetail;
}

async function waitUntil(predicate: () => boolean, timeoutMs = 3_000): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (!predicate()) {
        if (Date.now() >= deadline) throw new Error("Timed out waiting for expected runtime state.");
        await new Promise((resolve) => setTimeout(resolve, 15));
    }
}

test("NativeRuntime persists runtimeId upon workflow admission so pause and cancel target pending runs and guard in-flight admission", async () => {
    const f = appFixture();
    let runtime: NativeRuntime | undefined;
    try {
        initGitWorkspace(f.workspace);
        const newApprovedFeature = (requestId: string) => {
            const created = f.app.execute({ kind: "create_feature", requestId, input: requirements() }, actor);
            f.app.execute({ kind: "approve_requirements", featureId: created.id, expectedRevision: created.current.id }, actor,
                { granted: true, revisionId: created.current.id, actor });
            return created;
        };

        const runs = new Map<string, { status: WorkflowRunResult["status"]; settle: (result: WorkflowRunResult) => void; promise: Promise<WorkflowRunResult> }>();
        const pausedIds: string[] = [];
        const cancelledIds: string[] = [];
        let runSeq = 0;
        let holdAdmission: (() => Promise<void>) | null = null;

        const session: NativeSession = {
            sessionId: "joined-admission-session",
            workflow: {
                run: async () => { throw new Error("Expected rpc.workflow.run + waitForRun path when available"); },
                waitForRun: async (runId: string) => runs.get(runId)!.promise,
                resume: async (runId: string) => ({ runId, status: "completed" } as WorkflowRunResult),
                pause: async (runId: string) => {
                    pausedIds.push(runId);
                    const result = { runId, status: "paused" } as WorkflowRunResult;
                    const entry = runs.get(runId);
                    if (entry) { entry.status = "paused"; entry.settle(result); }
                    return result;
                },
                cancel: async (runId: string) => {
                    cancelledIds.push(runId);
                    const result = { runId, status: "cancelled" } as WorkflowRunResult;
                    const entry = runs.get(runId);
                    if (entry) { entry.status = "cancelled"; entry.settle(result); }
                    return result;
                },
                getRun: async (runId: string) => ({ runId, status: runs.get(runId)?.status ?? "running" } as WorkflowRunResult),
                getRunDetail: async (runId: string) => makeDetail(runId),
            },
            rpc: {
                extensions: {
                    list: async () => ({ extensions: [{ id: "project:SoftwareFactory", name: "SoftwareFactory", source: "project" as const, status: "running" as const }] }),
                    reload: async () => {},
                },
                tools: {
                    initializeAndValidate: async () => ({ toolNames: [] }),
                    getCurrentMetadata: async () => ({ tools: ["view", "glob", "grep"].map((name) => ({ name, description: "fixture" })) }),
                    execute: async () => JSON.stringify({
                        ready: true,
                        sessionId: "joined-admission-session",
                        workflowNames: ["software-factory-check"],
                        runtimeFingerprint: loadedWorkflowFingerprint,
                        readerAgent: FACTORY_READER,
                    }),
                },
                workflow: {
                    run: async () => {
                        if (holdAdmission) await holdAdmission();
                        const runId = `native-run-${++runSeq}`;
                        let settle!: (result: WorkflowRunResult) => void;
                        const promise = new Promise<WorkflowRunResult>((resolve) => { settle = resolve; });
                        runs.set(runId, { status: "running", settle, promise });
                        return { runId, status: "running" } as WorkflowRunResult;
                    },
                },
            },
        };

        runtime = new NativeRuntime(f.app.lifecycle, session, f.app.shared);

        // 1. Admitted still-pending workflow persists runtimeId + status=running before settlement and dispatches pause.
        const feature1 = newApprovedFeature("admission-pause");
        const first = await runtime.start(feature1.id, "check", actor);
        await waitUntil(() => f.app.lifecycle.store.execution(feature1.id, first.id).runtimeId === "native-run-1");
        const activeFirst = f.app.lifecycle.store.execution(feature1.id, first.id);
        assert.equal(activeFirst.status, "running");
        assert.equal(activeFirst.runtimeId, "native-run-1");
        await runtime.control(feature1.id, first.id, "pause", actor);
        await waitUntil(() => f.app.lifecycle.store.execution(feature1.id, first.id).status === "paused");
        assert.deepEqual(pausedIds, ["native-run-1"]);

        // 2. Admitted still-pending workflow dispatches native cancel using persisted runtimeId instead of pre-admission shortcut.
        const feature2 = newApprovedFeature("admission-cancel");
        const second = await runtime.start(feature2.id, "check", actor);
        await waitUntil(() => f.app.lifecycle.store.execution(feature2.id, second.id).runtimeId === "native-run-2");
        const cancelQueued = await runtime.control(feature2.id, second.id, "cancel", actor) as Record<string, unknown>;
        assert.equal(cancelQueued.status, "queued");
        assert.equal(cancelQueued.agentsStarted, undefined);
        await waitUntil(() => f.app.lifecycle.store.execution(feature2.id, second.id).status === "cancelled");
        assert.deepEqual(cancelledIds, ["native-run-2"]);

        // 3. In-flight rpc.workflow.run admission does not falsely report agentsStarted: 0; it queues cancel and cancels once admitted.
        const feature3 = newApprovedFeature("admission-inflight");
        let releaseAdmission!: () => void;
        let admissionEntered = false;
        holdAdmission = () => {
            admissionEntered = true;
            return new Promise<void>((resolve) => { releaseAdmission = resolve; });
        };
        const third = await runtime.start(feature3.id, "check", actor);
        await waitUntil(() => admissionEntered);
        const inflightState = f.app.lifecycle.store.execution(feature3.id, third.id);
        assert.equal(inflightState.runtimeId, null);
        assert.ok(inflightState.admissionClaim, "admissionClaim is recorded before rpc.workflow.run");
        const inflightCancel = await runtime.control(feature3.id, third.id, "cancel", actor) as Record<string, unknown>;
        assert.equal(inflightCancel.status, "queued");
        assert.equal(inflightCancel.agentsStarted, undefined);
        holdAdmission = null;
        releaseAdmission();
        await waitUntil(() => f.app.lifecycle.store.execution(feature3.id, third.id).status === "cancelled");
        assert.equal(f.app.lifecycle.store.execution(feature3.id, third.id).runtimeId, "native-run-3");
        assert.deepEqual(cancelledIds, ["native-run-2", "native-run-3"]);

        // 4. Crash-uncertain admission (admissionClaim set, runtimeId null, offline owner) refuses false pre-admission cancel.
        const feature4 = newApprovedFeature("admission-uncertain");
        const uncertain = f.app.lifecycle.begin(feature4.id, "check", actor, {}, {}, undefined, {
            sessionId: "offline-owned-session", kind: "owned", established: true,
        });
        f.app.lifecycle.store.change(feature4.id, (state) => {
            const target = state.executions.find((item) => item.id === uncertain.id)!;
            target.admissionClaim = "crashed-owner-token";
        }, { localOnly: true });
        await assert.rejects(
            () => runtime!.control(feature4.id, uncertain.id, "cancel", actor),
            (error: unknown) => error instanceof FactoryError && error.code === "native_admission_uncertain",
        );
    } finally {
        await runtime?.close();
        f.cleanup();
    }
});

test("cold NativeRuntime reopens expired owned sessions for automatic recovery and pause/cancel without taking over live owners", async () => {
    const f = appFixture();
    let coldLiveRuntime: NativeRuntime | undefined;
    let coldRecoveryRuntime: NativeRuntime | undefined;
    try {
        initGitWorkspace(f.workspace);
        const newApprovedFeature = (requestId: string) => {
            const created = f.app.execute({ kind: "create_feature", requestId, input: requirements() }, actor);
            f.app.execute({ kind: "approve_requirements", featureId: created.id, expectedRevision: created.current.id }, actor,
                { granted: true, revisionId: created.current.id, actor });
            return created;
        };

        const resumedSessions: string[] = [];
        const pausedRuns: string[] = [];
        const cancelledRuns: string[] = [];
        const resumedRuns: string[] = [];
        const runStates = new Map<string, WorkflowRunResult["status"]>();

        const makeSession = (sessionId: string): NativeSession => ({
            sessionId,
            workflow: {
                run: async () => ({ runId: "unused", status: "completed" } as WorkflowRunResult),
                resume: async (runId: string) => {
                    resumedRuns.push(runId);
                    runStates.set(runId, "completed");
                    return { runId, status: "completed", result: { recovered: true } } as WorkflowRunResult;
                },
                pause: async (runId: string) => {
                    pausedRuns.push(runId);
                    runStates.set(runId, "paused");
                    return { runId, status: "paused" } as WorkflowRunResult;
                },
                cancel: async (runId: string) => {
                    cancelledRuns.push(runId);
                    runStates.set(runId, "cancelled");
                    return { runId, status: "cancelled" } as WorkflowRunResult;
                },
                getRun: async (runId: string) => ({ runId, status: runStates.get(runId) ?? "running" } as WorkflowRunResult),
                getRunDetail: async (runId: string) => makeDetail(runId),
            },
            rpc: {
                extensions: {
                    list: async () => ({ extensions: [{ id: "project:SoftwareFactory", name: "SoftwareFactory", source: "project" as const, status: "running" as const }] }),
                    reload: async () => {},
                },
                tools: {
                    initializeAndValidate: async () => ({ toolNames: [] }),
                    getCurrentMetadata: async () => ({ tools: ["view", "glob", "grep"].map((name) => ({ name, description: "fixture" })) }),
                    execute: async () => JSON.stringify({
                        ready: true,
                        sessionId,
                        workflowNames: ["software-factory-check"],
                        runtimeFingerprint: loadedWorkflowFingerprint,
                        readerAgent: FACTORY_READER,
                    }),
                },
            },
        });

        const makeOwnedClient = (): OwnedRuntimeClient => ({
            start: async () => {},
            stop: async () => [],
            createSession: async (cfg) => makeSession(cfg.sessionId!),
            resumeSession: async (sessionId) => {
                resumedSessions.push(sessionId);
                return makeSession(sessionId);
            },
            startAhpHost: async (opts) => ({
                publishSession: async (sid: string) => ({ sessionId: sid, sessionUri: `ahp://${opts.computeId}/${sid}` }),
                dispose: async () => {},
            }) as unknown as Awaited<ReturnType<OwnedRuntimeClient["startAhpHost"]>>,
        });

        const seedOwnedRunning = (sessionId: string, runId: string, leaseAgeMs: number | "live") => {
            const feat = newApprovedFeature(`cold-${sessionId}`);
            const exec = f.app.lifecycle.begin(feat.id, "check", actor, {}, {}, undefined, {
                sessionId, kind: "owned", established: true,
            });
            f.app.lifecycle.store.updateExecution(feat.id, exec.id, {
                runtimeId: runId,
                status: "running",
                runtimeFingerprint: loadedWorkflowFingerprint,
            });
            f.app.lifecycle.store.heartbeat(sessionId, `prior-owner-${sessionId}`);
            if (leaseAgeMs !== "live") {
                f.registry.connection.prepare("UPDATE factory_resident_owner SET heartbeat = ? WHERE session_id = ?")
                    .run(Date.now() - leaseAgeMs, sessionId);
                assert.equal(f.app.lifecycle.store.ownerOnline(sessionId), false);
            } else {
                assert.equal(f.app.lifecycle.store.ownerOnline(sessionId), true);
            }
            return { featureId: feat.id, exec };
        };

        // 1. Live foreign owner holds non-expired lease: cold controller must NOT reopen or take over.
        runStates.set("run-live", "halted");
        const live = seedOwnedRunning("owned-live-session", "run-live", "live");
        coldLiveRuntime = new NativeRuntime(f.app.lifecycle, undefined, f.app.shared, makeOwnedClient());
        await coldLiveRuntime.control(live.featureId, live.exec.id, "pause", actor);
        assert.deepEqual(resumedSessions, [] as string[], "must never reopen a session owned by another live controller");
        assert.deepEqual(pausedRuns, [] as string[]);
        assert.deepEqual(resumedRuns, [] as string[]);
        assert.equal(f.app.lifecycle.store.ownsSession("owned-live-session", "prior-owner-owned-live-session"), true);
        assert.equal(f.app.lifecycle.store.state(live.featureId).controls.at(-1)?.status, "pending");
        await coldLiveRuntime.close();
        coldLiveRuntime = undefined;

        // 2. Expired owned leases: cold controller automatically recovers halted run AND dispatches explicit pause/cancel without refresh.
        runStates.set("run-expired-halted", "halted");
        runStates.set("run-expired-pause", "running");
        runStates.set("run-expired-cancel", "running");
        const autoSeeded = seedOwnedRunning("owned-expired-auto", "run-expired-halted", 20_000);
        const pauseSeeded = seedOwnedRunning("owned-expired-pause", "run-expired-pause", 20_000);
        const cancelSeeded = seedOwnedRunning("owned-expired-cancel", "run-expired-cancel", 20_000);

        coldRecoveryRuntime = new NativeRuntime(f.app.lifecycle, undefined, f.app.shared, makeOwnedClient());
        await coldRecoveryRuntime.control(pauseSeeded.featureId, pauseSeeded.exec.id, "pause", actor);
        await coldRecoveryRuntime.control(cancelSeeded.featureId, cancelSeeded.exec.id, "cancel", actor);

        await waitUntil(() =>
            f.app.lifecycle.store.execution(autoSeeded.featureId, autoSeeded.exec.id).status === "completed" &&
            f.app.lifecycle.store.execution(pauseSeeded.featureId, pauseSeeded.exec.id).status === "paused" &&
            f.app.lifecycle.store.execution(cancelSeeded.featureId, cancelSeeded.exec.id).status === "cancelled");

        assert.ok(resumedSessions.includes("owned-expired-auto"), "cold controller reopens expired auto-recovery session");
        assert.ok(resumedSessions.includes("owned-expired-pause"), "cold controller reopens expired session for explicit pause");
        assert.ok(resumedSessions.includes("owned-expired-cancel"), "cold controller reopens expired session for explicit cancel");
        assert.deepEqual(resumedRuns, ["run-expired-halted"]);
        assert.deepEqual(pausedRuns, ["run-expired-pause"]);
        assert.deepEqual(cancelledRuns, ["run-expired-cancel"]);
        assert.equal(f.app.lifecycle.store.ownerOnline("owned-expired-auto"), true);
        assert.equal(f.app.lifecycle.store.ownerOnline("owned-expired-pause"), true);
        assert.equal(f.app.lifecycle.store.ownerOnline("owned-expired-cancel"), true);
    } finally {
        await coldLiveRuntime?.close();
        await coldRecoveryRuntime?.close();
        f.cleanup();
    }
});

test("install.ps1 supports Layer 3 snapshot without cli.ts and rejects absent WebUI or CheckRuntime targets clearly", () => {
    const factoryRoot = fileURLToPath(new URL("../", import.meta.url));
    const pwsh = process.platform === "win32" ? "pwsh" : "pwsh";
    const tempRoot = mkdtempSync(path.join(tmpdir(), "sf-install-test-"));
    try {
        // Copy install.ps1, package.json, tsconfig.json, and core/ into an isolated fixture sharing node_modules + stub npm.
        copyFileSync(path.join(factoryRoot, "install.ps1"), path.join(tempRoot, "install.ps1"));
        copyFileSync(path.join(factoryRoot, "package.json"), path.join(tempRoot, "package.json"));
        mkdirSync(path.join(tempRoot, "core"), { recursive: true });
        for (const file of ["contracts.ts", "doctor.ts", "runtime-health.ts"]) {
            copyFileSync(path.join(factoryRoot, "core", file), path.join(tempRoot, "core", file));
        }
        symlinkSync(path.join(factoryRoot, "node_modules"), path.join(tempRoot, "node_modules"), process.platform === "win32" ? "junction" : "dir");

        const fakeBin = path.join(tempRoot, "bin");
        mkdirSync(fakeBin, { recursive: true });
        if (process.platform === "win32") {
            writeFileSync(path.join(fakeBin, "npm.cmd"), "@echo off\r\nexit /b 0\r\n");
        } else {
            writeFileSync(path.join(fakeBin, "npm"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
        }
        const env = { ...process.env, PATH: `${fakeBin}${path.delimiter}${process.env.PATH ?? ""}` };
        const script = path.join(tempRoot, "install.ps1");

        // 1. Default Layer 3 setup succeeds via core/runtime-health.ts + core/doctor.ts fallback when cli.ts is absent.
        const okOut = execFileSync(pwsh, ["-NoProfile", "-File", script], { env, encoding: "utf8", stdio: "pipe" });
        assert.match(okOut, /"product":\s*"Software Factory"/);
        assert.match(okOut, /Software Factory core is installed/);
        assert.doesNotMatch(okOut, /Optional UI:/);

        // 2. -WebUI fails fast with a clear message when web/package.json is absent in this snapshot.
        assert.throws(
            () => execFileSync(pwsh, ["-NoProfile", "-File", script, "-WebUI"], { env, encoding: "utf8", stdio: "pipe" }),
            /web\/package\.json.*not present/i,
        );

        // 3. -CheckRuntime fails fast with a clear message when scripts['check:runtime'] is absent in this snapshot.
        assert.throws(
            () => execFileSync(pwsh, ["-NoProfile", "-File", script, "-CheckRuntime"], { env, encoding: "utf8", stdio: "pipe" }),
            /check:runtime.*not present/i,
        );
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});
