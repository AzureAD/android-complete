// Copyright (c) Microsoft Corporation. All rights reserved.

import type { Revision } from "./contracts.ts";
import type { Json } from "./lifecycle-contracts.ts";

export interface WordDiffToken {
    kind: "equal" | "add" | "remove";
    text: string;
}

export interface LineDiffRow {
    kind: "equal" | "add" | "remove";
    beforeLine: number | null;
    afterLine: number | null;
    text: string;
    tokens?: WordDiffToken[];
}

export type DiffHunk =
    | { type: "hunk"; header: string; rows: LineDiffRow[] }
    | { type: "collapsed"; hiddenCount: number; rows: LineDiffRow[] };

export interface SplitDiffRow {
    left: { line: number | null; kind: "equal" | "remove" | "empty"; text: string; tokens?: WordDiffToken[] };
    right: { line: number | null; kind: "equal" | "add" | "empty"; text: string; tokens?: WordDiffToken[] };
}

export interface TextDiffResult {
    changed: boolean;
    addedLines: number;
    removedLines: number;
    rows: LineDiffRow[];
    hunks: DiffHunk[];
    splitRows: SplitDiffRow[];
}

export interface ListSetDiff {
    added: string[];
    removed: string[];
    unchanged: string[];
}

export interface KeyedItemDiff {
    id: string;
    status: "added" | "removed" | "modified" | "unchanged";
    beforeText: string;
    afterText: string;
    tokens?: WordDiffToken[];
}

export interface RequirementsRevisionDiff {
    fromRevision: number;
    toRevision: number;
    changed: boolean;
    titleChanged: boolean;
    beforeTitle: string;
    afterTitle: string;
    titleTokens: WordDiffToken[];
    descriptionDiff: TextDiffResult;
    requirements: KeyedItemDiff[];
    designTasks: KeyedItemDiff[];
    blockingUnknowns: ListSetDiff;
    summaryCounts: {
        criteriaAdded: number;
        criteriaRemoved: number;
        criteriaModified: number;
        tasksChanged: number;
        unknownsResolved: number;
        unknownsAdded: number;
    };
}

export interface DesignSectionDiff {
    key: string;
    title: string;
    status: "added" | "removed" | "modified" | "unchanged";
    diff: TextDiffResult;
}

export interface DesignChangeUnitDiff {
    id: string;
    status: "added" | "removed" | "modified" | "unchanged";
    repository: string;
    path: string;
    beforeSummary: string;
    afterSummary: string;
    summaryTokens?: WordDiffToken[];
    changedFields: string[];
}

export interface DesignTraceabilityDiff {
    requirementId: string;
    status: "added" | "removed" | "modified" | "unchanged";
    sectionsAdded: string[];
    sectionsRemoved: string[];
    evidenceAdded: string[];
    evidenceRemoved: string[];
    verificationChanged: boolean;
    beforeVerification: string;
    afterVerification: string;
}

export interface DesignReviewDelta {
    findingsResolved: Array<{ id: string; severity: string; location: string; summary: string }>;
    findingsAdded: Array<{ id: string; severity: string; location: string; summary: string }>;
    findingsRemaining: Array<{ id: string; severity: string; location: string; summary: string }>;
    rubricFlips: Array<{ key: string; from: boolean; to: boolean }>;
    changeChecks: Array<{ findingId: string; status: string; rationale: string }>;
}

export interface DesignSnapshotDiff {
    changed: boolean;
    sections: DesignSectionDiff[];
    changes: DesignChangeUnitDiff[];
    traceability: DesignTraceabilityDiff[];
    assumptions: ListSetDiff;
    openDecisions: ListSetDiff;
    blockingUnknowns: ListSetDiff;
    reviewDelta: DesignReviewDelta | null;
    summaryCounts: {
        sectionsModified: number;
        sectionsAdded: number;
        sectionsRemoved: number;
        linesAdded: number;
        linesRemoved: number;
        changesModified: number;
        changesAdded: number;
        changesRemoved: number;
        traceabilityChanged: number;
        findingsResolved: number;
    };
}

export interface PlanPbiDiff {
    id: string;
    title: string;
    beforeTitle: string;
    repository: string;
    beforeRepository: string;
    status: "added" | "removed" | "modified" | "unchanged";
    titleChanged: boolean;
    repositoryChanged: boolean;
    descriptionDiff: TextDiffResult;
    files: ListSetDiff;
    tests: ListSetDiff;
    acceptanceCriteria: ListSetDiff;
    dependsOn: ListSetDiff;
    requirementIds: ListSetDiff;
    changedFields: string[];
}

