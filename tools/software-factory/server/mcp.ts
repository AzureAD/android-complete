// Copyright (c) Microsoft Corporation. All rights reserved.

import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { CallToolRequestSchema, ElicitRequestFormParamsSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { AjvJsonSchemaValidator } from "@modelcontextprotocol/sdk/validation/ajv-provider.js";
import type { Application } from "../core/application.ts";
import { FactoryError } from "../core/contracts.ts";
import { createChatTools, type ChatHost } from "../chat/tools.ts";
import { runtimeReport } from "../core/doctor.ts";

export function createFactoryMcp(application: Application) {
    const server = new Server({ name: "software-factory", version: "0.1.0" }, {
        capabilities: { tools: {} },
        instructions: "Use Software Factory's shared commands and exact-scope human gates. Present full reviews before requesting decisions. " +
            "This MCP connection is not a native Copilot workflow owner. New workflows run in Factory's SDK runtime; " +
            "existing chat-owned workflows stay with that owner. Never claim that an MCP connection ID is a Copilot app session.",
    });
    const requests = new AsyncLocalStorage<{ signal: AbortSignal; requestId: string | number }>();
    const sessionId = `mcp:${randomUUID()}`;
    const host: ChatHost = {
        sessionId,
        get capabilities() {
            const capability = server.getClientCapabilities()?.elicitation;
            return { ui: { elicitation: !!capability && ("form" in capability || Object.keys(capability).length === 0) } };
        },
        ui: {
            confirm: async () => { throw new FactoryError("confirmation_unavailable", "MCP approval requires a supported host form.", 409); },
            elicitation: async (input) => {
                const request = requests.getStore();
                if (!request || !host.capabilities.ui?.elicitation) {
                    throw new FactoryError("confirmation_unavailable", "This MCP host does not support form elicitation. Nothing was approved.", 409);
                }
                request.signal.throwIfAborted();
                const result = await server.elicitInput(ElicitRequestFormParamsSchema.parse({ ...input, mode: "form" }), {
                    signal: request.signal, relatedRequestId: request.requestId, timeout: 2_147_483_647,
                });
                request.signal.throwIfAborted();
                return result;
            },
        },
    };
    const tools = createChatTools(() => application, () => host);
    const validator = new AjvJsonSchemaValidator();
    const validators = new Map(tools.map((tool) => [tool.name, validator.getValidator(tool.parameters)]));
    server.setRequestHandler(ListToolsRequestSchema, async () => ({
        tools: tools.map((tool) => ({
            name: tool.name, description: tool.description,
            inputSchema: { ...tool.parameters, type: "object" as const },
        })),
    }));
    server.setRequestHandler(CallToolRequestSchema, async (request, extra) => {
        const tool = tools.find((candidate) => candidate.name === request.params.name);
        if (!tool) throw new FactoryError("unknown_tool", "Unknown Software Factory MCP tool.", 404);
        const input = request.params.arguments || {};
        const checked = validators.get(tool.name)!(input);
        if (!checked.valid) throw new FactoryError("invalid_input", checked.errorMessage || "Invalid tool arguments.");
        return requests.run({ signal: extra.signal, requestId: extra.requestId }, async () => {
            try {
                extra.signal.throwIfAborted();
                const result = tool.name === "software_factory_doctor" ? {
                    ...runtimeReport(), transport: "mcp", confirmationSupported: host.capabilities.ui?.elicitation === true,
                    nativeSessionJoined: false,
                    continuation: "Existing native owners are preserved; new runs use the Factory SDK runtime, not the MCP chat session.",
                } : await tool.handler(input, { sessionId, toolCallId: `${sessionId}:${extra.requestId}`, signal: extra.signal });
                return { content: [{ type: "text" as const, text: JSON.stringify(result) }] };
            } catch (error) {
                if (!(error instanceof FactoryError)) throw error;
                return { isError: true, content: [{ type: "text" as const,
                    text: JSON.stringify({ outcome: "blocked", error: { code: error.code, message: error.message } }) }] };
            }
        });
    });
    return server;
}
