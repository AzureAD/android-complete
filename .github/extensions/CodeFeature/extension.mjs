// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { statSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { observerCustomAgent, observerWorkflowDefinition } from "./observer.mjs";
import { customAgents, workflowDefinition } from "./workflow.mjs";

const sdkModule = process.env.COPILOT_SDK_PATH
    ? pathToFileURL(path.join(process.env.COPILOT_SDK_PATH, "extension.js")).href
    : "@github/copilot-sdk/extension";
const { defineWorkflow, joinSession } = await import(sdkModule);
const workflowUrl = new URL("./workflow.mjs", import.meta.url);
const observerUrl = new URL("./observer.mjs", import.meta.url);

let activeWorkflow = null;
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

await joinSession({
    customAgents: [...customAgents, observerCustomAgent],
    workflows: [
        defineWorkflow({
            ...workflowDefinition,
            run: async (ctx) => runExclusively("code-feature", async () => {
                const { mtimeMs } = statSync(workflowUrl);
                const latest = await import(`${workflowUrl.href}?v=${mtimeMs}`);
                return latest.runCodeFeature(ctx);
            }),
        }),
        defineWorkflow({
            ...observerWorkflowDefinition,
            run: async (ctx) => runExclusively("pr-observer", async () => {
                const { mtimeMs } = statSync(observerUrl);
                const latest = await import(`${observerUrl.href}?v=${mtimeMs}`);
                return latest.runPrObserver(ctx);
            }),
        }),
    ],
});
