// Copyright (c) Microsoft Corporation. All rights reserved.

import type { WorkflowDefinition } from "@github/copilot-sdk";

export const checkWorkflow: WorkflowDefinition = {
    meta: {
        name: "software-factory-check",
        description: "Verify native Software Factory workflow registration and durable steps without agents or external mutations.",
        phases: [{ title: "Native runtime check" }],
        argsSchema: { type: "object", properties: { pause: { type: "boolean" } } },
    },
    run: async (ctx) => {
        ctx.phase("Native runtime check");
        if (ctx.args && typeof ctx.args === "object" && !Array.isArray(ctx.args) && ctx.args.pause === true) {
            await ctx.pause("software-factory-native-checkpoint-v1");
        }
        return ctx.step("software-factory-check-v1", () => ({
            product: "Software Factory", nativeWorkflow: true, runId: ctx.runId,
            sessionId: ctx.session.sessionId, agentsStarted: 0,
            hostConfirmationSupported: ctx.session.capabilities.ui?.elicitation === true,
        }));
    },
};
