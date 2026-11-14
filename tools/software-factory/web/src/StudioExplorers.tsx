// Copyright (c) Microsoft Corporation. All rights reserved.

import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
    Artifact,
    Execution,
    ExecutionTraceEntry,
    ExecutionTranscriptResponse,
    FactoryFeature,
    Json,
    TranscriptAgentSummary,
    TranscriptMessageItem,
} from "../../core/lifecycle-contracts.ts";
import type { FeatureQaContext } from "../../core/qa-contracts.ts";
import { workerGuidance } from "../../runtime/guidance.ts";
import { request } from "./api";
import {
    buildDesignSnapshots,
    buildPlanSnapshots,
    DesignRevisionDiffExplorer,
    PlanRevisionDiffExplorer,
} from "./RevisionDiffViewer";

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

export function formatDuration(ms: number): string {
    const totalSec = Math.max(0, ms / 1000);
    if (totalSec < 60) return `${totalSec.toFixed(1)}s`;
    const mins = Math.floor(totalSec / 60);
    const secs = Math.round(totalSec % 60);
    return `${mins}m ${secs}s`;
}

export function formatAiUsage(nanoAiu: number): string {
    if (nanoAiu >= 10_000_000) {
        return `${(nanoAiu / 1_000_000_000).toFixed(1)} credits`;
    }
    return nanoAiu.toLocaleString();
}

function splitFilePath(filePath: string): { prefix: string; name: string } {
    const clean = filePath.replaceAll("\\", "/");
    const parts = clean.split("/").filter(Boolean);
    if (parts.length <= 1) return { prefix: "", name: clean };
    const name = parts[parts.length - 1]!;
    if (parts.length <= 3) return { prefix: `${parts.slice(0, -1).join("/")}/`, name };
    return { prefix: `${parts[0]}/…/${parts[parts.length - 2]}/`, name };
}

