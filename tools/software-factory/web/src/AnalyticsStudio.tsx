// Copyright (c) Microsoft Corporation. All rights reserved.

import { useMemo, useState } from "react";
import {
    computeFeatureScorecard,
    computeWorkspaceAnalytics,
    type FeatureScorecard,
} from "../../core/analytics.ts";
import type { FactoryFeature } from "../../core/lifecycle-contracts.ts";
import { formatDuration } from "./StudioExplorers";

function formatCreditsShort(credits: number): string {
    if (!Number.isFinite(credits) || credits <= 0) return "0 cr";
    if (credits >= 100) return `${credits.toFixed(1)} cr`;
    return `${credits.toFixed(2)} cr`;
}

function formatPct(value: number | null): string {
    return value === null ? "—" : `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`;
}

export function FeatureScorecardStrip({
    feature,
}: {
    feature: FactoryFeature;
}) {
    const [expanded, setExpanded] = useState(false);
    const card = useMemo(() => computeFeatureScorecard(feature), [feature]);

    if (card.totalRuns === 0 && card.designRounds === null && card.planRounds === null) {
        return null;
    }

    const activeStages = card.stages.filter((s) => s.runs > 0);
    const allCodingFirstPass = card.pbiMetrics.length > 0 && card.pbiMetrics.every((p) => p.firstRoundCodePass);
    const allFirstPushCiGreen = card.prCount > 0 && card.firstPushCiGreenCount === card.prCount;

    return (
        <div className="scorecard-strip" aria-label="Feature run scorecard">
            <div className="scorecard-strip-row">
                <div className="scorecard-metric">
                    <span className="scorecard-label">AI CREDITS</span>
                    <strong>{formatCreditsShort(card.totalCredits)}</strong>
                    <small>{card.totalRuns} workflow run{card.totalRuns === 1 ? "" : "s"}</small>
                </div>
                <div className="scorecard-metric">
                    <span className="scorecard-label">AGENT COMPUTE</span>
                    <strong>{card.totalActiveMs > 0 ? formatDuration(card.totalActiveMs) : "—"}</strong>
                    <small>{card.totalSubagents} specialist subagent{card.totalSubagents === 1 ? "" : "s"}</small>
                </div>
                <div className="scorecard-metric">
                    <span className="scorecard-label">REVIEW CONVERGENCE</span>
                    <strong>
                        {[
                            card.designRounds !== null ? `Design ${card.designRounds}R` : null,
                            card.planRounds !== null ? `Plan ${card.planRounds}R` : null,
                            card.pbiMetrics.length > 0
                                ? `Code ${Math.max(...card.pbiMetrics.map((p) => p.codingRounds))}R`
                                : null,
                        ].filter(Boolean).join(" · ") || "—"}
                    </strong>
                    <small>
                        {allCodingFirstPass ? "1st-pass independent code review ✓" : "Author ↔ Independent Reviewer loops"}
                    </small>
                </div>
                <div className="scorecard-metric">
                    <span className="scorecard-label">PR &amp; ACCEPTANCE GATE</span>
                    <strong>
                        {allFirstPushCiGreen
                            ? `1st-Push Green CI (${card.firstPushCiGreenCount}/${card.prCount}) ✓`
                            : card.prCount > 0
                                ? `${card.ciVerifiedPrCount}/${card.prCount} CI Green · ${card.tacticalRepairCount} repair${card.tacticalRepairCount === 1 ? "" : "s"}`
                                : "Pre-PR Stage"}
                    </strong>
                    <small>
                        {card.monitorCriteriaTotal > 0
                            ? `${card.monitorCriteriaAddressed}/${card.monitorCriteriaTotal} Stage 6 criteria addressed`
                            : "Stage 6 audit pending"}
                    </small>
                </div>
                {activeStages.length > 0 && (
                    <div className="scorecard-actions">
                        <button
                            type="button"
                            className="ghost-chip-btn"
                            onClick={() => setExpanded((prev) => !prev)}
                        >
                            {expanded ? "Hide cost split ▴" : "Cost by stage ▾"}
                        </button>
                    </div>
                )}
            </div>

            {expanded && activeStages.length > 0 && (
                <div className="scorecard-stage-bars">
                    {activeStages.map((st) => (
                        <div key={st.stage} className="scorecard-stage-row">
                            <span className="scorecard-stage-name">{st.label}</span>
                            <div className="scorecard-bar-track" aria-hidden="true">
                                <div
                                    className={`scorecard-bar-fill stage-${st.stage}`}
                                    style={{ width: `${Math.max(4, st.creditSharePct || st.timeSharePct)}%` }}
                                />
                            </div>
                            <span className="scorecard-stage-stats">
                                <strong>{formatCreditsShort(st.credits)}</strong>
                                <span>({st.creditSharePct}%)</span>
                                <span>· {st.activeMs > 0 ? formatDuration(st.activeMs) : "0s"}</span>
                                <span>· {st.subagents} agent{st.subagents === 1 ? "" : "s"}</span>
                            </span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export function WorkspaceAnalyticsView({
    features,
    onSelectFeature,
    onClose,
}: {
    features: FactoryFeature[];
    onSelectFeature: (featureId: string) => void;
    onClose: () => void;
}) {
    const [publishedOnly, setPublishedOnly] = useState(false);
    const analytics = useMemo(
        () => computeWorkspaceAnalytics(features, { publishedOnly }),
        [features, publishedOnly],
    );

    const activeStages = analytics.stageBreakdown.filter((s) => s.runs > 0);

    return (
        <section className="card analytics-studio" aria-label="Workspace Run Scorecard and Analytics">
            <header className="analytics-header">
                <div>
                    <span className="eyebrow">SOFTWARE FACTORY · ENGINEERING VELOCITY, CONVERGENCE &amp; COST TELEMETRY</span>
                    <h2>Run Scorecard &amp; Autonomous Quality Analytics</h2>
                    <p className="muted">
                        Aggregated across {analytics.featureCount} active feature{analytics.featureCount === 1 ? "" : "s"}
                        {analytics.supersededDraftCount > 0 ? ` (${analytics.supersededDraftCount} pre-publish local draft${analytics.supersededDraftCount === 1 ? "" : "s"} deduplicated)` : ""}.
                    </p>
                </div>
                <div className="analytics-header-controls">
                    <label className="diff-checkbox-pill">
                        <input
                            type="checkbox"
                            checked={publishedOnly}
                            onChange={(e) => setPublishedOnly(e.target.checked)}
                        />
                        <span>ADO Published Only ({analytics.publishedFeatureCount})</span>
                    </label>
                    <button type="button" className="primary" onClick={onClose}>
                        ← Back to Feature Studio
                    </button>
                </div>
            </header>

            <div className="analytics-kpi-grid">
                <div className="analytics-kpi-card accent-emerald">
                    <span className="eyebrow">FIRST-PUSH GREEN CI RATE</span>
                    <strong>{formatPct(analytics.convergence.firstPushCiGreenRatePct)}</strong>
                    <p>
                        {analytics.convergence.firstPushCiGreenCount} of {analytics.convergence.ciVerifiedPrCount || analytics.convergence.prCount} PRs passed full CI &amp; Copilot review on first push ({analytics.convergence.totalTacticalRepairs} repair pushes)
                    </p>
                </div>
                <div className="analytics-kpi-card accent-blue">
                    <span className="eyebrow">TOTAL AI CREDITS</span>
                    <strong>{formatCreditsShort(analytics.totalCredits)}</strong>
                    <p>
                        Avg {formatCreditsShort(analytics.avgCreditsPerExecutedFeature)} per executed feature across {analytics.totalExecutions} deduplicated workflow runs
                    </p>
                </div>
                <div className="analytics-kpi-card accent-purple">
                    <span className="eyebrow">ACTIVE AGENT COMPUTE</span>
                    <strong>{analytics.totalActiveMs > 0 ? formatDuration(analytics.totalActiveMs) : "0s"}</strong>
                    <p>
                        {analytics.totalSubagents} specialist subagent invocations across Author, Independent Reviewer &amp; Observer roles
                    </p>
                </div>
                <div className="analytics-kpi-card accent-emerald">
                    <span className="eyebrow">STAGE 6 ACCEPTANCE AUDIT</span>
                    <strong>{formatPct(analytics.convergence.monitorCriteriaPassRatePct)}</strong>
                    <p>
                        {analytics.convergence.monitorCriteriaAddressed} / {analytics.convergence.monitorCriteriaTotal} approved requirements independently verified in open PRs
                    </p>
                </div>
            </div>

            <div className="analytics-two-col">
                <section className="analytics-panel-box">
                    <div className="studio-section-head">
                        <h3>Author ↔ Independent Reviewer Convergence</h3>
                        <p className="muted">How many rounds each stage needed before passing independent verification.</p>
                    </div>
                    <div className="convergence-funnel-list">
                        <div className="convergence-row">
                            <div>
                                <strong>Stage 2 · Architecture Design</strong>
                                <span className="muted">{analytics.convergence.designEvaluatedCount} feature{analytics.convergence.designEvaluatedCount === 1 ? "" : "s"} evaluated</span>
                            </div>
                            <div className="convergence-badges">
                                <span className="studio-pill blue">
                                    Avg {analytics.convergence.designAvgRounds ?? "—"} round{analytics.convergence.designAvgRounds === 1 ? "" : "s"}
                                </span>
                                <span className="studio-pill neutral">
                                    1st-Pass: {formatPct(analytics.convergence.designFirstPassRatePct)}
                                </span>
                            </div>
                        </div>
                        <div className="convergence-row">
                            <div>
                                <strong>Stage 3 · Repo-Targeted Planning</strong>
                                <span className="muted">{analytics.convergence.planEvaluatedCount} plan{analytics.convergence.planEvaluatedCount === 1 ? "" : "s"} evaluated</span>
                            </div>
                            <div className="convergence-badges">
                                <span className="studio-pill blue">
                                    Avg {analytics.convergence.planAvgRounds ?? "—"} round{analytics.convergence.planAvgRounds === 1 ? "" : "s"}
                                </span>
                                <span className="studio-pill emerald">
                                    1st-Pass: {formatPct(analytics.convergence.planFirstPassRatePct)}
                                </span>
                            </div>
                        </div>
                        <div className="convergence-row">
                            <div>
                                <strong>Stage 5 · Autonomous Coding &amp; Code Review</strong>
                                <span className="muted">{analytics.convergence.codingPbiCount} PBI{analytics.convergence.codingPbiCount === 1 ? "" : "s"} built</span>
                            </div>
                            <div className="convergence-badges">
                                <span className="studio-pill blue">
                                    Avg {analytics.convergence.codingAvgRounds ?? "—"} round{analytics.convergence.codingAvgRounds === 1 ? "" : "s"}
                                </span>
                                <span className="studio-pill emerald">
                                    1st-Pass: {formatPct(analytics.convergence.codingFirstPassRatePct)}
                                </span>
                            </div>
                        </div>
                        <div className="convergence-row">
                            <div>
                                <strong>CI &amp; PR Observer Gate</strong>
                                <span className="muted">{analytics.convergence.prCount} PR{analytics.convergence.prCount === 1 ? "" : "s"} opened</span>
                            </div>
                            <div className="convergence-badges">
                                <span className="studio-pill emerald">
                                    1st-Push Green: {formatPct(analytics.convergence.firstPushCiGreenRatePct)}
                                </span>
                                <span className="studio-pill neutral">
                                    {analytics.convergence.totalTacticalRepairs} Tactical Repair{analytics.convergence.totalTacticalRepairs === 1 ? "" : "s"}
                                </span>
                            </div>
                        </div>
                    </div>
                </section>

                <section className="analytics-panel-box">
                    <div className="studio-section-head">
                        <h3>Cost &amp; Compute Share by Lifecycle Stage</h3>
                        <p className="muted">Where AI credits and active execution time are spent across the 6-stage pipeline.</p>
                    </div>
                    {activeStages.length > 0 ? (
                        <div className="scorecard-stage-bars">
                            {activeStages.map((st) => (
                                <div key={st.stage} className="scorecard-stage-row">
                                    <span className="scorecard-stage-name">{st.label}</span>
                                    <div className="scorecard-bar-track" aria-hidden="true">
                                        <div
                                            className={`scorecard-bar-fill stage-${st.stage}`}
                                            style={{ width: `${Math.max(4, st.creditSharePct || st.timeSharePct)}%` }}
                                        />
                                    </div>
                                    <span className="scorecard-stage-stats">
                                        <strong>{formatCreditsShort(st.credits)}</strong>
                                        <span>({st.creditSharePct}%)</span>
                                        <span>· {st.activeMs > 0 ? formatDuration(st.activeMs) : "0s"}</span>
                                    </span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <p className="muted">No paid workflow runs recorded yet.</p>
                    )}
                </section>
            </div>

            {analytics.specialistRoles.length > 0 && (
                <section className="analytics-panel-box">
                    <div className="studio-section-head">
                        <h3>Specialist Subagent Fleet Activity</h3>
                        <p className="muted">Role-separated authors, independent reviewers, researchers, and PR observers.</p>
                    </div>
                    <div className="plan-table">
                        <table>
                            <thead>
                                <tr>
                                    <th>Specialist Role</th>
                                    <th> Separation</th>
                                    <th>Invocations</th>
                                    <th>Active Compute</th>
                                    <th>Resolved Models</th>
                                </tr>
                            </thead>
                            <tbody>
                                {analytics.specialistRoles.map((role) => (
                                    <tr key={role.roleKey}>
                                        <td><strong>{role.displayName}</strong></td>
                                        <td>
                                            <span className={`studio-pill ${
                                                role.category === "reviewer" ? "purple"
                                                    : role.category === "observer" ? "emerald"
                                                        : role.category === "researcher" ? "amber" : "blue"
                                            }`}>
                                                {role.category.toUpperCase()}
                                            </span>
                                        </td>
                                        <td>
                                            {role.invocations} ({role.completed} completed{role.cancelled > 0 ? `, ${role.cancelled} superseded` : ""})
                                        </td>
                                        <td>{role.activeMs > 0 ? formatDuration(role.activeMs) : "—"}</td>
                                        <td>
                                            <div className="chip-wrap">
                                                {role.models.length > 0
                                                    ? role.models.map((m) => <span key={m} className="mono-chip">{m}</span>)
                                                    : <span className="muted">runtime default</span>}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </section>
            )}

            <section className="analytics-panel-box">
                <div className="studio-section-head">
                    <h3>Feature-by-Feature Delivery Scorecard</h3>
                    <p className="muted">Click any feature row to open its full Lifecycle Pipeline, Diff Viewer, and Flight Recorder.</p>
                </div>
                <div className="plan-table">
                    <table>
                        <thead>
                            <tr>
                                <th>Feature</th>
                                <th>Status</th>
                                <th>AI Credits</th>
                                <th>Compute</th>
                                <th>Design / Plan / Code</th>
                                <th>PR &amp; First-Push CI</th>
                                <th>Stage 6 Audit</th>
                            </tr>
                        </thead>
                        <tbody>
                            {analytics.featureScorecards.map((card: FeatureScorecard) => (
                                <tr
                                    key={card.featureId}
                                    className="interactive-row"
                                    onClick={() => onSelectFeature(card.featureId)}
                                >
                                    <td>
                                        <strong>{card.title}</strong>
                                        <div className="chip-wrap" style={{ marginTop: 4 }}>
                                            {card.adoWorkItemId && (
                                                <span className="mini-ado-pill">ADO #{card.adoWorkItemId}</span>
                                            )}
                                            <code className="commit-pill">{card.featureId.slice(0, 8)}</code>
                                        </div>
                                    </td>
                                    <td>
                                        <span className={`studio-pill ${
                                            card.lifecycleStatus === "monitoring" || card.lifecycleStatus === "completed"
                                                ? "emerald"
                                                : "neutral"
                                        }`}>
                                            {card.lifecycleStatus.replaceAll("_", " ")}
                                        </span>
                                    </td>
                                    <td><strong>{formatCreditsShort(card.totalCredits)}</strong></td>
                                    <td>{card.totalActiveMs > 0 ? formatDuration(card.totalActiveMs) : "—"}</td>
                                    <td>
                                        {[
                                            card.designRounds !== null ? `D:${card.designRounds}R` : "D:—",
                                            card.planRounds !== null ? `P:${card.planRounds}R` : "P:—",
                                            card.pbiMetrics.length > 0
                                                ? `C:${Math.max(...card.pbiMetrics.map((p) => p.codingRounds))}R`
                                                : "C:—",
                                        ].join(" · ")}
                                    </td>
                                    <td>
                                        {card.prCount > 0 ? (
                                            <span className={`studio-pill ${card.firstPushCiGreenCount === card.prCount ? "emerald" : "amber"}`}>
                                                {card.firstPushCiGreenCount === card.prCount
                                                    ? `1st-Push Green (${card.prCount} PR)`
                                                    : `${card.ciVerifiedPrCount}/${card.prCount} Green (${card.tacticalRepairCount} fix)`}
                                            </span>
                                        ) : <span className="muted">—</span>}
                                    </td>
                                    <td>
                                        {card.monitorCriteriaTotal > 0 ? (
                                            <span className={`studio-pill ${card.monitorPass ? "emerald" : "amber"}`}>
                                                {card.monitorCriteriaAddressed}/{card.monitorCriteriaTotal} Addressed
                                            </span>
                                        ) : <span className="muted">—</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>
        </section>
    );
}
