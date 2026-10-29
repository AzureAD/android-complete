// Copyright (c) Microsoft Corporation. All rights reserved.

import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Artifact, Execution, ExecutionTraceEntry, FactoryFeature, Json } from "../../core/lifecycle-contracts.ts";
import { workerGuidance } from "../../runtime/guidance.ts";

function isRecord(value: unknown): value is Record<string, Json> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asArray(value: unknown): Json[] {
    return Array.isArray(value) ? value : [];
}

function formatSectionTitle(key: string): string {
    return key
        .replace(/([A-Z])/g, " $1")
        .replace(/^./, (char) => char.toUpperCase())
        .trim();
}

function roleDisplayName(role?: string, label?: string): { title: string; badge: string; guideKey?: keyof typeof workerGuidance } {
    const raw = `${role || ""} ${label || ""}`.toLowerCase();
    if (raw.includes("researcher") || raw.includes("evidence") || raw.includes("draft success criteria")) {
        return { title: "Repository Researcher", badge: "RESEARCH", guideKey: "research" };
    }
    if (raw.includes("design-reviewer") || raw.includes("independent design reviewer") || raw.includes("independent-review")) {
        return { title: "Independent Design Reviewer", badge: "REVIEWER", guideKey: "review" };
    }
    if (raw.includes("design-author") || raw.includes("design author") || raw.includes("design revision")) {
        return { title: "Design Author", badge: "BUILDER", guideKey: "design" };
    }
    if (raw.includes("plan-review")) {
        return { title: "Independent Plan Reviewer", badge: "REVIEWER", guideKey: "review" };
    }
    if (raw.includes("planner") || raw.includes("feature-planner")) {
        return { title: "Feature Planner", badge: "PLANNER", guideKey: "plan" };
    }
    if (raw.includes("code-reviewer") || raw.includes("validate")) {
        return { title: "Independent Code Reviewer", badge: "REVIEWER", guideKey: "codeReview" };
    }
    if (raw.includes("coder") || raw.includes("local builder")) {
        return { title: "Coding Builder", badge: "BUILDER", guideKey: "code" };
    }
    return { title: label || role || "Factory Worker", badge: "WORKER" };
}

