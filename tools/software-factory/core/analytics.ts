// Copyright (c) Microsoft Corporation. All rights reserved.

import type { Execution, FactoryFeature, Json, WorkflowKind } from "./lifecycle-contracts.ts";

export const NANO_AIU_PER_CREDIT = 1_000_000_000;

export type AnalyticsStageKey = "intake" | "check" | "design" | "plan" | "implement" | "monitor";

export interface StageTelemetrySummary {
    stage: AnalyticsStageKey;
    label: string;
    runs: number;
    completedRuns: number;
    failedRuns: number;
    runningRuns: number;
    nanoAiu: number;
    credits: number;
    activeMs: number;
    subagents: number;
    creditSharePct: number;
    timeSharePct: number;
}

export interface PbiQualityMetric {
    itemId: string;
    title: string;
    repository: string;
    codingRounds: number;
    firstRoundCodePass: boolean;
    prOpened: boolean;
    prNumber: number | null;
    prUrl: string | null;
    tacticalRepairs: number;
    ciGreen: boolean;
    firstPushCiGreen: boolean;
}

export interface FeatureScorecard {
    featureId: string;
    title: string;
    lifecycleStatus: string;
    adoWorkItemId: number | null;
    isPublished: boolean;
    isSupersededLocalDraft: boolean;
    updatedAt: string;
    totalRuns: number;
    totalNanoAiu: number;
    totalCredits: number;
    totalActiveMs: number;
    totalSubagents: number;
    designRounds: number | null;
    designFixIterations: number | null;
    designFirstPass: boolean | null;
    designApproved: boolean;
    planRounds: number | null;
    plannedPbiCount: number;
    planFirstPass: boolean | null;
    planApproved: boolean;
    pbiMetrics: PbiQualityMetric[];
    prCount: number;
    ciVerifiedPrCount: number;
    firstPushCiGreenCount: number;
    tacticalRepairCount: number;
    monitorCriteriaAddressed: number;
    monitorCriteriaTotal: number;
    monitorPass: boolean | null;
    stages: StageTelemetrySummary[];
}

export interface SpecialistRoleSummary {
    roleKey: string;
    displayName: string;
    category: "author" | "reviewer" | "observer" | "researcher";
    invocations: number;
    completed: number;
    cancelled: number;
    failed: number;
    activeMs: number;
    models: string[];
}

export interface WorkspaceAnalytics {
    featureCount: number;
    publishedFeatureCount: number;
    supersededDraftCount: number;
    executedFeatureCount: number;
    deliveredOrMonitoringCount: number;
    totalExecutions: number;
    totalNanoAiu: number;
    totalCredits: number;
    avgCreditsPerExecutedFeature: number;
    totalActiveMs: number;
    totalSubagents: number;
    stageBreakdown: StageTelemetrySummary[];
    convergence: {
        designEvaluatedCount: number;
        designAvgRounds: number | null;
        designFirstPassRatePct: number | null;
        planEvaluatedCount: number;
        planAvgRounds: number | null;
        planFirstPassRatePct: number | null;
        codingPbiCount: number;
        codingAvgRounds: number | null;
        codingFirstPassRatePct: number | null;
        prCount: number;
        ciVerifiedPrCount: number;
        firstPushCiGreenCount: number;
        firstPushCiGreenRatePct: number | null;
        totalTacticalRepairs: number;
        monitorCriteriaAddressed: number;
        monitorCriteriaTotal: number;
        monitorCriteriaPassRatePct: number | null;
    };
    specialistRoles: SpecialistRoleSummary[];
    featureScorecards: FeatureScorecard[];
}

const STAGE_ORDER: readonly { key: AnalyticsStageKey; label: string }[] = [
    { key: "intake", label: "Stage 1 · Intake" },
    { key: "check", label: "Preflight Check" },
    { key: "design", label: "Stage 2 · Design" },
    { key: "plan", label: "Stage 3 · Plan" },
    { key: "implement", label: "Stage 5 · Implement & PR" },
    { key: "monitor", label: "Stage 6 · Monitor" },
];

