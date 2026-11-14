// Copyright (c) Microsoft Corporation. All rights reserved.

import { useMemo, useState } from "react";
import type { Revision } from "../../core/contracts.ts";
import {
    diffDesignSnapshots,
    diffPlanSnapshots,
    diffRequirementsRevisions,
    type DiffHunk,
    type LineDiffRow,
    type ListSetDiff,
    type SplitDiffRow,
    type TextDiffResult,
    type WordDiffToken,
} from "../../core/diff.ts";
import type { Artifact, Json } from "../../core/lifecycle-contracts.ts";
import type { FeatureQaContext } from "../../core/qa-contracts.ts";

export type DiffLayoutMode = "unified" | "split";

function isRecord(value: unknown): value is Record<string, Json> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asArray(value: unknown): Json[] {
    return Array.isArray(value) ? value : [];
}

function RenderWordTokens({
    tokens,
    fallbackText,
    side,
}: {
    tokens?: WordDiffToken[];
    fallbackText: string;
    side: "add" | "remove" | "both";
}) {
    if (!tokens || tokens.length === 0) {
        return <>{fallbackText || " "}</>;
    }
    return (
        <>
            {tokens.map((tok, idx) => {
                if (tok.kind === "add" && (side === "add" || side === "both")) {
                    return <mark key={idx} className="diff-word-add">{tok.text}</mark>;
                }
                if (tok.kind === "remove" && (side === "remove" || side === "both")) {
                    return <mark key={idx} className="diff-word-del">{tok.text}</mark>;
                }
                if (tok.kind === "equal") {
                    return <span key={idx}>{tok.text}</span>;
                }
                return null;
            })}
        </>
    );
}

