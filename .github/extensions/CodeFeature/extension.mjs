// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { statSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createWorkflowCompletionDelivery, createWorkflowPauseTool, createWorkflowStatusTool } from "./completion.mjs";
import { observerCustomAgent, observerWorkflowDefinition } from "./observer.mjs";
import { customAgents, workflowDefinition } from "./workflow.mjs";
import { createWorkflowSupervision } from "./supervision.mjs";

const sdkModule = process.env.COPILOT_SDK_PATH
    ? pathToFileURL(path.join(process.env.COPILOT_SDK_PATH, "extension.js")).href
    : "@github/copilot-sdk/extension";
const { defineWorkflow, joinSession } = await import(sdkModule);
const workflowUrl = new URL("./workflow.mjs", import.meta.url);
const observerUrl = new URL("./observer.mjs", import.meta.url);

let activeWorkflow = null;
let completionDelivery;
const supervision = createWorkflowSupervision(() => session);
async function milestoneHandoff(runId, workflowName, receipt) {
    try {
        if (!completionDelivery) throw new Error("Owning-session chat delivery has not initialized.");
        return await completionDelivery.enqueueMilestone(runId, workflowName, receipt);
    } finally {
        supervision.notifyMilestone(runId, receipt);
    }
}
async function runExclusively(name, fn) {
    if (activeWorkflow) {
        throw new Error(`Workflow "${activeWorkflow}" is already running in this session; use a separate session for concurrent jobs.`);
    }
    activeWorkflow = name;
    try {
        return await fn();
    } finally {
        activeWorkflow = null;
    }
}

const session = await joinSession({
    tools: [createWorkflowStatusTool(() => session), createWorkflowPauseTool(() => session), supervision.tool],
    customAgents: [...customAgents, observerCustomAgent],
    workflows: [
        defineWorkflow({
            ...workflowDefinition,
            run: async (ctx) => runExclusively("code-feature", async () => {
                completionDelivery?.track(ctx.runId, "code-feature");
                const { mtimeMs } = statSync(workflowUrl);
                const latest = await import(`${workflowUrl.href}?v=${mtimeMs}`);
                return latest.runCodeFeature({
                    ...ctx,
                    onMilestone: (receipt) => milestoneHandoff(ctx.runId, "code-feature", receipt),
                });
            }),
        }),
        defineWorkflow({
            ...observerWorkflowDefinition,
            run: async (ctx) => runExclusively("pr-observer", async () => {
                completionDelivery?.track(ctx.runId, "pr-observer");
                const { mtimeMs } = statSync(observerUrl);
                const latest = await import(`${observerUrl.href}?v=${mtimeMs}`);
                return latest.runPrObserver({
                    ...ctx,
                    onMilestone: (receipt) => milestoneHandoff(ctx.runId, "pr-observer", receipt),
                });
            }),
        }),
    ],
});

try {
    completionDelivery = createWorkflowCompletionDelivery(session);
    await completionDelivery.ready;
} catch (error) {
    const message = `Workflow completion delivery could not start: ${error.message}. Inspect durable workflow results manually until the extension is reloaded.`;
    console.error(message);
    await session.log(message, { level: "error" }).catch((logError) =>
        console.error(`Cannot log completion delivery startup failure: ${logError.message}`));
}