export interface PlanSnapshotDiff {
    changed: boolean;
    summaryDiff: TextDiffResult;
    items: PlanPbiDiff[];
    blockingUnknowns: ListSetDiff;
    summaryCounts: {
        addedPbis: number;
        removedPbis: number;
        modifiedPbis: number;
        unchangedPbis: number;
        filesAdded: number;
        filesRemoved: number;
        testsAdded: number;
        criteriaAdded: number;
    };
}

function isRecord(value: unknown): value is Record<string, Json> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asArray(value: unknown): Json[] {
    return Array.isArray(value) ? value : [];
}

function normalizeLines(text: string): string[] {
    if (!text) return [];
    return text.replace(/\r\n/g, "\n").split("\n");
}

function formatSectionTitle(key: string): string {
    return key
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
        .replace(/[_-]+/g, " ")
        .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

export function diffWords(beforeText: string, afterText: string): WordDiffToken[] {
    if (beforeText === afterText) {
        return beforeText ? [{ kind: "equal", text: beforeText }] : [];
    }
    const left = beforeText.match(/\s+|\w+|[^\s\w]+/g) ?? [];
    const right = afterText.match(/\s+|\w+|[^\s\w]+/g) ?? [];
    if (left.length === 0) return right.length ? [{ kind: "add", text: afterText }] : [];
    if (right.length === 0) return left.length ? [{ kind: "remove", text: beforeText }] : [];

    let start = 0;
    while (start < left.length && start < right.length && left[start] === right[start]) {
        start++;
    }
    let leftEnd = left.length - 1;
    let rightEnd = right.length - 1;
    while (leftEnd >= start && rightEnd >= start && left[leftEnd] === right[rightEnd]) {
        leftEnd--;
        rightEnd--;
    }

    const prefix = start > 0 ? [{ kind: "equal" as const, text: left.slice(0, start).join("") }] : [];
    const suffix = leftEnd < left.length - 1 ? [{ kind: "equal" as const, text: left.slice(leftEnd + 1).join("") }] : [];

    const midLeft = left.slice(start, leftEnd + 1);
    const midRight = right.slice(start, rightEnd + 1);

    if (midLeft.length === 0 && midRight.length === 0) {
        return [...prefix, ...suffix];
    }
    if (midLeft.length * midRight.length > 160_000) {
        const coarse: WordDiffToken[] = [...prefix];
        if (midLeft.length) coarse.push({ kind: "remove", text: midLeft.join("") });
        if (midRight.length) coarse.push({ kind: "add", text: midRight.join("") });
        coarse.push(...suffix);
        return mergeAdjacentTokens(coarse);
    }

    const m = midLeft.length;
    const n = midRight.length;
    const cols = n + 1;
    const dp = new Uint16Array((m + 1) * cols);
    for (let i = m - 1; i >= 0; i--) {
        for (let j = n - 1; j >= 0; j--) {
            if (midLeft[i] === midRight[j]) {
                dp[i * cols + j] = (dp[(i + 1) * cols + (j + 1)] ?? 0) + 1;
            } else {
                const down = dp[(i + 1) * cols + j] ?? 0;
                const across = dp[i * cols + (j + 1)] ?? 0;
                dp[i * cols + j] = down >= across ? down : across;
            }
        }
    }

    const midTokens: WordDiffToken[] = [];
    let i = 0;
    let j = 0;
    while (i < m && j < n) {
        if (midLeft[i] === midRight[j]) {
            midTokens.push({ kind: "equal", text: midLeft[i]! });
            i++;
            j++;
        } else if ((dp[(i + 1) * cols + j] ?? 0) >= (dp[i * cols + (j + 1)] ?? 0)) {
            midTokens.push({ kind: "remove", text: midLeft[i]! });
            i++;
        } else {
            midTokens.push({ kind: "add", text: midRight[j]! });
            j++;
        }
    }
    while (i < m) {
        midTokens.push({ kind: "remove", text: midLeft[i]! });
        i++;
    }
    while (j < n) {
        midTokens.push({ kind: "add", text: midRight[j]! });
        j++;
    }

    return mergeAdjacentTokens([...prefix, ...midTokens, ...suffix]);
}

function mergeAdjacentTokens(tokens: WordDiffToken[]): WordDiffToken[] {
    const merged: WordDiffToken[] = [];
    for (const tok of tokens) {
        if (!tok.text) continue;
        const last = merged.at(-1);
        if (last && last.kind === tok.kind) {
            last.text += tok.text;
        } else {
            merged.push({ ...tok });
        }
    }
    return merged;
}

function rawLcsLineDiff(left: string[], right: string[], leftOffset: number, rightOffset: number): LineDiffRow[] {
    let start = 0;
    while (start < left.length && start < right.length && left[start] === right[start]) {
        start++;
    }
    let leftEnd = left.length - 1;
    let rightEnd = right.length - 1;
    while (leftEnd >= start && rightEnd >= start && left[leftEnd] === right[rightEnd]) {
        leftEnd--;
        rightEnd--;
    }

    const rows: LineDiffRow[] = [];
    for (let idx = 0; idx < start; idx++) {
        rows.push({
            kind: "equal",
            beforeLine: leftOffset + idx + 1,
            afterLine: rightOffset + idx + 1,
            text: left[idx]!,
        });
    }

    const midLeft = left.slice(start, leftEnd + 1);
    const midRight = right.slice(start, rightEnd + 1);
    const m = midLeft.length;
    const n = midRight.length;

    if (m > 0 || n > 0) {
        if (m * n > 900_000) {
            for (let i = 0; i < m; i++) {
                rows.push({ kind: "remove", beforeLine: leftOffset + start + i + 1, afterLine: null, text: midLeft[i]! });
            }
            for (let j = 0; j < n; j++) {
                rows.push({ kind: "add", beforeLine: null, afterLine: rightOffset + start + j + 1, text: midRight[j]! });
            }
        } else {
            const cols = n + 1;
            const dp = new Uint16Array((m + 1) * cols);
            for (let i = m - 1; i >= 0; i--) {
                for (let j = n - 1; j >= 0; j--) {
                    if (midLeft[i] === midRight[j]) {
                        dp[i * cols + j] = (dp[(i + 1) * cols + (j + 1)] ?? 0) + 1;
                    } else {
                        const down = dp[(i + 1) * cols + j] ?? 0;
                        const across = dp[i * cols + (j + 1)] ?? 0;
                        dp[i * cols + j] = down >= across ? down : across;
                    }
                }
            }
            let i = 0;
            let j = 0;
            while (i < m && j < n) {
                if (midLeft[i] === midRight[j]) {
                    rows.push({
                        kind: "equal",
                        beforeLine: leftOffset + start + i + 1,
                        afterLine: rightOffset + start + j + 1,
                        text: midLeft[i]!,
                    });
                    i++;
                    j++;
                } else if ((dp[(i + 1) * cols + j] ?? 0) >= (dp[i * cols + (j + 1)] ?? 0)) {
                    rows.push({
                        kind: "remove",
                        beforeLine: leftOffset + start + i + 1,
                        afterLine: null,
                        text: midLeft[i]!,
                    });
                    i++;
                } else {
                    rows.push({
                        kind: "add",
                        beforeLine: null,
                        afterLine: rightOffset + start + j + 1,
                        text: midRight[j]!,
                    });
                    j++;
                }
            }
            while (i < m) {
                rows.push({
                    kind: "remove",
                    beforeLine: leftOffset + start + i + 1,
                    afterLine: null,
                    text: midLeft[i]!,
                });
                i++;
            }
            while (j < n) {
                rows.push({
                    kind: "add",
                    beforeLine: null,
                    afterLine: rightOffset + start + j + 1,
                    text: midRight[j]!,
                });
                j++;
            }
        }
    }

    for (let idx = leftEnd + 1; idx < left.length; idx++) {
        const rightIdx = rightEnd + 1 + (idx - (leftEnd + 1));
        rows.push({
            kind: "equal",
            beforeLine: leftOffset + idx + 1,
            afterLine: rightOffset + rightIdx + 1,
            text: left[idx]!,
        });
    }

    return enrichAdjacentWordDiffs(rows);
}

function enrichAdjacentWordDiffs(rows: LineDiffRow[]): LineDiffRow[] {
    let idx = 0;
    while (idx < rows.length) {
        if (rows[idx]?.kind !== "remove") {
            idx++;
            continue;
        }
        const remStart = idx;
        while (idx < rows.length && rows[idx]?.kind === "remove") idx++;
        const addStart = idx;
        while (idx < rows.length && rows[idx]?.kind === "add") idx++;
        const remCount = addStart - remStart;
        const addCount = idx - addStart;
        const pairCount = Math.min(remCount, addCount);
        if (pairCount > 0 && pairCount <= 12) {
            for (let p = 0; p < pairCount; p++) {
                const rRow = rows[remStart + p]!;
                const aRow = rows[addStart + p]!;
                const words = diffWords(rRow.text, aRow.text);
                rRow.tokens = words.filter((w) => w.kind !== "add");
                aRow.tokens = words.filter((w) => w.kind !== "remove");
            }
        }
    }
    return rows;
}

export function buildDiffHunks(rows: readonly LineDiffRow[], contextLines = 3): DiffHunk[] {
    if (rows.length === 0) return [];
    const changedIndices: number[] = [];
    rows.forEach((row, index) => {
        if (row.kind !== "equal") changedIndices.push(index);
    });
    if (changedIndices.length === 0) {
        return [{ type: "collapsed", hiddenCount: rows.length, rows: [...rows] }];
    }

    const keep = new Uint8Array(rows.length);
    for (const cIdx of changedIndices) {
        const from = Math.max(0, cIdx - contextLines);
        const to = Math.min(rows.length - 1, cIdx + contextLines);
        for (let k = from; k <= to; k++) keep[k] = 1;
    }

    const hunks: DiffHunk[] = [];
    let cursor = 0;
    while (cursor < rows.length) {
        const isKept = keep[cursor] === 1;
        const start = cursor;
        while (cursor < rows.length && (keep[cursor] === 1) === isKept) {
            cursor++;
        }
        const slice = rows.slice(start, cursor);
        if (!isKept) {
            if (slice.length <= 2 && hunks.length > 0 && cursor < rows.length) {
                const prev = hunks.at(-1);
                if (prev && prev.type === "hunk") {
                    prev.rows.push(...slice);
                    continue;
                }
            }
            hunks.push({ type: "collapsed", hiddenCount: slice.length, rows: slice });
        } else {
            const prev = hunks.at(-1);
            if (prev && prev.type === "hunk") {
                prev.rows.push(...slice);
            } else {
                const firstBefore = slice.find((r) => r.beforeLine !== null)?.beforeLine ?? 1;
                const firstAfter = slice.find((r) => r.afterLine !== null)?.afterLine ?? 1;
                hunks.push({
                    type: "hunk",
                    header: `@@ -${firstBefore} +${firstAfter} @@`,
                    rows: slice,
                });
            }
        }
    }
    return hunks;
}

export function buildSplitRows(rows: readonly LineDiffRow[]): SplitDiffRow[] {
    const split: SplitDiffRow[] = [];
    let idx = 0;
    while (idx < rows.length) {
        const row = rows[idx]!;
        if (row.kind === "equal") {
            split.push({
                left: { line: row.beforeLine, kind: "equal", text: row.text },
                right: { line: row.afterLine, kind: "equal", text: row.text },
            });
            idx++;
            continue;
        }
        const removes: LineDiffRow[] = [];
        while (idx < rows.length && rows[idx]?.kind === "remove") {
            removes.push(rows[idx]!);
            idx++;
        }
        const adds: LineDiffRow[] = [];
        while (idx < rows.length && rows[idx]?.kind === "add") {
            adds.push(rows[idx]!);
            idx++;
        }
        const maxLen = Math.max(removes.length, adds.length);
        for (let k = 0; k < maxLen; k++) {
            const rem = removes[k];
            const add = adds[k];
            split.push({
                left: rem
                    ? { line: rem.beforeLine, kind: "remove", text: rem.text, ...(rem.tokens ? { tokens: rem.tokens } : {}) }
                    : { line: null, kind: "empty", text: "" },
                right: add
                    ? { line: add.afterLine, kind: "add", text: add.text, ...(add.tokens ? { tokens: add.tokens } : {}) }
                    : { line: null, kind: "empty", text: "" },
            });
        }
    }
    return split;
}

export function diffText(beforeText: string, afterText: string, contextLines = 3): TextDiffResult {
    const left = normalizeLines(beforeText);
    const right = normalizeLines(afterText);
    const rows = rawLcsLineDiff(left, right, 0, 0);
    let addedLines = 0;
    let removedLines = 0;
    for (const r of rows) {
        if (r.kind === "add") addedLines++;
        else if (r.kind === "remove") removedLines++;
    }
    return {
        changed: addedLines > 0 || removedLines > 0,
        addedLines,
        removedLines,
        rows,
        hunks: buildDiffHunks(rows, contextLines),
        splitRows: buildSplitRows(rows),
    };
}

export function diffStringSets(beforeList: readonly string[], afterList: readonly string[]): ListSetDiff {
    const beforeSet = new Set(beforeList);
    const afterSet = new Set(afterList);
    return {
        added: afterList.filter((item) => !beforeSet.has(item)),
        removed: beforeList.filter((item) => !afterSet.has(item)),
        unchanged: afterList.filter((item) => beforeSet.has(item)),
    };
}

function diffKeyedItems(
    beforeItems: readonly { id: string; text: string }[],
    afterItems: readonly { id: string; text: string }[],
): KeyedItemDiff[] {
    const beforeMap = new Map(beforeItems.map((i) => [i.id, i.text]));
    const afterMap = new Map(afterItems.map((i) => [i.id, i.text]));
    const orderedIds = Array.from(new Set([...beforeItems.map((i) => i.id), ...afterItems.map((i) => i.id)]));

    return orderedIds.map((id) => {
        const b = beforeMap.get(id);
        const a = afterMap.get(id);
        if (b === undefined && a !== undefined) {
            return { id, status: "added" as const, beforeText: "", afterText: a };
        }
        if (b !== undefined && a === undefined) {
            return { id, status: "removed" as const, beforeText: b, afterText: "" };
        }
        const beforeText = b ?? "";
        const afterText = a ?? "";
        if (beforeText !== afterText) {
            return {
                id,
                status: "modified" as const,
                beforeText,
                afterText,
                tokens: diffWords(beforeText, afterText),
            };
        }
        return { id, status: "unchanged" as const, beforeText, afterText };
    });
}

export function diffRequirementsRevisions(before: Revision, after: Revision): RequirementsRevisionDiff {
    const titleChanged = before.title !== after.title;
    const titleTokens = diffWords(before.title, after.title);
    const descriptionDiff = diffText(before.description ?? "", after.description ?? "");
    const requirements = diffKeyedItems(before.requirements, after.requirements);
    const mapTaskItems = (tasks: readonly string[]) =>
        tasks.map((raw, idx) => {
            const m = /^(DT-\d+|T-\d+)\s*[:—-]\s*(.+)$/i.exec(raw);
            return m ? { id: m[1]!.toUpperCase(), text: m[2]! } : { id: `DT-${idx + 1}`, text: raw };
        });
    const designTasks = diffKeyedItems(
        mapTaskItems(before.designTasks ?? []),
        mapTaskItems(after.designTasks ?? []),
    );
    const blockingUnknowns = diffStringSets(before.blockingUnknowns, after.blockingUnknowns);

    const criteriaAdded = requirements.filter((r) => r.status === "added").length;
    const criteriaRemoved = requirements.filter((r) => r.status === "removed").length;
    const criteriaModified = requirements.filter((r) => r.status === "modified").length;
    const tasksChanged = designTasks.filter((t) => t.status !== "unchanged").length;

    const changed = titleChanged ||
        descriptionDiff.changed ||
        criteriaAdded > 0 ||
        criteriaRemoved > 0 ||
        criteriaModified > 0 ||
        tasksChanged > 0 ||
        blockingUnknowns.added.length > 0 ||
        blockingUnknowns.removed.length > 0;

    return {
        fromRevision: before.number,
        toRevision: after.number,
        changed,
        titleChanged,
        beforeTitle: before.title,
        afterTitle: after.title,
        titleTokens,
        descriptionDiff,
        requirements,
        designTasks,
        blockingUnknowns,
        summaryCounts: {
            criteriaAdded,
            criteriaRemoved,
            criteriaModified,
            tasksChanged,
            unknownsResolved: blockingUnknowns.removed.length,
            unknownsAdded: blockingUnknowns.added.length,
        },
    };
}

function extractSectionsMap(designObj: Record<string, Json>): Map<string, { title: string; content: string }> {
    const map = new Map<string, { title: string; content: string }>();
    const raw = designObj.sections;
    if (Array.isArray(raw)) {
        for (const item of raw) {
            if (!isRecord(item)) continue;
            const key = String(item.id || item.key || item.title || `section-${map.size + 1}`);
            const title = typeof item.title === "string" ? item.title : formatSectionTitle(key);
            const content = typeof item.content === "string"
                ? item.content
                : typeof item.markdown === "string"
                    ? item.markdown
                    : JSON.stringify(item, null, 2);
            map.set(key, { title, content });
        }
    } else if (isRecord(raw)) {
        for (const [key, val] of Object.entries(raw)) {
            const content = typeof val === "string" ? val : JSON.stringify(val, null, 2);
            map.set(key, { title: formatSectionTitle(key), content });
        }
    }
    return map;
}

export function diffDesignSnapshots(
    beforeDesignRaw: unknown,
    afterDesignRaw: unknown,
    beforeReviewRaw?: unknown,
    afterReviewRaw?: unknown,
): DesignSnapshotDiff {
    const beforeDesign = isRecord(beforeDesignRaw) ? beforeDesignRaw : {};
    const afterDesign = isRecord(afterDesignRaw) ? afterDesignRaw : {};

    const beforeSections = extractSectionsMap(beforeDesign);
    const afterSections = extractSectionsMap(afterDesign);
    const allSectionKeys = Array.from(new Set([...beforeSections.keys(), ...afterSections.keys()]));

    const sections: DesignSectionDiff[] = allSectionKeys.map((key) => {
        const b = beforeSections.get(key);
        const a = afterSections.get(key);
        const diff = diffText(b?.content ?? "", a?.content ?? "");
        const status: DesignSectionDiff["status"] = !b
            ? "added"
            : !a
                ? "removed"
                : diff.changed
                    ? "modified"
                    : "unchanged";
        return {
            key,
            title: a?.title ?? b?.title ?? formatSectionTitle(key),
            status,
            diff,
        };
    });

    const beforeChanges = new Map<string, Record<string, Json>>();
    for (const c of asArray(beforeDesign.changes).filter(isRecord)) {
        beforeChanges.set(String(c.id || c.path || beforeChanges.size), c);
    }
    const afterChanges = new Map<string, Record<string, Json>>();
    for (const c of asArray(afterDesign.changes).filter(isRecord)) {
        afterChanges.set(String(c.id || c.path || afterChanges.size), c);
    }
    const allChangeIds = Array.from(new Set([...beforeChanges.keys(), ...afterChanges.keys()]));

    const changes: DesignChangeUnitDiff[] = allChangeIds.map((id) => {
        const b = beforeChanges.get(id);
        const a = afterChanges.get(id);
        const bSummary = typeof b?.summary === "string" ? b.summary : (typeof b?.description === "string" ? b.description : "");
        const aSummary = typeof a?.summary === "string" ? a.summary : (typeof a?.description === "string" ? a.description : "");
        const repository = String(a?.repository ?? b?.repository ?? "");
        const filePath = String(a?.path ?? b?.path ?? "");
        if (!b) {
            return {
                id,
                status: "added",
                repository,
                path: filePath,
                beforeSummary: "",
                afterSummary: aSummary,
                changedFields: ["added"],
            };
        }
        if (!a) {
            return {
                id,
                status: "removed",
                repository,
                path: filePath,
                beforeSummary: bSummary,
                afterSummary: "",
                changedFields: ["removed"],
            };
        }
        const changedFields: string[] = [];
        if (String(b.repository ?? "") !== String(a.repository ?? "")) changedFields.push("repository");
        if (String(b.path ?? "") !== String(a.path ?? "")) changedFields.push("path");
        if (String(b.kind ?? "") !== String(a.kind ?? "")) changedFields.push("kind");
        if (bSummary !== aSummary) changedFields.push("summary");
        if (JSON.stringify(b.requirementIds ?? []) !== JSON.stringify(a.requirementIds ?? [])) changedFields.push("requirementIds");
        if (JSON.stringify(b.verification ?? null) !== JSON.stringify(a.verification ?? null)) changedFields.push("verification");

        return {
            id,
            status: changedFields.length > 0 ? "modified" : "unchanged",
            repository,
            path: filePath,
            beforeSummary: bSummary,
            afterSummary: aSummary,
            ...(bSummary !== aSummary ? { summaryTokens: diffWords(bSummary, aSummary) } : {}),
            changedFields,
        };
    });

    const beforeTrace = new Map<string, Record<string, Json>>();
    for (const t of asArray(beforeDesign.traceability).filter(isRecord)) {
        beforeTrace.set(String(t.requirementId || beforeTrace.size), t);
    }
    const afterTrace = new Map<string, Record<string, Json>>();
    for (const t of asArray(afterDesign.traceability).filter(isRecord)) {
        afterTrace.set(String(t.requirementId || afterTrace.size), t);
    }
    const allTraceIds = Array.from(new Set([...beforeTrace.keys(), ...afterTrace.keys()]));

    const traceability: DesignTraceabilityDiff[] = allTraceIds.map((requirementId) => {
        const b = beforeTrace.get(requirementId);
        const a = afterTrace.get(requirementId);
        const secDiff = diffStringSets(
            asArray(b?.sectionIds).map(String),
            asArray(a?.sectionIds).map(String),
        );
        const evDiff = diffStringSets(
            asArray(b?.evidenceIds).map(String),
            asArray(a?.evidenceIds).map(String),
        );
        const bVer = typeof b?.verification === "string" ? b.verification : "";
        const aVer = typeof a?.verification === "string" ? a.verification : "";
        const verificationChanged = bVer !== aVer;
        const status: DesignTraceabilityDiff["status"] = !b
            ? "added"
            : !a
                ? "removed"
                : (secDiff.added.length > 0 || secDiff.removed.length > 0 || evDiff.added.length > 0 || evDiff.removed.length > 0 || verificationChanged)
                    ? "modified"
                    : "unchanged";
        return {
            requirementId,
            status,
            sectionsAdded: secDiff.added,
            sectionsRemoved: secDiff.removed,
            evidenceAdded: evDiff.added,
            evidenceRemoved: evDiff.removed,
            verificationChanged,
            beforeVerification: bVer,
            afterVerification: aVer,
        };
    });

    const assumptions = diffStringSets(
        asArray(beforeDesign.assumptions).map(String),
        asArray(afterDesign.assumptions).map(String),
    );
    const openDecisions = diffStringSets(
        asArray(beforeDesign.openDecisions).map(String),
        asArray(afterDesign.openDecisions).map(String),
    );
    const blockingUnknowns = diffStringSets(
        asArray(beforeDesign.blockingUnknowns).map(String),
        asArray(afterDesign.blockingUnknowns).map(String),
    );

    let reviewDelta: DesignReviewDelta | null = null;
    const bRev = isRecord(beforeReviewRaw) ? beforeReviewRaw : null;
    const aRev = isRecord(afterReviewRaw) ? afterReviewRaw : null;
    if (bRev || aRev) {
        const mapFinding = (f: Record<string, Json>) => ({
            id: String(f.id || ""),
            severity: String(f.severity || "major"),
            location: String(f.location || ""),
            summary: String(f.requiredChange || f.evidence || f.summary || ""),
        });
        const bFindings = asArray(bRev?.findings).filter(isRecord).map(mapFinding);
        const aFindings = asArray(aRev?.findings).filter(isRecord).map(mapFinding);
        const aIds = new Set(aFindings.map((f) => `${f.id}:${f.location}`));
        const bIds = new Set(bFindings.map((f) => `${f.id}:${f.location}`));

        const findingsResolved = bFindings.filter((f) => !aIds.has(`${f.id}:${f.location}`));
        const findingsAdded = aFindings.filter((f) => !bIds.has(`${f.id}:${f.location}`));

        const rubricFlips: Array<{ key: string; from: boolean; to: boolean }> = [];
        const bRubric = isRecord(bRev?.rubric) ? bRev.rubric : {};
        const aRubric = isRecord(aRev?.rubric) ? aRev.rubric : {};
        for (const key of Array.from(new Set([...Object.keys(bRubric), ...Object.keys(aRubric)]))) {
            if (typeof bRubric[key] === "boolean" && typeof aRubric[key] === "boolean" && bRubric[key] !== aRubric[key]) {
                rubricFlips.push({ key, from: bRubric[key] as boolean, to: aRubric[key] as boolean });
            }
        }

        const changeChecks = asArray(aRev?.changeChecks).filter(isRecord).map((chk) => ({
            findingId: String(chk.findingId || chk.id || ""),
            status: String(chk.status || (chk.resolved === true ? "resolved" : "checked")),
            rationale: String(chk.rationale || chk.evidence || chk.summary || ""),
        }));

        reviewDelta = {
            findingsResolved,
            findingsAdded,
            findingsRemaining: aFindings,
            rubricFlips,
            changeChecks,
        };
    }

    const sectionsModified = sections.filter((s) => s.status === "modified").length;
    const sectionsAdded = sections.filter((s) => s.status === "added").length;
    const sectionsRemoved = sections.filter((s) => s.status === "removed").length;
    const linesAdded = sections.reduce((sum, s) => sum + s.diff.addedLines, 0);
    const linesRemoved = sections.reduce((sum, s) => sum + s.diff.removedLines, 0);
    const changesModified = changes.filter((c) => c.status === "modified").length;
    const changesAdded = changes.filter((c) => c.status === "added").length;
    const changesRemoved = changes.filter((c) => c.status === "removed").length;
    const traceabilityChanged = traceability.filter((t) => t.status !== "unchanged").length;
    const findingsResolvedCount = reviewDelta?.findingsResolved.length ?? 0;

    const changed = sectionsModified > 0 ||
        sectionsAdded > 0 ||
        sectionsRemoved > 0 ||
        changesModified > 0 ||
        changesAdded > 0 ||
        changesRemoved > 0 ||
        traceabilityChanged > 0 ||
        assumptions.added.length > 0 ||
        assumptions.removed.length > 0 ||
        openDecisions.added.length > 0 ||
        openDecisions.removed.length > 0 ||
        blockingUnknowns.added.length > 0 ||
        blockingUnknowns.removed.length > 0;

    return {
        changed,
        sections,
        changes,
        traceability,
        assumptions,
        openDecisions,
        blockingUnknowns,
        reviewDelta,
        summaryCounts: {
            sectionsModified,
            sectionsAdded,
            sectionsRemoved,
            linesAdded,
            linesRemoved,
            changesModified,
            changesAdded,
            changesRemoved,
            traceabilityChanged,
            findingsResolved: findingsResolvedCount,
        },
    };
}

export function diffPlanSnapshots(beforePlanRaw: unknown, afterPlanRaw: unknown): PlanSnapshotDiff {
    const beforePlan = isRecord(beforePlanRaw) ? beforePlanRaw : {};
    const afterPlan = isRecord(afterPlanRaw) ? afterPlanRaw : {};

    const summaryDiff = diffText(
        typeof beforePlan.summary === "string" ? beforePlan.summary : "",
        typeof afterPlan.summary === "string" ? afterPlan.summary : "",
    );

    const beforeItems = new Map<string, Record<string, Json>>();
    for (const item of asArray(beforePlan.items).filter(isRecord)) {
        beforeItems.set(String(item.id || beforeItems.size), item);
    }
    const afterItems = new Map<string, Record<string, Json>>();
    for (const item of asArray(afterPlan.items).filter(isRecord)) {
        afterItems.set(String(item.id || afterItems.size), item);
    }
    const allIds = Array.from(new Set([...beforeItems.keys(), ...afterItems.keys()]));

    const items: PlanPbiDiff[] = allIds.map((id) => {
        const b = beforeItems.get(id);
        const a = afterItems.get(id);
        const beforeTitle = typeof b?.title === "string" ? b.title : "";
        const title = typeof a?.title === "string" ? a.title : beforeTitle;
        const beforeRepository = typeof b?.repository === "string" ? b.repository : "";
        const repository = typeof a?.repository === "string" ? a.repository : beforeRepository;

        const descriptionDiff = diffText(
            typeof b?.description === "string" ? b.description : "",
            typeof a?.description === "string" ? a.description : "",
        );
        const files = diffStringSets(asArray(b?.files).map(String), asArray(a?.files).map(String));
        const tests = diffStringSets(asArray(b?.tests).map(String), asArray(a?.tests).map(String));
        const acceptanceCriteria = diffStringSets(
            asArray(b?.acceptanceCriteria).map(String),
            asArray(a?.acceptanceCriteria).map(String),
        );
        const dependsOn = diffStringSets(asArray(b?.dependsOn).map(String), asArray(a?.dependsOn).map(String));
        const requirementIds = diffStringSets(asArray(b?.requirementIds).map(String), asArray(a?.requirementIds).map(String));

        if (!b) {
            return {
                id,
                title,
                beforeTitle: "",
                repository,
                beforeRepository: "",
                status: "added",
                titleChanged: false,
                repositoryChanged: false,
                descriptionDiff,
                files,
                tests,
                acceptanceCriteria,
                dependsOn,
                requirementIds,
                changedFields: ["added"],
            };
        }
        if (!a) {
            return {
                id,
                title: beforeTitle,
                beforeTitle,
                repository: beforeRepository,
                beforeRepository,
                status: "removed",
                titleChanged: false,
                repositoryChanged: false,
                descriptionDiff,
                files,
                tests,
                acceptanceCriteria,
                dependsOn,
                requirementIds,
                changedFields: ["removed"],
            };
        }

        const changedFields: string[] = [];
        const titleChanged = beforeTitle !== title;
        const repositoryChanged = beforeRepository !== repository;
        if (titleChanged) changedFields.push("title");
        if (repositoryChanged) changedFields.push("repository");
        if (descriptionDiff.changed) changedFields.push("description");
        if (files.added.length > 0 || files.removed.length > 0) changedFields.push("files");
        if (tests.added.length > 0 || tests.removed.length > 0) changedFields.push("tests");
        if (acceptanceCriteria.added.length > 0 || acceptanceCriteria.removed.length > 0) changedFields.push("acceptanceCriteria");
        if (dependsOn.added.length > 0 || dependsOn.removed.length > 0) changedFields.push("dependsOn");
        if (requirementIds.added.length > 0 || requirementIds.removed.length > 0) changedFields.push("requirementIds");

        return {
            id,
            title,
            beforeTitle,
            repository,
            beforeRepository,
            status: changedFields.length > 0 ? "modified" : "unchanged",
            titleChanged,
            repositoryChanged,
            descriptionDiff,
            files,
            tests,
            acceptanceCriteria,
            dependsOn,
            requirementIds,
            changedFields,
        };
    });

    const blockingUnknowns = diffStringSets(
        asArray(beforePlan.blockingUnknowns).map(String),
        asArray(afterPlan.blockingUnknowns).map(String),
    );

    const addedPbis = items.filter((i) => i.status === "added").length;
    const removedPbis = items.filter((i) => i.status === "removed").length;
    const modifiedPbis = items.filter((i) => i.status === "modified").length;
    const unchangedPbis = items.filter((i) => i.status === "unchanged").length;

    const changed = summaryDiff.changed ||
        addedPbis > 0 ||
        removedPbis > 0 ||
        modifiedPbis > 0 ||
        blockingUnknowns.added.length > 0 ||
        blockingUnknowns.removed.length > 0;

    return {
        changed,
        summaryDiff,
        items,
        blockingUnknowns,
        summaryCounts: {
            addedPbis,
            removedPbis,
            modifiedPbis,
            unchangedPbis,
            filesAdded: items.reduce((s, i) => s + i.files.added.length, 0),
            filesRemoved: items.reduce((s, i) => s + i.files.removed.length, 0),
            testsAdded: items.reduce((s, i) => s + i.tests.added.length, 0),
            criteriaAdded: items.reduce((s, i) => s + i.acceptanceCriteria.added.length, 0),
        },
    };
}