function isRecord(value: unknown): value is Record<string, Json> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asArray(value: unknown): Json[] {
    return Array.isArray(value) ? value : [];
}

export function nanoToCredits(nanoAiu: number): number {
    if (!Number.isFinite(nanoAiu) || nanoAiu <= 0) return 0;
    return nanoAiu / NANO_AIU_PER_CREDIT;
}

function classifySpecialistLabel(rawLabel: string): {
    roleKey: string;
    displayName: string;
    category: "author" | "reviewer" | "observer" | "researcher";
} {
    const lower = rawLabel.toLowerCase();
    if (lower.includes("design-reviewer") || lower.includes("design review") || lower.includes("design-review")) {
        return { roleKey: "design-reviewer", displayName: "Independent Design Reviewer", category: "reviewer" };
    }
    if (lower.includes("design-author") || lower.includes("design author") || lower.includes("design-draft")) {
        return { roleKey: "design-author", displayName: "Design Author", category: "author" };
    }
    if (lower.includes("plan-review") || lower.includes("plan review")) {
        return { roleKey: "plan-reviewer", displayName: "Independent Plan Reviewer", category: "reviewer" };
    }
    if (lower.includes("planner") || lower.includes("plan-draft") || lower.includes("feature-planner")) {
        return { roleKey: "planner", displayName: "Feature Planner", category: "author" };
    }
    if (lower.includes("repair") && lower.includes("review")) {
        return { roleKey: "repair-reviewer", displayName: "Tactical Repair Reviewer", category: "reviewer" };
    }
    if (lower.includes("repair")) {
        return { roleKey: "repair-builder", displayName: "Tactical Repair Builder", category: "author" };
    }
    if (lower.includes("code-reviewer") || lower.includes("code review") || lower.includes("code-review") || lower.includes("reviewer")) {
        return { roleKey: "code-reviewer", displayName: "Independent Code Reviewer", category: "reviewer" };
    }
    if (lower.includes("coder") || lower.includes("builder") || lower.includes("implement")) {
        return { roleKey: "coder", displayName: "Coding Builder", category: "author" };
    }
    if (lower.includes("observer") || lower.includes("monitor")) {
        return { roleKey: "pr-observer", displayName: "PR & Acceptance Observer", category: "observer" };
    }
    if (lower.includes("research") || lower.includes("evidence") || lower.includes("intake") || lower.includes("reader")) {
        return { roleKey: "researcher", displayName: "Codebase Evidence Researcher", category: "researcher" };
    }
    return { roleKey: "specialist", displayName: rawLabel || "Factory Specialist", category: "author" };
}

export function dedupeExecutions(executions: readonly Execution[]): Execution[] {
    const byKey = new Map<string, Execution>();
    for (const exec of executions) {
        const key = exec.runtimeId ? `rt:${exec.runtimeId}` : `id:${exec.id}`;
        const prior = byKey.get(key);
        if (!prior) {
            byKey.set(key, exec);
            continue;
        }
        const priorUsage = prior.usage;
        const nextUsage = exec.usage;
        const mergedAgents = (() => {
            const left = priorUsage?.agents ?? [];
            const right = nextUsage?.agents ?? [];
            return right.length >= left.length ? right : left;
        })();
        const mergedUsage = priorUsage || nextUsage
            ? {
                observedAt: nextUsage?.observedAt ?? priorUsage?.observedAt ?? (Date.parse(exec.updatedAt) || 0),
                activeMs: Math.max(priorUsage?.activeMs ?? 0, nextUsage?.activeMs ?? 0),
                subagents: Math.max(priorUsage?.subagents ?? 0, nextUsage?.subagents ?? 0),
                nanoAiu: Math.max(priorUsage?.nanoAiu ?? 0, nextUsage?.nanoAiu ?? 0),
                declaredLimits: nextUsage?.declaredLimits ?? priorUsage?.declaredLimits ?? {},
                approvedLimits: nextUsage?.approvedLimits ?? priorUsage?.approvedLimits ?? null,
                agents: mergedAgents,
            }
            : undefined;
        const newer = exec.updatedAt >= prior.updatedAt ? exec : prior;
        byKey.set(key, { ...newer, ...(mergedUsage ? { usage: mergedUsage } : {}) });
    }
    return [...byKey.values()];
}

