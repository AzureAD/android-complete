// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, realpathSync, writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { projectSdkModule } from "../core/runtime-health.ts";
import { sessionConfiguration } from "../runtime/host.ts";
import { fixture } from "./helpers.ts";

async function nestedRegistration(embeddedHost: boolean) {
    const f = fixture();
    const { CopilotClient }: typeof import("@github/copilot-sdk") = await import(projectSdkModule("."));
    execFileSync("git", ["init", "--quiet", f.workspace]);
    const directory = path.join(f.workspace, ".github", "extensions", "NestedRegistrationProbe");
    const home = path.join(f.directory, "nested-data");
    mkdirSync(directory, { recursive: true });
    mkdirSync(home);
    const entry = path.join(directory, "extension.mjs");
    writeFileSync(entry, `
        const { joinSession } = await import(${JSON.stringify(projectSdkModule("./extension"))});
        const { createClient, sessionConfiguration } = await import(${JSON.stringify(new URL("../runtime/host.ts", import.meta.url).href)});
        const { defaultWorkspace } = await import(${JSON.stringify(new URL("../core/application.ts", import.meta.url).href)});
        const { awaitWorkflowRegistration, REGISTRATION_TOOL } = await import(${JSON.stringify(new URL("../runtime/registration.ts", import.meta.url).href)});
        const { runtimeFingerprint } = await import(${JSON.stringify(new URL("../core/evidence.ts", import.meta.url).href)});
        await joinSession({ tools: [{
            name: "probe_nested_registration", description: "Zero-agent registration diagnostic in an isolated test store.",
            parameters: { type: "object", properties: {}, additionalProperties: false }, skipPermission: true,
            handler: async () => {
                const originalExecutable = process.execPath;
                if (${embeddedHost}) Object.defineProperty(process, "execPath", {
                    value: ${JSON.stringify(path.join(f.directory, "embedded-desktop-host-not-node.exe"))}, configurable: true
                });
                const client = createClient(defaultWorkspace(), ${JSON.stringify(home)});
                try {
                    await client.start();
                    const session = await client.createSession({
                        ...sessionConfiguration(defaultWorkspace(), "Nested zero-agent registration diagnostic"),
                        availableTools: ["custom:" + REGISTRATION_TOOL],
                        onPermissionRequest: () => ({ kind: "reject", feedback: "This diagnostic permits no agent tools." })
                    });
                    try {
                        const registration = await awaitWorkflowRegistration(session, "software-factory-design", runtimeFingerprint());
                        const run = await session.workflow.run("software-factory-design", {
                            args: { featureId: "__registration_probe_missing_feature__", executionId: "__registration_probe_missing_execution__" },
                            notifyOnComplete: false
                        });
                        const detail = await session.workflow.getRunDetail(run.runId);
                        return JSON.stringify({ inExtension: !!process.env.SESSION_ID, extensions: (await session.rpc.extensions.list()).extensions,
                            status: run.status, error: run.error, agentsStarted: detail.consumed.subagents, registration });
                    } catch (error) {
                        return JSON.stringify({ inExtension: !!process.env.SESSION_ID, registrationError: error.message,
                            childExtensions: (await session.rpc.extensions.list()).extensions });
                    }
                } finally {
                    const errors = await client.stop();
                    Object.defineProperty(process, "execPath", { value: originalExecutable, configurable: true });
                    if (errors.length) throw new AggregateError(errors, "Nested diagnostic cleanup failed.");
                }
            }
        }] });
    `);
    const client = new CopilotClient({
        workingDirectory: f.workspace, logLevel: "error",
        extensionLaunchProvider: {
            resolve: async (request) => path.normalize(realpathSync(request.modulePath)).toLowerCase() === path.normalize(realpathSync(entry)).toLowerCase()
                ? { launch: { executable: process.execPath, args: [entry], env: {} } } : {},
        },
    });
    let timer: NodeJS.Timeout | undefined;
    try {
        await client.start();
        const session = await client.createSession({
            ...sessionConfiguration(f.workspace, "Outer extension registration fixture"),
            availableTools: ["custom:probe_nested_registration"],
            onPermissionRequest: () => ({ kind: "reject", feedback: "No agent tools are allowed." }),
        });
        const result = await Promise.race([
            session.rpc.tools.execute({ name: "probe_nested_registration", arguments: {} }),
            new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("Nested registration diagnostic timed out.")), 30_000); }),
        ]);
        const output = typeof result === "string" ? result : result.textResultForLlm;
        const probe = JSON.parse(output) as { inExtension: boolean; registrationError?: string; status?: string; error?: string; agentsStarted?: number;
            registration?: { workflowNames: string[]; nodeExecutable?: string; nodeVersion?: string } };
        assert.equal(probe.inExtension, true);
        assert.equal(probe.registrationError, undefined, output);
        assert.equal(probe.status, "error");
        assert.match(probe.error || "", /Feature not found in this project/);
        assert.equal(probe.agentsStarted, 0);
        const registration = probe.registration;
        assert.ok(registration);
        assert.ok(registration.workflowNames.includes("software-factory-design"));
        assert.ok(registration.nodeExecutable && !registration.nodeExecutable.includes("embedded-desktop-host-not-node"));
        assert.ok(Number(registration.nodeVersion?.split(".")[0]) >= 24);
    } finally {
        if (timer) clearTimeout(timer);
        const errors = await client.stop();
        f.cleanup();
        if (errors.length) throw new AggregateError(errors, "Outer diagnostic cleanup failed.");
    }
}

test("the real design workflow is registered when Factory's client runs inside an extension", () => nestedRegistration(false));
test("embedded app hosts do not use their non-Node process executable to launch Factory extensions", () => nestedRegistration(true));