export function DesignStudioExplorer({
    artifact,
    onAppendFeedback,
}: {
    artifact: Artifact;
    onAppendFeedback?: (snippet: string) => void;
}) {
    const body = isRecord(artifact.content) ? artifact.content : {};
    const design = isRecord(body.design) ? body.design : {};
    const sections = isRecord(design.sections) ? design.sections : {};
    const sectionEntries = Object.entries(sections);
    const evidence = isRecord(body.evidence) ? body.evidence : {};
    const references = asArray(evidence.references).filter(isRecord);
    const facts = asArray(evidence.facts).filter(isRecord);
    const traceability = asArray(design.traceability).filter(isRecord);
    const reviews = asArray(body.reviews).filter(isRecord);
    const stage0 = isRecord(body.stage0) ? body.stage0 : {};
    const requirements = asArray(stage0.requirements).filter(isRecord);
    const recommendation = typeof body.recommendation === "string" ? body.recommendation : "Design packet";
    const fixIterations = typeof body.fixIterations === "number" ? body.fixIterations : 0;
    const approvalVerification = isRecord(evidence.approvalVerification) ? evidence.approvalVerification : null;

    const assumptions = [
        ...asArray(design.assumptions).map(String),
        ...asArray(evidence.assumptions).map(String),
    ].filter((v, i, a) => v && a.indexOf(v) === i);
    const openDecisions = asArray(design.openDecisions).map(String).filter(Boolean);
    const blockingUnknowns = [
        ...asArray(design.blockingUnknowns).map(String),
        ...asArray(evidence.blockingUnknowns).map(String),
    ].filter((v, i, a) => v && a.indexOf(v) === i);
    const conflicts = asArray(evidence.conflicts).map(String).filter(Boolean);

    const [studioTab, setStudioTab] = useState<"sections" | "arena" | "traceability" | "risks">("sections");
    const [selectedReqId, setSelectedReqId] = useState<string | null>(null);
    const [selectedRoundIdx, setSelectedRoundIdx] = useState<number>(Math.max(0, reviews.length - 1));
    const [activeSectionFilter, setActiveSectionFilter] = useState<string | null>(null);

    const selectedTrace = traceability.find((row) => String(row.requirementId) === selectedReqId);
    const highlightedSections = new Set(asArray(selectedTrace?.sectionIds).map(String));
    const highlightedEvidence = new Set(asArray(selectedTrace?.evidenceIds).map(String));

    const currentReviewEntry = reviews[selectedRoundIdx];
    const currentReview = isRecord(currentReviewEntry?.review) ? currentReviewEntry.review : null;
    const currentValidationErrors = asArray(currentReviewEntry?.validationErrors).map(String);
    const reviewCriteria = asArray(currentReview?.criteria).filter(isRecord);
    const reviewFindings = asArray(currentReview?.findings).filter(isRecord);
    const passedCriteriaCount = reviewCriteria.filter((c) => c.pass === true).length;

    const isGo = recommendation === "GO to Planning";

    return (
        <article className="design-document studio-explorer">
            <div className="studio-banner">
                <div className="studio-banner-main">
                    <span className={`studio-pill ${isGo ? "emerald" : recommendation === "NO-GO" ? "crimson" : "amber"}`}>
                        {recommendation}
                    </span>
                    <span className="studio-pill neutral">
                        {sectionEntries.length} section{sectionEntries.length === 1 ? "" : "s"}
                    </span>
                    {references.length > 0 && (
                        <span className="studio-pill neutral">
                            {references.length} source ref{references.length === 1 ? "" : "s"} · {facts.length} verified fact{facts.length === 1 ? "" : "s"}
                        </span>
                    )}
                    {reviews.length > 0 && (
                        <span className="studio-pill purple">
                            {reviews.length} review round{reviews.length === 1 ? "" : "s"} ({fixIterations} fix{fixIterations === 1 ? "" : "es"})
                        </span>
                    )}
                    {approvalVerification?.verified === true && (
                        <span className="studio-pill emerald" title={String(approvalVerification.rationale || "")}>
                            Stage 0 Provenance Verified
                        </span>
                    )}
                </div>
                {(reviews.length > 0 || traceability.length > 0 || references.length > 0) && (
                    <div className="studio-subnav" role="tablist" aria-label="Design studio views">
                        <button
                            type="button"
                            className={studioTab === "sections" ? "studio-tab active" : "studio-tab"}
                            onClick={() => setStudioTab("sections")}
                        >
                            Design Specification ({sectionEntries.length})
                        </button>
                        {reviews.length > 0 && (
                            <button
                                type="button"
                                className={studioTab === "arena" ? "studio-tab active" : "studio-tab"}
                                onClick={() => setStudioTab("arena")}
                            >
                                Author vs. Reviewer ({reviews.length})
                            </button>
                        )}
                        {(traceability.length > 0 || references.length > 0) && (
                            <button
                                type="button"
                                className={studioTab === "traceability" ? "studio-tab active" : "studio-tab"}
                                onClick={() => setStudioTab("traceability")}
                            >
                                Traceability &amp; Evidence ({references.length})
                            </button>
                        )}
                        {(assumptions.length > 0 || openDecisions.length > 0 || blockingUnknowns.length > 0 || conflicts.length > 0) && (
                            <button
                                type="button"
                                className={studioTab === "risks" ? "studio-tab active" : "studio-tab"}
                                onClick={() => setStudioTab("risks")}
                            >
                                Decisions &amp; Assumptions ({assumptions.length + openDecisions.length + blockingUnknowns.length + conflicts.length})
                            </button>
                        )}
                    </div>
                )}
            </div>

            {(blockingUnknowns.length > 0 || conflicts.length > 0) && (
                <div className="studio-callout warning">
                    <strong>Attention — Unresolved Conflicts or Blocking Unknowns:</strong>
                    <ul>
                        {blockingUnknowns.map((item, i) => <li key={`bu-${i}`}>[Blocking] {item}</li>)}
                        {conflicts.map((item, i) => <li key={`cf-${i}`}>[Conflict] {item}</li>)}
                    </ul>
                </div>
            )}

            {studioTab === "sections" && (
                <div className="studio-sections-layout">
                    {sectionEntries.length > 3 && (
                        <nav className="studio-toc" aria-label="Design sections">
                            <span className="eyebrow">SECTIONS</span>
                            <button
                                type="button"
                                className={activeSectionFilter === null ? "toc-item active" : "toc-item"}
                                onClick={() => setActiveSectionFilter(null)}
                            >
                                Show all ({sectionEntries.length})
                            </button>
                            {sectionEntries.map(([name]) => {
                                const coveredReqs = traceability
                                    .filter((t) => asArray(t.sectionIds).map(String).includes(name))
                                    .map((t) => String(t.requirementId));
                                return (
                                    <button
                                        type="button"
                                        key={name}
                                        className={activeSectionFilter === name ? "toc-item active" : "toc-item"}
                                        onClick={() => setActiveSectionFilter(activeSectionFilter === name ? null : name)}
                                    >
                                        <span>{formatSectionTitle(name)}</span>
                                        {coveredReqs.length > 0 && <small className="toc-badge">{coveredReqs.join(", ")}</small>}
                                    </button>
                                );
                            })}
                        </nav>
                    )}
                    <div className="studio-sections-body">
                        {sectionEntries
                            .filter(([name]) => !activeSectionFilter || activeSectionFilter === name)
                            .map(([name, content]) => {
                                const coveredReqs = traceability
                                    .filter((t) => asArray(t.sectionIds).map(String).includes(name))
                                    .map((t) => String(t.requirementId));
                                return (
                                    <section key={name} className="studio-doc-section">
                                        <div className="studio-doc-section-header">
                                            <div>
                                                <h3>{name.replace(/([A-Z])/g, " $1")}</h3>
                                                {coveredReqs.length > 0 && (
                                                    <div className="section-req-chips">
                                                        {coveredReqs.map((reqId) => (
                                                            <span key={reqId} className="mono-chip emerald">Covers {reqId}</span>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                            {onAppendFeedback && (
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn"
                                                    onClick={() => onAppendFeedback(`[Design section: ${formatSectionTitle(name)}] `)}
                                                >
                                                    + Comment on section
                                                </button>
                                            )}
                                        </div>
                                        <div className="markdown-prose">
                                            <Markdown
                                                remarkPlugins={[remarkGfm]}
                                                skipHtml
                                                components={{ img: ({ alt }) => <span>[Image: {alt || "not loaded"}]</span> }}
                                            >
                                                {typeof content === "string" ? content : JSON.stringify(content)}
                                            </Markdown>
                                        </div>
                                    </section>
                                );
                            })}
                    </div>
                </div>
            )}

            {studioTab === "arena" && reviews.length > 0 && (
                <div className="arena-container" aria-label="Design Author vs Independent Reviewer Arena">
                    <div className="round-scrubber">
                        <span className="eyebrow">ADVERSARIAL REVIEW ROUNDS</span>
                        <div className="round-buttons">
                            {reviews.map((entry, idx) => {
                                const rev = isRecord(entry.review) ? entry.review : null;
                                const findings = asArray(rev?.findings);
                                const valErrors = asArray(entry.validationErrors);
                                const criteria = asArray(rev?.criteria).filter(isRecord);
                                const passed = rev !== null && findings.length === 0 && valErrors.length === 0 && criteria.every((c) => c.pass === true);
                                return (
                                    <button
                                        type="button"
                                        key={idx}
                                        className={`round-pill ${selectedRoundIdx === idx ? "selected" : ""} ${passed ? "pass" : "changes"}`}
                                        onClick={() => setSelectedRoundIdx(idx)}
                                    >
                                        <strong>Round {Number(entry.round ?? idx) + 1}</strong>
                                        <span>{passed ? "✓ Passed Rubric" : valErrors.length ? `${valErrors.length} Schema Issue(s)` : `${findings.length} Finding(s)`}</span>
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    <div className="arena-split">
                        <div className="arena-pane builder-pane">
                            <div className="arena-pane-header">
                                <span className="studio-pill blue">BUILDER</span>
                                <h4>Design Author — Round {selectedRoundIdx + 1}</h4>
                            </div>
                            <p className="muted">
                                {selectedRoundIdx === 0
                                    ? "Initial architecture and section synthesis grounded in repository evidence."
                                    : `Revision ${selectedRoundIdx} authored to resolve independent reviewer findings from Round ${selectedRoundIdx}.`}
                            </p>
                            <div className="builder-section-cards">
                                {sectionEntries.map(([name, content]) => (
                                    <details key={name} className="compact-section-card">
                                        <summary>
                                            <strong>{formatSectionTitle(name)}</strong>
                                            <span>{typeof content === "string" ? `${content.length.toLocaleString()} chars` : "JSON"}</span>
                                        </summary>
                                        <div className="markdown-prose">
                                            <Markdown remarkPlugins={[remarkGfm]} skipHtml>
                                                {typeof content === "string" ? content : JSON.stringify(content)}
                                            </Markdown>
                                        </div>
                                    </details>
                                ))}
                            </div>
                        </div>

                        <div className="arena-pane reviewer-pane">
                            <div className="arena-pane-header">
                                <span className="studio-pill purple">INDEPENDENT REVIEWER</span>
                                <h4>Rubric &amp; Findings — Round {selectedRoundIdx + 1}</h4>
                                {reviewCriteria.length > 0 && (
                                    <span className={`studio-pill ${passedCriteriaCount === reviewCriteria.length && reviewFindings.length === 0 ? "emerald" : "amber"}`}>
                                        {passedCriteriaCount}/{reviewCriteria.length} Rubric Passed
                                    </span>
                                )}
                            </div>

                            {currentValidationErrors.length > 0 && (
                                <div className="studio-callout warning">
                                    <strong>Structural Validation Errors:</strong>
                                    <ul>{currentValidationErrors.map((err, i) => <li key={i}>{err}</li>)}</ul>
                                </div>
                            )}

                            {reviewCriteria.length > 0 && (
                                <div className="rubric-grid">
                                    {reviewCriteria.map((criterion) => {
                                        const id = String(criterion.id);
                                        const pass = criterion.pass === true;
                                        return (
                                            <div key={id} className={`rubric-chip ${pass ? "pass" : "fail"}`}>
                                                <span>{pass ? "✓" : "✕"}</span>
                                                <strong>{formatSectionTitle(id)}</strong>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}

                            {reviewFindings.length > 0 ? (
                                <div className="findings-list">
                                    <h5>Independent Reviewer Findings ({reviewFindings.length})</h5>
                                    {reviewFindings.map((finding) => {
                                        const id = String(finding.id);
                                        const severity = String(finding.severity || "major");
                                        const resolvedLater = selectedRoundIdx < reviews.length - 1;
                                        return (
                                            <div key={id} className={`finding-card severity-${severity}`}>
                                                <div className="finding-header">
                                                    <span className={`severity-badge ${severity}`}>{severity.toUpperCase()}</span>
                                                    <code className="mono-chip">{id}</code>
                                                    <span className="mono-chip">{String(finding.rubricId || "")}</span>
                                                    <code className="location-chip">{String(finding.location || "")}</code>
                                                    {resolvedLater && <span className="studio-pill emerald">Addressed in next round ✓</span>}
                                                </div>
                                                <p className="finding-evidence"><strong>Evidence:</strong> {String(finding.evidence || "")}</p>
                                                <p className="finding-fix"><strong>Required Change:</strong> {String(finding.requiredChange || "")}</p>
                                                {onAppendFeedback && (
                                                    <button
                                                        type="button"
                                                        className="ghost-chip-btn"
                                                        onClick={() => onAppendFeedback(`[Finding ${id} (${finding.location})]: ${finding.requiredChange} `)}
                                                    >
                                                        + Copy into human revision feedback
                                                    </button>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : (
                                currentValidationErrors.length === 0 && (
                                    <div className="studio-callout success">
                                        <strong>Zero findings in Round {selectedRoundIdx + 1}.</strong> All independent rubric checks passed.
                                    </div>
                                )
                            )}
                        </div>
                    </div>
                </div>
            )}

            {studioTab === "traceability" && (
                <div className="traceability-studio">
                    {traceability.length > 0 && (
                        <section className="traceability-matrix-section">
                            <div className="studio-section-head">
                                <h4>Requirement-to-Evidence Traceability Matrix</h4>
                                <p className="muted">Click any requirement row to filter and highlight its linked design sections and source code citations below.</p>
                            </div>
                            <div className="plan-table">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Requirement</th>
                                            <th>Design Sections</th>
                                            <th>Source Citations</th>
                                            <th>Verification Method</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {traceability.map((row) => {
                                            const reqId = String(row.requirementId);
                                            const reqObj = requirements.find((r) => String(r.id) === reqId);
                                            const isSelected = selectedReqId === reqId;
                                            return (
                                                <tr
                                                    key={reqId}
                                                    className={isSelected ? "interactive-row selected" : "interactive-row"}
                                                    onClick={() => setSelectedReqId(isSelected ? null : reqId)}
                                                >
                                                    <td>
                                                        <span className="mono-chip emerald">{reqId}</span>
                                                        {reqObj && <p className="trace-req-text">{String(reqObj.text)}</p>}
                                                    </td>
                                                    <td>
                                                        <div className="chip-wrap">
                                                            {asArray(row.sectionIds).map((sec) => (
                                                                <span key={String(sec)} className="mono-chip">{formatSectionTitle(String(sec))}</span>
                                                            ))}
                                                        </div>
                                                    </td>
                                                    <td>
                                                        <div className="chip-wrap">
                                                            {asArray(row.evidenceIds).map((ev) => (
                                                                <span key={String(ev)} className="mono-chip blue">{String(ev)}</span>
                                                            ))}
                                                        </div>
                                                    </td>
                                                    <td>{String(row.verification)}</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </section>
                    )}

                    {references.length > 0 && (
                        <section className="evidence-ledger-section">
                            <div className="studio-section-head">
                                <h4>Verified Source Evidence Ledger ({references.length} references)</h4>
                                {selectedReqId && (
                                    <button type="button" className="ghost-chip-btn" onClick={() => setSelectedReqId(null)}>
                                        Clear filter ({selectedReqId})
                                    </button>
                                )}
                            </div>
                            <div className="evidence-cards-grid">
                                {references
                                    .filter((ref) => !selectedReqId || highlightedEvidence.has(String(ref.id)))
                                    .map((ref) => {
                                        const refId = String(ref.id);
                                        const linkedFacts = facts.filter((f) => asArray(f.referenceIds).map(String).includes(refId));
                                        return (
                                            <div key={refId} className={`evidence-card ${highlightedEvidence.has(refId) ? "highlighted" : ""}`}>
                                                <div className="evidence-card-head">
                                                    <span className="mono-chip blue">{refId}</span>
                                                    <span className="mono-chip">{String(ref.repository)}</span>
                                                    <code className="commit-pill" title={String(ref.revision)}>{String(ref.revision).slice(0, 10)}</code>
                                                </div>
                                                <code className="evidence-path">{String(ref.path)}</code>
                                                <div className="evidence-locator">Locator: <code>{String(ref.locator)}</code></div>
                                                {linkedFacts.length > 0 && (
                                                    <ul className="evidence-facts">
                                                        {linkedFacts.map((fact, idx) => (
                                                            <li key={idx}>{String(fact.statement)}</li>
                                                        ))}
                                                    </ul>
                                                )}
                                            </div>
                                        );
                                    })}
                            </div>
                        </section>
                    )}
                </div>
            )}

            {studioTab === "risks" && (
                <div className="risks-studio-grid">
                    <div className="studio-info-card">
                        <h4>Open Decisions ({openDecisions.length})</h4>
                        {openDecisions.length ? (
                            <ul>{openDecisions.map((item, i) => <li key={i}>{item}</li>)}</ul>
                        ) : <p className="muted">No open decisions recorded.</p>}
                    </div>
                    <div className="studio-info-card">
                        <h4>Engineering Assumptions ({assumptions.length})</h4>
                        {assumptions.length ? (
                            <ul>{assumptions.map((item, i) => <li key={i}>{item}</li>)}</ul>
                        ) : <p className="muted">No unverified assumptions recorded.</p>}
                    </div>
                    <div className="studio-info-card">
                        <h4>Blocking Unknowns &amp; Conflicts ({blockingUnknowns.length + conflicts.length})</h4>
                        {blockingUnknowns.length + conflicts.length > 0 ? (
                            <ul>
                                {blockingUnknowns.map((item, i) => <li key={`b-${i}`}>{item}</li>)}
                                {conflicts.map((item, i) => <li key={`c-${i}`}>{item}</li>)}
                            </ul>
                        ) : <p className="muted">Zero blocking unknowns or source conflicts.</p>}
                    </div>
                </div>
            )}

            <details>
                <summary>Full evidence, traceability, and review packet</summary>
                <pre>{JSON.stringify(body, null, 2)}</pre>
            </details>
        </article>
    );
}

export function PlanStudioExplorer({
    artifact,
    feature,
    onAppendFeedback,
    onSelectItemForOps,
}: {
    artifact: Artifact;
    feature?: FactoryFeature;
    onAppendFeedback?: (snippet: string) => void;
    onSelectItemForOps?: (itemId: string) => void;
}) {
    const body = isRecord(artifact.content) ? artifact.content : {};
    const items = asArray(body.items).filter(isRecord);
    const reviewHistory = asArray(body.reviewHistory).filter(isRecord);
    const [selectedItemId, setSelectedItemId] = useState<string | null>(items[0] ? String(items[0].id) : null);
    const [viewMode, setViewMode] = useState<"dag" | "reviews">("dag");

    // Compute dependency waves for visual DAG
    const waves: Record<string, Json>[][] = [];
    const placed = new Set<string>();
    const remaining = [...items];
    let guard = 0;
    while (remaining.length > 0 && guard++ < 20) {
        const wave = remaining.filter((item) => {
            const deps = asArray(item.dependsOn).map(String);
            return deps.every((d) => placed.has(d) || !items.some((cand) => String(cand.id) === d));
        });
        if (wave.length === 0) {
            waves.push([...remaining]);
            break;
        }
        waves.push(wave);
        for (const w of wave) {
            placed.add(String(w.id));
            const idx = remaining.indexOf(w);
            if (idx >= 0) remaining.splice(idx, 1);
        }
    }

    const selectedItem = items.find((item) => String(item.id) === selectedItemId) || items[0] || null;
    const monitorArtifact = feature?.factory.artifacts.find((a) => a.id === feature.currentArtifacts.monitor);
    const monitorContent = isRecord(monitorArtifact?.content) ? monitorArtifact.content : null;
    const monitorItems = asArray(monitorContent?.items).filter(isRecord);
    const monitorPrs = asArray(monitorContent?.prs).filter(isRecord);

    const workItemStatusFor = (id: string) => {
        const fromMonitor = monitorItems.find((w) => String(w.key) === id);
        if (fromMonitor) return { adoId: Number(fromMonitor.adoId), status: String(fromMonitor.status), url: String(fromMonitor.url) };
        const op = feature?.factory.operations.find((o) => o.itemId === id && o.kind === "work_item" && o.status === "completed");
        if (op && isRecord(op.receipt) && typeof op.receipt.adoId === "number") {
            return { adoId: op.receipt.adoId, status: String(op.receipt.status || "Created"), url: String(op.receipt.url || "") };
        }
        return null;
    };

    const prFor = (id: string) => {
        const fromMonitor = monitorPrs.find((p) => String(p.key) === id);
        if (fromMonitor) return { number: Number(fromMonitor.number), status: String(fromMonitor.status), checks: String(fromMonitor.checks), url: String(fromMonitor.url) };
        const binding = feature?.factory.prBindings.find((b) => b.itemId === id);
        if (binding) return { number: binding.number, status: "bound", checks: "unknown", url: "" };
        return null;
    };

    return (
        <div className="plan-document studio-explorer">
            <div className="studio-banner">
                <div className="studio-banner-main">
                    <span className="studio-pill emerald">{items.length} Planned PBI{items.length === 1 ? "" : "s"}</span>
                    <span className="studio-pill neutral">{waves.length} Execution Wave{waves.length === 1 ? "" : "s"}</span>
                    {reviewHistory.length > 0 && (
                        <span className="studio-pill purple">{reviewHistory.length} Planner vs. Reviewer Round{reviewHistory.length === 1 ? "" : "s"}</span>
                    )}
                </div>
                {reviewHistory.length > 0 && (
                    <div className="studio-subnav">
                        <button type="button" className={viewMode === "dag" ? "studio-tab active" : "studio-tab"} onClick={() => setViewMode("dag")}>
                            Architecture DAG &amp; Backlog Matrix
                        </button>
                        <button type="button" className={viewMode === "reviews" ? "studio-tab active" : "studio-tab"} onClick={() => setViewMode("reviews")}>
                            Planner vs. Reviewer History ({reviewHistory.length})
                        </button>
                    </div>
                )}
            </div>

            <p className="plan-summary-lead">{typeof body.summary === "string" ? body.summary : ""}</p>

            {viewMode === "dag" && waves.length > 0 && (
                <div className="dag-studio-grid">
                    <div className="dag-waves-canvas" aria-label="Interactive PBI Dependency Waves">
                        <div className="studio-section-head">
                            <h4>Interactive Execution Waves &amp; Dependency DAG</h4>
                            <span className="muted">Click any PBI node to inspect its full specification, files, tests, and live delivery links.</span>
                        </div>
                        <div className="waves-track">
                            {waves.map((wave, waveIndex) => (
                                <div key={waveIndex} className="dag-wave-column">
                                    <div className="wave-header">
                                        <span className="eyebrow">WAVE {waveIndex + 1}</span>
                                        <small>{waveIndex === 0 ? "Ready first (Independent)" : `Depends on Wave ${waveIndex}`}</small>
                                    </div>
                                    <div className="wave-nodes">
                                        {wave.map((item) => {
                                            const id = String(item.id);
                                            const deps = asArray(item.dependsOn).map(String);
                                            const reqs = asArray(item.requirementIds).map(String);
                                            const wi = workItemStatusFor(id);
                                            const pr = prFor(id);
                                            const isSelected = String(selectedItem?.id) === id;
                                            return (
                                                <button
                                                    type="button"
                                                    key={id}
                                                    className={`dag-node-card ${isSelected ? "selected" : ""}`}
                                                    onClick={() => setSelectedItemId(id)}
                                                >
                                                    <div className="dag-node-top">
                                                        <span className="mono-chip emerald">{id}</span>
                                                        <span className="mono-chip blue">{String(item.repository)}</span>
                                                        {wi && <span className="mono-chip">AB#{wi.adoId}</span>}
                                                        {pr && <span className="mono-chip purple">PR #{pr.number}</span>}
                                                    </div>
                                                    <strong className="dag-node-title">{String(item.title)}</strong>
                                                    <div className="dag-node-meta">
                                                        <span>Reqs: {reqs.join(", ") || "—"}</span>
                                                        <span>{deps.length ? `← Needs ${deps.join(", ")}` : "← Start (No deps)"}</span>
                                                    </div>
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {selectedItem && (
                        <aside className="pbi-inspector-card" aria-label="Selected PBI Inspector">
                            <div className="pbi-inspector-head">
                                <div>
                                    <span className="mono-chip emerald">{String(selectedItem.id)}</span>
                                    <span className="mono-chip blue">{String(selectedItem.repository)}</span>
                                    <h4>{String(selectedItem.title)}</h4>
                                </div>
                                <div className="pbi-inspector-actions">
                                    {onAppendFeedback && (
                                        <button
                                            type="button"
                                            className="ghost-chip-btn"
                                            onClick={() => onAppendFeedback(`[PBI ${String(selectedItem.id)} (${String(selectedItem.repository)})]: `)}
                                        >
                                            + Comment on PBI
                                        </button>
                                    )}
                                    {onSelectItemForOps && (
                                        <button
                                            type="button"
                                            className="ghost-chip-btn"
                                            onClick={() => onSelectItemForOps(String(selectedItem.id))}
                                        >
                                            Open in PR Ops →
                                        </button>
                                    )}
                                </div>
                            </div>

                            {(() => {
                                const wi = workItemStatusFor(String(selectedItem.id));
                                const pr = prFor(String(selectedItem.id));
                                if (!wi && !pr) return null;
                                return (
                                    <div className="pbi-live-links">
                                        {wi && (
                                            <a href={wi.url || "#"} target="_blank" rel="noreferrer" className="studio-pill neutral">
                                                ADO Work Item #{wi.adoId} ({wi.status})
                                            </a>
                                        )}
                                        {pr && (
                                            <a href={pr.url || "#"} target="_blank" rel="noreferrer" className="studio-pill purple">
                                                PR #{pr.number} ({pr.status} · checks: {pr.checks})
                                            </a>
                                        )}
                                    </div>
                                );
                            })()}

                            <div className="pbi-inspector-section">
                                <h5>Self-Contained Specification</h5>
                                <div className="markdown-prose">
                                    <Markdown remarkPlugins={[remarkGfm]} skipHtml>
                                        {String(selectedItem.description || "")}
                                    </Markdown>
                                </div>
                            </div>

                            <div className="pbi-inspector-section">
                                <h5>Acceptance Criteria ({asArray(selectedItem.acceptanceCriteria).length})</h5>
                                <ul>
                                    {asArray(selectedItem.acceptanceCriteria).map((c, idx) => (
                                        <li key={idx}>{String(c)}</li>
                                    ))}
                                </ul>
                            </div>

                            <div className="pbi-inspector-split">
                                <div>
                                    <h5>Target Files ({asArray(selectedItem.files).length})</h5>
                                    <div className="chip-wrap">
                                        {asArray(selectedItem.files).length ? asArray(selectedItem.files).map((f, i) => (
                                            <code key={i} className="file-path-chip">{String(f)}</code>
                                        )) : <span className="muted">Repository-scoped</span>}
                                    </div>
                                </div>
                                <div>
                                    <h5>Required Tests ({asArray(selectedItem.tests).length})</h5>
                                    <ul>
                                        {asArray(selectedItem.tests).map((t, i) => (
                                            <li key={i}><code>{String(t)}</code></li>
                                        ))}
                                    </ul>
                                </div>
                            </div>
                        </aside>
                    )}
                </div>
            )}

            {viewMode === "reviews" && reviewHistory.length > 0 && (
                <div className="arena-container">
                    <h4>Planner vs. Independent Plan Reviewer Rounds</h4>
                    <div className="findings-list">
                        {reviewHistory.map((entry, idx) => {
                            const rev = isRecord(entry.review) ? entry.review : null;
                            const pass = rev?.pass === true;
                            const findings = asArray(rev?.findings).map(String);
                            const validationError = typeof entry.validationError === "string" ? entry.validationError : null;
                            return (
                                <div key={idx} className={`finding-card ${pass ? "severity-minor" : "severity-major"}`}>
                                    <div className="finding-header">
                                        <span className={`studio-pill ${pass ? "emerald" : "amber"}`}>
                                            Attempt {Number(entry.attempt ?? idx) + 1}: {pass ? "Approved by Independent Reviewer ✓" : "Revision Requested"}
                                        </span>
                                    </div>
                                    {validationError && <p className="finding-evidence"><strong>Schema / DAG Validation:</strong> {validationError}</p>}
                                    {findings.length > 0 && (
                                        <ul>
                                            {findings.map((f, fIdx) => <li key={fIdx}>{f}</li>)}
                                        </ul>
                                    )}
                                    {pass && findings.length === 0 && <p className="muted">All requirements, repository routes, dependencies, and test specifications verified.</p>}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            <div className="plan-table">
                <table>
                    <thead>
                        <tr>
                            <th>PBI</th>
                            <th>Repository</th>
                            <th>Dependencies</th>
                            <th>Acceptance criteria</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((raw, index) => (
                            <tr
                                key={String(raw.id || index)}
                                className={String(selectedItem?.id) === String(raw.id) ? "interactive-row selected" : "interactive-row"}
                                onClick={() => setSelectedItemId(String(raw.id))}
                            >
                                <td>
                                    <strong>{String(raw.id)}</strong>
                                    <br />
                                    {String(raw.title)}
                                </td>
                                <td><span className="mono-chip blue">{String(raw.repository)}</span></td>
                                <td>
                                    {Array.isArray(raw.dependsOn) && raw.dependsOn.length
                                        ? raw.dependsOn.map(String).join(", ")
                                        : "Independent"}
                                </td>
                                <td>
                                    <ul>
                                        {Array.isArray(raw.acceptanceCriteria) &&
                                            raw.acceptanceCriteria.map((criterion, criterionIndex) => (
                                                <li key={criterionIndex}>{String(criterion)}</li>
                                            ))}
                                    </ul>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <section aria-label="Dependency graph" className="dependency-graph">
                <h3>Dependency graph</h3>
                {items.map((raw, index) => (
                    <p key={index}>
                        <code>
                            {Array.isArray(raw.dependsOn) && raw.dependsOn.length
                                ? `${raw.dependsOn.map(String).join(" + ")} -> `
                                : "Start -> "}
                            {String(raw.id)}
                        </code>
                    </p>
                ))}
            </section>

            <details>
                <summary>Complete PBI specifications, files, tests, and traceability</summary>
                <pre>{JSON.stringify(body, null, 2)}</pre>
            </details>
        </div>
    );
}

export function CodeArenaExplorer({ artifact }: { artifact: Artifact }) {
    const body = isRecord(artifact.content) ? artifact.content : {};
    const items: Record<string, Json>[] = artifact.kind === "implementation_attempt"
        ? [body]
        : asArray(body.items).filter(isRecord);

    const [selectedItemIdx, setSelectedItemIdx] = useState(0);
    const activeItem = items[selectedItemIdx] || items[0] || null;
    const reviewHistory = asArray(activeItem?.reviewHistory).filter(isRecord);
    const [selectedRoundIdx, setSelectedRoundIdx] = useState(Math.max(0, reviewHistory.length - 1));
    const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);

    const currentRound = reviewHistory[selectedRoundIdx] || reviewHistory.at(-1) || null;
    const review = isRecord(currentRound?.review) ? currentRound.review : null;
    const criteria = asArray(review?.criteria).filter(isRecord);
    const findings = asArray(review?.findings).filter(isRecord);
    const receipt = isRecord(activeItem?.receipt) ? activeItem.receipt : null;
    const pr = isRecord(receipt?.pr) ? receipt.pr : null;
    const committedFiles = asArray(receipt?.files).map(String);
    const roundFiles = asArray(currentRound?.files).map(String);
    const testsUpdated = asArray(currentRound?.testsAddedOrUpdated).map(String);

    return (
        <div className="code-arena-explorer studio-explorer" aria-label="Code Builder vs Independent Reviewer Arena">
            <div className="studio-banner">
                <div className="studio-banner-main">
                    <span className="studio-pill blue">
                        MODE: {String(body.mode || activeItem?.mode || "local").toUpperCase()}
                    </span>
                    <span className="studio-pill neutral">
                        {items.length} PBI Implementation{items.length === 1 ? "" : "s"}
                    </span>
                    {typeof activeItem?.status === "string" && (
                        <span className={`studio-pill ${activeItem.status === "published" || activeItem.status === "review_passed" ? "emerald" : "amber"}`}>
                            {activeItem.status.replaceAll("_", " ")}
                        </span>
                    )}
                    {typeof activeItem?.branch === "string" && (
                        <code className="commit-pill">branch: {activeItem.branch}</code>
                    )}
                    {pr && typeof pr.url === "string" && (
                        <a href={pr.url} target="_blank" rel="noreferrer" className="studio-pill purple">
                            PR #{String(pr.number)} ({String(pr.status)})
                        </a>
                    )}
                </div>
            </div>

            {items.length > 1 && (
                <div className="studio-subnav" role="tablist" aria-label="Implemented PBIs">
                    {items.map((item, idx) => (
                        <button
                            type="button"
                            key={String(item.itemId || idx)}
                            className={selectedItemIdx === idx ? "studio-tab active" : "studio-tab"}
                            onClick={() => {
                                setSelectedItemIdx(idx);
                                const hist = asArray(item.reviewHistory);
                                setSelectedRoundIdx(Math.max(0, hist.length - 1));
                                setSelectedFilePath(null);
                            }}
                        >
                            {String(item.itemId || `Item ${idx + 1}`)} ({String(item.status || item.recommendation || "run")})
                        </button>
                    ))}
                </div>
            )}

            {reviewHistory.length > 0 && (
                <div className="round-scrubber">
                    <span className="eyebrow">BUILDER VS. INDEPENDENT CODE REVIEWER ROUNDS</span>
                    <div className="round-buttons">
                        {reviewHistory.map((roundEntry, idx) => {
                            const rev = isRecord(roundEntry.review) ? roundEntry.review : null;
                            const pass = rev?.pass === true && asArray(rev?.findings).length === 0;
                            const valErr = typeof roundEntry.validationError === "string";
                            return (
                                <button
                                    type="button"
                                    key={idx}
                                    className={`round-pill ${selectedRoundIdx === idx ? "selected" : ""} ${pass ? "pass" : "changes"}`}
                                    onClick={() => setSelectedRoundIdx(idx)}
                                >
                                    <strong>Round {Number(roundEntry.round ?? idx + 1)}</strong>
                                    <span>{pass ? "✓ Approved" : valErr ? "Schema Error" : `${asArray(rev?.findings).length} Finding(s)`}</span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}

            <div className="arena-split">
                <div className="arena-pane builder-pane">
                    <div className="arena-pane-header">
                        <span className="studio-pill blue">STAGE 3 CODER</span>
                        <h4>Builder Output — {String(activeItem?.itemId || "PBI")}</h4>
                    </div>
                    {typeof currentRound?.builderSummary === "string" && (
                        <p className="builder-summary-box">{currentRound.builderSummary}</p>
                    )}
                    {typeof currentRound?.validationError === "string" && (
                        <div className="studio-callout warning">
                            <strong>Builder Validation Error:</strong> {currentRound.validationError}
                        </div>
                    )}
                    <div className="pbi-inspector-split">
                        <div>
                            <h5>Modified / Created Files ({roundFiles.length || committedFiles.length})</h5>
                            <div className="chip-wrap">
                                {(roundFiles.length ? roundFiles : committedFiles).map((file) => (
                                    <button
                                        type="button"
                                        key={file}
                                        className={`file-path-chip ${selectedFilePath === file ? "active" : ""}`}
                                        onClick={() => setSelectedFilePath(selectedFilePath === file ? null : file)}
                                    >
                                        📄 {file}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div>
                            <h5>Tests Added or Updated ({testsUpdated.length})</h5>
                            <div className="chip-wrap">
                                {testsUpdated.length ? testsUpdated.map((t) => (
                                    <code key={t} className="mono-chip emerald">🧪 {t}</code>
                                )) : <span className="muted">Tracked in acceptance criteria</span>}
                            </div>
                        </div>
                    </div>
                </div>

                <div className="arena-pane reviewer-pane">
                    <div className="arena-pane-header">
                        <span className="studio-pill purple">INDEPENDENT CODE REVIEWER</span>
                        <h4>Review Verdict &amp; Criteria Scorecard</h4>
                        {review && (
                            <span className={`studio-pill ${review.pass === true ? "emerald" : "amber"}`}>
                                {review.pass === true ? "PASSED ✓" : "CHANGES REQUESTED"}
                            </span>
                        )}
                    </div>
                    {typeof review?.summary === "string" && <p className="muted">{review.summary}</p>}

                    {criteria.length > 0 && (
                        <div className="criteria-scorecard">
                            <h5>Acceptance Criteria Verification ({criteria.filter((c) => c.status === "addressed").length}/{criteria.length})</h5>
                            {criteria.map((c, idx) => {
                                const status = String(c.status || "unknown");
                                return (
                                    <div key={idx} className={`criterion-check-row status-${status}`}>
                                        <span className={`studio-pill ${status === "addressed" ? "emerald" : status === "partial" ? "amber" : "crimson"}`}>
                                            {status.toUpperCase()}
                                        </span>
                                        <div>
                                            <strong>{String(c.criterion)}</strong>
                                            <p className="muted">{String(c.evidence)}</p>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {findings.length > 0 && (
                        <div className="findings-list">
                            <h5>Code Review Findings ({findings.length})</h5>
                            {findings
                                .filter((f) => !selectedFilePath || String(f.file) === selectedFilePath)
                                .map((finding) => {
                                    const severity = String(finding.severity || "major");
                                    return (
                                        <div key={String(finding.id)} className={`finding-card severity-${severity}`}>
                                            <div className="finding-header">
                                                <span className={`severity-badge ${severity}`}>{severity.toUpperCase()}</span>
                                                <code className="mono-chip">{String(finding.id)}</code>
                                                <span className="mono-chip blue">{String(finding.category || "correctness")}</span>
                                                <code className="location-chip">{String(finding.file)}:{String(finding.location)}</code>
                                            </div>
                                            <p className="finding-evidence"><strong>Evidence:</strong> {String(finding.evidence)}</p>
                                            <p className="finding-fix"><strong>Required Fix:</strong> {String(finding.requiredChange)}</p>
                                        </div>
                                    );
                                })}
                        </div>
                    )}
                </div>
            </div>

            <details>
                <summary>Full implementation &amp; review history JSON</summary>
                <pre>{JSON.stringify(body, null, 2)}</pre>
            </details>
        </div>
    );
}

export function DeliveryCommandCenter({
    artifact,
    feature,
    onPrefillWaiver,
    onPrefillPrFeedback,
}: {
    artifact: Artifact;
    feature: FactoryFeature;
    onPrefillWaiver?: (itemId: string, criterion: string) => void;
    onPrefillPrFeedback?: (itemId: string, feedbackBody: string) => void;
}) {
    const body = isRecord(artifact.content) ? artifact.content : {};
    const items = asArray(body.items).filter(isRecord);
    const prs = asArray(body.prs).filter(isRecord);
    const validation = asArray(body.validation).filter(isRecord);
    const reviews = asArray(body.reviews).filter(isRecord);
    const errors = asArray(body.errors).map(String);
    const delivered = body.delivered === true;
    const addressedCount = validation.filter((v) => v.status === "addressed").length;

    return (
        <div className="delivery-command-center studio-explorer" aria-label="Delivery and PR Command Center">
            <div className="studio-banner">
                <div className="studio-banner-main">
                    <span className={`studio-pill ${delivered ? "emerald" : "amber"}`}>
                        {delivered ? "✓ All Delivery Checks Passed" : "In Delivery / Verification"}
                    </span>
                    <span className="studio-pill neutral">{items.length} ADO Work Item{items.length === 1 ? "" : "s"}</span>
                    <span className="studio-pill purple">{prs.length} Pull Request{prs.length === 1 ? "" : "s"}</span>
                    <span className={`studio-pill ${addressedCount === validation.length && validation.length > 0 ? "emerald" : "amber"}`}>
                        {addressedCount}/{validation.length} Criteria Verified
                    </span>
                </div>
                {typeof body.observedAt === "string" && (
                    <span className="muted">Observed {new Date(body.observedAt).toLocaleString()}</span>
                )}
            </div>

            {errors.length > 0 && (
                <div className="studio-callout warning">
                    <strong>Delivery Observation Alerts:</strong>
                    <ul>{errors.map((err, i) => <li key={i}>{err}</li>)}</ul>
                </div>
            )}

            <div className="delivery-cards-grid">
                {items.map((item) => {
                    const key = String(item.key);
                    const pr = prs.find((p) => String(p.key) === key);
                    const itemValidation = validation.filter((v) => String(v.itemId) === key);
                    const review = reviews.find((r) => String(r.itemId) === key);
                    return (
                        <div key={key} className="delivery-pbi-card">
                            <div className="delivery-pbi-head">
                                <div>
                                    <span className="mono-chip emerald">{key}</span>
                                    <span className="mono-chip blue">{String(item.repository)}</span>
                                    <a href={String(item.url)} target="_blank" rel="noreferrer" className="mono-chip">
                                        AB#{String(item.adoId)} · {String(item.status)} ↗
                                    </a>
                                </div>
                                {pr ? (
                                    <a href={String(pr.url)} target="_blank" rel="noreferrer" className={`studio-pill ${pr.status === "merged" ? "emerald" : "purple"}`}>
                                        PR #{String(pr.number)}: {String(pr.status).toUpperCase()} · CI: {String(pr.checks).toUpperCase()} ↗
                                    </a>
                                ) : (
                                    <span className="studio-pill amber">Awaiting PR</span>
                                )}
                            </div>
                            <h4>{String(item.title)}</h4>

                            {review && (
                                <div className="delivery-review-box">
                                    <div className="finding-header">
                                        <span className={`studio-pill ${review.pass === true ? "emerald" : "amber"}`}>
                                            AI Review Round {String(review.round)}/{String(review.maxRounds)}: {String(review.status).replaceAll("_", " ")}
                                        </span>
                                        <code className="commit-pill">head: {String(review.head).slice(0, 8)}</code>
                                        {typeof review.suggestedFeedback === "string" && onPrefillPrFeedback && (
                                            <button
                                                type="button"
                                                className="ghost-chip-btn"
                                                onClick={() => onPrefillPrFeedback(key, String(review.suggestedFeedback))}
                                            >
                                                Load into PR Feedback Composer →
                                            </button>
                                        )}
                                    </div>
                                </div>
                            )}

                            {itemValidation.length > 0 && (
                                <div className="criteria-scorecard">
                                    {itemValidation.map((v, i) => {
                                        const status = String(v.status);
                                        const criterionText = String(v.criterion);
                                        const waived = feature.factory.waivers.some(
                                            (w) => w.monitorId === artifact.id && w.itemId === key && w.criterion === criterionText,
                                        );
                                        return (
                                            <div key={i} className={`criterion-check-row status-${status}`}>
                                                <span className={`studio-pill ${status === "addressed" || waived ? "emerald" : status === "partial" ? "amber" : "crimson"}`}>
                                                    {waived ? "WAIVED" : status.toUpperCase()}
                                                </span>
                                                <div className="criterion-check-body">
                                                    <strong>{criterionText}</strong>
                                                    <p className="muted">{String(v.evidence)}</p>
                                                </div>
                                                {status !== "addressed" && !waived && onPrefillWaiver && (
                                                    <button
                                                        type="button"
                                                        className="ghost-chip-btn"
                                                        onClick={() => onPrefillWaiver(key, criterionText)}
                                                    >
                                                        Waive gap…
                                                    </button>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            <details>
                <summary>Full monitor report JSON</summary>
                <pre>{JSON.stringify(body, null, 2)}</pre>
            </details>
        </div>
    );
}

export function SubagentFlightRecorder({
    feature,
    selectedExecutionId,
    onSelectExecution,
}: {
    feature: FactoryFeature;
    selectedExecutionId?: string;
    onSelectExecution?: (id: string) => void;
}) {
    const executions = [...feature.factory.executions].reverse();
    const activeExec = executions.find((e) => e.id === selectedExecutionId) || executions[0] || null;
    const [traceFilter, setTraceFilter] = useState<"all" | "agent_turn" | "tool_call">("all");
    const [inspectedTrace, setInspectedTrace] = useState<ExecutionTraceEntry | null>(null);

    if (!activeExec) {
        return (
            <div className="flight-recorder-empty">
                <span className="eyebrow">FLIGHT RECORDER IDLE</span>
                <p className="muted">
                    No subagent workflows have run yet. When you start Intake, Design, Planning, Coding, or Monitoring, live subagent swimlanes, scoped repository reads/searches, and structured transcripts appear here.
                </p>
            </div>
        );
    }

    const trace = activeExec.trace || [];
    const filteredTrace = trace.filter((entry) => traceFilter === "all" || entry.kind === traceFilter);
    const usageAgents = activeExec.usage?.agents || [];
    const turnEntries = trace.filter((t) => t.kind === "agent_turn");
    const toolEntries = trace.filter((t) => t.kind === "tool_call");

    return (
        <div className="flight-recorder" aria-label="Subagent Flight Recorder">
            {executions.length > 1 && (
                <div className="flight-exec-selector">
                    <label htmlFor="flight-exec-select" className="eyebrow">SELECT EXECUTION RUN</label>
                    <select
                        id="flight-exec-select"
                        value={activeExec.id}
                        onChange={(e) => {
                            onSelectExecution?.(e.target.value);
                            setInspectedTrace(null);
                        }}
                    >
                        {executions.map((exec) => (
                            <option key={exec.id} value={exec.id}>
                                {exec.kind.toUpperCase()} — {exec.status} ({exec.phase}) · {new Date(exec.updatedAt).toLocaleTimeString()}
                            </option>
                        ))}
                    </select>
                </div>
            )}

            <div className="flight-telemetry-bar">
                <div className="telemetry-stat">
                    <span>STAGE / STATUS</span>
                    <strong>{activeExec.kind.toUpperCase()} · {activeExec.status.toUpperCase()}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>ACTIVE TIME</span>
                    <strong>{activeExec.usage ? `${(activeExec.usage.activeMs / 1000).toFixed(1)}s` : "—"}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>SUBAGENTS</span>
                    <strong>{activeExec.usage?.subagents ?? turnEntries.length}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>SCOPED READS</span>
                    <strong>{toolEntries.length}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>NANO-AIU</span>
                    <strong>{activeExec.usage ? activeExec.usage.nanoAiu.toLocaleString() : "—"}</strong>
                </div>
            </div>

            {(usageAgents.length > 0 || turnEntries.length > 0) && (
                <div className="swimlanes-section">
                    <span className="eyebrow">SUBAGENT SWIMLANES &amp; SPECIALIST ROLES</span>
                    <div className="swimlane-cards">
                        {(usageAgents.length > 0
                            ? usageAgents.map((ag, idx) => {
                                const info = roleDisplayName(undefined, ag.label);
                                const matchingTurn = turnEntries.find((t) => t.label === ag.label);
                                return {
                                    key: `${ag.label}-${idx}`,
                                    badge: info.badge,
                                    title: info.title,
                                    label: ag.label,
                                    status: ag.status,
                                    model: ag.resolvedModel || ag.requestedModel || "Runtime default",
                                    duration: `${(ag.activeMs / 1000).toFixed(1)}s`,
                                    guides: info.guideKey ? workerGuidance[info.guideKey] : [],
                                    turn: matchingTurn || null,
                                };
                            })
                            : turnEntries.map((turn) => {
                                const info = roleDisplayName(turn.role, turn.label);
                                return {
                                    key: turn.id,
                                    badge: info.badge,
                                    title: info.title,
                                    label: turn.label || turn.role || "worker",
                                    status: turn.status,
                                    model: "Scoped session model",
                                    duration: turn.durationMs ? `${(turn.durationMs / 1000).toFixed(1)}s` : "running",
                                    guides: info.guideKey ? workerGuidance[info.guideKey] : [],
                                    turn,
                                };
                            })
                        ).map((lane) => (
                            <div
                                key={lane.key}
                                className={`swimlane-card status-${lane.status} ${inspectedTrace && lane.turn?.id === inspectedTrace.id ? "selected" : ""}`}
                                onClick={() => lane.turn && setInspectedTrace(lane.turn)}
                            >
                                <div className="swimlane-top">
                                    <span className={`studio-pill ${lane.badge === "REVIEWER" ? "purple" : lane.badge === "BUILDER" ? "blue" : "emerald"}`}>
                                        {lane.badge}
                                    </span>
                                    <span className="swimlane-duration">{lane.duration}</span>
                                </div>
                                <strong>{lane.title}</strong>
                                <code className="swimlane-label">{lane.label}</code>
                                <div className="swimlane-meta">
                                    <span className="mono-chip">{lane.model}</span>
                                    <span className={`status-pill-mini ${lane.status}`}>{lane.status}</span>
                                </div>
                                {lane.guides.length > 0 && (
                                    <div className="swimlane-guides" title={lane.guides.join("\n")}>
                                        📚 {lane.guides.length} domain guide{lane.guides.length === 1 ? "" : "s"}
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}

            <div className="trace-stream-section">
                <div className="trace-stream-header">
                    <span className="eyebrow">LIVE TOOL CALLS &amp; TURN TRANSCRIPTS ({filteredTrace.length})</span>
                    <div className="trace-filter-pills">
                        <button type="button" className={traceFilter === "all" ? "active" : ""} onClick={() => setTraceFilter("all")}>
                            All ({trace.length})
                        </button>
                        <button type="button" className={traceFilter === "agent_turn" ? "active" : ""} onClick={() => setTraceFilter("agent_turn")}>
                            Turns ({turnEntries.length})
                        </button>
                        <button type="button" className={traceFilter === "tool_call" ? "active" : ""} onClick={() => setTraceFilter("tool_call")}>
                            Reads/Searches ({toolEntries.length})
                        </button>
                    </div>
                </div>

                {filteredTrace.length > 0 ? (
                    <div className="trace-feed">
                        {[...filteredTrace].reverse().map((entry) => {
                            const isTurn = entry.kind === "agent_turn";
                            const icon = isTurn ? "🤖" : entry.tool?.includes("search") ? "🔍" : entry.tool?.includes("find") ? "📂" : "📄";
                            return (
                                <div
                                    key={entry.id}
                                    className={`trace-item ${isTurn ? "turn-item" : "tool-item"} ${inspectedTrace?.id === entry.id ? "selected" : ""}`}
                                    onClick={() => setInspectedTrace(inspectedTrace?.id === entry.id ? null : entry)}
                                >
                                    <div className="trace-item-row">
                                        <span className="trace-icon">{icon}</span>
                                        <strong className="trace-title">
                                            {isTurn ? (entry.label || entry.role) : entry.tool?.replace("software_factory_", "")}
                                        </strong>
                                        <code className="trace-target">{entry.target}</code>
                                        <span className="trace-time">
                                            {entry.durationMs ? `${(entry.durationMs / 1000).toFixed(1)}s · ` : ""}
                                            {new Date(entry.timestamp).toLocaleTimeString()}
                                        </span>
                                    </div>
                                    {entry.detail && <p className="trace-detail">{entry.detail}</p>}
                                    {inspectedTrace?.id === entry.id && entry.outputPreview && (
                                        <div className="trace-transcript-drawer" onClick={(e) => e.stopPropagation()}>
                                            <div className="trace-drawer-head">
                                                <span>{isTurn ? "Structured Agent Output / Transcript Preview" : "Scoped Read Output Preview"}</span>
                                                <button type="button" className="ghost-chip-btn" onClick={() => setInspectedTrace(null)}>Close</button>
                                            </div>
                                            <pre>{entry.outputPreview}</pre>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                ) : (
                    <p className="muted">
                        {activeExec.status === "running" || activeExec.status === "starting"
                            ? `Active phase: "${activeExec.phase}". Waiting for worker tool calls and turn completions...`
                            : "No granular tool or turn events were recorded for this execution."}
                    </p>
                )}
            </div>
        </div>
    );
}