function summarizePlainPreview(textValue: string, maxChars = 240): { preview: string; truncated: boolean } {
    const stripped = textValue.replace(/`([^`]+)`/g, "$1").replace(/\s+/g, " ").trim();
    if (stripped.length <= maxChars) return { preview: stripped, truncated: false };
    const cut = stripped.slice(0, maxChars);
    const lastSpace = cut.lastIndexOf(" ");
    return { preview: `${(lastSpace > 120 ? cut.slice(0, lastSpace) : cut).trim()}…`, truncated: true };
}

function resolveVerifiedChecks(
    feature: FactoryFeature | undefined,
    itemId: string,
    prNumber: number,
    prHead: string | undefined,
    rawChecks: string,
): string {
    if (rawChecks !== "pending" && rawChecks !== "unknown") return rawChecks;
    if (!feature) return rawChecks;
    const implArt = [...feature.factory.artifacts].reverse().find(
        (a) => a.kind === "implementation" && a.requirementsRevision === feature.current.id,
    );
    if (!implArt || !isRecord(implArt.content)) return rawChecks;
    const implItems = asArray(implArt.content.items).filter(isRecord);
    const match = implItems.find((entry) => String(entry.itemId) === itemId);
    if (!match || match.observerStatus !== "ready-for-human-merge") return rawChecks;
    const receipt = isRecord(match.receipt) ? match.receipt : null;
    const receiptPr = Number(receipt?.prNumber || (isRecord(receipt?.pr) ? receipt.pr.number : 0));
    const receiptHead = typeof receipt?.headSha === "string" ? receipt.headSha : "";
    if (receiptPr === prNumber && (!prHead || !receiptHead || prHead === receiptHead)) {
        return "passing";
    }
    return rawChecks;
}

function StructuredOutputPreview({ raw }: { raw: string }) {
    try {
        const parsed: unknown = JSON.parse(raw);
        if (isRecord(parsed)) {
            const entries = Object.entries(parsed).slice(0, 8);
            return (
                <div className="structured-preview-grid">
                    {entries.map(([key, val]) => {
                        let summary = "";
                        if (typeof val === "string") summary = val.length > 220 ? `${val.slice(0, 220)}…` : val;
                        else if (typeof val === "number" || typeof val === "boolean") summary = String(val);
                        else if (Array.isArray(val)) summary = `${val.length} item${val.length === 1 ? "" : "s"}`;
                        else if (isRecord(val)) summary = Object.keys(val).map(formatSectionTitle).join(", ");
                        else summary = "—";
                        return (
                            <div key={key} className="structured-preview-row">
                                <span className="structured-preview-key">{formatSectionTitle(key)}</span>
                                <span className="structured-preview-val">{summary}</span>
                            </div>
                        );
                    })}
                </div>
            );
        }
    } catch {
        // Plain code or text snippet — render as clean code block.
    }
    return <pre className="code-snippet-box">{raw}</pre>;
}

function describeGuidanceFile(filePath: string): { title: string; summary: string } {
    if (filePath.includes("research-standards.md")) {
        return {
            title: "Codebase Research Standards",
            summary: "Requires pinned file-and-line source citations across target and consumer repos; forbids unverified assumptions.",
        };
    }
    if (filePath.includes("design-standards.md")) {
        return {
            title: "Android Auth Design Standards",
            summary: "Enforces the 10-section architecture contract, IPC wire compatibility, telemetry schema, and requirement traceability.",
        };
    }
    if (filePath.includes("review-rubric.md")) {
        return {
            title: "Independent Design & Plan Review Rubric",
            summary: "Adversarial GO/NO-GO checklist auditing Stage 0 fidelity, cross-version IPC safety, concurrency, and testability.",
        };
    }
    if (filePath.includes("planning-standards.md")) {
        return {
            title: "Feature Planning & PBI DAG Standards",
            summary: "Rules for decomposing an approved design into single-repo PBIs, ordered dependency waves, and unit/E2E test targets.",
        };
    }
    if (filePath.includes("coding-standards.md")) {
        return {
            title: "Android Auth Coding Standards",
            summary: "Enforces Kotlin-first conventions, custom Logger privacy rules, structured concurrency, and module boundaries.",
        };
    }
    if (filePath.includes("review-standards.md")) {
        return {
            title: "Independent Code Review Standards",
            summary: "Audits diffs and test results against every PBI acceptance criterion, compatibility invariant, and security rule.",
        };
    }
    if (filePath.includes("copilot-instructions.md")) {
        return {
            title: "Workspace Architecture & Multi-Repo Guide",
            summary: "Defines MSAL/OneAuth → Common IPC → Broker → eSTS flow boundaries and repository ownership.",
        };
    }
    return {
        title: filePath.split("/").pop() || filePath,
        summary: "Read-only repository guidance loaded into this specialist's context.",
    };
}

function roleDisplayName(role?: string, label?: string): {
    title: string;
    badge: string;
    purpose: string;
    guideKey?: keyof typeof workerGuidance;
} {
    const raw = `${role || ""} ${label || ""}`.toLowerCase();
    if (raw.includes("researcher") || raw.includes("evidence") || raw.includes("draft success criteria")) {
        return {
            title: "Repository Researcher",
            badge: "RESEARCH",
            purpose: "Reads pinned source files across target and consumer repositories to gather verified file/line facts, call flows, and compatibility constraints before authoring.",
            guideKey: "research",
        };
    }
    if (raw.includes("design-reviewer") || raw.includes("independent design reviewer") || raw.includes("independent-review")) {
        return {
            title: "Independent Design Reviewer",
            badge: "REVIEWER",
            purpose: "Adversarially audits the authored specification against repository source code, Stage 0 requirements, and IPC/wire compatibility; blocks approval until zero findings remain.",
            guideKey: "review",
        };
    }
    if (raw.includes("design revision")) {
        return {
            title: "Design Author (Revision)",
            badge: "BUILDER",
            purpose: "Updates the technical specification, sequence flows, and traceability matrix to resolve every blocker/major/minor finding raised by the Independent Design Reviewer.",
            guideKey: "design",
        };
    }
    if (raw.includes("design-author") || raw.includes("design author")) {
        return {
            title: "Design Author",
            badge: "BUILDER",
            purpose: "Authors the 10-section technical design, component contracts, failure modes, rollout telemetry, and requirement traceability grounded in Researcher evidence.",
            guideKey: "design",
        };
    }
    if (raw.includes("plan-review")) {
        return {
            title: "Independent Plan Reviewer",
            badge: "REVIEWER",
            purpose: "Verifies that planned PBIs cover 100% of approved requirements, respect single-repo boundaries, and form a valid, acyclic dependency DAG.",
            guideKey: "review",
        };
    }
    if (raw.includes("planner") || raw.includes("feature-planner")) {
        return {
            title: "Feature Planner",
            badge: "PLANNER",
            purpose: "Decomposes the approved design into ordered, repository-scoped backlog items with explicit file targets, acceptance criteria, and test plans.",
            guideKey: "plan",
        };
    }
    if (raw.includes("code-reviewer") || raw.includes("validate")) {
        return {
            title: "Independent Code Reviewer",
            badge: "REVIEWER",
            purpose: "Independently reviews code changes and test coverage against PBI acceptance criteria without mutation privileges.",
            guideKey: "codeReview",
        };
    }
    if (raw.includes("coder") || raw.includes("local builder") || raw.includes("tactical repair")) {
        return {
            title: raw.includes("repair") ? "Tactical Repair Builder" : "Coding Builder",
            badge: "BUILDER",
            purpose: raw.includes("repair")
                ? "Applies minimal, surgical source and test repairs to resolve CI pipeline failures or actionable PR review threads."
                : "Implements scoped code changes and unit tests inside an isolated repository worktree and iterates on reviewer findings.",
            guideKey: "code",
        };
    }
    if (raw.includes("verify tactical") || raw.includes("repair review")) {
        return {
            title: "Independent Repair Reviewer",
            badge: "REVIEWER",
            purpose: "Independently audits tactical PR repairs before any commit is pushed to the open pull request.",
            guideKey: "codeReview",
        };
    }
    if (raw.includes("observe pr") || raw.includes("observer")) {
        return {
            title: "PR & CI Observer",
            badge: "OBSERVER",
            purpose: "Monitors live CI pipelines, mergeability, and PR review threads to classify readiness or trigger bounded tactical repairs.",
        };
    }
    return {
        title: label || role || "Factory Worker",
        badge: "WORKER",
        purpose: "Executes a bounded workflow step inside the guarded Software Factory runtime.",
    };
}

export function DesignStudioExplorer({
    artifact,
    allDesignArtifacts,
    onAppendFeedback,
    onAskAi,
}: {
    artifact: Artifact;
    allDesignArtifacts?: Artifact[];
    onAppendFeedback?: (snippet: string) => void;
    onAskAi?: (context: FeatureQaContext) => void;
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

    const [studioTab, setStudioTab] = useState<"sections" | "arena" | "diff" | "traceability" | "risks">("sections");
    const [selectedReqId, setSelectedReqId] = useState<string | null>(null);
    const [selectedRoundIdx, setSelectedRoundIdx] = useState<number>(Math.max(0, reviews.length - 1));
    const [activeSectionFilter, setActiveSectionFilter] = useState<string | null>(null);
    const designSnapshots = buildDesignSnapshots(artifact, allDesignArtifacts);

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
                        <button
                            type="button"
                            className={studioTab === "diff" ? "studio-tab active" : "studio-tab"}
                            onClick={() => setStudioTab("diff")}
                        >
                            Revision Diff ({designSnapshots.length})
                        </button>
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
                                            <div className="studio-badge-row">
                                                {onAskAi && (
                                                    <button
                                                        type="button"
                                                        className="ghost-chip-btn ask-ai-chip"
                                                        onClick={() => onAskAi({
                                                            kind: "design-section",
                                                            title: formatSectionTitle(name),
                                                            subtitle: coveredReqs.length ? `Covers ${coveredReqs.join(", ")}` : undefined,
                                                            snippet: (typeof content === "string" ? content : JSON.stringify(content)).slice(0, 6000),
                                                        })}
                                                    >
                                                        ✦ Ask AI
                                                    </button>
                                                )}
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
                                                <div className="studio-badge-row">
                                                    {onAskAi && (
                                                        <button
                                                            type="button"
                                                            className="ghost-chip-btn ask-ai-chip"
                                                            onClick={() => onAskAi({
                                                                kind: "review-finding",
                                                                title: `Design Finding ${id} (${severity.toUpperCase()})`,
                                                                subtitle: String(finding.location || ""),
                                                                snippet: `Evidence: ${String(finding.evidence || "")}\nRequired Change: ${String(finding.requiredChange || "")}`,
                                                            })}
                                                        >
                                                            ✦ Ask AI
                                                        </button>
                                                    )}
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

            {studioTab === "diff" && (
                <DesignRevisionDiffExplorer
                    artifact={artifact}
                    allDesignArtifacts={allDesignArtifacts}
                    onAskAi={onAskAi}
                />
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
        </article>
    );
}

export function PlanStudioExplorer({
    artifact,
    allPlanArtifacts,
    feature,
    onAppendFeedback,
    onSelectItemForOps,
    onAskAi,
}: {
    artifact: Artifact;
    allPlanArtifacts?: Artifact[];
    feature?: FactoryFeature;
    onAppendFeedback?: (snippet: string) => void;
    onSelectItemForOps?: (itemId: string) => void;
    onAskAi?: (context: FeatureQaContext) => void;
}) {
    const body = isRecord(artifact.content) ? artifact.content : {};
    const items = asArray(body.items).filter(isRecord);
    const reviewHistory = asArray(body.reviewHistory).filter(isRecord);
    const isLiveInProgress = body.liveInProgress === true;
    const [selectedItemId, setSelectedItemId] = useState<string | null>(items[0] ? String(items[0].id) : null);
    const [viewMode, setViewMode] = useState<"dag" | "reviews" | "diff">("dag");
    const planSnapshots = buildPlanSnapshots(artifact, allPlanArtifacts);
    const [selectedReviewRound, setSelectedReviewRound] = useState<number>(Math.max(0, reviewHistory.length - 1));
    const [summaryExpanded, setSummaryExpanded] = useState(false);
    const [roundSummaryExpanded, setRoundSummaryExpanded] = useState(false);

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
        if (fromMonitor) {
            const num = Number(fromMonitor.number);
            const rawChecks = String(fromMonitor.checks);
            const head = typeof fromMonitor.head === "string" ? fromMonitor.head : undefined;
            return {
                number: num,
                status: String(fromMonitor.status),
                checks: resolveVerifiedChecks(feature, id, num, head, rawChecks),
                url: String(fromMonitor.url),
            };
        }
        const implArt = feature ? [...feature.factory.artifacts].reverse().find((a) => a.kind === "implementation" && a.requirementsRevision === feature.current.id) : undefined;
        const implContent = isRecord(implArt?.content) ? implArt.content : null;
        const implMatch = asArray(implContent?.items).filter(isRecord).find((entry) => String(entry.itemId) === id);
        const receipt = isRecord(implMatch?.receipt) ? implMatch.receipt : null;
        if (receipt && typeof receipt.prNumber === "number" && typeof receipt.url === "string") {
            return {
                number: receipt.prNumber,
                status: "open",
                checks: implMatch?.observerStatus === "ready-for-human-merge" ? "passing" : "pending",
                url: receipt.url,
            };
        }
        const binding = feature?.factory.prBindings.find((b) => b.itemId === id);
        if (binding) return { number: binding.number, status: "bound", checks: "unknown", url: "" };
        return null;
    };

    const rawPlanSummary = typeof body.summary === "string" ? body.summary.trim() : "";
    const planSummaryPreview = summarizePlainPreview(rawPlanSummary, 240);

    return (
        <div className="plan-document studio-explorer">
            {isLiveInProgress && (
                <div className="studio-iteration-banner">
                    <div>
                        <strong>Live Planning Run in Progress</strong>
                        <p className="muted">
                            Streaming live Feature Planner drafts and Independent Plan Reviewer rounds as each subagent finishes.
                        </p>
                    </div>
                </div>
            )}
            <div className="studio-banner">
                <div className="studio-banner-main">
                    <span className="studio-pill emerald">{items.length} Planned PBI{items.length === 1 ? "" : "s"}</span>
                    <span className="studio-pill neutral">{waves.length} Execution Wave{waves.length === 1 ? "" : "s"}</span>
                    {reviewHistory.length > 0 && (
                        <span className="studio-pill purple">{reviewHistory.length} Planner vs. Reviewer Round{reviewHistory.length === 1 ? "" : "s"}</span>
                    )}
                </div>
                <div className="studio-subnav">
                    <button type="button" className={viewMode === "dag" ? "studio-tab active" : "studio-tab"} onClick={() => setViewMode("dag")}>
                        Execution Waves &amp; PBI Specs
                    </button>
                    {reviewHistory.length > 0 && (
                        <button type="button" className={viewMode === "reviews" ? "studio-tab active" : "studio-tab"} onClick={() => setViewMode("reviews")}>
                            Planner vs. Reviewer ({reviewHistory.length})
                        </button>
                    )}
                    <button type="button" className={viewMode === "diff" ? "studio-tab active" : "studio-tab"} onClick={() => setViewMode("diff")}>
                        Revision Diff ({planSnapshots.length})
                    </button>
                </div>
            </div>

            {rawPlanSummary && (
                <div className="plan-overview-card">
                    <div className="plan-overview-head">
                        <span className="eyebrow">ARCHITECTURE &amp; ROLLOUT OVERVIEW</span>
                        {planSummaryPreview.truncated && (
                            <button type="button" className="ghost-chip-btn" onClick={() => setSummaryExpanded(!summaryExpanded)}>
                                {summaryExpanded ? "Collapse overview ▴" : "Read full overview ▾"}
                            </button>
                        )}
                    </div>
                    {summaryExpanded || !planSummaryPreview.truncated ? (
                        <div className="markdown-prose compact-prose">
                            <Markdown remarkPlugins={[remarkGfm]} skipHtml>{rawPlanSummary}</Markdown>
                        </div>
                    ) : (
                        <p className="plan-overview-preview">{planSummaryPreview.preview}</p>
                    )}
                </div>
            )}

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
                                    {onAskAi && (
                                        <button
                                            type="button"
                                            className="ghost-chip-btn ask-ai-chip"
                                            onClick={() => onAskAi({
                                                kind: "pbi",
                                                title: `${String(selectedItem.id)} — ${String(selectedItem.title)}`,
                                                subtitle: String(selectedItem.repository || ""),
                                                snippet: String(selectedItem.description || "").slice(0, 6000),
                                            })}
                                        >
                                            ✦ Ask AI
                                        </button>
                                    )}
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
                                        {asArray(selectedItem.files).length ? asArray(selectedItem.files).map((f, i) => {
                                            const full = String(f);
                                            const parts = splitFilePath(full);
                                            return (
                                                <code key={i} className="file-path-chip" title={full}>
                                                    {parts.prefix && <span className="path-prefix">{parts.prefix}</span>}
                                                    <strong>{parts.name}</strong>
                                                </code>
                                            );
                                        }) : <span className="muted">Repository-scoped</span>}
                                    </div>
                                </div>
                                <div>
                                    <h5>Verification Suites ({asArray(selectedItem.tests).length})</h5>
                                    <ul className="compact-test-list">
                                        {asArray(selectedItem.tests).map((t, i) => (
                                            <li key={i}>{String(t)}</li>
                                        ))}
                                    </ul>
                                </div>
                            </div>
                        </aside>
                    )}
                </div>
            )}

            {viewMode === "reviews" && reviewHistory.length > 0 && (() => {
                const activeIdx = Math.min(selectedReviewRound, reviewHistory.length - 1);
                const entry = reviewHistory[activeIdx]!;
                const nextEntry = reviewHistory[activeIdx + 1];
                const rev = isRecord(entry.review) ? entry.review : null;
                const pass = rev?.pass === true;
                const findings = asArray(rev?.findings).map(String);
                const validationError = typeof entry.validationError === "string" ? entry.validationError : null;
                const candidate = isRecord(entry.candidate) ? entry.candidate : null;
                const candidateItems = asArray(candidate?.items).filter(isRecord);
                const nextCandidate = isRecord(nextEntry?.candidate) ? nextEntry.candidate : (!nextEntry && activeIdx < reviewHistory.length - 1 ? null : (activeIdx === reviewHistory.length - 1 && !candidate ? body : null));
                const nextItems = asArray(nextCandidate?.items).filter(isRecord);

                const resolvedDiffs: { id: string; changes: string[] }[] = [];
                if (candidate && nextCandidate && nextItems.length > 0) {
                    for (const nextItem of nextItems) {
                        const id = String(nextItem.id);
                        const prevItem = candidateItems.find((p) => String(p.id) === id);
                        const diffs: string[] = [];
                        if (!prevItem) {
                            diffs.push(`Added new PBI "${String(nextItem.title)}" (${String(nextItem.repository)})`);
                        } else {
                            if (String(prevItem.title) !== String(nextItem.title)) {
                                diffs.push(`Updated title → "${String(nextItem.title)}"`);
                            }
                            const prevFiles = new Set(asArray(prevItem.files).map(String));
                            const currFiles = new Set(asArray(nextItem.files).map(String));
                            const addedFiles = [...currFiles].filter((f) => !prevFiles.has(f));
                            const removedFiles = [...prevFiles].filter((f) => !currFiles.has(f));
                            if (addedFiles.length) diffs.push(`Added target files: ${addedFiles.join(", ")}`);
                            if (removedFiles.length) diffs.push(`Removed files: ${removedFiles.join(", ")}`);
                            if (String(prevItem.description) !== String(nextItem.description)) {
                                diffs.push("Revised technical specification & method contracts");
                            }
                            if (JSON.stringify(prevItem.acceptanceCriteria) !== JSON.stringify(nextItem.acceptanceCriteria)) {
                                diffs.push("Updated acceptance criteria");
                            }
                            if (JSON.stringify(prevItem.tests) !== JSON.stringify(nextItem.tests)) {
                                diffs.push("Updated required test suite");
                            }
                        }
                        if (diffs.length) resolvedDiffs.push({ id, changes: diffs });
                    }
                }

                return (
                    <div className="arena-container">
                        <div className="round-scrubber">
                            <span className="eyebrow">SELECT PLANNER VS. REVIEWER ROUND</span>
                            <div className="round-buttons">
                                {reviewHistory.map((rEntry, rIdx) => {
                                    const rRev = isRecord(rEntry.review) ? rEntry.review : null;
                                    const rPass = rRev?.pass === true;
                                    const rFindings = asArray(rRev?.findings);
                                    const rValErr = typeof rEntry.validationError === "string" ? rEntry.validationError : null;
                                    const awaitingReview = !rRev && !rValErr;
                                    return (
                                        <button
                                            type="button"
                                            key={rIdx}
                                            onClick={() => setSelectedReviewRound(rIdx)}
                                            className={`round-pill ${activeIdx === rIdx ? "selected" : ""} ${rPass ? "pass" : "changes"}`}
                                        >
                                            <strong>Round {Number(rEntry.attempt ?? rIdx) + 1}</strong>
                                            <span>
                                                {awaitingReview
                                                    ? "Under Review…"
                                                    : rPass
                                                        ? "Approved ✓"
                                                        : rValErr
                                                            ? "Schema Fix Needed"
                                                            : `${rFindings.length} Objection${rFindings.length === 1 ? "" : "s"}`}
                                            </span>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>

                        <div className="arena-split">
                            <section className="arena-pane author-pane">
                                <div className="studio-badge-row">
                                    <span className="studio-pill blue">PLANNER · ROUND {activeIdx + 1}</span>
                                    {candidateItems.length > 0 && <span className="mono-chip">{candidateItems.length} PBIs</span>}
                                </div>
                                <h4>Feature Planner Proposal &amp; Resolution</h4>
                                {candidate ? (
                                    <>
                                        {typeof candidate.summary === "string" && candidate.summary.trim() && (() => {
                                            const cPrev = summarizePlainPreview(candidate.summary, 220);
                                            return (
                                                <div className="builder-summary-box">
                                                    {roundSummaryExpanded || !cPrev.truncated ? (
                                                        <div className="markdown-prose compact-prose">
                                                            <Markdown remarkPlugins={[remarkGfm]} skipHtml>{candidate.summary}</Markdown>
                                                        </div>
                                                    ) : (
                                                        <p className="plan-overview-preview">{cPrev.preview}</p>
                                                    )}
                                                    {cPrev.truncated && (
                                                        <button type="button" className="ghost-chip-btn" style={{ marginTop: 8 }} onClick={() => setRoundSummaryExpanded(!roundSummaryExpanded)}>
                                                            {roundSummaryExpanded ? "Show less ▴" : "Expand round summary ▾"}
                                                        </button>
                                                    )}
                                                </div>
                                            );
                                        })()}
                                        <ul className="studio-bullet-list">
                                            {candidateItems.map((ci) => (
                                                <li key={String(ci.id)}>
                                                    <strong>{String(ci.id)} ({String(ci.repository)}):</strong> {String(ci.title)}
                                                </li>
                                            ))}
                                        </ul>
                                    </>
                                ) : (
                                    <p className="muted">Final revised plan shown in the Execution Waves tab ({items.length} PBIs).</p>
                                )}

                                {resolvedDiffs.length > 0 && (
                                    <div className="scope-approval-receipt" style={{ marginTop: 12 }}>
                                        <strong>✓ How Round {activeIdx + 2} Planner Resolved Round {activeIdx + 1}&apos;s Objections:</strong>
                                        <ul className="studio-bullet-list">
                                            {resolvedDiffs.map((d) => (
                                                <li key={d.id}>
                                                    <strong>{d.id}:</strong> {d.changes.join(" · ")}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </section>

                            <section className="arena-pane reviewer-pane">
                                <div className="studio-badge-row">
                                    <span className="studio-pill purple">INDEPENDENT PLAN REVIEWER · ROUND {activeIdx + 1}</span>
                                    <span className={`studio-pill ${pass ? "emerald" : rev || validationError ? "amber" : "blue"}`}>
                                        {!rev && !validationError ? "Reviewing…" : pass ? "PASS ✓" : "REVISION REQUESTED"}
                                    </span>
                                </div>
                                <h4>Reviewer Objections &amp; Verification</h4>
                                {validationError && (
                                    <div className="finding-card severity-major">
                                        <strong>Schema / DAG Validation Error</strong>
                                        <p>{validationError}</p>
                                    </div>
                                )}
                                {findings.length > 0 ? (
                                    <div className="findings-list">
                                        {findings.map((f, fIdx) => (
                                            <div key={fIdx} className="finding-card severity-major">
                                                <div className="finding-header">
                                                    <span className="mono-chip">OBJECTION #{fIdx + 1}</span>
                                                    {(activeIdx < reviewHistory.length - 1) && (
                                                        <span className="studio-pill emerald">Addressed in Round {activeIdx + 2} ✓</span>
                                                    )}
                                                    {onAskAi && (
                                                        <button
                                                            type="button"
                                                            className="ghost-chip-btn ask-ai-chip"
                                                            onClick={() => onAskAi({
                                                                kind: "review-finding",
                                                                title: `Plan Objection #${fIdx + 1} (Round ${activeIdx + 1})`,
                                                                snippet: f,
                                                            })}
                                                        >
                                                            ✦ Ask AI
                                                        </button>
                                                    )}
                                                </div>
                                                <p className="finding-evidence">{f}</p>
                                            </div>
                                        ))}
                                    </div>
                                ) : pass ? (
                                    <p className="muted">All requirements, repository routes, API-24 compatibility, dependencies, and test targets verified with zero remaining objections.</p>
                                ) : (
                                    <p className="muted">Independent Plan Reviewer is actively auditing this round&apos;s PBIs against repository source.</p>
                                )}
                            </section>
                        </div>
                    </div>
                );
            })()}
            {viewMode === "diff" && (
                <PlanRevisionDiffExplorer
                    artifact={artifact}
                    allPlanArtifacts={allPlanArtifacts}
                    onAskAi={onAskAi}
                />
            )}
        </div>
    );
}

