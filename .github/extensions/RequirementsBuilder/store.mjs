// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { mkdir, readFile, writeFile, rename, unlink, open } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { createDraft, validateId, exportMarkdown, templateHash, fieldById, validateValue } from "./model.mjs";

export class DraftStore {
    constructor(root) { this.root = root; }

    path(id) { validateId(id); return join(this.root, `${id}.json`); }

    async read(id) {
        try {
            const draft = JSON.parse(await readFile(this.path(id), "utf8"));
            if (draft.version !== 1 || draft.id !== id || !Number.isInteger(draft.revision) || !draft.metadata || !draft.answers || !draft.proposals) throw new Error("Invalid saved requirements draft.");
            if (draft.templateHash !== templateHash) throw new Error("Requirements template changed. Preserve this draft and explicitly migrate it before editing.");
            for (const [id, answer] of Object.entries(draft.answers)) validateValue(fieldById.get(id), answer.value);
            for (const [id, proposal] of Object.entries(draft.proposals)) validateValue(fieldById.get(id), proposal.value);
            return draft;
        } catch (error) {
            if (error.code === "ENOENT") return createDraft(id);
            throw error;
        }
    }

    async mutate(id, revision, operation) {
        if (!Number.isInteger(revision) || revision < 0) throw new Error("A draft revision is required.");
        await mkdir(this.root, { recursive: true });
        const path = this.path(id);
        let lock;
        try { lock = await open(`${path}.lock`, "wx", 0o600); }
        catch (error) {
            if (error.code === "EEXIST") throw new Error("Draft is being saved elsewhere. Retry after refreshing; a leftover lock after a crash requires manual inspection.");
            throw error;
        }
        const temp = `${path}.${randomUUID()}.tmp`;
        try {
            const draft = await this.read(id);
            if (draft.revision !== revision) throw new Error("Draft changed. Refresh before saving; your unsaved edits have not been applied.");
            await operation(draft);
            draft.revision++;
            const content = JSON.stringify(draft, null, 2);
            if (Buffer.byteLength(content) > 524288) throw new Error("Draft exceeds 512 KiB. Shorten the input; nothing was silently truncated.");
            await writeFile(temp, content, { mode: 0o600, flag: "wx" });
            await rename(temp, path);
            return draft;
        } finally {
            try {
                await unlink(temp).catch((error) => { if (error.code !== "ENOENT") throw error; });
            } finally {
                await lock.close();
                await unlink(`${path}.lock`);
            }
        }
    }

    async export(id, revision) {
        const draft = await this.read(id);
        if (draft.revision !== revision) throw new Error("Draft changed. Refresh before exporting.");
        if (Object.keys(draft.proposals).length || draft.request?.status === "pending") throw new Error("Review or reject AI proposals, and finish or cancel pending assistance, before export.");
        const markdown = exportMarkdown(draft);
        const path = join(this.root, `${id}.md`);
        // A download is the publication boundary; saved exports stay in private session storage.
        await mkdir(this.root, { recursive: true });
        const temp = `${path}.${randomUUID()}.tmp`;
        try {
            await writeFile(temp, markdown, { flag: "wx", mode: 0o600 });
            await rename(temp, path);
        } finally {
            await unlink(temp).catch((error) => { if (error.code !== "ENOENT") throw error; });
        }
        return { path, markdown, revision, status: "Draft" };
    }
}
