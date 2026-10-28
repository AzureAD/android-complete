// Copyright (c) Microsoft Corporation. All rights reserved.

import type { CopilotSession } from "@github/copilot-sdk";
import { FactoryError, object, text } from "../core/contracts.ts";
import type { WorkflowRegistration } from "../core/lifecycle-contracts.ts";

export const REGISTRATION_TOOL = "software_factory_workflow_registration";

type RegistrationSession = Pick<CopilotSession, "sessionId"> & {
    rpc: {
        extensions: Pick<CopilotSession["rpc"]["extensions"], "list" | "reload">;
        tools: Pick<CopilotSession["rpc"]["tools"], "execute">;
    };
};

export async function awaitWorkflowRegistration(session: RegistrationSession, workflow: string, expectedFingerprint: string): Promise<WorkflowRegistration> {
    let last = "The Software Factory extension has not joined.";
    for (let attempt = 0; attempt < 2; attempt++) {
        if (attempt) await session.rpc.extensions.reload();
        const deadline = Date.now() + 20_000;
        while (Date.now() < deadline) {
            const extensions = (await session.rpc.extensions.list()).extensions;
            const factory = extensions.find((extension) => extension.id === "project:SoftwareFactory");
            const others = extensions.filter((extension) => extension.id !== "project:SoftwareFactory")
                .map((extension) => `${extension.id}: ${extension.status}`).join(", ");
            last = `Required project:SoftwareFactory: ${factory?.status || "not discovered"}.${others ? ` Other extensions (not required for Factory registration): ${others}.` : ""}`;
            if (factory?.status === "disabled") throw new FactoryError("workflow_extension_disabled", "Software Factory is disabled in this execution session. No workflow was started; enable it through the host's supported controls.", 409);
            if (!factory || factory.status === "failed") break;
            if (factory.status === "running") {
                let result: Awaited<ReturnType<RegistrationSession["rpc"]["tools"]["execute"]>> | undefined;
                try { result = await session.rpc.tools.execute({ name: REGISTRATION_TOOL, arguments: {} }); }
                catch (error) {
                    if (!(error instanceof Error) || !/not found|not offered|not registered|unknown tool/i.test(error.message)) throw error;
                    last += `; registration handshake pending: ${error.message}`;
                }
                if (result !== undefined) {
                    const content = typeof result === "string" ? result : result.textResultForLlm;
                    if (typeof result !== "string" && result.resultType !== "success") {
                        if (!/not found|not offered|not registered|unknown tool/i.test(content)) {
                            throw new FactoryError("workflow_registration_failed", `The registration handshake failed: ${content}. No workflow was started.`, 503);
                        }
                        last += `; registration handshake pending: ${content}`;
                    } else {
                        let input: unknown;
                        try { input = JSON.parse(content); }
                        catch { throw new FactoryError("workflow_registration_invalid", "The registration handshake returned invalid JSON. No workflow was started.", 503); }
                        const status = object(input, "Workflow registration");
                        if (typeof status.ready !== "boolean") throw new FactoryError("workflow_registration_invalid", "The registration handshake omitted its readiness state. No workflow was started.", 503);
                        if (status.ready === true) {
                            if (status.sessionId !== session.sessionId) throw new FactoryError("workflow_registration_owner", "The workflows registered with a different session. No workflow was started.", 503);
                            if (!Array.isArray(status.workflowNames) || !status.workflowNames.every((name) => typeof name === "string") ||
                                !status.workflowNames.includes(workflow)) {
                                throw new FactoryError("workflow_registration_missing", `The owning extension did not register ${workflow}. No workflow was started.`, 503);
                            }
                            if (status.runtimeFingerprint !== expectedFingerprint) throw new FactoryError("workflow_registration_stale", "The extension loaded a different implementation revision. No workflow was started; reload the integration before a newly confirmed attempt.", 409);
                            return { sessionId: session.sessionId, workflowNames: status.workflowNames.map((name) => text(name, "Workflow name")), runtimeFingerprint: expectedFingerprint,
                                ...(typeof status.nodeExecutable === "string" ? { nodeExecutable: status.nodeExecutable } : {}),
                                ...(typeof status.nodeVersion === "string" ? { nodeVersion: status.nodeVersion } : {}),
                                ...(typeof status.readerAgent === "string" ? { readerAgent: status.readerAgent } : {}) };
                        }
                        last += "; the extension is still joining.";
                    }
                }
            }
            await new Promise((resolve) => setTimeout(resolve, 100));
        }
    }
    throw new FactoryError("workflow_registration_unavailable",
        `Could not establish ${workflow} in execution session ${session.sessionId}. ${last} No agents were started. Inspect extension discovery/launch rather than assuming a package reinstall or app restart will fix it.`, 503);
}