export function CodeStyleTextDiff({
    diff,
    layout,
    emptyMessage = "No text changes between selected snapshots.",
}: {
    diff: TextDiffResult;
    layout: DiffLayoutMode;
    emptyMessage?: string;
}) {
    const [expandedGaps, setExpandedGaps] = useState<Set<number>>(new Set());

    if (!diff.changed) {
        return <p className="muted diff-unchanged-note">{emptyMessage}</p>;
    }

    const toggleGap = (idx: number) => {
        setExpandedGaps((prev) => {
            const next = new Set(prev);
            if (next.has(idx)) next.delete(idx);
            else next.add(idx);
            return next;
        });
    };

    if (layout === "split") {
        return (
            <div className="sf-diff-table-wrap" role="region" aria-label="Side-by-side text diff">
                <table className="sf-diff-table split">
                    <colgroup>
                        <col className="diff-col-num" />
                        <col className="diff-col-code" />
                        <col className="diff-col-num" />
                        <col className="diff-col-code" />
                    </colgroup>
                    <tbody>
                        {diff.splitRows.map((row: SplitDiffRow, idx: number) => (
                            <tr key={idx}>
                                <td className={`diff-ln diff-${row.left.kind}`}>{row.left.line ?? ""}</td>
                                <td className={`diff-cell diff-${row.left.kind}`}>
                                    {row.left.kind === "remove" && <span className="diff-sign">−</span>}
                                    {row.left.kind === "equal" && <span className="diff-sign"> </span>}
                                    <span className="diff-text">
                                        <RenderWordTokens
                                            tokens={row.left.tokens}
                                            fallbackText={row.left.text}
                                            side="remove"
                                        />
                                    </span>
                                </td>
                                <td className={`diff-ln diff-${row.right.kind}`}>{row.right.line ?? ""}</td>
                                <td className={`diff-cell diff-${row.right.kind}`}>
                                    {row.right.kind === "add" && <span className="diff-sign">+</span>}
                                    {row.right.kind === "equal" && <span className="diff-sign"> </span>}
                                    <span className="diff-text">
                                        <RenderWordTokens
                                            tokens={row.right.tokens}
                                            fallbackText={row.right.text}
                                            side="add"
                                        />
                                    </span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        );
    }

    const renderUnifiedRow = (row: LineDiffRow, key: string) => (
        <tr key={key} className={`diff-row-${row.kind}`}>
            <td className={`diff-ln diff-${row.kind}`}>{row.beforeLine ?? ""}</td>
            <td className={`diff-ln diff-${row.kind}`}>{row.afterLine ?? ""}</td>
            <td className={`diff-cell diff-${row.kind}`}>
                <span className="diff-sign">
                    {row.kind === "add" ? "+" : row.kind === "remove" ? "−" : " "}
                </span>
                <span className="diff-text">
                    <RenderWordTokens
                        tokens={row.tokens}
                        fallbackText={row.text}
                        side={row.kind === "add" ? "add" : row.kind === "remove" ? "remove" : "both"}
                    />
                </span>
            </td>
        </tr>
    );

    return (
        <div className="sf-diff-table-wrap" role="region" aria-label="Unified text diff">
            <table className="sf-diff-table unified">
                <colgroup>
                    <col className="diff-col-num" />
                    <col className="diff-col-num" />
                    <col className="diff-col-code-full" />
                </colgroup>
                <tbody>
                    {diff.hunks.map((hunk: DiffHunk, hIdx: number) => {
                        if (hunk.type === "collapsed") {
                            if (expandedGaps.has(hIdx)) {
                                return hunk.rows.map((r, rIdx) => renderUnifiedRow(r, `exp-${hIdx}-${rIdx}`));
                            }
                            return (
                                <tr key={`gap-${hIdx}`} className="diff-collapsed-row">
                                    <td colSpan={3}>
                                        <button
                                            type="button"
                                            className="diff-expand-btn"
                                            onClick={() => toggleGap(hIdx)}
                                        >
                                            ↕ Expand {hunk.hiddenCount} unchanged line{hunk.hiddenCount === 1 ? "" : "s"}
                                        </button>
                                    </td>
                                </tr>
                            );
                        }
                        return [
                            <tr key={`hdr-${hIdx}`} className="diff-hunk-header">
                                <td colSpan={3}>{hunk.header}</td>
                            </tr>,
                            ...hunk.rows.map((r, rIdx) => renderUnifiedRow(r, `h-${hIdx}-${rIdx}`)),
                        ];
                    })}
                </tbody>
            </table>
        </div>
    );
}

function ListSetDiffChips({ label, diff }: { label: string; diff: ListSetDiff }) {
    if (diff.added.length === 0 && diff.removed.length === 0) return null;
    return (
        <div className="diff-list-block">
            <span className="eyebrow">{label}</span>
            <ul className="diff-item-list">
                {diff.removed.map((item, idx) => (
                    <li key={`rem-${idx}`} className="diff-list-item removed">
                        <span className="diff-badge removed">− REMOVED</span>
                        <span>{item}</span>
                    </li>
                ))}
                {diff.added.map((item, idx) => (
                    <li key={`add-${idx}`} className="diff-list-item added">
                        <span className="diff-badge added">+ ADDED</span>
                        <span>{item}</span>
                    </li>
                ))}
            </ul>
        </div>
    );
}

function DiffToolbar({
    fromLabel,
    toLabel,
    options,
    fromIdx,
    toIdx,
    onSelectFrom,
    onSelectTo,
    layout,
    onLayoutChange,
    changesOnly,
    onChangesOnlyChange,
    onAskAi,
}: {
    fromLabel?: string;
    toLabel?: string;
    options: Array<{ label: string; sublabel?: string }>;
    fromIdx: number;
    toIdx: number;
    onSelectFrom: (idx: number) => void;
    onSelectTo: (idx: number) => void;
    layout: DiffLayoutMode;
    onLayoutChange: (mode: DiffLayoutMode) => void;
    changesOnly: boolean;
    onChangesOnlyChange: (next: boolean) => void;
    onAskAi?: () => void;
}) {
    return (
        <div className="diff-toolbar">
            <div className="diff-selectors">
                <label className="diff-select-label">
                    <span>{fromLabel || "Base"}</span>
                    <select
                        value={fromIdx}
                        onChange={(e) => onSelectFrom(Number(e.target.value))}
                        aria-label="Base snapshot"
                    >
                        {options.map((opt, idx) => (
                            <option key={idx} value={idx}>
                                {opt.label}{opt.sublabel ? ` (${opt.sublabel})` : ""}
                            </option>
                        ))}
                    </select>
                </label>
                <span className="diff-arrow" aria-hidden="true">→</span>
                <label className="diff-select-label">
                    <span>{toLabel || "Compare"}</span>
                    <select
                        value={toIdx}
                        onChange={(e) => onSelectTo(Number(e.target.value))}
                        aria-label="Target snapshot"
                    >
                        {options.map((opt, idx) => (
                            <option key={idx} value={idx}>
                                {opt.label}{opt.sublabel ? ` (${opt.sublabel})` : ""}
                            </option>
                        ))}
                    </select>
                </label>
            </div>

            <div className="diff-toolbar-controls">
                <div className="diff-mode-toggle" role="group" aria-label="Diff layout">
                    <button
                        type="button"
                        className={layout === "unified" ? "active" : ""}
                        onClick={() => onLayoutChange("unified")}
                    >
                        Unified
                    </button>
                    <button
                        type="button"
                        className={layout === "split" ? "active" : ""}
                        onClick={() => onLayoutChange("split")}
                    >
                        Side-by-Side
                    </button>
                </div>
                <label className="diff-checkbox-pill">
                    <input
                        type="checkbox"
                        checked={changesOnly}
                        onChange={(e) => onChangesOnlyChange(e.target.checked)}
                    />
                    <span>Changes only</span>
                </label>
                {onAskAi && (
                    <button type="button" className="ghost-chip-btn ask-ai-chip" onClick={onAskAi}>
                        ✦ Ask AI about Diff
                    </button>
                )}
            </div>
        </div>
    );
}

export function RequirementsRevisionDiffExplorer({
    history,
    onAskAi,
}: {
    history: Revision[];
    onAskAi?: (ctx: FeatureQaContext) => void;
}) {
    const [fromIdx, setFromIdx] = useState(() => Math.max(0, history.length - 2));
    const [toIdx, setToIdx] = useState(() => Math.max(0, history.length - 1));
    const [layout, setLayout] = useState<DiffLayoutMode>("unified");
    const [changesOnly, setChangesOnly] = useState(true);

    const safeFrom = Math.min(Math.max(0, fromIdx), Math.max(0, history.length - 1));
    const safeTo = Math.min(Math.max(0, toIdx), Math.max(0, history.length - 1));
    const before = history[safeFrom];
    const after = history[safeTo];

    const diff = useMemo(() => {
        if (!before || !after) return null;
        return diffRequirementsRevisions(before, after);
    }, [before, after]);

    if (history.length < 2 || !before || !after || !diff) {
        return null;
    }

    const options = history.map((rev) => ({
        label: `Revision ${rev.number}`,
        sublabel: `${rev.requirements.length} criteria · ${new Date(rev.createdAt).toLocaleTimeString()}`,
    }));

    const visibleReqs = changesOnly
        ? diff.requirements.filter((r) => r.status !== "unchanged")
        : diff.requirements;
    const visibleTasks = changesOnly
        ? diff.designTasks.filter((t) => t.status !== "unchanged")
        : diff.designTasks;

    return (
        <div className="revision-diff-container">
            <DiffToolbar
                fromLabel="From Brief"
                toLabel="To Brief"
                options={options}
                fromIdx={safeFrom}
                toIdx={safeTo}
                onSelectFrom={setFromIdx}
                onSelectTo={setToIdx}
                layout={layout}
                onLayoutChange={setLayout}
                changesOnly={changesOnly}
                onChangesOnlyChange={setChangesOnly}
                onAskAi={onAskAi ? () => onAskAi({
                    kind: "requirement",
                    title: `Brief Revision ${before.number} → Revision ${after.number} Diff`,
                    snippet: [
                        `Criteria +${diff.summaryCounts.criteriaAdded} / -${diff.summaryCounts.criteriaRemoved} / ~${diff.summaryCounts.criteriaModified}`,
                        `Design tasks changed: ${diff.summaryCounts.tasksChanged}`,
                        `Unknowns resolved: ${diff.summaryCounts.unknownsResolved}`,
                    ].join(" · "),
                }) : undefined}
            />

            <div className="diff-summary-strip">
                {diff.summaryCounts.criteriaAdded > 0 && (
                    <span className="studio-pill emerald">+{diff.summaryCounts.criteriaAdded} Criteria Added</span>
                )}
                {diff.summaryCounts.criteriaModified > 0 && (
                    <span className="studio-pill blue">~{diff.summaryCounts.criteriaModified} Criteria Refined</span>
                )}
                {diff.summaryCounts.criteriaRemoved > 0 && (
                    <span className="studio-pill crimson">−{diff.summaryCounts.criteriaRemoved} Criteria Removed</span>
                )}
                {diff.summaryCounts.tasksChanged > 0 && (
                    <span className="studio-pill purple">{diff.summaryCounts.tasksChanged} Design Obligation{diff.summaryCounts.tasksChanged === 1 ? "" : "s"} Updated</span>
                )}
                {diff.summaryCounts.unknownsResolved > 0 && (
                    <span className="studio-pill emerald">✓ {diff.summaryCounts.unknownsResolved} Open Question{diff.summaryCounts.unknownsResolved === 1 ? "" : "s"} Resolved</span>
                )}
                {!diff.changed && <span className="studio-pill neutral">Identical revisions</span>}
            </div>

            {diff.titleChanged && (
                <div className="diff-section-card">
                    <div className="diff-section-head">
                        <strong>Feature Title Updated</strong>
                    </div>
                    <p className="diff-inline-sentence">
                        <RenderWordTokens tokens={diff.titleTokens} fallbackText={diff.afterTitle} side="both" />
                    </p>
                </div>
            )}

            {diff.descriptionDiff.changed && (
                <div className="diff-section-card">
                    <div className="diff-section-head">
                        <strong>Problem Statement &amp; Brief</strong>
                        <span className="diff-stat-pill">
                            <span className="add">+{diff.descriptionDiff.addedLines}</span>{" "}
                            <span className="del">−{diff.descriptionDiff.removedLines}</span>
                        </span>
                    </div>
                    <CodeStyleTextDiff diff={diff.descriptionDiff} layout={layout} />
                </div>
            )}

            {visibleReqs.length > 0 && (
                <div className="diff-section-card">
                    <div className="diff-section-head">
                        <strong>Success Criteria ({visibleReqs.length})</strong>
                    </div>
                    <ul className="diff-item-list">
                        {visibleReqs.map((item) => (
                            <li key={item.id} className={`diff-list-item ${item.status}`}>
                                <span className="mono-chip">{item.id}</span>
                                <span className={`diff-badge ${item.status}`}>{item.status.toUpperCase()}</span>
                                <span className="diff-item-body">
                                    {item.status === "modified" ? (
                                        <RenderWordTokens tokens={item.tokens} fallbackText={item.afterText} side="both" />
                                    ) : item.status === "removed" ? (
                                        item.beforeText
                                    ) : (
                                        item.afterText
                                    )}
                                </span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {visibleTasks.length > 0 && (
                <div className="diff-section-card">
                    <div className="diff-section-head">
                        <strong>Engineering Obligations for Design Agents ({visibleTasks.length})</strong>
                    </div>
                    <ul className="diff-item-list">
                        {visibleTasks.map((task) => (
                            <li key={task.id} className={`diff-list-item ${task.status}`}>
                                <span className="mono-chip">{task.id}</span>
                                <span className={`diff-badge ${task.status}`}>{task.status.toUpperCase()}</span>
                                <span className="diff-item-body">
                                    {task.status === "modified" ? (
                                        <RenderWordTokens tokens={task.tokens} fallbackText={task.afterText} side="both" />
                                    ) : task.status === "removed" ? (
                                        task.beforeText
                                    ) : (
                                        task.afterText
                                    )}
                                </span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <ListSetDiffChips label="OPEN SCOPE QUESTIONS" diff={diff.blockingUnknowns} />
        </div>
    );
}

interface DesignSnapshotEntry {
    id: string;
    label: string;
    sublabel: string;
    design: Record<string, Json>;
    review: Record<string, Json> | null;
}

export function buildDesignSnapshots(
    currentArtifact: Artifact,
    allDesignArtifacts: Artifact[] = [],
): DesignSnapshotEntry[] {
    const snapshots: DesignSnapshotEntry[] = [];
    const orderedArtifacts = allDesignArtifacts.length > 0
        ? [...allDesignArtifacts].sort((a, b) => a.createdAt.localeCompare(b.createdAt))
        : [currentArtifact];

    const multiGen = orderedArtifacts.length > 1;
    for (const art of orderedArtifacts) {
        const body = isRecord(art.content) ? art.content : {};
        const finalDesign = isRecord(body.design) ? body.design : null;
        const reviews = asArray(body.reviews).filter(isRecord);
        const genPrefix = multiGen ? `Gen ${art.generation} · ` : "";

        let addedCandidates = 0;
        reviews.forEach((revEntry, idx) => {
            const candidate = isRecord(revEntry.candidate) ? revEntry.candidate : null;
            const rev = isRecord(revEntry.review) ? revEntry.review : null;
            const findingsCount = asArray(rev?.findings).length;
            const pass = rev?.pass === true;
            if (candidate) {
                addedCandidates++;
                snapshots.push({
                    id: `${art.id}:r${idx + 1}`,
                    label: `${genPrefix}Round ${idx + 1} Draft`,
                    sublabel: pass ? "Reviewer Approved ✓" : `${findingsCount} Reviewer Finding${findingsCount === 1 ? "" : "s"}`,
                    design: candidate,
                    review: rev,
                });
            }
        });

        if (finalDesign && addedCandidates === 0) {
            const lastRev = isRecord(reviews.at(-1)?.review) ? (reviews.at(-1)!.review as Record<string, Json>) : null;
            const rec = typeof body.recommendation === "string" ? body.recommendation : "Saved Spec";
            snapshots.push({
                id: `${art.id}:final`,
                label: `${genPrefix}Final Spec`,
                sublabel: rec,
                design: finalDesign,
                review: lastRev,
            });
        } else if (finalDesign && addedCandidates === 1 && reviews.length > 1) {
            const lastRev = isRecord(reviews.at(-1)?.review) ? (reviews.at(-1)!.review as Record<string, Json>) : null;
            snapshots.push({
                id: `${art.id}:final`,
                label: `${genPrefix}Round ${reviews.length} Final`,
                sublabel: typeof body.recommendation === "string" ? body.recommendation : "Approved",
                design: finalDesign,
                review: lastRev,
            });
        }
    }

    return snapshots;
}

export function DesignRevisionDiffExplorer({
    artifact,
    allDesignArtifacts = [],
    onAskAi,
}: {
    artifact: Artifact;
    allDesignArtifacts?: Artifact[];
    onAskAi?: (ctx: FeatureQaContext) => void;
}) {
    const snapshots = useMemo(
        () => buildDesignSnapshots(artifact, allDesignArtifacts),
        [artifact, allDesignArtifacts],
    );
    const [fromIdx, setFromIdx] = useState(() => Math.max(0, snapshots.length - 2));
    const [toIdx, setToIdx] = useState(() => Math.max(0, snapshots.length - 1));
    const [layout, setLayout] = useState<DiffLayoutMode>("unified");
    const [changesOnly, setChangesOnly] = useState(true);

    const safeFrom = Math.min(Math.max(0, fromIdx), Math.max(0, snapshots.length - 1));
    const safeTo = Math.min(Math.max(0, toIdx), Math.max(0, snapshots.length - 1));
    const beforeSnap = snapshots[safeFrom];
    const afterSnap = snapshots[safeTo];

    const diff = useMemo(() => {
        if (!beforeSnap || !afterSnap) return null;
        return diffDesignSnapshots(beforeSnap.design, afterSnap.design, beforeSnap.review, afterSnap.review);
    }, [beforeSnap, afterSnap]);

    if (snapshots.length < 2 || !beforeSnap || !afterSnap || !diff) {
        return (
            <div className="studio-callout neutral">
                <strong>Single Design Snapshot</strong> — This design passed in a single round with no subsequent revision yet. Run a revision or open a multi-round design to compare drafts.
            </div>
        );
    }

    const visibleSections = changesOnly
        ? diff.sections.filter((s) => s.status !== "unchanged")
        : diff.sections;
    const visibleChanges = changesOnly
        ? diff.changes.filter((c) => c.status !== "unchanged")
        : diff.changes;
    const visibleTrace = changesOnly
        ? diff.traceability.filter((t) => t.status !== "unchanged")
        : diff.traceability;

    return (
        <div className="revision-diff-container">
            <DiffToolbar
                fromLabel="Base Design"
                toLabel="Revised Design"
                options={snapshots.map((s) => ({ label: s.label, sublabel: s.sublabel }))}
                fromIdx={safeFrom}
                toIdx={safeTo}
                onSelectFrom={setFromIdx}
                onSelectTo={setToIdx}
                layout={layout}
                onLayoutChange={setLayout}
                changesOnly={changesOnly}
                onChangesOnlyChange={setChangesOnly}
                onAskAi={onAskAi ? () => onAskAi({
                    kind: "design-section",
                    title: `Design Diff (${beforeSnap.label} → ${afterSnap.label})`,
                    snippet: [
                        `Sections modified: ${diff.summaryCounts.sectionsModified} (+${diff.summaryCounts.linesAdded} / -${diff.summaryCounts.linesRemoved} lines)`,
                        `Change units updated: ${diff.summaryCounts.changesModified + diff.summaryCounts.changesAdded}`,
                        `Reviewer findings resolved: ${diff.summaryCounts.findingsResolved}`,
                    ].join(" · "),
                }) : undefined}
            />

            <div className="diff-summary-strip">
                <span className="studio-pill blue">
                    {diff.summaryCounts.sectionsModified + diff.summaryCounts.sectionsAdded} Section{diff.summaryCounts.sectionsModified + diff.summaryCounts.sectionsAdded === 1 ? "" : "s"} Changed
                </span>
                <span className="studio-pill emerald">+{diff.summaryCounts.linesAdded} lines</span>
                <span className="studio-pill crimson">−{diff.summaryCounts.linesRemoved} lines</span>
                {(diff.summaryCounts.changesModified + diff.summaryCounts.changesAdded + diff.summaryCounts.changesRemoved) > 0 && (
                    <span className="studio-pill purple">
                        {diff.summaryCounts.changesModified + diff.summaryCounts.changesAdded + diff.summaryCounts.changesRemoved} CHG Unit{diff.summaryCounts.changesModified + diff.summaryCounts.changesAdded + diff.summaryCounts.changesRemoved === 1 ? "" : "s"} Updated
                    </span>
                )}
                {diff.summaryCounts.findingsResolved > 0 && (
                    <span className="studio-pill emerald">
                        ✓ {diff.summaryCounts.findingsResolved} Reviewer Finding{diff.summaryCounts.findingsResolved === 1 ? "" : "s"} Resolved
                    </span>
                )}
            </div>

            {diff.reviewDelta && (diff.reviewDelta.findingsResolved.length > 0 || diff.reviewDelta.changeChecks.length > 0) && (
                <section className="diff-resolution-banner">
                    <div className="diff-section-head">
                        <strong>✓ Reviewer Findings Closed Between {beforeSnap.label} and {afterSnap.label}</strong>
                    </div>
                    <div className="diff-resolution-grid">
                        {diff.reviewDelta.findingsResolved.map((f, idx) => {
                            const matchingCheck = diff.reviewDelta?.changeChecks.find((c) => c.findingId === f.id);
                            return (
                                <div key={`${f.id}-${idx}`} className="diff-resolved-card">
                                    <div className="studio-badge-row">
                                        <span className="mono-chip emerald">{f.id || `F-${idx + 1}`} · RESOLVED ✓</span>
                                        {f.location && <span className="mono-chip">{f.location}</span>}
                                    </div>
                                    <p className="diff-resolved-finding">{f.summary}</p>
                                    {matchingCheck?.rationale && (
                                        <p className="diff-resolved-rationale">
                                            <strong>Independent Verification:</strong> {matchingCheck.rationale}
                                        </p>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </section>
            )}

            {visibleSections.length > 0 && (
                <div className="diff-sections-stack">
                    {visibleSections.map((sec) => (
                        <section key={sec.key} className="diff-section-card">
                            <div className="diff-section-head">
                                <div className="studio-badge-row">
                                    <strong>{sec.title}</strong>
                                    <span className={`diff-badge ${sec.status}`}>{sec.status.toUpperCase()}</span>
                                </div>
                                <div className="studio-badge-row">
                                    <span className="diff-stat-pill">
                                        <span className="add">+{sec.diff.addedLines}</span>{" "}
                                        <span className="del">−{sec.diff.removedLines}</span>
                                    </span>
                                    {onAskAi && sec.diff.changed && (
                                        <button
                                            type="button"
                                            className="ghost-chip-btn ask-ai-chip"
                                            onClick={() => onAskAi({
                                                kind: "design-section",
                                                title: `${sec.title} (${beforeSnap.label} → ${afterSnap.label})`,
                                                snippet: sec.diff.rows
                                                    .filter((r) => r.kind !== "equal")
                                                    .slice(0, 24)
                                                    .map((r) => `${r.kind === "add" ? "+" : "-"} ${r.text}`)
                                                    .join("\n"),
                                            })}
                                        >
                                            ✦ Ask AI
                                        </button>
                                    )}
                                </div>
                            </div>
                            <CodeStyleTextDiff diff={sec.diff} layout={layout} />
                        </section>
                    ))}
                </div>
            )}

            {visibleChanges.length > 0 && (
                <section className="diff-section-card">
                    <div className="diff-section-head">
                        <strong>Repository Change Units (`CHG-*`) Diff ({visibleChanges.length})</strong>
                    </div>
                    <ul className="diff-item-list">
                        {visibleChanges.map((chg) => (
                            <li key={chg.id} className={`diff-list-item ${chg.status}`}>
                                <span className="mono-chip blue">{chg.id}</span>
                                {chg.repository && <span className="mono-chip">{chg.repository}</span>}
                                <span className={`diff-badge ${chg.status}`}>{chg.status.toUpperCase()}</span>
                                <div className="diff-chg-body">
                                    {chg.path && <code className="diff-chg-path">{chg.path}</code>}
                                    <p>
                                        {chg.summaryTokens ? (
                                            <RenderWordTokens tokens={chg.summaryTokens} fallbackText={chg.afterSummary} side="both" />
                                        ) : (
                                            chg.afterSummary || chg.beforeSummary
                                        )}
                                    </p>
                                </div>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {visibleTrace.length > 0 && (
                <section className="diff-section-card">
                    <div className="diff-section-head">
                        <strong>Traceability Matrix Updates ({visibleTrace.length})</strong>
                    </div>
                    <ul className="diff-item-list">
                        {visibleTrace.map((tr) => (
                            <li key={tr.requirementId} className={`diff-list-item ${tr.status}`}>
                                <span className="mono-chip emerald">{tr.requirementId}</span>
                                <span className={`diff-badge ${tr.status}`}>{tr.status.toUpperCase()}</span>
                                <div className="diff-chg-body">
                                    {tr.sectionsAdded.length > 0 && <span>+Sections: {tr.sectionsAdded.join(", ")} · </span>}
                                    {tr.evidenceAdded.length > 0 && <span>+Evidence: {tr.evidenceAdded.join(", ")} · </span>}
                                    {tr.verificationChanged && <span>Verification updated → {tr.afterVerification}</span>}
                                </div>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            <ListSetDiffChips label="ASSUMPTIONS DIFF" diff={diff.assumptions} />
            <ListSetDiffChips label="OPEN DECISIONS DIFF" diff={diff.openDecisions} />
            <ListSetDiffChips label="BLOCKING UNKNOWNS DIFF" diff={diff.blockingUnknowns} />
        </div>
    );
}

interface PlanSnapshotEntry {
    id: string;
    label: string;
    sublabel: string;
    plan: Record<string, Json>;
}

export function buildPlanSnapshots(
    currentArtifact: Artifact,
    allPlanArtifacts: Artifact[] = [],
): PlanSnapshotEntry[] {
    const snapshots: PlanSnapshotEntry[] = [];
    const ordered = allPlanArtifacts.length > 0
        ? [...allPlanArtifacts].sort((a, b) => a.createdAt.localeCompare(b.createdAt))
        : [currentArtifact];

    const multiGen = ordered.length > 1;
    for (const art of ordered) {
        const body = isRecord(art.content) ? art.content : {};
        const reviewHistory = asArray(body.reviewHistory).filter(isRecord);
        const genPrefix = multiGen ? `Gen ${art.generation} · ` : "";

        let addedCandidates = 0;
        reviewHistory.forEach((entry, idx) => {
            const candidate = isRecord(entry.candidate) ? entry.candidate : null;
            const rev = isRecord(entry.review) ? entry.review : null;
            const pass = rev?.pass === true;
            const objections = asArray(rev?.findings).length;
            if (candidate) {
                addedCandidates++;
                const pbiCount = asArray(candidate.items).length;
                snapshots.push({
                    id: `${art.id}:r${idx + 1}`,
                    label: `${genPrefix}Round ${idx + 1} Plan`,
                    sublabel: pass ? `${pbiCount} PBIs · Approved ✓` : `${pbiCount} PBIs · ${objections} Objection${objections === 1 ? "" : "s"}`,
                    plan: candidate,
                });
            }
        });

        if (addedCandidates === 0 && Array.isArray(body.items)) {
            snapshots.push({
                id: `${art.id}:final`,
                label: `${genPrefix}Saved Plan`,
                sublabel: `${body.items.length} PBIs`,
                plan: body,
            });
        }
    }
    return snapshots;
}

export function PlanRevisionDiffExplorer({
    artifact,
    allPlanArtifacts = [],
    onAskAi,
}: {
    artifact: Artifact;
    allPlanArtifacts?: Artifact[];
    onAskAi?: (ctx: FeatureQaContext) => void;
}) {
    const snapshots = useMemo(
        () => buildPlanSnapshots(artifact, allPlanArtifacts),
        [artifact, allPlanArtifacts],
    );
    const [fromIdx, setFromIdx] = useState(() => Math.max(0, snapshots.length - 2));
    const [toIdx, setToIdx] = useState(() => Math.max(0, snapshots.length - 1));
    const [layout, setLayout] = useState<DiffLayoutMode>("unified");
    const [changesOnly, setChangesOnly] = useState(true);

    const safeFrom = Math.min(Math.max(0, fromIdx), Math.max(0, snapshots.length - 1));
    const safeTo = Math.min(Math.max(0, toIdx), Math.max(0, snapshots.length - 1));
    const beforeSnap = snapshots[safeFrom];
    const afterSnap = snapshots[safeTo];

    const diff = useMemo(() => {
        if (!beforeSnap || !afterSnap) return null;
        return diffPlanSnapshots(beforeSnap.plan, afterSnap.plan);
    }, [beforeSnap, afterSnap]);

    if (snapshots.length < 2 || !beforeSnap || !afterSnap || !diff) {
        return (
            <div className="studio-callout neutral">
                <strong>Single Plan Snapshot</strong> — The Feature Planner converged in Round 1 with zero Reviewer objections. If you request a plan revision, round-over-round and generation-over-generation PBI diffs appear here automatically.
            </div>
        );
    }

    const visibleItems = changesOnly
        ? diff.items.filter((i) => i.status !== "unchanged")
        : diff.items;

    return (
        <div className="revision-diff-container">
            <DiffToolbar
                fromLabel="Base Plan"
                toLabel="Revised Plan"
                options={snapshots.map((s) => ({ label: s.label, sublabel: s.sublabel }))}
                fromIdx={safeFrom}
                toIdx={safeTo}
                onSelectFrom={setFromIdx}
                onSelectTo={setToIdx}
                layout={layout}
                onLayoutChange={setLayout}
                changesOnly={changesOnly}
                onChangesOnlyChange={setChangesOnly}
                onAskAi={onAskAi ? () => onAskAi({
                    kind: "pbi",
                    title: `Plan Diff (${beforeSnap.label} → ${afterSnap.label})`,
                    snippet: `+${diff.summaryCounts.addedPbis} added / ~${diff.summaryCounts.modifiedPbis} modified / -${diff.summaryCounts.removedPbis} removed PBIs`,
                }) : undefined}
            />

            <div className="diff-summary-strip">
                {diff.summaryCounts.addedPbis > 0 && <span className="studio-pill emerald">+{diff.summaryCounts.addedPbis} PBI Added</span>}
                {diff.summaryCounts.modifiedPbis > 0 && <span className="studio-pill blue">~{diff.summaryCounts.modifiedPbis} PBI Modified</span>}
                {diff.summaryCounts.removedPbis > 0 && <span className="studio-pill crimson">−{diff.summaryCounts.removedPbis} PBI Removed</span>}
                {diff.summaryCounts.filesAdded > 0 && <span className="studio-pill neutral">+{diff.summaryCounts.filesAdded} Target Files</span>}
                {diff.summaryCounts.testsAdded > 0 && <span className="studio-pill purple">+{diff.summaryCounts.testsAdded} Required Tests</span>}
                {!diff.changed && <span className="studio-pill neutral">Identical plan snapshots</span>}
            </div>

            {diff.summaryDiff.changed && (
                <section className="diff-section-card">
                    <div className="diff-section-head">
                        <strong>Architecture &amp; Rollout Overview Diff</strong>
                        <span className="diff-stat-pill">
                            <span className="add">+{diff.summaryDiff.addedLines}</span>{" "}
                            <span className="del">−{diff.summaryDiff.removedLines}</span>
                        </span>
                    </div>
                    <CodeStyleTextDiff diff={diff.summaryDiff} layout={layout} />
                </section>
            )}

            {visibleItems.map((item) => (
                <section key={item.id} className="diff-section-card">
                    <div className="diff-section-head">
                        <div className="studio-badge-row">
                            <span className="mono-chip emerald">{item.id}</span>
                            {item.repository && <span className="mono-chip">{item.repository}</span>}
                            <strong>{item.title}</strong>
                            <span className={`diff-badge ${item.status}`}>{item.status.toUpperCase()}</span>
                        </div>
                        {item.changedFields.length > 0 && (
                            <span className="muted">Changed: {item.changedFields.join(", ")}</span>
                        )}
                    </div>
                    <ListSetDiffChips label="TARGET REPOSITORY FILES" diff={item.files} />
                    <ListSetDiffChips label="REQUIRED UNIT / INTEGRATION TESTS" diff={item.tests} />
                    <ListSetDiffChips label="ACCEPTANCE CRITERIA" diff={item.acceptanceCriteria} />
                    <ListSetDiffChips label="DEPENDENCIES" diff={item.dependsOn} />
                    {item.descriptionDiff.changed && (
                        <div style={{ marginTop: 10 }}>
                            <span className="eyebrow">TECHNICAL SPECIFICATION DIFF</span>
                            <CodeStyleTextDiff diff={item.descriptionDiff} layout={layout} />
                        </div>
                    )}
                </section>
            ))}
        </div>
    );
}
