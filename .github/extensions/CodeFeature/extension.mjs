// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import path from "node:path";
import { pathToFileURL } from "node:url";
import { customAgents, workflowDefinition } from "./workflow.mjs";

const sdkModule = process.env.COPILOT_SDK_PATH
    ? pathToFileURL(path.join(process.env.COPILOT_SDK_PATH, "extension.js")).href
    : "@github/copilot-sdk/extension";
const { defineWorkflow, joinSession } = await import(sdkModule);

await joinSession({
    customAgents,
    workflows: [defineWorkflow(workflowDefinition)],
});
