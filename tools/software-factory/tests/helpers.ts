// Copyright (c) Microsoft Corporation. All rights reserved.

import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Actor, Requirements } from "../core/contracts.ts";
import type { Execution } from "../core/lifecycle-contracts.ts";
import { LifecycleStore, now } from "../core/lifecycle-store.ts";
import { Registry } from "../core/registry.ts";

export const actor: Actor = { channel: "chat", reference: "tool-call-1", sessionId: "owning-session" };
export const requirements = (): Requirements => ({
    title: "Preserve authentication compatibility",
    requirements: [{ id: "R1", text: "Existing clients keep the current response shape." }],
    blockingUnknowns: [],
});

export function makeExecution(id: string, requirementsRevision: string, overrides: Partial<Execution> = {}): Execution {
    const timestamp = now();
    return {
        id,
        kind: "design",
        requirementsRevision,
        configurationId: null,
        sessionId: actor.sessionId || "owning-session",
        runtimeId: "rt-1",
        parentSessionId: null,
        status: "starting",
        phase: "starting",
        generation: { design: 0, plan: 0 },
        sourcePins: {},
        runtimeFingerprint: "fp-1",
        requestedBy: actor,
        limits: {},
        checkpoints: {},
        outcome: null,
        error: null,
        createdAt: timestamp,
        updatedAt: timestamp,
        ...overrides,
    };
}

export function fixture() {
    const directory = mkdtempSync(path.join(os.tmpdir(), "software-factory-test-"));
    const workspace = path.join(directory, "workspace");
    mkdirSync(workspace);
    const database = path.join(directory, "registry.sqlite");
    const registry = new Registry(database, workspace);
    const store = new LifecycleStore(registry);
    return {
        directory,
        workspace,
        database,
        registry,
        store,
        cleanup: () => {
            registry.close();
            rmSync(directory, { recursive: true });
        },
    };
}
