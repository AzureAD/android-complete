// Copyright (c) Microsoft Corporation. All rights reserved.

import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { Application } from "../core/application.ts";
import type { Actor, Requirements } from "../core/contracts.ts";

export const actor: Actor = { channel: "chat", reference: "tool-call-1", sessionId: "owning-session" };
export const requirements = (): Requirements => ({
    title: "Preserve authentication compatibility",
    requirements: [{ id: "R1", text: "Existing clients keep the current response shape." }],
    blockingUnknowns: [],
});

export function fixture() {
    const directory = mkdtempSync(path.join(os.tmpdir(), "software-factory-test-"));
    const workspace = path.join(directory, "workspace");
    mkdirSync(workspace);
    const options = { database: path.join(directory, "registry.sqlite"), workspace };
    const app = new Application(options);
    return { directory, workspace, options, app, cleanup: () => { app.close(); rmSync(directory, { recursive: true }); } };
}
