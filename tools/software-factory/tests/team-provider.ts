// Copyright (c) Microsoft Corporation. All rights reserved.

import assert from "node:assert/strict";
import { FactoryError } from "../core/contracts.ts";
import { digest, parseSnapshot, validateTransition, type AdoIdentity, type SharedProvider, type SharedRecord, type SharedSnapshot, type SharedTarget } from "../core/shared-contracts.ts";
import type { Configuration } from "../core/lifecycle-contracts.ts";

export class TeamProvider implements SharedProvider {
    records = new Map<number, SharedRecord>();
    online = true;
    canEdit = true;
    user: AdoIdentity = { id: "alice", displayName: "Alice" };
    async identity() { this.available(); return this.user; }
    available() { if (!this.online) throw new FactoryError("shared_unavailable", "ADO is offline.", 503); }
    async read(target: SharedTarget) { this.available(); return structuredClone(this.records.get(target.workItemId) || null); }
    async write(target: SharedTarget, expected: SharedRecord | null, snapshot: SharedSnapshot, user: AdoIdentity) {
        this.available();
        if (!this.canEdit) throw new FactoryError("forbidden", "ADO edit permission denied.", 403);
        const previous = this.records.get(target.workItemId);
        if ((previous?.revision || null) !== (expected?.revision || null)) throw new FactoryError("shared_conflict", "Concurrent ADO edit.", 409);
        assert.equal(user.id, this.user.id);
        validateTransition(previous?.snapshot || null, snapshot, user);
        const record = { target, revision: (previous?.revision || 0) + 1, digest: digest(snapshot), snapshot: parseSnapshot(structuredClone(snapshot)) };
        this.records.set(target.workItemId, record);
        return structuredClone(record);
    }
    async list() { return [...this.records.values()].map((record) => record.target); }
}

export const teamConfig = (localPath = ".", account = "alice"): Configuration => ({
    repositories: { common: { provider: "github", hostname: "github.com", slug: "example/common", baseBranch: "dev", account, localPath } },
    boards: { organization: "example", project: "Engineering", workItemType: "Product Backlog Item", areaPath: "Engineering",
        iterationPath: "Engineering", assignee: "", parentId: null },
});
