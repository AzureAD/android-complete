// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash } from "node:crypto";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { existsSync } from "node:fs";

const sdkModule = process.env.COPILOT_SDK_PATH
    ? pathToFileURL(path.join(process.env.COPILOT_SDK_PATH, "extension.js")).href
    : "@github/copilot-sdk/extension";
const { defineWorkflow, joinSession } = await import(sdkModule);

if (Number(process.versions.node.split(".")[0]) < 24) {
    await joinSession({
        tools: [{
            name: "software_factory_doctor",
            description: "Explain why Software Factory could not load in this runtime.",
            handler: () => {
                throw new Error("Software Factory requires a Node.js 24+ extension host for native TypeScript and SQLite. No features were opened or modified.");
            },
        }],
    });
} else {
    const { Application, defaultDatabase } = await import("../../../tools/software-factory/core/application.ts");
    const { createChatTools } = await import("../../../tools/software-factory/chat/tools.ts");
    const { createCommands } = await import("../../../tools/software-factory/chat/commands.ts");
    const { createReaderTools, readerAgents, FACTORY_READER } = await import("../../../tools/software-factory/runtime/reader.ts");
    const { checkWorkflow } = await import("../../../tools/software-factory/workflows/check.ts");
    const { designEvaluationWorkflow, evaluationAgent } = await import("../../../tools/software-factory/evals/design.mjs");
    const { intakeWorkflow, designWorkflow, planWorkflow, implementWorkflow, monitorWorkflow, loadedWorkflowFingerprint } = await import("../../../tools/software-factory/workflows/lifecycle.ts");
    let application;
    let session;
    let ready = false;
    const getApplication = () => application ??= new Application({ workspace: process.env.SOFTWARE_FACTORY_WORKSPACE });
    const definitions = [checkWorkflow, intakeWorkflow, designWorkflow, planWorkflow, implementWorkflow, monitorWorkflow, designEvaluationWorkflow];
    const tools = createChatTools(
        getApplication,
        () => {
            if (!session) throw new Error("Software Factory is not attached to an owning chat session.");
            return session;
        },
    );
    session = await joinSession({
        tools: [...tools, ...createReaderTools(getApplication, () => {
            if (!session) throw new Error("Factory readers are not attached to an owning session.");
            return session;
        }), {
            name: "software_factory_workflow_registration",
            description: "Read this exact session's Software Factory workflow registration and implementation fingerprint. Does not prove registration in another session or launch agents.",
            parameters: { type: "object", properties: {}, additionalProperties: false }, skipPermission: true,
            handler: () => JSON.stringify(session && ready ? {
                ready: true, sessionId: session.sessionId,
                sessionIdHash: createHash("sha256").update(session.sessionId).digest("hex"),
                workflowNames: definitions.map((definition) => definition.meta.name),
                runtimeFingerprint: loadedWorkflowFingerprint,
                nodeExecutable: process.execPath, nodeVersion: process.versions.node,
                readerAgent: FACTORY_READER,
            } : { ready: false }),
        }],
        customAgents: [...readerAgents, evaluationAgent],
        commands: createCommands(
            getApplication,
            () => {
                if (!session) throw new Error("Software Factory has no owning command session.");
                return session;
            },
        ),
        workflows: definitions.map((definition) => defineWorkflow(definition)),
    });
    if (process.env.SOFTWARE_FACTORY_OWNED_RUNTIME !== "1") await getApplication().bindChatSession(session);
    ready = true;
    if (process.env.SOFTWARE_FACTORY_OWNED_RUNTIME !== "1" && existsSync(defaultDatabase())) {
        application ??= new Application({ workspace: process.env.SOFTWARE_FACTORY_WORKSPACE });
        if (application.registry.list().some((feature) => application.lifecycle.store.state(feature.id).schedule)) await application.runtime();
    }
    process.once("SIGTERM", () => {
        if (!application) process.exit(0);
        void application.shutdown().then(() => process.exit(0)).catch((error) => {
            console.error("Software Factory shutdown failed:", error);
            process.exit(1);
        });
    });
    process.once("exit", () => application?.close());
}
