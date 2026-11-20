// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash, randomUUID } from "node:crypto";
import { createReadStream, mkdirSync, realpathSync } from "node:fs";
import path from "node:path";
import { backup, DatabaseSync } from "node:sqlite";
import { FactoryError, object } from "./contracts.ts";
import type { Registry } from "./registry.ts";

async function digest(file: string): Promise<string> {
    const hash = createHash("sha256");
    for await (const chunk of createReadStream(file)) hash.update(chunk);
    return hash.digest("hex");
}

export async function inspectBackup(file: string) {
    const location = realpathSync(file);
    const source = new DatabaseSync(location, { readOnly: true });
    try {
        const check = source.prepare("PRAGMA quick_check").get();
        if (!check || Object.values(check)[0] !== "ok") throw new FactoryError("invalid_backup", "SQLite integrity checking failed.", 409);
        const version = source.prepare("PRAGMA user_version").get()?.user_version;
        if (version !== 1 && version !== 2) throw new FactoryError("invalid_backup", "Unsupported Software Factory backup schema.", 409);
        const tables = source.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map((row) => row.name);
        const required = ["projects", "features", "revisions", "approvals", "audit", ...(version === 2 ? ["factory_state", "factory_configuration"] : [])];
        if (!required.every((table) => tables.includes(table)) || source.prepare("PRAGMA foreign_key_check").get()) {
            throw new FactoryError("invalid_backup", "The snapshot is missing required tables or contains broken references.", 409);
        }
        const projects = source.prepare("SELECT id, workspace FROM projects").all().map((row) => ({
            id: String(row.id), workspace: String(row.workspace),
        }));
        return { path: location, hash: await digest(location), schemaVersion: Number(version), projects };
    } finally { source.close(); }
}

export async function createBackup(registry: Registry) {
    const directory = path.join(path.dirname(registry.databasePath), "backups");
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const file = path.join(directory, `backup-${randomUUID()}.sqlite`);
    await backup(registry.connection, file);
    return inspectBackup(file);
}

export async function restoreClone(registry: Registry, file: string, expectedHash: string) {
    const preview = await inspectBackup(file);
    if (preview.path === realpathSync(registry.databasePath)) throw new FactoryError("live_registry_restore", "Create a consistent backup first; do not restore the live database.", 409);
    if (preview.hash !== expectedHash) throw new FactoryError("stale_backup", "The backup changed; inspect it again.", 409);
    const directory = path.join(path.dirname(registry.databasePath), "restored", randomUUID());
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const destination = path.join(directory, "registry.sqlite");
    const source = new DatabaseSync(preview.path, { readOnly: true });
    try { await backup(source, destination); }
    finally { source.close(); }
    const clone = new DatabaseSync(destination);
    try {
        if (preview.schemaVersion === 2) {
            for (const row of clone.prepare("SELECT feature_id, content_json FROM factory_state").all()) {
                if (typeof row.content_json !== "string") throw new FactoryError("invalid_backup", "Lifecycle data is invalid.", 409);
                const state = object(JSON.parse(row.content_json), "Restored lifecycle state");
                state.schedule = null;
                state.controls = [];
                state.sessionBinding = null;
                if (Array.isArray(state.executions)) {
                    for (const raw of state.executions) {
                        const execution = object(raw, "Restored execution");
                        if (["starting", "running", "paused"].includes(String(execution.status))) {
                            execution.status = "error";
                            execution.error = "Restored snapshot: inspect the actual owning runtime and reconcile external effects before recovery. Journals were not copied.";
                        }
                    }
                }
                clone.prepare("UPDATE factory_state SET content_json = ?, version = version + 1 WHERE feature_id = ?")
                    .run(JSON.stringify(state), String(row.feature_id));
            }
            if (clone.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='factory_resident_owner'").get()) {
                clone.exec("DELETE FROM factory_resident_owner");
            }
        }
    } finally { clone.close(); }
    return {
        dataHome: directory, registryPath: destination, sourceHash: preview.hash,
        liveRegistryReplaced: false, schedulesDisabled: true, journalsCopied: false,
        nextAction: "Stop original hosts. Start a new host with SOFTWARE_FACTORY_HOME set to dataHome. Workspace identities are preserved; no automatic project or runtime ownership transfer occurs.",
    };
}