function mapStageKey(kind: WorkflowKind): AnalyticsStageKey {
    if (kind === "intake" || kind === "check" || kind === "design" || kind === "plan" || kind === "implement" || kind === "monitor") {
        return kind;
    }
    return "implement";
}

export function findSupersededLocalDraftIds(features: readonly FactoryFeature[]): Set<string> {
    const publishedRootRevisionIds = new Set<string>();
    for (const f of features) {
        if (f.shared && f.history[0]?.id) {
            publishedRootRevisionIds.add(f.history[0].id);
        }
    }
    const superseded = new Set<string>();
    for (const f of features) {
        if (!f.shared && f.history[0]?.id && publishedRootRevisionIds.has(f.history[0].id)) {
            superseded.add(f.id);
        }
    }
    return superseded;
}

export function computeFeatureScorecard(
    feature: FactoryFeature,
    supersededDraftIds?: ReadonlySet<string>,
): FeatureScorecard {
    const dedupedExecs = dedupeExecutions(feature.factory.executions);
    const stageAccum = new Map<AnalyticsStageKey, {
        runs: number;
        completedRuns: number;
        failedRuns: number;
        runningRuns: number;
        nanoAiu: number;
        activeMs: number;
        subagents: number;
    }>();

    for (const st of STAGE_ORDER) {
        stageAccum.set(st.key, {
            runs: 0,
            completedRuns: 0,
            failedRuns: 0,
            runningRuns: 0,
            nanoAiu: 0,
            activeMs: 0,
            subagents: 0,
        });
    }

    let totalNanoAiu = 0;
    let totalActiveMs = 0;
    let totalSubagents = 0;

    for (const exec of dedupedExecs) {
        const key = mapStageKey(exec.kind);
        const bucket = stageAccum.get(key)!;
        bucket.runs += 1;
        if (exec.status === "completed") bucket.completedRuns += 1;
        else if (exec.status === "error") bucket.failedRuns += 1;
        else if (exec.status === "running" || exec.status === "starting") bucket.runningRuns += 1;

        const nano = exec.usage?.nanoAiu ?? 0;
        const ms = exec.usage?.activeMs ?? 0;
        const subs = Math.max(exec.usage?.subagents ?? 0, exec.usage?.agents?.length ?? 0);
        bucket.nanoAiu += nano;
        bucket.activeMs += ms;
        bucket.subagents += subs;
        totalNanoAiu += nano;
        totalActiveMs += ms;
        totalSubagents += subs;
    }

    const stages: StageTelemetrySummary[] = STAGE_ORDER.map((st) => {
        const bucket = stageAccum.get(st.key)!;
        return {
            stage: st.key,
            label: st.label,
            runs: bucket.runs,
            completedRuns: bucket.completedRuns,
            failedRuns: bucket.failedRuns,
            runningRuns: bucket.runningRuns,
            nanoAiu: bucket.nanoAiu,
            credits: nanoToCredits(bucket.nanoAiu),
            activeMs: bucket.activeMs,
            subagents: bucket.subagents,
            creditSharePct: totalNanoAiu > 0 ? Math.round((bucket.nanoAiu / totalNanoAiu) * 1000) / 10 : 0,
            timeSharePct: totalActiveMs > 0 ? Math.round((bucket.activeMs / totalActiveMs) * 1000) / 10 : 0,
        };
    });

    const designArtifact = feature.currentArtifacts.design
        ? feature.factory.artifacts.find((a) => a.id === feature.currentArtifacts.design)
        : [...feature.factory.artifacts].reverse().find((a) => a.kind === "design" && a.requirementsRevision === feature.current.id);
    const designBody = isRecord(designArtifact?.content) ? designArtifact.content : null;
    const designReviews = designBody ? asArray(designBody.reviews).filter(isRecord) : [];
    const designRounds = designArtifact ? Math.max(1, designReviews.length) : null;
    const designFixIterations = designBody && typeof designBody.fixIterations === "number"
        ? designBody.fixIterations
        : (designRounds !== null ? Math.max(0, designRounds - 1) : null);
    const designFirstPass = designRounds !== null ? (designRounds === 1 && (designFixIterations ?? 0) === 0) : null;
    const designApproved = Boolean(
        designArtifact &&
        feature.factory.decisions.filter((d) => d.stage === "design" && d.targetId === designArtifact.id).at(-1)?.approved,
    );

    const planArtifact = feature.currentArtifacts.plan
        ? feature.factory.artifacts.find((a) => a.id === feature.currentArtifacts.plan)
        : [...feature.factory.artifacts].reverse().find((a) => a.kind === "plan" && a.requirementsRevision === feature.current.id);
    const planBody = isRecord(planArtifact?.content) ? planArtifact.content : null;
    const planItems = planBody ? asArray(planBody.items).filter(isRecord) : [];
    const planReviews = planBody ? asArray(planBody.reviewHistory).filter(isRecord) : [];
    const planRounds = planArtifact ? Math.max(1, planReviews.length) : null;
    const planFirstPass = planRounds !== null ? planRounds === 1 : null;
    const planApproved = Boolean(
        planArtifact &&
        feature.factory.decisions.filter((d) => d.stage === "plan" && d.targetId === planArtifact.id).at(-1)?.approved,
    );

    const monitorArtifact = feature.currentArtifacts.monitor
        ? feature.factory.artifacts.find((a) => a.id === feature.currentArtifacts.monitor)
        : [...feature.factory.artifacts].reverse().find((a) => a.kind === "monitor" && a.requirementsRevision === feature.current.id);
    const monitorBody = isRecord(monitorArtifact?.content) ? monitorArtifact.content : null;
    const monitorPrs = monitorBody ? asArray(monitorBody.prs).filter(isRecord) : [];
    const monitorAcceptance = monitorBody
        ? asArray(monitorBody.validation ?? monitorBody.acceptance ?? monitorBody.criteria).filter(isRecord)
        : [];
    const monitorCriteriaTotal = monitorAcceptance.length;
    const monitorCriteriaAddressed = monitorAcceptance.filter((c) => c.status === "addressed").length;
    const monitorPass = monitorArtifact
        ? (monitorCriteriaTotal > 0 ? monitorCriteriaAddressed === monitorCriteriaTotal : asArray(monitorBody?.errors).length === 0)
        : null;

    const latestImpl = [...feature.factory.artifacts].reverse().find(
        (a) => a.kind === "implementation" && a.requirementsRevision === feature.current.id,
    );
    const implItems = isRecord(latestImpl?.content) ? asArray(latestImpl.content.items).filter(isRecord) : [];
    const implAttempts = feature.factory.artifacts.filter(
        (a) => a.kind === "implementation_attempt" && a.requirementsRevision === feature.current.id,
    );

    const pbiIds = new Set<string>();
    for (const item of planItems) {
        if (typeof item.id === "string" && item.id) pbiIds.add(item.id);
    }
    for (const item of implItems) {
        if (typeof item.itemId === "string" && item.itemId) pbiIds.add(item.itemId);
    }
    for (const att of implAttempts) {
        if (isRecord(att.content) && typeof att.content.itemId === "string" && att.content.itemId) {
            pbiIds.add(att.content.itemId);
        }
    }

    const pbiMetrics: PbiQualityMetric[] = [];
    for (const itemId of pbiIds) {
        const planSpec = planItems.find((p) => String(p.id) === itemId);
        const implItem = implItems.find((i) => String(i.itemId) === itemId);
        const lastAttempt = [...implAttempts].reverse().find(
            (a) => isRecord(a.content) && String(a.content.itemId) === itemId,
        );
        const attemptBody = isRecord(lastAttempt?.content) ? lastAttempt.content : null;
        const source = implItem ?? attemptBody;
        if (!source && !feature.factory.prBindings.some((b) => b.itemId === itemId)) {
            continue;
        }

        const reviewHistory = source ? asArray(source.reviewHistory).filter(isRecord) : [];
        const roundsCompleted = source && typeof source.roundsCompleted === "number"
            ? source.roundsCompleted
            : reviewHistory.length;
        const codingRounds = Math.max(1, roundsCompleted || reviewHistory.length || 1);

        const receipt = source && isRecord(source.receipt) ? source.receipt : null;
        const binding = feature.factory.prBindings.find((b) => b.itemId === itemId);
        const monPr = monitorPrs.find((p) => String(p.key) === itemId);

        const prNumber = typeof receipt?.prNumber === "number"
            ? receipt.prNumber
            : typeof monPr?.number === "number"
                ? monPr.number
                : (binding ? binding.number : null);
        const prUrl = typeof receipt?.url === "string"
            ? receipt.url
            : typeof monPr?.url === "string"
                ? monPr.url
                : null;
        const prOpened = prNumber !== null;

        const repairOps = feature.factory.operations.filter(
            (op) => op.kind === "repair" && op.itemId === itemId,
        );
        const inlineRepairs = source ? asArray(source.repairs) : [];
        const explicitRepairAttempts = source && typeof source.repairAttempts === "number" ? source.repairAttempts : 0;
        const tacticalRepairs = Math.max(repairOps.length, inlineRepairs.length, explicitRepairAttempts);

        const observerStatus = typeof source?.observerStatus === "string"
            ? source.observerStatus
            : (isRecord(source?.observer) && typeof source.observer.status === "string" ? source.observer.status : "");
        const sourceCiStatus = isRecord(source?.ci) && typeof source.ci.status === "string" ? source.ci.status : "";
        const monChecks = typeof monPr?.checks === "string" ? monPr.checks : "";
        const ciGreen = observerStatus === "awaiting-human-review" ||
            observerStatus === "ready-for-human-merge" ||
            sourceCiStatus === "passed" ||
            monChecks === "passing";
        const firstPushCiGreen = prOpened && ciGreen && tacticalRepairs === 0;

        pbiMetrics.push({
            itemId,
            title: typeof planSpec?.title === "string" ? planSpec.title : itemId,
            repository: typeof planSpec?.repository === "string" ? planSpec.repository : "",
            codingRounds,
            firstRoundCodePass: codingRounds === 1,
            prOpened,
            prNumber,
            prUrl,
            tacticalRepairs,
            ciGreen,
            firstPushCiGreen,
        });
    }

    const prCount = pbiMetrics.filter((m) => m.prOpened).length;
    const ciVerifiedPrCount = pbiMetrics.filter((m) => m.prOpened && m.ciGreen).length;
    const firstPushCiGreenCount = pbiMetrics.filter((m) => m.firstPushCiGreen).length;
    const tacticalRepairCount = pbiMetrics.reduce((sum, m) => sum + m.tacticalRepairs, 0);

    return {
        featureId: feature.id,
        title: feature.current.title,
        lifecycleStatus: feature.lifecycleStatus,
        adoWorkItemId: feature.shared?.target.workItemId ?? null,
        isPublished: Boolean(feature.shared),
        isSupersededLocalDraft: supersededDraftIds ? supersededDraftIds.has(feature.id) : false,
        updatedAt: feature.updatedAt,
        totalRuns: dedupedExecs.length,
        totalNanoAiu,
        totalCredits: nanoToCredits(totalNanoAiu),
        totalActiveMs,
        totalSubagents,
        designRounds,
        designFixIterations,
        designFirstPass,
        designApproved,
        planRounds,
        plannedPbiCount: planItems.length,
        planFirstPass,
        planApproved,
        pbiMetrics,
        prCount,
        ciVerifiedPrCount,
        firstPushCiGreenCount,
        tacticalRepairCount,
        monitorCriteriaAddressed,
        monitorCriteriaTotal,
        monitorPass,
        stages,
    };
}

