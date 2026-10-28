// Copyright (c) Microsoft Corporation. All rights reserved.

import type { SessionConfig } from "@github/copilot-sdk";
import { realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defaultWorkspace } from "../core/application.ts";
import { projectSdkModule, requireRuntime } from "../core/runtime-health.ts";
import { resolveExtensionInterpreter, type ExtensionInterpreter } from "./interpreter.ts";
import { readerToolNames } from "./reader.ts";

requireRuntime();
const { CopilotClient, RuntimeConnection }: typeof import("@github/copilot-sdk") = await import(projectSdkModule("."));

export function ownedRuntimeEnvironment(source: NodeJS.ProcessEnv): Record<string, string> {
    const environment: Record<string, string> = {};
    for (const [name, value] of Object.entries(source)) {
        if (value !== undefined && !["SESSION_ID", "COPILOT_SDK_PATH", "COPILOT_EXTENSION_PARENT_PID"].includes(name.toUpperCase())) {
            environment[name] = value;
        }
    }
    return environment;
}

export function createClient(workspace = defaultWorkspace(), dataHome = process.env.SOFTWARE_FACTORY_HOME) {
    const entry = realpathSync(fileURLToPath(new URL("../../../.github/extensions/SoftwareFactory/extension.mjs", import.meta.url)));
    const identity = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
    const environment = ownedRuntimeEnvironment(process.env);
    let interpreter: Promise<ExtensionInterpreter> | undefined;
    return new CopilotClient({
        connection: RuntimeConnection.forStdio({
            path: requireRuntime().runtimeExecutable,
            env: environment,
        }),
        workingDirectory: workspace,
        logLevel: "error",
        extensionLaunchProvider: {
            resolve: async (request) => {
                if (identity(realpathSync(request.modulePath)) !== identity(entry)) return {};
                interpreter ??= resolveExtensionInterpreter(environment);
                const node = await interpreter;
                return {
                    launch: {
                        executable: node.executable, args: [entry],
                        env: { SOFTWARE_FACTORY_HOME: dataHome, SOFTWARE_FACTORY_WORKSPACE: workspace, SOFTWARE_FACTORY_OWNED_RUNTIME: "1" },
                    },
                };
            },
        },
    });
}

export function sessionConfiguration(workspace: string, name: string, extraRoots: string[] = []): SessionConfig {
    const normalize = (root: string) => process.platform === "win32" ? root.toLowerCase() : root;
    const roots = [workspace, ...extraRoots].map((root) => normalize(realpathSync(root)));
    return {
        name, workingDirectory: workspace, trustWorkingDirectory: "session",
        enableExperimentalMode: true, enableConfigDiscovery: true, requestExtensions: true,
        extensionSdkPath: path.dirname(fileURLToPath(projectSdkModule("./extension"))),
        enableFileHooks: false,
        availableTools: ["builtin:view", "builtin:read", "builtin:glob", "builtin:grep", "builtin:rg", "builtin:skill", "custom:software_factory_get_feature", "custom:software_factory_workflow_registration", ...readerToolNames.map((name) => `custom:${name}`)],
        excludedTools: ["mcp:*", "builtin:shell", "builtin:powershell", "builtin:write", "builtin:edit", "builtin:apply_patch"],
        additionalDirectories: extraRoots,
        onPermissionRequest: (request) => {
            if (request.kind === "read" && !request.managedApprovalRequired && !request.requestSandboxBypass) {
                try {
                    const target = normalize(realpathSync(path.resolve(workspace, request.path)));
                    if (roots.some((root) => target === root || target.startsWith(root + path.sep))) return { kind: "approve-once" };
                } catch { return { kind: "reject", feedback: "The requested read path could not be verified." }; }
            }
            return { kind: "reject", feedback: "Workers may only read approved source/approval directories; mutations require application adapters." };
        },
    };
}