export function CodeArenaExplorer({
    artifact,
    onAskAi,
}: {
    artifact: Artifact;
    onAskAi?: (context: FeatureQaContext) => void;
}) {
    const body = isRecord(artifact.content) ? artifact.content : {};
    const items: Record<string, Json>[] = artifact.kind === "implementation_attempt"
        ? [body]
        : asArray(body.items).filter(isRecord);

    const [selectedItemIdx, setSelectedItemIdx] = useState(0);
    const activeItem = items[selectedItemIdx] || items[0] || null;
    const reviewHistory = asArray(activeItem?.reviewHistory).filter(isRecord);
    const [selectedRoundIdx, setSelectedRoundIdx] = useState(Math.max(0, reviewHistory.length - 1));
    const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
    const [codeViewTab, setCodeViewTab] = useState<"arena" | "observer">("arena");

    const currentRound = reviewHistory[selectedRoundIdx] || reviewHistory.at(-1) || null;
    const review = isRecord(currentRound?.review) ? currentRound.review : null;
    const criteria = asArray(review?.criteria).filter(isRecord);
    const findings = asArray(review?.findings).filter(isRecord);
    const receipt = isRecord(activeItem?.receipt) ? activeItem.receipt : null;
    const prObj = isRecord(receipt?.pr) ? receipt.pr : null;
    const prNumber = typeof receipt?.prNumber === "number" ? receipt.prNumber : (typeof prObj?.number === "number" ? prObj.number : null);
    const prUrl = typeof receipt?.url === "string" ? receipt.url : (typeof prObj?.url === "string" ? prObj.url : "");
    const observer = isRecord(activeItem?.observer) ? activeItem.observer : null;
    const repairs = asArray(activeItem?.repairs).filter(isRecord);
    const observerStatus = typeof activeItem?.observerStatus === "string"
        ? activeItem.observerStatus
        : (typeof observer?.status === "string" ? observer.status : "");
    const hasObserverDetails = Boolean(observer || repairs.length > 0);

    const committedFiles = asArray(receipt?.files).map(String);
    const fallbackRoundWithFiles = [...reviewHistory].reverse().find((r) => asArray(r.files).length > 0);
    const fallbackFiles = fallbackRoundWithFiles ? asArray(fallbackRoundWithFiles.files).map(String) : committedFiles;
    const roundFiles = asArray(currentRound?.files).map(String);
    const effectiveFiles = roundFiles.length > 0 ? roundFiles : fallbackFiles;
    const usingFallbackFiles = roundFiles.length === 0 && effectiveFiles.length > 0;

    const fallbackRoundWithTests = [...reviewHistory].reverse().find((r) => asArray(r.testsAddedOrUpdated).length > 0);
    const testsUpdated = asArray(currentRound?.testsAddedOrUpdated).map(String);
    const effectiveTests = testsUpdated.length > 0
        ? testsUpdated
        : (fallbackRoundWithTests ? asArray(fallbackRoundWithTests.testsAddedOrUpdated).map(String) : []);

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
                    {observerStatus && (
                        <span className={`studio-pill ${observerStatus === "ready-for-human-merge" ? "emerald" : "amber"}`}>
                            PR Observer: {observerStatus}
                        </span>
                    )}
                    {typeof activeItem?.branch === "string" && (
                        <code className="commit-pill">branch: {activeItem.branch}</code>
                    )}
                    {prNumber && prUrl && (
                        <a href={prUrl} target="_blank" rel="noreferrer" className="studio-pill purple">
                            PR #{prNumber} ↗
                        </a>
                    )}
                </div>
                {hasObserverDetails && (
                    <div className="studio-subnav" role="tablist" aria-label="Implementation and Observer views">
                        <button
                            type="button"
                            className={codeViewTab === "arena" ? "studio-tab active" : "studio-tab"}
                            onClick={() => setCodeViewTab("arena")}
                        >
                            Builder vs. Reviewer ({reviewHistory.length})
                        </button>
                        <button
                            type="button"
                            className={codeViewTab === "observer" ? "studio-tab active" : "studio-tab"}
                            onClick={() => setCodeViewTab("observer")}
                        >
                            PR Observer &amp; CI Repairs ({repairs.length ? `${repairs.length} repair · ` : ""}{observerStatus === "ready-for-human-merge" ? "All Green ✓" : observerStatus || "Tracked"})
                        </button>
                    </div>
                )}
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

            {codeViewTab === "observer" && hasObserverDetails && (
                <section className="observer-studio" aria-label="PR Observer and Tactical Repair Timeline">
                    {observer && (
                        <section className="studio-doc-section observer-verdict-card">
                            <div className="studio-doc-section-header">
                                <div>
                                    <div className="studio-badge-row">
                                        <span className={`studio-pill ${observerStatus === "ready-for-human-merge" ? "emerald" : "amber"}`}>
                                            {observerStatus === "ready-for-human-merge" ? "✓ READY FOR HUMAN MERGE" : observerStatus.toUpperCase()}
                                        </span>
                                        {typeof receipt?.headSha === "string" && (
                                            <code className="commit-pill">Verified Head: {receipt.headSha.slice(0, 10)}</code>
                                        )}
                                        {repairs.length > 0 && (
                                            <span className="studio-pill blue">
                                                {repairs.length} Tactical Repair Commit{repairs.length === 1 ? "" : "s"} Pushed
                                            </span>
                                        )}
                                    </div>
                                    <h3>Post-PR CI Supervision &amp; Review Resolution</h3>
                                </div>
                                {typeof observer.prUrl === "string" && (
                                    <a href={observer.prUrl} target="_blank" rel="noreferrer" className="secondary ado-link-btn">
                                        Open PR on GitHub ↗
                                    </a>
                                )}
                            </div>
                            {typeof observer.summary === "string" && (
                                <div className="markdown-prose compact-prose">
                                    <Markdown remarkPlugins={[remarkGfm]} skipHtml>{observer.summary}</Markdown>
                                </div>
                            )}
                            {asArray(observer.evidence).length > 0 && (
                                <div className="observer-evidence-block">
                                    <h5>Verified CI Pipelines &amp; Review Threads ({asArray(observer.evidence).length})</h5>
                                    <ul className="observer-evidence-list">
                                        {asArray(observer.evidence).map((ev, idx) => (
                                            <li key={idx}>
                                                <span className="checkmark">✓</span>
                                                <span>{String(ev)}</span>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                        </section>
                    )}

                    {repairs.length > 0 && (
                        <section className="observer-repairs-section">
                            <div className="studio-section-head">
                                <h4>Tactical CI &amp; Review Repairs ({repairs.length})</h4>
                                <span className="muted">Autonomous post-PR fixes verified by an independent reviewer before pushing to the PR branch.</span>
                            </div>
                            {repairs.map((op, idx) => {
                                const rep = isRecord(op.repair) ? op.repair : {};
                                const builder = isRecord(rep.builder) ? rep.builder : {};
                                const rev = isRecord(rep.review) ? rep.review : {};
                                const repReceipt = isRecord(op.receipt) ? op.receipt : {};
                                const fromSha = typeof rep.expectedHead === "string" ? rep.expectedHead.slice(0, 8) : "initial";
                                const toSha = typeof repReceipt.headSha === "string"
                                    ? repReceipt.headSha.slice(0, 8)
                                    : (typeof rep.headSha === "string" ? rep.headSha.slice(0, 8) : "pushed");
                                const modFiles = asArray(builder.files).length > 0 ? asArray(builder.files) : asArray(rep.files);
                                return (
                                    <div key={String(op.key || idx)} className="repair-round-card">
                                        <div className="repair-round-head">
                                            <div className="studio-badge-row">
                                                <span className="studio-pill blue">TACTICAL REPAIR ROUND #{Number(rep.round ?? idx) + 1}</span>
                                                <span className={`studio-pill ${op.status === "completed" ? "emerald" : "amber"}`}>
                                                    {op.status === "completed" ? "✓ Verified & Pushed" : String(op.status)}
                                                </span>
                                                <code className="commit-pill">{fromSha} → {toSha}</code>
                                            </div>
                                            {typeof op.updatedAt === "string" && (
                                                <span className="muted">{new Date(op.updatedAt).toLocaleString()}</span>
                                            )}
                                        </div>
                                        <div className="arena-split">
                                            <div className="arena-pane">
                                                <div className="arena-pane-header">
                                                    <span className="studio-pill blue">REPAIR BUILDER</span>
                                                    <h4>Root-Cause Fix</h4>
                                                    {typeof builder.verdict === "string" && (
                                                        <span className="studio-pill emerald">{builder.verdict.toUpperCase()}</span>
                                                    )}
                                                </div>
                                                {typeof builder.summary === "string" && (
                                                    <div className="markdown-prose compact-prose">
                                                        <Markdown remarkPlugins={[remarkGfm]} skipHtml>{builder.summary}</Markdown>
                                                    </div>
                                                )}
                                                {modFiles.length > 0 && (
                                                    <div style={{ marginTop: 10 }}>
                                                        <h5>Patched Files ({modFiles.length})</h5>
                                                        <div className="chip-wrap">
                                                            {modFiles.map((f, fIdx) => {
                                                                const rawPath = isRecord(f) ? String(f.path || "") : String(f);
                                                                const parts = splitFilePath(rawPath);
                                                                return (
                                                                    <code key={fIdx} className="file-path-chip" title={rawPath}>
                                                                        {parts.prefix && <span className="path-prefix">{parts.prefix}</span>}
                                                                        <strong>{parts.name}</strong>
                                                                    </code>
                                                                );
                                                            })}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                            <div className="arena-pane">
                                                <div className="arena-pane-header">
                                                    <span className="studio-pill purple">INDEPENDENT REPAIR REVIEWER</span>
                                                    <h4>Pre-Push Audit</h4>
                                                    {typeof rev.verdict === "string" && (
                                                        <span className={`studio-pill ${rev.verdict === "pass" ? "emerald" : "amber"}`}>
                                                            {rev.verdict.toUpperCase()} ✓
                                                        </span>
                                                    )}
                                                </div>
                                                {typeof rev.summary === "string" && (
                                                    <div className="markdown-prose compact-prose">
                                                        <Markdown remarkPlugins={[remarkGfm]} skipHtml>{rev.summary}</Markdown>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </section>
                    )}
                </section>
            )}

            {codeViewTab === "arena" && (
                <>
                    {reviewHistory.length > 0 && (
                        <div className="round-scrubber">
                            <span className="eyebrow">BUILDER VS. INDEPENDENT CODE REVIEWER ROUNDS</span>
                            <div className="round-buttons">
                                {reviewHistory.map((roundEntry, idx) => {
                                    const rev = isRecord(roundEntry.review) ? roundEntry.review : null;
                                    const rCriteria = asArray(rev?.criteria).filter(isRecord);
                                    const rFindings = asArray(rev?.findings);
                                    const pass = rev?.pass === true && rFindings.length === 0 && rCriteria.length > 0 &&
                                        rCriteria.every((criterion) => criterion.status === "addressed");
                                    const valErr = typeof roundEntry.validationError === "string";
                                    return (
                                        <button
                                            type="button"
                                            key={idx}
                                            className={`round-pill ${selectedRoundIdx === idx ? "selected" : ""} ${pass ? "pass" : "changes"}`}
                                            onClick={() => setSelectedRoundIdx(idx)}
                                        >
                                            <strong>Round {Number(roundEntry.round ?? idx + 1)}</strong>
                                            <span>{pass ? "✓ Approved" : valErr ? "Schema Error" : rFindings.length ? `${rFindings.length} Finding(s)` : "Changes required"}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    <div className="arena-split">
                        <div className="arena-pane builder-pane">
                            <div className="arena-pane-header">
                                <span className="studio-pill blue">CODING BUILDER</span>
                                <h4>Builder Output — {String(activeItem?.itemId || "PBI")} (Round {selectedRoundIdx + 1})</h4>
                                {onAskAi && (
                                    <button
                                        type="button"
                                        className="ghost-chip-btn ask-ai-chip"
                                        onClick={() => onAskAi({
                                            kind: "implementation",
                                            title: `${String(activeItem?.itemId || "PBI")} — Round ${selectedRoundIdx + 1}`,
                                            subtitle: `${effectiveFiles.length} file(s), ${effectiveTests.length} test(s)`,
                                            path: selectedFilePath || effectiveFiles[0],
                                            snippet: [
                                                typeof currentRound?.builderSummary === "string" ? currentRound.builderSummary : "",
                                                effectiveFiles.length ? `Files: ${effectiveFiles.join(", ")}` : "",
                                                effectiveTests.length ? `Tests: ${effectiveTests.join(", ")}` : "",
                                            ].filter(Boolean).join("\n\n").slice(0, 6000),
                                        })}
                                    >
                                        ✦ Ask AI
                                    </button>
                                )}
                            </div>
                            {typeof currentRound?.builderSummary === "string" && currentRound.builderSummary ? (
                                <div className="builder-summary-box markdown-prose compact-prose">
                                    <Markdown remarkPlugins={[remarkGfm]} skipHtml>{currentRound.builderSummary}</Markdown>
                                </div>
                            ) : usingFallbackFiles && (
                                <p className="muted">
                                    Initial implementation authored across {effectiveFiles.length} target files and submitted to Independent Code Review.
                                </p>
                            )}
                            {typeof currentRound?.validationError === "string" && (
                                <div className="studio-callout warning">
                                    <strong>Builder Validation Error:</strong> {currentRound.validationError}
                                </div>
                            )}
                            <div className="pbi-inspector-split">
                                <div>
                                    <h5>{usingFallbackFiles ? `PBI Implementation Files (${effectiveFiles.length})` : `Modified / Created Files (${effectiveFiles.length})`}</h5>
                                    <div className="chip-wrap">
                                        {effectiveFiles.length ? effectiveFiles.map((file) => {
                                            const parts = splitFilePath(file);
                                            return (
                                                <button
                                                    type="button"
                                                    key={file}
                                                    title={file}
                                                    className={`file-path-chip ${selectedFilePath === file ? "active" : ""}`}
                                                    onClick={() => setSelectedFilePath(selectedFilePath === file ? null : file)}
                                                >
                                                    {parts.prefix && <span className="path-prefix">{parts.prefix}</span>}
                                                    <strong>{parts.name}</strong>
                                                </button>
                                            );
                                        }) : <span className="muted">No modified files recorded</span>}
                                    </div>
                                </div>
                                <div>
                                    <h5>Tests Added or Updated ({effectiveTests.length})</h5>
                                    <div className="chip-wrap">
                                        {effectiveTests.length ? effectiveTests.map((t) => {
                                            const parts = splitFilePath(t);
                                            return (
                                                <code key={t} className="mono-chip emerald" title={t}>🧪 {parts.name}</code>
                                            );
                                        }) : <span className="muted">Verified via acceptance criteria</span>}
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
                                                <div className="criterion-check-body">
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
                                                        {onAskAi && (
                                                            <button
                                                                type="button"
                                                                className="ghost-chip-btn ask-ai-chip"
                                                                onClick={() => onAskAi({
                                                                    kind: "review-finding",
                                                                    title: `Code Finding ${String(finding.id)} (${severity.toUpperCase()})`,
                                                                    subtitle: `${String(finding.file)}:${String(finding.location)}`,
                                                                    path: String(finding.file),
                                                                    snippet: `Evidence: ${String(finding.evidence)}\nRequired Fix: ${String(finding.requiredChange)}`,
                                                                })}
                                                            >
                                                                ✦ Ask AI
                                                            </button>
                                                        )}
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
                </>
            )}
        </div>
    );
}

export function DeliveryCommandCenter({
    artifact,
    feature,
    onPrefillWaiver,
    onPrefillPrFeedback,
    onAskAi,
}: {
    artifact: Artifact;
    feature: FactoryFeature;
    onPrefillWaiver?: (itemId: string, criterion: string) => void;
    onPrefillPrFeedback?: (itemId: string, feedbackBody: string, prNumber?: number) => void;
    onAskAi?: (context: FeatureQaContext) => void;
}) {
    const body = isRecord(artifact.content) ? artifact.content : {};
    const items = asArray(body.items).filter(isRecord);
    const prs = asArray(body.prs).filter(isRecord);
    const validation = asArray(body.validation).filter(isRecord);
    const reviews = asArray(body.reviews).filter(isRecord);
    const errors = asArray(body.errors).map(String);
    const delivered = body.delivered === true;
    const waivedCount = validation.filter((v) =>
        v.status !== "addressed" &&
        feature.factory.waivers.some(
            (w) => w.monitorId === artifact.id && w.itemId === String(v.itemId) && w.criterion === String(v.criterion),
        ),
    ).length;
    const addressedCount = validation.filter((v) => v.status === "addressed").length + waivedCount;
    const hasPartialGaps = validation.length > 0 && addressedCount < validation.length;

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

            {hasPartialGaps && (
                <div className="studio-callout info">
                    <strong>Why {addressedCount}/{validation.length} criteria show verified:</strong>{" "}
                    Stage 6 runs an independent post-PR audit against live repository state. Rows marked <code>PARTIAL</code> indicate edge-case findings for your judgment — either click <strong>Review &amp; Post PR Comment</strong> to request a code change on the PR, or click <strong>Sign off gap…</strong> to record a reasoned human sign-off if intentional.
                </div>
            )}

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
                    const prNumber = pr ? Number(pr.number) : undefined;
                    const prHead = pr && typeof pr.head === "string" ? pr.head : (review && typeof review.head === "string" ? review.head : undefined);
                    const resolvedChecks = pr && prNumber
                        ? resolveVerifiedChecks(feature, key, prNumber, prHead, String(pr.checks))
                        : "";
                    return (
                        <div key={key} className="delivery-pbi-card">
                            <div className="delivery-pbi-head">
                                <div className="studio-badge-row">
                                    <span className="mono-chip emerald">{key}</span>
                                    <span className="mono-chip blue">{String(item.repository)}</span>
                                    <a href={String(item.url)} target="_blank" rel="noreferrer" className="mono-chip">
                                        AB#{String(item.adoId)} · {String(item.status)} ↗
                                    </a>
                                </div>
                                <div className="studio-badge-row">
                                    {pr ? (
                                        <a
                                            href={String(pr.url)}
                                            target="_blank"
                                            rel="noreferrer"
                                            className={`studio-pill ${pr.status === "merged" || resolvedChecks === "passing" ? "emerald" : resolvedChecks === "failing" ? "crimson" : "purple"}`}
                                        >
                                            PR #{String(pr.number)}: {String(pr.status).toUpperCase()} · CI: {resolvedChecks.toUpperCase()} {resolvedChecks === "passing" ? "✓ " : ""}↗
                                        </a>
                                    ) : (
                                        <span className="studio-pill amber">Not Dispatched Yet (Wave Dependency)</span>
                                    )}
                                    {onAskAi && (
                                        <button
                                            type="button"
                                            className="ghost-chip-btn ask-ai-chip"
                                            onClick={() => onAskAi({
                                                kind: "pr",
                                                title: pr ? `${key} · PR #${String(pr.number)}` : `${key} — ${String(item.title)}`,
                                                subtitle: `${String(item.repository)} · AB#${String(item.adoId)}`,
                                                snippet: [
                                                    String(item.title),
                                                    pr ? `PR #${String(pr.number)} (${String(pr.status)}, checks: ${resolvedChecks}) ${String(pr.url)}` : "No PR yet",
                                                    ...itemValidation.map((v) => `[${String(v.status)}] ${String(v.criterion)}: ${String(v.evidence)}`),
                                                ].join("\n").slice(0, 6000),
                                            })}
                                        >
                                            ✦ Ask AI
                                        </button>
                                    )}
                                </div>
                            </div>
                            <h4>{String(item.title)}</h4>

                            {review && (
                                <div className="delivery-review-box">
                                    <div className="finding-header">
                                        <span className={`studio-pill ${review.pass === true ? "emerald" : "amber"}`}>
                                            Post-PR Audit {String(review.round)}/{String(review.maxRounds)}: {String(review.status).replaceAll("_", " ")}
                                        </span>
                                        <code className="commit-pill">head: {String(review.head).slice(0, 8)}</code>
                                        {typeof review.suggestedFeedback === "string" && onPrefillPrFeedback && (
                                            <button
                                                type="button"
                                                className="ghost-chip-btn"
                                                onClick={() => onPrefillPrFeedback(key, String(review.suggestedFeedback), prNumber)}
                                            >
                                                Review &amp; Post PR Comment →
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
                                                    {waived ? "SIGNED OFF ✓" : status.toUpperCase()}
                                                </span>
                                                <div className="criterion-check-body">
                                                    <strong>{criterionText}</strong>
                                                    <p className="muted">{String(v.evidence)}</p>
                                                </div>
                                                {status !== "addressed" && !waived && onPrefillWaiver && (
                                                    <button
                                                        type="button"
                                                        className="ghost-chip-btn"
                                                        title="Record a reasoned human sign-off if this edge case is intentional or tracked separately"
                                                        onClick={() => onPrefillWaiver(key, criterionText)}
                                                    >
                                                        Sign off gap…
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
        </div>
    );
}

function formatTokens(tokens?: number): string {
    if (!tokens || tokens <= 0) return "0";
    if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(2)}M`;
    if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k`;
    return tokens.toLocaleString();
}

function AssistantDeliverableView({ content }: { content: string }) {
    const [showFormattedJson, setShowFormattedJson] = useState(false);
    const trimmed = content.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
        try {
            const parsed: unknown = JSON.parse(trimmed);
            const pretty = JSON.stringify(parsed, null, 2);
            return (
                <div className="deliverable-structured-wrap">
                    <StructuredOutputPreview raw={trimmed} />
                    <div className="deliverable-json-toggle-row">
                        <button
                            type="button"
                            className="ghost-chip-btn"
                            onClick={() => setShowFormattedJson(!showFormattedJson)}
                        >
                            {showFormattedJson ? "Hide Formatted JSON ▴" : "{ } Inspect Formatted JSON ▾"}
                        </button>
                    </div>
                    {showFormattedJson && <pre className="code-snippet-box">{pretty}</pre>}
                </div>
            );
        } catch {
            // Not valid JSON — render as Markdown below.
        }
    }
    return (
        <div className="markdown-prose transcript-markdown">
            <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
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
    const [stageKindFilter, setStageKindFilter] = useState<string>("all");
    const stageFilteredExecutions = stageKindFilter === "all"
        ? executions
        : executions.filter((e) => e.kind === stageKindFilter);

    // Prefer explicitly selected execution, else an active or subagent-bearing run so users immediately see rich transcripts.
    const defaultExec = stageFilteredExecutions.find((e) => e.status === "running" || e.status === "starting")
        || stageFilteredExecutions.find((e) => (e.usage?.subagents ?? 0) > 0 || (e.trace?.length ?? 0) > 0)
        || stageFilteredExecutions[0]
        || executions[0]
        || null;
    const activeExec = executions.find((e) => e.id === selectedExecutionId) || defaultExec;

    const [selectedAgentId, setSelectedAgentId] = useState<string | undefined>(undefined);
    const [transcriptData, setTranscriptData] = useState<ExecutionTranscriptResponse | null>(null);
    const [loadingTranscript, setLoadingTranscript] = useState(false);
    const [transcriptFilter, setTranscriptFilter] = useState<"all" | "conversation" | "tools">("all");
    const [searchQuery, setSearchQuery] = useState("");
    const [expandedToolIds, setExpandedToolIds] = useState<Record<string, boolean>>({});
    const [expandedPrompts, setExpandedPrompts] = useState<Record<string, boolean>>({});
    const [showPlaybooks, setShowPlaybooks] = useState(false);
    const [showMilestones, setShowMilestones] = useState(false);

    useEffect(() => {
        setSelectedAgentId(undefined);
        setExpandedToolIds({});
        setExpandedPrompts({});
    }, [activeExec?.id]);

    useEffect(() => {
        if (!activeExec) {
            setTranscriptData(null);
            return;
        }
        let cancelled = false;
        const fetchTranscript = async (isBackgroundPoll = false) => {
            if (!isBackgroundPoll) setLoadingTranscript(true);
            try {
                const qs = selectedAgentId ? `?agentId=${encodeURIComponent(selectedAgentId)}` : "";
                const data = await request<ExecutionTranscriptResponse>(
                    `/api/executions/${encodeURIComponent(feature.id)}/${encodeURIComponent(activeExec.id)}/transcript${qs}`,
                );
                if (!cancelled) {
                    setTranscriptData(data);
                    if (!selectedAgentId && data.selectedAgentId) {
                        setSelectedAgentId(data.selectedAgentId);
                    }
                }
            } catch {
                if (!cancelled) setTranscriptData(null);
            } finally {
                if (!cancelled && !isBackgroundPoll) setLoadingTranscript(false);
            }
        };
        void fetchTranscript(false);

        const isLive = activeExec.status === "running" || activeExec.status === "starting";
        const timer = isLive ? setInterval(() => { void fetchTranscript(true); }, 2500) : undefined;
        return () => {
            cancelled = true;
            if (timer) clearInterval(timer);
        };
    }, [feature.id, activeExec?.id, activeExec?.status, activeExec?.updatedAt, selectedAgentId]);

    if (!activeExec) {
        return (
            <div className="flight-recorder-empty" aria-label="Subagent Flight Recorder">
                <span className="eyebrow">FLIGHT RECORDER IDLE</span>
                <p className="muted">
                    No subagent workflows have run yet. When you start Intake, Design, Planning, Coding, or Monitoring, full subagent prompts, reasoning thoughts, messages, and repository tool calls appear here.
                </p>
            </div>
        );
    }

    const agents: TranscriptAgentSummary[] = transcriptData?.agents?.length
        ? transcriptData.agents
        : (activeExec.usage?.agents || []).map((ag, idx) => ({
            agentId: `fallback-${idx}`,
            label: ag.label,
            agentType: "software-factory-worker",
            status: ag.status,
            model: ag.resolvedModel || ag.requestedModel || null,
            reasoningEffort: null,
            startedAtMs: null,
            completedAtMs: null,
            activeMs: ag.activeMs,
            nanoAiu: 0,
            messageCount: 0,
            thoughtCount: 0,
            toolCallCount: 0,
            totalTokens: 0,
        }));

    const activeAgent = agents.find((a) => a.agentId === (transcriptData?.selectedAgentId || selectedAgentId)) || agents[0] || null;
    const activeAgentIdx = activeAgent ? agents.findIndex((a) => a.agentId === activeAgent.agentId) : -1;
    const activeRoleInfo = activeAgent ? roleDisplayName(activeAgent.agentType, activeAgent.label) : null;
    const activeGuides = activeRoleInfo?.guideKey ? workerGuidance[activeRoleInfo.guideKey] : [];

    const rawItems: TranscriptMessageItem[] = transcriptData?.transcript || [];
    const conversationCount = rawItems.filter((i) => i.kind === "prompt" || i.kind === "thought" || i.kind === "assistant").length;
    const toolCount = rawItems.filter((i) => i.kind === "tool_call").length;

    const filteredItems = rawItems.filter((item) => {
        if (transcriptFilter === "conversation" && item.kind === "tool_call") return false;
        if (transcriptFilter === "tools" && item.kind !== "tool_call") return false;
        if (searchQuery.trim()) {
            const q = searchQuery.toLowerCase();
            const hay = `${item.title || ""} ${item.content} ${item.toolTarget || ""} ${item.toolOutput || ""}`.toLowerCase();
            if (!hay.includes(q)) return false;
        }
        return true;
    });

    const stageKinds = Array.from(new Set(executions.map((e) => e.kind)));
    const milestones = transcriptData?.progress || [];
    const totalSubagentsInRun = Math.max(activeExec.usage?.subagents ?? 0, agents.length);
    const totalToolsAcrossAgents = agents.reduce((sum, a) => sum + a.toolCallCount, 0) || (activeExec.trace || []).filter((t) => t.kind === "tool_call").length;
    const totalThoughtsAcrossAgents = agents.reduce((sum, a) => sum + a.thoughtCount, 0);

    const toggleTool = (id: string) => {
        setExpandedToolIds((prev) => ({ ...prev, [id]: !prev[id] }));
    };

    return (
        <div className="flight-recorder" aria-label="Subagent Flight Recorder">
            <div className="flight-top-controls">
                {stageKinds.length > 1 && (
                    <div className="flight-stage-pills" role="group" aria-label="Filter runs by stage">
                        <button
                            type="button"
                            className={stageKindFilter === "all" ? "active" : ""}
                            onClick={() => {
                                setStageKindFilter("all");
                            }}
                        >
                            All Stages ({executions.length})
                        </button>
                        {stageKinds.map((kind) => {
                            const count = executions.filter((e) => e.kind === kind).length;
                            return (
                                <button
                                    type="button"
                                    key={kind}
                                    className={stageKindFilter === kind ? "active" : ""}
                                    onClick={() => {
                                        setStageKindFilter(kind);
                                        const bestForKind = executions.find((e) => e.kind === kind && ((e.usage?.subagents ?? 0) > 0 || e.status === "running"))
                                            || executions.find((e) => e.kind === kind);
                                        if (bestForKind) onSelectExecution?.(bestForKind.id);
                                    }}
                                >
                                    {kind.toUpperCase()} ({count})
                                </button>
                            );
                        })}
                    </div>
                )}

                {stageFilteredExecutions.length > 1 && (
                    <div className="flight-exec-selector">
                        <label htmlFor="flight-exec-select" className="eyebrow">EXECUTION RUN</label>
                        <select
                            id="flight-exec-select"
                            value={activeExec.id}
                            onChange={(e) => {
                                onSelectExecution?.(e.target.value);
                            }}
                        >
                            {stageFilteredExecutions.map((exec) => {
                                const subCount = exec.usage?.subagents ?? 0;
                                const credits = exec.usage?.nanoAiu ? ` · ${formatAiUsage(exec.usage.nanoAiu)}` : "";
                                return (
                                    <option key={exec.id} value={exec.id}>
                                        {exec.kind.toUpperCase()} — {exec.status} ({subCount} subagent{subCount === 1 ? "" : "s"}{credits}) · {new Date(exec.updatedAt).toLocaleString()}
                                    </option>
                                );
                            })}
                        </select>
                    </div>
                )}
            </div>

            <div className="flight-telemetry-bar">
                <div className="telemetry-stat">
                    <span>STAGE / STATUS</span>
                    <strong>{activeExec.kind.toUpperCase()} · {activeExec.status.toUpperCase()}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>ACTIVE TIME</span>
                    <strong>{activeExec.usage ? formatDuration(activeExec.usage.activeMs) : "—"}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>SUBAGENTS</span>
                    <strong>{totalSubagentsInRun}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>AGENT THOUGHTS</span>
                    <strong>{totalThoughtsAcrossAgents > 0 ? totalThoughtsAcrossAgents.toLocaleString() : "—"}</strong>
                </div>
                <div className="telemetry-stat">
                    <span>TOOL CALLS</span>
                    <strong>{totalToolsAcrossAgents.toLocaleString()}</strong>
                </div>
                <div className="telemetry-stat" title={activeExec.usage ? `${activeExec.usage.nanoAiu.toLocaleString()} nano-AIU` : undefined}>
                    <span>AI USAGE</span>
                    <strong>{activeExec.usage ? formatAiUsage(activeExec.usage.nanoAiu) : "—"}</strong>
                </div>
            </div>

            {milestones.length > 0 && (
                <div className="flight-milestones-bar">
                    <button
                        type="button"
                        className="milestones-toggle-btn"
                        aria-expanded={showMilestones}
                        onClick={() => setShowMilestones(!showMilestones)}
                    >
                        <span className="studio-pill blue">ORCHESTRATOR LOG</span>
                        <strong>{milestones.at(-1)?.title || activeExec.phase}</strong>
                        <span className="muted">({milestones.length} workflow milestone{milestones.length === 1 ? "" : "s"} {showMilestones ? "▴" : "▾"})</span>
                    </button>
                    {showMilestones && (
                        <div className="milestones-timeline">
                            {milestones.map((m) => (
                                <div key={m.seq} className="milestone-row">
                                    <span className={`studio-pill ${m.outcome === "failure" ? "crimson" : m.outcome === "warning" ? "amber" : "emerald"}`}>
                                        #{m.seq}
                                    </span>
                                    <div className="milestone-body">
                                        <strong>{m.title}</strong>
                                        {m.summary && <p className="muted">{m.summary}</p>}
                                    </div>
                                    {m.prUrl && (
                                        <a href={m.prUrl} target="_blank" rel="noreferrer" className="mono-chip emerald">
                                            Open PR ↗
                                        </a>
                                    )}
                                    <span className="trace-time">{new Date(m.recordedAt).toLocaleTimeString()}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            <div className="flight-studio-split">
                <aside className="flight-roster-pane" aria-label="Subagent roster">
                    <div className="flight-roster-header">
                        <span className="eyebrow">SPECIALIST SUBAGENTS ({agents.length})</span>
                        <span className="field-help">Select any specialist to read its full conversation</span>
                    </div>
                    {agents.length > 0 ? (
                        <div className="swimlane-cards vertical-roster">
                            {agents.map((ag, idx) => {
                                const info = roleDisplayName(ag.agentType, ag.label);
                                const isSelected = activeAgent?.agentId === ag.agentId;
                                const pillTone = info.badge === "REVIEWER" ? "purple"
                                    : info.badge === "BUILDER" ? "blue"
                                        : info.badge === "OBSERVER" ? "amber"
                                            : "emerald";
                                return (
                                    <button
                                        type="button"
                                        key={ag.agentId}
                                        aria-expanded={isSelected}
                                        className={`swimlane-card status-${ag.status} ${isSelected ? "selected" : ""}`}
                                        onClick={() => {
                                            setSelectedAgentId(ag.agentId);
                                        }}
                                    >
                                        <div className="swimlane-top">
                                            <span className={`studio-pill ${pillTone}`}>
                                                #{idx + 1} · {info.badge}
                                            </span>
                                            <span className="swimlane-duration">{formatDuration(ag.activeMs)}</span>
                                        </div>
                                        <strong>{info.title}</strong>
                                        <code className="swimlane-label">{ag.label}</code>
                                        <div className="swimlane-meta">
                                            {ag.model && <span className="mono-chip">{ag.model}{ag.reasoningEffort ? ` · ${ag.reasoningEffort}` : ""}</span>}
                                            {ag.nanoAiu > 0 && <span className="mono-chip emerald">{formatAiUsage(ag.nanoAiu)}</span>}
                                            <span className={`status-pill-mini ${ag.status}`}>{ag.status}</span>
                                        </div>
                                        {(ag.thoughtCount > 0 || ag.messageCount > 0 || ag.toolCallCount > 0) && (
                                            <div className="swimlane-counts">
                                                {ag.thoughtCount > 0 && <span>💭 {ag.thoughtCount} thoughts</span>}
                                                {ag.messageCount > 0 && <span>💬 {ag.messageCount} msg{ag.messageCount === 1 ? "" : "s"}</span>}
                                                {ag.toolCallCount > 0 && <span>🔧 {ag.toolCallCount} tools</span>}
                                            </div>
                                        )}
                                    </button>
                                );
                            })}
                        </div>
                    ) : (
                        <p className="muted">
                            {activeExec.status === "error"
                                ? `This run stopped before spawning subagents (${activeExec.error || activeExec.phase}). Choose another run above to inspect subagent transcripts.`
                                : "Waiting for the orchestrator to spawn the first specialist subagent…"}
                        </p>
                    )}
                </aside>

                <div className="flight-transcript-pane">
                    {activeAgent && activeRoleInfo && (
                        <section className="swimlane-inspector" aria-label="Selected Specialist Inspector">
                            <div className="swimlane-inspector-head">
                                <div>
                                    <div className="studio-badge-row">
                                        <span className={`studio-pill ${activeRoleInfo.badge === "REVIEWER" ? "purple" : activeRoleInfo.badge === "BUILDER" ? "blue" : "emerald"}`}>
                                            STEP #{activeAgentIdx + 1} · {activeRoleInfo.badge}
                                        </span>
                                        <span className={`status-pill-mini ${activeAgent.status}`}>{activeAgent.status}</span>
                                        {activeAgent.model && (
                                            <span className="mono-chip">
                                                {activeAgent.model}{activeAgent.reasoningEffort ? ` (${activeAgent.reasoningEffort})` : ""}
                                            </span>
                                        )}
                                        <span className="mono-chip">{formatDuration(activeAgent.activeMs)}</span>
                                        {activeAgent.totalTokens > 0 && <span className="mono-chip">{formatTokens(activeAgent.totalTokens)} tokens</span>}
                                        {activeAgent.nanoAiu > 0 && <span className="mono-chip emerald">{formatAiUsage(activeAgent.nanoAiu)}</span>}
                                    </div>
                                    <h4>{activeRoleInfo.title} <span className="muted">({activeAgent.label})</span></h4>
                                </div>
                                {activeGuides.length > 0 && (
                                    <button
                                        type="button"
                                        className="ghost-chip-btn"
                                        aria-expanded={showPlaybooks}
                                        onClick={() => setShowPlaybooks(!showPlaybooks)}
                                    >
                                        📘 Domain Playbooks ({activeGuides.length}) {showPlaybooks ? "▴" : "▾"}
                                    </button>
                                )}
                            </div>
                            <p className="swimlane-purpose">{activeRoleInfo.purpose}</p>
                            {activeAgent.status === "cancelled" && (
                                <p className="swimlane-cancelled-note">
                                    <strong>Why cancelled?</strong> This specialist turn was interrupted when the workflow reached a configured timeout ceiling; resuming the workflow with a higher ceiling automatically re-ran this step cleanly.
                                </p>
                            )}

                            {activeGuides.length > 0 && (showPlaybooks || transcriptData?.source === "trace_fallback") && (
                                <div className="guide-cards-block">
                                    <span className="eyebrow">ENFORCED REPOSITORY STANDARDS (DOMAIN PLAYBOOKS)</span>
                                    <p className="field-help">
                                        Read-only repository instruction files injected into this specialist&apos;s prompt contract:
                                    </p>
                                    <div className="guide-cards-grid">
                                        {activeGuides.map((guidePath) => {
                                            const desc = describeGuidanceFile(guidePath);
                                            return (
                                                <div key={guidePath} className="guide-card">
                                                    <strong>📘 {desc.title}</strong>
                                                    <code>{guidePath}</code>
                                                    <p className="muted">{desc.summary}</p>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            )}
                        </section>
                    )}

                    <div className="transcript-toolbar">
                        <div className="trace-filter-pills" role="tablist" aria-label="Transcript view filter">
                            <button
                                type="button"
                                role="tab"
                                aria-selected={transcriptFilter === "all"}
                                className={transcriptFilter === "all" ? "active" : ""}
                                onClick={() => setTranscriptFilter("all")}
                            >
                                Full Transcript ({rawItems.length})
                            </button>
                            <button
                                type="button"
                                role="tab"
                                aria-selected={transcriptFilter === "conversation"}
                                className={transcriptFilter === "conversation" ? "active" : ""}
                                onClick={() => setTranscriptFilter("conversation")}
                            >
                                💬 Prompts, Reasoning &amp; Messages ({conversationCount})
                            </button>
                            <button
                                type="button"
                                role="tab"
                                aria-selected={transcriptFilter === "tools"}
                                className={transcriptFilter === "tools" ? "active" : ""}
                                onClick={() => setTranscriptFilter("tools")}
                            >
                                🔧 Tool Calls ({toolCount})
                            </button>
                        </div>
                        <input
                            type="search"
                            className="transcript-search-input"
                            placeholder="Search thoughts, messages, or file paths…"
                            aria-label="Search transcript"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                        />
                    </div>

                    {loadingTranscript && !transcriptData ? (
                        <p className="muted">Loading full subagent conversation transcript…</p>
                    ) : filteredItems.length > 0 ? (
                        <div className="transcript-stream" role="log" aria-label="Subagent conversation transcript">
                            {filteredItems.map((item) => {
                                if (item.kind === "prompt") {
                                    const isLong = item.content.length > 900;
                                    const isExpanded = Boolean(expandedPrompts[item.id]);
                                    const visiblePrompt = isLong && !isExpanded ? `${item.content.slice(0, 900)}…` : item.content;
                                    return (
                                        <article key={item.id} className="transcript-bubble bubble-prompt">
                                            <header className="bubble-header">
                                                <div className="studio-badge-row">
                                                    <span className="studio-pill neutral">📋 PROMPT</span>
                                                    <strong>{item.title || "Specialist Task Prompt"}</strong>
                                                </div>
                                                <span className="trace-time">{new Date(item.timestamp).toLocaleTimeString()}</span>
                                            </header>
                                            <div className="markdown-prose transcript-markdown">
                                                <Markdown remarkPlugins={[remarkGfm]}>{visiblePrompt}</Markdown>
                                            </div>
                                            {isLong && (
                                                <button
                                                    type="button"
                                                    className="ghost-chip-btn"
                                                    onClick={() => setExpandedPrompts((prev) => ({ ...prev, [item.id]: !isExpanded }))}
                                                >
                                                    {isExpanded ? "Collapse prompt ▴" : `Show full prompt (${item.content.length.toLocaleString()} chars) ▾`}
                                                </button>
                                            )}
                                            {item.attachedDataPreview && (
                                                <details className="prompt-data-drawer">
                                                    <summary>Attached Context &amp; Preloaded Playbooks Preview</summary>
                                                    <pre className="code-snippet-box">{item.attachedDataPreview}</pre>
                                                </details>
                                            )}
                                        </article>
                                    );
                                }

                                if (item.kind === "thought") {
                                    return (
                                        <article key={item.id} className="transcript-bubble bubble-thought">
                                            <header className="bubble-header">
                                                <div className="studio-badge-row">
                                                    <span className="studio-pill blue">💭 REASONING</span>
                                                    <strong>{item.title || "Agent Reasoning"}</strong>
                                                    {item.tokens && (
                                                        <span className="mono-chip">
                                                            {formatTokens(item.tokens.input)} in · {formatTokens(item.tokens.reasoning)} thought · {formatTokens(item.tokens.output)} out
                                                            {item.tokens.durationMs ? ` · ${(item.tokens.durationMs / 1000).toFixed(1)}s` : ""}
                                                        </span>
                                                    )}
                                                </div>
                                                <span className="trace-time">{new Date(item.timestamp).toLocaleTimeString()}</span>
                                            </header>
                                            <div className="markdown-prose transcript-markdown">
                                                <Markdown remarkPlugins={[remarkGfm]}>{item.content}</Markdown>
                                            </div>
                                        </article>
                                    );
                                }

                                if (item.kind === "assistant") {
                                    return (
                                        <article key={item.id} className="transcript-bubble bubble-assistant">
                                            <header className="bubble-header">
                                                <div className="studio-badge-row">
                                                    <span className="studio-pill emerald">🤖 AGENT MESSAGE</span>
                                                    <strong>{item.title || "Agent Response"}</strong>
                                                    {item.tokens && !item.tokens.reasoning && (
                                                        <span className="mono-chip">
                                                            {formatTokens(item.tokens.input)} in · {formatTokens(item.tokens.output)} out
                                                        </span>
                                                    )}
                                                </div>
                                                <span className="trace-time">{new Date(item.timestamp).toLocaleTimeString()}</span>
                                            </header>
                                            <AssistantDeliverableView content={item.content} />
                                        </article>
                                    );
                                }

                                if (item.kind === "lifecycle") {
                                    return (
                                        <div key={item.id} className="transcript-lifecycle-banner">
                                            <span className="studio-pill emerald">🏁 {item.title || "Completed"}</span>
                                            <span>{item.content}</span>
                                            <span className="trace-time">{new Date(item.timestamp).toLocaleTimeString()}</span>
                                        </div>
                                    );
                                }

                                // Tool call row
                                const isExpanded = Boolean(expandedToolIds[item.id]);
                                const icon = item.toolName?.includes("search") || item.toolName?.includes("grep") ? "🔍"
                                    : item.toolName?.includes("find") || item.toolName?.includes("glob") ? "📂"
                                        : item.toolName?.includes("sources") ? "🧭"
                                            : "📄";
                                return (
                                    <div
                                        key={item.id}
                                        className={`trace-item tool-item ${isExpanded ? "selected" : ""}`}
                                        onClick={() => item.toolOutput && toggleTool(item.id)}
                                    >
                                        <div className="trace-item-row">
                                            <span className="trace-icon">{icon}</span>
                                            <strong className="trace-title">{item.title || item.toolName}</strong>
                                            <code className="trace-target">{item.toolTarget || item.content}</code>
                                            <span className={`status-pill-mini ${item.toolStatus === "failed" ? "error" : "completed"}`}>
                                                {item.toolStatus === "failed" ? "failed" : item.toolStatus === "running" ? "running" : "ok"}
                                            </span>
                                            <span className="trace-time">{new Date(item.timestamp).toLocaleTimeString()}</span>
                                        </div>
                                        {isExpanded && item.toolOutput && (
                                            <div className="trace-transcript-drawer" onClick={(e) => e.stopPropagation()}>
                                                <div className="trace-drawer-head">
                                                    <span>Tool Output Snippet ({item.toolOutput.length.toLocaleString()} chars)</span>
                                                    <button type="button" className="ghost-chip-btn" onClick={() => toggleTool(item.id)}>Close</button>
                                                </div>
                                                <StructuredOutputPreview raw={item.toolOutput} />
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    ) : (
                        <p className="muted">
                            {searchQuery.trim()
                                ? `No transcript entries match "${searchQuery}".`
                                : activeExec.status === "running" || activeExec.status === "starting"
                                    ? `Active phase: "${activeExec.phase}". Waiting for subagent messages and tool calls…`
                                    : "No transcript messages were recorded for this filter."}
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}
