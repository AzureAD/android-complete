// Copyright (c) Microsoft Corporation. All rights reserved.

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import path from "node:path";
import { Application } from "./core/application.ts";
import { createFactoryMcp } from "./server/mcp.ts";

const [flag, workspace, ...extra] = process.argv.slice(2);
if (extra.length || (flag !== undefined && (flag !== "--workspace" || !workspace || !path.isAbsolute(workspace)))) {
    throw new Error("Usage: node mcp.ts [--workspace <absolute-workspace-path>]");
}
const application = new Application({ workspace });
const server = createFactoryMcp(application);
let stopping: Promise<void> | undefined;
const stop = () => stopping ??= Promise.resolve().then(async () => {
    await server.close();
    await application.shutdown();
});
const stopSafely = () => { void stop().catch((error: unknown) => { console.error(error); process.exitCode = 1; }); };
server.onclose = stopSafely;
process.once("SIGINT", stopSafely);
process.once("SIGTERM", stopSafely);
try {
    await server.connect(new StdioServerTransport());
} catch (error) {
    await stop();
    throw error;
}
