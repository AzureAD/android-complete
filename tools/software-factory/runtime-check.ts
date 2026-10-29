// Copyright (c) Microsoft Corporation. All rights reserved.

import { Application, defaultWorkspace } from "./core/application.ts";
import { createClient, sessionConfiguration } from "./runtime/host.ts";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { awaitWorkflowRegistration, REGISTRATION_TOOL } from "./runtime/registration.ts";
import { runtimeFingerprint } from "./core/evidence.ts";

const diagnosticHome = mkdtempSync(path.join(os.tmpdir(), "software-factory-registration-"));
let client = createClient(defaultWorkspace(), diagnosticHome);
try {
    await client.start();
    const session = await client.createSession({
        ...sessionConfiguration(defaultWorkspace(), "Software Factory zero-agent runtime check"),
        availableTools: [`custom:${REGISTRATION_TOOL}`],
        onPermissionRequest: () => ({ kind: "reject", feedback: "This check permits no agent tools or external mutations." }),
    });
    const registration = await awaitWorkflowRegistration(session, "software-factory-design", runtimeFingerprint());
    const productionRegistration: { workflow: string; sessionId: string; nativeRunId: string; scopeGuardVerified: boolean; agentsStarted: number }[] = [];
    for (const kind of ["intake", "design", "plan", "monitor"]) {
        const workflow = `software-factory-${kind}`;
        const probe = await session.workflow.run(workflow, {
            args: { featureId: "__registration_probe_missing_feature__", executionId: "__registration_probe_missing_execution__" },
            notifyOnComplete: false,
        });
        const detail = await session.workflow.getRunDetail(probe.runId);
        if (probe.status !== "error" || !probe.error?.includes("Feature not found in this project.") || detail.consumed.subagents !== 0) {
            throw new Error(`${workflow} did not reach its expected zero-agent scope guard: ${JSON.stringify(probe)}`);
        }
        if (detail.declaredLimits.maxAiCredits != null) throw new Error(`${workflow} unexpectedly declares an AI-credit cap.`);
        productionRegistration.push({ workflow, sessionId: session.sessionId, nativeRunId: probe.runId, scopeGuardVerified: true, agentsStarted: 0 });
    }
    const paused = await session.workflow.run("software-factory-check", { args: { pause: true }, limits: { maxAiCredits: 1 }, notifyOnComplete: false });
    if (paused.status !== "paused") throw new Error(`Native checkpoint check ended as ${paused.status}, expected paused.`);
    if ((await session.workflow.getRunDetail(paused.runId)).approved?.maxAiCredits !== 1) {
        throw new Error("The zero-agent fixture's explicit credit ceiling was not applied.");
    }
    const stopped = await client.stop();
    if (stopped.length) throw new AggregateError(stopped, "Could not stop the original runtime cleanly.");
    client = createClient(defaultWorkspace(), diagnosticHome);
    await client.start();
    const recovered = await client.resumeSession(session.sessionId, {
        ...sessionConfiguration(defaultWorkspace(), "Software Factory zero-agent cold resume"),
        availableTools: [`custom:${REGISTRATION_TOOL}`],
        onPermissionRequest: () => ({ kind: "reject", feedback: "No tools are permitted during the zero-agent recovery check." }),
    });
    await awaitWorkflowRegistration(recovered, "software-factory-check", runtimeFingerprint());
    const result = await recovered.workflow.resume(paused.runId, { limits: { maxAiCredits: null }, notifyOnComplete: false });
    if (result.status !== "completed") throw new Error(`Native workflow check ended as ${result.status}: ${JSON.stringify(result.failure || result.error)}`);
    if ((await recovered.workflow.getRunDetail(result.runId)).approved?.maxAiCredits != null) {
        throw new Error("The explicit null override did not remove the zero-agent fixture's credit ceiling.");
    }
    // Probe the real UI/chat controller only in a temporary store, never granting authority in the user's registry.
    const temporary = mkdtempSync(path.join(os.tmpdir(), "software-factory-native-"));
    const app = new Application({ database: path.join(temporary, "registry.sqlite"), workspace: defaultWorkspace() });
    try {
        const actor = { channel: "web" as const, reference: "zero-agent-controller-fixture" };
        const feature = app.execute({
            kind: "create_feature", requestId: "native-zero-agent-probe",
            input: { title: "Native controller check", requirements: [{ id: "R1", text: "Run no agents and perform no external mutations." }], blockingUnknowns: [] },
        }, actor);
        app.execute({ kind: "approve_requirements", featureId: feature.id, expectedRevision: feature.current.id },
            actor, { granted: true, revisionId: feature.current.id, actor });
        const runtime = await app.runtime();
        const execution = await runtime.start(feature.id, "check", actor);
        const deadline = Date.now() + 60_000;
        let current = app.lifecycle.store.execution(feature.id, execution.id);
        while (["starting", "running"].includes(current.status) && Date.now() < deadline) {
            await new Promise((resolve) => setTimeout(resolve, 100));
            current = app.lifecycle.store.execution(feature.id, execution.id);
        }
        if (current.status !== "completed" || !current.runtimeId) throw new Error(`Native application controller failed: ${current.error || current.status}`);
        const actual = await runtime.control(feature.id, current.id, "refresh", actor);
        if (!actual) throw new Error("Native controller observation returned no detail.");
        const observed = app.lifecycle.store.execution(feature.id, current.id);
        if (!observed.usage || observed.usage.subagents !== 0 || observed.usage.nanoAiu !== 0 || observed.observationError) {
            throw new Error("Native controller did not preserve the actual zero-agent usage measurements.");
        }
        if (observed.usage.approvedLimits?.maxAiCredits != null) {
            throw new Error("The controller unexpectedly applied an AI-credit cap to an uncapped run.");
        }
        const context = observed.launchContext;
        const actualRegistration = observed.registration;
        const binding = app.lifecycle.store.state(feature.id).sessionBinding;
        if (!binding?.established || !binding.publishedSessionUri) {
            throw new Error("Native controller did not publish the owned session through its loopback AHP host.");
        }
        if (!context || !actualRegistration?.nodeExecutable || context.hostExecutable !== process.execPath ||
            realpathSync(actualRegistration.nodeExecutable) !== context.extensionNode ||
            actualRegistration.nodeVersion !== context.nodeVersion || Number(context.nodeVersion.split(".")[0]) < 24 ||
            context.extensionNode === context.nativeRuntime) {
            throw new Error("Native controller launch diagnostics do not match the actual verified extension interpreter.");
        }
        const peer = new Application({ database: app.registry.databasePath, workspace: defaultWorkspace() });
        try {
            const successor = await (await peer.runtime()).start(feature.id, "check", actor);
            const until = Date.now() + 30_000;
            let next = app.lifecycle.store.execution(feature.id, successor.id);
            while (["starting", "running"].includes(next.status) && Date.now() < until) {
                await new Promise((resolve) => setTimeout(resolve, 100));
                next = app.lifecycle.store.execution(feature.id, successor.id);
            }
            if (next.status !== "completed" || next.sessionId !== current.sessionId || !next.runtimeId || next.runtimeId === current.runtimeId) {
                throw new Error(`The resident owned-session controller did not handle its successor: ${next.error || next.status}`);
            }
            await runtime.control(feature.id, next.id, "refresh", actor);
            if (app.lifecycle.store.execution(feature.id, next.id).usage?.subagents !== 0) throw new Error("The owned-session successor was not a zero-agent check.");
        } finally { await peer.shutdown(); }
        console.log(JSON.stringify({
            sessionId: session.sessionId, coldResumeVerified: true, creditCapRemovalVerified: true, ownedSessionReuseVerified: true, ...result, registration, productionRegistration,
            applicationController: { status: current.status, executionId: current.id, sessionId: current.sessionId, nativeRunId: current.runtimeId, outcome: current.outcome, usage: observed.usage, registration: observed.registration, launchContext: context },
        }, null, 2));
    } finally {
        await app.shutdown();
        rmSync(temporary, { recursive: true });
    }
} finally {
    const errors = await client.stop();
    rmSync(diagnosticHome, { recursive: true });
    if (errors.length) throw new AggregateError(errors, "Native runtime cleanup failed.");
}
