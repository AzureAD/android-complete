// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { defineWorkflow, joinSession } from "@github/copilot-sdk/extension";
import { workflowDefinition } from "./workflow.mjs";

await joinSession({ workflows: [defineWorkflow(workflowDefinition)] });
