// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { fixture } from "./helpers.ts";

test("chat-host bare SDK redirection cannot change Doctor, client runtime or extension SDK selection", () => {
    const f = fixture();
    try {
        const injected = path.join(f.directory, "desktop-sdk.mjs");
        const hook = path.join(f.directory, "redirect-sdk.mjs");
        writeFileSync(injected, 'export class CopilotClient { constructor() { throw new Error("The desktop injected SDK must not own Software Factory execution."); } }');
        writeFileSync(hook, `
            import { registerHooks } from "node:module";
            registerHooks({ resolve(specifier, context, nextResolve) {
                if (specifier === "@github/copilot-sdk") return { url: ${JSON.stringify(pathToFileURL(injected).href)}, shortCircuit: true };
                return nextResolve(specifier, context);
            } });
        `);
        const output = execFileSync(process.execPath, ["--import", pathToFileURL(hook).href, "--input-type=module", "-e", `
            import assert from "node:assert/strict";
            import path from "node:path";
            import { fileURLToPath } from "node:url";
            const health = await import(process.argv[1]);
            assert.equal(import.meta.resolve("@github/copilot-sdk"), process.argv[4], "the simulated desktop redirect must actually be active");
            const status = health.runtimeHealth();
            assert.equal(status.ready, true, status.message);
            assert.equal(status.sdkRoot, health.projectSdkRoot());
            const host = await import(process.argv[2]);
            const { defaultWorkspace } = await import(process.argv[5]);
            assert.equal(host.sessionConfiguration(defaultWorkspace(), "SDK source check").extensionSdkPath,
                path.dirname(fileURLToPath(health.projectSdkModule("./extension"))));
            await import(process.argv[3]);
        `,
        new URL("../core/runtime-health.ts", import.meta.url).href,
        new URL("../runtime/host.ts", import.meta.url).href,
        new URL("../runtime-check.ts", import.meta.url).href,
        pathToFileURL(injected).href,
        new URL("../core/application.ts", import.meta.url).href], {
            cwd: fileURLToPath(new URL("../../../", import.meta.url)),
            env: {
                ...process.env,
                COPILOT_CLI_PATH: path.join(f.directory, "not-the-factory-runtime.exe"),
                COPILOT_SDK_DEFAULT_CONNECTION: "inprocess",
                SESSION_ID: "parent-extension-fixture",
                COPILOT_SDK_PATH: f.directory,
                COPILOT_EXTENSION_PARENT_PID: String(process.pid),
            },
            encoding: "utf8", timeout: 120_000, maxBuffer: 1024 * 1024,
        });
        const result = JSON.parse(output) as { coldResumeVerified: boolean; creditCapRemovalVerified: boolean; ownedSessionReuseVerified: boolean; productionRegistration: { workflow: string; agentsStarted: number; scopeGuardVerified: boolean }[];
            applicationController: { status: string; usage: { subagents: number } } };
        assert.equal(result.coldResumeVerified, true);
        assert.equal(result.creditCapRemovalVerified, true);
        assert.equal(result.ownedSessionReuseVerified, true);
        assert.equal(result.applicationController.status, "completed");
        assert.equal(result.applicationController.usage.subagents, 0);
        assert.deepEqual(result.productionRegistration.map((entry) => entry.workflow), [
            "software-factory-intake", "software-factory-design", "software-factory-plan", "software-factory-implement", "software-factory-monitor",
        ]);
        assert.ok(result.productionRegistration.every((entry) => entry.agentsStarted === 0 && entry.scopeGuardVerified));
    } finally { f.cleanup(); }
});
