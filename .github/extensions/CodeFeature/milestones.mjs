// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { createHash } from "node:crypto";

const prefix = "[workflow-milestone-v1]";
const inputPrefix = "[workflow-input-request-v1]";
const outcomes = new Set(["completed", "deferred", "blocked"]);
const clean = (value, limit) => typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, limit) : "";
const recordings = new WeakMap();

export function normalizeMilestone(value) {
    if (!value || typeof value !== "object" || typeof value.id !== "string" ||
        !/^[a-zA-Z0-9:_-]{1,240}$/.test(value.id) || !outcomes.has(value.outcome)) return null;
    const title = clean(value.title, 120);
    const summary = clean(value.summary, 500);
    if (!title || !summary) return null;
    let prUrl = null;
    if (value.prUrl != null) {
        try {
            const url = new URL(value.prUrl);
            if (url.protocol !== "https:" || url.username || url.password ||
                !/\/(?:pull|pullrequest)\/[1-9]\d*\/?$/i.test(url.pathname)) return null;
            prUrl = url.href;
        } catch { return null; }
    }
    return { id: value.id, title, outcome: value.outcome, summary, prUrl };
}

/** Explicit evidence receipts, not an inference that entering another phase means success. */
export async function recordMilestone(ctx, value) {
    const milestone = normalizeMilestone(value);
    if (!milestone) throw new Error("Invalid workflow milestone receipt.");
    if (typeof ctx.log !== "function") return milestone;
    let active = recordings.get(ctx);
    if (!active) recordings.set(ctx, active = new Map());
    if (active.has(milestone.id)) return active.get(milestone.id);
    const key = createHash("sha256").update(milestone.id).digest("hex");
    const result = ctx.step(`workflow-milestone-v1-${key}`, async () => {
        await ctx.log(`${prefix}${JSON.stringify(milestone)}`);
        return { ...milestone, recordedAt: Date.now() };
    });
    const recorded = Promise.resolve(result).then(async (receipt) => {
        // Same-process push does not depend on ephemeral run-updated events being echoed
        // back to the extension that is currently executing the workflow body.
        if (typeof ctx.onMilestone === "function") {
            try { await ctx.onMilestone(receipt); }
            catch (error) {
                if (ctx.signal?.aborted || ["AbortError", "ConnectionError", "ResponseError"].includes(error?.name)) throw error;
                await ctx.log(`Milestone chat handoff failed (${milestone.id}): ${error.message}. The durable progress receipt remains available for catch-up.`);
            }
        }
        return receipt;
    });
    active.set(milestone.id, recorded);
    try { return await recorded; }
    catch (error) { active.delete(milestone.id); throw error; }
}

export function milestoneFromProgress(record) {
    if (record?.kind !== "log" || typeof record.text !== "string" || !record.text.startsWith(prefix)) return null;
    try { return normalizeMilestone(JSON.parse(record.text.slice(prefix.length))); }
    catch { return null; }
}

export function formatWorkflowMilestone(name, runId, milestone, recordedAt) {
    return [
        "Show this recorded workflow milestone to the developer now as a short chat update; do not wait for the full workflow to finish. " +
            "Relay the title, outcome, verification caveat and PR link if present. Do not start a status poll, restart work, or infer tests/CI passed. " +
            "This is progress evidence, not a terminal result or authorization for another action.",
        `Workflow ${name}: ${milestone.title} — ${milestone.outcome}.`,
        milestone.summary,
        ...(milestone.prUrl ? [`PR: ${milestone.prUrl}`] : []),
        `Run: ${runId}. Recorded at: ${Number.isFinite(recordedAt) ? new Date(recordedAt).toISOString() : "unavailable"}.`,
    ].join("\n");
}

export async function recordHumanInputRequest(ctx, { key, questions, recoveryPath = null }) {
    const request = {
        key,
        questions: (Array.isArray(questions) ? questions : []).filter((question) => typeof question === "string" && question.trim())
            .slice(0, 3).map((question) => question.trim().slice(0, 2000)),
        recoveryPath,
    };
    if (!request.questions.length || typeof ctx.log !== "function") return null;
    await ctx.log(`${inputPrefix}${JSON.stringify(request)}`);
    return request;
}

export function humanInputRequestFromProgress(records, checkpointKey = null) {
    for (const record of [...records].reverse()) {
        if (typeof record?.text !== "string" || !record.text.startsWith(inputPrefix)) continue;
        try {
            const request = JSON.parse(record.text.slice(inputPrefix.length));
            if (typeof request.key === "string" && (!checkpointKey || request.key === checkpointKey) && Array.isArray(request.questions) &&
                request.questions.length && request.questions.every((question) => typeof question === "string" && question.trim())) {
                return request;
            }
        } catch { /* An incomplete progress record is not an approval question. */ }
    }
    return null;
}