export function computeWorkspaceAnalytics(
    features: readonly FactoryFeature[],
    options: { publishedOnly?: boolean } = {},
): WorkspaceAnalytics {
    const supersededIds = findSupersededLocalDraftIds(features);
    const activeFeatures = features.filter((f) => {
        if (supersededIds.has(f.id)) return false;
        if (options.publishedOnly && !f.shared) return false;
        return true;
    });

    const scorecards = activeFeatures
        .map((f) => computeFeatureScorecard(f, supersededIds))
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));

    const stageTotals = new Map<AnalyticsStageKey, {
        runs: number;
        completedRuns: number;
        failedRuns: number;
        runningRuns: number;
        nanoAiu: number;
        activeMs: number;
        subagents: number;
    }>();
    for (const st of STAGE_ORDER) {
        stageTotals.set(st.key, {
            runs: 0,
            completedRuns: 0,
            failedRuns: 0,
            runningRuns: 0,
            nanoAiu: 0,
            activeMs: 0,
            subagents: 0,
        });
    }

    const roleMap = new Map<string, {
        roleKey: string;
        displayName: string;
        category: "author" | "reviewer" | "observer" | "researcher";
        invocations: number;
        completed: number;
        cancelled: number;
        failed: number;
        activeMs: number;
        models: Set<string>;
    }>();

    for (const feature of activeFeatures) {
        const deduped = dedupeExecutions(feature.factory.executions);
        for (const exec of deduped) {
            for (const ag of exec.usage?.agents ?? []) {
                const info = classifySpecialistLabel(ag.label);
                let role = roleMap.get(info.roleKey);
                if (!role) {
                    role = {
                        ...info,
                        invocations: 0,
                        completed: 0,
                        cancelled: 0,
                        failed: 0,
                        activeMs: 0,
                        models: new Set<string>(),
                    };
                    roleMap.set(info.roleKey, role);
                }
                role.invocations += 1;
                if (ag.status === "completed") role.completed += 1;
                else if (ag.status === "cancelled") role.cancelled += 1;
                else if (ag.status === "failed") role.failed += 1;
                role.activeMs += ag.activeMs ?? 0;
                const model = ag.resolvedModel || ag.requestedModel;
                if (model) role.models.add(model);
            }
        }
    }

    let totalExecutions = 0;
    let totalNanoAiu = 0;
    let totalActiveMs = 0;
    let totalSubagents = 0;

    for (const card of scorecards) {
        totalExecutions += card.totalRuns;
        totalNanoAiu += card.totalNanoAiu;
        totalActiveMs += card.totalActiveMs;
        totalSubagents += card.totalSubagents;
        for (const st of card.stages) {
            const bucket = stageTotals.get(st.stage)!;
            bucket.runs += st.runs;
            bucket.completedRuns += st.completedRuns;
            bucket.failedRuns += st.failedRuns;
            bucket.runningRuns += st.runningRuns;
            bucket.nanoAiu += st.nanoAiu;
            bucket.activeMs += st.activeMs;
            bucket.subagents += st.subagents;
        }
    }

    const stageBreakdown: StageTelemetrySummary[] = STAGE_ORDER.map((st) => {
        const bucket = stageTotals.get(st.key)!;
        return {
            stage: st.key,
            label: st.label,
            runs: bucket.runs,
            completedRuns: bucket.completedRuns,
            failedRuns: bucket.failedRuns,
            runningRuns: bucket.runningRuns,
            nanoAiu: bucket.nanoAiu,
            credits: nanoToCredits(bucket.nanoAiu),
            activeMs: bucket.activeMs,
            subagents: bucket.subagents,
            creditSharePct: totalNanoAiu > 0 ? Math.round((bucket.nanoAiu / totalNanoAiu) * 1000) / 10 : 0,
            timeSharePct: totalActiveMs > 0 ? Math.round((bucket.activeMs / totalActiveMs) * 1000) / 10 : 0,
        };
    });

    const executedFeatureCount = scorecards.filter((c) => c.totalRuns > 0).length;
    const totalCredits = nanoToCredits(totalNanoAiu);
    const avgCreditsPerExecutedFeature = executedFeatureCount > 0
        ? totalCredits / executedFeatureCount
        : 0;

    const designEvaluated = scorecards.filter((c) => c.designRounds !== null);
    const planEvaluated = scorecards.filter((c) => c.planRounds !== null);
    const allPbis = scorecards.flatMap((c) => c.pbiMetrics);

    const pct = (num: number, den: number): number | null =>
        den > 0 ? Math.round((num / den) * 1000) / 10 : null;
    const avg = (vals: number[]): number | null =>
        vals.length > 0 ? Math.round((vals.reduce((s, v) => s + v, 0) / vals.length) * 100) / 100 : null;

    const prCount = allPbis.filter((p) => p.prOpened).length;
    const ciVerifiedPrCount = allPbis.filter((p) => p.prOpened && p.ciGreen).length;
    const firstPushCiGreenCount = allPbis.filter((p) => p.firstPushCiGreen).length;
    const totalTacticalRepairs = allPbis.reduce((s, p) => s + p.tacticalRepairs, 0);

    const monitorCriteriaAddressed = scorecards.reduce((s, c) => s + c.monitorCriteriaAddressed, 0);
    const monitorCriteriaTotal = scorecards.reduce((s, c) => s + c.monitorCriteriaTotal, 0);

    const specialistRoles: SpecialistRoleSummary[] = [...roleMap.values()]
        .map((r) => ({
            roleKey: r.roleKey,
            displayName: r.displayName,
            category: r.category,
            invocations: r.invocations,
            completed: r.completed,
            cancelled: r.cancelled,
            failed: r.failed,
            activeMs: r.activeMs,
            models: [...r.models].sort(),
        }))
        .sort((a, b) => b.activeMs - a.activeMs || b.invocations - a.invocations);

    return {
        featureCount: activeFeatures.length,
        publishedFeatureCount: activeFeatures.filter((f) => Boolean(f.shared)).length,
        supersededDraftCount: supersededIds.size,
        executedFeatureCount,
        deliveredOrMonitoringCount: activeFeatures.filter((f) =>
            f.lifecycleStatus === "monitoring" || f.lifecycleStatus === "completed",
        ).length,
        totalExecutions,
        totalNanoAiu,
        totalCredits,
        avgCreditsPerExecutedFeature,
        totalActiveMs,
        totalSubagents,
        stageBreakdown,
        convergence: {
            designEvaluatedCount: designEvaluated.length,
            designAvgRounds: avg(designEvaluated.map((c) => c.designRounds!)),
            designFirstPassRatePct: pct(designEvaluated.filter((c) => c.designFirstPass === true).length, designEvaluated.length),
            planEvaluatedCount: planEvaluated.length,
            planAvgRounds: avg(planEvaluated.map((c) => c.planRounds!)),
            planFirstPassRatePct: pct(planEvaluated.filter((c) => c.planFirstPass === true).length, planEvaluated.length),
            codingPbiCount: allPbis.length,
            codingAvgRounds: avg(allPbis.map((p) => p.codingRounds)),
            codingFirstPassRatePct: pct(allPbis.filter((p) => p.firstRoundCodePass).length, allPbis.length),
            prCount,
            ciVerifiedPrCount,
            firstPushCiGreenCount,
            firstPushCiGreenRatePct: pct(firstPushCiGreenCount, ciVerifiedPrCount || prCount),
            totalTacticalRepairs,
            monitorCriteriaAddressed,
            monitorCriteriaTotal,
            monitorCriteriaPassRatePct: pct(monitorCriteriaAddressed, monitorCriteriaTotal),
        },
        specialistRoles,
        featureScorecards: scorecards,
    };
}
